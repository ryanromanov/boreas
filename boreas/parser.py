from dataclasses import dataclass, field, fields, is_dataclass
from .lexer import lex, LangError
from .kinds import lit_int, SPELLINGS, KIND_TYPES

TYPES = {"integer", "real", "logical", "string", "file", "complex", "int8", "int16", "int32", "int64", "real32", "real64"}


# --- AST ---------------------------------------------------------------
@dataclass
class Num:  value: object
@dataclass
class Str:  value: str
@dataclass
class Bool: value: bool
@dataclass
class Var:
    name: str
    line: int = field(default=0, compare=False, repr=False)   # where the name is, for `boreas check`
    col: int = field(default=0, compare=False, repr=False)
@dataclass
class Null: pass          # the absent value of an optional (`T?`) variable
@dataclass
class ArrayLit: items: list
@dataclass
class Gen:
    """A generator inside an array literal: `(value do i = lo, hi, step)`. `body` holds one or more values
    (scalars or one-dimensional arrays) that are spliced in for each i."""
    body: list; var: str; lo: object; hi: object; step: object; line: int = 0
    var_col: int = field(default=0, compare=False, repr=False)
@dataclass
class Slice: lo: object; hi: object
@dataclass
class Apply:   # index, slice, or call
    name: str; args: list; line: int
    col: int = field(default=0, compare=False, repr=False)
    bracket: bool = field(default=False, compare=False, repr=False)   # written name[...]: indexing, never a call
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
class FieldAccess:    # base.field or base.field[subscripts] for an array field
    base: object; field: str; args: object; line: int
    bracket: bool = field(default=False, compare=False, repr=False)
    call: bool = field(default=False, compare=False, repr=False)      # x.area(): a record's function, not an array field
@dataclass
class NamedArg: name: str; value: object     # `x = 1.0` inside a record constructor
@dataclass
class RecordDef:
    name: str
    fields: list          # [Decl], no initializers
    public: bool
    line: int
    functions: dict = field(default_factory=dict)   # name -> Function; each takes the record first, as `self`
    implements: list = field(default_factory=list)  # `record circle is shape`: [(interface name, line)]
@dataclass
class InterfaceDef:
    """`interface shape`: the signatures a value must have to be used as a shape. Each is a Function with
    no body whose first parameter is `self`."""
    name: str
    functions: dict       # name -> Function
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
    var_col: int = field(default=0, compare=False, repr=False)
@dataclass
class DoWhile: cond: object; body: list; label: str = None; line: int = 0
@dataclass
class Case:
    values: object      # a list of constants and Slice ranges, or None for `case default`
    body: list
    line: int
    col: int = field(default=0, compare=False, repr=False)   # a select type: where the record's name starts
@dataclass
class Select:
    subject: object
    cases: list
    line: int
    flow: bool = False  # `select case fallthrough`: a finished body continues into the next case
@dataclass
class SelectType(Select):
    """`select type x as p`: each case names a record type (its `values` is that name, None for `case default`),
    and inside it `p` is a copy of the value, as that record."""
    var: str = None
@dataclass
class Break: pass       # leaves the nearest enclosing select statement
@dataclass
class Print:   # print/println go to the screen, write/writeln to the file `to`; the `ln` forms end the line
    args: list; newline: bool = True; to: object = None; line: int = 0
@dataclass
class Close: file: object; line: int
@dataclass
class Stop: code: object; line: int
@dataclass
class Return: value: object
@dataclass
class Exit: label: str = None; line: int = 0      # label names an enclosing loop
@dataclass
class Cycle: label: str = None; line: int = 0
@dataclass
class ExprStmt: expr: object
@dataclass
class Call:
    name: str; args: list; line: int
    col: int = field(default=0, compare=False, repr=False)
    receiver: object = None    # `call x.grow(2.0)`: the record `x`, which is passed first, as `self`
@dataclass
class Allocate: name: str; dims: list; line: int; update: bool = False   # allocate a[n, m]: one extent per dimension
@dataclass
class Deallocate: name: str; line: int; update: bool = False
@dataclass
class Comment: text: str          # only produced by `boreas convert`; the interpreter skips it

@dataclass(frozen=True)
class ProcType:
    """The type of a procedure parameter: `function(real :: x) -> real` or `subroutine(inout integer)`.
    Passing a procedure needs an exact match on kind, parameter types and intents, and return type;
    the names are documentation only, so they do not take part in comparing."""
    is_sub: bool
    params: tuple                                   # ((type, intent), ...)
    ret: str | None
    names: tuple = field(default=(), compare=False)    # one name or None per parameter

    def __str__(self):
        names = self.names or (None,) * len(self.params)
        parts = [("" if intent == "in" else intent + " ") + typ + (f" :: {n}" if n else "")
                 for (typ, intent), n in zip(self.params, names)]
        head = ("subroutine" if self.is_sub else "function") + "(" + ", ".join(parts) + ")"
        return head + (f" -> {self.ret}" if self.ret else "")


def signature_of(f):
    """The ProcType a procedure `f` has, for matching it against a procedure parameter."""
    return ProcType(f.is_sub, tuple((t, i) for t, _, i in f.params), f.ret, tuple(n for _, n, _ in f.params))


def is_optional(typ):
    return isinstance(typ, str) and typ.endswith("?")


@dataclass
class Function:
    """A function or, when is_sub is set, a subroutine."""
    name: str
    params: list          # [(type, name, intent)], intent in {"in", "out", "inout"}; type is a ProcType for a callback
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
    decls: list           # [Decl]: the module's constants and variables, in order
    functions: dict       # name -> Function
    line: int
    comments: list = field(default_factory=list)   # printed above the header by `convert`
    records: dict = field(default_factory=dict)    # name -> RecordDef
    interfaces: dict = field(default_factory=dict) # name -> InterfaceDef
@dataclass
class Unit:
    functions: dict       # top-level procedures (outside any module)
    program: Program      # None for a library-only file
    modules: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)    # top-level record types
    ieee: bool = False    # `use ieee`: real arithmetic follows IEEE (NaN, Infinity) instead of stopping
    interfaces: dict = field(default_factory=dict) # top-level interfaces


def root_name(target):
    """The variable an assignment target writes into: `p` for `p`, `p(2)`, `p.x` and `p.v(2).y`."""
    while isinstance(target, FieldAccess):
        target = target.base
    return target.name


KEYWORDS = {"if", "then", "else", "do", "while", "end", "program", "function", "subroutine", "module",
            "record", "interface", "const", "public", "private", "use", "call", "return", "exit", "cycle", "print", "println", "write", "writeln", "close", "stop",
            "select", "case", "default", "fallthrough", "break", "update", "allocate", "deallocate",
            "allocatable", "in", "out", "inout", "true", "false", "null", "and", "or", "not"}


def check_constant(e, what, line):
    """A field default is built from literals, `const` names, arithmetic, array literals,
    record constructors and built-in functions (which names are allowed is checked at link time)."""
    if isinstance(e, (Num, Str, Bool, Var, Null)):
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


def check_writes(body, params, shared=None, is_sub=True):
    shared = shared or {}
    """`update` marks exactly the writes that reach beyond the procedure: assignments to out/inout
    parameters and to module variables (`shared`, the ones declared in or imported into this
    procedure's module). `params` maps parameter name -> intent. A function stays free of side
    effects, so only a subroutine may change a module variable."""
    def reaches(name, update):
        if params.get(name) in ("out", "inout"):
            return params[name] + " parameter"
        if name in shared and name not in params:
            return "module variable" if shared[name] or update else None     # an import may be a constant
        return None

    def write(s, name, shown, what, imported=False):
        if what == "module variable" and not is_sub:
            raise LangError(f"line {s.line}: a function cannot change the module variable '{name}'; "
                            f"make it a subroutine")
        if what and not s.update:
            raise LangError(f"line {s.line}: '{name}' is {article(what)}, so other code sees this write; "
                            f"write 'update {shown} = ...'")
        if s.update and not what and not imported:
            kind = "an 'in' parameter" if params.get(name) == "in" else "a local variable"
            raise LangError(f"line {s.line}: 'update' is for out and inout parameters and module variables, "
                            f"but '{name}' is {kind}")

    for s in body:
        if isinstance(s, list):
            check_writes(s, params, shared, is_sub)
        elif isinstance(s, Assign):
            name = root_name(s.target)
            write(s, name, target_text(s.target) if isinstance(s.target, FieldAccess) else name,
                  reaches(name, s.update), name in shared and not shared[name] and name not in params)
        elif isinstance(s, If):
            for _, b in s.branches:
                check_writes(b, params, shared, is_sub)
            check_writes(s.orelse, params, shared, is_sub)
        elif isinstance(s, Select):
            for c in s.cases:
                check_writes(c.body, params, shared, is_sub)
        elif isinstance(s, Do):
            if s.var in params:
                raise LangError(f"line {s.line}: loop variable '{s.var}' cannot be a parameter")
            check_writes(s.body, params, shared, is_sub)
        elif isinstance(s, DoWhile):
            check_writes(s.body, params, shared, is_sub)


def article(what):
    return ("an " if what[0] in "aeiou" else "a ") + what


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


def check_allocs(body, params, shared=None, is_sub=True):
    """`allocate` and `deallocate` need an allocatable array of matching rank: a local one, or a module
    variable (`shared`: name -> its Decl, None for an import), which other code sees and so needs
    `update allocate`, in a subroutine. `params` is the set of parameter names (never allocated)."""
    shared = shared or {}
    decls = {d.name: d for d in _nodes(body) if isinstance(d, Decl)}
    for s in _nodes(body):
        if not isinstance(s, (Allocate, Deallocate)):
            continue
        word = "allocate" if isinstance(s, Allocate) else "deallocate"
        d = decls.get(s.name)
        if s.name in params:
            why = "a parameter; only a local allocatable array can be allocated or deallocated"
        elif d is None and s.name in shared:
            if not is_sub:
                raise LangError(f"line {s.line}: a function cannot {word} the module variable '{s.name}'; "
                                f"make it a subroutine")
            if not s.update:
                raise LangError(f"line {s.line}: '{s.name}' is a module variable, so other code sees this; "
                                f"write 'update {word} {s.name}{'(...)' if word == 'allocate' else ''}'")
            m = shared[s.name]
            if m is not None and not m.allocatable:
                why = "not allocatable (declare it with 'allocatable')"
            elif m is not None and isinstance(s, Allocate) and len(s.dims) != len(m.dims):
                raise LangError(f"line {s.line}: '{s.name}' has {len(m.dims)} dimension(s) but "
                                f"{len(s.dims)} extent(s) were given to allocate")
            else:
                continue
        elif d is None:
            why = "not declared as a local allocatable array"
        elif not d.allocatable:
            why = ("a constant" if d.const else "not allocatable (declare it with 'allocatable')")
        elif isinstance(s, Allocate) and len(s.dims) != len(d.dims):
            raise LangError(f"line {s.line}: '{s.name}' has {len(d.dims)} dimension(s) but "
                            f"{len(s.dims)} extent(s) were given to allocate")
        else:
            if s.update:
                raise LangError(f"line {s.line}: 'update' is for module variables, but '{s.name}' is a local array")
            continue
        raise LangError(f"line {s.line}: cannot {word} '{s.name}': it is {why}")


# --- Parser ------------------------------------------------------------
class Parser:
    def __init__(self, src):
        self.module_checks = []                     # one list per module being parsed: its procedures, checked at its end
        self.toks, self.i = lex(src), 0
        self.inside = []                  # enclosing "select" / "loop" constructs, innermost last

    @property
    def tok(self):
        return self.toks[self.i]

    def error(self, msg):
        err = LangError(f"line {self.tok.line}: {msg} (at {self.tok.text!r})")
        err.col = self.tok.col or None
        raise err

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
        funcs, mods, prog, recs, ieee, ifaces = {}, {}, None, {}, False, {}
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
            elif self.at("interface"):
                d = self.interface_def(False)
                if d.name in ifaces:
                    raise LangError(f"line {d.line}: interface '{d.name}' is defined more than once")
                ifaces[d.name] = d
            elif self.at("module"):
                m = self.module()
                if m.name in mods:
                    raise LangError(f"line {m.line}: module '{m.name}' is defined more than once")
                mods[m.name] = m
            elif self.at("program"):
                if prog:
                    self.error("only one program allowed")
                prog = self.program()
            elif self.at("use") and self.toks[self.i + 1].text == "ieee" and self.toks[self.i + 2].kind in ("nl", "eof"):
                self.next(); self.next()
                ieee = True                                  # `use ieee`: real arithmetic gives NaN and Infinity
            else:
                self.error("expected 'use ieee', 'module', 'record', 'interface', 'function', 'subroutine' or 'program'")
            self.skip_newlines()
        if not prog:
            raise LangError("no program found")
        return Unit(funcs, prog, mods, recs, ieee, ifaces)

    def record_def(self, public):
        """`record point` / one `type :: name` line per field / `end record`."""
        line = self.expect("record").line
        name = self.ident()
        if name in KEYWORDS or name in TYPES:
            self.error(f"'{name}' cannot be the name of a record")
        implements = []
        if self.tok.kind == "ident" and self.tok.text == "is":           # record circle is shape, drawable
            self.next()
            while True:
                at = self.tok.line
                implements.append((self.ident(), at))
                if not self.accept(","):
                    break
        self.newline()
        fields, functions = [], {}
        while not (self.at("end") and self.toks[self.i + 1].text == "record"):
            if self.tok.kind == "eof":
                self.error("unexpected end of file, missing 'end record'")
            if self.at("function", "subroutine"):
                fn_line = self.tok.line
                fn = self.procedure(owner=name)
                if fn.name in functions:
                    raise LangError(f"line {fn_line}: record '{name}' has two functions called '{fn.name}'")
                if any(d.name == fn.name for d in fields):
                    raise LangError(f"line {fn_line}: record '{name}' has a field and a function called '{fn.name}'")
                functions[fn.name] = fn
                self.skip_newlines()
                continue
            if self.at("const", "allocatable", "public", "private"):
                self.error("record fields are plain declarations: 'type :: name'")
            f = self.decl()
            if f.name in functions:
                raise LangError(f"line {f.line}: record '{name}' has a field and a function called '{f.name}'")
            if f.init is not None:
                check_constant(f.init, f"the default of field '{f.name}'", f.line)
            fields.append(f)
            self.skip_newlines()
        self.expect("end")
        self.expect("record")
        if self.tok.kind == "ident":
            self.next()
        self.newline()
        return RecordDef(name, fields, public, line, functions, implements)

    def interface_def(self, public):
        """`interface shape` / one function or subroutine signature per line / `end interface`."""
        line = self.expect("interface").line
        name = self.ident()
        if name in KEYWORDS or name in TYPES:
            self.error(f"'{name}' cannot be the name of an interface")
        self.newline()
        functions = {}
        self.skip_newlines()
        while not (self.at("end") and self.toks[self.i + 1].text == "interface"):
            if self.tok.kind == "eof":
                self.error("unexpected end of file, missing 'end interface'")
            if not self.at("function", "subroutine"):
                self.error("an interface lists signatures: 'function name(self, ...) -> type' or 'subroutine name(self, ...)'")
            at = self.tok.line
            is_sub = self.next().text == "subroutine"
            fname = self.ident()
            params = self.params(is_sub, name)
            ret = None
            if not is_sub:
                if not self.accept("->"):
                    self.error("a function signature needs its result type: function area(self) -> real")
                ret = self.result_type()
            if fname in functions:
                raise LangError(f"line {at}: interface '{name}' lists '{fname}' twice")
            functions[fname] = Function(fname, params, ret, [], is_sub)
            self.newline()
            self.skip_newlines()
        self.expect("end")
        self.expect("interface")
        if self.tok.kind == "ident":
            self.next()
        self.newline()
        return InterfaceDef(name, functions, public, line)

    def program(self):
        self.expect("program")
        name = self.ident()
        self.newline()
        uses = self.uses()
        body = self.block("program")
        self.expect("program")
        if self.tok.kind == "ident":
            self.next()
        check_writes(body, {}, {n: False for n in (n for u in uses for n in u.names)})
        check_loops(body)
        check_allocs(body, set(), {n: None for u in uses for n in u.names})
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
        decls, funcs, recs, ifaces = [], {}, {}, {}
        taken = {}                                     # name -> line, across constants, variables and procedures
        checks = []                                    # (procedure, its parameter modes): checked once every variable is known
        shared = {n: False for u in uses for n in u.names}   # names other procedures can see: imported (False) or declared here (True)
        self.module_checks.append(checks)

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
                    decls.append(x)
            elif self.at("record"):
                r = self.record_def(public)
                claim(r.name, r.line)
                recs[r.name] = r
            elif self.at("interface"):
                d = self.interface_def(public)
                claim(d.name, d.line)
                ifaces[d.name] = d
            elif self.at("function", "subroutine"):
                at = self.tok.line
                f = self.procedure()
                f.public = public
                claim(f.name, at)
                funcs[f.name] = f
            elif self.at("allocatable"):
                self.next()
                x = self.decl(allocatable=True)        # a shared array: allocated only with `update allocate`
                x.public = public
                claim(x.name, x.line)
                decls.append(x)
                shared[x.name] = True
            elif self.tok.kind == "ident" and (self.tok.text in TYPES or self.typed_ahead()):
                x = self.decl()                        # a module variable: shared, changed only with `update`
                x.public = public
                claim(x.name, x.line)
                decls.append(x)
                if not x.const:
                    shared[x.name] = True
            else:
                self.error("expected a declaration, 'record', 'interface', 'function', 'subroutine' or 'end module'")
            self.skip_newlines()
        self.expect("end")
        self.expect("module")
        self.module_checks.pop()
        if self.tok.kind == "ident":
            self.next()
        variables = {d.name: d for d in decls if not d.const}
        for f, params in checks:
            check_writes(f.body, params, {**{n: False for u in f.uses for n in u.names}, **shared}, f.is_sub)
            check_allocs(f.body, set(params), {**{n: None for u in f.uses for n in u.names},
                                               **{n: None for u in uses for n in u.names}, **variables}, f.is_sub)
        return Module(name, uses, decls, funcs, line, records=recs, interfaces=ifaces)

    def procedure(self, owner=None):
        """A function or subroutine. Inside a record (`owner` is the record's name) the first parameter is
        `self`, which has the record's type."""
        kind = self.next().text           # "function" or "subroutine"
        is_sub = kind == "subroutine"
        name = self.ident()
        params = self.params(is_sub, owner)
        ret = None
        if not is_sub and self.accept("->"):
            ret = self.result_type()
        self.newline()
        uses = self.uses()
        body = self.block(kind)
        self.expect(kind)
        if self.tok.kind == "ident":
            self.next()
        f = Function(name, params, ret, body, is_sub, uses=uses)
        modes = {n: intent for _, n, intent in params}
        if self.module_checks:
            self.module_checks[-1].append((f, modes))
        else:
            check_writes(body, modes, {n: False for u in uses for n in u.names}, is_sub)
        check_loops(body)
        if not self.module_checks:                  # a module's procedures are checked when the module ends
            check_allocs(body, {n for _, n, _ in params}, {n: None for u in uses for n in u.names}, is_sub)
        return f

    def params(self, is_sub, owner=None):
        """`(real :: x, inout integer :: n)`; the mode defaults to `in`. In a record's function the list
        starts with `self` (`(self, real :: x)`, `(inout self)`), which has the record's type."""
        self.expect("(")
        params = []
        if owner is not None:
            intent = "in"
            if self.at("in", "out", "inout") and self.toks[self.i + 1].text == "self":
                intent = self.next().text
                if intent != "in" and not is_sub:
                    self.error("only subroutines may have out or inout parameters")
                if intent == "out":
                    self.error("'self' cannot be an out parameter")
            if not (self.tok.kind == "ident" and self.tok.text == "self"):
                self.error(f"a function in record '{owner}' takes 'self' first: (self, ...)")
            self.next()
            params.append((owner, "self", intent))
            if not self.at(")"):
                self.expect(",")
        if not self.at(")"):
            while True:
                intent = "in"
                if self.at("in", "out", "inout"):
                    intent = self.next().text
                    if intent != "in" and not is_sub:
                        self.error("only subroutines may have out or inout parameters")
                if self.at("function", "subroutine"):
                    if intent != "in":
                        self.error("a procedure parameter cannot be out or inout")
                    t = self.proc_type()
                else:
                    t = self.param_type()
                if is_optional(t) and intent != "in":
                    self.error("an optional ('?') parameter must be an 'in' parameter")
                self.expect("::")
                params.append((t, self.ident(), intent))
                if not self.accept(","):
                    break
        self.expect(")")
        return params

    def proc_type(self):
        """`function(real, real :: y) -> logical` or `subroutine(inout integer)`: the signature of a callback."""
        is_sub = self.next().text == "subroutine"
        self.expect("(")
        params, names = [], []
        if not self.at(")"):
            while True:
                intent = "in"
                if self.at("in", "out", "inout"):
                    intent = self.next().text
                    if intent != "in" and not is_sub:
                        self.error("only a subroutine signature may have out or inout parameters")
                if self.at("function", "subroutine"):
                    self.error("a procedure signature cannot take a procedure")
                t = self.param_type(anywhere=True)
                if is_optional(t):
                    self.error("a procedure signature cannot have an optional ('?') parameter")
                params.append((t, intent))
                names.append(self.ident() if self.accept("::") else None)
                if not self.accept(","):
                    break
        self.expect(")")
        ret = None
        if not is_sub:
            if not self.accept("->"):
                self.error("a function signature needs its result type: function(real) -> real")
            ret = self.type_name(anywhere=True)
            if self.at("["):
                self.error("a procedure signature cannot return an array")
        return ProcType(is_sub, tuple(params), ret, tuple(names))

    def param_type(self, anywhere=False):
        """A parameter's type: a type, or an array of one with its rank (`real[]`, `real[,]`)."""
        name = self.type_name(anywhere=anywhere)
        if not self.at("["):
            return name
        if is_optional(name):
            self.error("an array parameter cannot be optional ('?')")
        return name + self.rank_suffix()

    def rank_suffix(self):
        """`[]`, `[,]`, ...: the brackets after a type, a comma for each dimension beyond the first."""
        self.expect("[")
        rank = 1
        while self.accept(","):
            rank += 1
        self.expect("]")
        return "[" + "," * (rank - 1) + "]"

    def result_type(self):
        """A function's result type: a type, or an array of one (`integer[]`, `real[,]`: a comma for each
        extra dimension). An array result's length is only known after the call."""
        name = self.type_name(anywhere=True)
        if not self.at("["):
            return name
        if is_optional(name):
            self.error("an array result cannot be optional ('?')")
        return name + self.rank_suffix()

    def typed_ahead(self, j=None):
        """Does a `::` follow the type name at token j (the current token if j is None), after an optional `?`
        and an optional `[...]`?"""
        j = self.i + 1 if j is None else j + 1
        if self.toks[j].text == "?":
            j += 1
        if self.toks[j].text == "[":
            depth = 0
            while self.toks[j].kind not in ("nl", "eof"):
                depth += (self.toks[j].text == "[") - (self.toks[j].text == "]")
                j += 1
                if depth == 0:
                    break
        return self.toks[j].text == "::"

    def type_name(self, anywhere=False):
        """A built-in type, or (when a `::` follows, or `anywhere`) the name of a record; a `?`
        right after it makes the value optional (`integer?`)."""
        t = self.tok
        if t.kind == "ident" and t.text in TYPES:
            name = SPELLINGS.get(t.text, t.text)
            self.next()
        elif t.kind == "ident" and t.text not in KEYWORDS and (anywhere or self.typed_ahead()):
            name = self.next().text
        else:
            self.error("expected a type (integer, real, logical, string, or a record)")
        return name + "?" if self.accept("?") else name

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
                                                              or self.typed_ahead(self.i + 1)):
            self.next()
            return self.decl(allocatable=True)
        if t.kind == "ident" and (t.text in TYPES or (t.text not in KEYWORDS and self.typed_ahead())):
            return self.decl()
        if self.at("record"):
            self.error("records are defined at the top level or inside a module, not in a procedure")
        if t.kind == "ident" and t.text in ("allocate", "deallocate") and self.toks[self.i + 1].kind == "ident":
            return self.allocation()
        if self.at("update") and self.toks[self.i + 1].text in ("allocate", "deallocate"):
            return self.allocation()
        if self.at("if"):
            return self.if_stmt()
        if self.at("do"):
            return self.do_stmt()
        if self.at("select"):
            if self.toks[self.i + 1].kind == "ident" and self.toks[self.i + 1].text == "type":
                return self.select_type_stmt()
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
        if self.at("print", "println", "write", "writeln"):
            word = self.next().text
            self.expect("(")
            to = None
            if word.startswith("write"):
                to = self.expr()
                if not self.at(")"):
                    self.expect(",")
            args = []
            if not self.at(")"):
                args.append(self.expr())
                while self.accept(","):
                    args.append(self.expr())
            self.expect(")")
            self.newline()
            newline = word.endswith("ln")
            if not args and not newline:
                self.error(f"'{word}' needs at least one value")
            return Print(args, newline, to, t.line)
        if self.at("close"):
            self.next()
            f = self.expr()
            self.newline()
            return Close(f, t.line)
        if self.at("stop"):
            self.next()
            code = None if self.tok.kind == "nl" else self.expr()
            self.newline()
            return Stop(code, t.line)
        if self.at("call") and self.toks[self.i + 2].text in (".", "["):      # call x.grow(2.0): a record's function
            self.next()
            target = self.primary()
            if not (isinstance(target, FieldAccess) and not target.bracket and target.args is not None):
                self.error("expected a call: call record.function(...)")
            self.newline()
            return Call(target.field, target.args, t.line, t.col, target.base)
        if self.at("call"):
            self.next()
            col = self.tok.col
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
            return Call(name, args, t.line, col)
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
        """`allocate a[n, m]` or `deallocate a`: one array per statement."""
        update = bool(self.accept("update"))
        t = self.next()
        name = self.ident()
        if t.text == "deallocate":
            self.newline()
            return Deallocate(name, t.line, update)
        if self.at("("):
            self.error(f"arrays use brackets: write 'allocate {name}[...]'")
        if not self.at("["):
            self.error("expected the extents, as in 'allocate a[n]'")
        self.next()
        dims = [self.expr()]
        while self.accept(","):
            dims.append(self.expr())
        self.expect("]")
        self.newline()
        return Allocate(name, dims, t.line, update)

    def decl(self, const=False, allocatable=False):
        line = self.tok.line
        typ = self.type_name()
        dims = None
        if self.at("["):                               # real[3] :: v, real[2, 3] :: m, allocatable real[,] :: m
            dims = self.type_dims(allocatable)
        self.expect("::")
        name = self.ident()
        if self.at("("):
            if allocatable:
                self.error(f"arrays use brackets: write 'allocatable {typ}[] :: {name}', "
                           f"with a comma for each extra dimension")
            self.error(f"arrays use brackets: write '{typ}[size] :: {name}'")
        if allocatable and dims is None:
            self.error(f"allocatable '{name}' needs its rank: write 'allocatable {typ}[] :: {name}', "
                       f"with a comma for each extra dimension")
        if typ.endswith("?") and (dims is not None or allocatable):
            self.error(f"'{name}' is an array of optional values, which is not supported")
        init = self.expr() if self.accept("=") else None
        if allocatable and init is not None:
            self.error(f"allocatable array '{name}' cannot have an initializer")
        if const and init is None:
            self.error(f"constant '{name}' needs a value")
        if self.at(","):
            self.error("declare one variable per line")
        self.newline()
        return Decl(typ, name, dims, init, line, const, allocatable=allocatable)

    def type_dims(self, allocatable):
        """The `[...]` after a type in a declaration: sizes for a fixed array (`real[2, 3]`), or only commas
        for an allocatable one (`real[,]`), where the rank is the number of commas plus one."""
        self.expect("[")
        if allocatable:
            dims = [Slice(None, None)]
            while self.accept(","):
                dims.append(Slice(None, None))
            if not self.at("]"):
                self.error("an allocatable array is declared with no sizes, as in 'real[,]'; "
                           "its sizes come from 'allocate'")
            self.next()
            return dims
        if self.at("]", ","):
            self.error("a fixed-size array needs a size, as in 'real[3]'; use 'allocatable' for one sized later")
        dims = [self.expr()]
        while self.accept(","):
            dims.append(self.expr())
        self.expect("]")
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
        var_col = self.tok.col
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
        return Do(var, lo, hi, step, body, line, label, var_col)

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

    def select_type_stmt(self):
        """`select type x as p` / `case rect` ... / `case default` / `end select`; the default is required."""
        line = self.expect("select").line
        self.next()                                              # the word `type`
        subject = self.expr()
        if not (self.tok.kind == "ident" and self.tok.text == "as"):
            self.error("select type names the variable for each case: select type x as p")
        self.next()
        var = self.ident()
        self.newline()
        cases, seen = [], set()
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
            col = self.tok.col
            if self.accept("default"):
                name = None
            else:
                name = self.ident()
            if name in seen:
                raise LangError(f"line {at}: {'case default' if name is None else 'case ' + name} appears twice in this select type")
            seen.add(name)
            self.newline()
            body = []
            while not (self.at("case") or self.at_end_select()):
                if self.tok.kind == "eof":
                    self.error("unexpected end of file, missing 'end select'")
                body.append(self.statement())
                self.skip_newlines()
            cases.append(Case(name, body, at, col))
        self.inside.pop()
        if None not in seen:
            raise LangError(f"line {line}: a select type needs a 'case default': other records can fit the interface "
                            f"later, so say what to do with them (an empty default is fine)")
        return SelectType(subject, cases, line, False, var)

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
        return self.coalesce()

    def coalesce(self):
        """`a ?? b`: a, or b when a is absent. The loosest operator, so `n ?? 0 + 1` is `n ?? (0 + 1)`."""
        e = self.or_()
        while self.accept("??"):
            e = Binary("??", e, self.or_())
        return e

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
        while self.at("+", "-", "+%", "-%"):
            op = self.next().text
            e = Binary(op, e, self.mul())
        return e

    def mul(self):
        e = self.unary()
        while self.at("*", "/", "*%"):
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

    def literal_item(self):
        """One item of an array literal: an expression, or a parenthesized generator."""
        if self.at("(") and self.generator_ahead():
            self.next()
            body = [self.literal_item()]
            while self.accept(","):
                body.append(self.literal_item())
            g = self.generator_tail(body)
            self.expect(")")
            return g
        return self.expr()

    def generator_ahead(self):
        """Is the "(" here the start of `(value do i = ...)`? Looks for a `do` before the matching ")"."""
        depth, j = 0, self.i
        while self.toks[j].kind not in ("nl", "eof"):
            t = self.toks[j]
            if t.text in ("(", "["):
                depth += 1
            elif t.text in (")", "]"):
                depth -= 1
                if depth == 0:
                    return False
            elif t.text == "do" and depth == 1:
                return True
            j += 1
        return False

    def generator_tail(self, body):
        """`do i = lo, hi[, step]` after the values of a generator."""
        line = self.expect("do").line
        var_col = self.tok.col
        var = self.ident()
        self.expect("=")
        lo = self.expr()
        self.expect(",")
        hi = self.expr()
        step = self.expr() if self.accept(",") else None
        return Gen(body, var, lo, hi, step, line, var_col)

    def primary(self):
        t = self.tok
        if t.kind == "int":
            self.next(); return Num(lit_int(int(t.text)))
        if t.kind == "real":
            self.next(); return Num(float(t.text))
        if t.kind == "str":
            self.next(); return Str(t.text[1:-1])
        if self.accept("true"):
            return Bool(True)
        if self.accept("false"):
            return Bool(False)
        if self.accept("null"):
            return Null()
        if self.accept("("):
            e = self.expr()
            if self.at("do"):
                self.error("a generator belongs in an array literal: [(value do i = 1, n)]")
            self.expect(")")
            return e
        if self.accept("["):
            items = []
            if not self.at("]"):
                items.append(self.literal_item())
                while self.accept(","):
                    items.append(self.literal_item())
                if self.at("do"):                      # shorthand: [value do i = 1, n]
                    if len(items) != 1:
                        self.error("with several items, write the generator in parentheses: [x, (0.0 do i = 1, n)]")
                    items = [self.generator_tail(items)]
            self.expect("]")
            return ArrayLit(items)
        if self.at("select"):
            return self.select_expr()
        if self.at("break"):
            self.error("'break' cannot be used in an expression")
        if t.kind == "ident":
            name = self.next().text
            e = Var(name, t.line, t.col)
            if self.accept("("):
                e = Apply(name, self.call_args(), t.line, t.col)
            elif self.accept("["):
                e = Apply(name, self.call_args("]"), t.line, t.col, True)
            while self.at("."):                    # record fields: p.x, pts[2].pos.y, p.v[3]
                self.next()
                field = self.ident()
                if self.accept("["):
                    e = FieldAccess(e, field, self.call_args("]"), t.line, True)
                else:
                    e = FieldAccess(e, field, self.call_args() if self.accept("(") else None, t.line)
            return e
        self.error("expected an expression")

    def call_args(self, close=")"):
        """The arguments after an opening parenthesis (or bracket), through the closing one."""
        args = []
        if not self.at(close):
            while True:
                args.append(self.arg(close))
                if not self.accept(","):
                    break
        self.expect(close)
        named = [isinstance(a, NamedArg) for a in args]
        if named != sorted(named):
            self.error("positional arguments must come before named arguments")
        return args

    def arg(self, close=")"):
        if self.tok.kind == "ident" and self.toks[self.i + 1].text == "=" and self.toks[self.i + 1].kind == "op":
            name = self.next().text
            self.next()
            return NamedArg(name, self.expr())
        lo = None if self.at(":") else self.expr()
        if self.accept(":"):
            hi = None if self.at(",", close) else self.expr()
            return Slice(lo, hi)
        return lo


def parse(src):
    return Parser(src).parse()
