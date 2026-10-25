import math
from itertools import product
from .lexer import LangError
from .parser import *


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


def type_of(v):
    if isinstance(v, bool): return "logical"
    if isinstance(v, int): return "integer"
    if isinstance(v, float): return "real"
    if isinstance(v, str): return "string"
    if isinstance(v, Array): return "array"
    if isinstance(v, Record): return v.rdef.name
    return "?"


def coerce(typ, value, what):
    """Assignment rules: integer -> real is implicit; everything else must match."""
    got = type_of(value)
    if got == typ:
        return value.copy() if isinstance(value, Record) else value
    if typ == "real" and got == "integer":
        return float(value)
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
    return sum(x * y for x, y in zip(a.data, b.data))


def _allocated(x):
    raise LangError("allocated() needs the name of an allocatable array")   # apply() handles real calls


def _reduce(name, f):
    return lambda a: f(_need_array(name, a).data)


BUILTINS = {
    "size": _size, "shape": _shape, "reshape": _reshape, "matmul": _matmul,
    "transpose": _transpose, "dot_product": _dot_product, "allocated": _allocated,
    "sum": _reduce("sum", sum), "maxval": _reduce("maxval", max), "minval": _reduce("minval", min),
    "any": _reduce("any", any), "all": _reduce("all", all),
    "max": max, "min": min,
}
# Elementwise functions: applied to every element when given an array.
ELEMENTAL = {
    "sqrt": math.sqrt, "abs": abs, "sin": math.sin, "cos": math.cos, "exp": math.exp,
    "log": math.log, "real": float, "int": int,
    "mod": lambda a, b: int(math.fmod(a, b)) if isinstance(a, int) and isinstance(b, int) else math.fmod(a, b),
}


# A variable slot is [type, value, is_array, intent]. `value` is None until
# assigned (scalars) or allocated (allocatable arrays); `intent` is None for
# ordinary locals, "const" for constants, else "in" / "out" / "inout".
# An allocatable array's slot has a fifth entry, its rank.
TYPE, VALUE, IS_ARRAY, INTENT, RANK = range(5)
DEFAULTS = {"integer": 0, "real": 0.0, "logical": False, "string": ""}

# A reference to something assignable is (slot, subs, name). `subs` is None for
# the whole variable, else one entry per dimension: an int or an inclusive (lo, hi).


def _span(s):
    return (s, s) if isinstance(s, int) else s


def overlaps(a, b):
    """Could two subscript lists of the same array touch a common element?"""
    if a is None or b is None:
        return True
    return all(max(_span(x)[0], _span(y)[0]) <= min(_span(x)[1], _span(y)[1]) for x, y in zip(a, b))


class Scope:
    """What a procedure (or the program) can see besides its own variables:
    procedures and constants, both its own and the ones it imported."""
    def __init__(self, funcs, consts, records=None):
        self.funcs, self.consts = funcs, consts
        self.records = {} if records is None else records


class Interp:
    def __init__(self, unit, out=print):
        self.unit, self.out = unit, out
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
                if f.type not in DEFAULTS and f.type not in scope.records:
                    raise LangError(f"line {f.line}: field '{f.name}' of record '{r.name}' has unknown type '{f.type}'")
                for d in f.dims or []:
                    if not (isinstance(d, Num) and isinstance(d.value, int) and d.value >= 0):
                        raise LangError(f"line {f.line}: an array field of a record needs a literal size")

        def contains(name, path):
            if name in path:
                raise LangError(f"line {records[name].line}: record '{name}' contains itself: "
                                + " -> ".join(path[path.index(name):] + (name,)))
            for f in scope.records[name].fields:
                if f.type in scope.records:
                    contains(f.type, path + (name,))

        for name in records:
            contains(name, ())

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
        self.loading.pop()
        self.exports[name] = {
            **{f.name: f for f in mod.functions.values() if f.public},
            **{r.name: r for r in mod.records.values() if r.public},
            **{d.name: env[d.name] for d in mod.consts if d.public}}
        return self.exports[name]

    def run(self):
        self.scope = self.program_scope
        self.exec_block(self.unit.program.body, dict(self.scope.consts))

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
            self.out(" ".join(self.show(self.eval(a, env)) for a in s.args))
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
        if s.type not in DEFAULTS:
            sub = self.scope.records.get(s.type)
            if sub is None:
                raise LangError(f"{where}: unknown type '{s.type}'")
            if s.allocatable:
                raise LangError(f"{where}: allocatable arrays of records are not supported")
        if s.allocatable:
            env[s.name] = [s.type, None, True, None, len(s.dims)]
            return
        if s.dims is None:
            if s.init is not None:
                val = coerce(s.type, self.eval(s.init, env), where)
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
            sub = owner.records.get(f.type)
            if f.dims is None:
                value = self.new_record(sub, blank) if sub else (None if blank else DEFAULTS[f.type])
                fields[f.name] = [f.type, value, False, None]
            else:
                shape = [d.value for d in f.dims]
                n = math.prod(shape)
                data = ([self.new_record(sub, blank) for _ in range(n)] if sub
                        else [None if blank else DEFAULTS[f.type]] * n)
                fields[f.name] = [f.type, Array(shape, data), True, None]
        return Record(rdef, fields)

    def construct(self, rdef, e, env):
        """`point(1.0, 2.0)` gives every field in order; `point(x = 1.0)` gives only the named
        ones (the others have no value yet); positional arguments may come first."""
        line = f"line {e.line}"
        pos = [a for a in e.args if not isinstance(a, NamedArg)]
        named = [a for a in e.args if isinstance(a, NamedArg)]
        if len(pos) > len(rdef.fields):
            raise LangError(f"{line}: {rdef.name} has {len(rdef.fields)} field(s), got {len(pos)} value(s)")
        if not named and len(pos) != len(rdef.fields):
            raise LangError(f"{line}: {rdef.name}() needs all {len(rdef.fields)} field(s) "
                            f"({', '.join(f.name for f in rdef.fields)}), or name the ones you give")
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
            raise LangError(f"line {line}: '{t.name}' is not an array")
        return Ref(slot, self.subscripts(slot[VALUE], t.args, env, line, t.name), t.name)

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
            if isinstance(v, Record):
                raise LangError(f"records cannot be used with '{e.op}'; use their fields")
            f = (lambda x: not self.truth(x)) if e.op == "not" else (lambda x: -x) if e.op == "-" else (lambda x: x)
            return Array(v.shape, [f(x) for x in v.data]) if isinstance(v, Array) else f(v)
        if k is Binary:
            return self.binary(e.op, self.eval(e.left, env), self.eval(e.right, env))
        if k is Apply: return self.apply(e, env)
        if k is SelectExpr: return self.select_expr(e, env)
        raise LangError(f"cannot evaluate {k.__name__}")

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
        if typ not in ("integer", "string", "logical"):
            raise LangError(f"line {line}: select case needs an integer, string or logical value, got {typ}")
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
                    if x is not None and type_of(x) != typ:
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
        if op in ("==", "/=", "<", "<=", ">", ">="):
            return {"==": a == b, "/=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        if isinstance(a, str) and op == "+" and isinstance(b, str):
            return a + b
        for v in (a, b):
            if type_of(v) not in ("integer", "real"):
                raise LangError(f"operator {op!r} needs numbers, got {type_of(v)}")
        if op == "+": return a + b
        if op == "-": return a - b
        if op == "*": return a * b
        if op == "**": return a ** b
        if op == "/":
            if b == 0 and isinstance(b, int):
                raise LangError("integer division by zero")
            if isinstance(a, int) and isinstance(b, int):
                return int(a / b)  # truncates toward zero, like Fortran
            return a / b
        raise LangError(f"unknown operator {op}")

    def apply(self, e, env):
        if e.name == "allocated" and e.name not in env and "allocated" not in self.scope.funcs:
            if len(e.args) != 1 or not isinstance(e.args[0], Var):
                raise LangError(f"line {e.line}: allocated() needs the name of an allocatable array")
            return self.allocatable(e.args[0].name, env, e.line)[VALUE] is not None
        if e.name in env:  # indexing or slicing
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

    def call_builtin(self, name, args, line):
        try:
            if name in ELEMENTAL:
                f = ELEMENTAL[name]
                if not any(isinstance(a, Array) for a in args):
                    return f(*args)
                shape = self.common_shape(args)
                n = math.prod(shape)
                cols = [a.data if isinstance(a, Array) else [a] * n for a in args]
                return Array(shape, [f(*xs) for xs in zip(*cols)])
            return BUILTINS[name](*args)
        except LangError:
            raise
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as err:
            raise LangError(f"line {line}: {name}(): {err}")

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

    def call_function(self, f, args, line):
        if len(args) != len(f.params):
            raise LangError(f"line {line}: {f.name} expects {len(f.params)} arguments, got {len(args)}")
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
        if len(s.args) != len(f.params):
            raise LangError(f"line {s.line}: {s.name} expects {len(f.params)} arguments, got {len(s.args)}")

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
