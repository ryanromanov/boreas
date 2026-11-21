"""The static type checker, first part: it works out the type of every expression it can, without running the program.

`infer(unit, interp)` returns a Checker whose `types` maps `id(node)` to a `Type` (types.py). Anything it cannot work
out is UNKNOWN, and UNKNOWN never makes an error. A type that depends on a value (`floor(x)` is an `int32` or an
`int` depending on how big the answer is) is also UNKNOWN: those are recorded in `Checker.value_dependent`, because a
compiler needs them decided.

The checker is checked against the interpreter, the way the compiler will be: set BOREAS_TYPE_ORACLE to a file name and
every expression the interpreter evaluates is compared with its static type (see `install_oracle`)."""
import json
import os
from . import types as T
from .parser import (Apply, ArrayLit, Assign, Bool, Binary, Call, Case, Decl, Do, DoWhile, FieldAccess, Gen, If, IsType,
                     MatrixLit, NamedArg, Null, Num, Return, SelectExpr, SelectType, Slice, Str, Unary, Var, is_optional)
from .tree import STATEMENTS, children, statements_in, walk
from .types import (COMPLEX, INT, INT32, LOGICAL, NULL, REAL, REAL32, STRING, UNKNOWN, Type, parse, promote)
from .kinds import LO32, HI32

TAKES_SCALAR = T.SCALAR_KINDS - {"file"}


def rank_of(args):
    """The rank of an elementwise result: 0 for all single values, the arrays' rank otherwise, -1 if unknown."""
    ranks = [a.rank for a in args]
    if any(r < 0 for r in ranks):
        return -1
    return max(ranks, default=0)


def float_kind(args):
    """The kind a real-valued function gives: complex for a complex argument, a single for single arguments only."""
    kinds = {a.kind for a in args}
    if "complex" in kinds:
        return "complex"
    return "real32" if "real32" in kinds and "real" not in kinds else "real"


def elemental(kind_of):
    def rule(args):
        if not args or not all(a.known and a.is_number for a in args):
            return UNKNOWN
        return Type(kind_of(args), rank_of(args))
    return rule


def fixed(result):
    """A function that always gives `result` (a Type), whatever its arguments."""
    return lambda args: result


def of_first(args):
    return args[0].unwrapped() if args and args[0].known else UNKNOWN


def reduction(args):
    """sum, product, maxval, minval: the element kind; with `dim`, one rank less."""
    if not args or not args[0].known or not args[0].is_number:
        return UNKNOWN
    a = args[0]
    if len(args) == 1 or a.rank < 0:
        return a.element() if len(args) == 1 else UNKNOWN
    return a.with_rank(a.rank - 1) if a.rank > 1 else a.element()


def logical_reduction(args):
    if not args or not args[0].known:
        return UNKNOWN
    a = args[0]
    if len(args) == 1:
        return LOGICAL
    return Type("logical", a.rank - 1) if a.rank > 1 else LOGICAL


def counting(args):
    if not args or not args[0].known:
        return UNKNOWN
    a = args[0]
    if len(args) == 1:
        return INT
    return Type("int", a.rank - 1) if a.rank > 1 else INT


def locating(takes_value):
    def rule(args):
        if not args or not args[0].known:
            return UNKNOWN
        a = args[0]
        dim_given = len(args) > (2 if takes_value else 1) and not (isinstance(args[-1], Type) and args[-1].kind == "logical"
                                                                   and len(args) == (3 if takes_value else 2))
        if a.rank < 0:
            return UNKNOWN
        if a.rank == 1:
            return INT
        return Type("int", a.rank - 1) if dim_given and a.rank > 1 else Type("int", 1)
    return rule


def numeric_kind(args):
    kinds = [a.kind for a in args]
    kind = kinds[0]
    for k in kinds[1:]:
        kind = promote(kind, k)
    return kind


def abs_kind(args):
    return "real" if args[0].kind == "complex" else args[0].kind


def conversion(kind):
    return elemental(lambda args: kind)


def widest(args):
    """max and min: the widest kind of the arguments (mixed numbers widen, so `max(3, 2.5)` is 2.5 and `max(3, 2.0)` 3.0)."""
    if len(args) < 2 or not all(a.known for a in args):
        return UNKNOWN
    if all(a.kind == "string" for a in args):
        return Type("string", rank_of(args))
    if not all(a.is_number and a.kind != "complex" for a in args):
        return UNKNOWN
    return Type(numeric_kind(args), rank_of(args))


def whole_number(args):
    """floor, ceiling, nint: an int for a real, an int32 for a real32 (a whole number stays as it is)."""
    if len(args) != 1 or not args[0].known or not args[0].is_number or args[0].kind == "complex":
        return UNKNOWN
    a = args[0]
    kind = {"real": "int", "real32": "int32"}.get(a.kind, a.kind)
    return Type(kind, a.rank)


def matmul(args):
    if len(args) != 2 or not all(a.known and a.is_number for a in args):
        return UNKNOWN
    a, b = args
    if a.rank not in (1, 2) or b.rank not in (1, 2):
        return UNKNOWN
    kind = promote(a.kind, b.kind)
    if a.rank == 2 and b.rank == 2:
        return Type(kind, 2)
    return Type(kind, 1)


def dot(args):
    if len(args) != 2 or not all(a.known and a.is_number for a in args):
        return UNKNOWN
    return Type(promote(args[0].kind, args[1].kind))


def reshape(args):
    if not args or not args[0].known:
        return UNKNOWN
    return Type(args[0].kind, -1, False, args[0].name)


def keep(args):
    return args[0].unwrapped() if args and args[0].known and args[0].rank != 0 else UNKNOWN


def vector_of(kind):
    return fixed(Type(kind, 1))


def bits(args):
    """iand, ior, ieor: the wider integer of the two."""
    if not args or not all(a.known and a.kind in T.INTEGERS for a in args):
        return UNKNOWN
    return Type(numeric_kind(args[:2]), rank_of(args))


def first_integer(args):
    if not args or not args[0].known or args[0].kind not in T.INTEGERS:
        return UNKNOWN
    return Type(args[0].kind, rank_of(args))


def shape_rule(args):
    return Type("int", 1) if len(args) == 1 else UNKNOWN


def bounds(args):
    return Type("int", 1) if len(args) == 1 else INT


def epsilon_like(args):
    return args[0].element() if args and args[0].known and args[0].is_number else UNKNOWN


FLOATS = ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh", "exp",
          "log", "log10", "gamma", "log_gamma", "erf", "erfc", "hypot", "atan2", "bessel_j0", "bessel_j1", "bessel_y0",
          "bessel_y1", "bessel_jn", "bessel_yn")

BUILTIN_RULES = {name: elemental(float_kind) for name in FLOATS}
BUILTIN_RULES.update({
    "abs": elemental(abs_kind), "mod": elemental(numeric_kind), "modulo": elemental(numeric_kind),
    "sign": elemental(numeric_kind), "dim": elemental(numeric_kind),
    "aint": elemental(float_kind), "anint": elemental(float_kind),
    "real": conversion("real"), "real64": conversion("real"), "real32": conversion("real32"),
    "int": conversion("int"), "int32": conversion("int32"), "int8": conversion("int8"),
    "int16": conversion("int16"), "int64": conversion("int"),
    "max": widest, "min": widest, "floor": whole_number, "ceiling": whole_number, "nint": whole_number,
    "isnan": lambda args: Type("logical", rank_of(args)) if args and args[0].known else UNKNOWN,
    "isinf": lambda args: Type("logical", rank_of(args)) if args and args[0].known else UNKNOWN,
    "cmplx": elemental(lambda args: "complex"), "conjg": elemental(lambda args: "complex"),
    "aimag": elemental(lambda args: "real"),
    "iand": bits, "ior": bits, "ieor": bits, "bitnot": first_integer, "ishft": first_integer, "shiftl": first_integer,
    "shiftr": first_integer, "shifta": first_integer, "ibset": first_integer, "ibclr": first_integer,
    "ibits": first_integer, "btest": lambda args: Type("logical", rank_of(args)) if args and args[0].known else UNKNOWN,
    "popcnt": lambda args: Type("int", rank_of(args)) if args and args[0].known else UNKNOWN,
    "leadz": lambda args: Type("int", rank_of(args)) if args and args[0].known else UNKNOWN,
    "trailz": lambda args: Type("int", rank_of(args)) if args and args[0].known else UNKNOWN,
    "bit_size": fixed(INT),
    "size": fixed(INT), "len": fixed(INT), "len_trim": fixed(INT), "index": fixed(INT),
    "scan": fixed(INT), "verify": fixed(INT), "iachar": fixed(INT), "argument_count": fixed(INT),
    "count": counting, "shape": shape_rule, "lbound": bounds, "ubound": bounds,
    "sum": reduction, "product": reduction, "maxval": reduction, "minval": reduction,
    "any": logical_reduction, "all": logical_reduction,
    "maxloc": locating(False), "minloc": locating(False), "findloc": locating(True),
    "matmul": matmul, "dot_product": dot, "transpose": keep, "reshape": reshape,
    "norm2": lambda args: Type(float_kind(args)) if args and args[0].known and args[0].is_number else UNKNOWN,
    "pack": lambda args: Type(args[0].kind, 1) if args and args[0].known and args[0].is_array else UNKNOWN,
    "spread": lambda args: Type(args[0].kind, max(args[0].rank, 0) + 1) if args and args[0].known and args[0].rank >= 0 else UNKNOWN,
    "cshift": keep, "flatten": lambda args: Type(args[0].kind, 1) if args and args[0].known and args[0].is_array else UNKNOWN,
    "merge": lambda args: Type(args[0].kind, rank_of(args), False, args[0].name) if len(args) == 3
    and all(a.known for a in args) and args[0].kind == args[1].kind and args[0].rank >= 0 else UNKNOWN,
    "trim": fixed(STRING), "upper": fixed(STRING), "lower": fixed(STRING), "replace": fixed(STRING),
    "join": fixed(STRING), "adjustl": fixed(STRING), "adjustr": fixed(STRING), "repeat": fixed(STRING),
    "achar": fixed(STRING), "fixed": fixed(STRING), "right": fixed(STRING), "left": fixed(STRING),
    "text": fixed(STRING), "scientific": fixed(STRING), "general": fixed(STRING), "tf": fixed(STRING),
    "argument": fixed(STRING),
    "contains": fixed(LOGICAL), "starts_with": fixed(LOGICAL), "ends_with": fixed(LOGICAL),
    "allocated": fixed(LOGICAL), "present": fixed(LOGICAL), "has": fixed(LOGICAL), "exists": fixed(LOGICAL),
    "split": vector_of("string"), "fields": vector_of("string"),
    "field": fixed(Type("string", 0, True)), "read_line": fixed(Type("string", 0, True)),
    "to_integer": fixed(Type("int", 0, True)), "to_real": fixed(Type("real", 0, True)),
    "to_logical": fixed(Type("logical", 0, True)),
    "random": fixed(REAL), "nan": fixed(REAL), "infinity": fixed(REAL),
    "get": lambda args: Type(args[0].args[1], 0, True) if len(args) == 2 and args[0].kind == "map" and len(args[0].args) == 2
    and args[0].args[1] in TAKES_SCALAR else UNKNOWN,
    "keys": lambda args: Type(args[0].args[0], 1) if len(args) == 1 and args[0].kind in ("map", "set") and args[0].args
    and args[0].args[0] in TAKES_SCALAR else UNKNOWN,
    "values": lambda args: Type(args[0].args[1], 1) if len(args) == 1 and args[0].kind == "map" and len(args[0].args) == 2
    and args[0].args[1] in TAKES_SCALAR else UNKNOWN,
    "huge": epsilon_like, "epsilon": epsilon_like, "tiny": epsilon_like,
    "open": fixed(Type("file")), "try_open": fixed(Type("file", 0, True)),
})
VALUE_DEPENDENT = set()          # functions whose type depends on a value: there are none now (see D77)


class Checker:
    def __init__(self, unit, interp):
        self.unit, self.interp = unit, interp
        self.types = {}                 # id(node) -> Type
        self.value_dependent = []       # (line, what): expressions whose type depends on a value
        self.keep = []                  # the nodes, so that their ids stay valid
        self.errors = []                # (line, col, message, length): mistakes the checker is sure of
        self.conv = {}                  # id(node) -> the type its value is converted to where it is used (for the compiler)
        self.where = {}                 # error -> the statement it was found in
        self.line = 0                   # the line of the statement being looked at
        self.stmt = None                # ... and the statement itself
        self.func = None                # the function being looked at
        self.procs = {}                 # callback parameter name -> its ProcType (per procedure)

    # -- procedures ---------------------------------------------------------
    def run(self):
        u, interp = self.unit, self.interp
        for f in self.all_functions():
            scope = interp.scopes.get(id(f))
            if scope is not None:
                self.func = f
                self.procedure(f.params, f.body, scope)
        self.func = None
        if u.program is not None and interp.program_scope is not None:
            self.procedure([], u.program.body, interp.program_scope)
        return self

    def all_functions(self):
        u = self.unit
        out = list(u.functions.values())
        for mod in u.modules.values():
            out += mod.functions.values()
            for r in mod.records.values():
                out += r.functions.values()
        for r in u.records.values():
            out += r.functions.values()
        return out

    def slot_type(self, slot, scope):
        typ, value = slot[0], slot[1]
        rank = value.rank if hasattr(value, "rank") and hasattr(value, "shape") else 0
        if rank == 0 and slot[2]:
            rank = slot[4] if len(slot) > 4 else -1
        t = parse(typ, scope, max(rank, 0))
        return Type(t.kind, rank, t.optional, t.name) if t.known and rank != 0 and t.rank == 0 else t

    def decl_type(self, d, scope):
        rank = len(d.dims) if isinstance(d.dims, list) else 0
        t = parse(d.type, scope, rank)
        return t

    def procedure(self, params, body, scope):
        env = {}
        self.procs = {}
        for name, slot in scope.consts.items():
            env[name] = self.slot_type(slot, scope)
        for typ, name, intent in params:
            env[name] = parse(typ, scope)
            if not isinstance(typ, str) and hasattr(typ, "params"):
                self.procs[name] = typ
        for node in walk(body):
            if isinstance(node, Decl):
                env[node.name] = self.decl_type(node, scope)
            elif isinstance(node, IsType) and node.var:
                env[node.var] = Type("interface" if node.type in scope.interfaces else "record", 0, False, node.type)
            elif isinstance(node, SelectType):
                env[node.var] = UNKNOWN
        self.scope = scope
        over = {}                    # id(statement) -> the names a select type or `is` gives its copy, with their types there
        for s in statements_in(body):
            self.line = getattr(s, "line", 0) or self.line
            self.stmt = s
            here = {**env, **over[id(s)]} if id(s) in over else env
            for operand in self.operands(s):
                self.expr(operand, here)
            self.check_statement(s, here)
            for name, t, inner in self.copies(s, scope):
                for n in statements_in(inner):
                    over.setdefault(id(n), {})[name] = t

    def copies(self, s, scope):
        """(name, type, statements) for each copy `s` makes: a select type's case variable inside its case, `c` of
        `x is circle as c` inside the branch. (The type of the subject has been worked out by now.)"""
        if isinstance(s, SelectType) and s.var:
            subject = self.types.get(id(s.subject), UNKNOWN)
            for c in s.cases:
                if c.values is None:
                    t = subject if subject.known else UNKNOWN
                else:
                    t = Type("interface" if c.values in scope.interfaces else "record", 0, False, c.values)
                yield s.var, t, c.body
        elif isinstance(s, If):
            for cond, body in s.branches:
                if isinstance(cond, IsType) and cond.var:
                    yield cond.var, Type("interface" if cond.type in scope.interfaces else "record", 0, False, cond.type), body

    @staticmethod
    def operands(s):
        """The expressions that belong directly to the statement `s`."""
        for c in children(s):
            if isinstance(c, tuple(STATEMENTS)):
                continue
            if isinstance(c, Case):
                for x in children(c):
                    if not isinstance(x, tuple(STATEMENTS)):
                        yield x
            else:
                yield c

    # -- expressions --------------------------------------------------------
    def expr(self, e, env):
        if isinstance(e, list):
            return [self.expr(x, env) for x in e]
        if e is None or not hasattr(e, "__dataclass_fields__"):
            return UNKNOWN
        t = self.infer(e, env)
        self.types[id(e)] = t
        self.keep.append(e)
        return t

    def infer(self, e, env):
        k = type(e)
        if k is Num:
            if isinstance(e.value, bool):
                return LOGICAL
            if isinstance(e.value, int):
                return INT
            if isinstance(e.value, float):
                return REAL
            return UNKNOWN
        if k is Str:
            return STRING
        if k is Bool:
            return LOGICAL
        if k is Null:
            return NULL
        if k is Var:
            if e.name in env:
                return env[e.name]
            return REAL if e.name == "pi" else UNKNOWN
        if k is Unary:
            t = self.expr(e.operand, env)
            if not t.known:
                return UNKNOWN
            if e.op == "not":
                self.need_logical(t)
            return LOGICAL.with_rank(t.rank) if e.op == "not" else t.unwrapped()
        if k is Binary:
            return self.binary(e, env)
        if k is Apply:
            return self.apply(e, env)
        if k is FieldAccess:
            return self.field(e, env)
        if k is ArrayLit:
            return self.array_literal(e.items, 1, env)
        if k is MatrixLit:
            for row in e.rows:
                self.expr(row, env)
            return self.items_type([x for row in e.rows for x in row], 2, env, infer_items=False)
        if k is Gen:
            inner = dict(env)
            inner[e.var] = INT
            self.expr([e.lo, e.hi, e.step], env)
            ts = self.expr(e.body, inner)
            return ts[0] if len(ts) == 1 else UNKNOWN
        if k is SelectExpr:
            self.expr(e.subject, env)
            for arm in e.arms:
                self.expr(arm.values, env)
                self.expr(arm.value, env)
            ts = {self.types[id(arm.value)] for arm in e.arms if id(arm.value) in self.types}
            if len(ts) == 1 and next(iter(ts)).known:
                return next(iter(ts))
            if ts and all(t.known and t.kind in ("int", "real") and t.rank == 0 for t in ts):
                return REAL if any(t.kind == "real" for t in ts) else INT
            return UNKNOWN
        if k is IsType:
            self.expr(e.subject, env)
            return LOGICAL
        if k is Slice:
            self.expr([e.lo, e.hi, e.step], env)
            return UNKNOWN
        if k is NamedArg:
            return self.expr(e.value, env)
        for c in children(e):                      # anything else: type what is inside, and give no type
            self.expr(c, env)
        return UNKNOWN

    def broadcast(self, a, b):
        if a.rank < 0 or b.rank < 0:
            return -1
        return max(a.rank, b.rank)

    def binary(self, e, env):
        lt, rt = self.expr(e.left, env), self.expr(e.right, env)
        op = e.op
        if op == "??":
            if lt.known and rt.known and lt.kind == rt.kind and lt.rank == rt.rank:
                return Type(lt.kind, lt.rank, rt.optional, lt.name)
            if lt.known and rt.known and lt.is_number and rt.is_number and lt.rank == rt.rank == 0 \
                    and "complex" not in (lt.kind, rt.kind):
                return Type(lt.kind, 0, rt.optional)     # the fallback takes the kind of x: `real32? ?? 0.5` is a real32
            return UNKNOWN
        if op in ("==", "/=") and ("map" in (lt.kind, rt.kind) or "set" in (lt.kind, rt.kind)):     # two collections (or a literal) compared
            return LOGICAL
        if not (lt.known and rt.known):
            return UNKNOWN
        # a real literal next to a single is a single (as in Fortran)
        if rt.kind == "real" and self.is_literal(e.right) and lt.kind == "real32":
            rt = Type("real32", rt.rank)
        elif lt.kind == "real" and self.is_literal(e.left) and rt.kind == "real32":
            lt = Type("real32", lt.rank)
        elif rt.kind == "int" and self.int32_literal(e.right) and lt.kind == "int32":
            rt = Type("int32", rt.rank)                      # a whole-number literal next to an int32 is an int32
        elif lt.kind == "int" and self.int32_literal(e.left) and rt.kind == "int32":
            lt = Type("int32", lt.rank)
        if lt.kind == "null" or rt.kind == "null":
            return LOGICAL if op in ("==", "/=") else UNKNOWN
        self.check_operands(e, lt, rt)
        if lt.is_number and rt.is_number and e.op not in ("and", "or"):    # both sides are converted to the wider kind
            kind = promote(lt.kind, rt.kind)
            for node in (e.left, e.right):
                self.convert(node, Type(kind), literal=self.is_literal(node))
        rank = self.broadcast(lt, rt)
        if op in ("==", "/=", "<", "<=", ">", ">=", "and", "or"):
            return Type("logical", rank)
        if op == "+" and lt.kind == "string" and rt.kind == "string":
            return Type("string", rank)
        if lt.is_number and rt.is_number:
            return Type(promote(lt.kind, rt.kind), rank)
        return UNKNOWN

    @staticmethod
    def int32_literal(e):
        """Is `e` a whole-number literal (or its negative) that fits in 32 bits?"""
        n = e.value if type(e) is Num else (-e.operand.value if type(e) is Unary and e.op == "-" and type(e.operand) is Num
                                            else None)
        return isinstance(n, int) and not isinstance(n, bool) and LO32 <= n <= HI32

    @staticmethod
    def is_literal(e):
        return type(e) is Num or (type(e) is Unary and type(e.operand) is Num)

    def items_type(self, items, rank, env, infer_items=True):
        ts = [self.types[id(x)] if id(x) in self.types else (self.expr(x, env) if infer_items else UNKNOWN) for x in items]
        if not ts or not all(t.known for t in ts):
            return UNKNOWN
        ts = self.adapt_literals(items, ts)
        kinds = {(t.kind, t.name) for t in ts}
        if len(kinds) == 1 and all(t.rank in (0, 1) for t in ts):
            kind, name = next(iter(kinds))
            return Type(kind, rank, False, name)
        if all(t.is_number and t.kind != "complex" and t.rank in (0, 1) for t in ts):
            kind = ts[0].kind                # numbers of different kinds are all the widest of them
            for t in ts[1:]:
                kind = promote(kind, t.kind)
            return Type(kind, rank)
        return UNKNOWN

    def adapt_literals(self, items, ts):
        """In an array literal, a whole-number literal next to an int32 is an int32 and a real literal next to a real32
        is a real32 (as in a binary operation)."""
        if len(items) != len(ts):
            return ts
        if any(t.kind == "int32" for t in ts):
            ts = [Type("int32", t.rank) if t.kind == "int" and self.int32_literal(x) else t for x, t in zip(items, ts)]
        if any(t.kind == "real32" for t in ts):
            ts = [Type("real32", t.rank) if t.kind == "real" and self.is_literal(x) else t for x, t in zip(items, ts)]
        return ts

    def array_literal(self, items, rank, env):
        for x in items:
            self.expr(x, env)
        return self.items_type(items, rank, env, infer_items=False)

    def index_type(self, t, args):
        """What `a[...]` is for an array (or string) type `t` and subscripts `args` (nodes)."""
        if not t.known:
            return UNKNOWN
        if t.kind == "string" and t.rank == 0:
            return STRING
        if t.rank == 0:
            return UNKNOWN
        slices = sum(1 for a in args if isinstance(a, Slice))
        vector = any(isinstance(a, Apply) or (id(a) in self.types and self.types[id(a)].rank != 0) for a in args
                     if not isinstance(a, Slice) and id(a) in self.types and self.types[id(a)].rank != 0)
        if vector:
            return UNKNOWN
        return t.element() if slices == 0 else t.with_rank(slices)

    def apply(self, e, env):
        arg_nodes = [a.value if isinstance(a, NamedArg) else a for a in e.args]
        args = [self.expr(a, env) for a in arg_nodes]
        if e.chain:
            self.expr(e.chain, env)
            mt = env.get(e.name)
            if mt is not None and mt.kind == "map" and len(mt.args) == 2 and mt.args[1] in ("int[]", "real[]") and e.bracket \
                    and len(args) == 1 and len(e.chain) == 1:                  # m["a"][i]: one element of an array in a map
                return Type(mt.args[1][:-2])
            return UNKNOWN
        name = e.name
        scope = self.scope
        if name not in env and any(a.kind == "int32" for a in args):     # as in a binary operation
            args = [Type("int32", a.rank) if a.kind == "int" and self.int32_literal(n) else a
                    for a, n in zip(args, arg_nodes)]
        if name not in env and any(a.kind == "real32" for a in args):
            args = [Type("real32", a.rank) if a.kind == "real" and self.is_literal(n) else a
                    for a, n in zip(args, arg_nodes)]
        if name in env:
            t = env[name]
            if t.kind == "proc":
                return self.proc_result(name, args)
            if t.kind in ("map", "set"):                      # age["ann"]: the value type, when it is a single value
                if t.kind == "map" and e.bracket and len(args) == 1 and len(t.args) == 2 and t.args[1] in TAKES_SCALAR:
                    return Type(t.args[1])
                if t.kind == "map" and e.bracket and len(args) == 1 and len(t.args) == 2:     # a set or array that lives in the map
                    v = t.args[1]
                    if v in ("set[string]", "set[int]"):
                        return Type("set", 0, False, "", (v[4:-1],))
                    if v in ("int[]", "real[]"):
                        return Type(v[:-2], 0 if getattr(e, "chain", None) else 1)
                return UNKNOWN
            if t.rank > 0 and e.bracket:
                for a in arg_nodes:
                    at = self.type_of_node(a)
                    if not isinstance(a, Slice) and at.known and at.rank == 0 and at.kind in ("real", "real32", "complex",
                                                                                              "string", "logical"):
                        self.error("array index must be integer", e, len(name))
            return self.index_type(t, arg_nodes)
        if name in scope.records:
            return Type("record", 0, False, name)
        if name in scope.funcs:
            if not scope.funcs[name].is_sub:
                self.check_call(scope.funcs[name], arg_nodes, e)
            return self.call_result(scope.funcs[name], args)
        from .interp import ELEMENTAL
        if name in ELEMENTAL and name not in ("merge", "max", "min"):
            for a in args:
                if a.known and a.kind in ("string", "logical"):
                    self.error(f"{name}(): needs numbers, got {a.kind}", e, len(name))
                    break
        if name in VALUE_DEPENDENT:
            self.value_dependent.append((e.line, f"{name}() gives an int or an int32 depending on its value"))
            return UNKNOWN
        if name == "reshape" and len(arg_nodes) == 2 and isinstance(arg_nodes[1], ArrayLit) and arg_nodes[1].items \
                and args[0].known and args[0].is_array:
            return Type(args[0].kind, len(arg_nodes[1].items), False, args[0].name)     # the shape is written out: its length is the rank
        rule = BUILTIN_RULES.get(name)
        if rule is not None and all(a.known for a in args):
            try:
                return rule(args)
            except (IndexError, AttributeError):
                return UNKNOWN
        return UNKNOWN

    def proc_result(self, name, args):
        proc = self.procs.get(name)
        if proc is None or proc.ret is None:
            return UNKNOWN
        return parse(proc.ret, self.scope)

    def call_result(self, f, args):
        scope = self.interp.scopes.get(id(f), self.scope)
        if f.ret is None or hasattr(f.ret, "types"):
            return UNKNOWN
        ret = parse(f.ret, scope)
        if not ret.known:
            return UNKNOWN
        scalar_params = [parse(typ, scope).rank == 0 and parse(typ, scope).kind in TAKES_SCALAR
                         for typ, _, _ in f.params if isinstance(typ, str)]
        if ret.rank == 0 and any(a.known and a.rank != 0 and p for a, p in zip(args, scalar_params)):
            ranks = [a.rank for a, p in zip(args, scalar_params) if p]
            return Type(ret.kind, -1 if any(r < 0 for r in ranks) else max(ranks), ret.optional, ret.name)
        return ret

    def field(self, e, env):
        bt = self.expr(e.base, env)
        arg_nodes = []
        if e.args is not None:
            arg_nodes = [a.value if isinstance(a, NamedArg) else a for a in e.args]
            self.expr(arg_nodes, env)
        if e.chain:
            self.expr(e.chain, env)
            return UNKNOWN
        if not bt.known or bt.rank != 0 or bt.kind not in ("record", "interface"):
            return UNKNOWN
        scope = self.scope
        if e.args is not None and not e.bracket:                       # a record's function: x.area(...)
            owner = (scope.records.get(bt.name) if bt.kind == "record" else scope.interfaces.get(bt.name))
            f = owner.functions.get(e.field) if owner is not None else None
            if f is None or f.ret is None or hasattr(f.ret, "types"):
                return UNKNOWN
            ret = parse(f.ret, self.interp.scopes.get(id(f), scope))
            return ret
        if bt.kind != "record":
            return UNKNOWN
        rdef = scope.records.get(bt.name)
        if rdef is None:
            return UNKNOWN
        field = next((d for d in rdef.fields if d.name == e.field), None)
        if field is None:
            if isinstance(e.base, Var) and e.field not in rdef.functions:
                self.error(f"{bt.name} '{e.base.name}' has no field '{e.field}'", e)
            return UNKNOWN
        ft = self.decl_type(field, self.interp.owner.get(id(rdef), scope) if hasattr(self.interp, "owner") else scope)
        if e.args is not None and e.bracket:
            if ft.kind == "map" and len(ft.args) == 2 and len(arg_nodes) == 1 and ft.args[1] in ("set[string]", "set[int]", "int[]", "real[]"):
                v = ft.args[1]                                  # a set or array that lives in the map
                return Type("set", 0, False, "", (v[4:-1],)) if v.startswith("set[") else Type(v[:-2], 1)
            return self.index_type(ft, arg_nodes)
        return ft

    # -- mistakes the checker is sure of -------------------------------------
    def error(self, message, node=None, length=None):
        line = getattr(node, "line", 0) or self.line
        col = getattr(node, "col", 0) or None
        item = (line, col, message, length)
        if item not in self.errors:
            self.errors.append(item)
            self.where[item] = self.stmt

    def need_logical(self, t, node=None):
        if t.known and t.kind in T.NUMBERS + ("string", "file") and t.kind != "logical":
            self.error(f"condition must be logical, got {t.kind}", node)

    def check_operands(self, e, lt, rt):
        op = e.op
        if op in ("and", "or"):
            self.need_logical(lt)
            self.need_logical(rt)
            return
        if op in ("==", "/=", "<", "<=", ">", ">="):
            def category(t):
                return "string" if t.kind == "string" else "logical" if t.kind == "logical" else \
                    "number" if t.kind in T.NUMBERS else None
            a, b = category(lt), category(rt)
            if a is None or b is None:
                return
            if a != b:
                self.error(f"cannot compare {lt.kind} with {rt.kind} using '{op}'")
            elif a == "logical" and op not in ("==", "/="):
                self.error(f"logicals can be compared with == and /=, not '{op}'")
            elif "complex" in (lt.kind, rt.kind) and op not in ("==", "/="):
                self.error(f"complex numbers cannot be ordered with '{op}'; compare abs() or the parts")
            return
        if op in ("+", "-", "*", "/", "**", "+%", "-%", "*%"):
            if op == "+" and lt.kind == "string" and rt.kind == "string":
                return
            for t in (lt, rt):
                if t.kind in ("record", "interface"):
                    self.error(f"records cannot be used with '{op}'; use their fields")
                    return
                if t.kind in ("string", "logical", "file"):
                    self.error(f"operator '{op}' needs numbers, got {t.kind}")
                    return

    def assign_problem(self, target, value):
        """Why `value` cannot be stored in `target` (a message like the interpreter's), or None when it can, or when the
        checker is not sure (anything but numbers, text and logicals)."""
        simple = T.NUMBERS + ("string", "logical")
        if not (target.known and value.known) or target.kind not in simple or value.kind not in simple:
            return None
        if target.rank == 0 and value.rank > 0:
            return f"cannot assign array to {target.kind}"
        if target.rank < 0 or value.rank < 0:
            return None
        def category(t):
            return "number" if t.kind in T.NUMBERS else t.kind
        if category(target) != category(value):
            return f"cannot assign {value.kind} to {target.kind}"
        if target.kind in T.NUMBERS:
            if value.kind == "complex" and target.kind != "complex":
                return f"cannot assign complex to {target.kind}"
            if value.kind in T.REALS and target.kind in T.INTEGERS:
                return f"cannot assign {value.kind} to {target.kind}"
        return None

    WIDTH = {"int8": 0, "int16": 1, "int32": 2, "int": 3, "real32": 4, "real": 5, "complex": 6}

    def convert(self, node, target, literal=False):
        """Record that the number `node` (a single value or an array) is converted to `target` where it is used: the
        emitter reads this. A narrowing (an `int` stored in an `int32`) is checked against the value when it happens, so it
        is not recorded, except for a literal next to an int32, which is an int32 from the start."""
        t = self.types.get(id(node))
        if t is not None and t.known and target.known and t.rank >= 0 and t.kind != target.kind \
                and t.kind in T.NUMBERS and target.kind in T.NUMBERS and (t.rank == target.rank or target.rank <= 0 or t.rank == 0):
            if self.WIDTH[target.kind] < self.WIDTH[t.kind] and not literal:
                return
            self.conv[id(node)] = Type(target.kind, t.rank)

    def type_of_node(self, node):
        return self.types.get(id(node), UNKNOWN)

    def check_statement(self, s, env):
        if isinstance(s, Assign):
            self.convert(s.value, self.type_of_node(s.target))
            problem = self.assign_problem(self.type_of_node(s.target), self.type_of_node(s.value))
            if problem:
                self.error(problem, s)
        elif isinstance(s, Decl) and s.init is not None and not s.allocatable:
            self.convert(s.init, env.get(s.name, UNKNOWN))
            problem = self.assign_problem(env.get(s.name, UNKNOWN), self.type_of_node(s.init))
            if problem:
                self.error(problem, s)
        elif isinstance(s, Return) and s.value is not None and self.func is not None and self.func.ret is not None \
                and not hasattr(self.func.ret, "types"):
            ret = parse(self.func.ret, self.scope)
            self.convert(s.value, ret)
            problem = self.assign_problem(ret, self.type_of_node(s.value))
            if problem:
                self.error(f"function {self.func.name}: {problem}", s)
        elif isinstance(s, DoWhile):
            self.need_logical(self.type_of_node(s.cond), s)
        elif isinstance(s, If):
            for cond, _ in s.branches:
                if not isinstance(cond, IsType):
                    self.need_logical(self.type_of_node(cond), s)
        elif isinstance(s, Call) and s.receiver is None and s.name in self.scope.funcs and s.name not in env:
            f = self.scope.funcs[s.name]
            if f.is_sub:
                self.check_call(f, s.args, s)

    def check_call(self, f, args, node):
        """A call of the user procedure `f`: how many arguments, and whether each one can be passed."""
        if any(isinstance(a, NamedArg) for a in args):
            return
        most = len(f.params)
        least = most
        while least and is_optional(f.params[least - 1][0]):
            least -= 1
        if not least <= len(args) <= most:
            count = str(most) if least == most else f"{least} to {most}"
            self.error(f"{f.name} expects {count} arguments, got {len(args)}", node)
            return
        scope = self.interp.scopes.get(id(f), self.scope)
        for (typ, pname, intent), a in zip(f.params, args):
            if not isinstance(typ, str):
                continue
            ptype, atype = parse(typ, scope), self.type_of_node(a)
            if not (ptype.known and atype.known):
                continue
            if intent == "in":
                self.convert(a, ptype)
            if intent in ("out", "inout"):
                if ptype.kind in T.SCALAR_KINDS and atype.kind in T.SCALAR_KINDS and ptype.rank >= 0 and atype.rank >= 0 \
                        and (ptype.kind != atype.kind or ptype.rank != atype.rank) and not ptype.optional:
                    from .printer import expr as text
                    try:
                        shown = text(a.expr if hasattr(a, "expr") else a)
                    except Exception:
                        shown = pname
                    self.error(f"argument '{pname}' is {ptype.unwrapped()} but '{shown}' is {atype}", node)
            elif ptype.rank == 0 and atype.rank > 0:
                continue                                   # a function of single values runs once per element
            else:
                problem = self.assign_problem(ptype, atype)
                if problem:
                    self.error(f"argument '{pname}': {problem}", node)


def infer(unit, interp):
    """The Checker for a linked program."""
    return Checker(unit, interp).run()


def type_errors(unit, interp):
    """The mistakes of type the checker is sure of, as (line, col, message, length), like `undeclared_names`."""
    return infer(unit, interp).errors


# -- checking the checker against the interpreter -----------------------------
def runtime_type(v):
    """(kind, rank, record name) of an interpreter value, or None when it has no single type."""
    from .interp import Array, MapValue, Record, SetValue, type_of
    if isinstance(v, Array):
        if not v.data:
            return ("?", v.rank, "")
        first = type_of(v.data[0])
        if first in T.SCALAR_KINDS:
            return (first, v.rank, "")
        if isinstance(v.data[0], Record):
            return ("record", v.rank, v.data[0].rdef.name)
        return None
    if isinstance(v, Record):
        return ("record", 0, v.rdef.name)
    if isinstance(v, (MapValue, SetValue)):
        return None
    kind = type_of(v)
    if kind == "null":
        return ("null", 0, "")
    return (kind, 0, "") if kind in T.SCALAR_KINDS else None


def mismatch(static, runtime):
    """Why the static type `static` does not fit the value `runtime` (see runtime_type), or None."""
    kind, rank, name = runtime
    if static.known is False:
        return None
    if kind == "null":
        return None if static.optional or static.kind == "null" else "kind"
    if static.kind == "null":
        return "kind"
    if kind != "?":
        if static.kind == "interface" and kind == "record":
            pass
        elif static.kind != kind or (static.kind == "record" and static.name != name):
            return "kind"
    if static.rank >= 0 and static.rank != rank:
        return "rank"
    return None


def install_oracle(interp_class, path):
    """Make every interpreter created from now on compare the type of each expression it evaluates with the checker's
    answer, writing the differences (and how many expressions had a static type) to the file `path`."""
    import atexit
    original_init, original_eval = interp_class.__init__, interp_class.eval
    seen, counts = set(), {"evaluated": 0, "typed": 0}
    untyped = {}

    def init(self, *a, **k):
        original_init(self, *a, **k)
        try:
            self.checker = infer(self.unit, self)
        except Exception as err:                      # a crash in the checker is itself a finding
            self.checker = None
            record({"crash": f"{type(err).__name__}: {err}"})

    def record(item):
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            with open(path, "a") as out:
                out.write(key + "\n")

    def evaluate(self, e, env):
        v = original_eval(self, e, env)
        checker = getattr(self, "checker", None)
        if checker is not None:
            counts["evaluated"] += 1
            static = checker.types.get(id(e))
            if static is None or not static.known:
                key = type(e).__name__ + (":" + e.name if hasattr(e, "name") and isinstance(getattr(e, "name"), str) else "")
                untyped[key] = untyped.get(key, 0) + 1
            if static is not None and static.known:
                counts["typed"] += 1
                runtime = runtime_type(v)
                if runtime is not None:
                    why = mismatch(static, runtime)
                    if why:
                        from .printer import expr as show
                        try:
                            text = show(e)
                        except Exception:
                            text = type(e).__name__
                        record({"why": why, "static": str(static), "runtime": f"{runtime[0]}[{runtime[1]}]{runtime[2]}",
                                "expr": text, "node": type(e).__name__})
        return v

    def summary():
        with open(path, "a") as out:
            out.write(json.dumps({"counts": counts, "untyped": untyped}) + "\n")

    original_run = interp_class.run_program

    def run_program(self):
        code = original_run(self)             # no error: so any mistake the checker reports here is suspect
        checker = getattr(self, "checker", None)
        if checker is not None:
            from .printer import stmts
            for item in checker.errors:
                try:
                    text = " / ".join(x.strip() for x in stmts([checker.where[item]], 0))
                except Exception:
                    text = "?"
                record({"false_positive?": item[2], "line": item[0], "statement": text[:120]})
        return code

    interp_class.__init__, interp_class.eval, interp_class.run_program = init, evaluate, run_program
    atexit.register(summary)
