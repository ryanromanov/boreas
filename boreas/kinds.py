"""Fixed-width numbers.

Plain Python `int` is Boreas's `integer` (32 bits, checked) and plain `float` is `real` (a double). The
other widths are subclasses, so a value carries its kind and arithmetic can pick the right one:
int8, int16, int64, real32."""
import struct
from .lexer import LangError


class Int8(int): __slots__ = ()
class Int16(int): __slots__ = ()
class Int64(int): __slots__ = ()
class Real32(float): __slots__ = ()


INT_RANGE = {"int8": (-2 ** 7, 2 ** 7 - 1), "int16": (-2 ** 15, 2 ** 15 - 1),
             "integer": (-2 ** 31, 2 ** 31 - 1), "int64": (-2 ** 63, 2 ** 63 - 1)}
INT_CLASS = {"int8": Int8, "int16": Int16, "integer": int, "int64": Int64}
INT_ORDER = ["int8", "int16", "integer", "int64"]          # narrowest first
INT_BITS = {"int8": 8, "int16": 16, "integer": 32, "int64": 64}
LO32, HI32 = INT_RANGE["integer"]

TYPE_NAMES = {bool: "logical", int: "integer", Int8: "int8", Int16: "int16", Int64: "int64",
              float: "real", Real32: "real32", str: "string", complex: "complex"}
NUMERIC = {"integer", "int8", "int16", "int64", "real", "real32", "complex"}
TAGGED = (Int8, Int16, Int64, Real32)

# `int32` and `real64` are other spellings of the plain types.
SPELLINGS = {"int32": "integer", "real64": "real"}
KIND_TYPES = ["int8", "int16", "int32", "int64", "real32", "real64"]

REAL32_MAX = 3.4028234663852886e38
REAL32_TINY = 1.1754943508222875e-38
REAL32_EPSILON = 2.0 ** -23

_F32 = struct.Struct("f")


def _round32(x):
    try:
        return _F32.unpack(_F32.pack(x))[0]
    except OverflowError:
        return float("inf") if x > 0 else float("-inf")


def round32(x):
    """The nearest single-precision value to x."""
    r = _round32(x)
    if r in (float("inf"), float("-inf")) and x - x == 0:      # x was finite
        raise LangError(f"{x!r} is too large for real32")
    return r


def shortest32(x):
    """The shortest decimal text that reads back as the same single-precision value."""
    if x != x or x in (float("inf"), float("-inf")):
        return str(float(x))
    for digits in range(1, 10):
        text = f"{x:.{digits}g}"
        if _round32(float(text)) == x:
            break
    if "e" not in text and "." not in text:
        text += ".0"
    return text


def make_int(kind, value, what="value"):
    lo, hi = INT_RANGE[kind]
    if not lo <= value <= hi:
        raise LangError(f"{what}: {value} does not fit in {'integer (32 bits)' if kind == 'integer' else kind}")
    return INT_CLASS[kind](value)


def lit_int(v):
    """An integer literal too big for 32 bits is an int64."""
    return v if LO32 <= v <= HI32 else Int64(v)


def int_div(a, b):
    """Integer division truncating toward zero, exact for any size."""
    if b == 0:
        raise LangError("integer division by zero")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def int_pow(a, b):
    if b >= 0:
        return a ** b
    if a == 0:
        raise LangError("zero cannot be raised to a negative integer power")
    if a == 1:
        return 1
    if a == -1:
        return 1 if b % 2 == 0 else -1
    return 0                                    # 1 / a**|b| truncates to zero


def raw_arith(op, a, b):
    """The arithmetic result as an untagged Python number."""
    try:
        if op == "+": return a + b
        if op == "-": return a - b
        if op == "*": return a * b
        if op == "/":
            if isinstance(a, int) and isinstance(b, int):
                return int_div(a, b)
            return a / b
        if op == "**":
            if isinstance(a, int) and isinstance(b, int):
                return int_pow(a, b)
            return a ** b
    except ZeroDivisionError:
        raise LangError("division by zero")
    except OverflowError:
        raise LangError(f"{a} {op} {b} is too large")
    raise LangError(f"unknown operator {op}")


def result_kind(ka, kb):
    """The kind of `a op b`: the wider of the two, with a real beating an integer."""
    if ka == kb:
        return ka
    if "complex" in (ka, kb):
        return "complex"
    if ka in ("real", "real32") or kb in ("real", "real32"):
        return "real" if "real" in (ka, kb) else "real32"
    return ka if INT_ORDER.index(ka) > INT_ORDER.index(kb) else kb


def tag(kind, r, what="result"):
    """Give an untagged Python number `r` the kind `kind`, checking it fits."""
    if kind == "real":
        return float(r)
    if kind == "real32":
        return Real32(round32(float(r)))
    if kind == "complex":
        return complex(r)
    return make_int(kind, r, what)


def wrap(kind, r):
    """Two's-complement wrap-around of an integer result to `kind`."""
    bits = INT_BITS[kind]
    half = 1 << (bits - 1)
    return INT_CLASS[kind](((r + half) % (1 << bits)) - half)


def widest_tag(args):
    """For a function applied to `args`: the kind its result should keep, or None. Tagged integer
    arguments give the widest integer kind; real32 arguments give real32 unless a real64 is mixed in."""
    ints, real32, real64 = None, False, False
    for a in args:
        v = a
        if hasattr(a, "data"):
            if not a.data:
                continue
            v = a.data[0]
        t = type(v)
        if t is Real32:
            real32 = True
        elif t is float:
            real64 = True
        elif t in (Int8, Int16, Int64):
            k = TYPE_NAMES[t]
            if ints is None or INT_ORDER.index(k) > INT_ORDER.index(ints):
                ints = k
    return ints, (real32 and not real64)
