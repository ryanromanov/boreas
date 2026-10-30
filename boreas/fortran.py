"""A free-form Fortran front end: Fortran source -> the Boreas AST.

It covers a modern subset (program/function/subroutine, declarations, do/if,
array expressions, print/write to the terminal). Anything else becomes a
`! TODO(convert)` comment quoting the original line instead of failing the
whole file. Every translation that is not a 1:1 rename is recorded as a
finding, returned alongside the AST."""
import re
from dataclasses import dataclass, fields, is_dataclass, replace

from .fixedform import fixed_to_free, is_fixed_form
from .gotos import Goto, LabelMark, Structurer
from .lexer import LangError
from .parser import *
from .interp import BUILTINS, ELEMENTAL, PURE_BUILTINS
from .kinds import round32
from .printer import to_source
from .fformat import FormatError, parse_format, render, substitute

# Words the Boreas parser treats specially; Fortran has no reserved words,
# so a Fortran variable with one of these names is renamed.
RESERVED = {"const", "update", "use", "module", "public", "private", "and", "or", "not", "true", "false", "in", "out", "inout", "string", "integer",
            "real", "logical", "program", "function", "subroutine", "end", "if", "then",
            "else", "do", "while", "print", "write", "read", "stop", "call", "return", "exit", "cycle",
            "select", "case", "default", "fallthrough", "break",
            "allocatable", "allocate", "deallocate", "record"}

FORMAT_STATEMENT = re.compile(r"(\d+)\s+format\s*\((.*)\)\s*$", re.I | re.S)
TYPE_WORDS = {"integer", "real", "logical", "character", "double", "complex", "type"}
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


class Scope:
    def __init__(self, kind, name, dummies=()):
        self.kind, self.name, self.dummies = kind, name, list(dummies)
        self.decls = {}
        self.implicit_none = False
        self.result = None
        self.ret_type = None
        self.comments = []
        self.externals = set()
        self.host = None              # enclosing scope, for internal procedures
        self.body = None
        self.skipped = set()          # declared in Fortran we could not translate
        self.saved = {}               # local name -> (line, why) for variables Fortran keeps between calls
        self.save_all = None          # line of a bare `save` statement (every local is saved)
        self.bad_dummies = set()      # dummy arguments whose declaration we could not translate
        self.line = 0
        self.module = None            # for procedures inside a module: that module's scope
        self.uses = []                # [(module name, names or None for "all", line)]
        self.access = {}              # module scope: name -> "public" | "private" (explicit ones)
        self.default_public = True
        self.formats = {}             # label -> the text inside the parentheses of a `format` statement


@dataclass
class Raw:
    kind: str
    scope: Scope
    body: list
    line: int


@dataclass
class TypeInfo:
    """A Fortran derived type. `fields` are Boreas Decls; `ok` is False when it could not be translated."""
    name: str
    scope: object                 # the scope that defines it
    fields: list
    line: int
    ok: bool = True

    @property
    def module(self):
        return self.scope if self.scope.kind == "module" else None


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
        self.ended = None           # the label whose `do` loop just ended on its last statement
        self.do_labels = []         # terminating labels of the enclosing `do 10 ...` loops, innermost last
        self.loops = []             # enclosing named do loops: (Fortran name, Boreas label), innermost last
        self.construct = None       # the name in front of the statement being read: `outer: do`

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
                items.append(self.expr())
                while self.accept_op(","):
                    items.append(self.expr())
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
                name = SPECIFIC_NAMES.get(name, name)
                name, args = self.convert_intrinsic(name, args)
                intrinsic = name in BUILTINS or name in ELEMENTAL
                e = Apply(name if intrinsic else self.rename(name), args, self.line)
                if name == "len" and len(args) == 1 and self.char_info(args[0]) is not None \
                        and name not in self.cur_scope.decls:
                    e = Num(self.char_info(args[0]).length)          # the declared length, not the text's
            while self.accept_op("%"):                       # derived-type component: p%x, pts(2)%pos%y
                if isinstance(e, Apply) and any(isinstance(a, Slice) for a in e.args):
                    raise Unsupported("a component of an array section (pts(1:3)%x)")
                if isinstance(e, Var) and self.is_array_name(e.name):
                    raise Unsupported(f"a component of a whole array ({e.name}%...): Boreas needs one element")
                field = self.rename(self.expect_name())
                args = None
                if self.accept_op("("):
                    args = self.call_arguments(field)
                e = FieldAccess(e, field, args, self.line)
            return e
        raise Unsupported(f"unexpected {t.text!r} in expression")

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
        return args

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
                elif kind == "module":
                    self.i += 1
                    name = self.rename(toks[1].text) if len(toks) > 1 else "module"
                    self.parse_unit("module", Scope("module", name), it)
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

    def parse_procedure(self, toks, it, host=None):
        kind, name, dummies, result, ret_type = self.procedure_header(toks)
        scope = Scope(kind, name, dummies)
        scope.result, scope.ret_type = result, ret_type
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
        toks = self.tokens(self.items[self.i])
        if classify(toks) == ("contains",):
            self.i += 1
            if kind != "module":
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
                    self.parse_procedure(toks, cur, scope)
                else:
                    self.todo(cur.line, f"unsupported statement after contains: {cur.text}")
        self.i += 1                        # the `end` statement
        if kind == "module":
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
        if name in ("common", "equivalence", "namelist", "include",
                    "nullify", "open", "close", "inquire", "rewind",
                    "backspace", "format", "entry", "pause", "dimension", "pointer",
                    "target", "intrinsic", "import", "enum", "sync", "lock", "unlock"):
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
        if word == "type":
            if not self.accept_op("("):
                raise Unsupported("expected 'type(name)'")
            name = self.rename(self.expect_name())
            self.expect_op(")")
            info = self.types.get(name)
            if info is None:
                raise Unsupported(f"derived type '{name}' is not defined earlier in this file")
            if not info.ok:
                raise Unsupported(f"derived type '{name}' was not translated")
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
        typ = self.type_spec()
        char_len = self.char_len
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
                if attrs.get("allocatable"):
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
                if not attrs.get("parameter") and scope.kind not in ("program", "module", "type") \
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
                                         bool(attrs.get("parameter")), line, bool(attrs.get("allocatable")),
                                         bool(attrs.get("optional")), length)
                if attrs.get("external"):
                    scope.externals.add(name)
                if scope.kind == "module":
                    for word in ("public", "private"):
                        if attrs.get(word):
                            scope.access[name] = word
                    if not attrs.get("parameter") and not attrs.get("external"):
                        scope.skipped.add(name)          # mutable module state is not translated
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
        if scope.kind == "module":
            scope.skipped.add(name)
            raise Unsupported(f"allocatable module variable '{name}': Boreas modules hold constants "
                              f"and procedures only")
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
            self.note(it.line, f"'{it.text}' ignored (intrinsic module)")
            return []
        name = self.rename(self.expect_name())
        mod = self.modules.get(name)
        if mod is None:
            raise Unsupported(f"module '{name}' is not defined earlier in this file")
        names = None
        if self.accept_op(","):
            if not self.at_name("only"):
                raise Unsupported("renaming imports is not supported")
            self.k += 1
            self.expect_op(":")
            names = []
            while self.peek() is not None:
                local = self.rename(self.expect_name())
                if self.at_op("=>"):
                    raise Unsupported("renaming imports is not supported")
                names.append(local)
                if not self.accept_op(","):
                    break
        self.end_of_statement()
        exports = self.visible(mod)
        if names is None:
            scope.skipped |= {n for n in mod.skipped if self.is_public(mod, n)}
        else:
            for n in list(names):
                if n in exports:
                    continue
                names.remove(n)
                if n in mod.skipped and self.is_public(mod, n):
                    scope.skipped.add(n)
                    self.todo(it.line, f"'{n}' is a variable of module '{name}'; Boreas modules hold "
                                       f"constants and procedures only, so uses of it were not translated")
                else:
                    self.todo(it.line, f"module '{name}' has no public '{n}' that Boreas can import")
        scope.uses.append((name, names, it.line))
        return []

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
        return mod.access.get(name, "public" if mod.default_public else "private") == "public"

    def own_exports(self, mod):
        """Public names defined in a module that Boreas can export, in source order."""
        names = [t.name for t in self.types.values() if t.ok and t.scope is mod]
        names += [r.scope.name for r in self.raw if r.scope.module is mod and r.scope.host is mod]
        names += [n for n, info in mod.decls.items() if info.parameter]
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
            self.k += 1
        self.note(it.line, f"'{it.text}' ignored: Boreas finds procedures by name")
        return []

    def st_continue(self, it, scope):
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
            self.skip_block("do")
            raise Unsupported("'do concurrent' is not supported; skipped")
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
        status, whole_line = None, False
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
            unit = parts[0]
            if not (len(unit) == 1 and (unit[0].text == "*" or unit[0].value == 5)):
                raise Unsupported("read from a file unit is not supported")
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
            return self.read_whole_line(it, scope, targets, status)
        stmts = [Read(targets, status, it.line)]
        for t in targets:                                   # text read into a character(len=n) variable fits n
            info = self.char_info(t)
            if info is not None:
                stmts.append(Assign(t, self.fit_to_length(t, info.length), it.line))
        return stmts

    def read_whole_line(self, it, scope, targets, status):
        """`read (*, '(A)', iostat=ios) text`: the next line of input goes into one character variable."""
        target = targets[0]
        declared = scope.decls.get(target.name) if len(targets) == 1 and isinstance(target, (Var, Apply)) else None
        if declared is None or declared.type != "string":
            raise Unsupported("a '(A)' read of anything but one character variable")
        info = self.char_info(target)
        fit = (lambda e: self.fit_to_length(e, info.length)) if info is not None else (lambda e: e)
        if status is None:
            return [Assign(target, fit(Binary("??", Apply("read_line", [], it.line), Str(""))), it.line)]
        line = self.unique("line_read", scope, [])
        scope.decls[line] = Info("string?", None, False, None, None, False, it.line)
        return [Assign(Var(line), Apply("read_line", [], it.line), it.line),
                If([(Apply("present", [Var(line)], it.line),
                     [Assign(target, fit(Var(line)), it.line), Assign(status, Num(0), it.line)])],
                   [Assign(status, Num(-1), it.line)])]

    def st_data(self, it, scope):
        """`data a, b /1, 2/, c(1:3) /3*0/`: initial values, which become declaration initialisers."""
        while True:
            names = []
            while True:
                t = self.next()
                if t.kind != "name" or self.at_op("("):
                    raise Unsupported("data with array elements, sections or implied-do loops")
                names.append(self.rename(t.text))
                if not self.accept_op(","):
                    break
            self.expect_op("/")
            values = []
            while not self.at_op("/"):
                values.extend(self.data_value())
                if not self.accept_op(","):
                    break
            self.expect_op("/")
            for name in names:
                info = scope.decls.get(name)
                if info is None or name in scope.dummies:
                    raise Unsupported(f"data for '{name}', which is not a declared local variable")
                count = 1
                if info.dims is not None:
                    sizes = [self.constant_int(d) for d in info.dims]
                    if None in sizes:
                        raise Unsupported(f"data for '{name}', whose size is not a literal number")
                    count = 1
                    for n in sizes:
                        count *= n
                if len(values) < count:
                    raise Unsupported(f"data for '{name}' has too few values")
                mine, values = values[:count], values[count:]
                if info.type == "string" and info.length is not None:
                    mine = [self.fit_to_length(v, info.length) if isinstance(v, Str) else v for v in mine]
                if info.dims is None:
                    info.init = mine[0]
                else:
                    items = ArrayLit(mine)
                    info.init = items if len(sizes) == 1 else Apply("reshape", [items, ArrayLit(
                        [Num(n) for n in sizes])], self.line)
                if scope.kind not in ("program", "module", "type"):
                    scope.saved[name] = (it.line, "initialised by a data statement, which implies save")
            if values:
                raise Unsupported("data has more values than objects")
            if not self.accept_op(","):
                break
        self.end_of_statement()
        return []

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
        made = self.intrinsic_call(name, args, it.line, scope)
        return made if made is not None else [Call(name, args, it.line)]

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
            if self.at_op("::"):
                raise Unsupported(f"'{what}' with a type-spec is not supported")
            info = scope.decls.get(name)
            if info is None or not info.allocatable or name in scope.dummies:
                raise Unsupported(f"'{what}' of '{name}', which is not a local allocatable array "
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

    def st_allocate(self, it, scope):
        out = []
        for name, dims in self.allocation_objects(scope, "allocate"):
            if len(dims) != len(scope.decls[name].dims):
                raise Unsupported(f"allocate of '{name}' needs {len(scope.decls[name].dims)} extent(s)")
            out.append(Allocate(name, dims, it.line))
        return out

    def st_deallocate(self, it, scope):
        out = []
        for name, dims in self.allocation_objects(scope, "deallocate"):
            if dims:
                raise Unsupported("deallocate takes whole arrays")
            out.append(Deallocate(name, it.line))
        return out

    def note_auto_allocation(self, body):
        """Fortran 2003 allocates on assignment; Boreas does not. Flag a whole-array assignment
        to an allocatable that no earlier `allocate` (in text order) covers."""
        allocatable, ready = set(), set()
        for n in iter_nodes(body):
            if isinstance(n, Decl) and n.allocatable:
                allocatable.add(n.name)
            elif isinstance(n, Allocate):
                ready.add(n.name)
            elif isinstance(n, Deallocate):
                ready.discard(n.name)
            elif isinstance(n, Assign) and isinstance(n.target, Var) and n.target.name in allocatable \
                    and n.target.name not in ready:
                self.note(n.line, f"assignment to allocatable '{n.target.name}' relied on automatic allocation "
                                  f"in Fortran; Boreas requires an explicit allocate")

    def st_select(self, it, scope):
        resume = self.i
        try:
            return self.select_case(scope)
        except Unsupported:                  # leave this construct as a TODO; the rest still converts
            self.i = resume
            self.skip_block("select")
            raise

    def select_case(self, scope):
        if not self.at_name("case"):
            raise Unsupported("'select type' and 'select rank' are not supported; skipped")
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
        if self.at_op("("):                          # type(point) :: p
            self.k = 0
            return self.declaration(it, scope)
        return self.type_definition(it, scope)

    def type_definition(self, it, scope):
        """`type [, attrs] [::] name` through `end type`: a Boreas record, or a TODO for the
        whole type when any part of it cannot be translated (variables of it are then skipped)."""
        bad, access = None, None
        while self.accept_op(","):
            word = self.expect_name()
            if word in ("public", "private"):
                access = word
            elif word == "bind":
                self.skip_parens()
                self.note(it.line, "bind(c) ignored: Boreas records have no C layout")
            elif word in ("extends", "abstract"):
                bad = bad or f"'{word}' (inheritance) is not supported"
                if self.at_op("("):
                    self.skip_parens()
            else:
                bad = bad or f"unknown type attribute '{word}'"
        self.accept_op("::")
        name = self.rename(self.expect_name())
        if self.at_op("("):
            bad = bad or "parameterized derived types are not supported"
            self.skip_parens()
        self.end_of_statement()
        fields, holder = [], Scope("type", name)
        holder.host = scope
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
                bad = bad or "type-bound procedures are not supported"
                self.skip_block("type")
                break
            word = toks[0].text if toks and toks[0].kind == "name" else ""
            if word in ("private", "public") and len(toks) == 1:
                self.note(cur.line, f"'{word}' in a type: Boreas record fields are always accessible")
                continue
            if word == "sequence" and len(toks) == 1:
                continue
            self.toks, self.k = toks, 0
            if word in TYPE_WORDS and word != "type" or word == "type":
                try:
                    for c in self.declaration(cur, holder):         # an entity that could not be declared
                        bad = bad or c.text.replace("TODO(convert): ", "")
                except Unsupported as e:
                    bad = bad or f"{e}: {cur.text}"
            else:
                bad = bad or f"unsupported statement in a type: {cur.text}"
        else:
            raise LangError("unexpected end of file: 'type' is missing its 'end type'")
        for cname, info in holder.decls.items():
            dims = None
            if info.dims is not None:
                dims = [self.literal_size(d, holder) for d in info.dims]
                if None in dims:
                    bad = bad or f"component '{cname}' has an array size that is not an integer constant"
                    continue
            fields.append(Decl(info.type, cname, dims, self.component_default(cname, info, scope, holder),
                               info.line))
        if name in self.types:
            self.todo(it.line, f"derived type '{name}' is defined more than once")
        if bad:
            self.types[name] = TypeInfo(name, scope, [], it.line, ok=False)
            raise Unsupported(f"derived type '{name}' was not translated: {bad}")
        if scope.kind == "module" and access:
            scope.access[name] = access
        self.types[name] = TypeInfo(name, scope, fields, it.line)
        return []

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

    def implied_do(self):
        """Parse `(expr, expr, var = lo, hi [, step])` at the current position, or put everything back."""
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
                exprs.append(self.expr())
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

    def formatted_output(self, text, line, newline=True):
        values = self.print_items(expand_arrays=True, blank=False)
        try:
            lines, advance = render(parse_format(text)[0], values)
        except FormatError as e:
            raise Unsupported(f"unsupported write format: {e}")
        out = [Print([l]) for l in lines[:-1]]
        out.append(Print([lines[-1]], newline and advance))
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
        unit = parts[0]
        if not (len(unit) == 1 and (unit[0].text == "*" or unit[0].value == 6)):
            raise Unsupported("write to a file unit is not supported")
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
            return self.formatted_output(fmt, it.line, newline)
        return [Print(self.print_items(), newline)]

    # -- finishing: turn raw scopes into Boreas procedures
    def structure_gotos(self, r):
        s = r.scope
        pending = Structurer(lambda base: self.unique(base, s, r.body), self.todo)
        r.body = pending.run(r.body)
        for name in pending.new_vars:
            s.decls[name] = Info("integer", None, False, None, None, False, r.line)

    def finalize(self):
        for r in self.raw:
            self.structure_gotos(r)
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
        records = {}
        for t in self.types.values():
            if not t.ok:
                continue
            rd = RecordDef(t.name, t.fields, t.module is not None and self.is_public(t.module, t.name), t.line)
            (modules[t.module.name].records if t.module is not None else records)[t.name] = rd
        unit = Unit(functions, program, modules, records)
        self.link_uses(unit, built)
        self.infer_intents(unit)
        self.hoist_calls(unit)
        self.check_saved(unit, built)
        self.check_calls(unit)
        for owner in self.all_functions(unit) + ([program] if program else []):
            self.note_auto_allocation(owner.body)
        if program is not None:                          # a final plain `stop` changes nothing
            while program.body and isinstance(program.body[-1], Stop) and program.body[-1].code is None:
                program.body.pop()
        return unit

    def all_functions(self, unit):
        return list(unit.functions.values()) + [f for m in unit.modules.values() for f in m.functions.values()]

    def build_module(self, ms):
        consts = []
        mod = Module(ms.name, [], consts, {}, ms.line, list(ms.comments))
        mod.comments += [c.text for c in ms.body or [] if isinstance(c, Comment) and c.text.startswith("TODO(convert)")]
        for name, info in ms.decls.items():
            if name in ms.externals:
                continue
            if info.parameter:
                consts.append(Decl(info.type, name, info.dims, info.init, info.line, True,
                                   self.is_public(ms, name)))
            else:
                msg = (f"module variable '{name}' is not translated: Boreas modules hold constants "
                       f"and procedures only, and code that uses '{name}' became a TODO")
                self.todo(info.line, msg)
                mod.comments.append("TODO(convert): " + msg)
        return mod

    def references(self, nodes):
        """Names used in `nodes`: variables, calls, constructors, and the types of declarations."""
        out = set()
        for n in iter_nodes(nodes):
            if isinstance(n, (Var, Apply, Call)):
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

    def link_uses(self, unit, built):
        by_module = {}
        for owner, scope in built:
            if isinstance(owner, Program):
                owner.uses = self.make_uses(scope.uses, self.references(owner.body))
            else:
                sig = {p[0] for p in owner.params} | ({owner.ret} if owner.ret else set())
                owner.uses = self.make_uses(scope.uses, self.references(owner.body) | sig)
                if scope.module is not None:
                    by_module.setdefault(scope.module.name, []).append(owner)
        for name, ms in self.modules.items():
            mod = unit.modules[name]
            refs = self.references([d.init for d in mod.consts]) | {d.type for d in mod.consts}
            for r in mod.records.values():
                refs |= self.references([fl.init for fl in r.fields]) | {fl.type for fl in r.fields}
            for f in by_module.get(name, []):
                refs |= self.references(f.body) | {p[0] for p in f.params} | ({f.ret} if f.ret else set())
            mod.uses = self.make_uses(ms.uses, refs)

    def visible_procs(self, unit, f):
        """The procedures `f` can call: its siblings plus whatever it (or its module) imports."""
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
            decls.append(Decl(info.type, name, info.dims, info.init, info.line, info.parameter,
                              allocatable=info.allocatable))
        return decls

    def implicit_decls(self, scope, body, proc_names, line):
        known = set(scope.decls) | set(scope.dummies) | proc_names | self.imported_names(scope)
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
            elif info.optional and (info.dims is not None or info.shape_dropped or info.allocatable):
                msg = f"argument '{d}' is an optional array; Boreas optional arguments are single values"
                self.todo(line, msg)
                s.comments.append("TODO(convert): " + msg)
                params.append([info.type, d, info.intent])
            else:
                params.append([info.type + "?" if info.optional else info.type, d, info.intent])
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
            elif isinstance(s, Select):
                for c in s.cases:
                    self.fix_returns(c.body, result)

    # -- parameter modes
    def infer_intents(self, unit):
        """Dummy arguments without `intent` get one from how the body uses them."""
        funcs = self.all_functions(unit)
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
                if p[0].endswith("?") and p[2] != "in":
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
        for f in funcs:                            # writes that reach the caller are marked `update`
            modes = {p[1]: p[2] for p in f.params}
            for n in iter_nodes(f.body):
                if isinstance(n, Assign) and modes.get(root_name(n.target)) in ("out", "inout"):
                    n.update = True
                elif isinstance(n, Read) and any(
                        modes.get(root_name(t)) in ("out", "inout")
                        for t in n.targets + ([n.status] if n.status is not None else [])):
                    n.update = True
                elif isinstance(n, Do) and n.var in modes:
                    msg = f"loop variable '{n.var}' is a dummy argument; Boreas loop variables must be locals"
                    f.comments.append("TODO(convert): " + msg)
                    self.todo(n.line, msg)

    def functions_to_subroutines(self, funcs):
        """A Fortran function that changes its arguments becomes a subroutine whose last parameter is
        the result (`out`); its calls are rewritten later by hoist_calls. Returns {name: (function, type)}."""
        converted = {}
        for f in funcs:
            if f.is_sub or all(p[2] == "in" for p in f.params):
                continue
            last = f.body[-1] if f.body else None
            if not (isinstance(last, Return) and isinstance(last.value, Var)):
                continue
            result, ret = last.value.name, f.ret
            f.body = [d for d in f.body[:-1] if not (isinstance(d, Decl) and d.name == result)]
            for n in iter_nodes(f.body):
                if isinstance(n, Return) and isinstance(n.value, Var) and n.value.name == result:
                    n.value = None
            f.params.append([ret, result, "out"])
            f.is_sub, f.ret = True, None
            f.comments.append(f"note: this Fortran function changes its arguments, so it is a subroutine here; "
                              f"its result is the out parameter '{result}'")
            self.note(self.fn_lines.get(id(f), 0), f"function '{f.name}' changes its arguments: it became a "
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
        types.update({p[1]: p[0].rstrip("?") for p in params})
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
        Boreas locals start fresh. That only matters if the procedure changes the variable and
        could read the old value, so only then is it a TODO."""
        for fn, scope in built:
            if not isinstance(fn, Function):
                continue
            saved = dict(scope.saved)
            if scope.save_all is not None:
                for name, info in scope.decls.items():
                    if name not in scope.dummies and not info.parameter:
                        saved.setdefault(name, (scope.save_all, "the procedure has a bare save statement"))
            procs = self.visible_procs(unit, fn)
            for name, (line, why) in saved.items():
                info = scope.decls.get(name)
                if info is None or info.parameter or name in scope.dummies or name in (scope.name, scope.result):
                    continue
                _, writes, _ = self.usage(fn.body, name, procs)
                if not writes or self.overwritten_first(fn.body, name):
                    continue
                msg = (f"'{name}' ({why}) keeps its value between calls in Fortran, and this procedure changes "
                       f"it; Boreas locals start fresh on every call, so the converted procedure behaves "
                       f"differently (pass it in as an inout argument, or keep the state in the caller)")
                self.todo(line, msg)
                fn.comments.append("TODO(convert): " + msg)

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
                elif isinstance(s, Read):
                    for t in s.targets + ([s.status] if s.status is not None else []):
                        if ref_name(t) == name:
                            counts[1] += 1
                            counts[2] += top and not isinstance(t, FieldAccess)
                        else:
                            read_expr(t)
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
        if unit.program:
            p = unit.program
            procs = dict(unit.functions)
            for use in p.uses:
                src = unit.modules[use.module].functions
                procs.update({n: src[n] for n in use.names if n in src})
            scopes.append((p, procs, p.uses))
        for node, procs, uses in scopes:
            mod = next((m for m in unit.modules.values() if m.functions.get(node.name) is node), None)
            imported = {n for u in uses + (mod.uses if mod else []) for n in u.names}
            declared = {d.name for d in iter_nodes(node.body) if isinstance(d, Decl)} | imported
            declared |= {d.name for d in mod.consts} if mod else set()
            declared |= {p[1] for p in getattr(node, "params", [])}
            known = set(procs) | set(BUILTINS) | set(ELEMENTAL) | set(self.types)
            seen = set()
            for n in iter_nodes(node.body):
                if isinstance(n, Apply) and n.name not in declared and n.name not in known:
                    what = "function"
                elif isinstance(n, Call) and n.name not in procs:
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


def convert(src, filename="input"):
    """Fortran source -> (Boreas source, findings). Each finding is (line, kind, message).
    A fixed-form file (by its extension: .f, .for, ...) is read as such."""
    if is_fixed_form(filename):
        src = fixed_to_free(src)
    unit, trailing, findings = parse_fortran(src)
    todos = sum(1 for f in findings if f[1] == "todo")
    header = [f"Converted from {filename} by `boreas convert`."]
    if todos:
        header.append(f"{todos} construct(s) could not be translated: search for TODO(convert).")
    header.extend(trailing)
    return to_source(unit, header), findings
