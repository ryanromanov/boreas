"""The static types of Boreas, as the type checker (typecheck.py) and, later, the C emitter use them.

A `Type` is a kind, a rank (0 for a single value, 1 or more for an array, -1 for an array whose rank is not known
yet), whether it is optional (`T?`), and, for records and interfaces, a name. UNKNOWN means "not worked out": it
never makes an error, it only means the checker has no answer."""
import re
from dataclasses import dataclass
from .parser import ArrayOf, MapType, ProcType, ResultList, SetType

NUMBERS = ("int8", "int16", "integer", "int64", "real32", "real", "complex")
INTEGERS = ("int8", "int16", "integer", "int64")
REALS = ("real32", "real")
ALIASES = {"int32": "integer", "real64": "real"}
SCALAR_KINDS = set(NUMBERS) | {"logical", "string", "file"}


@dataclass(frozen=True)
class Type:
    kind: str
    rank: int = 0
    optional: bool = False
    name: str = ""

    def __str__(self):
        base = self.name or self.kind
        if self.rank > 0:
            base += "[" + "," * (self.rank - 1) + "]"
        elif self.rank < 0:
            base += "[...]"
        return base + ("?" if self.optional else "")

    @property
    def known(self):
        return self.kind != "unknown"

    @property
    def is_number(self):
        return self.kind in NUMBERS

    @property
    def is_array(self):
        return self.rank != 0

    def element(self):
        """The type of one element (an array's own type with no rank)."""
        return Type(self.kind, 0, False, self.name)

    def with_rank(self, rank):
        return Type(self.kind, rank, self.optional, self.name)

    def unwrapped(self):
        return Type(self.kind, self.rank, False, self.name)


UNKNOWN = Type("unknown")
INTEGER, INT64, REAL, REAL32 = Type("integer"), Type("int64"), Type("real"), Type("real32")
LOGICAL, STRING, COMPLEX, NULL = Type("logical"), Type("string"), Type("complex"), Type("null")


def kind_name(name):
    return ALIASES.get(name, name)


def promote(a, b):
    """The kind of `a op b` for two numeric kinds: the wider integer, a real beating an integer, a double beating a
    single, and a complex beating everything (as kinds.result_kind does at run time)."""
    if a == b:
        return a
    if "complex" in (a, b):
        return "complex"
    if a in REALS or b in REALS:
        return "real" if "real" in (a, b) else "real32"
    return a if INTEGERS.index(a) > INTEGERS.index(b) else b


def parse(typ, scope=None, rank=0):
    """The Type of a declared type: a name such as `real`, `real?` or `point`, an array type such as `real[,]`, or a
    MapType, SetType, ArrayOf or ProcType. `scope` (with `records` and `interfaces`) resolves record names; `rank` is
    the rank to give a plain name (a declaration's dimensions)."""
    if isinstance(typ, ProcType):
        return Type("proc")
    if isinstance(typ, MapType):
        return Type("map")
    if isinstance(typ, SetType):
        return Type("set")
    if isinstance(typ, ArrayOf):
        elem = parse(typ.elem, scope)
        return Type(elem.kind, typ.rank, False, elem.name) if elem.known else UNKNOWN
    if isinstance(typ, ResultList) or not isinstance(typ, str):
        return UNKNOWN
    text = str(typ).strip()
    optional = text.endswith("?")
    text = text.rstrip("?")
    m = re.fullmatch(r"(\w+)\s*(\[[,\s]*\])?", text)
    if not m:
        return UNKNOWN
    base = kind_name(m.group(1).lower())
    if m.group(2):
        rank = m.group(2).count(",") + 1
    if base in SCALAR_KINDS:
        return Type(base, rank, optional)
    if scope is not None and base in getattr(scope, "interfaces", {}):
        return Type("interface", rank, optional, base)
    if scope is not None and base in getattr(scope, "records", {}):
        return Type("record", rank, optional, base)
    return UNKNOWN
