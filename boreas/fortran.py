"""A free-form Fortran front end: Fortran source -> the Boreas AST.

It covers a modern subset (program/function/subroutine, declarations, do/if,
array expressions, print/write to the terminal). Anything else becomes a
`! TODO(convert)` comment quoting the original line instead of failing the
whole file. Every translation that is not a 1:1 rename is recorded as a
finding, returned alongside the AST."""
import re
from dataclasses import dataclass, fields, is_dataclass

from .lexer import LangError
from .parser import *
from .interp import BUILTINS, ELEMENTAL
from .printer import to_source

# Words the Boreas parser treats specially; Fortran has no reserved words,
# so a Fortran variable with one of these names is renamed.
RESERVED = {"and", "or", "not", "true", "false", "in", "out", "inout", "string", "integer",
            "real", "logical", "program", "function", "subroutine", "end", "if", "then",
            "else", "do", "while", "print", "call", "return", "exit", "cycle"}

TYPE_WORDS = {"integer", "real", "logical", "character", "double", "complex", "type"}
FIXED_FORM_EXTENSIONS = (".f", ".for", ".f77", ".ftn")
BLOCK_KINDS = ("select", "associate", "block", "interface", "where", "forall", "type", "module", "do")


class Unsupported(Exception):
    """Raised for Fortran this front end does not translate."""


# --- Lexing ------------------------------------------------------------
@dataclass
class Item:
    line: int
    text: str                    # one statement, comments removed
    comment: str | None = None   # set for a full-line comment
    toks: list | None = None


@dataclass
class Tok:
    kind: str                    # int, real, str, name, dop (.and. etc.), op
    text: str
    value: object = None


DOT_WORDS = "and|or|not|eqv|neqv|eq|ne|lt|le|gt|ge"
TOKEN_RE = re.compile(rf"""
    (?P<ws>[ \t]+)
  | (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
  | (?P<dop>\.(?:{DOT_WORDS}|true|false)\.)
  | (?P<real>(?:\d+\.(?!(?:{DOT_WORDS})\.)\d*(?:[ed][+-]?\d+)?|\.\d+(?:[ed][+-]?\d+)?|\d+[ed][+-]?\d+)(?:_[a-z0-9_]+)?)
  | (?P<int>\d+(?:_[a-z0-9_]+)?)
  | (?P<name>[a-z_][a-z0-9_]*)
  | (?P<op>\(/|/\)|::|=>|\*\*|//|==|/=|<=|>=|[-+*/<>=(),:%\[\]])
""", re.X | re.I)


def split_comment(raw):
    """Split `raw` at the first `!` that is not inside a string."""
    quote = None
    for i, ch in enumerate(raw):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "!":
            return raw[:i], raw[i + 1:].strip()
    return raw, None


def split_semicolons(text):
    parts, cur, quote = [], [], None
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == ";":
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def scan_lines(src):
    """Turn source text into logical statements (continuations joined, `;` split)."""
    items, pending, pending_line = [], "", None
    for lineno, raw in enumerate(src.splitlines(), 1):
        code, comment = split_comment(raw)
        stripped = code.strip()
        if not stripped and not pending:
            if comment is not None:
                items.append(Item(lineno, "", comment=comment))
            continue
        if not stripped:
            continue
        if pending and stripped.startswith("&"):
            stripped = stripped[1:].lstrip()
        cont = stripped.endswith("&")
        if cont:
            stripped = stripped[:-1].rstrip()
        if pending_line is None:
            pending_line = lineno
        pending = (pending + " " + stripped).strip()
        if cont:
            continue
        for part in split_semicolons(pending):
            if part.strip():
                items.append(Item(pending_line, part.strip()))
        pending, pending_line = "", None
    for part in split_semicolons(pending):
        if part.strip():
            items.append(Item(pending_line, part.strip()))
    return items


def tokenize(text):
    toks, pos = [], 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise Unsupported(f"unexpected character {text[pos]!r}")
        kind, raw = m.lastgroup, m.group()
        pos = m.end()
        if kind == "ws":
            continue
        if kind == "str":
            q = raw[0]
            toks.append(Tok("str", raw, raw[1:-1].replace(q + q, q)))
        elif kind == "int":
            toks.append(Tok("int", raw, int(raw.split("_")[0])))
        elif kind == "real":
            body = raw.lower().split("_")[0].replace("d", "e")
            toks.append(Tok("real", raw, float(body)))
        else:
            toks.append(Tok(kind, raw.lower()))
    return toks


# --- Parsing -----------------------------------------------------------
def classify(toks):
    """Label statements that end or divide a block: ('end', kind), ('else',), ..."""
    if not toks or toks[0].kind != "name":
        return None
    t0 = toks[0].text
    nxt = toks[1] if len(toks) > 1 else None
    if nxt is not None and nxt.kind == "op" and nxt.text in ("=", "("):
        if nxt.text == "=" or t0 not in ("else", "elseif", "end"):
            return None
    if t0.startswith("end") and t0 in ("enddo", "endif", "endprogram", "endfunction",
                                       "endsubroutine", "endselect", "endblock", "endmodule",
                                       "endinterface", "endtype", "endwhere", "endforall",
                                       "endassociate"):
        return ("end", t0[3:])
    if t0 == "end":
        if nxt is None:
            return ("end", None)
        if nxt.kind == "name":
            return ("end", nxt.text)
    if t0 == "else":
        return ("elseif",) if nxt is not None and nxt.text == "if" else ("else",)
    if t0 == "elseif":
        return ("elseif",)
    if t0 == "contains" and nxt is None:
        return ("contains",)
    return None


@dataclass
class Info:
    type: str
    dims: list | None
    shape_dropped: bool
    intent: str | None
    init: object
    parameter: bool
    line: int


class Scope:
    def __init__(self, kind, name, dummies=()):
        self.kind, self.name, self.dummies = kind, name, list(dummies)
        self.decls = {}
        self.implicit_none = False
        self.result = None
        self.ret_type = None
        self.comments = []
        self.externals = set()
        self.skipped = set()          # declared in Fortran we could not translate


@dataclass
class Raw:
    kind: str
    scope: Scope
    body: list
    line: int


class StopMarker(Comment):
    pass


def iter_nodes(obj):
    """Every dataclass node reachable from `obj` (statements, expressions, lists, tuples)."""
    if isinstance(obj, (list, tuple)):
        for x in obj:
            yield from iter_nodes(x)
    elif is_dataclass(obj):
        yield obj
        for f in fields(obj):
            yield from iter_nodes(getattr(obj, f.name))


def implicit_type(name):
    return "integer" if name[0] in "ijklmn" else "real"


class FParser:
    def __init__(self, src):
        self.items = scan_lines(src)
        self.i = 0
        self.findings = []          # (line, "todo" | "note", message)
        self.raw = []
        self.renames = {}
        self.line = 0
        self.toks, self.k = [], 0
        self.top_comments = []
        self.once = set()

    # -- reporting
    def note(self, line, msg, once=False):
        if once:
            if msg in self.once:
                return
            self.once.add(msg)
        if (line, "note", msg) not in self.findings:
            self.findings.append((line, "note", msg))

    def todo(self, line, msg):
        self.findings.append((line, "todo", msg))

    def todo_comment(self, it, msg):
        self.todo(it.line, f"{msg}: {it.text}")
        return Comment(f"TODO(convert): {msg}: {it.text}")

    def rename(self, name):
        if name in RESERVED:
            new = name + "_"
            if name not in self.renames:
                self.renames[name] = new
                self.note(self.line, f"'{name}' is a Boreas keyword; renamed to '{new}'")
            return new
        return name

    # -- token cursor
    def peek(self, off=0):
        j = self.k + off
        return self.toks[j] if j < len(self.toks) else None

    def next(self):
        t = self.peek()
        if t is None:
            raise Unsupported("unexpected end of statement")
        self.k += 1
        return t

    def at_op(self, *ops):
        t = self.peek()
        return t is not None and t.kind == "op" and t.text in ops

    def at_name(self, *names):
        t = self.peek()
        return t is not None and t.kind == "name" and (not names or t.text in names)

    def at_dop(self, *ops):
        t = self.peek()
        return t is not None and t.kind == "dop" and t.text in ops

    def accept_op(self, op):
        if self.at_op(op):
            self.k += 1
            return True
        return False

    def expect_op(self, op):
        if not self.accept_op(op):
            raise Unsupported(f"expected {op!r}")

    def expect_name(self):
        t = self.peek()
        if t is None or t.kind != "name":
            raise Unsupported("expected a name")
        self.k += 1
        return t.text

    def end_of_statement(self):
        if self.peek() is not None:
            raise Unsupported(f"unexpected {self.peek().text!r}")

    def skip_parens(self):
        """Skip a balanced (...) group; the cursor is on '('."""
        depth = 0
        while True:
            t = self.next()
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
                if depth == 0:
                    return

    # -- expressions
    def expr(self):
        e = self.or_()
        while self.at_dop(".eqv.", ".neqv."):
            op = self.next().text
            e = Binary("==" if op == ".eqv." else "/=", e, self.or_())
        return e

    def or_(self):
        e = self.and_()
        while self.at_dop(".or."):
            self.k += 1
            e = Binary("or", e, self.and_())
        return e

    def and_(self):
        e = self.not_()
        while self.at_dop(".and."):
            self.k += 1
            e = Binary("and", e, self.not_())
        return e

    def not_(self):
        if self.at_dop(".not."):
            self.k += 1
            return Unary("not", self.not_())
        return self.relational()

    REL = {"==": "==", "/=": "/=", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
           ".eq.": "==", ".ne.": "/=", ".lt.": "<", ".le.": "<=", ".gt.": ">", ".ge.": ">="}

    def relational(self):
        e = self.concat()
        t = self.peek()
        if t is not None and t.kind in ("op", "dop") and t.text in self.REL:
            self.k += 1
            e = Binary(self.REL[t.text], e, self.concat())
        return e

    def concat(self):
        e = self.add()
        while self.at_op("//"):
            self.k += 1
            e = Binary("+", e, self.add())
        return e

    def add(self):
        if self.at_op("+", "-"):                 # a leading sign covers the whole first term
            op = self.next().text
            e = Unary(op, self.mul())
        else:
            e = self.mul()
        while self.at_op("+", "-"):
            op = self.next().text
            e = Binary(op, e, self.mul())
        return e

    def mul(self):
        e = self.power()
        while self.at_op("*", "/"):
            op = self.next().text
            e = Binary(op, e, self.power())
        return e

    def power(self):
        base = self.primary()
        if self.accept_op("**"):
            if self.at_op("+", "-"):
                op = self.next().text
                return Binary("**", base, Unary(op, self.power()))
            return Binary("**", base, self.power())
        return base

    def primary(self):
        t = self.next()
        if t.kind == "int" or t.kind == "real":
            return Num(t.value)
        if t.kind == "str":
            if '"' in t.value:
                raise Unsupported("string containing a double quote")
            return Str(t.value)
        if t.kind == "dop" and t.text in (".true.", ".false."):
            return Bool(t.text == ".true.")
        if t.kind == "op" and t.text == "(":
            e = self.expr()
            if self.at_op(","):
                raise Unsupported("complex literal or implied-do")
            self.expect_op(")")
            return e
        if t.kind == "op" and t.text in ("[", "(/"):
            close = "]" if t.text == "[" else "/)"
            items = []
            if not self.at_op(close):
                items.append(self.expr())
                while self.accept_op(","):
                    items.append(self.expr())
            if not self.accept_op(close):
                raise Unsupported("implied-do or malformed array constructor")
            return ArrayLit(items)
        if t.kind == "name":
            name = t.text
            if self.at_op("%"):
                raise Unsupported("derived-type component")
            if not self.at_op("("):
                return Var(self.rename(name))
            self.k += 1
            args = []
            if not self.at_op(")"):
                while True:
                    a = self.argument(name)
                    if a is not None:
                        args.append(a)
                    if not self.accept_op(","):
                        break
            self.expect_op(")")
            if name in ("dble", "float"):
                name = "real"
            if name in ("real", "int", "dble") and len(args) > 1:
                self.note(self.line, f"{name}(): kind argument dropped")
                args = args[:1]
            intrinsic = name in BUILTINS or name in ELEMENTAL
            return Apply(name if intrinsic else self.rename(name), args, self.line)
        raise Unsupported(f"unexpected {t.text!r} in expression")

    def argument(self, fname):
        t, t2 = self.peek(), self.peek(1)
        if t is not None and t.kind == "name" and t2 is not None and t2.kind == "op" and t2.text == "=":
            if t.text == "kind":
                self.k += 2
                self.expr()
                return None
            raise Unsupported(f"keyword argument '{t.text}=' in {fname}()")
        lo = None if self.at_op(":") else self.expr()
        if self.accept_op(":"):
            hi = None if self.at_op(",", ")") else self.expr()
            if self.at_op(":"):
                raise Unsupported("array stride in a section")
            return Slice(lo, hi)
        return lo

    # -- file structure
    def tokens(self, it):
        if it.toks is None:
            it.toks = tokenize(it.text)
        return it.toks

    def unit_kind(self, toks):
        """'program', 'function', 'subroutine', a block word like 'module', or None."""
        if not toks or toks[0].kind != "name":
            return None
        if toks[0].text == "program" and len(toks) <= 2:
            return "program"
        depth = 0
        for j, t in enumerate(toks):
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
            elif depth == 0 and t.kind == "name" and t.text in ("function", "subroutine"):
                if j + 1 < len(toks) and toks[j + 1].kind == "name":
                    return t.text
                return None
            elif depth == 0 and t.kind == "op" and t.text in ("=", "::"):
                return None
        if toks[0].text in ("module", "submodule", "block"):
            return toks[0].text
        return None

    def parse(self):
        while self.i < len(self.items):
            it = self.items[self.i]
            start = self.i
            if it.comment is not None:
                self.top_comments.append(it.comment)
                self.i += 1
                continue
            self.line = it.line
            try:
                toks = self.tokens(it)
                kind = self.unit_kind(toks)
                if kind == "program":
                    self.i += 1
                    name = self.rename(toks[1].text) if len(toks) > 1 else "main"
                    self.parse_unit("program", Scope("program", name), it)
                elif kind in ("function", "subroutine"):
                    self.i += 1
                    self.parse_procedure(toks, it)
                elif kind in ("module", "submodule"):
                    self.i += 1
                    self.skip_block("module")
                    raise Unsupported("modules are not supported yet; skipped")
                else:
                    raise Unsupported("statement outside any program unit")
            except Unsupported as e:
                if self.i == start:
                    self.i += 1
                self.top_comments.append(f"TODO(convert): {e}: {it.text}")
                self.todo(it.line, f"{e}: {it.text}")
        return self.finalize()

    def procedure_header(self, toks):
        self.toks, self.k = toks, 0
        ret_type = None
        while True:
            if self.at_name("pure", "impure", "elemental", "recursive", "module", "non_recursive"):
                self.k += 1
            elif self.at_name(*TYPE_WORDS):
                ret_type = self.type_spec()
            else:
                break
        kind = self.expect_name()
        name = self.rename(self.expect_name())
        dummies = []
        if self.accept_op("("):
            if not self.at_op(")"):
                while True:
                    if self.at_op("*"):
                        raise Unsupported("alternate return")
                    dummies.append(self.rename(self.expect_name()))
                    if not self.accept_op(","):
                        break
            self.expect_op(")")
        result = None
        if kind == "function" and self.at_name("result"):
            self.k += 1
            self.expect_op("(")
            result = self.rename(self.expect_name())
            self.expect_op(")")
        if self.at_name("bind"):
            self.k += 1
            self.skip_parens()
        self.end_of_statement()
        return kind, name, dummies, result, ret_type

    def parse_procedure(self, toks, it):
        kind, name, dummies, result, ret_type = self.procedure_header(toks)
        scope = Scope(kind, name, dummies)
        scope.result, scope.ret_type = result, ret_type
        self.parse_unit(kind, scope, it)

    def parse_unit(self, kind, scope, it):
        """Parse from just after the header through `end <kind>`, hoisting internal procedures."""
        scope.comments, self.top_comments = self.top_comments, []
        body = self.parse_body(scope, {("end", kind), ("contains",)})
        toks = self.tokens(self.items[self.i])
        if classify(toks) == ("contains",):
            self.i += 1
            self.note(it.line, "internal procedures were moved out of their host; "
                               "variables of the host are not visible to them")
            while True:
                if self.items[self.i].comment is not None:
                    self.i += 1
                    continue
                cur = self.items[self.i]
                self.line = cur.line
                toks = self.tokens(cur)
                if classify(toks) is not None:
                    break
                inner = self.unit_kind(toks)
                self.i += 1
                if inner in ("function", "subroutine"):
                    self.parse_procedure(toks, cur)
                else:
                    self.todo(cur.line, f"unsupported statement after contains: {cur.text}")
        self.i += 1                        # the `end` statement
        self.raw.append(Raw(kind, scope, body, it.line))

    def is_stop(self, toks, stops):
        lab = classify(toks)
        if lab is None:
            return False
        if lab[0] == "end":
            return any(s[0] == "end" and (lab[1] == s[1] or lab[1] is None) for s in stops)
        return lab in stops

    def parse_body(self, scope, stops):
        body = []
        while self.i < len(self.items):
            it = self.items[self.i]
            start = self.i
            if it.comment is not None:
                body.append(Comment(it.comment))
                self.i += 1
                continue
            self.line = it.line
            try:
                toks = self.tokens(it)
                if self.is_stop(toks, stops):
                    return body
                self.i += 1
                nodes = self.statement(it, toks, scope)
                bad = self.uses_skipped(nodes, scope)
                if bad:
                    raise Unsupported(f"uses '{bad}', whose declaration could not be translated")
                body.extend(nodes)
            except Unsupported as e:
                if self.i == start:
                    self.i += 1
                body.append(self.todo_comment(it, str(e)))
        raise LangError("unexpected end of file: a block is missing its 'end' statement")

    def uses_skipped(self, nodes, scope):
        if not scope.skipped:
            return None
        for n in iter_nodes(nodes):
            for name in (getattr(n, "name", None), getattr(n, "var", None)):
                if isinstance(name, str) and name in scope.skipped and not isinstance(n, (Decl, Function)):
                    return name
        return None

    def skip_block(self, kind):
        """Consume statements through the `end <kind>` matching an already-consumed opener."""
        depth = 1
        while self.i < len(self.items):
            it = self.items[self.i]
            self.i += 1
            if it.comment is not None:
                continue
            try:
                toks = self.tokens(it)
            except Unsupported:
                continue
            lab = classify(toks)
            if lab is not None and lab[0] == "end" and lab[1] == kind:
                depth -= 1
                if depth == 0:
                    return
            elif toks and toks[0].kind == "name" and toks[0].text == kind and lab is None:
                if kind == "type" and len(toks) > 1 and toks[1].text == "(":
                    continue
                if kind == "module" and len(toks) > 1 and toks[1].text == "procedure":
                    continue
                depth += 1

    # -- statements
    def statement(self, it, toks, scope):
        self.toks, self.k = toks, 0
        t0 = toks[0]
        if t0.kind != "name":
            raise Unsupported("unrecognised statement (labels are not supported)")
        name = t0.text
        if len(toks) > 2 and toks[1].kind == "op" and toks[1].text == ":" and toks[2].kind == "name":
            self.k = 2                                # strip a construct name: `outer: do ...`
            self.note(it.line, f"construct name '{name}' dropped")
            name = toks[2].text
        elif self.is_assignment():
            return [self.assignment(it)]
        if any(t.kind == "op" and t.text == "%" for t in toks):
            raise Unsupported("derived-type component")
        if name in TYPE_WORDS and name != "type" and not (len(toks) > self.k + 1 and toks[self.k + 1].text == "="):
            return self.declaration(it, scope)
        handler = getattr(self, "st_" + name, None)
        if handler is not None:
            self.k += 1
            return handler(it, scope)
        if name in ("go", "goto", "common", "equivalence", "data", "namelist", "include",
                    "allocate", "deallocate", "nullify", "read", "open", "close", "inquire", "rewind",
                    "backspace", "format", "entry", "pause", "dimension", "parameter", "pointer",
                    "target", "intrinsic", "import", "enum", "sync", "lock", "unlock"):
            raise Unsupported(f"'{name}' is not supported")
        if name in BLOCK_KINDS or name in ("endselect",):
            raise Unsupported(f"'{name}' is not supported")
        raise Unsupported("unrecognised statement")

    def is_assignment(self):
        t = self.peek(1)
        if t is None:
            return False
        if t.kind == "op" and t.text == "=":
            return True
        if t.kind == "op" and t.text == "(":
            depth, j = 0, self.k + 1
            while j < len(self.toks):
                tj = self.toks[j]
                if tj.kind == "op" and tj.text == "(":
                    depth += 1
                elif tj.kind == "op" and tj.text == ")":
                    depth -= 1
                    if depth == 0:
                        nxt = self.toks[j + 1] if j + 1 < len(self.toks) else None
                        return nxt is not None and nxt.kind == "op" and nxt.text in ("=", "=>")
                j += 1
        return False

    def assignment(self, it):
        target = self.primary()
        if not isinstance(target, (Var, Apply)):
            raise Unsupported("cannot assign to this expression")
        if self.at_op("=>"):
            raise Unsupported("pointer assignment")
        self.expect_op("=")
        value = self.expr()
        self.end_of_statement()
        return Assign(target, value, it.line)

    # declarations
    def type_spec(self):
        word = self.expect_name()
        if word == "double":
            if not self.at_name("precision"):
                raise Unsupported("expected 'double precision'")
            self.k += 1
            self.note(self.line, "double precision became real (Boreas reals are double precision)", once=True)
            return "real"
        if word in ("complex", "type"):
            raise Unsupported(f"'{word}' is not supported")
        if self.at_op("("):
            self.skip_parens()
            if word != "character":
                self.note(self.line, f"{word} kind dropped (Boreas has one integer and one real kind)", once=True)
        elif self.at_op("*") and self.peek(1) is not None and self.peek(1).kind == "int":
            self.k += 2
        elif word == "real":
            self.note(self.line, "default 'real' is single precision in Fortran but double in Boreas, "
                                 "so results can differ in the last digits", once=True)
        return "string" if word == "character" else word

    def dim_specs(self):
        """Parse the inside of (...) after a name: a list of (lower, upper) pairs."""
        specs = []
        self.expect_op("(")
        while True:
            if self.at_op(":"):
                self.k += 1
                specs.append("deferred")
            elif self.at_op("*"):
                self.k += 1
                specs.append("assumed")
            else:
                first = self.expr()
                if self.accept_op(":"):
                    if self.at_op(",", ")"):
                        specs.append("deferred")
                    else:
                        specs.append((first, self.expr()))
                else:
                    specs.append((None, first))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return specs

    def remember_skipped(self, scope):
        """After an untranslatable declaration, note which names it declared."""
        toks = self.toks
        if not any(t.kind == "op" and t.text == "::" for t in toks):
            return
        j = next(n for n, t in enumerate(toks) if t.kind == "op" and t.text == "::") + 1
        depth, expect = 0, True
        for t in toks[j:]:
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
            elif depth == 0 and t.kind == "op" and t.text == ",":
                expect = True
            elif depth == 0 and expect and t.kind == "name":
                scope.skipped.add(self.rename(t.text))
                expect = False
            else:
                expect = False

    def declaration(self, it, scope):
        try:
            return self.declaration_inner(it, scope)
        except Unsupported:
            self.remember_skipped(scope)
            raise

    def declaration_inner(self, it, scope):
        typ = self.type_spec()
        attrs = {}
        while self.accept_op(","):
            word = self.expect_name()
            if word in ("intent", "dimension"):
                if word == "intent":
                    self.expect_op("(")
                    attrs["intent"] = self.expect_name()
                    if attrs["intent"] == "in" and self.at_name("out"):
                        self.k += 1
                        attrs["intent"] = "inout"
                    self.expect_op(")")
                else:
                    attrs["dimension"] = self.dim_specs()
            elif word in ("parameter", "save", "public", "private", "target", "intrinsic", "value", "optional", "external"):
                attrs[word] = True
                if word == "optional":
                    self.note(it.line, "'optional' dropped: Boreas has no optional arguments")
            elif word in ("allocatable", "pointer", "volatile", "asynchronous", "protected"):
                raise Unsupported(f"'{word}' declarations are not supported")
            else:
                raise Unsupported(f"unknown attribute '{word}'")
        self.accept_op("::")
        out = []
        while True:
            line = it.line
            try:
                name = self.rename(self.expect_name())
                specs = self.dim_specs() if self.at_op("(") else attrs.get("dimension")
                if self.at_op("*") and self.peek(1) is not None and self.peek(1).kind == "int":
                    self.k += 2
                init = None
                if self.at_op("=>"):
                    raise Unsupported("pointer initialisation")
                if self.accept_op("="):
                    init = self.expr()
                dims, dropped = None, False
                if specs is not None:
                    if name in scope.dummies:
                        dropped = True
                        self.note(line, f"shape of dummy argument '{name}' dropped; "
                                        f"Boreas takes it from the argument")
                    else:
                        dims = []
                        for s in specs:
                            if not isinstance(s, tuple):
                                raise Unsupported("deferred or assumed shape on a local array")
                            lo, hi = s
                            if lo is not None and not (isinstance(lo, Num) and lo.value == 1):
                                raise Unsupported("array lower bound other than 1")
                            dims.append(hi)
                if attrs.get("parameter"):
                    self.note(line, "constants declared with 'parameter' became ordinary variables "
                                    "(Boreas has no parameters)", once=True)
                elif init is not None and scope.kind != "program" and name not in scope.dummies:
                    self.note(line, f"'{name}' is initialised in a declaration inside a procedure: "
                                    f"Fortran implicitly SAVEs it between calls, Boreas re-initialises it each call")
                scope.decls[name] = Info(typ, dims, dropped, attrs.get("intent"), init,
                                         bool(attrs.get("parameter")), line)
                if attrs.get("external"):
                    scope.externals.add(name)
            except Unsupported as e:
                out.append(self.todo_comment(it, f"declaration of an entity skipped ({e})"))
                if self.toks[self.k - 1:self.k] and self.toks[self.k - 1].kind == "name":
                    scope.skipped.add(self.toks[self.k - 1].text)
                while self.peek() is not None and not self.at_op(","):
                    self.k += 1
            if not self.accept_op(","):
                break
        self.end_of_statement()
        return out

    def st_implicit(self, it, scope):
        if self.at_name("none"):
            scope.implicit_none = True
            return []
        raise Unsupported("only 'implicit none' is supported")

    def st_use(self, it, scope):
        if self.at_name("iso_fortran_env", "iso_c_binding", "ieee_arithmetic"):
            self.note(it.line, f"'{it.text}' ignored (intrinsic module)")
            return []
        raise Unsupported("modules are not supported yet")

    def st_save(self, it, scope):
        return []

    def st_external(self, it, scope):
        while self.peek() is not None:
            self.k += 1
        self.note(it.line, f"'{it.text}' ignored: Boreas finds procedures by name")
        return []

    def st_continue(self, it, scope):
        return []

    def st_contains(self, it, scope):
        raise Unsupported("unexpected 'contains'")

    # control flow
    def st_if(self, it, scope):
        self.expect_op("(")
        cond = self.expr()
        self.expect_op(")")
        if self.at_name("then") and self.peek(1) is None:
            branches, orelse = [], []
            stops = {("end", "if"), ("else",), ("elseif",)}
            body = self.parse_body(scope, stops)
            branches.append((cond, body))
            while True:
                cur = self.items[self.i]
                self.i += 1
                lab = classify(self.tokens(cur))
                if lab == ("elseif",):
                    self.toks = self.tokens(cur)
                    self.k = 2 if self.toks[0].text == "else" else 1
                    self.expect_op("(")
                    c = self.expr()
                    self.expect_op(")")
                    if not self.at_name("then"):
                        raise Unsupported("expected 'then'")
                    branches.append((c, self.parse_body(scope, stops)))
                elif lab == ("else",):
                    orelse = self.parse_body(scope, {("end", "if")})
                else:
                    break
            return [If(branches, orelse)]
        if self.peek() is None:
            raise Unsupported("expected a statement after 'if (...)'")
        inner = self.statement(it, self.toks[self.k:], scope)
        return [If([(cond, inner)], [])]

    def st_do(self, it, scope):
        if self.at_name("concurrent"):
            self.skip_block("do")
            raise Unsupported("'do concurrent' is not supported; skipped")
        if self.peek() is not None and self.peek().kind == "int":
            raise Unsupported("labelled do loops are not supported")
        if self.at_name("while"):
            self.k += 1
            self.expect_op("(")
            cond = self.expr()
            self.expect_op(")")
            self.end_of_statement()
            return [DoWhile(cond, self.block_until(scope, "do"))]
        if self.peek() is None:
            return [DoWhile(Bool(True), self.block_until(scope, "do"))]
        var = self.rename(self.expect_name())
        self.expect_op("=")
        lo = self.expr()
        self.expect_op(",")
        hi = self.expr()
        step = self.expr() if self.accept_op(",") else None
        self.end_of_statement()
        return [Do(var, lo, hi, step, self.block_until(scope, "do"), it.line)]

    def block_until(self, scope, kind):
        body = self.parse_body(scope, {("end", kind)})
        self.i += 1
        return body

    def st_exit(self, it, scope):
        if self.peek() is not None:
            raise Unsupported("'exit' with a construct name")
        return [Exit()]

    def st_cycle(self, it, scope):
        if self.peek() is not None:
            raise Unsupported("'cycle' with a construct name")
        return [Cycle()]

    def st_return(self, it, scope):
        if self.peek() is not None:
            raise Unsupported("alternate return")
        return [Return(None)]

    def st_stop(self, it, scope):
        return [StopMarker("TODO(convert): `stop` exits the program; it was not translated")]

    def st_error(self, it, scope):
        return self.st_stop(it, scope)

    def st_call(self, it, scope):
        name = self.rename(self.expect_name())
        args = []
        if self.accept_op("("):
            if not self.at_op(")"):
                while True:
                    a = self.argument(name)
                    if a is not None:
                        args.append(a)
                    if not self.accept_op(","):
                        break
            self.expect_op(")")
        self.end_of_statement()
        return [Call(name, args, it.line)]

    def st_select(self, it, scope):
        self.skip_block("select")
        raise Unsupported("'select case' is not supported yet; skipped")

    def st_associate(self, it, scope):
        self.skip_block("associate")
        raise Unsupported("'associate' is not supported; skipped")

    def st_interface(self, it, scope):
        self.skip_block("interface")
        raise Unsupported("interface blocks are not supported; skipped")

    def block_form(self, kind):
        if self.toks[-1].text == ")":              # nothing after the condition: a block construct
            self.skip_block(kind)
            raise Unsupported(f"'{kind}' blocks are not supported; skipped")
        raise Unsupported(f"'{kind}' is not supported")

    def st_where(self, it, scope):
        self.block_form("where")

    def st_forall(self, it, scope):
        self.block_form("forall")

    def st_type(self, it, scope):
        if self.at_op("("):
            self.remember_skipped(scope)
            raise Unsupported("derived-type variables are not supported")
        self.skip_block("type")
        raise Unsupported("derived-type definitions are not supported; skipped")

    # output
    def print_items(self):
        items = []
        if not self.at_op(")") and self.peek() is not None:
            items.append(self.expr())
            while self.accept_op(","):
                items.append(self.expr())
        self.end_of_statement()
        return items or [Str("")]

    def st_print(self, it, scope):
        if self.at_op("*"):
            self.k += 1
        elif self.peek() is not None and self.peek().kind == "str":
            self.note(it.line, f"print format {self.peek().text} dropped; Boreas uses default formatting")
            self.k += 1
        else:
            raise Unsupported("unsupported print format")
        if self.peek() is not None:
            self.expect_op(",")
        return [Print(self.print_items())]

    def st_write(self, it, scope):
        self.expect_op("(")
        parts, depth, cur = [], 1, []
        while depth:
            t = self.next()
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
                if depth == 0:
                    break
            if depth == 1 and t.kind == "op" and t.text == ",":
                parts.append(cur)
                cur = []
            else:
                cur.append(t)
        parts.append(cur)
        unit = parts[0]
        if not (len(unit) == 1 and (unit[0].text == "*" or unit[0].value == 6)):
            raise Unsupported("write to a file unit is not supported")
        if len(parts) > 1:
            fmt = parts[1]
            if len(fmt) == 1 and fmt[0].text == "*":
                pass
            elif len(parts) == 2 and len(fmt) == 1 and fmt[0].kind == "str":
                self.note(it.line, f"write format {fmt[0].text} dropped; Boreas uses default formatting")
            else:
                raise Unsupported("unsupported write format or option")
        if len(parts) > 2:
            raise Unsupported("unsupported write options")
        return [Print(self.print_items())]

    # -- finishing: turn raw scopes into Boreas procedures
    def finalize(self):
        proc_names = {r.scope.name for r in self.raw if r.kind != "program"}
        functions, program = {}, None
        for r in self.raw:
            if r.kind == "program":
                program = self.build_program(r, proc_names)
            else:
                if r.scope.name in functions:
                    self.todo(r.line, f"'{r.scope.name}' is defined more than once")
                functions[r.scope.name] = self.build_procedure(r, proc_names)
        unit = Unit(functions, program)
        self.infer_intents(unit)
        self.check_calls(unit)
        if program is not None:
            while program.body and isinstance(program.body[-1], StopMarker):
                program.body.pop()
        return unit

    def local_decls(self, scope, body, proc_names=()):
        decls = []
        for name, info in scope.decls.items():
            if name in scope.dummies:
                continue
            if name in proc_names or name in scope.externals:
                self.note(info.line, f"declaration of '{name}' dropped: it is a procedure, not a variable")
                continue
            decls.append(Decl(info.type, name, info.dims, info.init, info.line))
        return decls

    def implicit_decls(self, scope, body, proc_names, line):
        known = set(scope.decls) | set(scope.dummies) | proc_names
        used = {}
        for n in iter_nodes(body):
            if isinstance(n, Var):
                used.setdefault(n.name, True)
            elif isinstance(n, Do):
                used.setdefault(n.var, True)
        out = []
        for name in used:
            if name in known:
                continue
            if scope.implicit_none:
                self.note(line, f"'{name}' is not declared (the source says 'implicit none')")
                continue
            typ = implicit_type(name)
            self.note(line, f"'{name}' had no declaration; declared as {typ} using Fortran's implicit rules")
            out.append(Decl(typ, name, None, None, line))
        return out

    def build_program(self, r, proc_names):
        s = r.scope
        decls = self.implicit_decls(s, r.body, proc_names, r.line)
        body = decls + self.local_decls(s, r.body, proc_names) + r.body
        return Program(s.name, body, list(s.comments))

    def build_procedure(self, r, proc_names):
        s, body = r.scope, r.body
        is_sub = r.kind == "subroutine"
        line = r.line
        result = None
        ret = None
        if not is_sub:
            result = s.result or s.name
            if result == s.name:                       # the function name doubles as the result variable
                result = self.unique(s.name + "_result", s, body)
                self.note(line, f"result variable of '{s.name}' is called '{result}' in Boreas")
                self.rename_in(s, body, s.name, result)
            info = s.decls.get(result)
            if info is None and s.ret_type:
                s.decls[result] = info = Info(s.ret_type, None, False, None, None, False, line)
            if info is None:
                s.decls[result] = info = Info(implicit_type(s.name), None, False, None, None, False, line)
                self.note(line, f"result of '{s.name}' has no declared type; assumed {info.type}")
            if info.dims:
                self.todo(line, f"function '{s.name}' returns an array; Boreas functions return scalars only")
            ret = info.type
            self.fix_returns(body, result)
            body.append(Return(Var(result)))
        decls = self.implicit_decls(s, body, proc_names | {s.name}, line)
        params = []
        for d in s.dummies:
            info = s.decls.get(d)
            if info is None:
                typ = implicit_type(d)
                if s.implicit_none:
                    self.note(line, f"dummy argument '{d}' has no declaration")
                else:
                    self.note(line, f"dummy argument '{d}' had no declaration; assumed {typ}")
                params.append([typ, d, None])
            else:
                params.append([info.type, d, info.intent])
        fn = Function(s.name, params, ret, decls + self.local_decls(s, body, proc_names) + body, is_sub, list(s.comments))
        return fn

    def unique(self, base, scope, body):
        names = set(scope.decls) | set(scope.dummies) | {n.name for n in iter_nodes(body) if isinstance(n, (Var, Apply))}
        while base in names:
            base += "_"
        return base

    def rename_in(self, scope, body, old, new):
        for n in iter_nodes(body):
            if isinstance(n, (Var, Apply)) and n.name == old:
                n.name = new
            elif isinstance(n, Do) and n.var == old:
                n.var = new
        if old in scope.decls:
            scope.decls[new] = scope.decls.pop(old)

    def fix_returns(self, stmts, result):
        for n, s in enumerate(stmts):
            if isinstance(s, Return) and s.value is None:
                stmts[n] = Return(Var(result))
            elif isinstance(s, If):
                for _, b in s.branches:
                    self.fix_returns(b, result)
                self.fix_returns(s.orelse, result)
            elif isinstance(s, (Do, DoWhile)):
                self.fix_returns(s.body, result)

    # -- parameter modes
    def infer_intents(self, unit):
        """Dummy arguments without `intent` get one from how the body uses them."""
        procs = unit.functions
        lines = {r.scope.name: r.line for r in self.raw}
        inferred = {(f.name, p[1]) for f in procs.values() for p in f.params if p[2] is None}
        for _ in range(10):                        # callers depend on callees: iterate to a fixed point
            changed = False
            for f in procs.values():
                for p in f.params:
                    if (f.name, p[1]) not in inferred:
                        continue
                    reads, writes = self.usage(f.body, p[1], procs)
                    new = "in" if not writes else ("out" if not reads and p[0] != "string" else "inout")
                    if p[2] != new:
                        p[2], changed = new, True
            if not changed:
                break
        for f in procs.values():
            line = lines.get(f.name, 0)
            for p in f.params:
                if p[2] is None:
                    p[2] = "in"
                if (f.name, p[1]) in inferred and p[2] != "in":
                    f.comments.append(f"note: argument '{p[1]}' has no intent in the Fortran; "
                                      f"'{p[2]}' was inferred from how the body uses it")
                    self.note(line, f"{f.name}: argument '{p[1]}' had no intent; inferred '{p[2]}'")
                if not f.is_sub and p[2] != "in":
                    f.comments.append(f"TODO(convert): this function modifies argument '{p[1]}'; Boreas "
                                      f"functions only take 'in' arguments, so make it a subroutine")
                    self.todo(line, f"function '{f.name}' modifies argument '{p[1]}'")
                    p[2] = "in"

    def usage(self, body, name, procs):
        """Count (reads, writes) of `name` in a statement list."""
        counts = [0, 0]

        def read_expr(e):
            for n in iter_nodes(e):
                if isinstance(n, (Var, Apply)) and n.name == name:
                    counts[0] += 1

        def ref_name(e):
            return e.name if isinstance(e, (Var, Apply)) else None

        def scan(stmts):
            for s in stmts:
                if isinstance(s, list):
                    scan(s)
                elif isinstance(s, Assign):
                    if ref_name(s.target) == name:
                        counts[1] += 1
                        if isinstance(s.target, Apply):
                            for a in s.target.args:
                                read_expr(a)
                    else:
                        read_expr(s.target)
                    read_expr(s.value)
                elif isinstance(s, If):
                    for cond, b in s.branches:
                        read_expr(cond)
                        scan(b)
                    scan(s.orelse)
                elif isinstance(s, Do):
                    if s.var == name:
                        counts[1] += 1
                    read_expr([s.lo, s.hi, s.step])
                    scan(s.body)
                elif isinstance(s, DoWhile):
                    read_expr(s.cond)
                    scan(s.body)
                elif isinstance(s, Call):
                    callee = procs.get(s.name)
                    for i, a in enumerate(s.args):
                        if ref_name(a) == name:
                            if callee is None:
                                mode = "inout"
                            else:
                                mode = (callee.params[i][2] or "in") if i < len(callee.params) else "in"
                            if mode in ("in", "inout"):
                                counts[0] += 1
                            if mode in ("out", "inout"):
                                counts[1] += 1
                        else:
                            read_expr(a)
                elif isinstance(s, (Print, Return, ExprStmt)):
                    read_expr(getattr(s, "args", None) or getattr(s, "value", None) or getattr(s, "expr", None))

        scan(body)
        return counts[0], counts[1]

    def check_calls(self, unit):
        known = set(unit.functions) | set(BUILTINS) | set(ELEMENTAL)
        scopes = [(f.name, f) for f in unit.functions.values()]
        if unit.program:
            scopes.append((unit.program.name, unit.program))
        for owner, node in scopes:
            declared = {d.name for d in iter_nodes(node.body) if isinstance(d, Decl)}
            declared |= {p[1] for p in getattr(node, "params", [])}
            for n in iter_nodes(node.body):
                if isinstance(n, Apply) and n.name not in declared and n.name not in known:
                    self.note(n.line, f"'{n.name}' is not a Boreas built-in or a procedure in this file")
                elif isinstance(n, Call) and n.name not in unit.functions:
                    self.note(n.line, f"subroutine '{n.name}' is not defined in this file")


def parse_fortran(src):
    p = FParser(src)
    unit = p.parse()
    return unit, p.top_comments, p.findings


def convert(src, filename="input"):
    """Fortran source -> (Boreas source, findings). Each finding is (line, kind, message)."""
    unit, trailing, findings = parse_fortran(src)
    todos = sum(1 for f in findings if f[1] == "todo")
    header = [f"Converted from {filename} by `boreas convert`."]
    if todos:
        header.append(f"{todos} construct(s) could not be translated: search for TODO(convert).")
    header.extend(trailing)
    return to_source(unit, header), findings
