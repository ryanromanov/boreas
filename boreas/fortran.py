"""A free-form Fortran front end: Fortran source -> the Boreas AST.

It covers a modern subset (program/function/subroutine, declarations, do/if,
array expressions, print/write to the terminal). Anything else becomes a
`! TODO(convert)` comment quoting the original line instead of failing the
whole file. Every translation that is not a 1:1 rename is recorded as a
finding, returned alongside the AST."""
import re
from dataclasses import dataclass, field, fields, is_dataclass, replace

from .fixedform import fixed_to_free, is_fixed_form
from .preprocess import preprocess
from .gotos import Goto, LabelMark, Structurer, child_lists
from .lexer import LangError
from .parser import *
from .interp import BUILTINS, ELEMENTAL, PURE_BUILTINS
from .kinds import round32
from .printer import to_source
from .fformat import FormatError, parse_format, render, substitute

# Words the Boreas parser treats specially; Fortran has no reserved words,
# so a Fortran variable with one of these names is renamed.
RESERVED = {"left", "right", "fixed", "scientific", "general", "tf", "text", "split", "fields", "field", "to_integer", "to_real", "to_logical", "exists", "open", "try_open", "read_line",     # built-ins the converter writes itself
            "close", "file", "const", "use", "module", "public", "private", "and", "or", "not", "true", "false", "in", "out", "inout", "string", "integer",
            "real", "logical", "program", "function", "subroutine", "end", "if", "then",
            "else", "do", "while", "print", "println", "write", "writeln", "read", "stop", "call", "return", "exit", "cycle",
            "select", "case", "default", "fallthrough", "break",
            "allocatable", "allocate", "deallocate", "record"}

FORMAT_STATEMENT = re.compile(r"(\d+)\s+format\s*\((.*)\)\s*$", re.I | re.S)
TYPE_WORDS = {"integer", "real", "logical", "character", "double", "complex", "type", "class"}
BLOCK_KINDS = ("select", "associate", "block", "interface", "where", "forall", "type", "module", "do")


class Unsupported(Exception):
    """Raised for Fortran this front end does not translate."""


SUBMODULE_ORIGIN = "from submodule "


def rename_outside_strings(text, pat, names):
    """`text` with every whole-word match of `pat` swapped for names[match], except inside string literals."""
    parts = re.split(r"('(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")", text)
    for k in range(0, len(parts), 2):
        parts[k] = pat.sub(lambda m: names[m.group(1).lower()], parts[k])
    return "".join(parts)


# --- Lexing ------------------------------------------------------------
@dataclass
class Item:
    line: int
    text: str                    # one statement, comments removed
    comment: str | None = None   # set for a full-line comment
    toks: list | None = None
    label: int | None = None     # the statement label in front of the text (`10 continue`)


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
        glue = " "
        if pending and stripped.startswith("&"):
            stripped, glue = stripped[1:], ""                 # the text carries on exactly where the & was
        cont = stripped.endswith("&")
        if cont:
            stripped = stripped[:-1]
        if pending_line is None:
            pending_line = lineno
        pending = (pending + glue + stripped).strip() if glue else pending + stripped
        if cont:
            continue
        items.extend(statements(pending, pending_line))
        pending, pending_line = "", None
    items.extend(statements(pending, pending_line))
    return items


LABELLED = re.compile(r"(\d+)\s+(?!format\b)(\S.*)$", re.I | re.S)


def statements(text, line):
    """The Items in one logical line; a leading statement label is split off (a `format` keeps it)."""
    items = []
    for part in split_semicolons(text):
        part = part.strip()
        m = LABELLED.match(part)
        if m:
            items.append(Item(line, m.group(2), label=int(m.group(1))))
        elif part:
            items.append(Item(line, part))
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
    if is_case_line(toks):
        return ("case",)
    if t0 in ("type", "class") and nxt is not None and nxt.kind == "name" and nxt.text in ("is", "default"):
        return ("guard",)                           # type is (T) / class is (T) / class default
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


def is_case_line(toks):
    """`case (...)` or `case default`, optionally followed by a construct name."""
    if len(toks) < 2 or toks[0].text != "case" or toks[0].kind != "name":
        return False
    if toks[1].kind == "name" and toks[1].text == "default":
        return len(toks) <= 3
    if not (toks[1].kind == "op" and toks[1].text == "("):
        return False
    depth = 0
    for j, t in enumerate(toks[1:], 1):
        depth += (t.kind == "op" and t.text == "(") - (t.kind == "op" and t.text == ")")
        if depth == 0:
            return j == len(toks) - 1 or (j == len(toks) - 2 and toks[-1].kind == "name")
    return False


# Kind numbers (bytes) of the names in iso_fortran_env.
ISO_KINDS = {"int8": 1, "int16": 2, "int32": 4, "int64": 8, "real32": 4, "real64": 8, "real128": 16}
INT_KINDS = {1: "int8", 2: "int16", 4: "integer", 8: "int64"}
REAL_KINDS = {4: "real32", 8: "real"}
# Fortran lets an intrinsic's arguments be passed by name; Boreas built-ins are positional, so the converter
# puts them in this order. (Looking into keyword arguments for Boreas parameters is on the list: see D52.)
INTRINSIC_PARAMS = {"cshift": ["array", "shift", "dim"], "merge": ["tsource", "fsource", "mask"],
                    "count": ["mask", "dim"], "pack": ["array", "mask", "vector"],
                    "maxloc": ["array", "dim"], "minloc": ["array", "dim"]}
UNSET = object()          # "not given", for an argument that may be None
KIND_ARGUMENT = {"real", "int", "cmplx", "dble", "dcmplx", "float", "sngl", "nint", "floor", "ceiling"}


# Old type-specific intrinsic names, which Fortran still accepts, and the generic function each one is.
SPECIFIC_NAMES = {
    "dcmplx": "cmplx", "dimag": "aimag", "dconjg": "conjg", "cdabs": "abs", "cabs": "abs", "zabs": "abs",
    "csqrt": "sqrt", "cdsqrt": "sqrt", "zsqrt": "sqrt", "cexp": "exp", "cdexp": "exp", "zexp": "exp",
    "clog": "log", "cdlog": "log", "zlog": "log", "csin": "sin", "ccos": "cos",
    "dble": "real64", "float": "real32", "sngl": "real32", "idint": "int", "ifix": "int", "dint": "aint",
    "dnint": "anint", "idnint": "nint", "iabs": "abs", "dabs": "abs", "dsqrt": "sqrt", "dexp": "exp",
    "dlog": "log", "alog": "log", "dlog10": "log10", "alog10": "log10", "dsin": "sin", "dcos": "cos",
    "dtan": "tan", "dasin": "asin", "dacos": "acos", "datan": "atan", "datan2": "atan2",
    "dsinh": "sinh", "dcosh": "cosh", "dtanh": "tanh", "dmod": "mod", "amod": "mod", "dsign": "sign",
    "isign": "sign", "ddim": "dim", "idim": "dim", "dmax1": "max", "amax1": "max", "max0": "max",
    "dmin1": "min", "amin1": "min", "min0": "min",
    "char": "achar", "ichar": "iachar", "command_argument_count": "argument_count",
}


@dataclass
class Info:
    type: str
    dims: list | None
    shape_dropped: bool
    intent: str | None
    init: object
    parameter: bool
    line: int
    allocatable: bool = False
    optional: bool = False
    length: int | None = None     # declared character length, when it is a known number
    rank: int = 0                 # number of dimensions of a dummy whose shape was dropped
    poly: bool = False            # declared class(T) (or as a wrapper of one): any type that extends T
    wrapped: object = None        # the TypeInfo of a wrapper type this was declared with (its component is dropped)


class Scope:
    def __init__(self, kind, name, dummies=()):
        self.kind, self.name, self.dummies = kind, name, list(dummies)
        self.decls = {}
        self.implicit_none = False
        self.result = None
        self.ret_type = None
        self.comments = []
        self.externals = set()
        self.interfaces = {}          # procedure name -> ProcType, from interface blocks
        self.generics = {}            # generic name -> [specific procedure names], from generic interface blocks
        self.elemental = False
        self.proc_dummies = {}        # dummy argument -> ProcType, for `procedure(name) :: dummy`
        self.import_renames = {}      # local name -> the name the module exports, from `use m, only: local => remote`
        self.intrinsics = set()       # names in an INTRINSIC statement
        self.host = None              # enclosing scope, for internal procedures
        self.body = None
        self.skipped = set()          # declared in Fortran we could not translate
        self.data_cells = {}          # name -> {index tuple: value} from data statements
        self.saved = {}               # local name -> (line, why) for variables Fortran keeps between calls
        self.save_all = None          # line of a bare `save` statement (every local is saved)
        self.bad_dummies = set()      # dummy arguments whose declaration we could not translate
        self.line = 0
        self.module = None            # for procedures inside a module: that module's scope
        self.uses = []                # [(module name, names or None for "all", line)]
        self.access = {}              # module scope: name -> "public" | "private" (explicit ones)
        self.default_public = True
        self.formats = {}             # label -> the text inside the parentheses of a `format` statement
        self.units = set()            # unit numbers of the files this procedure uses
        self.commons = {}             # common block name ('' for blank common) -> [(variable, line)], in order
        self.equivs = []              # `equivalence` groups: ([(name, element or None)], line)


@dataclass
class Raw:
    kind: str
    scope: Scope
    body: list
    line: int


@dataclass
class Binding:
    """A type-bound procedure: `procedure :: name => impl`, or `procedure(iface), deferred :: name`."""
    name: str
    impl: str | None              # the module procedure that implements it; None for a deferred one
    iface: str | None             # for a deferred binding: the abstract interface that gives its signature
    line: int
    pass_arg: str | None = None   # `pass(c)`: the dummy argument that receives the object (None: the first)


@dataclass
class TypeInfo:
    """A Fortran derived type. `fields` are Boreas Decls (an extension's start with its parent's);
    `ok` is False when it could not be translated."""
    name: str
    scope: object                 # the scope that defines it
    fields: list
    line: int
    ok: bool = True
    parent: str | None = None     # extends(parent)
    abstract: bool = False
    bindings: dict = field(default_factory=dict)    # its own type-bound procedures: name -> Binding
    wrapper: tuple | None = None  # (T, component) for a type that only holds `class(T), allocatable :: component`
    poly_fields: dict = field(default_factory=dict)   # component -> (T, the wrapper TypeInfo it was declared with, or None)

    @property
    def module(self):
        return self.scope if self.scope.kind == "module" else None


def base_type(typ):
    """The element type of an array result type: 'real[,]' -> 'real'."""
    return typ.split("[")[0]


def type_names(typ):
    """The type names a parameter type mentions (a procedure parameter mentions its signature's)."""
    if isinstance(typ, ProcType):
        return {base_type(t) for t, _ in typ.params} | ({typ.ret} if typ.ret else set())
    return {base_type(typ)}


def iter_nodes(obj):
    """Every dataclass node reachable from `obj` (statements, expressions, lists, tuples)."""
    if isinstance(obj, (list, tuple)):
        for x in obj:
            yield from iter_nodes(x)
    elif is_dataclass(obj):
        yield obj
        for f in fields(obj):
            yield from iter_nodes(getattr(obj, f.name))


def replace_nodes(obj, make):
    """Rebuild `obj` with each node for which `make(node)` returns a node replaced by that node.
    Children are rewritten first; lists change in place, tuples are rebuilt."""
    if isinstance(obj, list):
        obj[:] = [replace_nodes(x, make) for x in obj]
        return obj
    if isinstance(obj, tuple):
        return tuple(replace_nodes(x, make) for x in obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            setattr(obj, f.name, replace_nodes(getattr(obj, f.name), make))
        new = make(obj)
        return obj if new is None else new
    return obj


@dataclass
class ReadMarker:
    """A `read(...,iostat=ios)` waiting for `resolve_reads`, which knows whether `ios` can be dropped."""
    targets: list
    types: list
    status: object
    source: list
    fits: list
    line: int


def implicit_type(name):
    return "integer" if name[0] in "ijklmn" else "real32"


class FParser:
    def __init__(self, src):
        self.items = scan_lines(src)
        self.i = 0
        self.findings = []          # (line, "todo" | "note", message)
        self.kind_names = dict(ISO_KINDS)   # names that stand for a kind number: dp, wp, real64, ...
        self.double_literals = {}           # id -> Num node written as double precision (1.0d0, 1.0_dp); holding the node stops its id being reused
        self.raw = []
        self.renames = {}
        self.line = 0
        self.toks, self.k = [], 0
        self.top_comments = []
        self.once = set()
        self.modules = {}           # module name -> Scope, in source order
        self.fn_lines = {}          # id(Function) -> line of its header
        self.types = {}             # derived type name -> TypeInfo, in source order
        self.cur_scope = None       # the scope of the statement being parsed
        self.generic_calls = {}     # id of an Apply/Call of a generic -> (node, generic, argument types, line)
        self.unresolved_generics = set()
        self.scope_of_procedure = {}
        self.interface_scopes = {}  # procedure name -> the Scope an interface block gave it
        self.header_elemental = False
        self.external_entries = {}  # id of a params entry for an `external` dummy -> (entry, line, procedure name)
        self.intrinsic_args = {}    # id of a Var naming an intrinsic that a call passes -> (Var, line)
        self.ended = None           # the label whose `do` loop just ended on its last statement
        self.unit_use = {}          # unit number -> {"read", "write"}: which way the program uses the file
        self.opens = []             # (unit number, the Str node holding the mode, status, action, position, line)
        self.block_data = []        # the scopes of `block data` units: they only give common variables their first values
        self.module_writers = set()  # ids of functions that change a module variable
        self.do_labels = []         # terminating labels of the enclosing `do 10 ...` loops, innermost last
        self.loops = []             # enclosing named do loops: (Fortran name, Boreas label), innermost last
        self.construct = None       # the name in front of the statement being read: `outer: do`
        self.iface_name = {}        # derived type -> the Boreas interface that stands for class(type), see oo_prepare
        self.raw_names = {}         # a type renamed because it is called like a built-in: new name -> the Fortran name
        self.method_functions = []  # the functions copied into records, see attach_bindings
        self.method_home = {}       # id(such a function) -> the Boreas Module its record is in (None: top level)
        self.method_origin = {}     # id(such a function) -> (type it was defined for, procedure it is a copy of)
        self.eff = {}               # derived type -> {binding name: (Binding, the type it was defined in)}
        self.poly_spec = None       # set by type_spec for class(T) (or a wrapper of one): the T
        self.wrapped = None         # set by type_spec for a wrapper type: its TypeInfo

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
        scope = self.cur_scope
        while scope is not None:                         # `use m, only: local => remote`
            if name in scope.import_renames:
                return scope.import_renames[name]
            scope = scope.host
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
            right = self.concat()
            padded = self.char_info(e) is not None or self.char_info(right) is not None
            if padded:                                       # Fortran ignores trailing blanks when comparing
                e, right = [Apply("trim", [x], self.line) if self.char_info(x) is not None
                            else Str(x.value.rstrip(" ")) if isinstance(x, Str) else x for x in (e, right)]
            e = Binary(self.REL[t.text], e, right)
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
            e = self.single_constant(Binary(op, e, self.mul()))
        return e

    def mul(self):
        e = self.power()
        while self.at_op("*", "/"):
            op = self.next().text
            e = self.single_constant(Binary(op, e, self.power()))
        return e

    def is_single_constant(self, e):
        """A number written in single precision, or arithmetic on such numbers (1.0/3.0)."""
        if isinstance(e, Num):
            return isinstance(e.value, float) and id(e) not in self.double_literals
        if isinstance(e, Apply):
            return e.name == "real32" and len(e.args) == 1
        if isinstance(e, Unary):
            return self.is_single_constant(e.operand)
        return False

    def single_constant(self, e):
        """Fortran computes 1.0/3.0 in single precision; so does real32(1.0/3.0) (a double holds a single
        operation exactly enough that rounding once gives the single result)."""
        if self.is_single_constant(e.left) and self.is_single_constant(e.right):
            return Apply("real32", [e], self.line)
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
            lit = Num(t.value)
            if t.kind == "int":
                n = self.literal_kind(t.text)
                if n in (1, 2, 8):                              # 5_int64, 5_8, 5_i2
                    return Apply(INT_KINDS[n], [lit], self.line)
            elif self.is_double_literal(t.text):
                self.double_literals[id(lit)] = lit
            elif round32(t.value) != t.value:                   # a single-precision 0.1 is not 0.1
                return Apply("real32", [lit], self.line)
            return lit
        if t.kind == "str":
            if '"' in t.value and "'" in t.value:
                raise Unsupported("string containing both kinds of quote")
            return Str(t.value)
        if t.kind == "dop" and t.text in (".true.", ".false."):
            return Bool(t.text == ".true.")
        if t.kind == "op" and t.text == "(":
            e = self.expr()
            if self.at_op(","):                          # (re, im) is a complex literal; (x(i), i=1,n) an implied do
                nxt = self.peek(2)
                if self.peek(1) is not None and self.peek(1).kind == "name" and nxt is not None and nxt.text == "=":
                    raise Unsupported("implied-do")
                self.k += 1
                im = self.expr()
                self.expect_op(")")
                return Apply("cmplx", [e, im], self.line)
            self.expect_op(")")
            return e
        if t.kind == "op" and t.text in ("[", "(/"):
            close = "]" if t.text == "[" else "/)"
            items = []
            if not self.at_op(close):
                items.append(self.constructor_item())
                while self.accept_op(","):
                    items.append(self.constructor_item())
            if not self.accept_op(close):
                raise Unsupported("implied-do or malformed array constructor")
            return ArrayLit(items)
        if t.kind == "name":
            name = t.text
            if not self.at_op("("):
                e = Var(self.rename(name))
            else:
                self.k += 1
                args = self.call_arguments(name)
                if name == "allocated" and len(args) == 1 and isinstance(args[0], (Var, Apply, FieldAccess)) \
                        and self.is_poly(args[0]):
                    raise Unsupported("allocated() of a class(T) value: Boreas interface values are always set")
                name = SPECIFIC_NAMES.get(name, name)
                name, args = self.convert_intrinsic(name, args)
                intrinsic = name in BUILTINS or name in ELEMENTAL
                e = Apply(name if intrinsic else self.rename(name), args, self.line)
                if not intrinsic:
                    self.note_generic_call(e, e.name, args, self.line)
                if name == "len" and len(args) == 1 and self.char_info(args[0]) is not None \
                        and name not in self.cur_scope.decls:
                    e = Num(self.char_info(args[0]).length)          # the declared length, not the text's
            while self.accept_op("%"):                       # derived-type component: p%x, pts(2)%pos%y
                if isinstance(e, Apply) and any(isinstance(a, Slice) for a in e.args):
                    raise Unsupported("a component of an array section (pts(1:3)%x)")
                if isinstance(e, Var) and self.is_array_name(e.name):
                    raise Unsupported(f"a component of a whole array ({e.name}%...): Boreas needs one element")
                field = self.rename(self.expect_name())
                holder = self.wrapped_by(e)
                if holder is not None and field == holder.wrapper[1]:
                    if self.at_op("("):
                        raise Unsupported(f"'{field}' of a wrapper type is a single value, not an array")
                    continue                                 # all(i)%s is just all(i)
                args = None
                if self.accept_op("("):
                    args = self.call_arguments(field)
                e = FieldAccess(e, field, args, self.line)
                e.call = args is not None and self.is_binding(e.base, field)
            return e
        raise Unsupported(f"unexpected {t.text!r} in expression")

    def record_type_of(self, e):
        """The derived type of a variable, array element or component, or None."""
        if isinstance(e, (Var, Apply)):
            info = self.lookup_decl(e.name)
            return info.type if info is not None and info.type in self.types else None
        if isinstance(e, FieldAccess):
            base = self.types.get(self.record_type_of(e.base))
            for d in base.fields if base is not None else []:
                if d.name == e.field:
                    return d.type if d.type in self.types else None
        return None

    def is_binding(self, base, name):
        """Is `name` a type-bound procedure of the derived type of `base` (or of a type it extends)?"""
        t = self.types.get(self.record_type_of(base))
        while t is not None:
            if name in t.bindings:
                return True
            t = self.types.get(t.parent)
        return False

    def wrapped_by(self, e):
        """The wrapper type a variable, array element or component was declared with, or None."""
        if isinstance(e, (Var, Apply)):
            info = self.lookup_decl(e.name)
            return info.wrapped if info is not None else None
        if isinstance(e, FieldAccess):
            owner = self.types.get(self.record_type_of(e.base))
            return owner.poly_fields.get(e.field, (None, None))[1] if owner is not None else None
        return None

    def is_poly(self, e):
        """Is this expression a class(T) value: a variable, element or component declared that way?"""
        if isinstance(e, (Var, Apply)):
            info = self.lookup_decl(e.name)
            return info is not None and info.poly
        if isinstance(e, FieldAccess):
            owner = self.types.get(self.record_type_of(e.base))
            return owner is not None and e.field in owner.poly_fields
        return False

    def literal_kind(self, raw):
        """The kind number after the underscore in 5_8 or 1.0_dp, or None."""
        if "_" not in raw:
            return None
        suffix = raw.lower().split("_", 1)[1]
        return int(suffix) if suffix.isdigit() else self.kind_names.get(suffix)

    def is_double_literal(self, raw):
        """1.0d0 and 1.0_dp are double precision (a name that is not a known kind counts as single)."""
        kind = self.literal_kind(raw)
        if kind is not None:
            return kind >= 8
        return "d" in raw.lower()

    def kind_number(self, e):
        """The kind number (bytes) an expression stands for, or None: 8, dp, real64, kind(1.0d0),
        selected_real_kind(15), selected_int_kind(9), ..."""
        if isinstance(e, Num) and isinstance(e.value, int):
            return e.value
        if isinstance(e, Var):
            return self.kind_names.get(e.name)
        if isinstance(e, Apply) and e.name in ("kind", "selected_real_kind", "selected_int_kind") and e.args:
            a = e.args[0]
            if e.name == "kind":
                if isinstance(a, Num):
                    return 4 if isinstance(a.value, int) else (8 if id(a) in self.double_literals else 4)
                if isinstance(a, Apply) and a.name in ("real32", "real"):
                    return 4 if a.name == "real32" else 8
                return None
            if not (isinstance(a, Num) and isinstance(a.value, int)):
                return None
            if e.name == "selected_real_kind":
                return 4 if a.value <= 6 else 8 if a.value <= 15 else 16
            return 1 if a.value <= 2 else 2 if a.value <= 4 else 4 if a.value <= 9 else 8 if a.value <= 18 else 16
        return None

    def convert_intrinsic(self, name, args):
        """Kind arguments of the conversion functions, and the kind of a bare real literal that
        epsilon/huge/tiny look at."""
        kind_args = [a for a in args if isinstance(a, NamedArg) and a.name == "kind"]
        args = [a for a in args if not (isinstance(a, NamedArg) and a.name == "kind")]
        if name in ("epsilon", "tiny", "huge") and args and isinstance(args[0], Num) \
                and isinstance(args[0].value, float) and id(args[0]) not in self.double_literals:
            return name, [Apply("real32", [args[0]], self.line)]
        if name in ("real", "int", "cmplx"):
            keep = 2 if name == "cmplx" else 1
            kind = kind_args[0].value if kind_args else args[keep] if len(args) > keep else None
            args = args[:keep]
            if kind is not None:
                n = self.kind_number(kind)
                if n is None:
                    self.note(self.line, f"{name}(): kind argument not understood, dropped")
                elif name == "int":
                    if n not in INT_KINDS:
                        raise Unsupported(f"int() of kind {n}")
                    name = {"int8": "int8", "int16": "int16", "integer": "int", "int64": "int64"}[INT_KINDS[n]]
                elif name == "real":
                    if n not in REAL_KINDS:
                        raise Unsupported(f"real() of kind {n}")
                    name = "real32" if n == 4 else "real64"
                else:
                    self.note(self.line, "cmplx(): kind argument dropped (Boreas complex numbers are double precision)")
            elif name == "real" and not self.is_complex_expr(args[0] if args else None):
                name = "real32"                              # real(x) is a default (single) real, unless x is complex
        return name, args

    def is_complex_expr(self, e):
        """Is this expression certainly complex? (real(z) is the real part, in z's own kind.)"""
        if isinstance(e, Apply):
            if e.name in ("cmplx", "conjg"):
                return True
            info = self.lookup_decl(e.name)
            return info is not None and info.type == "complex"
        if isinstance(e, Var):
            info = self.lookup_decl(e.name)
            return info is not None and info.type == "complex"
        if isinstance(e, Binary):
            return self.is_complex_expr(e.left) or self.is_complex_expr(e.right)
        if isinstance(e, Unary):
            return self.is_complex_expr(e.operand)
        return False

    def lookup_decl(self, name):
        s = self.cur_scope
        while s is not None:
            info = s.decls.get(name)
            if info is not None:
                return info
            s = s.host
        return None

    def is_array_name(self, name):
        s = self.cur_scope
        while s is not None:
            info = s.decls.get(name)
            if info is not None:
                return info.dims is not None or info.shape_dropped
            s = s.host
        return False

    def call_arguments(self, fname):
        """The arguments after an opening parenthesis, through the closing one."""
        args = []
        if not self.at_op(")"):
            while True:
                a = self.argument(fname)
                if a is not None:
                    args.append(a)
                if not self.accept_op(","):
                    break
        self.expect_op(")")
        if any(isinstance(a, NamedArg) and a.name != "kind" for a in args) and fname in INTRINSIC_PARAMS:
            args = self.positional_order(fname, args)
        return args

    def positional_order(self, fname, args):
        """Boreas built-ins take positional arguments only, so `cshift(array = a, shift = 2)` becomes
        `cshift(a, 2)`. A parameter left out in the middle cannot be skipped."""
        names = INTRINSIC_PARAMS[fname]
        slots = {}
        for n, a in enumerate(args):
            if isinstance(a, NamedArg) and a.name in names:
                key, value = names.index(a.name), a.value
            else:
                key, value = n, a
            if key in slots:
                raise Unsupported(f"argument given twice in {fname}()")
            slots[key] = value
        top = max(slots)
        if any(k not in slots for k in range(top + 1)):
            raise Unsupported(f"{fname}() with an argument left out before a later one")
        return [slots[k] for k in range(top + 1)]

    def argument(self, fname):
        t, t2 = self.peek(), self.peek(1)
        if t is not None and t.kind == "name" and t2 is not None and t2.kind == "op" and t2.text == "=":
            if t.text == "kind":
                self.k += 2
                value = self.expr()
                return NamedArg("kind", value) if fname in KIND_ARGUMENT else None
            if self.rename(fname) in self.types:             # a structure constructor: point(x = 1.0)
                self.k += 2
                return NamedArg(self.rename(t.text), self.expr())
            if fname in INTRINSIC_PARAMS and t.text in INTRINSIC_PARAMS[fname]:
                self.k += 2
                return NamedArg(t.text, self.expr())
            raise Unsupported(f"keyword argument '{t.text}=' in {fname}()")
        lo = None if self.at_op(":") else self.expr()
        if isinstance(lo, Var) and lo.name in self.cur_scope.intrinsics:
            self.intrinsic_args[id(lo)] = (lo, self.line)         # wrapped in a function once the callee is known
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
        self.merge_submodules()
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
                elif kind == "module":
                    self.i += 1
                    name = self.rename(toks[1].text) if len(toks) > 1 else "module"
                    self.parse_unit("module", Scope("module", name), it)
                elif kind == "block" and len(toks) > 1 and toks[1].text == "data":
                    self.i += 1
                    self.parse_unit("block", Scope("blockdata", "block_data"), it)
                elif kind == "submodule":
                    self.i += 1
                    self.skip_block("module")
                    raise Unsupported("submodules are not supported; skipped")
                else:
                    raise Unsupported("statement outside any program unit")
            except Unsupported as e:
                if self.i == start:
                    self.i += 1
                self.top_comments.append(f"TODO(convert): {e}: {it.text}")
                self.todo(it.line, f"{e}: {it.text}")
        return self.finalize()

    def merge_submodules(self):
        """Fortran splits a module into headers (the module) and bodies (its submodules) to save recompiling.
        Boreas runs from source and has no use for the split, so each submodule's declarations and procedures
        are moved into the parent module, which keeps one module per file (D49). A private procedure whose name
        another part already uses is renamed with the submodule's name in front. Works on the statement list,
        before parsing, so the parts may come in any order and from several files."""
        units = self.top_level_units()
        subs = [u for u in units if u["kind"] == "submodule"]
        if not subs:
            return
        mods = {}
        for u in units:
            if u["kind"] == "module":
                mods.setdefault(u["name"], u)
        taken = {}                     # procedure name -> how many parts define it
        for u in units:
            if u["kind"] == "module" or u in subs:
                for name in u["procs"]:
                    taken[name] = taken.get(name, 0) + 1
        by_root = {}
        for u in subs:
            if u["root"] in mods:
                by_root.setdefault(u["root"], []).append(u)
            else:
                self.todo(self.items[u["start"]].line, f"submodule '{u['name']}': its parent module '{u['root']}' "
                                                       f"was not given; convert them together")
        drop, before_contains, before_end = set(), {}, {}
        for root, parts in by_root.items():
            m = mods[root]
            spec, body = [], []
            for u in parts:
                items = self.renamed_private(u, taken)
                inner = self.expand_module_procedures(items[u["start"] + 1:u["end"]], self.items[m["start"]:m["end"]])
                k = self.contains_index(inner, 0, len(inner))
                spec += inner[:k]
                hidden = [f"{u['name']}_{n}" if self.is_clash(n, u, taken) else n
                          for n, public in u["procs"].items() if not public]
                if hidden:                                # a submodule's own procedures stay hidden from users
                    spec.append(Item(items[u["start"]].line, "private :: " + ", ".join(hidden)))
                if k < len(inner):
                    body.append(Item(items[u["start"]].line, "", comment=f"{SUBMODULE_ORIGIN}{u['name']} (of {root})"))
                    body += inner[k + 1:]
                drop.update(range(u["start"], u["end"] + 1))
            mc = self.contains_index(self.items, m["start"] + 1, m["end"])
            if mc < m["end"]:
                before_contains[mc] = spec
            else:
                body = spec + [Item(self.items[m["end"]].line, "contains")] + body
            before_end[m["end"]] = body
        out = []
        for j, it in enumerate(self.items):
            if j in drop:
                continue
            out.extend(before_contains.get(j, ()))
            out.extend(before_end.get(j, ()))
            out.append(it)
        self.items = out

    def expand_module_procedures(self, inner, parent):
        """`module procedure NAME` ... `end procedure` takes its header and its dummy declarations from the
        interface in the parent module. Write them out in full, as an ordinary function or subroutine."""
        out, j = [], 0
        while j < len(inner):
            it = inner[j]
            toks = None
            if it.comment is None:
                try:
                    toks = self.tokens(it)
                except Unsupported:
                    toks = None
            if not toks or len(toks) != 3 or toks[0].text != "module" or toks[1].text != "procedure":
                out.append(it)
                j += 1
                continue
            name, found = toks[2].text, self.interface_body(toks[2].text, parent)
            if found is None:
                out.append(it)
                j += 1
                continue
            head, decls, kind = found
            out.append(head)
            out.extend(decls)
            j += 1
            while j < len(inner):
                if inner[j].comment is None:
                    try:
                        lab = classify(self.tokens(inner[j]))
                    except Unsupported:
                        lab = None
                    if lab is not None and lab[0] == "end" and lab[1] in ("procedure", None):
                        break
                out.append(inner[j])
                j += 1
            out.append(Item(inner[min(j, len(inner) - 1)].line, f"end {kind}"))
            j += 1
        return out

    def interface_body(self, name, parent):
        """(header, declarations, 'function' or 'subroutine') of the interface body for `name` in `parent`'s
        statements, or None. `import` is dropped: it only means something inside an interface."""
        for j, it in enumerate(parent):
            if it.comment is not None:
                continue
            try:
                toks = self.tokens(it)
            except Unsupported:
                continue
            kind = self.unit_kind(toks)
            if kind not in ("function", "subroutine") or not self.has_module_prefix(toks) \
                    or self.procedure_name(toks) != name:
                continue
            decls = []
            for k in range(j + 1, len(parent)):
                inner = parent[k]
                if inner.comment is None:
                    try:
                        tk = self.tokens(inner)
                    except Unsupported:
                        continue
                    lab = classify(tk)
                    if lab is not None and lab[0] == "end" and lab[1] in (kind, None):
                        return it, decls, kind
                    if tk and tk[0].text == "import":
                        continue
                decls.append(inner)
        return None

    def contains_index(self, items, lo, hi):
        """Index of the first `contains` in items[lo:hi], or hi."""
        for j in range(lo, hi):
            if items[j].comment is None:
                try:
                    if classify(self.tokens(items[j])) == ("contains",):
                        return j
                except Unsupported:
                    pass
        return hi

    def top_level_units(self):
        """Every module and submodule in the statement list: kind, name, root module, the first and last
        statement, and the names of the procedures it defines."""
        units, i = [], 0
        while i < len(self.items):
            it = self.items[i]
            toks = None
            if it.comment is None:
                try:
                    toks = self.tokens(it)
                except Unsupported:
                    toks = None
            if not toks or toks[0].kind != "name" or toks[0].text not in ("module", "submodule") \
                    or self.unit_kind(toks) not in ("module", "submodule") \
                    or (toks[0].text == "module" and len(toks) > 1 and toks[1].text == "procedure"):
                i += 1
                continue
            kind = toks[0].text
            names = [t.text for t in toks[1:] if t.kind == "name"]
            if kind == "submodule":
                if len(names) < 2:
                    i += 1
                    continue
                name, root = names[-1], names[0]
            else:
                name = root = names[0] if names else "module"
            end, procs, depth = None, {}, 0
            for j in range(i + 1, len(self.items)):
                jt = self.items[j]
                if jt.comment is not None:
                    continue
                try:
                    tk = self.tokens(jt)
                except Unsupported:
                    continue
                lab = classify(tk)
                if lab is not None and lab[0] == "end" and lab[1] in ("module", "submodule", None) and depth == 0:
                    end = j
                    break
                if lab is not None and lab[0] == "end" and lab[1] in ("function", "subroutine"):
                    depth -= 1
                    continue
                uk = self.unit_kind(tk)
                if uk in ("function", "subroutine"):
                    if depth == 0:
                        procs[self.rename(self.procedure_name(tk))] = self.has_module_prefix(tk)
                    depth += 1
            if end is None:
                i += 1
                continue
            units.append({"kind": kind, "name": name, "root": root, "start": i, "end": end, "procs": procs})
            i = end + 1
        return units

    def has_module_prefix(self, toks):
        """True for `module function f(...)`, with or without `pure` and the like in front."""
        for t in toks:
            if t.text in ("function", "subroutine"):
                return False
            if t.text == "module":
                return True
        return False

    def procedure_name(self, toks):
        """The name in a `function`/`subroutine` statement."""
        depth = 0
        for j, t in enumerate(toks):
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
            elif depth == 0 and t.kind == "name" and t.text in ("function", "subroutine"):
                return toks[j + 1].text
        return ""

    def is_clash(self, name, u, taken):
        """A private procedure of a submodule that some other part also defines."""
        return not u["procs"][name] and taken.get(name, 0) > 1

    def renamed_private(self, u, taken):
        """The statements of submodule `u` (as a list from the first statement of the submodule), with each
        private procedure that another part also defines renamed to `<submodule>_<name>`."""
        items = list(self.items[:u["end"] + 1])
        clashes = {n: f"{u['name']}_{n}" for n in u["procs"] if self.is_clash(n, u, taken)}
        if not clashes:
            return items
        pat = re.compile(r"(?<![\w%])(" + "|".join(map(re.escape, clashes)) + r")(?!\w)", re.I)
        for j in range(u["start"] + 1, u["end"]):
            it = items[j]
            if it.comment is None:
                items[j] = Item(it.line, rename_outside_strings(it.text, pat, clashes), None, None, it.label)
        return items

    def procedure_header(self, toks):
        self.toks, self.k = toks, 0
        self.header_elemental = False
        ret_type = None
        while True:
            if self.at_name("pure", "impure", "elemental", "recursive", "module", "non_recursive"):
                if self.at_name("elemental"):
                    self.header_elemental = True
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

    def parse_procedure(self, toks, it, host=None):
        kind, name, dummies, result, ret_type = self.procedure_header(toks)
        scope = Scope(kind, name, dummies)
        scope.result, scope.ret_type = result, ret_type
        scope.elemental = self.header_elemental
        if host is not None:
            scope.host = host
            scope.implicit_none = host.implicit_none       # internal procedures inherit it
            scope.module = host if host.kind == "module" else host.module
            if scope.module is not None:
                scope.skipped = set(scope.module.skipped)   # module variables we could not translate
        self.parse_unit(kind, scope, it)

    def parse_unit(self, kind, scope, it):
        """Parse from just after the header through `end <kind>`, hoisting internal procedures."""
        scope.comments, self.top_comments = self.top_comments, []
        self.collect_formats(scope, kind)
        body = self.parse_body(scope, {("end", kind), ("contains",)})
        scope.body = body
        self.check_data_cells(scope, it.line)
        self.check_import_renames(scope, it.line)
        toks = self.tokens(self.items[self.i])
        if classify(toks) == ("contains",):
            self.i += 1
            if kind != "module":
                self.note(it.line, "internal procedures were moved out of their host; "
                                   "variables of the host are not visible to them")
            while True:
                if self.items[self.i].comment is not None:
                    if self.items[self.i].comment.startswith(SUBMODULE_ORIGIN):
                        self.top_comments.append(self.items[self.i].comment)   # where a merged procedure came from
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
                    self.parse_procedure(toks, cur, scope)
                else:
                    self.todo(cur.line, f"unsupported statement after contains: {cur.text}")
        self.i += 1                        # the `end` statement
        if kind == "block":
            self.block_data.append(scope)
        elif kind == "module":
            scope.line = it.line
            if scope.name in self.modules:
                self.todo(it.line, f"module '{scope.name}' is defined more than once")
            self.modules[scope.name] = scope
        else:
            self.raw.append(Raw(kind, scope, body, it.line))

    def collect_formats(self, scope, kind):
        """Labelled `format` statements may come after the write that uses them, so find them first."""
        j = self.i
        while j < len(self.items):
            it = self.items[j]
            j += 1
            if it.comment is not None:
                continue
            m = FORMAT_STATEMENT.match(it.text)
            if m:
                scope.formats[int(m.group(1))] = m.group(2)
                continue
            try:
                lab = classify(self.tokens(it))
            except Unsupported:
                continue
            if lab == ("contains",) or (lab is not None and lab[0] == "end" and lab[1] in (kind, None)):
                return

    def is_stop(self, toks, stops):
        lab = classify(toks)
        if lab is None:
            return False
        if lab[0] == "end":
            return any(s[0] == "end" and (lab[1] == s[1] or lab[1] is None) for s in stops)
        return lab in stops

    def parse_body(self, scope, stops, until=None):
        """Statements up to one of `stops`, or (for `do 10 ...`) through the statement labelled `until`."""
        body = []
        while self.i < len(self.items):
            it = self.items[self.i]
            start = self.i
            if it.comment is not None:
                body.append(Comment(it.comment))
                self.i += 1
                continue
            self.line = it.line
            if until is not None and self.ended == until:
                return body                                   # an inner loop already took the shared last statement
            self.ended = None
            ends = until is not None and it.label == until
            try:
                toks = self.tokens(it)
                if self.is_stop(toks, stops):
                    return body
                self.i += 1
                nodes = self.statement(it, toks, scope)
                bad = self.uses_skipped(nodes, scope)
                if bad:
                    raise Unsupported(f"uses '{bad}', whose declaration could not be translated")
                if it.label is not None:
                    body.append(LabelMark(it.label))
                body.extend(nodes)
            except Unsupported as e:
                if self.i == start:
                    self.i += 1
                body.append(self.todo_comment(it, str(e)))
            if ends:
                self.ended = until
                return body
        raise LangError("unexpected end of file: a block is missing its 'end' statement")

    def uses_skipped(self, nodes, scope):
        if not scope.skipped:
            return None
        for n in iter_nodes(nodes):
            for name in (getattr(n, "name", None), getattr(n, "var", None)):
                if isinstance(name, str) and name in scope.skipped and not isinstance(n, (Decl, Function, NamedArg)) \
                        and name not in scope.decls and (name not in scope.dummies or name in scope.bad_dummies):
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
        self.cur_scope = scope
        t0 = toks[0]
        if FORMAT_STATEMENT.match(it.text):
            return []                                  # used by the writes that name its label
        if t0.kind != "name":
            raise Unsupported("unrecognised statement (labels are not supported)")
        name = t0.text
        self.construct = None
        if len(toks) > 2 and toks[1].kind == "op" and toks[1].text == ":" and toks[2].kind == "name":
            self.k = 2                                # `outer: do ...`
            if toks[2].text == "do":
                self.construct = name                 # st_do keeps it as the loop's label
            else:
                self.note(it.line, f"construct name '{name}' dropped")
            name = toks[2].text
        elif self.is_assignment():
            return [self.assignment(it)]
        if name in TYPE_WORDS and name != "type" and not (len(toks) > self.k + 1 and toks[self.k + 1].text == "="):
            return self.declaration(it, scope)
        handler = getattr(self, "st_" + name, None)
        if handler is not None:
            self.k += 1
            return handler(it, scope)
        if name in ("namelist", "include",
                    "nullify", "rewind",
                    "backspace", "format", "entry", "pause", "dimension", "pointer",
                    "target", "enum", "sync", "lock", "unlock"):
            raise Unsupported(f"'{name}' is not supported")
        if name in BLOCK_KINDS or name in ("endselect",):
            raise Unsupported(f"'{name}' is not supported")
        raise Unsupported("unrecognised statement")

    def is_assignment(self):
        """Is this `name = ...`, `name(...) = ...` or `name%part(...)%part = ...`?"""
        j = self.k + 1
        while j < len(self.toks):
            t = self.toks[j]
            if t.kind == "op" and t.text == "(":
                depth = 0
                while j < len(self.toks):
                    tj = self.toks[j]
                    if tj.kind == "op" and tj.text == "(":
                        depth += 1
                    elif tj.kind == "op" and tj.text == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                j += 1
            elif t.kind == "op" and t.text == "%":
                j += 2
            else:
                return t.kind == "op" and t.text in ("=", "=>") and j > self.k + 0
        return False

    def assignment(self, it):
        target = self.primary()
        if not isinstance(target, (Var, Apply, FieldAccess)):
            raise Unsupported("cannot assign to this expression")
        if self.at_op("=>"):
            raise Unsupported("pointer assignment")
        self.expect_op("=")
        value = self.expr()
        self.end_of_statement()
        info = self.char_info(target)
        if info is not None:
            value = self.fit_to_length(value, info.length)
        return Assign(target, value, it.line)

    def character_length(self):
        """The length in `character(len=5)`, `character(5)`, `character*5`; None when it is `*`, a name we
        cannot resolve, or missing (length 1 is then the Fortran default)."""
        if self.at_op("*") and self.peek(1) is not None and self.peek(1).kind == "int":
            self.k += 1
            return self.next().value
        if self.at_op("*") and self.peek(1) is not None and self.peek(1).text == "(":      # character*(*), character*(n)
            self.k += 2
            length = None if self.accept_op("*") else self.constant_length(self.expr())
            self.expect_op(")")
            return length
        if not self.at_op("("):
            return 1
        self.k += 1
        length, positional = None, 0
        while not self.at_op(")"):
            keyword = self.peek().text if self.peek() is not None and self.peek(1) is not None \
                and self.peek(1).text == "=" else None
            if keyword is not None:
                self.k += 2
            else:
                keyword = ("len", "kind")[positional] if positional < 2 else "kind"
                positional += 1
            if keyword == "len" and self.accept_op("*"):
                length = None
            elif keyword == "len":
                length = self.constant_length(self.expr())
            else:
                self.expr()                                     # kind
            if not self.accept_op(","):
                break
        self.expect_op(")")
        return length

    def constant_length(self, e):
        if isinstance(e, Num) and isinstance(e.value, int):
            return e.value
        info = getattr(self, "cur_scope", None) and self.cur_scope.decls.get(e.name) if isinstance(e, Var) else None
        if info is not None and info.parameter and isinstance(info.init, Num) and isinstance(info.init.value, int):
            return info.init.value
        return None

    def char_info(self, e):
        """The declared Info of a fixed-length character variable or array element named by `e`, or None."""
        scope = getattr(self, "cur_scope", None)
        if scope is None or not isinstance(e, (Var, Apply)):
            return None
        info = scope.decls.get(e.name)
        if info is None or info.type != "string" or info.length is None:
            return None
        is_array = info.dims is not None or info.shape_dropped
        if isinstance(e, Apply) and not is_array:
            return None                                  # a substring, not an element
        if isinstance(e, Var) and is_array:
            return None                                  # a whole array
        return info

    def fit_to_length(self, value, n):
        """Fortran stores text in a character(len=n) variable padded with blanks or cut to n."""
        if isinstance(value, Str):
            return Str(value.value[:n].ljust(n))
        return Apply("left", [value, Num(n)], self.line)

    # declarations
    def type_spec(self):
        word = self.expect_name()
        if word == "double":
            if self.at_name("complex"):
                self.k += 1
                return "complex"
            if not self.at_name("precision"):
                raise Unsupported("expected 'double precision' or 'double complex'")
            self.k += 1
            return "real"
        if word == "class":
            if not self.accept_op("("):
                raise Unsupported("expected 'class(name)'")
            if self.at_op("*"):
                raise Unsupported("class(*) is not supported")
            name = self.type_name(self.expect_name())
            self.expect_op(")")
            info = self.types.get(name)
            if info is None:
                raise Unsupported(f"derived type '{name}' is not defined earlier in this file")
            if not info.ok:
                raise Unsupported(f"derived type '{name}' was not translated")
            if info.wrapper is not None:
                raise Unsupported(f"class({name}) of a wrapper type")
            self.poly_spec = name
            return name
        if word == "type":
            if not self.accept_op("("):
                raise Unsupported("expected 'type(name)'")
            name = self.type_name(self.expect_name())
            self.expect_op(")")
            info = self.types.get(name)
            if info is None:
                raise Unsupported(f"derived type '{name}' is not defined earlier in this file")
            if not info.ok:
                raise Unsupported(f"derived type '{name}' was not translated")
            if info.wrapper is not None:               # a wrapper of class(T) is just a polymorphic T in Boreas
                self.poly_spec, self.wrapped = info.wrapper[0], info
                return info.wrapper[0]
            if info.abstract:
                raise Unsupported(f"type({name}) of an abstract type")
            return name
        if word == "character":
            self.char_len = self.character_length()
            return "string"
        number = None
        if self.at_op("("):
            number = self.kind_in_parens(word)
        elif self.at_op("*") and self.peek(1) is not None and self.peek(1).kind == "int":
            number = self.peek(1).value
            self.k += 2
            if word == "complex":
                number //= 2                              # complex*16 is two 8-byte reals
        elif word == "complex":
            self.note(self.line, "default 'complex' is single precision in Fortran but double in Boreas, "
                                 "so results can differ in the last digits", once=True)
        return self.kinded(word, number)

    def kind_in_parens(self, word):
        """`(8)`, `(kind=8)`, `(dp)`: the kind number, or None when it cannot be worked out."""
        self.expect_op("(")
        if self.at_name("kind") and self.peek(1) is not None and self.peek(1).text == "=":
            self.k += 2
        e = self.expr()
        self.expect_op(")")
        number = self.kind_number(e)
        if number is None:
            self.note(self.line, f"{word} kind could not be worked out and was dropped", once=True)
        return number

    def kinded(self, word, number):
        """The Boreas type for a Fortran type word and kind number (None: the default kind)."""
        if word == "integer":
            if number is None:
                return "integer"
            if number not in INT_KINDS:
                raise Unsupported(f"integer(kind={number}) is not supported")
            return INT_KINDS[number]
        if word == "real":
            if number is None:
                return "real32"
            if number not in REAL_KINDS:
                raise Unsupported(f"real(kind={number}) is not supported (Boreas has real32 and real64)")
            return REAL_KINDS[number]
        if word == "complex" and number is not None:
            if number not in (4, 8):
                raise Unsupported(f"complex(kind={number}) is not supported")
        return word

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
        self.char_len = None
        self.poly_spec = self.wrapped = None
        typ = self.type_spec()
        char_len = self.char_len
        poly, wrapped = self.poly_spec, self.wrapped
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
            elif word == "allocatable":
                attrs["allocatable"] = True
            elif word in ("pointer", "volatile", "asynchronous", "protected"):
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
                length = char_len
                if self.at_op("*") and self.peek(1) is not None and self.peek(1).kind == "int":
                    length = self.peek(1).value                  # character :: name*20
                    self.k += 2
                init = None
                if self.at_op("=>"):
                    raise Unsupported("pointer initialisation")
                if self.accept_op("="):
                    init = self.expr()
                dims, dropped = None, False
                scalar_poly = bool(poly) and bool(attrs.get("allocatable")) and specs is None
                if scalar_poly:                  # class(T), allocatable :: s: a Boreas variable of an interface type
                    if scope.kind != "type" and name in scope.dummies:
                        raise Unsupported(f"allocatable dummy argument '{name}'")
                elif attrs.get("allocatable"):
                    if poly:
                        raise Unsupported("an allocatable array of polymorphic values")
                    dims = self.allocatable_dims(name, specs, init, scope)
                elif specs is not None:
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
                if attrs.get("parameter") and init is None:
                    raise Unsupported(f"constant '{name}' has no value here (implied-shape or set elsewhere)")
                if attrs.get("parameter") and typ == "integer" and dims is None:
                    number = self.kind_number(init)
                    if number is not None:                     # a kind constant: dp = kind(1.0d0), i8 = 8, ...
                        self.kind_names[name] = number
                        if isinstance(init, Apply):
                            init = Num(number)
                if not attrs.get("parameter") and scope.kind not in ("program", "module", "type", "blockdata") \
                        and name not in scope.dummies and (init is not None or attrs.get("save")):
                    scope.saved[name] = (line, "declared save" if attrs.get("save")
                                         else "initialised in its declaration, which implies save")
                if typ == "string":
                    if length is None or scope.kind == "type":
                        self.note(line, "character length not tracked here: Boreas strings never pad or truncate, "
                                        "so assigning to a shorter variable or comparing padded text can differ",
                                  once=True)
                        length = None
                    elif isinstance(init, Str):
                        init = self.fit_to_length(init, length)
                scope.decls[name] = Info(typ, dims, dropped, attrs.get("intent"), init,
                                         bool(attrs.get("parameter")), line,
                                         bool(attrs.get("allocatable")) and not scalar_poly,
                                         bool(attrs.get("optional")), length,
                                         len(specs) if dropped else 0, bool(poly), wrapped)
                if attrs.get("external"):
                    scope.externals.add(name)
                if scope.kind == "module":
                    for word in ("public", "private"):
                        if attrs.get(word):
                            scope.access[name] = word
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

    def allocatable_dims(self, name, specs, init, scope):
        """The dims of an allocatable local: one `:` per dimension, which is its rank."""
        if scope.kind == "type":
            raise Unsupported(f"allocatable component '{name}': Boreas record fields cannot be allocatable")
        if name in scope.dummies:
            scope.bad_dummies.add(name)
            scope.skipped.add(name)
            raise Unsupported(f"allocatable dummy argument '{name}': Boreas parameters cannot be "
                              f"allocatable, and a callee cannot allocate its caller's array")
        if specs is None or not all(sp == "deferred" for sp in specs):
            raise Unsupported(f"allocatable '{name}' needs a deferred shape such as '(:)'")
        if init is not None:
            raise Unsupported("allocatable with an initializer")
        return [Slice(None, None) for _ in specs]

    def st_implicit(self, it, scope):
        if self.at_name("none"):
            scope.implicit_none = True
            return []
        raise Unsupported("only 'implicit none' is supported")

    def st_use(self, it, scope):
        if self.accept_op(","):                       # `use, intrinsic :: iso_fortran_env`
            if not self.at_name("intrinsic", "non_intrinsic"):
                raise Unsupported("'use' with attributes is not supported")
            self.k += 1
            self.accept_op("::")
        if self.at_name("iso_fortran_env", "iso_c_binding", "ieee_arithmetic"):
            self.k += 1
            while self.peek() is not None:               # `only: rk => real64` is another name for a kind
                if self.at_name("only") or self.at_op(",", ":"):
                    self.k += 1
                    continue
                local = self.expect_name()
                if self.accept_op("=>"):
                    remote = self.expect_name()
                    if remote in self.kind_names:
                        self.kind_names[local] = self.kind_names[remote]
            self.note(it.line, f"'{it.text}' ignored (intrinsic module)")
            return []
        name = self.rename(self.expect_name())
        mod = self.modules.get(name)
        if mod is None:
            raise Unsupported(f"module '{name}' is not defined earlier in this file")
        names = None
        exports = self.visible(mod)
        if self.accept_op(","):
            only = self.at_name("only")
            if only:
                self.k += 1
                self.expect_op(":")
                names = []
            while self.peek() is not None:
                local_raw = self.expect_name()
                if self.accept_op("=>"):
                    remote_raw = self.expect_name()
                    self.import_rename(scope, local_raw, remote_raw, names, it.line, exports)
                elif only:
                    names.append(self.export_name(local_raw))
                else:
                    raise Unsupported("'use' with a rename list needs 'only:' or 'local => remote' entries")
                if not self.accept_op(","):
                    break
        self.end_of_statement()
        if names is None:
            scope.skipped |= {n for n in mod.skipped if self.is_public(mod, n)}
        else:
            for n in list(names):
                if n in mod.generics and self.is_public(mod, n):       # a generic: its specifics are what exist
                    names.remove(n)
                    names += [sp for sp in mod.generics[n] if sp not in names]
                    scope.generics[n] = list(mod.generics[n])
                    continue
                if n in exports:
                    continue
                names.remove(n)
                if n in mod.skipped and self.is_public(mod, n):
                    scope.skipped.add(n)
                    self.todo(it.line, f"'{n}' is a variable of module '{name}'; Boreas modules hold "
                                       f"constants and procedures only, so uses of it were not translated")
                else:
                    self.todo(it.line, f"module '{name}' has no public '{n}' that Boreas can import")
        if names is not None and not names:
            return []                                     # everything it named was a kind: nothing to import
        scope.uses.append((name, names, it.line))
        return []

    def import_rename(self, scope, local, remote, names, line, exports):
        """`local => remote`: the file's `local` is the module's `remote`, so uses of `local` are renamed to
        `remote`. A kind constant also gets `local` as a second kind name, and need not be imported."""
        kind = remote in self.kind_names
        if kind:
            self.kind_names[local] = self.kind_names[remote]
        if kind and self.rename(remote) not in exports:
            self.note(line, f"'{local}' is another name for the kind '{remote}', which Boreas writes out")
            return
        scope.import_renames[local] = self.rename(remote)
        if names is not None:
            names.append(self.rename(remote))
        self.note(line, f"'{local}' is imported as '{remote}'; uses of '{local}' were renamed")

    def st_public(self, it, scope):
        self.access_statement(scope, "public")
        return []

    def st_private(self, it, scope):
        self.access_statement(scope, "private")
        return []

    def access_statement(self, scope, word):
        if scope.kind != "module":
            raise Unsupported(f"'{word}' outside a module")
        if self.peek() is None:
            scope.default_public = word == "public"
            return
        self.accept_op("::")
        while self.peek() is not None:
            if self.at_name("operator", "assignment"):
                raise Unsupported(f"'{word}' of an operator or assignment")
            scope.access[self.rename(self.expect_name())] = word
            if not self.accept_op(","):
                break
        self.end_of_statement()

    def is_public(self, mod, name):
        access = mod.access.get(name)
        if access is None:                                # `public :: shape` names a type we call shape_
            access = mod.access.get(self.raw_names.get(name))
        return (access or ("public" if mod.default_public else "private")) == "public"

    def own_exports(self, mod):
        """Public names defined in a module that Boreas can export, in source order."""
        names = [t.name for t in self.types.values() if t.ok and t.scope is mod and t.wrapper is None]
        names += [self.iface_name[t.name] for t in self.types.values()
                  if t.ok and t.scope is mod and t.name in self.iface_name and self.iface_name[t.name] != t.name]
        names += [r.scope.name for r in self.raw if r.scope.module is mod and r.scope.host is mod]
        names += [n for n, info in mod.decls.items() if n not in mod.externals and n not in mod.skipped]
        return [n for n in names if self.is_public(mod, n)]

    def visible(self, mod, seen=()):
        """name -> module that defines it, for everything `use mod` brings in (own public
        names plus public names it imported itself; Fortran re-exports those)."""
        out = {n: mod.name for n in self.own_exports(mod)}
        for src, names, _ in mod.uses:
            other = self.modules.get(src)
            if other is None or other.name in seen or other is mod:
                continue
            for n, origin in self.visible(other, seen + (mod.name,)).items():
                if (names is None or n in names) and self.is_public(mod, n) and n not in out:
                    out[n] = origin
        return out

    def st_save(self, it, scope):
        """`save` alone keeps every local between calls; `save :: a, b` keeps those."""
        names = []
        self.accept_op("::")
        while self.peek() is not None:
            t = self.next()
            if t.kind == "name":
                names.append(self.rename(t.text))
        if scope.kind not in ("program", "module"):
            if names:
                for n in names:
                    scope.saved[n] = (it.line, "named in a save statement")
            else:
                scope.save_all = it.line
        return []

    def st_external(self, it, scope):
        while self.peek() is not None:
            t = self.next()
            if t.kind == "name" and self.rename(t.text) in scope.dummies:     # only a dummy needs a signature
                scope.externals.add(self.rename(t.text))
        self.note(it.line, f"'{it.text}' ignored: Boreas finds procedures by name")
        return []

    def st_intrinsic(self, it, scope):
        while self.peek() is not None:
            t = self.next()
            if t.kind == "name":
                scope.intrinsics.add(t.text)
        self.note(it.line, f"'{it.text}' ignored: Boreas built-ins need no declaration")
        return []

    def st_continue(self, it, scope):
        return []

    def st_common(self, it, scope):
        """`common /block/ a, b(10), c`: shared storage, which becomes a module of variables (merge_commons)."""
        block = ""
        if self.accept_op("//"):
            pass
        while self.peek() is not None:
            if self.accept_op("/"):
                block = "" if self.accept_op("/") else self.rename(self.expect_name())
                if block:
                    self.expect_op("/")
            name = self.rename(self.expect_name())
            info = scope.decls.get(name)
            if info is None:
                info = scope.decls[name] = Info(implicit_type(name), None, False, None, None, False, it.line)
            if self.accept_op("("):
                specs = self.dim_specs()
                if not all(isinstance(sp, tuple) for sp in specs):
                    raise Unsupported("common variable with a deferred or assumed shape")
                info.dims = [hi for _, hi in specs]
            scope.commons.setdefault(block, []).append((name, it.line))
            self.accept_op(",")
        return []

    def st_equivalence(self, it, scope):
        """`equivalence (a, b), (v(2), x)`: names for the same storage. Recorded here and rewritten
        in finalize (apply_equivalences) once every declaration is known."""
        while True:
            self.expect_op("(")
            group = []
            while True:
                name = self.rename(self.expect_name())
                element = None
                if self.accept_op("("):
                    element = self.constant_int(self.expr())
                    if element is None or not self.accept_op(")"):
                        raise Unsupported("equivalence with a subscript that is not a literal number")
                if name not in scope.decls:
                    scope.decls[name] = Info(implicit_type(name), None, False, None, None, False, it.line)
                group.append((name, element))
                if not self.accept_op(","):
                    break
            self.expect_op(")")
            scope.equivs.append((group, it.line))
            if not self.accept_op(","):
                break
        self.end_of_statement()
        return []

    def st_go(self, it, scope):
        if not self.at_name("to"):
            raise Unsupported("unrecognised statement")
        self.k += 1
        return self.st_goto(it, scope)

    def st_goto(self, it, scope):
        """`goto 10`, and the computed `goto (10, 20, 30), i`."""
        if self.peek() is not None and self.peek().kind == "int":
            label = self.next().value
            self.end_of_statement()
            return [Goto(label, it.line)]
        if not self.accept_op("("):
            raise Unsupported("assigned goto")
        labels = []
        while True:
            if self.peek() is None or self.peek().kind != "int":
                raise Unsupported("unrecognised goto")
            labels.append(self.next().value)
            if not self.accept_op(","):
                break
        self.expect_op(")")
        self.accept_op(",")
        index = self.expr()
        self.end_of_statement()
        cases = [Case([Num(n)], [Goto(label, it.line)], it.line) for n, label in enumerate(labels, 1)]
        return [Select(index, cases, it.line)]

    def st_parameter(self, it, scope):
        """The F77 `parameter (n = 3, m = 2 * n)` statement: names declared earlier become constants."""
        self.expect_op("(")
        while True:
            name = self.expect_name()
            self.expect_op("=")
            value = self.expr()
            info = scope.decls.get(name)
            if info is None:
                info = scope.decls[name] = Info(implicit_type(name), None, False, None, None, False, it.line)
            info.parameter, info.init = True, value
            if info.type == "string" and info.length is None and isinstance(value, Str):
                info.length = len(value.value)
            if not self.accept_op(","):
                break
        self.expect_op(")")
        self.end_of_statement()
        return []

    def st_contains(self, it, scope):
        raise Unsupported("unexpected 'contains'")

    # control flow
    def st_if(self, it, scope):
        self.expect_op("(")
        cond = self.expr()
        self.expect_op(")")
        if self.peek() is not None and self.peek().kind == "int":          # the arithmetic if
            labels = []
            while True:
                labels.append(self.next().value)
                if not self.accept_op(","):
                    break
            self.end_of_statement()
            if len(labels) != 3:
                raise Unsupported("arithmetic if needs three labels")
            below, zero, above = (Goto(n, it.line) for n in labels)
            return [If([(Binary("<", cond, Num(0)), [below]), (Binary("==", cond, Num(0)), [zero])], [above])]
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
        cname, self.construct = self.construct, None
        if cname is None:
            return self.do_loop(it, scope, None)
        self.loops.append((cname, self.rename(cname)))
        try:
            return self.do_loop(it, scope, self.loops[-1][1])
        finally:
            self.loops.pop()

    def do_loop(self, it, scope, label):
        if self.at_name("concurrent"):
            return self.do_concurrent(it, scope, label)
        end_label = None
        if self.peek() is not None and self.peek().kind == "int":
            end_label = self.next().value
            self.accept_op(",")
        if self.at_name("while"):
            self.k += 1
            self.expect_op("(")
            cond = self.expr()
            self.expect_op(")")
            self.end_of_statement()
            return [DoWhile(cond, self.loop_body(scope, end_label), label, it.line)]
        if self.peek() is None:
            return [DoWhile(Bool(True), self.loop_body(scope, end_label), label, it.line)]
        var = self.rename(self.expect_name())
        self.expect_op("=")
        lo = self.expr()
        self.expect_op(",")
        hi = self.expr()
        step = self.expr() if self.accept_op(",") else None
        self.end_of_statement()
        return [Do(var, lo, hi, step, self.loop_body(scope, end_label), it.line, label)]

    def do_concurrent(self, it, scope, label):
        """`do concurrent (i = 1:n, j = 1:m, mask)` runs in any order, so it becomes nested `do` loops
        (the first index outermost) with the mask as an `if`."""
        self.k += 1
        self.expect_op("(")
        if self.peek() is not None and self.peek().text in TYPE_WORDS:
            while not self.accept_op("::"):
                self.k += 1
        controls, mask = [], None
        while True:
            t, t2 = self.peek(), self.peek(1)
            if t is not None and t.kind == "name" and t2 is not None and t2.kind == "op" and t2.text == "=":
                var = self.rename(self.expect_name())
                self.expect_op("=")
                lo = self.expr()
                self.expect_op(":")
                hi = self.expr()
                step = self.expr() if self.accept_op(":") else None
                controls.append((var, lo, hi, step))
            else:
                mask = self.expr()
            if not self.accept_op(","):
                break
        self.expect_op(")")
        if self.peek() is not None:                      # local(...), shared(...), reduce(...) and so on
            self.skip_block("do")
            raise Unsupported(f"'do concurrent' with a '{self.peek().text}' clause is not supported; skipped")
        self.end_of_statement()
        body = self.block_until(scope, "do")
        if mask is not None:
            body = [If([(mask, body)], [])]
        for n, (var, lo, hi, step) in enumerate(reversed(controls)):
            body = [Do(var, lo, hi, step, body, it.line, label if n == 0 else None)]
        return [Comment("was do concurrent: the iterations are independent")] + body

    def loop_body(self, scope, end_label):
        """The body of a do loop: up to `end do`, or through the statement labelled `end_label`."""
        if end_label is None:
            return self.block_until(scope, "do")
        self.do_labels.append(end_label)
        try:
            return self.parse_body(scope, set(), until=end_label)
        finally:
            self.do_labels.pop()

    def block_until(self, scope, kind):
        body = self.parse_body(scope, {("end", kind)})
        self.i += 1
        return body

    def loop_target(self, word):
        """The Boreas label for the construct name after `exit` / `cycle`, or None when there is none."""
        if self.peek() is None:
            return None
        name = self.expect_name()
        self.end_of_statement()
        for fname, label in reversed(self.loops):
            if fname == name:
                return label
        raise Unsupported(f"'{word} {name}': '{name}' is not an enclosing do loop")

    def st_exit(self, it, scope):
        return [Exit(self.loop_target("exit"), it.line)]

    def st_cycle(self, it, scope):
        return [Cycle(self.loop_target("cycle"), it.line)]

    def st_return(self, it, scope):
        if self.peek() is not None:
            raise Unsupported("alternate return")
        return [Return(None)]

    def st_stop(self, it, scope):
        """`stop`, `stop 3`; a message (`stop 'bye'`) goes to stderr in Fortran, so it is dropped with a note."""
        code = None
        if self.peek() is not None:
            t = self.peek()
            if t.kind == "str":
                self.k += 1
                self.note(it.line, f"stop message {t.text} dropped (Fortran prints it on stderr)")
            else:
                code = self.expr()
        self.end_of_statement()
        return [Stop(code, it.line)]

    def st_error(self, it, scope):
        if self.peek() is None or self.peek().text != "stop":
            raise Unsupported("unrecognised statement")
        self.k += 1
        stop = self.st_stop(it, scope)[0]
        if stop.code is None:
            stop.code = Num(1)                       # `error stop` exits with a non-zero code
        return [stop]

    def st_read(self, it, scope):
        """List-directed input from the keyboard: `read(*,*) a, b`, `read(*,*,iostat=ios) a`, `read *, a`."""
        status, whole_line, source = None, False, None
        if self.accept_op("("):
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
            source = self.unit_target(parts[0], scope, "read", terminal=5)
            whole_line = len(parts) > 1 and len(parts[1]) == 1 and parts[1][0].kind == "str" \
                and parts[1][0].value.replace(" ", "").lower() == "(a)"
            if len(parts) < 2 or not (whole_line or (len(parts[1]) == 1 and parts[1][0].text == "*")):
                raise Unsupported("formatted read")
            for opt in parts[2:]:
                if len(opt) == 3 and opt[0].text == "iostat" and opt[1].text == "=" and opt[2].kind == "name":
                    status = Var(self.rename(opt[2].text))
                else:
                    raise Unsupported("unsupported read options")
        elif self.at_op("*"):
            self.k += 1
            if self.peek() is not None:
                self.expect_op(",")
        else:
            raise Unsupported("formatted read")
        targets = []
        if self.peek() is not None:
            targets.append(self.expr())
            while self.accept_op(","):
                targets.append(self.expr())
        self.end_of_statement()
        if not targets or not all(isinstance(t, (Var, Apply, FieldAccess)) for t in targets):
            raise Unsupported("read of something other than variables")
        if whole_line:
            return self.read_whole_line(it, scope, targets, status, source)
        return self.read_values(it, scope, targets, status, source)

    READ_CONVERSIONS = {"integer": "to_integer", "real": "to_real", "real32": "to_real", "logical": "to_logical", "string": None}

    def read_target_type(self, scope, target):
        """The Boreas type of a scalar read target, or raise Unsupported for the ones the converter does not map."""
        if isinstance(target, FieldAccess):
            raise Unsupported("read into a record field")
        info = scope.decls.get(target.name)
        if info is None:
            raise Unsupported(f"read into '{target.name}', whose type is not declared")
        is_array = info.dims is not None or info.shape_dropped
        if isinstance(target, Var) and is_array or isinstance(target, Apply) and (
                not is_array or any(isinstance(a, Slice) for a in target.args)):
            raise Unsupported(f"read into the array or section '{target.name}'")
        if info.type not in self.READ_CONVERSIONS:
            raise Unsupported(f"read into a {info.type}")
        return info.type

    def read_parts_name(self, scope, line):
        """The shared temporary that holds the pieces of a read line (made once per procedure)."""
        if getattr(scope, "read_parts", None) is None:
            scope.read_parts = self.unique("read_parts", scope, [])
            scope.decls[scope.read_parts] = Info("string", [Slice(None, None)], False, None, None, False, line, True)
        return scope.read_parts

    def read_piece(self, parts, n, typ, line):
        piece = Apply(parts, [Num(n)], line)
        conv = self.READ_CONVERSIONS[typ]
        if conv is None:
            return piece
        value = Apply(conv, [piece], line)
        return Apply("real32", [value], line) if typ == "real32" else value

    def read_values(self, it, scope, targets, status, source):
        """`read(u,*) a, b`: one line of input, cut into pieces at blanks and commas, one piece per target. Without
        `iostat` every value must be on the line, and a missing or bad one stops the program (as Fortran does).
        With `iostat` a ReadMarker is left for `resolve_reads`."""
        types = [self.read_target_type(scope, t) for t in targets]
        src = [] if source is None else [source]
        line = it.line
        if not getattr(scope, "read_noted", False):
            scope.read_noted = True
            self.note(line, "read: the values are taken from one line, separated by blanks or commas "
                            "(Fortran also continues on the next line and accepts quoted text and repeat counts)")
        fit = lambda t: [Assign(t, self.fit_to_length(t, self.char_info(t).length), line)] if self.char_info(t) else []
        if status is not None:
            return [ReadMarker(targets, types, status, src, [fit(t) for t in targets], line)]
        parts = self.read_parts_name(scope, line)
        out = [Assign(Var(parts), Apply("fields", [Binary("??", Apply("read_line", src, line), Str("")), Str(",")], line), line)]
        for n, (t, typ) in enumerate(zip(targets, types), 1):
            out.append(Assign(t, self.read_piece(parts, n, typ, line), line))
            out.extend(fit(t))
        return out

    # `iostat` reads. The status is 0 (good), -1 (no line) or 1 (a bad or missing value); nothing is stored unless every
    # value is good. When `ios` is only ever compared with -1, 0 or 1, it is dropped and each comparison becomes a test
    # of the optional values, as hand-written Boreas would do; otherwise the status variable is kept.
    COMPARE = {"==": lambda a, b: a == b, "/=": lambda a, b: a != b, "<": lambda a, b: a < b,
               "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
    FLIP = {"==": "==", "/=": "/=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}

    @staticmethod
    def int_literal(e):
        if isinstance(e, Num) and isinstance(e.value, int) and not isinstance(e.value, bool):
            return e.value
        if isinstance(e, Unary) and e.op == "-" and isinstance(e.operand, Num) and isinstance(e.operand.value, int):
            return -e.operand.value
        return None

    def status_comparison(self, node, name):
        """(op, value) when `node` compares the variable `name` with -1, 0 or 1, else None."""
        if not (isinstance(node, Binary) and node.op in self.COMPARE):
            return None
        for var, lit, op in ((node.left, node.right, node.op), (node.right, node.left, self.FLIP[node.op])):
            if isinstance(var, Var) and var.name == name and self.int_literal(lit) in (-1, 0, 1):
                return op, self.int_literal(lit)
        return None

    def can_drop_status(self, r, name, markers):
        scope = r.scope
        info = scope.decls.get(name)
        if info is None or info.type != "integer" or info.dims is not None or info.shape_dropped or info.parameter \
                or info.allocatable or name in scope.dummies or name in scope.saved:
            return False
        if any(name == n for group, _ in scope.equivs for n, _ in group) \
                or any(name == c for entries in scope.commons.values() for c, _ in entries):
            return False
        if any(other.scope.host is scope for other in self.raw):         # an internal procedure may use it too
            return False
        if any(m.types != markers[0].types for m in markers):
            return False
        uses = sum(isinstance(n, Var) and n.name == name for n in iter_nodes(r.body))
        compared = sum(self.status_comparison(n, name) is not None for n in iter_nodes(r.body))
        return uses == len(markers) + compared

    def read_temps(self, scope, body, name, types, line, cache):
        """The line temporary and one optional value temporary per target for the status `name`; the same names are
        used again by every read of that status."""
        if (name, "line") not in cache:
            cache[name, "line"] = self.unique(f"{name}_line", scope, body)
            scope.decls[cache[name, "line"]] = Info("string?", None, False, None, None, False, line)
        values, seen = [], {}
        for typ in types:
            temp = "real" if typ == "real32" else typ
            seen[temp] = seen.get(temp, 0) + 1
            key = (name, temp, seen[temp])
            if key not in cache:
                cache[key] = self.unique(f"{name}_{temp}" + (f"_{seen[temp]}" if seen[temp] > 1 else ""), scope, body)
                scope.decls[cache[key]] = Info(temp + "?", None, False, None, None, False, line)
            values.append(cache[key])
        return cache[name, "line"], values

    def expand_read(self, m, text, values, keep_status):
        line = m.line
        parts = lambda k: Apply("field", [Var(text), Num(k), Str(",")], line)
        out = [Assign(Var(text), Apply("read_line", m.source, line), line)]
        for k, (v, typ) in enumerate(zip(values, m.types), 1):
            conv = self.READ_CONVERSIONS[typ]
            out.append(Assign(Var(v), parts(k) if conv is None else Apply(conv, [parts(k)], line), line))
        store = []
        for t, v, typ, fits in zip(m.targets, values, m.types, m.fits):
            value = Apply("real32", [Var(v)], line) if typ == "real32" else Var(v)
            store += [Assign(t, value, line)] + fits
        good = self.all_present(values, line)
        if not keep_status:
            return out + [If([(good, store)], [])]
        return out + [If([(Unary("not", Apply("present", [Var(text)], line)), [Assign(m.status, Num(-1), line)]),
                          (good, store + [Assign(m.status, Num(0), line)])], [Assign(m.status, Num(1), line)])]

    @staticmethod
    def all_present(values, line):
        good = Apply("present", [Var(values[0])], line)
        for v in values[1:]:
            good = Binary("and", good, Apply("present", [Var(v)], line))
        return good

    def status_test(self, op, k, text, values, line):
        """The condition that says the same as `ios <op> k` for ios in {-1, 0, 1}, as a test of the optionals."""
        holds = frozenset(v for v in (-1, 0, 1) if self.COMPARE[op](v, k))
        have, good = Apply("present", [Var(text)], line), self.all_present(values, line)
        table = {frozenset(): Bool(False), frozenset({-1, 0, 1}): Bool(True), frozenset({0}): good,
                 frozenset({-1}): Unary("not", have), frozenset({0, 1}): have,
                 frozenset({1}): Binary("and", have, Unary("not", good)),
                 frozenset({-1, 1}): Unary("not", good), frozenset({-1, 0}): Binary("or", Unary("not", have), good)}
        return table[holds]

    def resolve_reads(self, r):
        markers = [n for n in iter_nodes(r.body) if isinstance(n, ReadMarker)]
        if not markers:
            return
        by_name = {}
        for m in markers:
            by_name.setdefault(m.status.name, []).append(m)
        cache, drop = {}, {}
        for name, ms in by_name.items():
            drop[name] = self.can_drop_status(r, name, ms)
            if drop[name]:
                r.scope.decls.pop(name)
                text, values = self.read_temps(r.scope, r.body, name, ms[0].types, ms[0].line, cache)
                replace_nodes(r.body, lambda node, name=name, text=text, values=values, line=ms[0].line:
                              None if self.status_comparison(node, name) is None
                              else self.status_test(*self.status_comparison(node, name), text, values, line))

        def expand(stmts):
            out = []
            for s in stmts:
                if isinstance(s, list):
                    out.extend(expand(s))
                elif isinstance(s, ReadMarker):
                    text, values = self.read_temps(r.scope, r.body, s.status.name, s.types, s.line, cache)
                    out.extend(self.expand_read(s, text, values, not drop[s.status.name]))
                else:
                    for sub in child_lists(s):
                        sub[:] = expand(sub)
                    out.append(s)
            return out
        r.body[:] = expand(r.body)

    def read_whole_line(self, it, scope, targets, status, source=None):
        """`read (*, '(A)', iostat=ios) text`: the next line of input goes into one character variable."""
        target = targets[0]
        declared = scope.decls.get(target.name) if len(targets) == 1 and isinstance(target, (Var, Apply)) else None
        if declared is None or declared.type != "string":
            raise Unsupported("a '(A)' read of anything but one character variable")
        info = self.char_info(target)
        fit = (lambda e: self.fit_to_length(e, info.length)) if info is not None else (lambda e: e)
        if status is None:
            return [Assign(target, fit(Binary("??", Apply("read_line", [] if source is None else [source], it.line), Str(""))), it.line)]
        line = self.unique("line_read", scope, [])
        scope.decls[line] = Info("string?", None, False, None, None, False, it.line)
        return [Assign(Var(line), Apply("read_line", [] if source is None else [source], it.line), it.line),
                If([(Apply("present", [Var(line)], it.line),
                     [Assign(target, fit(Var(line)), it.line), Assign(status, Num(0), it.line)])],
                   [Assign(status, Num(-1), it.line)])]

    def st_data(self, it, scope):
        """`data a, b /1, 2/, c(1:3) /3*0/, (m(1, j), j = 1, 4) /4*7/`: initial values, which become
        declaration initialisers. The values are kept per element until an array is completely filled."""
        while True:
            cells = []
            while True:
                cells.extend(self.data_object(scope, {}))
                if not self.accept_op(","):
                    break
            self.expect_op("/")
            values = []
            while not self.at_op("/"):
                values.extend(self.data_value())
                if not self.accept_op(","):
                    break
            self.expect_op("/")
            if len(values) < len(cells):
                raise Unsupported("data has too few values")
            if len(values) > len(cells):
                raise Unsupported("data has more values than objects")
            touched = []
            for (name, index), value in zip(cells, values):
                info = scope.decls[name]
                if info.type == "string" and info.length is not None and isinstance(value, Str):
                    value = self.fit_to_length(value, info.length)
                scope.data_cells.setdefault(name, {})[index] = value
                if name not in touched:
                    touched.append(name)
            for name in touched:
                self.data_complete(scope, name, it.line)
            if not self.accept_op(","):
                break
        self.end_of_statement()
        return []

    def data_object(self, scope, env):
        """The elements one data object names, as [(variable, index tuple)] in the order they get values.
        `env` holds the variables of the implied-do loops around it."""
        if self.at_op("("):                                  # (object, object, i = lo, hi [, step])
            self.k += 1
            start = self.k
            depth = 0
            while True:                                      # find the loop control: `name = ...`
                t, nxt = self.peek(), self.peek(1)
                if t is None:
                    raise Unsupported("malformed implied-do loop in data")
                if depth == 0 and t.kind == "name" and nxt is not None and nxt.text == "=":
                    break
                depth += (t.text == "(") - (t.text == ")")
                self.k += 1
            body_end = self.k
            var = self.rename(self.next().text)
            self.expect_op("=")
            lo, hi = self.data_int(self.expr(), scope, env), None
            self.expect_op(",")
            hi = self.data_int(self.expr(), scope, env)
            step = self.data_int(self.expr(), scope, env) if self.accept_op(",") else 1
            self.expect_op(")")
            if not step:
                raise Unsupported("data implied-do loop with a zero step")
            after = self.k
            cells = []
            for value in range(lo, hi + (1 if step > 0 else -1), step):
                self.k = start
                inner = dict(env, **{var: value})
                while self.k < body_end:
                    cells.extend(self.data_object(scope, inner))
                    self.accept_op(",")
            self.k = after
            return cells
        t = self.next()
        if t.kind != "name":
            raise Unsupported("malformed data statement")
        name = self.rename(t.text)
        info = scope.decls.get(name)
        if info is None or name in scope.dummies:
            raise Unsupported(f"data for '{name}', which is not a declared local variable")
        sizes = [1] if info.dims is None else self.data_sizes(info, scope)
        if None in sizes:
            raise Unsupported(f"data for '{name}', whose size is not a constant")
        if info.dims is None:
            if self.at_op("("):
                raise Unsupported(f"data for an element of '{name}', which is not an array")
            return [(name, ())]
        ranges = [range(1, n + 1) for n in sizes]
        if self.accept_op("("):
            ranges = []
            while True:
                lo = None if self.at_op(":") else self.expr()
                if self.accept_op(":"):
                    hi = None if self.at_op(",", ")") else self.expr()
                    if self.at_op(":"):
                        raise Unsupported("data with a strided section")
                    n = sizes[len(ranges)] if len(ranges) < len(sizes) else 0
                    first = 1 if lo is None else self.data_int(lo, scope, env)
                    last = n if hi is None else self.data_int(hi, scope, env)
                    ranges.append(range(first, last + 1))
                else:
                    ranges.append([self.data_int(lo, scope, env)])
                if not self.accept_op(","):
                    break
            self.expect_op(")")
            if len(ranges) != len(sizes):
                raise Unsupported(f"data for '{name}' with the wrong number of subscripts")
        for r, n in zip(ranges, sizes):
            if any(not 1 <= i <= n for i in r):
                raise Unsupported(f"data for '{name}' outside its bounds")
        cells = [()]                                         # column-major: the first subscript varies fastest
        for r in ranges:
            cells = [c + (i,) for i in r for c in cells]
        return [(name, c) for c in cells]

    def data_sizes(self, info, scope):
        """The extents of a declared array when they are constants (literals or parameters), else [None]."""
        try:
            return [self.data_int(d, scope, {}) for d in info.dims]
        except Unsupported:
            return [None]

    def data_int(self, e, scope, env):
        """The value of a constant integer expression in a data statement (loop variables allowed)."""
        if isinstance(e, Num) and isinstance(e.value, int) and not isinstance(e.value, bool):
            return e.value
        if isinstance(e, Var):
            if e.name in env:
                return env[e.name]
            info = scope.decls.get(e.name)
            if info is not None and info.parameter and info.init is not None:
                return self.data_int(info.init, scope, {})
        if isinstance(e, Unary) and e.op == "-":
            return -self.data_int(e.operand, scope, env)
        if isinstance(e, Binary) and e.op in ("+", "-", "*"):
            a, b = self.data_int(e.left, scope, env), self.data_int(e.right, scope, env)
            return a + b if e.op == "+" else a - b if e.op == "-" else a * b
        raise Unsupported("data subscript or loop bound that is not a constant")

    def data_complete(self, scope, name, line):
        """Once every element of `name` has a value, they become its initial value."""
        info, cells = scope.decls[name], scope.data_cells[name]
        sizes = [] if info.dims is None else self.data_sizes(info, scope)
        count = 1
        for n in sizes:
            count *= n
        if len(cells) < count:
            return
        order = [()]
        for n in sizes:
            order = [c + (i,) for i in range(1, n + 1) for c in order]
        mine = [cells[c] for c in order]
        if info.dims is None:
            info.init = mine[0]
        else:
            items = ArrayLit(mine)
            info.init = items if len(sizes) == 1 else Apply("reshape", [items, ArrayLit(
                [Num(n) for n in sizes])], self.line)
        if scope.kind not in ("program", "module", "type", "blockdata"):
            scope.saved[name] = (line, "initialised by a data statement, which implies save")

    def check_import_renames(self, scope, line):
        for local, remote in scope.import_renames.items():
            if remote in scope.decls:
                msg = f"'{local}' is imported as '{remote}', but '{remote}' is also declared here"
                scope.comments.append("TODO(convert): " + msg)
                self.todo(line, msg)

    def check_data_cells(self, scope, line):
        for name, cells in scope.data_cells.items():
            if scope.decls[name].init is None:
                msg = f"data fills only {len(cells)} elements of '{name}'; the rest have no value here"
                scope.comments.append("TODO(convert): " + msg)
                self.todo(line, msg)

    def data_value(self):
        """One value of a data list: a constant, or `n*constant` (n copies)."""
        repeat = 1
        t, nxt = self.peek(), self.peek(1)
        if t is not None and t.kind == "int" and nxt is not None and nxt.text == "*":
            repeat = t.value
            self.k += 2
        sign = self.next().text if self.at_op("+", "-") else None
        value = self.primary()
        if sign == "-":
            value = Unary("-", value)
        return [value] * repeat

    def has_component_call(self):
        """Does this `call` name a component (`call s%grow(2.0)`, `call all(i)%s%describe()`)?"""
        depth = 0
        for t in self.toks[self.k:]:
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
            elif t.kind == "op" and t.text == "%" and depth == 0:
                return True
        return False

    def method_call(self, it):
        """`call x%f(args)`: the type-bound procedure `f` of the record `x`."""
        e = self.primary()
        if not (isinstance(e, FieldAccess) and e.args is not None):
            raise Unsupported("call of a component that is not a procedure")
        if not e.call:
            raise Unsupported(f"'{e.field}' is not a type-bound procedure of a type this conversion knows")
        self.end_of_statement()
        return [Call(e.field, e.args, it.line, 0, e.base)]

    def st_call(self, it, scope):
        if self.has_component_call():
            return self.method_call(it)
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
        made = self.intrinsic_call(name, args, it.line, scope)
        if made is not None:
            return made
        call = Call(name, args, it.line)
        self.note_generic_call(call, name, args, it.line)
        return [call]

    def intrinsic_call(self, name, args, line, scope):
        """Intrinsic subroutines become something else in Boreas: assignments from a function, or nothing."""
        if name == "random_number" and len(args) == 1 and isinstance(args[0], Var):
            info = scope.decls.get(args[0].name)
            if info is not None and info.dims is None:
                return [Assign(args[0], Apply("random", [], line), line)]
            raise Unsupported("random_number() of an array")
        if name in ("random_init", "random_seed"):
            self.note(line, f"{name}() dropped: Boreas seeds its random numbers itself, so random output differs",
                      once=True)
            return []
        if name == "get_command_argument" and len(args) == 2:
            return [Assign(args[1], Apply("argument", [args[0]], line), line)]
        if name == "get_command_argument":
            raise Unsupported("get_command_argument() with a length or status")
        return None

    def allocation_objects(self, scope, what):
        """The `(a(n), b(m, k), stat=...)` part of allocate / deallocate: [(name, extents)]."""
        self.expect_op("(")
        objects = []
        while True:
            t, t2 = self.peek(), self.peek(1)
            if t is not None and t2 is not None and t.kind == "name" and t2.kind == "op" and t2.text == "=":
                raise Unsupported(f"'{what}' with {t.text}= is not supported")
            name = self.rename(self.expect_name())
            if self.at_op("%"):
                raise Unsupported(f"'{what}' of a component ({name}%...): Boreas cannot unassign or resize one; "
                                  f"a class(T) component is set by assigning to it")
            if self.at_op("::"):
                raise Unsupported(f"'{what}' with a type-spec is not supported")
            info = scope.decls.get(name) if name not in scope.dummies else None
            if info is None and name not in scope.dummies:
                info = self.shared_info(scope, name)           # an allocatable module variable
            if info is None or not info.allocatable:
                raise Unsupported(f"'{what}' of '{name}', which is not an allocatable array "
                                  f"that Boreas can allocate")
            dims = []
            if self.at_op("("):
                self.k += 1
                while True:
                    a = self.argument(what)
                    if isinstance(a, Slice):
                        if not (isinstance(a.lo, Num) and a.lo.value == 1 and a.hi is not None):
                            raise Unsupported("allocate with a lower bound other than 1")
                        a = a.hi
                    dims.append(a)
                    if not self.accept_op(","):
                        break
                self.expect_op(")")
            objects.append((name, dims))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        self.end_of_statement()
        return objects

    def shared_info(self, scope, name):
        """The declaration of a module variable that `scope` can see: its own module's, or an imported one."""
        s = scope
        while s is not None:
            owners = [(s.module, None)] if s.module is not None else []
            owners += [(self.modules.get(modname), names) for modname, names, _ in s.uses + (s.module.uses if s.module else [])]
            if s.kind == "module":
                owners.append((s, None))
            for other, names in owners:
                info = other.decls.get(name) if other is not None and (names is None or name in names) else None
                if info is not None and not info.parameter:
                    return info
            s = s.host
        return None

    def is_polymorphic_allocate(self):
        """`allocate(s, source=...)` / `allocate(T :: s)`: the allocation of a class(T) value."""
        depth = 0
        for j in range(self.k, len(self.toks)):
            t = self.toks[j]
            if t.kind == "op" and t.text == "(":
                depth += 1
            elif t.kind == "op" and t.text == ")":
                depth -= 1
            elif depth == 1 and t.kind == "op" and t.text == "::":
                return True
            elif depth == 1 and t.kind == "name" and t.text in ("source", "mold") and \
                    j + 1 < len(self.toks) and self.toks[j + 1].text == "=":
                return True
        return False

    def polymorphic_allocate(self, it):
        """`allocate(all(1)%s, source=circle(...))` becomes `all[1] = circle(...)`, and `allocate(circle :: s)`
        becomes `s = circle()` (a record built from its defaults)."""
        self.expect_op("(")
        type_spec = None
        if self.peek() is not None and self.peek(1) is not None and self.peek(1).text == "::":
            type_spec = self.type_name(self.expect_name())
            self.expect_op("::")
        target = self.primary()
        if not isinstance(target, (Var, Apply, FieldAccess)) or not self.is_poly(target):
            raise Unsupported("'allocate' of something that is not a class(T) value")
        value = None
        while self.accept_op(","):
            key = self.expect_name()
            self.expect_op("=")
            if key != "source":
                raise Unsupported(f"'allocate' with {key}= is not supported")
            value = self.expr()
        self.expect_op(")")
        self.end_of_statement()
        if value is None:
            if type_spec is None:
                raise Unsupported("'allocate' of a class(T) value needs source= or a type")
            tinfo = self.types.get(type_spec)
            if tinfo is None or not tinfo.ok or tinfo.abstract:
                raise Unsupported(f"'allocate({type_spec} :: ...)': not a record this conversion knows")
            value = Apply(type_spec, [], it.line)
        return [Assign(target, value, it.line)]

    def st_allocate(self, it, scope):
        if self.is_polymorphic_allocate():
            return self.polymorphic_allocate(it)
        out = []
        for name, dims in self.allocation_objects(scope, "allocate"):
            info = scope.decls.get(name) or self.shared_info(scope, name)
            if len(dims) != len(info.dims):
                raise Unsupported(f"allocate of '{name}' needs {len(info.dims)} extent(s)")
            out.append(Allocate(name, dims, it.line))
        return out

    def st_deallocate(self, it, scope):
        out = []
        for name, dims in self.allocation_objects(scope, "deallocate"):
            if dims:
                raise Unsupported("deallocate takes whole arrays")
            out.append(Deallocate(name, it.line))
        return out

    def st_select(self, it, scope):
        resume = self.i
        try:
            return self.select_case(scope)
        except Unsupported:                  # leave this construct as a TODO; the rest still converts
            self.i = resume
            self.skip_block("select")
            raise

    def select_type(self, scope):
        """`select type (p => x)` ... `type is (T)` ... `class default` ... `end select`. The associate name is a
        copy of the value as that record in Boreas, so a case that changes it writes it back."""
        import copy
        self.k += 1                                           # `type`
        self.expect_op("(")
        assoc = None
        if self.peek() is not None and self.peek(1) is not None and self.peek().kind == "name" and self.peek(1).text == "=>":
            assoc = self.rename(self.expect_name())
            self.k += 1
        subject = self.expr()
        self.expect_op(")")
        self.end_of_statement()
        line = self.line
        if not isinstance(subject, (Var, Apply, FieldAccess)):
            raise Unsupported("select type of something that is not a variable")
        stops = {("end", "select"), ("guard",)}
        lead = self.parse_body(scope, stops)
        if any(not isinstance(n, Comment) for n in lead):
            raise Unsupported("statements before the first 'type is'")
        bound = assoc or (subject.name if isinstance(subject, Var) else None)
        base = self.lookup_decl(self.root_name_of(subject)) if not isinstance(subject, FieldAccess) else None
        cases = []
        while True:
            cur = self.items[self.i]
            self.i += 1
            toks = self.tokens(cur)
            if classify(toks) != ("guard",):
                break                                         # `end select`
            self.toks, self.k, self.line = toks, 0, cur.line
            word = self.expect_name()
            col = 0
            if word == "class" and self.at_name("default"):
                self.k += 1
                name = None
            elif word == "type" and self.at_name("is"):
                self.k += 1
                self.expect_op("(")
                if self.peek() is not None and self.peek().text in TYPE_WORDS:
                    raise Unsupported(f"'type is ({self.peek().text})': only derived types are supported")
                name = self.type_name(self.expect_name())
                self.expect_op(")")
                info = self.types.get(name)
                if info is None or not info.ok or info.abstract or info.wrapper is not None:
                    raise Unsupported(f"'type is ({name})': not a record this conversion knows")
            elif word == "class" and self.at_name("is"):
                self.k += 1
                self.expect_op("(")
                if self.peek() is not None and self.peek().text in TYPE_WORDS:
                    raise Unsupported(f"'class is ({self.peek().text})': only derived types are supported")
                top = self.type_name(self.expect_name())
                self.expect_op(")")
                info = self.types.get(top)
                if info is None or not info.ok or info.wrapper is not None:
                    raise Unsupported(f"'class is ({top})': not a record this conversion knows")
                name = ("class", top, False)                  # settled by guard_cases once every type is known
            else:
                raise Unsupported("this guard of 'select type' is not supported")
            shadowed = scope.decls.get(bound) if bound is not None else None
            if bound is not None:                             # inside the case the name has the narrowed type
                if name is not None:
                    scope.decls[bound] = Info(name[1] if isinstance(name, tuple) else name, None, False, None, None, False, cur.line)
                elif base is not None:
                    scope.decls[bound] = replace(base, dims=None, shape_dropped=False, rank=0, intent=None,
                                                 allocatable=False, init=None, parameter=False)
            try:
                body = self.parse_body(scope, stops)
            finally:
                if bound is not None:
                    if shadowed is not None:
                        scope.decls[bound] = shadowed
                    else:
                        scope.decls.pop(bound, None)
            cases.append(Case(name, body, cur.line, col))
        if not cases:
            raise Unsupported("'select type' without any case")
        if sum(c.values is None for c in cases) > 1 or len({c.values for c in cases}) < len(cases):
            raise Unsupported("a type or the default appears twice")
        if assoc is None:                                     # select type (x): x names the narrowed value inside
            if not isinstance(subject, Var):
                raise Unsupported("select type of an expression needs an associate name: select type (p => expr)")
            assoc = subject.name + "_as"
            while assoc in scope.decls or assoc in scope.dummies:
                assoc += "_"
            for c in cases:
                for n in iter_nodes(c.body):
                    if isinstance(n, (Var, Apply, Call, Allocate, Deallocate)) and n.name == subject.name:
                        n.name = assoc
        read_only = base is not None and (base.intent == "in" or base.parameter)     # Fortran cannot change it either
        for c in cases:                                       # the copy goes back if the case changed it
            if isinstance(c.values, tuple):                   # class is (T): can the body do with just T's functions?
                c.values = ("class", c.values[1], self.only_calls_methods(c.body, assoc))
            if c.values is not None and not read_only and self.changes(c.body, assoc):
                if not isinstance(subject, (Var, Apply)):
                    raise Unsupported(f"the guard for '{self.guard_name(c)}' changes '{assoc}', which is not a variable to write back to")
                self.write_back_before_exits(c.body, lambda: Assign(copy.deepcopy(subject), Var(assoc), c.line))
                c.body.append(Assign(copy.deepcopy(subject), Var(assoc), c.line))
        if None not in {c.values for c in cases}:
            if len(cases) == 1 and isinstance(cases[0].values, str):      # one `type is` and nothing else: an inline test
                only = cases[0]
                return lead + [If([(IsType(subject, only.values, assoc, line), only.body)], [])]
            cases.append(Case(None, [Comment("no other type is handled here (Fortran does nothing for them either)")], line))
        return lead + [SelectType(subject, cases, line, False, assoc)]

    @staticmethod
    def write_back_before_exits(node, make):
        """A case that leaves early (exit, cycle, return) would skip the write-back at its end, so the write-back
        goes in front of each of them too. Statements are changed in place."""
        if isinstance(node, list):
            out = []
            for x in node:
                if isinstance(x, (Exit, Cycle, Return)):
                    out.append(make())
                else:
                    FParser.write_back_before_exits(x, make)
                out.append(x)
            node[:] = out
        elif isinstance(node, tuple):                    # an if's (condition, body) branches
            for x in node:
                FParser.write_back_before_exits(x, make)
        elif is_dataclass(node):
            for f in fields(node):
                FParser.write_back_before_exits(getattr(node, f.name), make)

    @staticmethod
    def guard_name(c):
        return c.values[1] if isinstance(c.values, tuple) else c.values

    @staticmethod
    def only_calls_methods(body, name):
        """Does this code use the variable `name` only as `name.f(...)` or `call name.f(...)`?"""
        allowed = set()
        for n in iter_nodes(body):
            if isinstance(n, FieldAccess) and n.call and isinstance(n.base, Var):
                allowed.add(id(n.base))
            elif isinstance(n, Call) and isinstance(n.receiver, Var):
                allowed.add(id(n.receiver))
        for n in iter_nodes(body):
            if isinstance(n, (Var, Apply, Allocate, Deallocate)) and n.name == name and id(n) not in allowed:
                return False
        return True

    @staticmethod
    def changes(body, name):
        """Might this code change the variable `name`? (an assignment to it or a part of it, or a call it takes part in)"""
        for n in iter_nodes(body):
            if isinstance(n, Assign):
                t = n.target
                while isinstance(t, FieldAccess):
                    t = t.base
                if isinstance(t, (Var, Apply)) and t.name == name:
                    return True
            elif isinstance(n, Call):
                if isinstance(n.receiver, (Var, Apply, FieldAccess)) and FParser.root_name_of(n.receiver) == name:
                    return True
                if any(isinstance(a, (Var, Apply, FieldAccess)) and FParser.root_name_of(a) == name for a in n.args):
                    return True
        return False

    @staticmethod
    def root_name_of(e):
        while isinstance(e, FieldAccess):
            e = e.base
        return e.name if isinstance(e, (Var, Apply)) else None

    def select_case(self, scope):
        if self.at_name("type"):
            return self.select_type(scope)
        if not self.at_name("case"):
            raise Unsupported("'select rank' is not supported; skipped")
        self.k += 1
        self.expect_op("(")
        subject = self.expr()
        if self.char_info(subject) is not None:           # Fortran ignores trailing blanks here too
            subject = Apply("trim", [subject], self.line)
        self.expect_op(")")
        self.end_of_statement()
        stops = {("end", "select"), ("case",)}
        lead = self.parse_body(scope, stops)           # only comments may precede the first case
        if any(not isinstance(n, Comment) for n in lead):
            raise Unsupported("statements before the first 'case'")
        cases, line = [], self.line
        while True:
            cur = self.items[self.i]
            self.i += 1
            toks = self.tokens(cur)
            if classify(toks) != ("case",):
                break                                  # `end select`
            self.toks, self.k, self.line = toks, 1, cur.line
            values = self.case_selector(scope)
            cases.append(Case(values, self.parse_body(scope, stops), cur.line))
        if not cases:
            raise Unsupported("'select case' without any case")
        if sum(c.values is None for c in cases) > 1:
            raise Unsupported("more than one 'case default'")
        try:
            check_cases([(c.values, c.line) for c in cases])
        except LangError as e:
            raise Unsupported(str(e).split(": ", 1)[-1])
        return lead + [Select(subject, cases, line)]

    def case_selector(self, scope):
        """After `case`: `default` (None) or `( value, lo:hi, :hi, lo: )`."""
        if self.at_name("default"):
            self.k += 1
            values = None
        else:
            self.expect_op("(")
            values = []
            while True:
                if self.accept_op(":"):
                    values.append(Slice(None, self.case_value(scope)))
                else:
                    lo = self.case_value(scope)
                    if self.accept_op(":"):
                        hi = None if self.at_op(",", ")") else self.case_value(scope)
                        values.append(Slice(lo, hi))
                    else:
                        values.append(lo)
                if not self.accept_op(","):
                    break
            self.expect_op(")")
        if self.at_name():                              # a construct name: `case (1) name`
            self.k += 1
        self.end_of_statement()
        return values

    def case_value(self, scope):
        """Boreas case values are literals or `const` names; real or computed ones are not translated."""
        e = self.expr()
        if isinstance(e, Var):
            if not self.is_parameter(e.name, scope):
                raise Unsupported(f"case value '{e.name}' is not a named constant")
        elif literal_value(e) is NOT_LITERAL:
            raise Unsupported("case value is not a literal or a named constant")
        elif isinstance(literal_value(e), float):
            raise Unsupported("real case value")
        return e

    def is_parameter(self, name, scope):
        for sc in (scope, scope.module):
            info = sc.decls.get(name) if sc is not None else None
            if info is not None:
                return info.parameter and not info.dims
        try:
            imported = name in self.imported_names(scope)
        except KeyError:
            return False
        return imported and any(m.decls.get(name) is not None and m.decls[name].parameter
                                and not m.decls[name].dims for m in self.modules.values())

    def st_associate(self, it, scope):
        self.skip_block("associate")
        raise Unsupported("'associate' is not supported; skipped")

    def st_interface(self, it, scope):
        return self.interface_block(it, scope)

    def st_abstract(self, it, scope):
        if not self.at_name("interface"):
            raise Unsupported("unrecognised statement")
        self.k += 1
        return self.interface_block(it, scope)

    def st_import(self, it, scope):
        self.note(it.line, f"'{it.text}' ignored (it only matters inside an interface block)")
        return []

    def interface_block(self, it, scope):
        """An interface block describes procedures. Boreas finds procedures by name, so the block itself is
        dropped, but each procedure it describes keeps its signature: `procedure(name) :: dummy` turns into
        a procedure parameter with that signature."""
        generic = None
        if self.peek() is not None:
            generic = self.rename(self.expect_name()) if self.peek().kind == "name" else "operator"
        specifics = []
        while self.i < len(self.items):
            item = self.items[self.i]
            self.i += 1
            if item.comment is not None:
                continue
            try:
                toks = self.tokens(item)
            except Unsupported:
                continue
            if classify(toks) == ("end", "interface"):
                break
            if toks and toks[0].text == "module" and len(toks) > 1 and toks[1].text == "procedure":
                specifics += [t.text for t in toks[2:] if t.kind == "name"]
            elif self.unit_kind(toks) in ("function", "subroutine"):
                specifics.append(self.interface_procedure(toks, item, scope))
        self.cur_scope = scope
        if generic == "operator":
            raise Unsupported("operator overloading (an interface for an operator) is not supported")
        if generic is not None:
            scope.generics.setdefault(generic, [])
            scope.generics[generic] += [n for n in specifics if n not in scope.generics[generic]]
            self.note(it.line, f"generic interface '{generic}' dropped: calls are rewritten to the specific "
                               f"procedures", once=True)
            return []
        self.note(it.line, "interface block dropped: Boreas finds procedures by name", once=True)
        return []

    def interface_procedure(self, toks, item, scope):
        kind, name, dummies, result, ret_type = self.procedure_header(toks)
        inner = Scope(kind, name, dummies)
        inner.result, inner.ret_type = result, ret_type
        inner.elemental = self.header_elemental
        self.parse_body(inner, {("end", kind)})
        self.i += 1                                       # the `end function` / `end subroutine`
        sig = self.interface_signature(inner)
        if sig is not None:
            scope.interfaces[name] = sig
        self.interface_scopes[name] = inner
        return name

    def interface_signature(self, s):
        """The ProcType of a procedure an interface block describes, or None when Boreas cannot say it
        (optional or procedure arguments, an array result, intents a function cannot have)."""
        is_sub = s.kind == "subroutine"
        params = []
        for d in s.dummies:
            info = s.decls.get(d)
            typ = implicit_type(d) if info is None else self.dummy_type(info)
            if d in s.proc_dummies or (info is not None and (info.optional or info.allocatable)):
                return None
            intent = None if info is None else info.intent
            if not is_sub and intent not in (None, "in"):
                return None
            params.append((typ, intent or ("?" if is_sub else "in")))
        ret = None
        if not is_sub:
            info = s.decls.get(s.result or s.name)
            if info is not None and info.dims:
                return None
            ret = info.type if info is not None else (s.ret_type or implicit_type(s.name))
        return ProcType(is_sub, tuple(params), ret, tuple(s.dummies))

    def find_generic(self, scope, name):
        """The specific procedure names a generic interface gives `name`, looking outward through the
        enclosing scopes and the modules this one uses, or None."""
        s = scope
        while s is not None:
            if name in s.generics:
                return s.generics[name]
            for modname, names, _ in s.uses:
                mod = self.modules.get(modname)
                if mod is not None and name in mod.generics and (names is None or name in names):
                    return mod.generics[name]
            s = s.host
        return None

    INT_ORDER = ("int8", "int16", "integer", "int64")
    REAL_ORDER = ("real32", "real")

    def static_type(self, e):
        """(type, rank) of an expression in the current scope, or None when it is not worked out. Only what a
        generic call needs: literals, declared variables and array elements, conversions and arithmetic."""
        if isinstance(e, Num):
            if isinstance(e.value, int):
                return ("integer", 0)
            return ("real" if id(e) in self.double_literals else "real32", 0)
        if isinstance(e, Str):
            return ("string", 0)
        if isinstance(e, Bool):
            return ("logical", 0)
        if isinstance(e, Var):
            info = self.lookup_decl(e.name)
            return None if info is None else (info.type, self.rank_of(info))
        if isinstance(e, Apply):
            info = self.lookup_decl(e.name)
            if info is not None and self.rank_of(info) > 0:
                return (info.type, sum(isinstance(a, Slice) for a in e.args))
            if e.name in ("index", "len", "len_trim", "size", "count", "scan", "verify") and info is None:
                return ("integer", 0)
            if e.name in INT_KINDS.values() or e.name in ("real32", "real") and len(e.args) == 1:
                inner = self.static_type(e.args[0])
                return None if inner is None else (e.name, inner[1])
            return None
        if isinstance(e, Unary):
            inner = self.static_type(e.operand)
            return ("logical", inner[1]) if e.op == "not" and inner is not None else inner
        if isinstance(e, Binary):
            a, b = self.static_type(e.left), self.static_type(e.right)
            if a is None or b is None:
                return None
            rank = max(a[1], b[1])
            if e.op in ("==", "/=", "<", "<=", ">", ">=", "and", "or"):
                return ("logical", rank)
            if a[0] == b[0]:
                return (a[0], rank)
            for order in (self.REAL_ORDER, self.INT_ORDER):
                if a[0] in order and b[0] in order:
                    return (max(a[0], b[0], key=order.index), rank)
            if (a[0] in self.REAL_ORDER and b[0] in self.INT_ORDER) or (b[0] in self.REAL_ORDER and a[0] in self.INT_ORDER):
                return (a[0] if a[0] in self.REAL_ORDER else b[0], rank)
            return None
        if isinstance(e, ArrayLit) and e.items:
            types = {self.static_type(x) for x in e.items}
            if len(types) == 1 and None not in types:
                t = types.pop()
                return (t[0], 1)
        return None

    @staticmethod
    def rank_of(info):
        return len(info.dims) if info.dims is not None else info.rank

    def note_generic_call(self, node, name, args, line):
        """A call to a generic name is rewritten at the end, when every specific procedure is known."""
        if self.find_generic(self.cur_scope, name) is not None:
            self.generic_calls[id(node)] = (node, name, [self.static_type(a) for a in args], line,
                                            self.cur_scope)

    def specific_signature(self, name):
        """[(type, rank, optional)] for a specific procedure's dummies, plus whether it is elemental, or None."""
        s = self.scope_of_procedure.get(name) or self.interface_scopes.get(name)
        if s is None:
            return None
        sig = []
        for d in s.dummies:
            info = s.decls.get(d)
            if info is None or d in s.proc_dummies or d in s.externals:
                sig.append((None, None, info is not None and info.optional))    # not known: matches anything
            else:
                sig.append((info.type, self.rank_of(info), info.optional))
        return sig, s.elemental

    def resolve_generics(self, unit):
        """Replace each call to a generic name by the specific procedure the arguments select. Fortran picks
        the one specific whose arguments agree in type, kind and rank, so ruling out every specific that a
        known argument contradicts leaves the one Fortran would have called. With one specific there is
        nothing to decide."""
        for node, generic, types, line, scope in list(self.generic_calls.values()):
            cands = self.find_generic(scope, generic) or []
            keep = []
            for c in cands:
                got = self.specific_signature(c)
                if got is None:
                    keep.append(c)
                    continue
                sig, elemental = got
                required = sum(1 for _, _, opt in sig if not opt)
                if not required <= len(types) <= len(sig):
                    continue
                if all(t is None or sig[i][0] is None or (t[0] == sig[i][0] and (elemental or t[1] == sig[i][1]))
                       for i, t in enumerate(types)):
                    keep.append(c)
            if len(cands) == 1:
                keep = cands
            if len(keep) == 1:
                if keep[0] != generic:
                    node.name = keep[0]
                    self.note(line, f"'{generic}' is a generic interface; this call goes to '{keep[0]}'", once=True)
            else:
                shown = keep or cands
                self.unresolved_generics.add(generic)
                msg = (f"the call to the generic '{generic}' could not be matched to one of its procedures "
                       f"({', '.join(shown)}); call the specific procedure by name")
                self.todo(line, msg)

    def find_interface(self, scope, name):
        """The signature an interface block gave `name`, looking outward through the enclosing scopes
        and the modules this one uses."""
        s = scope
        while s is not None:
            if name in s.interfaces:
                return s.interfaces[name]
            for modname, names, _ in s.uses:
                mod = self.modules.get(modname)
                if mod is not None and name in mod.interfaces and (names is None or name in names):
                    return mod.interfaces[name]
            s = s.host
        return None

    def st_procedure(self, it, scope):
        """`procedure(name) :: dummy`: the dummy argument is a procedure with the signature `name` has."""
        self.expect_op("(")
        iface = self.rename(self.expect_name())
        self.expect_op(")")
        attrs = set()
        while self.accept_op(","):
            word = self.expect_name()
            attrs.add(word)
            if self.at_op("("):
                self.skip_parens()
        self.accept_op("::")
        names = []
        while self.peek() is not None:
            names.append(self.rename(self.expect_name()))
            if self.at_op("=>"):
                raise Unsupported("a procedure variable with an initial value is not supported")
            if not self.accept_op(","):
                break
        for n in names:
            if "pointer" in attrs or n not in scope.dummies:
                raise Unsupported(f"'{n}' is a procedure variable; Boreas can only pass procedures as arguments")
            if "optional" in attrs:
                raise Unsupported(f"'{n}' is an optional procedure argument, which is not supported")
            sig = self.find_interface(scope, iface)
            if sig is None:
                raise Unsupported(f"the interface '{iface}' is not described by an interface block here")
            scope.proc_dummies[n] = sig
        return []

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
        if self.at_op("("):                          # type(point) :: p
            self.k = 0
            return self.declaration(it, scope)
        return self.type_definition(it, scope)

    def type_name(self, name):
        """A derived type's name in Boreas: renamed like any other name, and also when it is the name of a
        built-in function (a type called `shape`), since a record or interface cannot share that name."""
        name = self.rename(name)
        if name in BUILTINS or name in ELEMENTAL:
            new = name + "_"
            self.raw_names[new] = name
            if name not in self.renames:
                self.renames[name] = new
                self.note(self.line, f"'{name}' is the name of a Boreas built-in function; the type is called '{new}'")
            return new
        return name

    def export_name(self, raw):
        """The Boreas name of something a `use ..., only:` list names: a type renamed for being called like a
        built-in is found under its new name."""
        new = self.renames.get(raw)
        return new if new is not None and new in self.types else self.rename(raw)

    def type_definition(self, it, scope):
        """`type [, attrs] [::] name` through `end type`: a Boreas record (an interface too when it is abstract
        or used polymorphically; see oo_prepare), or a TODO for the whole type when any part of it cannot be
        translated (variables of it are then skipped)."""
        bad, access = None, None
        abstract, parent = False, None
        while self.accept_op(","):
            word = self.expect_name()
            if word in ("public", "private"):
                access = word
            elif word == "bind":
                self.skip_parens()
                self.note(it.line, "bind(c) ignored: Boreas records have no C layout")
            elif word == "abstract":
                abstract = True
            elif word == "extends":
                self.expect_op("(")
                parent = self.type_name(self.expect_name())
                self.expect_op(")")
            else:
                bad = bad or f"unknown type attribute '{word}'"
        self.accept_op("::")
        raw_name = self.peek().text if self.peek() is not None else ""
        name = self.type_name(self.expect_name())
        if self.at_op("("):
            bad = bad or "parameterized derived types are not supported"
            self.skip_parens()
        self.end_of_statement()
        pinfo = None
        if parent is not None:
            pinfo = self.types.get(parent)
            if pinfo is None:
                bad = bad or f"its parent type '{parent}' is not defined earlier in this file"
            elif not pinfo.ok:
                bad = bad or f"its parent type '{parent}' was not translated"
        if not abstract and (raw_name in BUILTINS or raw_name in ELEMENTAL):
            bad = bad or "a record named like a built-in function cannot be told from a call of it"
        own, holder = [], Scope("type", name)
        holder.host = scope
        bindings, poly_comps, in_bindings = {}, [], False
        self.finals = []
        while self.i < len(self.items):
            cur = self.items[self.i]
            self.i += 1
            if cur.comment is not None:
                continue
            self.line = cur.line
            toks = self.tokens(cur)
            lab = classify(toks)
            if lab == ("end", "type") or (lab is not None and lab[0] == "end" and lab[1] in (None, "type")):
                break
            if lab == ("contains",):
                in_bindings = True
                continue
            if in_bindings:
                try:
                    msg = self.binding_line(toks, cur.line, bindings)
                except Unsupported as e:
                    msg = f"{e}: {cur.text}"
                bad = bad or msg
                continue
            word = toks[0].text if toks and toks[0].kind == "name" else ""
            if word in ("private", "public") and len(toks) == 1:
                self.note(cur.line, f"'{word}' in a type: Boreas record fields are always accessible")
                continue
            if word == "sequence" and len(toks) == 1:
                continue
            self.toks, self.k = toks, 0
            if word == "class":
                try:
                    poly_comps.append(self.poly_component() + (cur.line,))
                except Unsupported as e:
                    bad = bad or f"{e}: {cur.text}"
            elif word in TYPE_WORDS and word != "type" or word == "type":
                try:
                    for c in self.declaration(cur, holder):         # an entity that could not be declared
                        bad = bad or c.text.replace("TODO(convert): ", "")
                except Unsupported as e:
                    bad = bad or f"{e}: {cur.text}"
            else:
                bad = bad or f"unsupported statement in a type: {cur.text}"
        else:
            raise LangError("unexpected end of file: 'type' is missing its 'end type'")
        polys = dict(pinfo.poly_fields) if pinfo is not None and pinfo.ok else {}
        for cname, info in holder.decls.items():
            dims = None
            if info.poly:                                  # type(wrapper) :: items(2): an interface-typed field
                polys[cname] = (info.type, info.wrapped)
            if info.dims is not None:
                dims = [self.literal_size(d, holder) for d in info.dims]
                if None in dims:
                    bad = bad or f"component '{cname}' has an array size that is not an integer constant"
                    continue
            own.append(Decl(info.type, cname, dims, self.component_default(cname, info, scope, holder),
                            info.line))
        fields = [replace(d) for d in pinfo.fields] if pinfo is not None and pinfo.ok else []
        wrapper = None
        if poly_comps:
            if len(poly_comps) == 1 and not own and parent is None and not bindings and not abstract:
                wrapper = poly_comps[0][:2]           # only holds a polymorphic value: it is dropped (see primary)
            else:                                     # class(T), allocatable :: item: a field of T's interface
                for t, cname, line in poly_comps:
                    polys[cname] = (t, None)
                    own.append(Decl(t, cname, None, None, line))
                own.sort(key=lambda d: d.line)
        taken = {d.name for d in fields}
        for d in own:
            if d.name in taken:
                bad = bad or f"component '{d.name}' is already a component of the parent type"
        fields += own
        if name in self.types:
            self.todo(it.line, f"derived type '{name}' is defined more than once")
        notes = []
        if self.finals and not bad:
            names = ", ".join(self.finals)
            msg = (f"'{names}' was a finalizer of '{name}': Boreas has no destructors, so it is no longer called "
                   f"automatically; call it where a '{name}' goes away if that matters")
            self.todo(it.line, msg)
            notes.append(Comment("TODO(convert): " + msg))
        if bad:
            self.types[name] = TypeInfo(name, scope, [], it.line, ok=False)
            raise Unsupported(f"derived type '{name}' was not translated: {bad}")
        if scope.kind == "module" and access:
            scope.access[name] = access
        self.types[name] = TypeInfo(name, scope, fields, it.line, parent=parent, abstract=abstract,
                                    bindings=bindings, wrapper=wrapper, poly_fields=polys)
        return notes

    def poly_component(self):
        """`class(T), allocatable :: name` inside a type: (T, name)."""
        self.expect_name()
        self.expect_op("(")
        if self.at_op("*"):
            raise Unsupported("class(*) is not supported")
        t = self.type_name(self.expect_name())
        self.expect_op(")")
        attrs = set()
        while self.accept_op(","):
            attrs.add(self.expect_name())
        if "allocatable" not in attrs:
            raise Unsupported("a polymorphic component must be allocatable")
        self.accept_op("::")
        name = self.rename(self.expect_name())
        if self.at_op("("):
            raise Unsupported("an array of polymorphic values inside a type")
        info = self.types.get(t)
        if info is None or not info.ok:
            raise Unsupported(f"derived type '{t}' is not defined earlier in this file")
        return (t, name)

    def binding_line(self, toks, line, bindings):
        """One line after a type's `contains`: `procedure :: name [=> impl]` or `procedure(iface), deferred :: name`.
        Fills `bindings`; returns why the type cannot be translated, or None."""
        self.toks, self.k = toks, 0
        word = self.expect_name()
        if word in ("private", "public") and len(toks) == 1:
            return None
        if word == "final":                               # a destructor: Boreas has none, the procedure stays
            self.accept_op("::")
            while True:
                self.finals.append(self.rename(self.expect_name()))
                if not self.accept_op(","):
                    break
            return None
        if word != "procedure":
            return f"'{word}' (a generic or other type-bound statement) is not supported"
        iface = None
        if self.accept_op("("):
            iface = self.rename(self.expect_name())
            self.expect_op(")")
        attrs, pass_arg = set(), None
        while self.accept_op(","):
            a = self.expect_name()
            attrs.add(a)
            if a == "pass" and self.at_op("("):
                self.k += 1
                pass_arg = self.rename(self.expect_name())
                self.expect_op(")")
            elif self.at_op("("):
                self.skip_parens()
        if "nopass" in attrs:
            return "a 'nopass' binding is not supported (it gets no object, and Boreas functions in a record always do)"
        deferred = "deferred" in attrs
        if deferred and pass_arg is not None:
            return "a deferred binding with pass(arg) is not supported"
        if deferred and iface is None:
            return "a deferred binding needs an interface: procedure(name), deferred :: binding"
        if iface is not None and not deferred:
            return "a binding with an interface (procedure(name) :: x) is only supported as deferred"
        self.accept_op("::")
        while True:
            name = self.rename(self.expect_name())
            impl = name
            if self.accept_op("=>"):
                impl = self.rename(self.expect_name())
            bindings[name] = Binding(name, None if deferred else impl, iface if deferred else None, line, pass_arg)
            if not self.accept_op(","):
                break
        return None

    def literal_size(self, e, scope):
        """An array size as a literal Num, folding constants (parameters, + - *); None if it is not one."""
        v = self.const_int(e, scope)
        return None if v is None else Num(v)

    def const_int(self, e, scope, depth=0):
        if depth > 20:
            return None
        if isinstance(e, Num) and isinstance(e.value, int):
            return e.value
        if isinstance(e, Var):
            s = scope
            while s is not None:
                info = s.decls.get(e.name)
                if info is not None:
                    return self.const_int(info.init, s, depth + 1) if info.parameter and info.init is not None else None
                for modname, names, _ in s.uses:                      # a constant brought in by `use`
                    other = self.modules.get(modname)
                    info = other.decls.get(e.name) if other is not None and (names is None or e.name in names) else None
                    if info is not None and info.parameter and info.init is not None:
                        return self.const_int(info.init, other, depth + 1)
                s = s.host
            return None
        if isinstance(e, Unary) and e.op == "-":
            v = self.const_int(e.operand, scope, depth + 1)
            return None if v is None else -v
        if isinstance(e, Binary) and e.op in ("+", "-", "*"):
            a, b = self.const_int(e.left, scope, depth + 1), self.const_int(e.right, scope, depth + 1)
            if a is None or b is None:
                return None
            return a + b if e.op == "+" else a - b if e.op == "-" else a * b
        return None

    def component_default(self, cname, info, scope, holder):
        """The default of a component as a Boreas constant expression, or None (with a TODO)."""
        if info.init is None:
            return None
        import copy
        try:
            return self.constant_expr(copy.deepcopy(info.init), scope, holder, 0)
        except Unsupported as e:
            self.todo(info.line, f"default of component '{cname}' dropped: {e}")
            return None

    def constant_expr(self, e, scope, holder, depth):
        """Rewrite a Fortran default so Boreas accepts it: parameters are inlined unless they are
        module constants; only records, built-in functions and literals may remain."""
        if depth > 20:
            raise Unsupported("it refers to itself")
        if isinstance(e, Var):
            s = scope
            while s is not None:
                info = s.decls.get(e.name)
                if info is not None and info.parameter and info.init is not None:
                    if s.kind == "module":
                        return e
                    import copy
                    return self.constant_expr(copy.deepcopy(info.init), s, holder, depth + 1)
                if info is not None:
                    break
                s = s.host
            raise Unsupported(f"'{e.name}' is not a constant")
        if isinstance(e, Unary):
            e.operand = self.constant_expr(e.operand, scope, holder, depth)
        elif isinstance(e, Binary):
            e.left = self.constant_expr(e.left, scope, holder, depth)
            e.right = self.constant_expr(e.right, scope, holder, depth)
        elif isinstance(e, ArrayLit):
            e.items = [self.constant_expr(x, scope, holder, depth) for x in e.items]
        elif isinstance(e, Apply):
            if e.name not in self.types and e.name not in PURE_BUILTINS:
                raise Unsupported(f"'{e.name}' is not a record or a built-in function Boreas has")
            e.args = [NamedArg(a.name, self.constant_expr(a.value, scope, holder, depth)) if isinstance(a, NamedArg)
                      else self.constant_expr(a, scope, holder, depth) for a in e.args]
        elif not isinstance(e, (Num, Str, Bool)):
            raise Unsupported("it is not a constant expression")
        return e

    # output
    def print_items(self, expand_arrays=False, blank=True):
        items = []
        if not self.at_op(")") and self.peek() is not None:
            while True:
                items.extend(self.output_item(expand_arrays))
                if not self.accept_op(","):
                    break
        self.end_of_statement()
        return items or ([Str("")] if blank else [])

    def output_item(self, expand_arrays):
        """One item of an output list: an expression, an implied do `(a(i), i = 1, 3)` (unrolled), or,
        under a format, a whole array (one item per element)."""
        loop = self.implied_do()
        if loop is not None:
            exprs, var, lo, hi, step = loop
            bounds = [self.constant_int(x) for x in (lo, hi, step)]
            if None in bounds or bounds[2] == 0:
                raise Unsupported("implied do loop with bounds that are not literal numbers")
            lo, hi, step = bounds
            values = range(lo, hi + (1 if step > 0 else -1), step)
            if len(values) > 1000:
                raise Unsupported("implied do loop with more than 1000 steps")
            return [substitute(e, var, i) for i in values for e in exprs]
        e = self.expr()
        if expand_arrays and isinstance(e, Var):
            info = self.cur_scope.decls.get(e.name)
            if info is not None and info.dims is not None:
                sizes = [self.constant_int(d) for d in info.dims]
                if len(sizes) != 1 or sizes[0] is None or sizes[0] > 1000:
                    raise Unsupported(f"array '{e.name}' written under a format (only one-dimensional arrays "
                                      f"with a literal size are unrolled)")
                return [Apply(e.name, [Num(i)], self.line) for i in range(1, sizes[0] + 1)]
        return [e]

    def constant_int(self, e):
        if isinstance(e, Num) and isinstance(e.value, int):
            return e.value
        if isinstance(e, Unary) and e.op == "-" and isinstance(e.operand, Num) and isinstance(e.operand.value, int):
            return -e.operand.value
        return None

    def constructor_item(self):
        """One item of an array constructor: an expression, or an implied do (which becomes a Boreas generator)."""
        loop = self.implied_do(self.constructor_item)
        if loop is None:
            return self.expr()
        body, var, lo, hi, step = loop
        return Gen(body, var, lo, hi, None if isinstance(step, Num) and step.value == 1 else step, self.line)

    def implied_do(self, item=None):
        """Parse `(expr, expr, var = lo, hi [, step])` at the current position, or put everything back.
        `item` parses the expressions (an array constructor allows implied dos inside them)."""
        item = item or self.expr
        start = self.k
        if not self.at_op("("):
            return None
        self.k += 1
        exprs = []
        try:
            while True:
                nxt = self.peek(1)
                if self.peek() is not None and self.peek().kind == "name" and nxt is not None and nxt.text == "=":
                    break
                exprs.append(item())
                if not self.accept_op(","):
                    self.k = start
                    return None
            var = self.rename(self.expect_name())
            self.expect_op("=")
            lo = self.expr()
            self.expect_op(",")
            hi = self.expr()
            step = Num(1)
            if self.accept_op(","):
                step = self.expr()
            self.expect_op(")")
        except Unsupported:
            self.k = start
            return None
        return exprs, var, lo, hi, step

    def format_text(self, toks, scope):
        """The text inside the parentheses of a format given as a string or as a statement label."""
        if len(toks) == 1 and toks[0].kind == "str":
            text = toks[0].value.strip()
            if not (text.startswith("(") and text.endswith(")")):
                raise Unsupported("format string without parentheses")
            return text[1:-1]
        if len(toks) == 1 and toks[0].kind == "int":
            if toks[0].value not in scope.formats:
                raise Unsupported(f"no format statement with label {toks[0].value}")
            return scope.formats[toks[0].value]
        raise Unsupported("unsupported write format or option")

    def formatted_output(self, text, line, newline=True, to=None):
        values = self.print_items(expand_arrays=True, blank=False)
        try:
            lines, advance = render(parse_format(text)[0], values)
        except FormatError as e:
            raise Unsupported(f"unsupported write format: {e}")
        out = [Print([l], True, to) for l in lines[:-1]]
        out.append(Print([lines[-1]], newline and advance, to))
        return out

    def st_print(self, it, scope):
        fmt = None
        if self.at_op("*"):
            self.k += 1
        elif self.peek() is not None and self.peek().kind in ("str", "int"):
            fmt = self.format_text([self.next()], scope)
        else:
            raise Unsupported("unsupported print format")
        if self.peek() is not None:
            self.expect_op(",")
        if fmt is not None:
            return self.formatted_output(fmt, it.line)
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
        to = self.unit_target(parts[0], scope, "write", terminal=6)
        fmt, newline = None, True
        if len(parts) > 1:
            f = parts[1]
            if not (len(f) == 1 and f[0].text == "*"):
                fmt = self.format_text(f, scope)
        for opt in parts[2:]:
            if len(opt) == 3 and opt[0].text == "advance" and opt[1].text == "=" and opt[2].kind == "str" \
                    and opt[2].value.lower() in ("no", "yes"):
                newline = opt[2].value.lower() == "yes"
            else:
                raise Unsupported("unsupported write options")
        if fmt is not None:
            return self.formatted_output(fmt, it.line, newline, to)
        return [Print(self.print_items(), newline, to)]

    def unit_target(self, tokens, scope, way, terminal):
        """The Boreas file variable for the unit of a read/write, or None for the terminal (`*` or the
        usual unit number)."""
        if len(tokens) == 1 and tokens[0].text == "*":
            return None
        if len(tokens) == 1 and tokens[0].kind == "int" and tokens[0].value == terminal:
            return None
        if len(tokens) == 1 and tokens[0].kind == "int":
            e = Num(tokens[0].value)
        elif len(tokens) == 1 and tokens[0].kind == "name":
            e = Var(self.rename(tokens[0].text))
        else:
            raise Unsupported("a unit number that is not a plain number or name")
        return self.unit_var(e, scope, way)

    def unit_var(self, e, scope, way=None):
        """The file variable (`unit_9`) standing for the constant unit number `e`."""
        n = self.const_int(e, scope)
        if n is None:
            raise Unsupported("the unit number is not a constant, so there is no file variable for it")
        if n in (0, 5, 6):
            raise Unsupported(f"unit {n} (a standard stream) used as a file")
        scope.units.add(n)
        if way:
            self.unit_use.setdefault(n, set()).add(way)
        return Var(f"unit_{n}")

    def keyword_args(self):
        """The `(unit=9, file=name, ...)` of open / close / inquire: [(keyword or None, expression)]."""
        self.expect_op("(")
        out = []
        while True:
            key = None
            if self.peek() is not None and self.peek().kind == "name" and self.peek(1) is not None \
                    and self.peek(1).kind == "op" and self.peek(1).text == "=":
                key = self.peek().text
                self.k += 2
            out.append((key, self.expr()))
            if not self.accept_op(","):
                break
        self.expect_op(")")
        self.end_of_statement()
        return out

    @staticmethod
    def sorted_args(args, positional):
        """Split keyword_args into a dict, giving the leading positional ones the names in `positional`."""
        found = {}
        for n, (key, e) in enumerate(args):
            if key is None:
                if n >= len(positional):
                    raise Unsupported("too many positional arguments")
                key = positional[n]
            found[key] = e
        return found

    @staticmethod
    def required(args, key, statement):
        if key not in args:
            raise Unsupported(f"{statement} without a {key}")
        return args.pop(key)

    def word(self, e, what):
        if not isinstance(e, Str):
            raise Unsupported(f"{what} that is not a literal word")
        return e.value.strip().lower()

    def st_open(self, it, scope):
        """`open(unit=9, file=name, status='replace', iostat=ios)` -> `unit_9 = open(name, "write")`."""
        args = self.sorted_args(self.keyword_args(), ["unit", "file"])
        unit = self.unit_var(self.required(args, "unit", "open"), scope)
        n = int(unit.name[5:])
        name = args.pop("file", None)
        if name is None:
            raise Unsupported("open without a file name")
        status = self.word(args.pop("status", Str("unknown")), "a status")
        action = self.word(args.pop("action", Str("")), "an action")
        position = self.word(args.pop("position", Str("asis")), "a position")
        iostat = args.pop("iostat", None)
        for key, expected in (("form", "formatted"), ("access", "sequential"), ("blank", "null")):
            if key in args and self.word(args.pop(key), key) != expected:
                raise Unsupported(f"open with {key} other than '{expected}'")
        if args:
            raise Unsupported(f"open option '{sorted(args)[0]}'")
        if status == "scratch":
            raise Unsupported("a scratch file")
        if not isinstance(name, Str):
            name = Apply("trim", [name], it.line)               # Fortran ignores trailing blanks in a file name
        mode = Str("read")
        self.opens.append((n, mode, status, action, position, it.line))
        if iostat is None:
            return [Assign(unit, Apply("open", [name, mode], it.line), it.line)]
        if not isinstance(iostat, Var):
            raise Unsupported("iostat that is not a plain variable")
        tmp = self.unique("opened", scope, [])
        scope.decls[tmp] = Info("file?", None, False, None, None, False, it.line)
        return [Assign(Var(tmp), Apply("try_open", [name, mode], it.line), it.line),
                If([(Apply("present", [Var(tmp)], it.line),
                     [Assign(unit, Var(tmp), it.line), Assign(iostat, Num(0), it.line)])],
                   [Assign(iostat, Num(1), it.line)])]

    def st_close(self, it, scope):
        args = self.sorted_args(self.keyword_args(), ["unit"])
        unit = self.unit_var(self.required(args, "unit", "close"), scope)
        if "status" in args and self.word(args.pop("status"), "a status") != "keep":
            raise Unsupported("close with status 'delete'")
        if args:
            raise Unsupported(f"close option '{sorted(args)[0]}'")
        return [Close(unit, it.line)]

    def st_inquire(self, it, scope):
        """Only `inquire(file=name, exist=flag)`: becomes `flag = exists(name)`."""
        args = self.sorted_args(self.keyword_args(), [])
        name, flag = args.pop("file", None), args.pop("exist", None)
        if name is None or flag is None or args:
            raise Unsupported("inquire other than file=, exist=")
        if not isinstance(name, Str):
            name = Apply("trim", [name], it.line)
        return [Assign(flag, Apply("exists", [name], it.line), it.line)]

    def merge_units(self):
        """The file variables for unit numbers live in one module, so every procedure sees the same
        open file; their modes come from how the program uses each unit."""
        used = {n for r in self.raw for n in r.scope.units}
        if not used:
            return
        modname = self.unique_module_name("file_units")
        ms = Scope("module", modname)
        ms.comments.append("one file variable for each Fortran unit number")
        for n in sorted(used):
            ms.decls[f"unit_{n}"] = Info("file", None, False, None, None, False, 0)
        self.modules[modname] = ms
        for r in self.raw:
            if r.scope.units:
                self.add_use(r.scope, modname, [f"unit_{n}" for n in sorted(r.scope.units)], r.line)
        for n, node, status, action, position, line in self.opens:
            ways = self.unit_use.get(n, set())
            if action == "read" or (not action and "write" not in ways and (status == "old" or "read" in ways)):
                node.value = "read"
            elif position == "append":
                node.value = "append"
            else:
                node.value = "write"
            if "read" in ways and "write" in ways:
                self.todo(line, f"unit {n} is used for both reading and writing; a Boreas file is one or the other")
            if action in ("", "read", "write") and status == "old" and node.value == "write":
                self.note(line, f"unit {n} is opened with status 'old' but written: the Boreas \"write\" mode "
                                f"replaces the file's contents, as Fortran's sequential write does")

    # -- finishing: turn raw scopes into Boreas procedures
    def merge_commons(self):
        """Each common block becomes a module of variables named `common_<block>`. The names come from
        the first unit that mentions the block (a `block data` unit first of all); other units refer
        to the same variables by position, so their own names are renamed to match."""
        blocks = {}                                      # block -> (module scope, canonical names, Infos)
        units = list(self.block_data) + [r.scope for r in self.raw]
        by_scope = {id(r.scope): r for r in self.raw}
        for sc in units:
            for block, entries in sc.commons.items():
                names = [n for n, _ in entries]
                line = entries[0][1]
                label = f"common block /{block}/" if block else "blank common"
                if block not in blocks:
                    modname = self.unique_module_name("common_" + (block or "blank"))
                    ms = Scope("module", modname)
                    ms.line = line
                    ms.comments.append(f"{label}, as a module of variables")
                    members = []
                    for n in names:
                        info = sc.decls[n]
                        members.append(n)
                        ms.decls[n] = replace(info, init=None, parameter=False, intent=None)
                    self.modules[modname] = ms
                    blocks[block] = (ms, members)
                    self.note(line, f"{label} became module {modname}")
                ms, members = blocks[block]
                if len(names) != len(members):
                    self.todo(line, f"{label} has {len(members)} variables in one place and {len(names)} in another")
                    continue
                bad = [n for n, m in zip(names, members)
                       if sc.decls[n].type != ms.decls[m].type or len(sc.decls[n].dims or []) != len(ms.decls[m].dims or [])]
                if bad:
                    self.todo(line, f"{label}: '{bad[0]}' has a different type or rank than in the unit that "
                                    f"first names the block")
                    continue
                r = by_scope.get(id(sc))
                for n, m in zip(names, members):
                    info = sc.decls.pop(n)
                    if info.init is not None:               # `data` in a block data unit
                        ms.decls[m].init = info.init
                    if n != m:
                        if r is not None:
                            if m in sc.decls or m in sc.dummies:
                                self.todo(line, f"{label}: '{n}' would be renamed to '{m}', which is already "
                                                f"used in this procedure")
                            self.rename_in(sc, r.body, n, m)
                if r is not None:
                    self.add_use(sc, ms.name, members, line)

    def apply_equivalences(self, r):
        """Names that share storage become one name. Same-type scalars and arrays work out to a
        rename (`equivalence (a, b)`) or an element of the larger one (`equivalence (v(3), x)` makes
        `x` stand for `v(3)`); overlapping storage of different types has no Boreas form."""
        s = r.scope
        for group, line in s.equivs:
            why = self.equivalence_plan(s, group)
            if isinstance(why, str):
                self.todo(line, why)
                r.body.insert(0, Comment("TODO(convert): " + why))
                continue
            anchor, plan = why
            for name, make in plan.items():
                info = s.decls.pop(name)
                s.saved.pop(name, None)
                ainfo = s.decls[anchor]
                if info.init is not None:
                    ainfo.init = info.init
                if make is not None:
                    r.body[:] = replace_nodes(r.body, make(name))
                    left = [n for n in iter_nodes(r.body) if isinstance(n, (Var, Apply)) and n.name == name]
                    if left or any(isinstance(n, Do) and n.var == name for n in iter_nodes(r.body)):
                        msg = f"'{name}' shares storage with '{anchor}' and is used in a way that cannot be rewritten"
                        self.todo(line, msg)
                        r.body.insert(0, Comment("TODO(convert): " + msg))
                    continue
                r.body[:] = replace_nodes(r.body, lambda n, old=name: self.rename_node(n, old, anchor))

    @staticmethod
    def rename_node(n, old, new):
        if isinstance(n, (Var, Apply)) and n.name == old:
            n.name = new
        elif isinstance(n, Do) and n.var == old:
            n.var = new
        return None

    def equivalence_plan(self, s, group):
        """Either a message saying why this group cannot be rewritten, or (anchor name, {name: rewrite})."""
        names = [n for n, _ in group]
        text = "equivalence (" + ", ".join(n if k is None else f"{n}({k})" for n, k in group) + ")"
        if len(set(names)) < len(names):
            return f"{text} names a variable twice"
        infos = {n: s.decls[n] for n in names}
        for n in names:
            i = infos[n]
            if n in s.dummies or i.parameter or i.allocatable:
                return f"{text}: '{n}' is an argument, a constant or allocatable"
            if any(n == c for entries in s.commons.values() for c, _ in entries):
                return f"{text}: '{n}' is in a common block"
        if len({i.type for i in infos.values()}) > 1:
            return f"{text} overlays different types, which Boreas cannot express"
        if any(i.type == "string" for i in infos.values()):
            return f"{text} overlays character variables"
        size = {}
        for n, k in group:
            i = infos[n]
            if i.dims is None:
                if k is not None:
                    return f"{text}: '{n}' is not an array"
                size[n] = None
                continue
            sizes = [self.const_int(d, s) for d in i.dims]
            if None in sizes:
                return f"{text}: '{n}' has a size that is not a literal number"
            size[n] = sizes
        first = group[0][0]
        arrays = [n for n in names if size[n] is not None]
        if any(len(size[n]) > 1 for n in arrays):                 # whole same-shape arrays only
            if any(k is not None for _, k in group) or any(size[n] != size[first] for n in names):
                return f"{text} overlays arrays of different shapes"
            if any(infos[n].init is not None for n in names[1:]) or any(
                    infos[n].dims != infos[first].dims for n in names):
                return f"{text}: the arrays are declared differently"
            return first, {n: None for n in names[1:]}
        start, extent = {}, {}                                    # element 1 of each name, and its length
        for n, k in group:
            count = 1 if size[n] is None else size[n][0]
            if k is not None and not 1 <= k <= count:
                return f"{text}: '{n}' has no element {k}"
            start[n], extent[n] = -((k or 1) - 1), count          # positions are counted from the shared element
        anchor = next((n for n in names if all(start[n] <= start[m] and start[m] + extent[m] <= start[n] + extent[n]
                                                for m in names)), None)
        if anchor is None:
            return f"{text} overlays storage that neither name covers"
        k_a = dict(group)[anchor] or 1
        plan = {}
        for n, k in group:
            if n == anchor:
                continue
            shift = k_a - (k or 1)
            if infos[n].init is not None and (shift or size[n] != size[anchor] or infos[anchor].init is not None):
                return f"{text}: '{n}' has initial values that cannot move to '{anchor}'"
            if size[n] is None:                                   # a scalar: one element of the anchor
                if size[anchor] is None:
                    plan[n] = None
                else:
                    plan[n] = lambda old, a=anchor, at=k_a: (
                        lambda node: Apply(a, [Num(at)], getattr(node, "line", 0))
                        if isinstance(node, Var) and node.name == old else None)
                continue
            if shift == 0 and size[n] == size[anchor]:
                plan[n] = None
            elif size[anchor] is None:
                return f"{text}: '{n}' is larger than '{anchor}'"
            else:
                plan[n] = lambda old, a=anchor, sh=shift: self.shifted(old, a, sh)
        return anchor, plan

    @staticmethod
    def shifted(old, anchor, shift):
        def make(node):
            if isinstance(node, Apply) and node.name == old and len(node.args) == 1 and not isinstance(node.args[0], Slice):
                i = node.args[0]
                if shift == 0:
                    new = i
                elif isinstance(i, Num) and isinstance(i.value, int):
                    new = Num(i.value + shift)
                else:
                    new = Binary("+" if shift > 0 else "-", i, Num(abs(shift)))
                return Apply(anchor, [new], node.line)
            return None
        return make

    def add_use(self, scope, modname, names, line):
        """A procedure inside a module imports through its module (Boreas modules import for their procedures)."""
        (scope.module or scope).uses.append((modname, names, line))

    def unique_module_name(self, base):
        taken = set(self.modules) | {r.scope.name for r in self.raw}
        while base in taken:
            base += "_"
        return base

    def structure_gotos(self, r):
        s = r.scope
        pending = Structurer(lambda base: self.unique(base, s, r.body), self.todo)
        r.body = pending.run(r.body)
        for name in pending.new_vars:
            s.decls[name] = Info("integer", None, False, None, None, False, r.line)

    def finalize(self):
        self.oo_prepare()
        for r in self.raw:                               # a module that stopped half way keeps what it had
            if r.scope.module is not None:
                self.modules.setdefault(r.scope.module.name, r.scope.module)
        for r in self.raw:
            self.resolve_reads(r)
        for r in self.raw:
            self.apply_equivalences(r)
        self.merge_commons()
        self.merge_units()
        for r in self.raw:
            self.structure_gotos(r)
        for ms in self.modules.values():           # a public generic needs its specifics reachable
            for generic, specifics in ms.generics.items():
                if self.is_public(ms, generic):
                    for sp in specifics:
                        if not self.is_public(ms, sp):
                            ms.access[sp] = "public"
        proc_names = {r.scope.name for r in self.raw if r.kind != "program"}
        functions, program = {}, None
        modules = {name: self.build_module(ms) for name, ms in self.modules.items()}
        built = []                                       # (Function | Program, its scope)
        for r in self.raw:
            if r.kind == "program":
                program = self.build_program(r, proc_names)
                built.append((program, r.scope))
                continue
            mod = r.scope.module
            target = modules[mod.name].functions if mod is not None else functions
            if r.scope.name in target:
                self.todo(r.line, f"'{r.scope.name}' is defined more than once")
            fn = self.build_procedure(r, proc_names)
            fn.public = mod is not None and r.scope.host is mod and self.is_public(mod, fn.name)
            self.fn_lines[id(fn)] = r.line
            target[fn.name] = fn
            built.append((fn, r.scope))
        records, interfaces = {}, {}
        for t in self.types.values():
            if not t.ok or t.wrapper is not None:
                continue
            if not t.abstract:
                fields = [replace(d, type=self.iface_name.get(d.type, d.type)) if d.name in t.poly_fields else d
                          for d in t.fields]
                rd = RecordDef(t.name, fields, t.module is not None and self.is_public(t.module, t.name), t.line,
                               implements=self.implemented_interfaces(t))
                (modules[t.module.name].records if t.module is not None else records)[t.name] = rd
            if t.name in self.iface_name:
                name = self.iface_name[t.name]
                idef = InterfaceDef(name, {}, t.module is not None and self.is_public(t.module, name), t.line)
                (modules[t.module.name].interfaces if t.module is not None else interfaces)[name] = idef
        unit = Unit(functions, program, modules, records, ieee=True, interfaces=interfaces)  # Fortran arithmetic is IEEE
        self.scope_of_procedure = {r.scope.name: r.scope for r in self.raw if r.kind != "program"}
        self.resolve_generics(unit)
        self.add_intrinsic_wrappers(unit)
        self.link_uses(unit, built)
        self.mark_indexing(unit, built)
        self.check_saved(unit, built)
        self.infer_intents(unit)
        self.mark_module_writes(unit)
        self.hoist_calls(unit)
        self.attach_bindings(unit)
        self.check_calls(unit)
        if program is not None:                          # a final plain `stop` changes nothing
            while program.body and isinstance(program.body[-1], Stop) and program.body[-1].code is None:
                program.body.pop()
        return unit

    @staticmethod
    def indexable(info):
        """Is a name with this declaration written `name[...]`: an array, or a string (a substring)?"""
        return info.dims is not None or info.allocatable or info.rank > 0 or info.type == "string"

    def indexable_names(self, scope):
        """Every name `scope` can index: its own variables, those of the procedures and module around it,
        and the ones it imports."""
        names, s = set(), scope
        while s is not None:
            names |= {n for n, i in s.decls.items() if self.indexable(i)}
            if s.module is not None and s.module is not s:
                names |= {n for n, i in s.module.decls.items() if self.indexable(i)}
            for modname, only, _ in s.uses:
                other = self.modules.get(modname)
                if other is not None:
                    vis = self.visible(other)
                    names |= {n for n in vis if (only is None or n in only)
                              and (n in other.decls and self.indexable(other.decls[n])
                                   or n not in other.decls and self.indexable_origin(vis[n], n))}
            names -= s.externals
            s = s.host
        return names - set(self.scope_of_procedure)       # `character :: grade` can declare the type of a function

    def indexable_origin(self, modname, name):
        mod = self.modules.get(modname)
        return mod is not None and name in mod.decls and self.indexable(mod.decls[name])

    def mark_indexing(self, unit, built):
        """Boreas writes an array element as `a[i]`, and keeps `f(x)` for calls. Fortran writes both with
        parentheses, so mark each name the code declares (or imports) as an array or a string, and every
        field with subscripts."""
        for owner, scope in built:
            names = self.indexable_names(scope)
            for n in iter_nodes(owner.body):
                if isinstance(n, Apply) and n.name in names:
                    n.bracket = True
                elif isinstance(n, FieldAccess) and n.args is not None and not n.call:
                    n.bracket = True
        for name, ms in self.modules.items():
            names = self.indexable_names(ms)
            for n in iter_nodes([d.init for d in unit.modules[name].decls]):
                if isinstance(n, Apply) and n.name in names:
                    n.bracket = True
                elif isinstance(n, FieldAccess) and n.args is not None and not n.call:
                    n.bracket = True
        for rd in list(unit.records.values()) + [r for m in unit.modules.values() for r in m.records.values()]:
            for n in iter_nodes([f.init for f in rd.fields]):
                if isinstance(n, FieldAccess) and n.args is not None and not n.call:
                    n.bracket = True

    # -- type-bound procedures: records, interfaces and copies of the procedures
    def oo_prepare(self):
        """Once every type is known: each type's procedures (its own and its parent's), and which types need a
        Boreas interface. An abstract type is one; a normal type that others extend and that is used as
        class(T) is one too, under the name T_any (the record keeps the name T)."""
        types = {n: t for n, t in self.types.items() if t.ok and t.wrapper is None}
        for t in types.values():                       # a parent is always defined before its extensions
            eff = dict(self.eff.get(t.parent, {}))
            for b in t.bindings.values():
                eff[b.name] = (b, t.name)
            self.eff[t.name] = eff
        has_children = {t.parent for t in types.values() if t.parent is not None}
        impls = {(r.scope.module.name if r.scope.module is not None else None, r.scope.name): r
                 for r in self.raw if r.kind != "program"}
        passed = set()                                 # the `self` of a procedure that implements a binding: it just means "this record"
        for t in types.values():
            for b in t.bindings.values():
                r = impls.get((t.module.name if t.module is not None else None, b.impl))
                if r is not None and r.scope.dummies:
                    passed.add((id(r.scope), b.pass_arg if b.pass_arg in r.scope.dummies else r.scope.dummies[0]))
        used = set()
        for sc in [r.scope for r in self.raw] + list(self.modules.values()):
            for name, info in sc.decls.items():
                if info.poly and (id(sc), name) not in passed:
                    used.add(info.type)
        for t in types.values():
            used |= {T for T, _ in t.poly_fields.values()}
        taken = set(self.types) | {r.scope.name for r in self.raw} | {n for m in self.modules.values() for n in m.decls}
        for t in types.values():
            if t.abstract:
                self.iface_name[t.name] = t.name
            elif t.name in used and t.name in has_children:
                name = t.name + "_any"
                while name in taken:
                    name += "_"
                taken.add(name)
                self.iface_name[t.name] = name
                if t.scope.kind == "module":
                    t.scope.access[name] = t.scope.access.get(t.name, "public" if t.scope.default_public else "private")
                self.note(t.line, f"'{t.name}' is also used as class({t.name}): the record keeps the name and the "
                                  f"interface for class({t.name}) is '{name}'")
        self.guard_cases()

    def guard_cases(self):
        """`class is (T)` matches T and everything that extends it. Boreas matches a `case` on one record or on
        an interface (any record that fits it), so a guard becomes `case T_any` when its body only calls T's
        functions and no unrelated record could fit that interface; otherwise it becomes one case per concrete
        type that extends T (a converted program is closed, so they are all known)."""
        import copy
        concrete = [t for t in self.types.values() if t.ok and t.wrapper is None and not t.abstract]
        for r in self.raw:
            for sel in reversed([n for n in iter_nodes(r.body) if isinstance(n, SelectType)]):   # inner ones first
                guards = [c for c in sel.cases if isinstance(c.values, tuple)]
                if not guards:
                    continue
                guards.sort(key=lambda c: -len(self.ancestors(self.types[c.values[1]])))     # the most specific first
                if all(c.values[2] and self.interface_fits(self.types[c.values[1]], concrete) for c in guards):
                    slots = [i for i, c in enumerate(sel.cases) if isinstance(c.values, tuple)]
                    for i, c in zip(slots, guards):               # the more specific interface is tried first
                        sel.cases[i] = c
                    for c in guards:
                        c.values = self.iface_name[c.values[1]]
                    continue
                taken = {c.values for c in sel.cases if isinstance(c.values, str)}
                made_for = {}
                for c in guards:
                    top = c.values[1]
                    family = [t for t in concrete if t.name not in taken and any(a.name == top for a in self.ancestors(t))]
                    seen = self.imported_names(r.scope)
                    made = [t.name for t in family if t.module is r.scope.module or t.name in seen]
                    hidden = [t.name for t in family if t.name not in made]
                    taken |= set(made)
                    made_for[id(c)] = [Case(name, copy.deepcopy(c.body), c.line, c.col) for name in made]
                    self.note(c.line, f"'class is ({top})' is written out as a case for each type that extends it: "
                                      f"{', '.join(made) or 'none'}")
                    if hidden:                                # Fortran matches them too, but this code cannot name them
                        msg = (f"'class is ({top})' also matches {', '.join(hidden)}, which this code cannot see "
                               f"(its module is not used here); add a case for it")
                        self.todo(c.line, msg)
                        home = made_for[id(c)][0] if made_for[id(c)] else next(x for x in sel.cases if x.values is None)
                        home.body.insert(0, Comment("TODO(convert): " + msg))
                out = []
                for c in sel.cases:
                    out.extend(made_for[id(c)] if id(c) in made_for else [c])
                sel.cases[:] = out

    def interface_fits(self, t, concrete):
        """May a guard `class is (t)` be a case on t's interface? It needs one, with functions to tell the types
        apart, and no record outside t's family may have all of them."""
        if t.name not in self.iface_name or not self.eff.get(t.name):
            return False
        wanted = set(self.eff[t.name])
        return not any(all(a.name != t.name for a in self.ancestors(c)) and wanted <= set(self.eff.get(c.name, {}))
                       for c in concrete)

    def ancestors(self, t):
        """The type itself, then its parent, and so on."""
        out = []
        while t is not None:
            out.append(t)
            t = self.types.get(t.parent)
        return out

    def implemented_interfaces(self, t):
        return [(self.iface_name[a.name], t.line) for a in self.ancestors(t) if a.name in self.iface_name]

    @staticmethod
    def passed_first(params, b):
        """The parameters with the one that receives the object first (it is the first unless `pass(arg)` says so)."""
        names = [q[1] for q in params]
        k = names.index(b.pass_arg) if b.pass_arg in names else 0
        return [params[k]] + params[:k] + params[k + 1:]

    def binding_signature(self, t, b, origin, fn, iname):
        """The Function (no body) an interface lists for the binding `b` of type `t`: from the procedure that
        implements it, or from the abstract interface a deferred binding names."""
        if fn is not None:
            params = self.passed_first(fn.params, b)
            first = params[0][2] or "in"
            return Function(b.name, [[iname, "self", first]] + [list(p) for p in params[1:]], fn.ret, [], fn.is_sub)
        pt = self.find_interface(self.types[origin].scope, b.iface)
        if pt is None:
            return None
        first = pt.params[0][1] if pt.params[0][1] in ("in", "inout", "out") else "in"
        names = list(pt.names[1:]) if pt.names else []
        params = [[iname, "self", first]]
        for k, (typ, intent) in enumerate(pt.params[1:]):
            params.append([typ, names[k] if k < len(names) and names[k] else f"arg{k + 1}",
                           intent if intent in ("in", "inout", "out") else "in"])
        return Function(b.name, params, pt.ret, [], pt.is_sub)

    def attach_bindings(self, unit):
        """Give each record the functions its type-bound procedures run (copies of the procedures that implement
        them, so `self` is the record itself and dynamic dispatch picks the right one), and give each interface
        the signatures of its type's procedures. Runs after the other passes so the copies carry their results."""
        import copy

        def place(t, what):
            return unit.modules[t.module.name].__dict__[what] if t.module is not None else getattr(unit, what)

        copied = {}
        for t in self.types.values():
            if not t.ok or t.wrapper is not None:
                continue
            rec = place(t, "records").get(t.name)
            idef = place(t, "interfaces").get(self.iface_name[t.name]) if t.name in self.iface_name else None
            problems = []
            for bname, (b, origin) in self.eff.get(t.name, {}).items():
                otype = self.types[origin]
                fn = None
                if b.impl is not None:
                    pool = place(otype, "functions") if otype.module is not None else unit.functions
                    fn = pool.get(b.impl)
                    if fn is None:
                        problems.append(f"procedure '{b.impl}' of '{bname}' is not defined where the type is")
                        continue
                    needs = []
                    if otype.module is not t.module:
                        why, needs = self.foreign_names(unit, fn, b, otype, t)
                        if why:
                            problems.append(f"'{bname}' is inherited from module '{otype.module.name}', and its "
                                            f"implementation '{b.impl}' {why}")
                            continue
                elif not t.abstract:
                    problems.append(f"the deferred procedure '{bname}' is never implemented")
                    continue
                if fn is not None and self.converted.get(fn.name, (None,))[0] is fn:
                    problems.append(f"'{bname}' is a function that changes its arguments or module variables, so it became "
                                    f"a subroutine; calls through a record are not rewritten")
                    continue
                if idef is not None:
                    sig = self.binding_signature(t, b, origin, fn, idef.name)
                    if sig is None:
                        problems.append(f"the interface '{b.iface}' of '{bname}' could not be read")
                    else:
                        idef.functions[bname] = sig
                if rec is None or fn is None:
                    continue
                if any(d.name == bname for d in rec.fields):
                    problems.append(f"'{bname}' is both a component and a procedure")
                    continue
                f = copy.deepcopy(fn)
                if b.pass_arg is not None and b.pass_arg not in [q[1] for q in f.params]:
                    problems.append(f"'{bname}' says pass({b.pass_arg}), but '{b.impl}' has no argument of that name")
                    continue
                f.params = self.passed_first(f.params, b)
                old = f.params[0][1]
                if any(isinstance(d, Decl) and d.name == "self" for d in iter_nodes(f.body)) or \
                        (old != "self" and any(q[1] == "self" for q in f.params[1:])):
                    problems.append(f"'{bname}' has a variable called 'self'")
                    continue
                f.name, f.public = bname, False
                f.params[0] = [t.name, "self", f.params[0][2] or "in"]
                if old != "self":
                    for n in iter_nodes(f.body):
                        if isinstance(n, (Var, Apply, Call, Allocate, Deallocate)) and n.name == old:
                            n.name = "self"
                rec.functions[bname] = f
                self.add_imports(unit, t, needs)
                self.method_functions.append(f)
                self.method_home[id(f)] = unit.modules[t.module.name] if t.module is not None else None
                self.method_origin[id(f)] = (origin, b.impl)
                copied[(origin, b.impl)] = (bname, t.name)
            if problems:
                if rec is not None:
                    rec.implements = []                    # it cannot honestly say it fits the interface
                for why in problems:
                    self.todo(t.line, f"type '{t.name}': {why}")
        owners = [(f, f.body) for f in self.all_functions(unit)] + [(f, f.body) for f in self.method_functions]
        if unit.program:
            owners.append((unit.program, unit.program.body))
        flagged = set()
        for (origin, impl), (bname, tname) in copied.items():       # the procedure itself is gone unless something uses it
            otype = self.types[origin]
            pool = unit.modules[otype.module.name].functions if otype.module is not None else unit.functions
            fn = pool.get(impl)
            if fn is None:
                continue
            hits = []
            for owner, body in owners:
                if owner is fn or self.method_origin.get(id(owner)) == (origin, impl):
                    continue                                        # its own body (or a copy of it)
                for n in iter_nodes(body):
                    if isinstance(n, (Apply, Var)) and n.name == impl or \
                            isinstance(n, Call) and n.receiver is None and n.name == impl:
                        hits.append((owner, getattr(n, "line", 0) or self.fn_lines.get(id(owner), 0)))
            if not hits:
                del pool[impl]
                continue
            msg = (f"'{impl}' implements the type-bound procedure '{bname}' of '{tname}' and is also used by name; "
                   f"Boreas has it as a function of the record, so call it as x.{bname}(...)")
            fn.comments.append("TODO(convert): " + msg)
            self.todo(self.fn_lines.get(id(fn), 0), msg)
            for owner, line in hits:
                if (id(owner), impl) not in flagged:
                    flagged.add((id(owner), impl))
                    if hasattr(owner, "comments"):
                        owner.comments.append("TODO(convert): " + msg)
                    self.todo(line, msg)

    def foreign_names(self, unit, fn, b, otype, t):
        """An inherited procedure is copied into a record of another module, so its body runs where the parent's
        module is not in scope. Every name it uses must still be reachable there: returns (why not, or None;
        the imports the copy needs as (module, name))."""
        home = unit.modules[otype.module.name]
        mine = unit.modules[t.module.name] if t.module is not None else None
        impl = b.impl
        local = {q[1] for q in fn.params} | {impl}
        for n in iter_nodes(fn.body):
            if isinstance(n, Decl):
                local.add(n.name)
            elif isinstance(n, (Do, SelectType, Gen)):
                local.add(getattr(n, "var", None))
        refs = self.references(fn.body) | {x for q in self.passed_first(fn.params, b)[1:] for x in type_names(q[0])}
        refs |= {base_type(fn.ret)} if fn.ret else set()
        imported = {n: u.module for u in fn.uses + home.uses for n in u.names}
        own = {x.name for x in (mine.decls if mine is not None else [])} | set(mine.functions if mine else {}) \
            | set(mine.records if mine else {}) | set(mine.interfaces if mine else {})
        here = {n: u.module for u in (mine.uses if mine is not None else unit.program.uses if unit.program else [])
                for n in u.names}
        private, clash, needs = [], [], []
        for n in sorted(refs - local - set(BUILTINS) - set(ELEMENTAL)):
            if n in imported:
                source, public = imported[n], True
            elif n in home.functions:
                source, public = home.name, home.functions[n].public
            elif n in home.records:
                source, public = home.name, home.records[n].public
            elif n in home.interfaces:
                source, public = home.name, home.interfaces[n].public
            elif any(d.name == n for d in home.decls):
                source, public = home.name, next(d.public for d in home.decls if d.name == n)
            else:
                continue                                         # not something defined in a module
            if not public:
                private.append(n)
            elif n in own or here.get(n, source) != source:
                clash.append(n)
            else:
                needs.append((source, n))
        if private:
            return (f"uses {', '.join(repr(n) for n in private)}, private to module '{home.name}', which a copy in "
                    f"another module cannot see"), []
        if clash:
            return (f"uses {', '.join(repr(n) for n in clash)}, a name this module already has for something else"), []
        return None, needs

    def add_imports(self, unit, t, needs):
        """Make the names a copied procedure uses importable where the copy now lives."""
        target = unit.modules[t.module.name].uses if t.module is not None else unit.program.uses
        for module, name in needs:
            use = next((u for u in target if u.module == module), None)
            if use is None:
                target.append(Use(module, [name], t.line))
            elif name not in use.names:
                use.names.append(name)

    def all_functions(self, unit):
        return list(unit.functions.values()) + [f for m in unit.modules.values() for f in m.functions.values()]

    def build_module(self, ms):
        consts = []
        mod = Module(ms.name, [], consts, {}, ms.line, list(ms.comments))
        mod.comments += [c.text for c in ms.body or [] if isinstance(c, Comment) and c.text.startswith("TODO(convert)")]
        for name, info in ms.decls.items():
            if name in ms.externals:
                continue
            if name in ms.skipped:
                continue
            consts.append(Decl(self.info_type(info), name, info.dims, info.init, info.line, info.parameter,
                               self.is_public(ms, name), allocatable=info.allocatable))
        return mod

    def references(self, nodes):
        """Names used in `nodes`: variables, calls, constructors, and the types of declarations."""
        out = set()
        for n in iter_nodes(nodes):
            if isinstance(n, Case) and isinstance(n.values, str):
                out.add(n.values)                            # select type: `case circle`
                continue
            if isinstance(n, IsType):
                out.add(n.type)                              # `x is circle as c`
            if isinstance(n, Call) and n.receiver is not None:
                continue                                     # call x.grow(2.0): `grow` belongs to the record
            if isinstance(n, (Var, Apply, Call, Allocate, Deallocate)):
                out.add(n.name)
            elif isinstance(n, Decl):
                out.add(n.type)
        return out

    def make_uses(self, entries, refs):
        """Fortran `use` entries -> explicit Boreas imports. `use m` (everything) becomes the
        names this code actually refers to; names a module re-exports come from where they are defined."""
        merged = {}
        for modname, names, line in entries:
            vis = self.visible(self.modules[modname])
            wanted = [n for n in vis if n in refs] if names is None else [n for n in names if n in vis]
            for n in wanted:
                use = merged.setdefault(vis[n], Use(vis[n], [], line))
                if n not in use.names:
                    use.names.append(n)
        return list(merged.values())

    def procedure_uses(self, owner, scope, refs):
        """The imports of a procedure: its own `use` statements, plus what the program or procedure it was written
        inside imported (an internal procedure sees those), when it uses the name and has no variable of that name."""
        own = self.make_uses(scope.uses, refs)
        inherited, host = [], scope.host
        while host is not None and host.kind != "module":
            inherited += [u for u in host.uses if u not in inherited]
            host = host.host
        if not inherited:
            return own
        mine = {d.name for d in iter_nodes(owner.body) if isinstance(d, Decl)} | {p[1] for p in owner.params}
        wanted = refs - mine
        inherited = [(mod, names if names is None else [n for n in names if n in wanted], line)
                     for mod, names, line in inherited]
        for use in self.make_uses([u for u in inherited if u[1] is None or u[1]], wanted):
            same = next((u for u in own if u.module == use.module), None)
            if same is None:
                own.append(use)
            else:
                same.names += [n for n in use.names if n not in same.names]
        return own

    def link_uses(self, unit, built):
        by_module = {}
        for owner, scope in built:
            if isinstance(owner, Program):
                owner.uses = self.make_uses(scope.uses, self.references(owner.body))
            else:
                sig = {n for p in owner.params for n in type_names(p[0])} | ({base_type(owner.ret)} if owner.ret else set())
                owner.uses = self.procedure_uses(owner, scope, self.references(owner.body) | sig)
                if scope.module is not None:
                    by_module.setdefault(scope.module.name, []).append(owner)
        for name, ms in self.modules.items():
            mod = unit.modules[name]
            refs = self.references([d.init for d in mod.decls]) | {d.type for d in mod.decls}
            for r in mod.records.values():
                refs |= self.references([fl.init for fl in r.fields]) | {fl.type for fl in r.fields}
                refs |= {name for name, _ in r.implements}           # `record bird is animal_any` needs animal_any in scope
            for f in by_module.get(name, []):
                refs |= self.references(f.body) | {n for p in f.params for n in type_names(p[0])} | ({base_type(f.ret)} if f.ret else set())
            mod.uses = self.make_uses(ms.uses, refs)

    def visible_procs(self, unit, f, mod=UNSET):
        """The procedures `f` can call: its siblings plus whatever it (or its module) imports."""
        if mod is UNSET:
            mod = next((m for m in unit.modules.values() if m.functions.get(f.name) is f), None)
        procs = dict(mod.functions if mod else unit.functions)
        for use in f.uses + (mod.uses if mod else []):
            src = unit.modules[use.module].functions
            procs.update({n: src[n] for n in use.names if n in src})
        return procs

    def local_decls(self, scope, body, proc_names=()):
        decls = []
        for name, info in scope.decls.items():
            if name in scope.dummies:
                continue
            if name in proc_names or name in scope.externals:
                self.note(info.line, f"declaration of '{name}' dropped: it is a procedure, not a variable")
                continue
            decls.append(Decl(self.info_type(info), name, info.dims, info.init, info.line, info.parameter,
                              allocatable=info.allocatable))
        return decls

    def implicit_decls(self, scope, body, proc_names, line):
        known = set(scope.decls) | set(scope.dummies) | proc_names | self.imported_names(scope)
        known |= {n.var for n in iter_nodes(body) if isinstance(n, (SelectType, IsType)) and n.var}   # select type (p => x)
        if scope.module is not None:
            known |= set(scope.module.decls)
        host_names = set()
        if scope.host is not None and scope.host.kind != "module":
            h = scope.host
            host_names = set(h.decls) | set(h.dummies)
            host_names |= {n.name for n in iter_nodes(h.body) if isinstance(n, Var)}
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
            if name in host_names:
                msg = (f"'{name}' belongs to the enclosing program; in Boreas a procedure cannot see "
                       f"it, so pass it as an argument")
                self.todo(line, msg)
                scope.comments.append("TODO(convert): " + msg)
                continue
            if scope.implicit_none:
                self.note(line, f"'{name}' is not declared (the source says 'implicit none')")
                continue
            typ = implicit_type(name)
            self.note(line, f"'{name}' had no declaration; declared as {typ} using Fortran's implicit rules")
            out.append(Decl(typ, name, None, None, line))
        return out

    def imported_names(self, scope):
        names, s = set(), scope
        while s is not None:
            for modname, listed, _ in s.uses:
                names |= set(self.visible(self.modules[modname])) if listed is None else set(listed)
            s = s.host
        return names

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
            ret = self.info_type(info)
            if info.dims:
                ret = self.array_result(s, result, info, body, line)
            self.fix_returns(body, result)
            body.append(Return(Var(result)))
        decls = self.implicit_decls(s, body, proc_names | {s.name}, line)
        params = []
        for d in s.dummies:
            info = s.decls.get(d)
            if d in s.proc_dummies:
                params.append([s.proc_dummies[d], d, "in"])
            elif d in s.externals:
                placeholder, body_is_sub, counts = self.external_placeholder(s, d, body)
                params.append([placeholder, d, "in"])
                self.external_entries[id(params[-1])] = (params[-1], line, s.name, body_is_sub, counts)
            elif info is None:
                typ = implicit_type(d)
                if s.implicit_none:
                    self.note(line, f"dummy argument '{d}' has no declaration")
                else:
                    self.note(line, f"dummy argument '{d}' had no declaration; assumed {typ}")
                params.append([typ, d, None])
            elif info.optional and (info.dims is not None or info.shape_dropped or info.allocatable):
                msg = f"argument '{d}' is an optional array; Boreas optional arguments are single values"
                self.todo(line, msg)
                s.comments.append("TODO(convert): " + msg)
                params.append([self.dummy_type(info), d, info.intent])
            else:
                params.append([self.dummy_type(info) + ("?" if info.optional else ""), d, info.intent])
        fn = Function(s.name, params, ret, decls + self.local_decls(s, body, proc_names) + body, is_sub, list(s.comments))
        return fn

    def info_type(self, info):
        """The Boreas type of a declaration: class(T) is the interface that stands for it (see oo_prepare)."""
        return self.iface_name.get(info.type, info.type) if info.poly else info.type

    def dummy_type(self, info):
        """A dummy argument's Boreas type: `real[,]` for a two-dimensional array (its rank is all Boreas keeps)."""
        if info.shape_dropped and info.rank:
            return self.info_type(info) + "[" + "," * (info.rank - 1) + "]"
        return self.info_type(info)

    def array_result(self, scope, result, info, body, line):
        """A function result that is an array: `-> real[]` (one comma more per extra dimension). The result is
        an allocatable local; a fixed-size one is allocated at the top of the body, with the size Fortran gave."""
        rank = len(info.dims)
        if not info.allocatable:
            extents = info.dims
            info.dims, info.allocatable = [Slice(None, None) for _ in extents], True
            body.insert(0, Allocate(result, extents, line))
            self.note(line, f"array result '{result}' of '{scope.name}' is allocated at the start of the function")
        return info.type + "[" + "," * (rank - 1) + "]"

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
            elif isinstance(s, Select):
                for c in s.cases:
                    self.fix_returns(c.body, result)

    # -- parameter modes
    def module_variables_seen_by(self, unit, owner):
        """Names of the module variables (not constants) that `owner` can see: its module's own, and the
        ones it imports."""
        mod = next((m for m in unit.modules.values() if m.functions.get(getattr(owner, "name", None)) is owner), None)
        names = {d.name for d in mod.decls if not d.const} if mod else set()
        for use in owner.uses + (mod.uses if mod else []):
            src = unit.modules[use.module]
            names |= {d.name for d in src.decls if not d.const and d.name in use.names}
        own = {d.name for d in owner.body if isinstance(d, Decl)} | {p[1] for p in getattr(owner, "params", [])}
        return names - own

    def module_variable_writes(self, owner, names):
        """The assignments and reads in `owner` that change one of the module variables `names`."""
        found = []
        for n in iter_nodes(owner.body):
            if isinstance(n, Assign) and root_name(n.target) in names:
                found.append(n)
            elif isinstance(n, (Allocate, Deallocate)) and n.name in names:
                found.append(n)
        return found

    def mark_module_writes(self, unit):
        for owner in self.all_functions(unit) + ([unit.program] if unit.program else []):
            names = self.module_variables_seen_by(unit, owner)
            if names:
                for n in self.module_variable_writes(owner, names):
                    n.update = True

    def infer_intents(self, unit):
        """Dummy arguments without `intent` get one from how the body uses them."""
        funcs = self.all_functions(unit)
        for f in funcs:
            self.localize_loop_counters(f)
        self.module_writers = {id(f) for f in funcs
                               if self.module_variable_writes(f, self.module_variables_seen_by(unit, f))}
        visible = {id(f): self.visible_procs(unit, f) for f in funcs}
        inferred = {(id(f), p[1]) for f in funcs for p in f.params if p[2] is None}
        for _ in range(10):                        # callers depend on callees: iterate to a fixed point
            changed = False
            for f in funcs:
                for p in f.params:
                    if (id(f), p[1]) not in inferred:
                        continue
                    reads, writes, sure = self.usage(f.body, p[1], visible[id(f)])
                    new = "in" if not writes else ("out" if sure and not reads else "inout")
                    if p[2] != new:
                        p[2], changed = new, True
            if not changed:
                break
        for f in funcs:
            line = self.fn_lines.get(id(f), 0)
            for p in f.params:
                if p[2] is None:
                    p[2] = "in"
                if (id(f), p[1]) in inferred and p[2] != "in":
                    f.comments.append(f"note: argument '{p[1]}' has no intent in the Fortran; "
                                      f"'{p[2]}' was inferred from how the body uses it")
                    self.note(line, f"{f.name}: argument '{p[1]}' had no intent; inferred '{p[2]}'")
        self.converted = self.functions_to_subroutines(funcs)
        for f in funcs:                            # Boreas optional arguments are read-only
            for p in f.params:
                if is_optional(p[0]) and p[2] != "in":
                    p[0] = p[0][:-1]
                    msg = (f"argument '{p[1]}' is optional and modified; Boreas optional arguments are "
                           f"read-only, so it became a required '{p[2]}' argument")
                    f.comments.append("TODO(convert): " + msg)
                    self.todo(self.fn_lines.get(id(f), 0), msg)
        for f in funcs:                            # an out record must be completely assigned in Boreas
            for p in f.params:
                t = self.types.get(p[0])
                if p[2] == "out" and t is not None and t.ok:
                    need = {fl.name for fl in t.fields if fl.init is None}
                    written = self.fields_written(f.body, p[1])
                    if written is not True and not need <= written:
                        p[2] = "inout"
                        f.comments.append(f"note: argument '{p[1]}' is intent(out) but the body does not assign "
                                          f"every field; it is 'inout' in Boreas")
                        self.note(self.fn_lines.get(id(f), 0), f"{f.name}: intent(out) record '{p[1]}' "
                                  f"is not fully assigned; made inout")
        self.reconcile_callbacks(unit, funcs)
        for f in funcs:                            # writes that reach the caller are marked `@`
            modes = {p[1]: p[2] for p in f.params}
            for n in iter_nodes(f.body):
                if isinstance(n, Assign) and modes.get(root_name(n.target)) in ("out", "inout"):
                    n.update = True
                elif isinstance(n, Do) and n.var in modes:
                    msg = f"loop variable '{n.var}' is a dummy argument; Boreas loop variables must be locals"
                    f.comments.append("TODO(convert): " + msg)
                    self.todo(n.line, msg)

    def callback_sites(self, unit, funcs):
        """Every call that passes a bare name to a procedure parameter: (owner, callee, index, the Var)."""
        owners = [(f, self.visible_procs(unit, f)) for f in funcs]
        if unit.program:
            procs = dict(unit.functions)
            for use in unit.program.uses:
                src = unit.modules[use.module].functions
                procs.update({n: src[n] for n in use.names if n in src})
            owners.append((unit.program, procs))
        sites = []
        for owner, procs in owners:
            for n in iter_nodes(owner.body):
                if isinstance(n, (Call, Apply)) and n.name in procs:
                    callee = procs[n.name]
                    for i, a in enumerate(n.args):
                        if isinstance(a, Var) and i < len(callee.params) and isinstance(callee.params[i][0], ProcType):
                            sites.append((owner, procs, callee, i, a))
        return sites

    def add_intrinsic_wrappers(self, unit):
        """An intrinsic passed to a procedure parameter (`call integrate(sin, a, b)`) cannot be passed as
        itself, so a small function with the parameter's signature calls it, and that is passed."""
        if not self.intrinsic_args:
            return
        funcs = self.all_functions(unit)
        owners = [(f, self.visible_procs(unit, f)) for f in funcs]
        if unit.program:
            procs = dict(unit.functions)
            for use in unit.program.uses:
                src = unit.modules[use.module].functions
                procs.update({n: src[n] for n in use.names if n in src})
            owners.append((unit.program, procs))
        done, made = set(), {}
        for owner, procs in owners:
            mod = next((m for m in unit.modules.values() if m.functions.get(owner.name) is owner), None)
            home = mod.functions if mod is not None else unit.functions
            for n in iter_nodes(owner.body):
                if not isinstance(n, (Call, Apply)):
                    continue
                for i, a in enumerate(n.args):
                    if id(a) not in self.intrinsic_args or id(a) in done:
                        continue
                    done.add(id(a))
                    callee = procs.get(n.name)
                    want = callee.params[i][0] if callee is not None and i < len(callee.params) else None
                    if not isinstance(want, ProcType) or want.is_sub:
                        self.todo(self.intrinsic_args[id(a)][1],
                                  f"the intrinsic '{a.name}' is passed to '{n.name}', whose parameter is not a "
                                  f"function with a known signature, so it cannot be wrapped")
                        continue
                    generic = SPECIFIC_NAMES.get(a.name, a.name)
                    key = (id(home), generic, want)
                    if key not in made:
                        wrapper = generic + "_fn"
                        while wrapper in home or wrapper in unit.functions:
                            wrapper += "_"
                        names = [(nm or f"x{k + 1}") for k, nm in enumerate(want.names or (None,) * len(want.params))]
                        made[key] = home[wrapper] = Function(
                            wrapper, [[t, nm, "in"] for (t, _), nm in zip(want.params, names)], want.ret,
                            [Return(Apply(generic, [Var(nm) for nm in names], 0))], False,
                            [f"note: calls the intrinsic '{generic}' so that it can be passed as a procedure"])
                        self.note(self.intrinsic_args[id(a)][1], f"the intrinsic '{a.name}' is passed through the "
                                  f"function '{wrapper}', which calls it")
                    a.name = made[key].name
        for id_, (node, line) in self.intrinsic_args.items():
            if id_ not in done:
                self.todo(line, f"the intrinsic '{node.name}' is passed as an argument, which is not translated")

    def external_placeholder(self, s, d, body):
        """A dummy declared `external` has no signature in the Fortran; this records what the body shows
        (a function used as a value or a subroutine called) until the callers have been looked at."""
        uses = [n for n in iter_nodes(body) if isinstance(n, (Call, Apply)) and n.name == d]
        is_sub = None
        if any(isinstance(n, Call) for n in uses):
            is_sub = True
        elif uses:
            is_sub = False
        info = s.decls.get(d)
        ret = None if is_sub is not False else (info.type if info is not None else implicit_type(d))
        return ProcType(is_sub, (), ret, ()), is_sub, {len(n.args) for n in uses}

    def resolve_externals(self, unit, funcs):
        """Give every `external` dummy the signature of the procedures that callers pass to it (D45).
        A TODO when no caller passes a procedure whose signature is known, or when callers disagree."""
        if not self.external_entries:
            return
        pending = {k: v for k, v in self.external_entries.items()}
        found = {}                                  # id(entry) -> signatures seen
        sites = self.callback_sites(unit, funcs)
        for _ in range(3):                          # a dummy passed on to another one settles in a few rounds
            for owner, procs, callee, i, arg in sites:
                entry = callee.params[i]
                if id(entry) not in pending:
                    continue
                passed = procs.get(arg.name)
                if passed is not None:
                    sig = signature_of(passed)
                else:
                    dummy = next((p for p in getattr(owner, "params", []) if p[1] == arg.name
                                  and isinstance(p[0], ProcType)), None)
                    if dummy is None or id(dummy) in pending:
                        continue
                    sig = dummy[0]
                found.setdefault(id(entry), [])
                if sig not in found[id(entry)]:
                    found[id(entry)].append(sig)
            for k, sigs in found.items():
                if len(sigs) == 1:
                    self.external_entries[k][0][0] = sigs[0]
                    pending.pop(k, None)
        for k, (entry, line, proc, body_is_sub, counts) in self.external_entries.items():
            name = entry[1]
            sigs = found.get(k, [])
            if len(sigs) > 1:
                msg = (f"'{name}' of '{proc}' is passed procedures with different signatures "
                       f"({' and '.join(str(x) for x in sigs)}); Boreas needs one")
            elif not sigs:
                msg = (f"'{name}' of '{proc}' is an external procedure argument and no call passes a procedure "
                       f"whose signature is known, so its signature cannot be written")
            else:
                sig = sigs[0]
                bad = [n for n in counts if n != len(sig.params)]
                if body_is_sub not in (None, sig.is_sub):
                    msg = f"'{name}' of '{proc}' is used as a {'function' if sig.is_sub else 'subroutine'} but is passed {sig}"
                elif bad:
                    msg = f"'{name}' of '{proc}' is called with {bad[0]} arguments but is passed {sig}"
                else:
                    continue
            f = next((x for x in funcs if x.name == proc), None)
            if f is not None:
                f.comments.append("TODO(convert): " + msg)
            self.todo(line, msg)

    def reconcile_callbacks(self, unit, funcs):
        """An interface block often leaves argument intents out, but Boreas matches a passed procedure's
        signature exactly. Each unspecified intent (`?`) takes the intent of the procedures actually passed;
        what nothing decides becomes `in`. A mismatch that remains is a TODO."""
        self.resolve_externals(unit, funcs)
        sites = self.callback_sites(unit, funcs)
        for _ in range(3):                         # a callback passed on to another one settles in a few rounds
            for owner, procs, callee, i, arg in sites:
                want = callee.params[i][0]
                passed = procs.get(arg.name)
                if passed is not None:
                    got = signature_of(passed)
                else:
                    dummy = next((p[0] for p in getattr(owner, "params", []) if p[1] == arg.name
                                  and isinstance(p[0], ProcType)), None)
                    if dummy is None:
                        continue
                    got = dummy
                if got.is_sub != want.is_sub or len(got.params) != len(want.params):
                    continue
                new_want = self.adopt_intents(want, got)
                if new_want != want:
                    callee.params[i][0] = new_want
                if passed is None:
                    new_got = self.adopt_intents(got, new_want)
                    for p in owner.params:
                        if p[1] == arg.name:
                            p[0] = new_got
        for f in funcs:
            for p in f.params:
                if isinstance(p[0], ProcType) and any(i == "?" for _, i in p[0].params):
                    p[0] = ProcType(p[0].is_sub, tuple((t, "in" if i == "?" else i) for t, i in p[0].params),
                                    p[0].ret, p[0].names)
        for owner, procs, callee, i, arg in sites:
            passed = procs.get(arg.name)
            if passed is not None and signature_of(passed) != callee.params[i][0]:
                self.todo(self.fn_lines.get(id(owner), 0),
                          f"'{arg.name}' passed to '{callee.name}' is {signature_of(passed)}, but '{callee.params[i][1]}' "
                          f"is {callee.params[i][0]}")

    @staticmethod
    def adopt_intents(sig, other):
        """`sig` with each unspecified intent (`?`) replaced by the one `other` has there."""
        if not any(i == "?" for _, i in sig.params):
            return sig
        params = tuple((t, o[1] if i == "?" and o[1] != "?" else i) for (t, i), o in zip(sig.params, other.params))
        return ProcType(sig.is_sub, params, sig.ret, sig.names)

    def localize_loop_counters(self, f):
        """`do 10 info = 1, n` where `info` is a dummy argument: the loop counts with a new local, and
        the dummy gets the counter's value wherever the Fortran would have left it there."""
        dummies = {p[1]: p[0] for p in f.params if p[2] != "in"}
        counters = {}
        while True:
            found = self.counter_loop(f.body, dummies, [])
            if found is None:
                return
            trail = found
            lst, idx = trail[-1]
            loop = lst[idx]
            name, line = loop.var, loop.line
            needed = self.read_after(trail, name)            # before the declaration moves the indexes
            if name not in counters:
                counters[name] = self.unique_name(f, name + "_i")
                f.body.insert(0, Decl(dummies[name], counters[name], None, None, line))
            local = counters[name]
            for n in iter_nodes(loop.body):
                if isinstance(n, Var) and n.name == name:
                    n.name = local
            loop.var = local
            copy = lambda: Assign(Var(name), Var(local), line, True)
            leaving = self.copy_before_leaving(loop.body, copy, needed, ())
            final = self.final_counter(loop, name, leaving) if needed else None
            if needed and final is not None:
                lst.insert(idx + 1, Assign(Var(name), final, line, True))
            elif needed:
                msg = (f"the final value of '{name}' after the loop is not reproduced; "
                       f"Boreas loop variables must be locals")
                f.comments.append("TODO(convert): " + msg)
                self.todo(line, msg)

    def unique_name(self, f, base):
        names = {n.name for n in iter_nodes(f.body) if isinstance(n, (Var, Apply, Decl))} | {p[1] for p in f.params}
        while base in names:
            base += "_"
        return base

    def counter_loop(self, stmts, dummies, trail):
        """The first `do` whose variable is one of `dummies`, as a trail of (list, index) pairs."""
        for i, s in enumerate(stmts):
            here = trail + [(stmts, i)]
            if isinstance(s, Do) and s.var in dummies:
                return here
            for sub in child_lists(s):
                found = self.counter_loop(sub, dummies, here)
                if found is not None:
                    return found
        return None

    @staticmethod
    def read_after(trail, name):
        """Can the value of `name` after the loop at the end of `trail` be seen, by the caller or by the code?"""
        for depth in range(len(trail) - 1, -1, -1):
            lst, idx = trail[depth]
            for s in lst[idx + 1:]:
                if isinstance(s, Assign) and isinstance(s.target, Var) and s.target.name == name:
                    return any(isinstance(n, Var) and n.name == name for n in iter_nodes(s.value))
                if any(isinstance(n, (Var, Return)) and getattr(n, "name", None) in (name, None)
                       for n in iter_nodes(s)):
                    return True
            if depth and isinstance(trail[depth - 1][0][trail[depth - 1][1]], (Do, DoWhile)):
                return True                      # the enclosing loop comes round again
        return True                              # the end of the procedure: the caller sees it

    def copy_before_leaving(self, stmts, copy, needed, inner):
        """Put `copy()` before each return, and (when `needed`) before each exit that leaves the loop.
        Returns how many exits leave it."""
        out, leaving = [], 0
        for s in stmts:
            leaves_loop = isinstance(s, Exit) and (
                (s.label is None and not inner) or (s.label is not None and s.label not in inner))
            if isinstance(s, Return) or (leaves_loop and needed):
                out.append(copy())
            leaving += leaves_loop
            if not isinstance(s, (Do, DoWhile)):
                subs = child_lists(s)
                for sub in subs:
                    leaving += self.copy_before_leaving(sub, copy, needed, inner)
            else:
                leaving += self.copy_before_leaving(s.body, copy, needed, inner + (s.label,))
            out.append(s)
        stmts[:] = out
        return leaving

    def offset(self, e, by):
        n = self.int_literal(e)
        if n is not None:
            return Num(n + by)
        return Binary("+" if by > 0 else "-", e, Num(abs(by)))

    def final_counter(self, loop, name, leaving):
        """The value a Fortran loop variable has when the loop runs to its end, or None when unknown."""
        if leaving:
            return None
        written = {root_name(n.target) for n in iter_nodes(loop.body) if isinstance(n, Assign)}
        used = {n.name for e in (loop.lo, loop.hi, loop.step) for n in iter_nodes(e) if isinstance(n, Var)}
        if name in used or used & written:
            return None
        step = 1 if loop.step is None else self.int_literal(loop.step)
        if step == 1:
            return Apply("max", [loop.lo, self.offset(loop.hi, 1)], 0)
        if step == -1:
            return Apply("min", [loop.lo, self.offset(loop.hi, -1)], 0)
        return None

    def functions_to_subroutines(self, funcs):
        """A Fortran function that changes its arguments becomes a subroutine whose last parameter is
        the result (`out`); its calls are rewritten later by hoist_calls. Returns {name: (function, type)}."""
        converted = {}
        for f in funcs:
            if f.is_sub or (all(p[2] == "in" for p in f.params) and id(f) not in self.module_writers):
                continue
            last = f.body[-1] if f.body else None
            if not (isinstance(last, Return) and isinstance(last.value, Var)):
                continue
            result, ret = last.value.name, f.ret
            if ret is not None and ret.endswith("]"):
                what = "its arguments" if any(p[2] != "in" for p in f.params) else "module variables"
                msg = f"function '{f.name}' returns an array and changes {what}; it cannot become a subroutine"
                self.todo(self.fn_lines.get(id(f), 0), msg)
                f.body = [Comment("TODO(convert): " + msg)]       # the body would not be valid in a function
                continue
            f.body = [d for d in f.body[:-1] if not (isinstance(d, Decl) and d.name == result)]
            for n in iter_nodes(f.body):
                if isinstance(n, Return) and isinstance(n.value, Var) and n.value.name == result:
                    n.value = None
            f.params.append([ret, result, "out"])
            f.is_sub, f.ret = True, None
            what = "its arguments" if any(p[2] != "in" for p in f.params[:-1]) else "module variables"
            f.comments.append(f"note: this Fortran function changes {what}, so it is a subroutine here; "
                              f"its result is the out parameter '{result}'")
            self.note(self.fn_lines.get(id(f), 0), f"function '{f.name}' changes {what}: it became a "
                      f"subroutine with the result as the out parameter '{result}', and its calls were rewritten")
            converted[f.name] = (f, ret)
        return converted

    def hoist_calls(self, unit):
        """Calls of the converted functions sit inside expressions; a subroutine call is a statement, so each
        call moves in front of its statement and leaves a variable behind."""
        if not self.converted:
            return
        for owner in self.all_functions(unit) + ([unit.program] if unit.program else []):
            self.hoist_in(owner)

    def hoist_in(self, owner):
        types = {d.name: d.type for d in owner.body if isinstance(d, Decl)}
        arrays = {d.name for d in owner.body if isinstance(d, Decl) and d.dims is not None}
        params = getattr(owner, "params", [])
        types.update({p[1]: p[0].rstrip("?") for p in params if isinstance(p[0], str)})
        used = {n.name for n in iter_nodes(owner.body) if isinstance(n, (Var, Apply, Decl))}
        used |= {p[1] for p in params}
        temps = []

        def new_temp(base, typ, line):
            name = base + "_value"
            while name in used:
                name += "_"
            used.add(name)
            types[name] = typ
            temps.append(Decl(typ, name, None, None, line))
            return name

        class Unhoistable(Exception):
            pass

        def effectful(e):
            return any(isinstance(n, Apply) and n.name in self.converted and n.name not in arrays
                       for n in iter_nodes(e))

        def take(e, pre, line, guarded=False):
            if isinstance(e, list):
                return [take(x, pre, line, guarded) for x in e]
            if isinstance(e, tuple):
                return tuple(take(x, pre, line, guarded) for x in e)
            if not is_dataclass(e) or isinstance(e, type):
                return e
            if isinstance(e, Apply) and e.name in self.converted and e.name not in arrays:
                args = take(e.args, pre, line, guarded)
                if guarded:
                    raise Unhoistable("it is evaluated only conditionally (after and/or/??, or in a select arm)")
                tmp = new_temp(e.name, self.converted[e.name][1], line)
                pre.append(Call(e.name, args + [Var(tmp)], line))
                return Var(tmp)
            if isinstance(e, Binary) and e.op in ("and", "or", "??"):
                return Binary(e.op, take(e.left, pre, line, guarded), take(e.right, pre, line, True))
            if isinstance(e, SelectExpr):
                arms = [replace(a, value=take(a.value, pre, line, True)) for a in e.arms]
                return replace(e, subject=take(e.subject, pre, line, guarded), arms=arms)
            return replace(e, **{f.name: take(getattr(e, f.name), pre, line, guarded) for f in fields(e)})

        def todo(s, why):
            msg = f"a call of '{[n.name for n in iter_nodes(s) if isinstance(n, Apply) and n.name in self.converted][0]}' " \
                  f"cannot be moved in front of its statement: {why}"
            self.todo(getattr(s, "line", 0) or 0, msg)
            return Comment("TODO(convert): " + msg)

        def stmt_list(body):
            out = []
            for s in body:
                if isinstance(s, list):
                    out.append(stmt_list(s))
                else:
                    out.extend(one(s))
            return out

        def one(s):
            pre, line = [], getattr(s, "line", 0) or 0
            try:
                if isinstance(s, Assign):
                    v = s.value
                    if isinstance(v, Apply) and v.name in self.converted and v.name not in arrays \
                            and isinstance(s.target, Var) and types.get(s.target.name) == self.converted[v.name][1] \
                            and not any(isinstance(n, Var) and n.name == s.target.name for n in iter_nodes(v.args)) \
                            and not effectful(v.args):
                        return [Call(v.name, v.args + [s.target], s.line)]
                    target = take(s.target, pre, line)
                    return pre + [replace(s, target=target, value=take(s.value, pre, line))]
                if isinstance(s, Print):
                    args = take(s.args, pre, line)
                    return pre + [replace(s, args=args)]
                if isinstance(s, Call):
                    args = take(s.args, pre, line)
                    return pre + [replace(s, args=args)]
                if isinstance(s, Return) and s.value is not None:
                    return pre + [replace(s, value=take(s.value, pre, line))]
                if isinstance(s, ExprStmt):
                    return pre + [replace(s, expr=take(s.expr, pre, line))]
                if isinstance(s, If):
                    (first, body), rest = s.branches[0], s.branches[1:]
                    cond = take(first, pre, line)
                    notes = [todo(c, "it sits in an 'else if' condition") for c, _ in rest if effectful(c)]
                    branches = [(cond, stmt_list(body))] + [(c, stmt_list(b)) for c, b in rest]
                    return notes + pre + [replace(s, branches=branches, orelse=stmt_list(s.orelse))]
                if isinstance(s, Do):
                    lo, hi, step = (take(x, pre, line) for x in (s.lo, s.hi, s.step))
                    return pre + [replace(s, lo=lo, hi=hi, step=step, body=stmt_list(s.body))]
                if isinstance(s, DoWhile):
                    if effectful(s.cond):
                        return [todo(s.cond, "it is the condition of a 'do while', which is evaluated again each time"),
                                replace(s, body=stmt_list(s.body))]
                    return [replace(s, body=stmt_list(s.body))]
                if isinstance(s, Select):
                    subject = take(s.subject, pre, line)
                    cases = [replace(c, body=stmt_list(c.body)) for c in s.cases]
                    return pre + [replace(s, subject=subject, cases=cases)]
                if effectful(s):
                    return [todo(s, "this kind of statement is not handled"), s]
            except Unhoistable as why:
                return [todo(s, str(why)), s]
            return [s]

        owner.body = stmt_list(owner.body)
        if temps:
            at = max((n for n, d in enumerate(owner.body) if isinstance(d, Decl)), default=-1) + 1
            owner.body[at:at] = temps

    def check_saved(self, unit, built):
        """Fortran keeps `save`d locals (and ones initialised in their declaration) between calls;
        Boreas locals start fresh. That only matters if the procedure changes the variable and could
        read the old value, and if it runs more than once. When it does, the variable moves into a
        module of its own, so the state is kept and every write to it is marked `@`."""
        for fn, scope in built:
            if not isinstance(fn, Function):
                continue
            saved = dict(scope.saved)
            if scope.save_all is not None:
                for name, info in scope.decls.items():
                    if name not in scope.dummies and not info.parameter:
                        saved.setdefault(name, (scope.save_all, "the procedure has a bare save statement"))
            procs = self.visible_procs(unit, fn)
            keep = []
            for name, (line, why) in saved.items():
                info = scope.decls.get(name)
                if info is None or info.parameter or name in scope.dummies or name in (scope.name, scope.result):
                    continue
                _, writes, _ = self.usage(fn.body, name, procs)
                if not writes or self.overwritten_first(fn.body, name):
                    continue
                if self.called_once(unit, fn.name):
                    self.note(line, f"'{name}' ({why}) keeps its value between calls in Fortran; '{fn.name}' is "
                                    f"called only once, so a fresh start each call behaves the same")
                    continue
                decl = next((d for d in fn.body if isinstance(d, Decl) and d.name == name), None)
                if decl is None or self.references([decl.dims or [], decl.init]) - set(BUILTINS) - set(ELEMENTAL):
                    msg = (f"'{name}' ({why}) keeps its value between calls in Fortran, and this procedure changes "
                           f"it; Boreas locals start fresh on every call, so the converted procedure behaves "
                           f"differently (its declaration uses other names, so it was not moved to a module)")
                    self.todo(line, msg)
                    fn.comments.append("TODO(convert): " + msg)
                    continue
                keep.append((decl, line, why))
            if keep:
                self.move_to_module(unit, fn, [d for d, _, _ in keep], keep[0][1])

    def move_to_module(self, unit, fn, decls, line):
        """The saved locals `decls` of `fn` become public variables of a new module that only `fn` uses."""
        modname = self.unique_module_name(f"{fn.name}_state")
        mod = Module(modname, [], [replace(d, public=True) for d in decls], {}, line,
                     [f"state that {fn.name} keeps between calls (its Fortran locals were saved)"])
        for d in decls:
            fn.body.remove(d)
        unit.modules[modname] = mod
        names = [d.name for d in decls]
        host = next((m for m in unit.modules.values() if m.functions.get(fn.name) is fn), None)
        (host or fn).uses.append(Use(modname, names, line))
        self.note(line, f"{', '.join(names)} of '{fn.name}' keep their value between calls in Fortran, so they "
                        f"moved to module {modname}; writes to them are marked @")

    def called_once(self, unit, name, seen=()):
        """Is the procedure called from exactly one place that runs once (the main program, outside
        every loop), or from one procedure that is itself called once?"""
        if name in seen:
            return False
        sites = []                                          # (owner, inside a loop?)
        for owner in self.all_functions(unit) + ([unit.program] if unit.program else []):
            sites += [(owner, in_loop) for in_loop in self.call_sites(owner.body, name, False)]
        if len(sites) != 1 or sites[0][1]:
            return False
        owner = sites[0][0]
        return isinstance(owner, Program) or self.called_once(unit, owner.name, seen + (name,))

    def call_sites(self, body, name, in_loop):
        """One entry per call of `name` in `body`: True when the call sits inside a loop."""
        def calls(x, flag):
            return [flag for n in iter_nodes(x) if isinstance(n, (Call, Apply)) and n.name == name]

        sites = []
        for s in body:
            if isinstance(s, list):
                sites += self.call_sites(s, name, in_loop)
            elif isinstance(s, If):
                for cond, b in s.branches:
                    sites += calls(cond, in_loop) + self.call_sites(b, name, in_loop)
                sites += self.call_sites(s.orelse, name, in_loop)
            elif isinstance(s, Select):
                sites += calls(s.subject, in_loop)
                for c in s.cases:
                    sites += self.call_sites(c.body, name, in_loop)
            elif isinstance(s, (Do, DoWhile)):
                sites += calls([s.lo, s.hi, s.step] if isinstance(s, Do) else s.cond, in_loop or isinstance(s, DoWhile))
                sites += self.call_sites(s.body, name, True)
            else:
                sites += calls(s, in_loop)
        return sites

    def overwritten_first(self, body, name):
        """Is `name` always assigned before anything can read it? (Then a saved value is never seen.)"""
        def mentions(x):
            for n in iter_nodes(x):
                if isinstance(n, (Var, Apply, Call)) and n.name == name:
                    return True
                if isinstance(n, Do) and n.var == name:
                    return True
            return False

        for s in body:
            for s in (s if isinstance(s, list) else [s]):
                if not mentions(s):
                    continue
                if isinstance(s, Assign) and isinstance(s.target, Var) and s.target.name == name:
                    return not mentions(s.value)
                if isinstance(s, Do) and s.var == name:
                    return not mentions([s.lo, s.hi, s.step])
                return False
        return False

    def fields_written(self, body, name):
        """The fields of record `name` assigned whole (`name%f = ...`), or True if the whole
        record is assigned or handed to a procedure (then we cannot tell)."""
        written = set()
        for n in iter_nodes(body):
            if isinstance(n, Assign):
                t = n.target
                if isinstance(t, Var) and t.name == name:
                    return True
                if isinstance(t, FieldAccess) and t.args is None and isinstance(t.base, Var) and t.base.name == name:
                    written.add(t.field)
            elif isinstance(n, Call) and any(isinstance(a, Var) and a.name == name for a in n.args):
                return True
        return written

    def usage(self, body, name, procs):
        """Count (reads, writes) of `name` in a statement list."""
        counts = [0, 0, 0]          # reads, writes, writes that always happen (not inside if/do)

        def read_expr(e):
            for n in iter_nodes(e):
                if isinstance(n, (Var, Apply)) and n.name == name:
                    counts[0] += 1
                callee = procs.get(n.name) if isinstance(n, Apply) else None
                if callee is not None and not callee.is_sub:       # a function that changes its arguments
                    for i, a in enumerate(n.args):
                        if ref_name(a) == name and i < len(callee.params) and callee.params[i][2] in ("out", "inout"):
                            counts[1] += 1

        def ref_name(e):
            while isinstance(e, FieldAccess):
                e = e.base
            return e.name if isinstance(e, (Var, Apply)) else None

        def scan(stmts, top=True):
            for s in stmts:
                if isinstance(s, list):
                    scan(s, top)
                elif isinstance(s, Assign):
                    if ref_name(s.target) == name:
                        counts[1] += 1
                        counts[2] += top and not isinstance(s.target, FieldAccess)    # a field is a partial write
                        t = s.target
                        while isinstance(t, (Apply, FieldAccess)):
                            for a in t.args or []:
                                read_expr(a)
                            t = t.base if isinstance(t, FieldAccess) else None
                    else:
                        read_expr(s.target)
                    read_expr(s.value)
                elif isinstance(s, If):
                    for cond, b in s.branches:
                        read_expr(cond)
                        scan(b, False)
                    scan(s.orelse, False)
                elif isinstance(s, Select):
                    read_expr(s.subject)
                    for c in s.cases:
                        scan(c.body, False)
                elif isinstance(s, Do):
                    if s.var == name:
                        counts[1] += 1
                    read_expr([s.lo, s.hi, s.step])
                    scan(s.body, False)
                elif isinstance(s, DoWhile):
                    read_expr(s.cond)
                    scan(s.body, False)
                elif isinstance(s, Allocate):
                    read_expr(s.dims)
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
                                counts[2] += top and mode == "out" and not isinstance(a, FieldAccess)
                        else:
                            read_expr(a)
                elif isinstance(s, (Print, Return, ExprStmt)):
                    read_expr(getattr(s, "args", None) or getattr(s, "value", None) or getattr(s, "expr", None))

        scan(body)
        return counts[0], counts[1], counts[2] > 0

    def check_calls(self, unit):
        scopes = [(f, self.visible_procs(unit, f), f.uses) for f in self.all_functions(unit)]
        scopes += [(f, self.visible_procs(unit, f, self.method_home[id(f)]), f.uses) for f in self.method_functions]
        if unit.program:
            p = unit.program
            procs = dict(unit.functions)
            for use in p.uses:
                src = unit.modules[use.module].functions
                procs.update({n: src[n] for n in use.names if n in src})
            scopes.append((p, procs, p.uses))
        for node, procs, uses in scopes:
            if id(node) in self.method_home:                    # a function inside a record: its record's module
                mod = self.method_home[id(node)]
            else:
                mod = next((m for m in unit.modules.values() if m.functions.get(node.name) is node), None)
            imported = {n for u in uses + (mod.uses if mod else []) for n in u.names}
            declared = {d.name for d in iter_nodes(node.body) if isinstance(d, Decl)} | imported
            declared |= {d.name for d in mod.decls} if mod else set()
            declared |= {p[1] for p in getattr(node, "params", [])}
            known = set(procs) | set(BUILTINS) | set(ELEMENTAL) | set(self.types)
            seen = set()
            for n in iter_nodes(node.body):
                if isinstance(n, (Apply, Call)) and n.name in self.unresolved_generics:
                    continue                                    # already a TODO of its own
                if isinstance(n, Apply) and n.name not in declared and n.name not in known:
                    what = "function"
                elif isinstance(n, Call) and n.receiver is None and n.name not in procs and n.name not in declared:
                    what = "subroutine"
                else:
                    continue
                if n.name in seen:
                    continue
                seen.add(n.name)
                msg = f"{what} '{n.name}' is not a Boreas built-in or a procedure in this file; the call will fail"
                self.todo(n.line, msg)
                node.comments.append("TODO(convert): " + msg)


def parse_fortran(src):
    p = FParser(src)
    unit = p.parse()
    return unit, p.top_comments, p.findings


def convert(src, filename="input", defines=None, base_dir=None):
    """Fortran source -> (Boreas source, findings). Each finding is (line, kind, message).
    A fixed-form file (by its extension: .f, .for, ...) is read as such. Preprocessor lines are
    evaluated first, with the macros in `defines` ({name: text}); `base_dir` is where `#include` looks."""
    src, pre = preprocess(src, defines, base_dir)
    if is_fixed_form(filename):
        src = fixed_to_free(src)
    return convert_free(src, filename, pre)


def convert_files(sources, defines=None):
    """Several source files that make up one program -> (Boreas source, findings, where). `sources` is a
    list of (filename, text), or (filename, text, directory for #include). The files are joined in order,
    so a finding's line number counts through all of them; `where(line)` turns it back into (index of the
    file, line in that file)."""
    parts, starts, pre = [], [], []
    for source in sources:
        name, text = source[0], source[1]
        text, found = preprocess(text, defines, source[2] if len(source) > 2 else None)
        free = fixed_to_free(text) if is_fixed_form(name) else text
        starts.append(sum(p.count("\n") for p in parts))
        pre.extend((line + starts[-1], kind, msg) for line, kind, msg in found)
        parts.append(free if free.endswith("\n") else free + "\n")

    def where(line):
        for n in range(len(sources) - 1, -1, -1):
            if line > starts[n]:
                return n, line - starts[n]
        return 0, line

    text, findings = convert_free("".join(parts), ", ".join(s[0] for s in sources), pre)
    return text, findings, where


def convert_free(src, label, before=()):
    """`before` holds findings made ahead of the parse (the preprocessor's)."""
    unit, trailing, findings = parse_fortran(src)
    findings = list(before) + findings
    trailing = [f"TODO(convert): {msg}" for _, kind, msg in before if kind == "todo"] + list(trailing)
    todos = sum(1 for f in findings if f[1] == "todo")
    header = [f"Converted from {label} by `boreas convert`."]
    if todos:
        header.append(f"{todos} construct(s) could not be translated: search for TODO(convert).")
    header.extend(trailing)
    return to_source(unit, header), findings
