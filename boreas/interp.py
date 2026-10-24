import math
from itertools import product
from .lexer import LangError
from .parser import *


class _Return(Exception):
    def __init__(self, value): self.value = value
class _Exit(Exception): pass
class _Cycle(Exception): pass


def type_of(v):
    if isinstance(v, bool): return "logical"
    if isinstance(v, int): return "integer"
    if isinstance(v, float): return "real"
    if isinstance(v, str): return "string"
    if isinstance(v, Array): return "array"
    return "?"


def coerce(typ, value, what):
    """Assignment rules: integer -> real is implicit; everything else must match."""
    got = type_of(value)
    if got == typ:
        return value
    if typ == "real" and got == "integer":
        return float(value)
    raise LangError(f"{what}: cannot assign {got} to {typ}")


def fmt_shape(shape):
    return "(" + ", ".join(str(n) for n in shape) + ")"


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
        return Array(self.shape, list(self.data))

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


def _reduce(name, f):
    return lambda a: f(_need_array(name, a).data)


BUILTINS = {
    "size": _size, "shape": _shape, "reshape": _reshape, "matmul": _matmul,
    "transpose": _transpose, "dot_product": _dot_product,
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
# assigned (scalars only; an array always exists); `intent` is None for
# ordinary locals, "const" for constants, else "in" / "out" / "inout".
TYPE, VALUE, IS_ARRAY, INTENT = range(4)

# A reference to something assignable is (slot, subs, name). `subs` is None for
# the whole variable, else one entry per dimension: an int or an inclusive (lo, hi).


def _span(s):
    return (s, s) if isinstance(s, int) else s


def overlaps(a, b):
    """Could two subscript lists of the same array touch a common element?"""
    if a is None or b is None:
        return True
    return all(max(_span(x)[0], _span(y)[0]) <= min(_span(x)[1], _span(y)[1]) for x, y in zip(a, b))


class Interp:
    def __init__(self, unit, out=print):
        self.unit, self.out = unit, out

    def run(self):
        self.exec_block(self.unit.program.body, {})

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
            if (ref[0][INTENT] in ("out", "inout")) != s.update:
                raise LangError(f"line {s.line}: 'update' must be used exactly when assigning to an "
                                f"out or inout parameter ('{ref[2]}')")
            self.write(ref, self.eval(s.value, env), s.line)
        elif k is If:
            for cond, body in s.branches:
                if self.truth(self.eval(cond, env)):
                    self.exec_block(body, env)
                    return
            self.exec_block(s.orelse, env)
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
                except _Cycle:
                    pass
                except _Exit:
                    exited = True
                    break
                i += step
            if not exited:                  # like Fortran: the variable ends one step past the range
                self.write(ref, i, s.line)
        elif k is DoWhile:
            while self.truth(self.eval(s.cond, env)):
                try:
                    self.exec_block(s.body, env)
                except _Cycle:
                    pass
                except _Exit:
                    break
        elif k is Print:
            self.out(" ".join(self.show(self.eval(a, env)) for a in s.args))
        elif k is Call:
            self.call_subroutine(s, env)
        elif k is Return:
            raise _Return(self.eval(s.value, env) if s.value else None)
        elif k is Exit:
            raise _Exit()
        elif k is Cycle:
            raise _Cycle()
        elif k is ExprStmt:
            self.eval(s.expr, env)

    def declare(self, s, env):
        where = f"line {s.line}"
        if s.name in env:
            raise LangError(f"{where}: '{s.name}' already declared")
        if s.dims is None:
            val = None
            if s.init is not None:
                val = coerce(s.type, self.eval(s.init, env), where)
            env[s.name] = [s.type, val, False, "const" if s.const else None]
            return
        shape = [self.eval(d, env) for d in s.dims]
        if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in shape):
            raise LangError(f"{where}: array dimensions must be non-negative integers")
        n = math.prod(shape)
        default = {"integer": 0, "real": 0.0, "logical": False, "string": ""}[s.type]
        data = [default] * n
        if s.init is not None:
            init = self.eval(s.init, env)
            if isinstance(init, Array):
                if init.shape != tuple(shape):
                    raise LangError(f"{where}: initializer has shape {fmt_shape(init.shape)}, "
                                    f"'{s.name}' has shape {fmt_shape(shape)}")
                data = [coerce(s.type, x, where) for x in init.data]
            else:
                data = [coerce(s.type, init, where)] * n
        env[s.name] = [s.type, Array(shape, data), True, "const" if s.const else None]

    def show(self, v):
        if isinstance(v, bool): return "true" if v else "false"
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
        """Turn a variable, array element, or array section into a reference."""
        if not isinstance(t, (Var, Apply)):
            raise LangError(f"line {line}: this must be a variable or array element")
        slot = self.lookup(t.name, env, line)
        if isinstance(t, Var):
            return slot, None, t.name
        if not slot[IS_ARRAY]:
            raise LangError(f"line {line}: '{t.name}' is not an array")
        return slot, self.subscripts(slot[VALUE], t.args, env, line, t.name), t.name

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
        if slot[INTENT] == "in":
            raise LangError(f"line {line}: '{name}' is an 'in' parameter and cannot be assigned")
        if slot[INTENT] == "const":
            raise LangError(f"line {line}: '{name}' is a constant and cannot be assigned")
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
            v = self.lookup(e.name, env)[VALUE]
            if v is None:
                raise LangError(f"'{e.name}' is used before it is assigned")
            return v
        if k is ArrayLit:
            items = [self.eval(x, env) for x in e.items]
            if any(isinstance(x, Array) for x in items):
                raise LangError("array literals hold scalars; use reshape() to build a matrix")
            return Array((len(items),), items)
        if k is Unary:
            v = self.eval(e.operand, env)
            f = (lambda x: not self.truth(x)) if e.op == "not" else (lambda x: -x) if e.op == "-" else (lambda x: x)
            return Array(v.shape, [f(x) for x in v.data]) if isinstance(v, Array) else f(v)
        if k is Binary:
            return self.binary(e.op, self.eval(e.left, env), self.eval(e.right, env))
        if k is Apply: return self.apply(e, env)
        raise LangError(f"cannot evaluate {k.__name__}")

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
        if e.name in env:  # indexing or slicing
            if not env[e.name][IS_ARRAY]:
                raise LangError(f"line {e.line}: '{e.name}' is not an array")
            return self.read(self.resolve(e, env, e.line))
        args = [self.eval(a, env) for a in e.args]
        f = self.unit.functions.get(e.name)
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
        env = {}
        for (typ, name, intent), v in zip(f.params, values):
            is_arr = isinstance(v, Array)
            if v is not None and not is_arr:
                v = coerce(typ, v, f"line {line}: argument '{name}'")
            env[name] = [typ, v, is_arr, intent]
        return env

    def run_body(self, f, env):
        try:
            self.exec_block(f.body, env)
        except _Return as r:
            return r.value
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
        if isinstance(a, Var) and a.name in env:
            return env[a.name], None
        if isinstance(a, Apply) and a.name in env and env[a.name][IS_ARRAY]:
            slot = env[a.name]
            return slot, self.subscripts(slot[VALUE], a.args, env, line, a.name)
        return None

    def call_subroutine(self, s, env):
        f = self.unit.functions.get(s.name)
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
            if slot[INTENT] in ("in", "const"):
                what = "an 'in' parameter" if slot[INTENT] == "in" else "a constant"
                raise LangError(f"line {s.line}: '{ref[2]}' is {what} and cannot be passed as {intent}")
            if slot[TYPE] != typ:
                raise LangError(f"line {s.line}: argument '{name}' is {typ} but '{ref[2]}' is {slot[TYPE]}")
            outs.append((n, ref))
            current = self.read(ref)
            if isinstance(current, Array):
                values.append(current.copy())                     # copy in
            elif intent == "out":
                values.append(None)                               # out: starts unassigned
            elif current is None:
                raise LangError(f"'{ref[2]}' is used before it is assigned")
            else:
                values.append(current)

        # Aliasing: an out/inout variable may not overlap any other argument.
        for n, (slot, subs, name) in outs:
            for m, a in enumerate(s.args):
                other = self.reference_of(a, env, s.line)
                if m != n and other and other[0] is slot and overlaps(subs, other[1]):
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
            self.write(ref, result, s.line)
