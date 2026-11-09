"""Rewrite Boreas source from the old array syntax to brackets (D51).

    python3 tools/migrate_brackets.py FILE.bor [FILE.bor ...]      rewrite whole programs in place
    python3 tools/migrate_brackets.py --tests tests/test_x.py ...   rewrite the Boreas strings in test files

An array element, section or substring `a(i)` becomes `a[i]`, `real :: v(3)` becomes `real[3] :: v`,
`allocatable real :: m(:, :)` becomes `allocatable real[,] :: m`, `allocate a(n)` becomes `allocate a[n]` and
`p.tags(2)` becomes `p.tags[2]`. A name followed by `(` is left alone when it is something you can call: a
procedure or record defined in the text (or, with --tests, anywhere in the file), a built-in, a type
conversion or a keyword; any other name is an array. A parameter that takes an array is given its rank
(`real[,] :: m`) from how the function uses it, or from `ranks`, a {(function, parameter): "[,]"} map.
"""
import ast
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.interp import BUILTINS, ELEMENTAL
from boreas.lexer import LangError, lex
from boreas.parser import (Apply, FieldAccess, KEYWORDS, TYPES, Function, Slice, parse)

NOT_ARRAYS = (set(BUILTINS) | set(ELEMENTAL) | set(KEYWORDS) | set(TYPES) | {"present", "allocated"}) \
    - {"in", "out", "inout"}          # these are keywords only in a procedure header; as `out(i)` they are an array
DEFINES = re.compile(r"^[ \t]*(?:(?:public|private)[ \t]+)?(?:function|subroutine|record)[ \t]+([A-Za-z_]\w*)", re.I | re.M)
PROC_PARAM = re.compile(r"\b(?:function|subroutine)\s*\([^()]*\)(?:\s*->\s*\w+)?\s*::\s*([A-Za-z_]\w*)", re.I)


def callable_names(*texts):
    """Names defined as procedures or records in the texts, and procedure-valued parameters."""
    out = set()
    for t in texts:
        out |= {m.group(1).lower() for m in DEFINES.finditer(t)}
        out |= {m.group(1).lower() for m in PROC_PARAM.finditer(t)}
    return out


def line_starts(text):
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def match(toks, i):
    """Index of the ")" that closes the "(" at toks[i], or None."""
    depth = 0
    for j in range(i, len(toks)):
        if toks[j].text == "(" and toks[j].kind == "op":
            depth += 1
        elif toks[j].text == ")" and toks[j].kind == "op":
            depth -= 1
            if depth == 0:
                return j
    return None


def migrate(text, callables=(), ranks=None):
    """`text` with the old array syntax rewritten. `callables` are extra names that are calls, not arrays."""
    try:
        toks = lex(text)
    except LangError:
        return text
    calls = set(callables) | callable_names(text)
    ranks = ranks or {}
    starts = line_starts(text)

    def at(t):
        return starts[t.line - 1] + t.col - 1

    edits = []                                           # (start, end, replacement)
    statement, current = [], None
    stmts = []
    for t in toks:
        if t.kind in ("nl", "eof"):
            if statement:
                stmts.append(statement)
            statement = []
        else:
            statement.append(t)
    for st in stmts:
        texts = [t.text for t in st]
        head = [i for i, x in enumerate(texts) if x in ("function", "subroutine")]
        skip = set()
        if head and all(x in ("public", "private") for x in texts[:head[0]]) and head[0] + 1 < len(st) \
                and st[head[0] + 1].kind == "ident":
            current = texts[head[0] + 1]
            if head[0] + 2 < len(st) and texts[head[0] + 2] == "(":
                end = match(st, head[0] + 2)
                for i in range(head[0] + 3, end or 0):
                    if texts[i] == "::" and i + 1 < len(st) and st[i + 1].kind == "ident" \
                            and (current, texts[i + 1]) in ranks and i > 0 and st[i - 1].kind == "ident":
                        after = at(st[i - 1]) + len(text_of(text, st[i - 1], at))
                        edits.append((after, after, ranks[(current, texts[i + 1])]))
                skip.update(range(head[0] + 2, (end or head[0] + 2) + 1))
        # a declaration: TYPE :: name(dims)
        depth, colon = 0, None
        for i, x in enumerate(texts):
            if x == "(":
                depth += 1
            elif x == ")":
                depth -= 1
            elif x == "::" and depth == 0:
                colon = i
                break
        if colon is not None and not head and colon > 0 and colon + 2 < len(st) and st[colon + 1].kind == "ident" \
                and texts[colon + 2] == "(":
            close = match(st, colon + 2)
            if close is not None:
                inner = text[at(st[colon + 2]) + 1:at(st[close])]
                if "allocatable" in texts[:colon]:
                    suffix = "[" + "," * (inner.count(",") if inner.strip() else 0) + "]"
                else:
                    suffix = "[" + inner + "]"
                kind_end = at(st[colon - 1]) + len(text_of(text, st[colon - 1], at))
                edits.append((kind_end, kind_end, suffix))
                edits.append((at(st[colon + 2]), at(st[close]) + 1, ""))
                skip.update(range(colon + 1, close + 1))
        for i, t in enumerate(st):
            if i in skip or t.kind != "ident" or i + 1 >= len(st) or texts[i + 1] != "(":
                continue
            prev = texts[i - 1] if i else ""
            if prev in ("call", "function", "subroutine", "record", "->"):
                continue
            if prev not in (".", "allocate") and (t.text in NOT_ARRAYS or t.text in calls):
                continue
            close = match(st, i + 1)
            if close is None or close == i + 2:               # `name()` can only be a call
                continue
            edits.append((at(st[i + 1]), at(st[i + 1]) + 1, "["))
            edits.append((at(st[close]), at(st[close]) + 1, "]"))
    for start, end, repl in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def text_of(text, tok, at):
    """The source text of a token (identifiers keep the case they were written in)."""
    m = re.compile(r"[A-Za-z_]\w*|\S").match(text, at(tok))
    return m.group() if m else tok.text


def infer_ranks(text):
    """{(function, parameter): "[,]"} for the plain parameters of the functions in a program that the
    function indexes as an array (the number of subscripts is the rank), or None if the text does not parse."""
    try:
        unit = parse(text if "program" in text else text + "\nprogram t\nend program t\n")
    except LangError:
        return {}
    funcs = list(unit.functions.values()) + [f for m in unit.modules.values() for f in m.functions.values()]
    ranks = {}
    for f in funcs:
        params = {n: t for t, n, _ in f.params if isinstance(t, str) and not t.endswith("]") and not t.endswith("?")}
        found = {}
        for node in nodes(f.body):
            if isinstance(node, Apply) and not node.bracket and node.name in params:
                if params[node.name] == "string" and len(node.args) == 1 and isinstance(node.args[0], Slice):
                    continue                                  # part of a string
                found[node.name] = max(found.get(node.name, 0), len(node.args))
            elif isinstance(node, Apply) and node.name == "size" and len(node.args) == 2 \
                    and hasattr(node.args[0], "name") and node.args[0].name in params and hasattr(node.args[1], "value"):
                found[node.args[0].name] = max(found.get(node.args[0].name, 0), int(node.args[1].value))
        for name, rank in found.items():
            ranks[(f.name, name)] = "[" + "," * (rank - 1) + "]"
    return ranks


def nodes(obj):
    from dataclasses import is_dataclass, fields
    if isinstance(obj, (list, tuple)):
        for x in obj:
            yield from nodes(x)
    elif is_dataclass(obj) and not isinstance(obj, type):
        yield obj
        for f in fields(obj):
            yield from nodes(getattr(obj, f.name))


SAY_ARRAY = re.compile(r"say that '(\w+)' of (\w+) is an array: declare it as \w+(\[,*\]) ::")


def learned_ranks(text):
    """Run a (migrated) program and read the ranks of the array parameters off the interpreter's warnings:
    {(function, parameter): "[,]"}. Parameters the program never passes an array to stay unknown."""
    from boreas.interp import Interp
    warned = []
    try:
        Interp(parse(text), lambda *a: None, warn=warned.append).run()
    except Exception:
        pass
    return {(m.group(2), m.group(1)): m.group(3) for w in warned for m in [SAY_ARRAY.search(w)] if m}


def migrate_program(text, ranks=None):
    """A whole program migrated: ranks of array parameters come from how the functions use them, then from
    running the program."""
    found = infer_ranks(text)
    found.update(ranks or {})
    new = migrate(text, ranks=found)
    more = learned_ranks(new)
    if more:
        new = migrate(new, ranks=more)
    return new


def encode(value, original):
    """Python source for `value`, written in the quoting style of the `original` literal."""
    m = re.match(r"([rRbBuUfF]*)('''|\"\"\"|'|\")", original)
    prefix, quote = m.group(1), m.group(2)
    body = value.replace("\\", "\\\\")
    if len(quote) == 3:
        body = body.replace(quote[0] * 3, "\\" + quote[0] * 3)
        if body.endswith(quote[0]):
            body = body[:-1] + "\\" + quote[0]
    else:
        body = body.replace(quote, "\\" + quote).replace("\n", "\\n")
    return prefix.replace("r", "").replace("R", "") + quote + body + quote


def enclosing_defs(src):
    """A function from a line number to the (first line of the) innermost `def` that holds it, or None."""
    spans = [(n.lineno, n.end_lineno) for n in ast.walk(ast.parse(src))
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def of(line):
        inside = [sp for sp in spans if sp[0] <= line <= sp[1]]
        return max(inside)[0] if inside else None
    return of


def migrate_python(path, only=None, ranks=None):
    """Rewrite the Boreas strings in a Python test file. `only` limits the strings to those that occur
    inside one of the given program texts (the sources the tests really ran as Boreas)."""
    src = open(path).read()
    literals = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING and not tok.string.lstrip("rRuU").startswith(("b", "B", "f", "F")):
            try:
                literals.append((tok, ast.literal_eval(tok.string)))
            except (ValueError, SyntaxError):
                pass
    scopes = enclosing_defs(src)
    pools = {}
    for tok, value in literals:
        if isinstance(value, str):
            pools.setdefault(scopes(tok.start[0]), []).append(value)
    outside = callable_names(*pools.get(None, []))
    lines = src.splitlines(keepends=True)
    offsets = [0]
    for ln in lines:
        offsets.append(offsets[-1] + len(ln))
    edits = []
    for tok, value in literals:
        if not isinstance(value, str) or len(value) < 4:
            continue
        if only is not None and not any(value in s for s in only):
            continue
        new = migrate(value, outside | callable_names(*pools.get(scopes(tok.start[0]), [])), ranks=ranks)
        if new != value:
            a = offsets[tok.start[0] - 1] + tok.start[1]
            b = offsets[tok.end[0] - 1] + tok.end[1]
            edits.append((a, b, encode(new, tok.string)))
    for a, b, repl in sorted(edits, reverse=True):
        src = src[:a] + repl + src[b:]
    if edits:
        open(path, "w").write(src)
    return len(edits)


def main(argv):
    if argv and argv[0] == "--tests":
        for path in argv[1:]:
            print(path, migrate_python(path))
        return
    for path in argv:
        text = open(path).read()
        new = migrate_program(text)
        if new != text:
            open(path, "w").write(new)
        print(path, "rewritten" if new != text else "unchanged")


if __name__ == "__main__":
    main(sys.argv[1:])
