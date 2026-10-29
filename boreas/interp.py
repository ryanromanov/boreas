import math
import cmath
import operator
import random
import re
import sys
from itertools import product
from .lexer import LangError
from .parser import *
from .kinds import *
from .kinds import _F32


class _Stop(Exception):
    def __init__(self, code): self.code = code
class _Return(Exception):
    def __init__(self, value): self.value = value
class _Exit(Exception):
    def __init__(self, label=None):
        self.label = label          # None: the innermost loop
class _Cycle(Exception):
    def __init__(self, label=None):
        self.label = label


def _mine(e, loop):
    """Does this exit/cycle target `loop`? Otherwise the loop lets it pass outwards."""
    return e.label is None or e.label == loop.label
class _Break(Exception): pass


COMPARE = {"==": operator.eq, "/=": operator.ne, "<": operator.lt, "<=": operator.le,
           ">": operator.gt, ">=": operator.ge}


FAST_INT = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": int_div,
            "==": operator.eq, "/=": operator.ne, "<": operator.lt, "<=": operator.le,
            ">": operator.gt, ">=": operator.ge}
FAST_FLOAT = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv,
              "==": operator.eq, "/=": operator.ne, "<": operator.lt, "<=": operator.le,
              ">": operator.gt, ">=": operator.ge}


SINGLE_LITERALS = {}                    # literal value -> the real32 it becomes next to a real32 (a few per program)


def single_literal(x):
    r = SINGLE_LITERALS[x] = Real32(round32(x))
    return r


def _is_real32(v):
    return type(v) is Real32 or (type(v) is Array and bool(v.data) and type(v.data[0]) is Real32)


def _is_literal(e):
    return type(e) is Num or (type(e) is Unary and type(e.operand) is Num)


class _Null:
    """The absent value of an optional (`T?`) variable."""
    def __repr__(self): return "null"
    def copy(self): return self


NULL = _Null()


def base_type(t):
    """`integer` for `integer?`."""
    return t[:-1] if t.endswith("?") else t


def type_of(v):
    name = TYPE_NAMES.get(type(v))
    if name is not None: return name
    if isinstance(v, Array): return "array"
    if isinstance(v, Record): return v.rdef.name
    if v is NULL: return "null"
    return "?"


def coerce(typ, value, what):
    """Assignment rules. Integers go into any integer width if the value fits, integers and
    smaller reals widen to a real, and a real64 rounds into a real32; everything else must match.
    Only an optional type (`integer?`) can hold null."""
    got = type_of(value)
    if got == typ:
        return value.copy() if isinstance(value, Record) else value
    if typ in INT_RANGE:
        if got in INT_RANGE:
            return make_int(typ, value, what)
    elif typ == "real":
        if got in INT_RANGE or got == "real32":
            return float(value)
    elif typ == "real32":
        if got in INT_RANGE or got == "real":
            return Real32(round32(float(value)))
    elif typ == "complex":
        if got in INT_RANGE or got in ("real", "real32"):
            return complex(value)
    if typ[-1] == "?":                           # an optional type also takes null
        return NULL if value is NULL else coerce(typ[:-1], value, what)
    if value is NULL:
        raise LangError(f"{what}: cannot assign null to {typ} (declare it '{typ}?' to allow null)")
    raise LangError(f"{what}: cannot assign {got} to {typ}")


def fmt_shape(shape):
    return "(" + ", ".join(str(n) for n in shape) + ")"


# --- Records -----------------------------------------------------------
class Record:
    """A value of a record type. `fields` maps a field name to a variable slot
    [type, value, is_array, intent] (a field's intent is always None; whether it may be
    written is decided by the variable the record lives in)."""

    def __init__(self, rdef, fields):
        self.rdef, self.fields = rdef, fields

    def copy(self):
        return Record(self.rdef, {n: [t, v.copy() if isinstance(v, (Record, Array)) else v, ia, None]
                                  for n, (t, v, ia, _) in self.fields.items()})

    def first_unassigned(self, prefix=""):
        """The dotted path of the first scalar field that holds no value yet, else None."""
        for n, (_, v, _, _) in self.fields.items():
            for sub in (v.data if isinstance(v, Array) else [v]):
                if isinstance(sub, Record):
                    found = sub.first_unassigned(f"{prefix}{n}.")
                    if found:
                        return found
                elif sub is None:
                    return prefix + n
        return None


class Ref(tuple):
    """(slot, subs, name) plus where it lives: the variable `root` it is inside of, the
    subscripts `rsubs` of that variable if it is an array, and the field names `fpath`."""
    def __new__(cls, slot, subs, name, root=None, rsubs=None, fpath=()):
        r = super().__new__(cls, (slot, subs, name))
        r.root = slot if root is None else root
        r.rsubs = subs if root is None else rsubs
        r.fpath = fpath
        return r


def aliased(a, b):
    """Could two references touch the same storage?"""
    if a.root is not b.root or not overlaps(a.rsubs, b.rsubs):
        return False
    n = min(len(a.fpath), len(b.fpath))
    if a.fpath[:n] != b.fpath[:n]:
        return False                                # different fields of the same record
    return len(a.fpath) != len(b.fpath) or overlaps(a[1], b[1])


# --- Arrays ------------------------------------------------------------
class Array:
    """An N-dimensional array stored flat in column-major order, like Fortran:
    the first subscript varies fastest. Subscripts are 1-based."""

    def __init__(self, shape, data):
        self.shape = tuple(shape)
        self.data = data

    @property
    def rank(self):
        return len(self.shape)

    def copy(self):
        return Array(self.shape, [x.copy() if isinstance(x, Record) else x for x in self.data])

    def strides(self):
        out, s = [], 1
        for n in self.shape:
            out.append(s)
            s *= n
        return out

    def section(self, subs):
        """`subs` has one entry per dimension: an int, or an inclusive (lo, hi).
        Returns (flat positions in column-major order, shape of the result);
        integer subscripts drop their dimension."""
        lists = [[s] if isinstance(s, int) else list(range(s[0], s[1] + 1)) for s in subs]
        strides = self.strides()
        positions = [
            sum((c - 1) * st for c, st in zip(reversed(combo), strides))
            for combo in product(*reversed(lists))     # first dimension varies fastest
        ]
        shape = [len(l) for s, l in zip(subs, lists) if not isinstance(s, int)]
        return positions, shape


def _need_array(name, a, rank=None):
    if not isinstance(a, Array):
        raise LangError(f"{name}() needs an array, got {type_of(a)}")
    if rank is not None and a.rank not in rank:
        raise LangError(f"{name}() needs a rank-{' or '.join(map(str, rank))} array, got rank {a.rank}")
    return a


def _size(a, dim=None):
    _need_array("size", a)
    if dim is None:
        return len(a.data)
    if not isinstance(dim, int) or not 1 <= dim <= a.rank:
        raise LangError(f"size(): dimension must be 1..{a.rank}")
    return a.shape[dim - 1]


def _shape(a):
    _need_array("shape", a)
    return Array((a.rank,), list(a.shape))


def _reshape(src, shp):
    _need_array("reshape", src)
    _need_array("reshape", shp, rank=(1,))
    if not all(isinstance(n, int) and n >= 0 for n in shp.data):
        raise LangError("reshape(): shape must be non-negative integers")
    if math.prod(shp.data) != len(src.data):
        raise LangError(f"reshape(): {len(src.data)} elements cannot fill shape {fmt_shape(shp.data)}")
    return Array(shp.data, list(src.data))


def _matmul(a, b):
    _need_array("matmul", a, rank=(1, 2))
    _need_array("matmul", b, rank=(1, 2))
    ar = a.shape if a.rank == 2 else (1, a.shape[0])      # a vector on the left is a row
    br = b.shape if b.rank == 2 else (b.shape[0], 1)      # on the right, a column
    if ar[1] != br[0]:
        raise LangError(f"matmul(): inner dimensions differ ({ar[1]} vs {br[0]})")
    n, k, m = ar[0], ar[1], br[1]
    out = [0] * (n * m)
    for j in range(m):
        for i in range(n):
            out[i + j * n] = sum(a.data[i + t * n] * b.data[t + j * k] for t in range(k))
    shape = (n, m) if a.rank == 2 and b.rank == 2 else (n,) if a.rank == 2 else (m,)
    return Array(shape, out)


def _transpose(a):
    _need_array("transpose", a, rank=(2,))
    r, c = a.shape
    out = [None] * (r * c)
    for j in range(c):
        for i in range(r):
            out[j + i * c] = a.data[i + j * r]
    return Array((c, r), out)


def _dot_product(a, b):
    _need_array("dot_product", a, rank=(1,))
    _need_array("dot_product", b, rank=(1,))
    if a.shape != b.shape:
        raise LangError(f"dot_product(): shapes differ ({fmt_shape(a.shape)} vs {fmt_shape(b.shape)})")
    return sum((x.conjugate() if isinstance(x, complex) else x) * y for x, y in zip(a.data, b.data))


def _allocated(x):
    raise LangError("allocated() needs the name of an allocatable array")   # apply() handles real calls


def _present(x):
    raise LangError("present() needs a single value")   # apply() handles real calls


def _reduce(name, f):
    return lambda a: f(_need_array(name, a).data)


def _round_half_away(x):
    """Fortran's nearest whole number: halves round away from zero (Python's round() rounds to even)."""
    return math.copysign(math.floor(abs(x) + 0.5), x)


def _sign(a, b):
    """|a| with the sign of b (a real b of -0.0 counts as negative, like Fortran)."""
    if isinstance(a, int) and isinstance(b, int):
        return -abs(a) if b < 0 else abs(a)
    return math.copysign(abs(a), b)


def _modulo(a, b):
    """The remainder with the sign of b (mod() takes the sign of a)."""
    if isinstance(a, int) and isinstance(b, int):
        return a % b
    if b == 0:
        raise ZeroDivisionError("modulo by zero")
    return a - math.floor(a / b) * b


def _atan(y, x=None):
    if x is None:
        return cmath.atan(y) if isinstance(y, complex) else math.atan(y)
    return math.atan2(y, x)


def _kind_of(x, name):
    if isinstance(x, Array):
        if not x.data:
            raise LangError(f"{name}() of an empty array")
        x = x.data[0]
    return type_of(x)


def _huge(x):
    """The largest value of x's type: a 32-bit integer (like Fortran's default) or a double."""
    kind = _kind_of(x, "huge")
    if kind in INT_RANGE: return INT_CLASS[kind](INT_RANGE[kind][1])
    if kind == "real": return sys.float_info.max
    if kind == "real32": return Real32(REAL32_MAX)
    raise LangError(f"huge() needs an integer or a real, got {kind}")


def _epsilon(x):
    kind = _kind_of(x, "epsilon")
    if kind == "real32": return Real32(REAL32_EPSILON)
    if kind != "real":
        raise LangError("epsilon() needs a real")
    return sys.float_info.epsilon


def _tiny(x):
    kind = _kind_of(x, "tiny")
    if kind == "real32": return Real32(REAL32_TINY)
    if kind != "real":
        raise LangError("tiny() needs a real")
    return sys.float_info.min


def _complex_aware(real_fn, complex_fn):
    """A math function that also accepts a complex argument (the real one rejects negative square roots,
    out-of-range arcsines and so on instead of leaving the real line)."""
    def apply(x):
        try:
            return real_fn(x)
        except TypeError:
            if isinstance(x, complex):
                return complex_fn(x)
            raise
    return apply


def _real(x):
    return x.real if isinstance(x, complex) else float(x)


def _to_int(kind):
    """int8(x), int16(x), int(x), int64(x): truncate a real, check that the value fits."""
    def convert(x):
        if isinstance(x, complex) or isinstance(x, (str, bool)):
            raise TypeError(f"needs a number, got {type_of(x)}")
        return make_int(kind, math.trunc(x), f"{kind}()" if kind != "integer" else "int()")
    return convert


def _to_real32(x):
    if isinstance(x, (complex, str, bool)):
        raise TypeError(f"needs a number, got {type_of(x)}")
    return Real32(round32(float(x)))


def _cmplx(a, b=None):
    if isinstance(a, complex):
        if b is not None:
            raise TypeError("cmplx(z) takes a single complex argument")
        return a
    return complex(a, 0.0 if b is None else b)


def _need_complex(name, f):
    def apply(z):
        if not isinstance(z, complex):
            raise TypeError(f"needs a complex number, got {type_of(z)}")
        return f(z)
    return apply


def _need_string(name, x):
    if not isinstance(x, str):
        raise TypeError(f"needs a string, got {type_of(x)}")
    return x


def _len(s):
    return len(_need_string("len", s))


def _achar(i):
    if type_of(i) not in INT_RANGE or not 0 <= i <= 0x10FFFF:
        raise TypeError(f"needs a character code from 0 to 1114111, got {show_value(i)}")
    return chr(int(i))


def _iachar(c):
    if not isinstance(c, str) or len(c) != 1:
        raise TypeError(f"needs a one-character string, got {show_value(c)}")
    return ord(c)


_random = random.Random()


def _count(name, what, n):
    if type_of(n) not in INT_RANGE or n < 0:
        raise TypeError(f"{what} must be a whole number of 0 or more, got {n!r}")
    return int(n)


def _fixed(x, decimals, width=None):
    """x with exactly `decimals` digits after the point; with a width, right-aligned like Fortran's Fw.d
    (stars if it does not fit; a leading 0 is dropped before giving up)."""
    if type_of(x) not in NUMERIC or isinstance(x, complex):
        raise TypeError(f"needs a real or integer number, got {type_of(x)}")
    text = f"{float(x):.{_count('fixed', 'the number of decimals', decimals)}f}"
    if decimals == 0:
        text += "."                                        # Fortran's F4.0 shows the point: "2."
    if width is None or width == 0:
        return text
    width = _count("fixed", "the width", width)
    if len(text) > width:
        text = text.replace("0.", ".", 1) if text.lstrip("-").startswith("0.") else text
    return text.rjust(width) if len(text) <= width else "*" * width


def _pad(side):
    def pad(x, width, fill=" "):
        """Align x in `width` characters (left or right). Text that is too long is cut to its first
        characters, a number that does not fit is shown as stars (as Fortran does)."""
        width = _count(side, "the width", width)
        if not isinstance(fill, str) or len(fill) != 1:
            raise TypeError("the padding must be a one-character string")
        if isinstance(x, (Array, Record)) or x is NULL or isinstance(x, complex):
            raise TypeError(f"needs text or a number, got {type_of(x) if x is not NULL else 'null'}")
        text = x if isinstance(x, str) else ("true" if x is True else "false" if x is False else
                                           shortest32(x) if type(x) is Real32 else str(x))
        if width == 0:                                               # width 0: just the text (Fortran's I0)
            return text
        if len(text) > width:
            return text[:width] if isinstance(x, str) else "*" * width
        if side == "right" and fill == "0" and text[:1] in "-+" and not isinstance(x, str):
            return text[0] + text[1:].rjust(width - 1, "0")           # the sign stays in front: -0042
        return text.rjust(width, fill) if side == "right" else text.ljust(width, fill)
    return pad


def _repeat(text, n):
    if not isinstance(text, str):
        raise TypeError(f"needs a string, got {type_of(text)}")
    return text * _count("repeat", "the count", n)


def _trim(s):
    return _need_string("trim", s).rstrip(" ")


BUILTINS = {
    "trim": _trim, "len_trim": lambda s: len(_trim(s)),
    "fixed": _fixed, "right": _pad("right"), "left": _pad("left"), "repeat": _repeat,
    "len": _len, "achar": _achar, "iachar": _iachar,
    "random": lambda: _random.random(),
    "argument": None, "argument_count": None, "read_line": None,   # need the interpreter: see call_builtin
    "size": _size, "shape": _shape, "reshape": _reshape, "matmul": _matmul,
    "transpose": _transpose, "dot_product": _dot_product, "allocated": _allocated, "present": _present,
    "sum": _reduce("sum", sum), "maxval": _reduce("maxval", max), "minval": _reduce("minval", min),
    "any": _reduce("any", any), "all": _reduce("all", all),
    "max": max, "min": min, "product": _reduce("product", math.prod),
    "huge": _huge, "epsilon": _epsilon, "tiny": _tiny,
}
# Elementwise functions: applied to every element when given an array.
ELEMENTAL = {
    "abs": abs, "real": _real, "int": _to_int("integer"), "atan": _atan,
    "int8": _to_int("int8"), "int16": _to_int("int16"), "int32": _to_int("integer"), "int64": _to_int("int64"),
    "real32": _to_real32, "real64": _real, "atan2": math.atan2,
    "hypot": math.hypot, "gamma": math.gamma, "log_gamma": math.lgamma, "erf": math.erf, "erfc": math.erfc,
    "floor": math.floor, "ceiling": math.ceil, "nint": lambda x: int(_round_half_away(x)),
    "aint": lambda x: float(math.trunc(x)), "anint": _round_half_away,
    "sign": _sign, "modulo": _modulo, "dim": lambda a, b: max(a - b, 0),
    "mod": lambda a, b: int(math.fmod(a, b)) if isinstance(a, int) and isinstance(b, int) else math.fmod(a, b),
    "cmplx": _cmplx, "aimag": _need_complex("aimag", lambda z: z.imag),
    "conjg": _need_complex("conjg", lambda z: z.conjugate()),
}
# These also take a complex argument and then give a complex result.
ELEMENTAL.update({name: _complex_aware(getattr(math, name), getattr(cmath, name)) for name in (
    "sqrt", "sin", "cos", "tan", "asin", "acos", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
    "exp", "log", "log10")})


# Functions whose result has the width of their arguments.
KEEPS_KIND = {"sqrt", "abs", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
              "asinh", "acosh", "atanh", "exp", "log", "log10", "gamma", "log_gamma", "erf", "erfc", "hypot",
              "sign", "modulo", "dim", "mod", "aint", "anint", "sum", "product", "maxval", "minval",
              "dot_product", "matmul", "max", "min"}


# A variable slot is [type, value, is_array, intent]. `value` is None until
# assigned (scalars) or allocated (allocatable arrays); `intent` is None for
# ordinary locals, "const" for constants, else "in" / "out" / "inout".
# An allocatable array's slot has a fifth entry, its rank.
TYPE, VALUE, IS_ARRAY, INTENT, RANK = range(5)
DEFAULTS = {"integer": 0, "real": 0.0, "logical": False, "string": "", "complex": 0j,
            "int8": Int8(0), "int16": Int16(0), "int64": Int64(0), "real32": Real32(0.0)}

# A reference to something assignable is (slot, subs, name). `subs` is None for
# the whole variable, else one entry per dimension: an int or an inclusive (lo, hi).


def _span(s):
    return (s, s) if isinstance(s, int) else s


def overlaps(a, b):
    """Could two subscript lists of the same array touch a common element?"""
    if a is None or b is None:
        return True
    return all(max(_span(x)[0], _span(y)[0]) <= min(_span(x)[1], _span(y)[1]) for x, y in zip(a, b))


PURE_BUILTINS = (set(BUILTINS) | set(ELEMENTAL)) - {"allocated", "present", "random", "read_line", "argument",
                                                    "argument_count"}


def iter_apply(e):
    """Every Apply node inside an expression (None gives nothing)."""
    if isinstance(e, Apply):
        yield e
        for a in e.args:
            yield from iter_apply(a.value if isinstance(a, NamedArg) else a)
    elif isinstance(e, Unary):
        yield from iter_apply(e.operand)
    elif isinstance(e, Binary):
        yield from iter_apply(e.left)
        yield from iter_apply(e.right)
    elif isinstance(e, ArrayLit):
        for x in e.items:
            yield from iter_apply(x)


class Scope:
    """What a procedure (or the program) can see besides its own variables:
    procedures and constants, both its own and the ones it imported."""
    def __init__(self, funcs, consts, records=None):
        self.funcs, self.consts = funcs, consts
        self.records = {} if records is None else records


class LineInput:
    """The program's standard input, one line at a time. `source` is None (the real stdin), a string, or an
    iterable of lines."""
    def __init__(self, source=None):
        if source is None:
            self.lines = iter(sys.stdin)
        elif isinstance(source, str):
            self.lines = iter(source.splitlines())
        else:
            self.lines = iter(source)

    def next_line(self):
        line = next(self.lines, None)
        return None if line is None else line.rstrip("\r\n")


ITEM_RE = re.compile(r"""'[^']*'|"[^"]*"|[^\s,]+""")
INT_RE = re.compile(r"[+-]?\d+")
REAL_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?")
TRUE_WORDS, FALSE_WORDS = {"t", ".t.", "true", ".true."}, {"f", ".f.", "false", ".false."}
READ_NAMES = {"integer": "an integer", "int8": "an integer", "int16": "an integer", "int64": "an integer",
              "real": "a real number", "real32": "a real number", "logical": "true or false",
              "string": "text"}


def read_value(typ, token):
    """The value a list-directed `read` makes of one input item, or None if it is not a `typ`."""
    quoted = token[0] in "'\""
    if typ == "string":
        return token[1:-1] if quoted else token
    if quoted:
        return None
    if typ in INT_RANGE:
        if not INT_RE.fullmatch(token):
            return None
        try:
            return make_int(typ, int(token), "read")
        except LangError:
            return None
    if typ in ("real", "real32"):
        if not REAL_RE.fullmatch(token):
            return None
        x = float(token.lower().replace("d", "e"))
        try:
            return x if typ == "real" else Real32(round32(x))
        except LangError:
            return None
    if typ == "logical":
        word = token.lower()
        return True if word in TRUE_WORDS else False if word in FALSE_WORDS else None
    return None


class Interp:
    def __init__(self, unit, out=print, args=(), stdin=None):
        self.unit, self.out = unit, out
        self.input = LineInput(stdin)
        self.pending = ""                 # text written with `write` that is waiting for its newline
        self.args = list(args)            # the program's command-line arguments, for argument(n)
        self.scopes = {}                  # id(procedure) -> Scope
        self.exports = {}                 # module name -> {name: Function | const slot}
        self.loading = []
        self.owner = {}                   # id(RecordDef) -> the Scope its field types are looked up in
        self.scope = None
        self.link()

    def link(self):
        """Give every procedure its scope: top-level procedures share one namespace,
        a module's procedures see only that module plus what it imports."""
        unit = self.unit
        base = Scope(dict(unit.functions), {}, dict(unit.records))    # what top-level records can see
        for r in unit.records.values():
            self.owner[id(r)] = base
            if r.name in unit.functions or r.name in BUILTINS or r.name in ELEMENTAL:
                raise LangError(f"line {r.line}: the record '{r.name}' has the same name as a procedure")
        self.check_records(unit.records, base)
        self.check_defaults(unit.records, base)
        for f in unit.functions.values():
            self.scopes[id(f)] = self.import_into(Scope(dict(unit.functions), {}, dict(unit.records)), f.uses)
        prog = unit.program
        self.program_scope = (self.import_into(Scope(dict(unit.functions), {}, dict(unit.records)), prog.uses)
                              if prog else None)
        for name in unit.modules:
            self.load_module(name, 0)

    def check_records(self, records, scope):
        """Record definitions need known field types, literal array sizes, unique field
        names, and cannot contain themselves."""
        for r in records.values():
            if r.name in DEFAULTS or r.name in ("array", "?"):
                raise LangError(f"line {r.line}: '{r.name}' cannot be the name of a record")
            seen = set()
            for f in r.fields:
                if f.name in seen:
                    raise LangError(f"line {f.line}: record '{r.name}' has two fields called '{f.name}'")
                seen.add(f.name)
                if base_type(f.type) not in DEFAULTS and base_type(f.type) not in scope.records:
                    raise LangError(f"line {f.line}: field '{f.name}' of record '{r.name}' has unknown type '{f.type}'")
                for d in f.dims or []:
                    if not (isinstance(d, Num) and isinstance(d.value, int) and d.value >= 0):
                        raise LangError(f"line {f.line}: an array field of a record needs a literal size")
                for n in iter_apply(f.init):
                    if n.name in scope.records:
                        continue
                    if n.name in scope.funcs or n.name not in PURE_BUILTINS:
                        raise LangError(f"line {f.line}: the default of field '{f.name}' calls '{n.name}', "
                                        f"which is neither a record nor a built-in function "
                                        f"(defaults are constants)")

        def contains(name, path):
            if name in path:
                raise LangError(f"line {records[name].line}: record '{name}' contains itself: "
                                + " -> ".join(path[path.index(name):] + (name,)))
            for f in scope.records[name].fields:
                if base_type(f.type) in scope.records:
                    contains(base_type(f.type), path + (name,))

        for name in records:
            contains(name, ())

    def check_defaults(self, records, scope):
        """Build one of each record now, so a bad default fails when the program loads."""
        saved, self.scope = self.scope, scope
        try:
            for r in records.values():
                self.new_record(r)
        finally:
            self.scope = saved

    def import_into(self, scope, uses):
        taken = set(scope.funcs) | set(scope.consts) | set(scope.records)
        for u in uses:
            exports = self.load_module(u.module, u.line)
            mod = self.unit.modules[u.module]
            known = set(mod.functions) | {d.name for d in mod.consts} | set(mod.records)
            for name in u.names:
                if name not in known:
                    raise LangError(f"line {u.line}: module '{u.module}' has no '{name}'")
                if name not in exports:
                    raise LangError(f"line {u.line}: '{name}' is private to module '{u.module}' "
                                    f"(mark it 'public' there to import it)")
                if name in taken:
                    raise LangError(f"line {u.line}: '{name}' is already defined here; "
                                    f"it cannot also be imported from '{u.module}'")
                taken.add(name)
                item = exports[name]
                if isinstance(item, Function):
                    scope.funcs[name] = item
                elif isinstance(item, RecordDef):
                    scope.records[name] = item
                else:
                    scope.consts[name] = item
        return scope

    def load_module(self, name, line):
        if name in self.exports:
            return self.exports[name]
        mod = self.unit.modules.get(name)
        if mod is None:
            raise LangError(f"line {line}: no module named '{name}'")
        if name in self.loading:
            raise LangError(f"line {mod.line}: modules use each other in a cycle: "
                            + " -> ".join(self.loading[self.loading.index(name):] + [name]))
        self.loading.append(name)
        scope = Scope(dict(mod.functions), {}, dict(mod.records))
        for r in mod.records.values():
            self.owner[id(r)] = scope
        self.import_into(scope, mod.uses)
        self.check_records(mod.records, scope)
        for f in mod.functions.values():
            self.scopes[id(f)] = scope
        saved, self.scope = self.scope, scope          # initializers may call the module's functions
        env = dict(scope.consts)
        for d in mod.consts:
            self.declare(d, env)
            scope.consts[d.name] = env[d.name]
        self.scope = saved
        self.check_defaults(mod.records, scope)         # defaults may use the constants above
        self.loading.pop()
        self.exports[name] = {
            **{f.name: f for f in mod.functions.values() if f.public},
            **{r.name: r for r in mod.records.values() if r.public},
            **{d.name: env[d.name] for d in mod.consts if d.public}}
        return self.exports[name]

    def run(self):
        """Run the program; returns its exit code (0, or the value given to `stop`)."""
        self.scope = self.program_scope
        try:
            self.exec_block(self.unit.program.body, dict(self.scope.consts))
        except _Stop as stop:
            self.finish_line()
            return stop.code
        self.finish_line()
        return 0

    def emit(self, text, newline):
        if self.out is print:                 # the real terminal: show prompts at once
            sys.stdout.write(text + ("\n" if newline else ""))
            if not newline:
                sys.stdout.flush()
        elif newline:
            self.out(self.pending + text)
            self.pending = ""
        else:
            self.pending += text

    def finish_line(self):
        if self.pending:
            self.out(self.pending)
            self.pending = ""

    def exec_block(self, stmts, env):
        for s in stmts:
            self.exec(s, env)

    def exec(self, s, env):
        k = type(s)
        if k is list:                       # multi-declaration
            self.exec_block(s, env)
        elif k is Decl:
            self.declare(s, env)
        elif k is Assign:
            ref = self.resolve(s.target, env, s.line)
            if (ref.root[INTENT] in ("out", "inout")) != s.update:
                raise LangError(f"line {s.line}: 'update' must be used exactly when assigning to an "
                                f"out or inout parameter ('{ref[2]}')")
            self.write(ref, self.eval(s.value, env), s.line)
        elif k is If:
            for cond, body in s.branches:
                if self.truth(self.eval(cond, env)):
                    self.exec_block(body, env)
                    return
            self.exec_block(s.orelse, env)
        elif k is Select:
            n = self.select_index(s.subject, [c.values for c in s.cases], env, s.line)
            try:
                while n is not None and n < len(s.cases):
                    self.exec_block(s.cases[n].body, env)
                    if not s.flow:
                        break
                    n += 1                  # `select case fallthrough`: run the next case's body too
            except _Break:
                pass
        elif k is Do:
            ref = self.resolve(Var(s.var), env, s.line)
            lo, hi = self.eval(s.lo, env), self.eval(s.hi, env)
            step = self.eval(s.step, env) if s.step else 1
            if step == 0:
                raise LangError(f"line {s.line}: do step cannot be zero")
            i, exited = lo, False
            while (i <= hi) if step > 0 else (i >= hi):
                self.write(ref, i, s.line)
                try:
                    self.exec_block(s.body, env)
                except _Cycle as e:
                    if not _mine(e, s):
                        raise
                except _Exit as e:
                    if not _mine(e, s):
                        raise
                    exited = True
                    break
                i += step
            if not exited:                  # like Fortran: the variable ends one step past the range
                self.write(ref, i, s.line)
        elif k is DoWhile:
            while self.truth(self.eval(s.cond, env)):
                try:
                    self.exec_block(s.body, env)
                except _Cycle as e:
                    if not _mine(e, s):
                        raise
                except _Exit as e:
                    if not _mine(e, s):
                        raise
                    break
        elif k is Print:
            self.emit(" ".join(self.show(self.eval(a, env)) for a in s.args), s.newline)
        elif k is Read:
            self.read_stmt(s, env)
        elif k is Stop:
            code = 0 if s.code is None else self.eval(s.code, env)
            if type_of(code) not in INT_RANGE:
                raise LangError(f"line {s.line}: 'stop' takes an integer exit code, got {type_of(code)}")
            raise _Stop(int(code))
        elif k is Call:
            self.call_subroutine(s, env)
        elif k is Allocate:
            self.allocate(s, env)
        elif k is Deallocate:
            slot = self.allocatable(s.name, env, s.line)
            if slot[VALUE] is None:
                raise LangError(f"line {s.line}: cannot deallocate '{s.name}': it is not allocated")
            slot[VALUE] = None
        elif k is Return:
            raise _Return(self.eval(s.value, env) if s.value else None)
        elif k is Exit:
            raise _Exit(s.label)
        elif k is Cycle:
            raise _Cycle(s.label)
        elif k is Break:
            raise _Break()
        elif k is ExprStmt:
            self.eval(s.expr, env)

    def declare(self, s, env):
        where = f"line {s.line}"
        if s.name in env:
            raise LangError(f"{where}: '{s.name}' already declared")
        sub = None
        base = base_type(s.type)
        if base not in DEFAULTS:
            sub = self.scope.records.get(base)
            if sub is None:
                raise LangError(f"{where}: unknown type '{base}'")
            if s.allocatable:
                raise LangError(f"{where}: allocatable arrays of records are not supported")
        if s.allocatable:
            env[s.name] = [s.type, None, True, None, len(s.dims)]
            return
        if s.dims is None:
            if s.init is not None:
                val = coerce(s.type, self.eval(s.init, env), where)
            elif s.type.endswith("?"):
                val = NULL                               # an optional variable starts absent
            else:
                val = self.new_record(sub) if sub is not None else DEFAULTS[s.type]
            env[s.name] = [s.type, val, False, "const" if s.const else None]
            return
        shape = [self.eval(d, env) for d in s.dims]
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in shape):
            raise LangError(f"{where}: array dimensions must be non-negative integers")
        n = math.prod(shape)
        data = [self.new_record(sub) for _ in range(n)] if sub else [DEFAULTS[s.type]] * n
        if s.init is not None:
            init = self.eval(s.init, env)
            if isinstance(init, Array):
                if init.shape != tuple(shape):
                    raise LangError(f"{where}: initializer has shape {fmt_shape(init.shape)}, "
                                    f"'{s.name}' has shape {fmt_shape(shape)}")
                data = [coerce(s.type, x, where) for x in init.data]
            else:
                data = [coerce(s.type, init, where) for _ in range(n)]      # one copy per element
        env[s.name] = [s.type, Array(shape, data), True, "const" if s.const else None]

    def new_record(self, rdef, blank=False):
        """A record whose fields hold their type's default value (0, 0.0, false, ""). A `blank`
        record has no values at all: that is what an `out` parameter starts as."""
        owner = self.owner[id(rdef)]
        fields = {}
        for f in rdef.fields:
            sub = owner.records.get(base_type(f.type))
            if f.dims is None:
                if f.type.endswith("?"):
                    value = None if blank else NULL      # an optional field starts absent
                else:
                    value = self.new_record(sub, blank) if sub else (None if blank else DEFAULTS[f.type])
                fields[f.name] = [f.type, value, False, None]
            else:
                shape = [d.value for d in f.dims]
                n = math.prod(shape)
                data = ([self.new_record(sub, blank) for _ in range(n)] if sub
                        else [None if blank else DEFAULTS[base_type(f.type)]] * n)
                fields[f.name] = [f.type, Array(shape, data), True, None]
        rec = Record(rdef, fields)
        for f in rdef.fields:
            if f.init is not None:                  # a declared default applies even to a blank record
                self.apply_default(rec, f, owner)
        return rec

    def apply_default(self, rec, f, owner):
        """Give a field its declared default, evaluated in the scope the record was defined in."""
        where = f"line {f.line}: the default of field '{f.name}' in record '{rec.rdef.name}'"
        saved, self.scope = self.scope, owner
        try:
            value = self.eval(f.init, dict(owner.consts))
        except LangError as err:
            raise LangError(f"{where}: {err}")
        finally:
            self.scope = saved
        slot = rec.fields[f.name]
        if slot[IS_ARRAY]:
            arr = slot[VALUE]
            if isinstance(value, Array):
                if value.shape != arr.shape:
                    raise LangError(f"{where} has shape {fmt_shape(value.shape)}, the field has "
                                    f"shape {fmt_shape(arr.shape)}")
                values = value.data
            else:
                values = [value] * len(arr.data)
            slot[VALUE] = Array(arr.shape, [coerce(slot[TYPE], x, where) for x in values])
        else:
            slot[VALUE] = coerce(slot[TYPE], value, where)

    def construct(self, rdef, e, env):
        """`point(1.0, 2.0)` gives every field in order (trailing fields with a default may be left
        out); `point(x = 1.0)` gives only the named ones; positional arguments may come first.
        Fields not given keep their defaults."""
        line = f"line {e.line}"
        pos = [a for a in e.args if not isinstance(a, NamedArg)]
        named = [a for a in e.args if isinstance(a, NamedArg)]
        if len(pos) > len(rdef.fields):
            raise LangError(f"{line}: {rdef.name} has {len(rdef.fields)} field(s), got {len(pos)} value(s)")
        if not named and any(f.init is None for f in rdef.fields[len(pos):]):
            raise LangError(f"{line}: {rdef.name}() needs all {len(rdef.fields)} field(s) "
                            f"({', '.join(f.name for f in rdef.fields)}), or name the ones you give "
                            f"(only trailing fields with a default may be left out)")
        rec = self.new_record(rdef)
        given = set()
        for f, a in zip(rdef.fields, pos):
            self.set_field(rec, f.name, self.eval(a, env), line)
            given.add(f.name)
        for a in named:
            if a.name not in rec.fields:
                raise LangError(f"{line}: {rdef.name} has no field '{a.name}'")
            if a.name in given:
                raise LangError(f"{line}: field '{a.name}' is given twice")
            given.add(a.name)
            self.set_field(rec, a.name, self.eval(a.value, env), line)
        return rec

    def set_field(self, rec, name, value, where):
        slot = rec.fields[name]
        label = f"{where}: field '{name}' of {rec.rdef.name}"
        if not slot[IS_ARRAY]:
            slot[VALUE] = coerce(slot[TYPE], value, label)
            return
        arr = slot[VALUE]
        if not isinstance(value, Array) or value.shape != arr.shape:
            got = f"shape {fmt_shape(value.shape)}" if isinstance(value, Array) else type_of(value)
            raise LangError(f"{label} is an array of shape {fmt_shape(arr.shape)}, got {got}")
        slot[VALUE] = Array(arr.shape, [coerce(slot[TYPE], x, label) for x in value.data])

    def allocatable(self, name, env, line):
        """The slot of a local allocatable array, for allocate / deallocate / allocated."""
        slot = self.lookup(name, env, line)
        if len(slot) <= RANK:
            raise LangError(f"line {line}: '{name}' is not an allocatable array")
        return slot

    def allocate(self, s, env):
        slot = self.allocatable(s.name, env, s.line)
        if slot[VALUE] is not None:
            raise LangError(f"line {s.line}: cannot allocate '{s.name}': it is already allocated "
                            f"(deallocate it first)")
        if len(s.dims) != slot[RANK]:
            raise LangError(f"line {s.line}: '{s.name}' has {slot[RANK]} dimension(s) but "
                            f"{len(s.dims)} extent(s) were given to allocate")
        shape = [self.eval(d, env) for d in s.dims]
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in shape):
            raise LangError(f"line {s.line}: array extents must be non-negative integers")
        slot[VALUE] = Array(shape, [DEFAULTS[slot[TYPE]]] * math.prod(shape))

    def not_allocated(self, name, line=None):
        where = f"line {line}: " if line else ""
        return LangError(f"{where}'{name}' is not allocated; use 'allocate {name}(...)' first")

    def show(self, v):
        if isinstance(v, bool): return "true" if v else "false"
        if isinstance(v, complex): return f"({v.real}, {v.imag})"
        if type(v) is Real32: return shortest32(v)
        if isinstance(v, Record):
            missing = v.first_unassigned()
            if missing:
                raise LangError(f"cannot print a {v.rdef.name}: its field '{missing}' has no value yet")
            return v.rdef.name + "(" + ", ".join(f"{n}={self.show(s[VALUE])}" for n, s in v.fields.items()) + ")"
        if isinstance(v, Array):
            def rec(prefix):                       # first dimension is outermost
                d = len(prefix)
                if d == v.rank:
                    return self.show(v.data[v.section(prefix)[0][0]])
                return "[" + ", ".join(rec(prefix + (i,)) for i in range(1, v.shape[d] + 1)) + "]"
            return rec(())
        return str(v)

    def truth(self, v):
        if not isinstance(v, bool):
            raise LangError(f"condition must be logical, got {type_of(v)}")
        return v

    def lookup(self, name, env, line=None):
        if name not in env:
            raise LangError(f"line {line}: '{name}' is not declared" if line else f"'{name}' is not declared")
        return env[name]

    # -- subscripts and references
    def subscripts(self, arr, args, env, line, name):
        if len(args) != arr.rank:
            raise LangError(f"line {line}: '{name}' has {arr.rank} dimension(s) but {len(args)} subscript(s) were given")
        subs = []
        for d, (a, n) in enumerate(zip(args, arr.shape), 1):
            where = f" in dimension {d}" if arr.rank > 1 else ""
            if isinstance(a, Slice):
                lo = self.eval(a.lo, env) if a.lo else 1
                hi = self.eval(a.hi, env) if a.hi else n
                self.require_int(lo, line); self.require_int(hi, line)
                if lo <= hi and (lo < 1 or hi > n):
                    raise LangError(f"line {line}: slice {lo}:{hi} out of bounds 1..{n}{where}")
                subs.append((lo, hi))
            else:
                i = self.eval(a, env)
                self.require_int(i, line)
                if not 1 <= i <= n:
                    raise LangError(f"line {line}: index {i} out of bounds 1..{n}{where}")
                subs.append(i)
        return tuple(subs)

    def require_int(self, v, line):
        if not isinstance(v, int) or isinstance(v, bool):
            raise LangError(f"line {line}: array index must be integer")

    def resolve(self, t, env, line):
        """Turn a variable, array element, array section or record field into a reference."""
        if isinstance(t, FieldAccess):
            return self.resolve_field(t, env, line)
        if not isinstance(t, (Var, Apply)):
            raise LangError(f"line {line}: this must be a variable or array element")
        slot = self.lookup(t.name, env, line)
        if slot[IS_ARRAY] and slot[VALUE] is None:
            raise self.not_allocated(t.name, line)
        if isinstance(t, Var):
            return Ref(slot, None, t.name)
        if not slot[IS_ARRAY]:
            if slot[TYPE] == "string":
                return Ref(slot, self.substring_bounds(slot, t.args, env, line, t.name), t.name)
            raise LangError(f"line {line}: '{t.name}' is not an array")
        return Ref(slot, self.subscripts(slot[VALUE], t.args, env, line, t.name), t.name)

    def substring_bounds(self, slot, args, env, line, name):
        """The (lo, hi) of s(lo:hi), 1-based and inclusive, either end optional, as a one-entry subscript list."""
        text = slot[VALUE]
        if text is None:
            raise LangError(f"line {line}: '{name}' is used before it is assigned")
        if len(args) != 1 or not isinstance(args[0], Slice):
            raise LangError(f"line {line}: '{name}' is a string; take part of it with {name}(lo:hi)")
        lo = 1 if args[0].lo is None else self.eval(args[0].lo, env)
        hi = len(text) if args[0].hi is None else self.eval(args[0].hi, env)
        if type_of(lo) not in INT_RANGE or type_of(hi) not in INT_RANGE:
            raise LangError(f"line {line}: substring bounds of '{name}' must be integers")
        if lo < 1 or hi > len(text) or lo > hi + 1:
            raise LangError(f"line {line}: substring {name}({lo}:{hi}) is outside the string "
                            f"(length {len(text)})")
        return [(int(lo), int(hi))]

    def is_reference(self, e, env):
        """Does this expression name storage (a variable, array element or field)?"""
        if isinstance(e, FieldAccess):
            return self.is_reference(e.base, env)
        return isinstance(e, Var) or (isinstance(e, Apply) and e.name in env)

    def record_of(self, base, env, line, temp_ok=False):
        """The Record that `base` names, with the variable it lives in: (record, ref to it)."""
        if self.is_reference(base, env):
            ref = self.resolve(base, env, line)
            value = self.read(ref)
            if isinstance(value, Array):
                raise LangError(f"line {line}: '{ref[2]}' is an array; pick one element before using a field")
            if value is NULL:
                raise LangError(f"line {line}: '{ref[2]}' is null, so it has no fields (check with present())")
            if not isinstance(value, Record):
                raise LangError(f"line {line}: '{ref[2]}' is not a record")
            return value, ref
        if not temp_ok:
            raise LangError(f"line {line}: cannot assign to a field of a computed value")
        value = self.eval(base, env)
        if not isinstance(value, Record):
            raise LangError(f"line {line}: this value is not a record")
        return value, Ref([value.rdef.name, value, False, "const"], None, value.rdef.name)

    def resolve_field(self, t, env, line, temp_ok=False):
        rec, ref = self.record_of(t.base, env, line, temp_ok)
        slot = rec.fields.get(t.field)
        if slot is None:
            raise LangError(f"line {line}: {rec.rdef.name} '{ref[2]}' has no field '{t.field}'")
        name = f"{ref[2]}.{t.field}"
        fpath = ref.fpath + (t.field,)
        if t.args is None:
            return Ref(slot, None, name, ref.root, ref.rsubs, fpath)
        if not slot[IS_ARRAY]:
            raise LangError(f"line {line}: field '{name}' is not an array")
        return Ref(slot, self.subscripts(slot[VALUE], t.args, env, line, name), name, ref.root, ref.rsubs, fpath)

    def read(self, ref):
        slot, subs, _ = ref
        if subs is None:
            return slot[VALUE]
        if not slot[IS_ARRAY]:                  # a substring
            return slot[VALUE][subs[0][0] - 1:subs[0][1]]
        positions, shape = slot[VALUE].section(subs)
        data = slot[VALUE].data
        if not shape:                       # every subscript was a scalar
            return data[positions[0]]
        return Array(shape, [data[p] for p in positions])

    def write(self, ref, v, line):
        slot, subs, name = ref
        part = "part of " if ref.fpath else ""
        if ref.root[INTENT] == "in":
            raise LangError(f"line {line}: '{name}' is {part}an 'in' parameter and cannot be assigned")
        if ref.root[INTENT] == "const":
            raise LangError(f"line {line}: '{name}' is {part}a constant and cannot be assigned")
        where = f"line {line}"
        if not slot[IS_ARRAY]:
            if subs is not None:                # a substring keeps the string's length
                lo, hi = subs[0]
                if not isinstance(v, str) or len(v) != hi - lo + 1:
                    raise LangError(f"{where}: {name}({lo}:{hi}) holds {hi - lo + 1} characters; "
                                    f"got {show_value(v) if isinstance(v, str) else type_of(v)}")
                slot[VALUE] = slot[VALUE][:lo - 1] + v + slot[VALUE][hi:]
                return
            slot[VALUE] = coerce(slot[TYPE], v, where)
            return
        arr = slot[VALUE]
        if subs is None:
            positions, shape = range(len(arr.data)), list(arr.shape)
        else:
            positions, shape = arr.section(subs)
        if isinstance(v, Array):
            if list(v.shape) != shape:
                raise LangError(f"{where}: array shape mismatch: {fmt_shape(shape)} vs {fmt_shape(v.shape)}")
            values = v.data
        else:
            values = [v] * len(positions)
        values = [coerce(slot[TYPE], x, where) for x in values]   # validate before writing
        for p, x in zip(positions, values):
            arr.data[p] = x

    # -- expressions
    def eval(self, e, env):
        k = type(e)
        if k in (Num, Str, Bool): return e.value
        if k is Var:
            slot = self.lookup(e.name, env)
            v = slot[VALUE]
            if v is None and slot[IS_ARRAY]:
                raise self.not_allocated(e.name)
            if v is None:
                raise LangError(f"'{e.name}' is used before it is assigned")
            return v
        if k is ArrayLit:
            items = [self.eval(x, env) for x in e.items]
            if any(isinstance(x, Array) for x in items):
                raise LangError("array literals hold scalars; use reshape() to build a matrix")
            return Array((len(items),), items)
        if k is FieldAccess:
            return self.field_value(e, env)
        if k is NamedArg:
            raise LangError("named arguments are only for building records")
        if k is Unary:
            v = self.eval(e.operand, env)
            if v is NULL:
                raise LangError(f"'{e.op}' cannot be used with null; check with present() or use ??")
            if isinstance(v, Record):
                raise LangError(f"records cannot be used with '{e.op}'; use their fields")
            f = (lambda x: not self.truth(x)) if e.op == "not" else self.negate if e.op == "-" else (lambda x: x)
            return Array(v.shape, [f(x) for x in v.data]) if isinstance(v, Array) else f(v)
        if k is Binary:
            if e.op == "??":                          # the right side is only evaluated when needed
                left = self.eval(e.left, env)
                return self.eval(e.right, env) if left is NULL else left
            left, right = self.eval(e.left, env), self.eval(e.right, env)
            if type(left) is not type(right):
                left, right = self.adapt_literals(e, left, right)
            return self.binary(e.op, left, right)
        if k is Null: return NULL
        if k is Apply: return self.apply(e, env)
        if k is SelectExpr: return self.select_expr(e, env)
        raise LangError(f"cannot evaluate {k.__name__}")

    def adapt_literals(self, e, left, right):
        """A real literal next to a real32 value is a real32 (as in Fortran, where `x * 2.0` is single
        precision when x is), instead of promoting the whole expression to a double."""
        if type(right) is float and _is_literal(e.right) and _is_real32(left):
            right = SINGLE_LITERALS.get(right) or single_literal(right)
        elif type(left) is float and _is_literal(e.left) and _is_real32(right):
            left = SINGLE_LITERALS.get(left) or single_literal(left)
        return left, right

    def negate(self, x):
        t = type(x)
        if t is float or t is complex:
            return -x
        if t is bool:
            raise LangError("'-' needs a number, got logical")
        if t is str:
            raise LangError("'-' needs a number, got string")
        return tag(TYPE_NAMES[t], -x, f"-{x}")

    def field_value(self, e, env):
        ref = self.resolve_field(e, env, e.line, temp_ok=True)
        v = self.read(ref)
        if v is None:
            raise LangError(f"line {e.line}: field '{ref[2]}' is used before it is assigned")
        return v

    # -- select case
    def case_value(self, e, env, line):
        """A case value is a literal, a signed number or a `const` name."""
        if isinstance(e, Var):
            slot = self.lookup(e.name, env, line)
            if slot[INTENT] != "const" or slot[IS_ARRAY]:
                raise LangError(f"line {line}: case value '{e.name}' is not a scalar const")
            return slot[VALUE]
        v = literal_value(e)
        if v is NOT_LITERAL:
            raise LangError(f"line {line}: a case value must be a literal or the name of a const")
        return v

    def select_index(self, subject, groups, env, line):
        """Which group of case values (a list, or None for default) matches the subject: its index,
        the default's index when nothing matches, or None. The values are evaluated now, checked
        against the subject's type, and must not overlap."""
        v = self.eval(subject, env)
        typ = type_of(v)
        if typ not in INT_RANGE and typ not in ("string", "logical"):
            raise LangError(f"line {line}: select case needs an integer, string or logical value, got {typ}")
        if typ in INT_RANGE:
            typ = "integer"                           # any integer width matches the integer case values
        entries, default = [], None                   # entries: (lo, hi, is_point, group)
        for n, values in enumerate(groups):
            if values is None:
                default = n
                continue
            for item in values:
                point = not isinstance(item, Slice)
                lo = self.case_value(item if point else item.lo, env, line) if point or item.lo is not None else None
                hi = lo if point else self.case_value(item.hi, env, line) if item.hi is not None else None
                for x in (lo, hi):
                    if x is not None and typ != ("integer" if type_of(x) in INT_RANGE else type_of(x)):
                        raise LangError(f"line {line}: case value {show_value(x)} is {type_of(x)}, "
                                        f"but the select value is {typ}")
                if not point and typ == "logical":
                    raise LangError(f"line {line}: a logical case value cannot be a range")
                if lo is not None and hi is not None and lo > hi:
                    raise LangError(f"line {line}: the range {show_range(lo, hi, False)} is empty")
                entries.append((lo, hi, point, n))
        check_overlap([e[:3] for e in entries], line)
        for lo, hi, _, n in entries:
            if (lo is None or lo <= v) and (hi is None or v <= hi):
                return n
        return default

    def select_expr(self, e, env):
        n = self.select_index(e.subject, [a.values for a in e.arms], env, e.line)
        v = self.eval(e.arms[n].value, env)
        t, kinds = type_of(v), arm_types(e.arms)      # kinds: arms whose type is known without evaluating
        if kinds - {t}:
            if kinds <= {"integer", "real"} and t in ("integer", "real"):
                return float(v)                       # integer arms widen to real next to a real arm
            raise LangError(f"line {e.line}: the arms of a select expression have different types "
                            f"({' and '.join(sorted(kinds | {t}))})")
        return v

    def common_shape(self, values):
        shapes = {v.shape for v in values if isinstance(v, Array)}
        if len(shapes) > 1:
            a, b = sorted(shapes)[:2]
            raise LangError(f"array shape mismatch: {fmt_shape(a)} vs {fmt_shape(b)}")
        return shapes.pop()

    def binary(self, op, a, b):
        ta = type(a)
        if ta is type(b):                              # the common case: two plain integers or two doubles
            if ta is int:
                f = FAST_INT.get(op)
                if f is not None:
                    r = f(a, b)
                    if type(r) is int and not LO32 <= r <= HI32:
                        raise LangError(f"integer overflow: {a} {op} {b} does not fit in 32 bits "
                                        f"(use int64, or {op}% to wrap)")
                    return r
            elif ta is float:
                f = FAST_FLOAT.get(op)
                if f is not None:
                    try:
                        return f(a, b)
                    except ZeroDivisionError:
                        raise LangError("division by zero")
            elif ta is Real32:                         # two single-precision values: round each result
                f = FAST_FLOAT.get(op)
                if f is not None:
                    try:
                        r = f(a, b)
                    except ZeroDivisionError:
                        raise LangError("division by zero")
                    if type(r) is not float:
                        return r                       # a comparison
                    try:
                        return Real32(_F32.unpack(_F32.pack(r))[0])
                    except OverflowError:
                        pass                           # too large: the general path reports it
        if a is NULL or b is NULL:
            if op in ("==", "/="):
                return (a is b) == (op == "==")
            raise LangError(f"operator {op!r} cannot be used with null; check with present() or use ??")
        # whole-array operations: scalars broadcast, arrays must have the same shape
        if isinstance(a, Array) or isinstance(b, Array):
            shape = self.common_shape([a, b])
            n = math.prod(shape)
            ad = a.data if isinstance(a, Array) else [a] * n
            bd = b.data if isinstance(b, Array) else [b] * n
            return Array(shape, [self.binary(op, x, y) for x, y in zip(ad, bd)])
        if isinstance(a, Record) or isinstance(b, Record):
            raise LangError(f"records cannot be used with '{op}'; use their fields")
        if op in ("and", "or"):
            self.truth(a); self.truth(b)
            return (a and b) if op == "and" else (a or b)
        if op in ("<", "<=", ">", ">=") and (isinstance(a, complex) or isinstance(b, complex)):
            raise LangError(f"complex numbers cannot be ordered with '{op}'; compare abs() or the parts")
        if op in ("==", "/=", "<", "<=", ">", ">="):
            try:
                return COMPARE[op](a, b)
            except TypeError:
                raise LangError(f"cannot compare {type_of(a)} with {type_of(b)} using '{op}'")
        if isinstance(a, str) and op == "+" and isinstance(b, str):
            return a + b
        ka, kb = type_of(a), type_of(b)
        for k in (ka, kb):
            if k not in NUMERIC:
                raise LangError(f"operator {op!r} needs numbers, got {k}")
        kind = result_kind(ka, kb)
        if op[-1] == "%" and len(op) == 2:                 # +% -% *% wrap around instead of failing
            if kind not in INT_RANGE:
                raise LangError(f"operator {op!r} works on integers, got {ka} and {kb}")
            return wrap(kind, raw_arith(op[0], a, b))
        r = raw_arith(op, a, b)
        if kind in INT_RANGE and not isinstance(r, int):    # an integer power of an integer stays an integer
            raise LangError(f"operator {op!r} on {ka} and {kb} did not give an integer")
        return tag(kind, r, f"{a} {op} {b}")

    def apply(self, e, env):
        if e.name == "present" and e.name not in env and "present" not in self.scope.funcs:
            if len(e.args) != 1 or isinstance(e.args[0], (NamedArg, Slice)):
                raise LangError(f"line {e.line}: present() takes one value")
            return self.eval(e.args[0], env) is not NULL
        if e.name == "allocated" and e.name not in env and "allocated" not in self.scope.funcs:
            if len(e.args) != 1 or not isinstance(e.args[0], Var):
                raise LangError(f"line {e.line}: allocated() needs the name of an allocatable array")
            return self.allocatable(e.args[0].name, env, e.line)[VALUE] is not None
        if e.name in env:  # indexing or slicing
            if not env[e.name][IS_ARRAY] and env[e.name][TYPE] == "string":
                return self.read(self.resolve(e, env, e.line))
            if not env[e.name][IS_ARRAY]:
                raise LangError(f"line {e.line}: '{e.name}' is not an array")
            return self.read(self.resolve(e, env, e.line))
        rdef = self.scope.records.get(e.name)
        if rdef is not None:
            return self.construct(rdef, e, env)
        if any(isinstance(a, NamedArg) for a in e.args):
            raise LangError(f"line {e.line}: named arguments are only for building records")
        args = [self.eval(a, env) for a in e.args]
        f = self.scope.funcs.get(e.name)
        if f is not None:
            if f.is_sub:
                raise LangError(f"line {e.line}: '{e.name}' is a subroutine; use 'call {e.name}(...)'")
            return self.call_function(f, args, e.line)
        if e.name in BUILTINS or e.name in ELEMENTAL:
            return self.call_builtin(e.name, args, e.line)
        raise LangError(f"line {e.line}: unknown function or array '{e.name}'")

    def read_stmt(self, s, env):
        """`read a, b [status ios]`: list-directed input. Items are separated by blanks or commas and may
        continue on following lines; the rest of the last line is discarded."""
        refs = [self.resolve(t, env, s.line) for t in s.targets]
        status = self.resolve(s.status, env, s.line) if s.status is not None else None
        if status is not None and status[0][TYPE] not in ("integer", "integer?"):
            raise LangError(f"line {s.line}: the status of a read must be an integer variable")
        escaping = [r for r in refs + ([status] if status is not None else []) if r.root[INTENT] in ("out", "inout")]
        if bool(escaping) != s.update:
            raise LangError(f"line {s.line}: 'update' must be used exactly when reading into an "
                            f"out or inout parameter")
        items = []                                    # (ref, element position or None, element type)
        for ref in refs:
            typ = ref[0][TYPE].rstrip("?")
            if typ not in READ_NAMES:
                raise LangError(f"line {s.line}: 'read' cannot read a {typ} ('{ref[2]}')")
            current = self.read(ref)
            if isinstance(current, Array):
                items.extend((ref, n, typ) for n in range(len(current.data)))
            else:
                items.append((ref, None, typ))
        tokens, code, done = [], 0, 0
        arrays = {}
        for ref, pos, typ in items:
            while not tokens:
                line = self.input.next_line()
                if line is None:
                    code = -1
                    break
                tokens = ITEM_RE.findall(line)
            if code:
                break
            token = tokens.pop(0)
            value = read_value(typ, token)
            if value is None:
                if status is None:
                    raise LangError(f"line {s.line}: expected {READ_NAMES[typ]} for '{ref[2]}', "
                                    f"got {token if token[0] in chr(34) + chr(39) else repr(token)}")
                code = 1
                break
            if pos is None:
                self.write(ref, value, s.line)
            else:
                arrays.setdefault(id(ref), (ref, self.read(ref).copy()))[1].data[pos] = value
            done += 1
        for ref, arr in arrays.values():
            self.write(ref, arr, s.line)
        if code == -1 and status is None:
            raise LangError(f"line {s.line}: no more input")
        if status is not None:
            self.write(status, code, s.line)

    def call_builtin(self, name, args, line):
        try:
            if name == "argument":              # argument(0) is the program name, "" past the last one
                if len(args) != 1 or type_of(args[0]) not in INT_RANGE:
                    raise TypeError("needs one integer")
                return self.args[args[0]] if 0 <= args[0] < len(self.args) else ""
            if name == "argument_count":
                return len(self.args) - 1
            if name == "read_line":              # the next whole line of input, or null at the end
                line = self.input.next_line()
                return NULL if line is None else line
            if name in ELEMENTAL:
                f = ELEMENTAL[name]
                if not any(isinstance(a, Array) for a in args):
                    result = f(*args)
                else:
                    shape = self.common_shape(args)
                    n = math.prod(shape)
                    cols = [a.data if isinstance(a, Array) else [a] * n for a in args]
                    result = Array(shape, [f(*xs) for xs in zip(*cols)])
            else:
                result = BUILTINS[name](*args)
            return self.keep_kind(name, result, args) if name in KEEPS_KIND else result
        except LangError:
            raise
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as err:
            raise LangError(f"line {line}: {name}(): {err}")

    def keep_kind(self, name, result, args):
        """A function of fixed-width numbers gives a result of the same width (`sqrt` of a real32 is a
        real32), and a plain integer result must still fit in 32 bits."""
        ints, single = widest_tag(args)

        def fix(x):
            t = type(x)
            if t is int:
                if ints is not None:
                    return make_int(ints, x, f"{name}()")
                if not LO32 <= x <= HI32:
                    raise LangError(f"{name}(): integer overflow ({x} does not fit in 32 bits)")
            elif t is float and single:
                return Real32(round32(x))
            return x

        if isinstance(result, Array):
            return Array(result.shape, [fix(x) for x in result.data])
        return fix(result)

    # -- procedures
    def bind(self, f, values, line):
        """Build the callee's environment. `None` marks an unassigned out scalar."""
        env = dict(self.scopes[id(f)].consts)
        for (typ, name, intent), v in zip(f.params, values):
            is_arr = isinstance(v, Array)
            if isinstance(v, Record) and v.rdef.name == typ and v.rdef is not self.scopes[id(f)].records.get(typ):
                raise LangError(f"line {line}: argument '{name}' is a {typ}, but not the {typ} that "
                                f"{f.name} was written for (two records share that name)")
            if v is not None and not is_arr:
                v = coerce(typ, v, f"line {line}: argument '{name}'")
            env[name] = [typ, v, is_arr, intent]
        return env

    def run_body(self, f, env):
        saved, self.scope = self.scope, self.scopes[id(f)]
        try:
            self.exec_block(f.body, env)
        except _Return as r:
            return r.value
        finally:
            self.scope = saved
        return None

    def pad_optional(self, f, args, line):
        """Check the argument count and fill in null for optional parameters left off the end."""
        most = len(f.params)
        least = most
        while least and f.params[least - 1][0].endswith("?"):
            least -= 1
        if not least <= len(args) <= most:
            count = str(most) if least == most else f"{least} to {most}"
            raise LangError(f"line {line}: {f.name} expects {count} arguments, got {len(args)}")
        return list(args) + [NULL] * (most - len(args))

    def call_function(self, f, args, line):
        if len(args) != len(f.params):
            args = self.pad_optional(f, args, line)
        value = self.run_body(f, self.bind(f, args, line))
        if f.ret is None:
            return None
        if value is None:
            raise LangError(f"function {f.name} must return a {f.ret}")
        return coerce(f.ret, value, f"function {f.name}")

    def reference_of(self, a, env, line):
        """Best-effort (slot, subs) for an argument that names a variable."""
        if isinstance(a, FieldAccess) and self.is_reference(a, env):
            return self.resolve(a, env, line)
        if isinstance(a, Var) and a.name in env:
            return Ref(env[a.name], None, a.name)
        if isinstance(a, Apply) and a.name in env and env[a.name][IS_ARRAY]:
            slot = env[a.name]
            return Ref(slot, self.subscripts(slot[VALUE], a.args, env, line, a.name), a.name)
        return None

    def call_subroutine(self, s, env):
        f = self.scope.funcs.get(s.name)
        if f is None:
            raise LangError(f"line {s.line}: unknown subroutine '{s.name}'")
        if not f.is_sub:
            raise LangError(f"line {s.line}: '{s.name}' is a function; use it in an expression")
        self.pad_optional(f, s.args, s.line)

        outs, values = [], []   # outs: (param index, reference)
        for n, ((typ, name, intent), a) in enumerate(zip(f.params, s.args)):
            if intent == "in":
                values.append(self.eval(a, env))
                continue
            ref = self.resolve(a, env, s.line)
            slot = ref[0]
            if ref.root[INTENT] in ("in", "const"):
                what = "an 'in' parameter" if ref.root[INTENT] == "in" else "a constant"
                part = "part of " if ref.fpath else ""
                raise LangError(f"line {s.line}: '{ref[2]}' is {part}{what} and cannot be passed as {intent}")
            if slot[TYPE] != typ:
                raise LangError(f"line {s.line}: argument '{name}' is {typ} but '{ref[2]}' is {slot[TYPE]}")
            outs.append((n, ref))
            current = self.read(ref)
            if intent == "out" and typ not in DEFAULTS:           # an out record starts with no values
                sub = self.scopes[id(f)].records.get(typ)
                values.append(self.new_record(sub, blank=True) if sub else current.copy())
            elif isinstance(current, (Array, Record)):
                values.append(current.copy())                     # copy in
            elif intent == "out":
                values.append(None)                               # out: starts unassigned
            elif current is None:
                raise LangError(f"'{ref[2]}' is used before it is assigned")
            else:
                values.append(current)

        # Aliasing: an out/inout variable may not overlap any other argument.
        for n, ref in outs:
            name = ref[2]
            for m, a in enumerate(s.args):
                other = self.reference_of(a, env, s.line)
                if m != n and other is not None and aliased(ref, other):
                    raise LangError(f"line {s.line}: '{name}' is passed as {f.params[n][2]} and also "
                                    f"as another argument to {s.name}; arguments must not alias")

        values += [NULL] * (len(f.params) - len(values))          # omitted optional arguments are null
        callee = self.bind(f, values, s.line)
        value = self.run_body(f, callee)
        if value is not None:
            raise LangError(f"line {s.line}: subroutine {s.name} cannot return a value")

        for n, ref in outs:                                       # copy out
            name = f.params[n][1]
            result = callee[name][VALUE]
            if result is None:
                raise LangError(f"line {s.line}: out parameter '{name}' was never assigned in {s.name}")
            if f.params[n][2] == "out" and isinstance(result, Record):
                missing = result.first_unassigned()
                if missing:
                    raise LangError(f"line {s.line}: out parameter '{name}' was not fully assigned in "
                                    f"{s.name}: '{name}.{missing}' has no value")
            self.write(ref, result, s.line)
