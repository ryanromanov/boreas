import math
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
    if isinstance(v, list): return "array"
    return "?"


def coerce(typ, value, what):
    """Assignment rules: integer -> real is implicit; everything else must match."""
    got = type_of(value)
    if got == typ:
        return value
    if typ == "real" and got == "integer":
        return float(value)
    raise LangError(f"{what}: cannot assign {got} to {typ}")


# A variable slot is [type, value, is_array, intent]. `value` is None until
# assigned; `intent` is None for ordinary locals, else "in" / "out" / "inout".
TYPE, VALUE, IS_ARRAY, INTENT = range(4)


class Interp:
    def __init__(self, unit, out=print):
        self.unit, self.out = unit, out
        self.builtins = {
            "sqrt": math.sqrt, "abs": abs, "mod": lambda a, b: int(math.fmod(a, b)) if isinstance(a, int) else math.fmod(a, b),
            "size": len, "sum": sum, "real": float, "int": int, "max": max, "min": min,
            "sin": math.sin, "cos": math.cos, "exp": math.exp, "log": math.log,
        }

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
            if s.name in env:
                raise LangError(f"line {s.line}: '{s.name}' already declared")
            if s.size is not None:
                n = self.eval(s.size, env)
                default = {"integer": 0, "real": 0.0, "logical": False, "string": ""}[s.type]
                val = [default] * n
                if s.init is not None:
                    val = [coerce(s.type, x, f"line {s.line}") for x in self.broadcast(self.eval(s.init, env), n)]
                env[s.name] = [s.type, val, True, None]
            else:
                val = None
                if s.init is not None:
                    val = coerce(s.type, self.eval(s.init, env), f"line {s.line}")
                env[s.name] = [s.type, val, False, None]
        elif k is Assign:
            self.write(self.resolve(s.target, env, s.line), self.eval(s.value, env), s.line)
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
            i = lo
            while (i <= hi) if step > 0 else (i >= hi):
                self.write(ref, i, s.line)
                try:
                    self.exec_block(s.body, env)
                except _Cycle:
                    pass
                except _Exit:
                    break
                i += step
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

    def show(self, v):
        if isinstance(v, bool): return "true" if v else "false"
        if isinstance(v, list): return "[" + ", ".join(self.show(x) for x in v) + "]"
        return str(v)

    def truth(self, v):
        if not isinstance(v, bool):
            raise LangError(f"condition must be logical, got {type_of(v)}")
        return v

    def lookup(self, name, env, line=None):
        if name not in env:
            raise LangError(f"line {line}: '{name}' is not declared" if line else f"'{name}' is not declared")
        return env[name]

    def broadcast(self, v, n):
        return v if isinstance(v, list) else [v] * n

    def check_index(self, idx, arr, line):
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise LangError(f"line {line}: array index must be integer")
        if not 1 <= idx <= len(arr):
            raise LangError(f"line {line}: index {idx} out of bounds 1..{len(arr)}")

    # -- assignable places ("references")
    def resolve(self, t, env, line):
        """Turn a variable or array element into (slot, index-or-None, name)."""
        if not isinstance(t, (Var, Apply)):
            raise LangError(f"line {line}: this must be a variable or array element")
        slot = self.lookup(t.name, env, line)
        if isinstance(t, Var):
            return slot, None, t.name
        if not slot[IS_ARRAY]:
            raise LangError(f"line {line}: '{t.name}' is not an array")
        if len(t.args) != 1 or isinstance(t.args[0], Slice):
            raise LangError(f"line {line}: cannot assign to a slice of '{t.name}'")
        idx = self.eval(t.args[0], env)
        self.check_index(idx, slot[VALUE], line)
        return slot, idx, t.name

    def write(self, ref, v, line):
        slot, idx, name = ref
        if slot[INTENT] == "in":
            raise LangError(f"line {line}: '{name}' is an 'in' parameter and cannot be assigned")
        where = f"line {line}"
        if idx is not None:
            slot[VALUE][idx - 1] = coerce(slot[TYPE], v, where)
        elif slot[IS_ARRAY]:  # whole-array assignment
            v = self.broadcast(v, len(slot[VALUE]))
            if len(v) != len(slot[VALUE]):
                raise LangError(f"{where}: array size mismatch")
            slot[VALUE] = [coerce(slot[TYPE], x, where) for x in v]
        else:
            slot[VALUE] = coerce(slot[TYPE], v, where)

    # -- expressions
    def eval(self, e, env):
        k = type(e)
        if k in (Num, Str, Bool): return e.value
        if k is Var:
            v = self.lookup(e.name, env)[VALUE]
            if v is None:
                raise LangError(f"'{e.name}' is used before it is assigned")
            return v
        if k is ArrayLit: return [self.eval(x, env) for x in e.items]
        if k is Unary:
            v = self.eval(e.operand, env)
            if e.op == "not": return self.map1(lambda x: not self.truth(x), v)
            return self.map1((lambda x: -x) if e.op == "-" else (lambda x: x), v)
        if k is Binary:
            return self.binary(e.op, self.eval(e.left, env), self.eval(e.right, env))
        if k is Apply: return self.apply(e, env)
        raise LangError(f"cannot evaluate {k.__name__}")

    def map1(self, f, v):
        return [f(x) for x in v] if isinstance(v, list) else f(v)

    def binary(self, op, a, b):
        # whole-array operations: scalars broadcast, arrays must conform
        if isinstance(a, list) or isinstance(b, list):
            n = len(a) if isinstance(a, list) else len(b)
            a, b = self.broadcast(a, n), self.broadcast(b, n)
            if len(a) != len(b):
                raise LangError(f"array size mismatch: {len(a)} vs {len(b)}")
            return [self.binary(op, x, y) for x, y in zip(a, b)]
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
            slot = env[e.name]
            if not slot[IS_ARRAY]:
                raise LangError(f"line {e.line}: '{e.name}' is not an array")
            arr, a = slot[VALUE], e.args[0]
            if isinstance(a, Slice):
                lo = self.eval(a.lo, env) if a.lo else 1
                hi = self.eval(a.hi, env) if a.hi else len(arr)
                self.check_index(lo, arr, e.line); self.check_index(hi, arr, e.line)
                return arr[lo - 1:hi]
            idx = self.eval(a, env)
            self.check_index(idx, arr, e.line)
            return arr[idx - 1]
        args = [self.eval(a, env) for a in e.args]
        f = self.unit.functions.get(e.name)
        if f is not None:
            if f.is_sub:
                raise LangError(f"line {e.line}: '{e.name}' is a subroutine; use 'call {e.name}(...)'")
            return self.call_function(f, args, e.line)
        if e.name in self.builtins:
            return self.builtins[e.name](*args)
        raise LangError(f"line {e.line}: unknown function or array '{e.name}'")

    # -- procedures
    def bind(self, f, values, line):
        """Build the callee's environment. `None` marks an unassigned out scalar."""
        env = {}
        for (typ, name, intent), v in zip(f.params, values):
            is_arr = isinstance(v, list)
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
        """Best-effort (slot, index-or-None) for an argument that names a variable."""
        if isinstance(a, Var) and a.name in env:
            return env[a.name], None
        if isinstance(a, Apply) and a.name in env and env[a.name][IS_ARRAY]:
            if len(a.args) == 1 and not isinstance(a.args[0], Slice):
                return env[a.name], self.eval(a.args[0], env)
            return env[a.name], None
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
            if slot[INTENT] == "in":
                raise LangError(f"line {s.line}: '{ref[2]}' is an 'in' parameter and cannot be passed as {intent}")
            if slot[TYPE] != typ:
                raise LangError(f"line {s.line}: argument '{name}' is {typ} but '{ref[2]}' is {slot[TYPE]}")
            outs.append((n, ref))
            if ref[1] is None and slot[IS_ARRAY]:
                values.append(list(slot[VALUE]))                 # copy in
            elif intent == "inout":
                values.append(self.eval(a, env))
            else:
                values.append(None)                              # out: starts unassigned

        # Aliasing: an out/inout variable may not appear in any other argument.
        for n, (slot, idx, name) in outs:
            for m, a in enumerate(s.args):
                other = self.reference_of(a, env, s.line)
                if m != n and other and other[0] is slot and (idx is None or other[1] is None or idx == other[1]):
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
