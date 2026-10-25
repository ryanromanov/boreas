from dataclasses import dataclass, field, fields, is_dataclass
from .lexer import lex, LangError

TYPES = {"integer", "real", "logical", "string"}


# --- AST ---------------------------------------------------------------
@dataclass
class Num:  value: object
@dataclass
class Str:  value: str
@dataclass
class Bool: value: bool
@dataclass
class Var:  name: str
@dataclass
class ArrayLit: items: list
@dataclass
class Slice: lo: object; hi: object
@dataclass
class Apply: name: str; args: list; line: int   # index, slice, or call
@dataclass
class Unary: op: str; operand: object
@dataclass
class Binary: op: str; left: object; right: object
@dataclass
class Arm: values: object; value: object; line: int   # one `case ... then expr` of a select expression
@dataclass
class SelectExpr: subject: object; arms: list; line: int   # exhaustive: one arm has values None (`default`)

@dataclass
class Decl:   # dims: list of exprs or None
    type: str; name: str; dims: object; init: object; line: int; const: bool = False
    public: bool = False       # only meaningful for a module's constants
    allocatable: bool = False  # dims are then one `:` (a Slice) per dimension: the rank
@dataclass
class FieldAccess:    # base.field or base.field(subscripts) for an array field
    base: object; field: str; args: object; line: int
@dataclass
class NamedArg: name: str; value: object     # `x = 1.0` inside a record constructor
@dataclass
class RecordDef:
    name: str
    fields: list          # [Decl], no initializers
    public: bool
    line: int
@dataclass
class Assign: target: object; value: object; line: int; update: bool = False
@dataclass
class If: branches: list; orelse: list   # branches: [(cond, body)]
@dataclass
class Do:
    var: str; lo: object; hi: object; step: object; body: list; line: int
    label: str = None          # `outer: do ...` / `end do outer`
@dataclass
class DoWhile: cond: object; body: list; label: str = None; line: int = 0
@dataclass
class Case:
    values: object      # a list of constants and Slice ranges, or None for `case default`
    body: list
    line: int
@dataclass
class Select:
    subject: object
    cases: list
    line: int
    flow: bool = False  # `select case fallthrough`: a finished body continues into the next case
@dataclass
class Break: pass       # leaves the nearest enclosing select statement
@dataclass
class Print: args: list
@dataclass
class Return: value: object
@dataclass
class Exit: label: str = None; line: int = 0      # label names an enclosing loop
@dataclass
class Cycle: label: str = None; line: int = 0
@dataclass
class ExprStmt: expr: object
@dataclass
class Call: name: str; args: list; line: int
@dataclass
class Allocate: name: str; dims: list; line: int   # allocate a(n, m): one extent per dimension
@dataclass
class Deallocate: name: str; line: int
@dataclass
class Comment: text: str          # only produced by `boreas convert`; the interpreter skips it

@dataclass
class Function:
    """A function or, when is_sub is set, a subroutine."""
    name: str
    params: list          # [(type, name, intent)], intent in {"in", "out", "inout"}
    ret: str | None
    body: list
    is_sub: bool = False
    comments: list = field(default_factory=list)   # printed above the header by `convert`
    uses: list = field(default_factory=list)
    public: bool = False       # only meaningful inside a module
@dataclass
class Program:
    name: str
    body: list
    comments: list = field(default_factory=list)
    uses: list = field(default_factory=list)
@dataclass
class Use: module: str; names: list; line: int
@dataclass
class Module:
    name: str
    uses: list
    consts: list          # [Decl], all const
    functions: dict       # name -> Function
    line: int
    comments: list = field(default_factory=list)   # printed above the header by `convert`
    records: dict = field(default_factory=dict)    # name -> RecordDef
@dataclass
class Unit:
    functions: dict       # top-level procedures (outside any module)
    program: Program      # None for a library-only file
    modules: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)    # top-level record types


def root_name(target):
    """The variable an assignment target writes into: `p` for `p`, `p(2)`, `p.x` and `p.v(2).y`."""
    while isinstance(target, FieldAccess):
        target = target.base
    return target.name


KEYWORDS = {"if", "then", "else", "do", "while", "end", "program", "function", "subroutine", "module",
            "record", "const", "public", "private", "use", "call", "return", "exit", "cycle", "print",
            "select", "case", "default", "fallthrough", "break", "update", "allocate", "deallocate",
            "allocatable", "in", "out", "inout", "true", "false", "and", "or", "not"}


def check_constant(e, what, line):
    """A field default is built from literals, `const` names, arithmetic, array literals,
    record constructors and built-in functions (which names are allowed is checked at link time)."""
    if isinstance(e, (Num, Str, Bool, Var)):
        return
    if isinstance(e, Unary):
        check_constant(e.operand, what, line)
    elif isinstance(e, Binary):
        check_constant(e.left, what, line)
        check_constant(e.right, what, line)
    elif isinstance(e, ArrayLit):
        for x in e.items:
            check_constant(x, what, line)
    elif isinstance(e, Apply):
        for a in e.args:
            check_constant(a.value if isinstance(a, NamedArg) else a, what, line)
    else:
        raise LangError(f"line {line}: {what} must be a constant expression (literals, const names, "
                        f"arithmetic, array literals, record constructors and built-in functions)")


def target_text(t):
    """How an assignment target reads in a message: `p.x`, `a`, `a`."""
    if isinstance(t, FieldAccess):
        return f"{target_text(t.base)}.{t.field}" + ("(...)" if t.args is not None else "")
    return t.name


def check_writes(body, params):
    """`update` marks exactly the writes that reach the caller: assignments to
    out/inout parameters. `params` maps parameter name -> intent."""
    for s in body:
        if isinstance(s, list):
            check_writes(s, params)
        elif isinstance(s, Assign):
            name = root_name(s.target)
            escapes = params.get(name) in ("out", "inout")
            if escapes and not s.update:
                shown = target_text(s.target) if isinstance(s.target, FieldAccess) else name
                raise LangError(f"line {s.line}: '{name}' is an {params[name]} parameter, so the caller sees "
                                f"this write; write 'update {shown} = ...'")
            if s.update and not escapes:
                what = "an 'in' parameter" if params.get(name) == "in" else "a local variable"
                raise LangError(f"line {s.line}: 'update' is for out and inout parameters, but "
                                f"'{name}' is {what}")
        elif isinstance(s, If):
            for _, b in s.branches:
                check_writes(b, params)
            check_writes(s.orelse, params)
        elif isinstance(s, Select):
            for c in s.cases:
                check_writes(c.body, params)
        elif isinstance(s, Do):
            if s.var in params:
                raise LangError(f"line {s.line}: loop variable '{s.var}' cannot be a parameter")
            check_writes(s.body, params)
        elif isinstance(s, DoWhile):
            check_writes(s.body, params)


def sub_bodies(s):
    """The statement lists nested directly inside `s`."""
    if isinstance(s, If):
        return [b for _, b in s.branches] + [s.orelse]
    if isinstance(s, (Do, DoWhile)):
        return [s.body]
    if isinstance(s, Select):
        return [c.body for c in s.cases]
    return []


def check_loops(body, enclosing=()):
    """`exit L` / `cycle L` must name an enclosing loop, and a label cannot be
    reused by a loop inside the loop that already carries it."""
    for s in body:
        if isinstance(s, list):
            check_loops(s, enclosing)
        elif isinstance(s, (Exit, Cycle)) and s.label is not None and s.label not in enclosing:
            word = "exit" if isinstance(s, Exit) else "cycle"
            if enclosing:
                known = "enclosing loops are " + ", ".join(f"'{l}'" for l in enclosing)
            else:
                known = "this is not inside a labeled loop"
            raise LangError(f"line {s.line}: '{word} {s.label}' does not name an enclosing loop ({known})")
        else:
            inner = enclosing
            if isinstance(s, (Do, DoWhile)) and s.label is not None:
                if s.label in enclosing:
                    raise LangError(f"line {s.line}: loop label '{s.label}' is already "
                                    f"used by an enclosing loop")
                inner = enclosing + (s.label,)
            for b in sub_bodies(s):
                check_loops(b, inner)


# --- select case: checks shared by the parser and the interpreter ---------
NOT_LITERAL = object()


def literal_value(e):
    """The value of a literal case value (a number, string, logical or signed number), else NOT_LITERAL."""
    if isinstance(e, (Num, Str, Bool)):
        return e.value
    if isinstance(e, Unary) and e.op in "+-" and isinstance(e.operand, Num):
        return -e.operand.value if e.op == "-" else e.operand.value
    return NOT_LITERAL


def literal_type(v):
    return "logical" if isinstance(v, bool) else "integer" if isinstance(v, int) else \
           "real" if isinstance(v, float) else "string"


def show_value(v):
    return ("true" if v else "false") if isinstance(v, bool) else f'"{v}"' if isinstance(v, str) else str(v)


def show_range(lo, hi, point):
    if point:
        return show_value(lo)
    return (show_value(lo) if lo is not None else "") + ":" + (show_value(hi) if hi is not None else "")


def check_overlap(entries, line):
    """`entries` are (lo, hi, is_point) with lo/hi None for an open end; none may share a value."""
    for n, (lo1, hi1, p1) in enumerate(entries):
        for lo2, hi2, p2 in entries[n + 1:]:
            if (lo1 is None or hi2 is None or lo1 <= hi2) and (lo2 is None or hi1 is None or lo2 <= hi1):
                raise LangError(f"line {line}: case values {show_range(lo1, hi1, p1)} and "
                                f"{show_range(lo2, hi2, p2)} overlap")


def check_cases(groups):
    """Parse-time checks on the case values of one select. `groups` is [(values, line)], values
    None for `default`. Only literals can be judged here; the interpreter repeats the overlap
    check once `const` names have values."""
    kinds, entries = set(), []
    if sum(v is None for v, _ in groups) > 1:
        raise LangError(f"line {[l for v, l in groups if v is None][1]}: 'case default' appears more than once")
    for values, line in groups:
        for item in values or []:
            ends = [item.lo, item.hi] if isinstance(item, Slice) else [item]
            lits = [literal_value(x) for x in ends if x is not None]
            for v in lits:
                if v is not NOT_LITERAL:
                    kinds.add(literal_type(v))
            if "real" in kinds:
                raise LangError(f"line {line}: case values cannot be real")
            if len(kinds) > 1:
                raise LangError(f"line {line}: case values mix {' and '.join(sorted(kinds))}")
            if isinstance(item, Slice) and "logical" in kinds:
                raise LangError(f"line {line}: a logical case value cannot be a range")
            if NOT_LITERAL in lits:
                continue
            lo = literal_value(item.lo) if isinstance(item, Slice) and item.lo is not None else None
            hi = literal_value(item.hi) if isinstance(item, Slice) and item.hi is not None else None
            if not isinstance(item, Slice):
                lo = hi = lits[0]
            if lo is not None and hi is not None and lo > hi:
                raise LangError(f"line {line}: the range {show_range(lo, hi, False)} is empty")
            entries.append((lo, hi, not isinstance(item, Slice), line))
    for n in range(len(entries)):                          # report at the later of the two lines
        for m in range(n + 1, len(entries)):
            check_overlap([entries[n][:3], entries[m][:3]], entries[m][3])


def arm_types(arms):
    """The types of the arms that are plain literals (None for the others)."""
    out = set()
    for a in arms:
        v = literal_value(a.value)
        if v is not NOT_LITERAL:
            out.add(literal_type(v))
    return out


def _nodes(obj):
    """Every dataclass node inside `obj` (statements, expressions, lists, tuples), in source order."""
    if isinstance(obj, (list, tuple)):
        for x in obj:
            yield from _nodes(x)
    elif is_dataclass(obj):
        yield obj
        for f in fields(obj):
            yield from _nodes(getattr(obj, f.name))


def check_allocs(body, params):
    """`allocate` and `deallocate` need a local allocatable array of matching rank.
    `params` is the set of parameter names (these can never be allocated)."""
    decls = {d.name: d for d in _nodes(body) if isinstance(d, Decl)}
    for s in _nodes(body):
        if not isinstance(s, (Allocate, Deallocate)):
            continue
        word = "allocate" if isinstance(s, Allocate) else "deallocate"
        d = decls.get(s.name)
        if s.name in params:
            why = "a parameter; only a local allocatable array can be allocated or deallocated"
        elif d is None:
            why = "not declared as a local allocatable array"
        elif not d.allocatable:
            why = ("a constant" if d.const else "not allocatable (declare it with 'allocatable')")
        elif isinstance(s, Allocate) and len(s.dims) != len(d.dims):
            raise LangError(f"line {s.line}: '{s.name}' has {len(d.dims)} dimension(s) but "
                            f"{len(s.dims)} extent(s) were given to allocate")
        else:
            continue
        raise LangError(f"line {s.line}: cannot {word} '{s.name}': it is {why}")


# --- Parser ------------------------------------------------------------
class Parser:
    def __init__(self, src):
        self.toks, self.i = lex(src), 0
        self.inside = []                  # enclosing "select" / "loop" constructs, innermost last

    @property
    def tok(self):
        return self.toks[self.i]

    def error(self, msg):
        raise LangError(f"line {self.tok.line}: {msg} (at {self.tok.text!r})")

    def next(self):
        t = self.tok
        self.i += 1
        return t

    def at(self, *texts):
        return self.tok.kind in ("ident", "op") and self.tok.text in texts

    def accept(self, text):
        if self.at(text):
            return self.next()

    def expect(self, text):
        if not self.at(text):
            self.error(f"expected {text!r}")
        return self.next()

    def ident(self):
        if self.tok.kind != "ident":
            self.error("expected a name")
        return self.next().text

    def newline(self):
        if self.tok.kind not in ("nl", "eof"):
            self.error("expected end of line")
        while self.tok.kind == "nl":
            self.next()

    def skip_newlines(self):
        while self.tok.kind == "nl":
            self.next()

    # -- top level
    def parse(self):
        funcs, mods, prog, recs = {}, {}, None, {}
        self.skip_newlines()
        while self.tok.kind != "eof":
            if self.at("public", "private"):
                self.error("'public' is only for the contents of a module")
            if self.at("function", "subroutine"):
                f = self.procedure()
                if f.name in funcs:
                    raise LangError(f"'{f.name}' is defined more than once")
                funcs[f.name] = f
            elif self.at("record"):
                r = self.record_def(False)
                if r.name in recs:
                    raise LangError(f"line {r.line}: record '{r.name}' is defined more than once")
                recs[r.name] = r
            elif self.at("module"):
                m = self.module()
                if m.name in mods:
                    raise LangError(f"line {m.line}: module '{m.name}' is defined more than once")
                mods[m.name] = m
            elif self.at("program"):
                if prog:
                    self.error("only one program allowed")
                prog = self.program()
            else:
                self.error("expected 'module', 'record', 'function', 'subroutine' or 'program'")
            self.skip_newlines()
        if not prog:
            raise LangError("no program found")
        return Unit(funcs, prog, mods, recs)

    def record_def(self, public):
        """`record point` / one `type :: name` line per field / `end record`."""
        line = self.expect("record").line
        name = self.ident()
        if name in KEYWORDS or name in TYPES:
            self.error(f"'{name}' cannot be the name of a record")
        self.newline()
        fields = []
        while not (self.at("end") and self.toks[self.i + 1].text == "record"):
            if self.tok.kind == "eof":
                self.error("unexpected end of file, missing 'end record'")
            if self.at("const", "allocatable", "public", "private"):
                self.error("record fields are plain declarations: 'type :: name'")
            f = self.decl()
            if f.init is not None:
                check_constant(f.init, f"the default of field '{f.name}'", f.line)
            fields.append(f)
            self.skip_newlines()
        self.expect("end")
        self.expect("record")
        if self.tok.kind == "ident":
            self.next()
        self.newline()
        return RecordDef(name, fields, public, line)

    def program(self):
        self.expect("program")
        name = self.ident()
        self.newline()
        uses = self.uses()
        body = self.block("program")
        self.expect("program")
        if self.tok.kind == "ident":
            self.next()
        check_writes(body, {})
        check_loops(body)
        check_allocs(body, set())
        return Program(name, body, uses=uses)

    def uses(self):
        """`use geometry: area, pi` lines at the top of a module, procedure or program."""
        uses = []
        self.skip_newlines()
        while self.at("use"):
            line = self.next().line
            module = self.ident()
            self.expect(":")
            names = [self.ident()]
            while self.accept(","):
                names.append(self.ident())
            self.newline()
            uses.append(Use(module, names, line))
        return uses

    def module(self):
        line = self.expect("module").line
        name = self.ident()
        self.newline()
        uses = self.uses()
        consts, funcs, recs = [], {}, {}
        taken = {}                                     # name -> line, across constants and procedures

        def claim(n, at):
            if n in taken:
                raise LangError(f"line {at}: '{n}' is defined more than once in module '{name}'")
            taken[n] = at

        while not (self.at("end") and self.toks[self.i + 1].text == "module"):
            if self.tok.kind == "eof":
                self.error("unexpected end of file, missing 'end module'")
            if self.at("private"):
                self.error("names in a module are private unless marked 'public'")
            public = bool(self.accept("public"))
            if self.at("const"):
                self.next()
                d = self.decl(const=True)
                for x in (d if isinstance(d, list) else [d]):
                    x.public = public
                    claim(x.name, x.line)
                    consts.append(x)
            elif self.at("record"):
                r = self.record_def(public)
                claim(r.name, r.line)
                recs[r.name] = r
            elif self.at("function", "subroutine"):
                at = self.tok.line
                f = self.procedure()
                f.public = public
                claim(f.name, at)
                funcs[f.name] = f
            elif self.at("allocatable"):
                self.error("a module cannot hold allocatable arrays; declare them inside a procedure")
            elif self.tok.kind == "ident" and (self.tok.text in TYPES or self.toks[self.i + 1].text == "::"):
                self.error("a module holds constants, records and procedures; declare this with 'const'")
            else:
                self.error("expected 'const', 'record', 'function', 'subroutine' or 'end module'")
            self.skip_newlines()
        self.expect("end")
        self.expect("module")
        if self.tok.kind == "ident":
            self.next()
        return Module(name, uses, consts, funcs, line, records=recs)

    def procedure(self):
        kind = self.next().text           # "function" or "subroutine"
        is_sub = kind == "subroutine"
        name = self.ident()
        params = self.params(is_sub)
        ret = None
        if not is_sub and self.accept("->"):
            ret = self.type_name(anywhere=True)
        self.newline()
        uses = self.uses()
        body = self.block(kind)
        self.expect(kind)
        if self.tok.kind == "ident":
            self.next()
        check_writes(body, {n: intent for _, n, intent in params})
        check_loops(body)
        check_allocs(body, {n for _, n, _ in params})
        return Function(name, params, ret, body, is_sub, uses=uses)

    def params(self, is_sub):
        """`(real :: x, inout integer :: n)`; the mode defaults to `in`."""
        self.expect("(")
        params = []
        if not self.at(")"):
            while True:
                intent = "in"
                if self.at("in", "out", "inout"):
                    intent = self.next().text
                    if intent != "in" and not is_sub:
                        self.error("only subroutines may have out or inout parameters")
                t = self.type_name()
                self.expect("::")
                params.append((t, self.ident(), intent))
                if not self.accept(","):
                    break
        self.expect(")")
        return params

    def type_name(self, anywhere=False):
        """A built-in type, or (when a `::` follows, or `anywhere`) the name of a record."""
        t = self.tok
        if t.kind == "ident" and t.text in TYPES:
            return self.next().text
        if t.kind == "ident" and t.text not in KEYWORDS and (anywhere or self.toks[self.i + 1].text == "::"):
            return self.next().text
        self.error("expected a type (integer, real, logical, string, or a record)")

    # -- statements
    def block(self, *closers):
        """Parse statements until `end <closer>` / else / etc. Leaves 'end' consumed
        only when followed by one of `closers`."""
        body = []
        self.skip_newlines()
        while True:
            if self.at("end") and self.toks[self.i + 1].text in closers:
                self.next()
                return body
            if self.at("else") and "if" in closers:
                return body
            if self.tok.kind == "eof":
                self.error(f"unexpected end of file, missing 'end {closers[0]}'")
            body.append(self.statement())
            self.skip_newlines()

    def statement(self):
        t = self.tok
        if t.kind == "ident" and t.text == "const":
            self.next()
            if self.at("allocatable"):
                self.error("an allocatable array cannot be 'const'")
            return self.decl(const=True)
        if t.kind == "ident" and t.text == "allocatable" and self.toks[self.i + 1].text == "const":
            self.error("an allocatable array cannot be 'const'")
        if t.kind == "ident" and t.text == "allocatable" and (self.toks[self.i + 1].text in TYPES
                                                              or self.toks[self.i + 2].text == "::"):
            self.next()
            return self.decl(allocatable=True)
        if t.kind == "ident" and (t.text in TYPES or (t.text not in KEYWORDS and self.toks[self.i + 1].text == "::")):
            return self.decl()
        if self.at("record"):
            self.error("records are defined at the top level or inside a module, not in a procedure")
        if t.kind == "ident" and t.text in ("allocate", "deallocate") and self.toks[self.i + 1].kind == "ident":
            return self.allocation()
        if self.at("if"):
            return self.if_stmt()
        if self.at("do"):
            return self.do_stmt()
        if self.at("select"):
            return self.select_stmt()
        if self.at("fallthrough"):
            self.error("'fallthrough' is only allowed right after 'select case'")
        if self.at("break"):
            if not self.inside:
                self.error("'break' outside a select")
            if self.inside[-1] == "loop":
                self.error("'break' leaves a select, but the nearest enclosing construct is a loop; use 'exit'")
            self.next(); self.newline()
            return Break()
        if self.at("case"):
            self.error("'case' outside a select")
        if self.at("print"):
            self.next()
            args = [self.expr()]
            while self.accept(","):
                args.append(self.expr())
            self.newline()
            return Print(args)
        if self.at("call"):
            self.next()
            name = self.ident()
            args = []
            if self.accept("("):
                if not self.at(")"):
                    args.append(self.arg())
                    while self.accept(","):
                        args.append(self.arg())
                self.expect(")")
            if any(isinstance(a, NamedArg) for a in args):
                self.error("named arguments are only for building records")
            self.newline()
            return Call(name, args, t.line)
        if self.at("return"):
            self.next()
            val = None if self.tok.kind == "nl" else self.expr()
            self.newline()
            return Return(val)
        if self.at("exit", "cycle"):
            word = self.next().text
            label = self.ident() if self.tok.kind == "ident" else None
            self.newline()
            return (Exit if word == "exit" else Cycle)(label, t.line)
        if t.kind == "ident" and self.toks[self.i + 1].text == ":" and self.toks[self.i + 2].text == "do":
            return self.do_stmt()                  # `outer: do ...`
        update = bool(self.accept("update"))
        e = self.expr()
        if update or self.at("="):
            if not isinstance(e, (Var, Apply, FieldAccess)):
                self.error("cannot assign to this expression")
            self.expect("=")
            v = self.expr()
            self.newline()
            return Assign(e, v, t.line, update)
        self.newline()
        return ExprStmt(e)

    def allocation(self):
        """`allocate a(n, m)` or `deallocate a`: one array per statement."""
        t = self.next()
        name = self.ident()
        if t.text == "deallocate":
            self.newline()
            return Deallocate(name, t.line)
        if not self.at("("):
            self.error("expected the extents, as in 'allocate a(n)'")
        self.next()
        dims = [self.expr()]
        while self.accept(","):
            dims.append(self.expr())
        self.expect(")")
        self.newline()
        return Allocate(name, dims, t.line)

    def decl(self, const=False, allocatable=False):
        line = self.tok.line
        typ = self.type_name()
        self.expect("::")
        name = self.ident()
        dims = None
        if allocatable:
            dims = self.deferred_dims(name)
        elif self.accept("("):
            dims = [self.expr()]
            while self.accept(","):
                dims.append(self.expr())
            self.expect(")")
        init = self.expr() if self.accept("=") else None
        if allocatable and init is not None:
            self.error(f"allocatable array '{name}' cannot have an initializer")
        if const and init is None:
            self.error(f"constant '{name}' needs a value")
        if self.at(","):
            self.error("declare one variable per line")
        self.newline()
        return Decl(typ, name, dims, init, line, const, allocatable=allocatable)

    def deferred_dims(self, name):
        """The `(:, :)` after an allocatable name: one Slice per dimension, which fixes the rank."""
        if not self.accept("("):
            self.error(f"allocatable '{name}' needs one ':' per dimension, as in '{name}(:)'")
        dims = []
        while True:
            if not self.at(":"):
                self.error(f"allocatable '{name}' is declared with ':' for each dimension; "
                           f"its sizes come from 'allocate'")
            self.next()
            dims.append(Slice(None, None))
            if not self.accept(","):
                break
        self.expect(")")
        return dims

    def if_stmt(self):
        self.expect("if")
        branches, orelse = [], []
        cond = self.expr()
        self.expect("then")
        self.newline()
        branches.append((cond, self.block("if")))
        # block() stops at `else` (unconsumed) or after consuming `end`
        while self.at("else"):
            self.expect("else")
            if self.accept("if"):
                cond = self.expr()
                self.expect("then")
                self.newline()
                branches.append((cond, self.block("if")))
            else:
                self.newline()
                orelse = self.block("if")
        self.expect("if")
        self.newline()
        return If(branches, orelse)

    def do_stmt(self):
        label = None
        if self.tok.kind == "ident" and self.toks[self.i + 1].text == ":":
            label = self.next().text
            if label in ("end", "else"):
                self.error(f"'{label}' cannot be used as a loop label")
            self.next()                            # the ':'
        line = self.expect("do").line
        if self.accept("while"):
            cond = self.expr()
            self.newline()
            self.inside.append("loop")
            body = self.block("do")
            self.inside.pop()
            self.expect("do")
            self.end_label(label)
            return DoWhile(cond, body, label, line)
        var = self.ident()
        self.expect("=")
        lo = self.expr()
        self.expect(",")
        hi = self.expr()
        step = self.expr() if self.accept(",") else None
        self.newline()
        self.inside.append("loop")
        body = self.block("do")
        self.inside.pop()
        self.expect("do")
        self.end_label(label)
        return Do(var, lo, hi, step, body, line, label)

    def end_label(self, label):
        """After `end do`: a labeled loop must repeat its label, an unlabeled one must not have one."""
        if label is not None:
            if self.tok.kind != "ident" or self.tok.text != label:
                self.error(f"loop '{label}' must close with 'end do {label}'")
            self.next()
        elif self.tok.kind == "ident":
            self.error("this loop has no label, so it closes with plain 'end do'")
        self.newline()

    def at_end_select(self):
        return self.at("end") and self.toks[self.i + 1].text == "select"

    def case_values(self):
        """What follows `case`: `default` (None) or a comma-separated list of constants and ranges."""
        if self.accept("default"):
            return None
        values = [self.case_item()]
        while self.accept(","):
            values.append(self.case_item())
        return values

    def case_item(self):
        if self.accept(":"):
            return Slice(None, self.case_const())
        lo = self.case_const()
        if not self.accept(":"):
            return lo
        hi = None if self.at(",", "->") or self.tok.kind == "nl" else self.case_const()
        return Slice(lo, hi)

    def case_const(self):
        """A literal, a signed number, or a name (checked to be a `const` when the select runs)."""
        line = self.tok.line
        e = self.add()
        if not isinstance(e, Var) and literal_value(e) is NOT_LITERAL:
            raise LangError(f"line {line}: a case value must be a literal or the name of a const")
        return e

    def select_stmt(self):
        line = self.expect("select").line
        self.expect("case")
        flow = bool(self.accept("fallthrough"))
        subject = self.expr()
        self.newline()
        cases = []
        self.inside.append("select")
        while True:
            if self.at_end_select():
                self.next(); self.next()
                self.newline()
                break
            if self.tok.kind == "eof":
                self.error("unexpected end of file, missing 'end select'")
            if not self.at("case"):
                self.error("expected 'case' or 'end select' (statements belong inside a case)")
            at = self.next().line
            values = self.case_values()
            self.newline()
            body = []
            while not (self.at("case") or self.at_end_select()):
                if self.tok.kind == "eof":
                    self.error("unexpected end of file, missing 'end select'")
                body.append(self.statement())
                self.skip_newlines()
            cases.append(Case(values, body, at))
        self.inside.pop()
        if not cases:
            raise LangError(f"line {line}: a select needs at least one case")
        check_cases([(c.values, c.line) for c in cases])
        return Select(subject, cases, line, flow)

    def select_expr(self):
        line = self.expect("select").line
        self.expect("case")
        if self.at("fallthrough"):
            self.error("a select expression cannot fall through")
        subject = self.expr()
        arms = []
        while True:
            if self.tok.kind != "nl":
                self.error("expected end of line")
            self.skip_newlines()
            if self.at("end"):
                self.next()
                self.expect("select")
                break
            at = self.tok.line
            self.expect("case")
            values = self.case_values()
            if self.at("then"):
                self.error("a select expression arm is written 'case <values> -> <expression>'; use '->' instead of 'then'")
            self.expect("->")
            arms.append(Arm(values, self.expr(), at))
        if not any(a.values is None for a in arms):
            raise LangError(f"line {line}: a select expression must end with a 'case default -> ...' arm")
        check_cases([(a.values, a.line) for a in arms])
        kinds = arm_types(arms)
        if len(kinds) > 1 and kinds != {"integer", "real"}:
            raise LangError(f"line {line}: the arms of a select expression have different types "
                            f"({' and '.join(sorted(kinds))})")
        return SelectExpr(subject, arms, line)

    # -- expressions (lowest to highest precedence)
    def expr(self):
        return self.or_()

    def or_(self):
        e = self.and_()
        while self.accept("or"):
            e = Binary("or", e, self.and_())
        return e

    def and_(self):
        e = self.not_()
        while self.accept("and"):
            e = Binary("and", e, self.not_())
        return e

    def not_(self):
        if self.accept("not"):
            return Unary("not", self.not_())
        return self.compare()

    def compare(self):
        e = self.add()
        while self.at("==", "/=", "<", "<=", ">", ">="):
            op = self.next().text
            e = Binary(op, e, self.add())
        return e

    def add(self):
        e = self.mul()
        while self.at("+", "-"):
            op = self.next().text
            e = Binary(op, e, self.mul())
        return e

    def mul(self):
        e = self.unary()
        while self.at("*", "/"):
            op = self.next().text
            e = Binary(op, e, self.unary())
        return e

    def unary(self):
        if self.at("-", "+"):
            op = self.next().text
            return Unary(op, self.unary())
        return self.power()

    def power(self):
        base = self.primary()
        if self.accept("**"):
            return Binary("**", base, self.unary())  # right-associative
        return base

    def primary(self):
        t = self.tok
        if t.kind == "int":
            self.next(); return Num(int(t.text))
        if t.kind == "real":
            self.next(); return Num(float(t.text))
        if t.kind == "str":
            self.next(); return Str(t.text[1:-1])
        if self.accept("true"):
            return Bool(True)
        if self.accept("false"):
            return Bool(False)
        if self.accept("("):
            e = self.expr()
            self.expect(")")
            return e
        if self.accept("["):
            items = []
            if not self.at("]"):
                items.append(self.expr())
                while self.accept(","):
                    items.append(self.expr())
            self.expect("]")
            return ArrayLit(items)
        if self.at("select"):
            return self.select_expr()
        if self.at("break"):
            self.error("'break' cannot be used in an expression")
        if t.kind == "ident":
            name = self.next().text
            e = Var(name)
            if self.accept("("):
                e = Apply(name, self.call_args(), t.line)
            while self.at("."):                    # record fields: p.x, pts(2).pos.y, p.v(3)
                self.next()
                field = self.ident()
                e = FieldAccess(e, field, self.call_args() if self.accept("(") else None, t.line)
            return e
        self.error("expected an expression")

    def call_args(self):
        """The arguments after an opening parenthesis, through the closing one."""
        args = []
        if not self.at(")"):
            while True:
                args.append(self.arg())
                if not self.accept(","):
                    break
        self.expect(")")
        named = [isinstance(a, NamedArg) for a in args]
        if named != sorted(named):
            self.error("positional arguments must come before named arguments")
        return args

    def arg(self):
        if self.tok.kind == "ident" and self.toks[self.i + 1].text == "=" and self.toks[self.i + 1].kind == "op":
            name = self.next().text
            self.next()
            return NamedArg(name, self.expr())
        lo = None if self.at(":") else self.expr()
        if self.accept(":"):
            hi = None if self.at(",", ")") else self.expr()
            return Slice(lo, hi)
        return lo


def parse(src):
    return Parser(src).parse()
