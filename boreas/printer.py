"""Boreas AST -> Boreas source text.

Expressions are printed with Boreas's own precedence (not Fortran's), adding
parentheses only where the Boreas parser would otherwise group differently."""
from .lexer import LangError
from .parser import *

# Higher binds tighter. Mirrors the grammar in parser.py:
# or < and < not < compare < add < mul < unary < **
PREC = {"or": 1, "and": 2, "==": 4, "/=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4,
        "+": 5, "-": 5, "*": 6, "/": 6, "**": 8}
ATOM = 10


def prec(e):
    if isinstance(e, Binary):
        return PREC[e.op]
    if isinstance(e, Unary):
        return 3 if e.op == "not" else 7
    return ATOM


def expr(e, need=0):
    s = _expr(e)
    return f"({s})" if prec(e) < need else s


def _expr(e):
    k = type(e)
    if k is Num:
        if isinstance(e.value, float):
            if e.value != e.value or e.value in (float("inf"), float("-inf")):
                raise LangError("cannot print a non-finite number")
            return repr(e.value)
        return str(e.value)
    if k is Str:
        if '"' in e.value or "\n" in e.value:
            raise LangError("a string with a double quote or newline cannot be written")
        return f'"{e.value}"'
    if k is Bool:
        return "true" if e.value else "false"
    if k is Var:
        return e.name
    if k is ArrayLit:
        return "[" + ", ".join(expr(x) for x in e.items) + "]"
    if k is Slice:
        return (expr(e.lo) if e.lo is not None else "") + ":" + (expr(e.hi) if e.hi is not None else "")
    if k is Apply:
        return f"{e.name}(" + ", ".join(expr(a) for a in e.args) + ")"
    if k is Unary:
        sep = " " if e.op == "not" else ""
        return f"{e.op}{sep}{expr(e.operand, prec(e))}"
    if k is Binary:
        p = PREC[e.op]
        if e.op == "**":                  # Boreas: primary ** unary, right-associative
            left, right = expr(e.left, ATOM), expr(e.right, 7)
        elif p == 4:                      # comparisons do not chain
            left, right = expr(e.left, p + 1), expr(e.right, p + 1)
        else:
            left, right = expr(e.left, p), expr(e.right, p + 1)
        return f"{left} {e.op} {right}"
    raise LangError(f"cannot print expression {k.__name__}")


def _params(f):
    out = []
    for typ, name, intent in f.params:
        mode = "" if intent == "in" else intent + " "
        out.append(f"{mode}{typ} :: {name}")
    return ", ".join(out)


def _decl_head(d):
    return ("public " if d.public else "") + ("const " if d.const else "") + d.type


def _decl_entity(d):
    s = d.name
    if d.dims is not None:
        s += "(" + ", ".join(expr(x) for x in d.dims) + ")"
    if d.init is not None:
        s += " = " + expr(d.init)
    return s


def _comment(text):
    return "! " + " ".join(str(text).split())


def stmts(body, depth=1):
    """Print a statement list; consecutive plain declarations of one type share a line."""
    pad = "  " * depth
    flat = []
    for s in body:
        flat.extend(s if isinstance(s, list) else [s])
    lines, i = [], 0
    while i < len(flat):
        s = flat[i]
        if isinstance(s, Decl):
            j = i
            group = []
            while (j < len(flat) and isinstance(flat[j], Decl) and flat[j].type == s.type
                   and flat[j].const == s.const and flat[j].public == s.public and len(group) < 6):
                group.append(flat[j])
                j += 1
            lines.append(f"{pad}{_decl_head(s)} :: " + ", ".join(_decl_entity(d) for d in group))
            i = j
            continue
        lines.extend(stmt(s, depth))
        i += 1
    return lines


def stmt(s, depth):
    pad = "  " * depth
    k = type(s)
    if isinstance(s, Comment):
        return [pad + _comment(s.text)]
    if k is Assign:
        return [f"{pad}{'update ' if s.update else ''}{expr(s.target)} = {expr(s.value)}"]
    if k is Print:
        if not s.args:
            raise LangError("print needs at least one value")
        return [pad + "print " + ", ".join(expr(a) for a in s.args)]
    if k is Call:
        return [f"{pad}call {s.name}(" + ", ".join(expr(a) for a in s.args) + ")"]
    if k is Return:
        return [pad + "return" + (" " + expr(s.value) if s.value is not None else "")]
    if k is Exit:
        return [pad + "exit"]
    if k is Cycle:
        return [pad + "cycle"]
    if k is ExprStmt:
        return [pad + expr(s.expr)]
    if k is If:
        lines = []
        for n, (cond, body) in enumerate(s.branches):
            lines.append(f"{pad}{'if' if n == 0 else 'else if'} {expr(cond)} then")
            lines.extend(stmts(body, depth + 1))
        if s.orelse:
            lines.append(pad + "else")
            lines.extend(stmts(s.orelse, depth + 1))
        lines.append(pad + "end if")
        return lines
    if k is Do:
        head = f"{pad}do {s.var} = {expr(s.lo)}, {expr(s.hi)}"
        if s.step is not None:
            head += f", {expr(s.step)}"
        return [head] + stmts(s.body, depth + 1) + [pad + "end do"]
    if k is DoWhile:
        return [f"{pad}do while {expr(s.cond)}"] + stmts(s.body, depth + 1) + [pad + "end do"]
    if k is Decl:
        return [f"{pad}{_decl_head(s)} :: {_decl_entity(s)}"]
    raise LangError(f"cannot print statement {k.__name__}")


def _uses(uses, depth=1):
    return ["  " * depth + f"use {u.module}: " + ", ".join(u.names) for u in uses]


def module(m):
    lines = [f"module {m.name}"] + _uses(m.uses)
    lines.extend(stmts(m.consts))
    for f in m.functions.values():
        lines.extend("  " + l if l else l for l in procedure(f))
    return lines + [f"end module {m.name}"]


def procedure(f):
    kind = "subroutine" if f.is_sub else "function"
    lines = [_comment(c) for c in f.comments]
    head = f"{'public ' if f.public else ''}{kind} {f.name}({_params(f)})"
    if f.ret:
        head += f" -> {f.ret}"
    lines.append(head)
    lines.extend(_uses(f.uses))
    lines.extend(stmts(f.body))
    lines.append(f"end {kind}")
    return lines


def to_source(unit, header=()):
    """Render a whole Unit. `header` is a sequence of comment lines for the top."""
    blocks = []
    if header:
        blocks.append([_comment(h) for h in header])
    for m in unit.modules.values():
        blocks.append(module(m))
    for f in unit.functions.values():
        blocks.append(procedure(f))
    if unit.program is not None:
        p = unit.program
        blocks.append([_comment(c) for c in p.comments] + [f"program {p.name}"]
                      + _uses(p.uses) + stmts(p.body) + [f"end program {p.name}"])
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


def without_comments(unit):
    """A deep copy of `unit` with every comment removed (for structural round-trip checks)."""
    import copy
    u = copy.deepcopy(unit)

    def strip(stmts):
        stmts[:] = [s for s in stmts if not isinstance(s, Comment)]
        for s in stmts:
            if isinstance(s, If):
                for _, b in s.branches:
                    strip(b)
                strip(s.orelse)
            elif isinstance(s, (Do, DoWhile)):
                strip(s.body)

    for f in list(u.functions.values()) + [f for m in u.modules.values() for f in m.functions.values()]:
        f.comments = []
        strip(f.body)
    if u.program is not None:
        u.program.comments = []
        strip(u.program.body)
    return u
