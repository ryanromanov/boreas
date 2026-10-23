import math
from .lexer import LangError
from .parser import *


class _Return(Exception):
    def __init__(self, value): self.value = value
class _Exit(Exception): pass
class _Cycle(Exception): pass


PY_TYPE = {"integer": int, "real": float, "logical": bool, "string": str}


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

    # env maps name -> [type, value, is_array]
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
                env[s.name] = [s.type, val, True]
            else:
                val = None
                if s.init is not None:
                    val = coerce(s.type, self.eval(s.init, env), f"line {s.line}")
                env[s.name] = [s.type, val, False]
        elif k is Assign:
            self.assign(s, env)
        elif k is If:
            for cond, body in s.branches:
                if self.truth(self.eval(cond, env)):
                    self.exec_block(body, env)
                    return
            self.exec_block(s.orelse, env)
        elif k is Do:
            slot = self.lookup(s.var, env, s.line)
            lo, hi = self.eval(s.lo, env), self.eval(s.hi, env)
            step = self.eval(s.step, env) if s.step else 1
            if step == 0:
                raise LangError(f"line {s.line}: do step cannot be zero")
            i = lo
            while (i <= hi) if step > 0 else (i >= hi):
                slot[1] = i
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

    def assign(self, s, env):
        t = s.target
        if isinstance(t, Var):
            slot = self.lookup(t.name, env, s.line)
            v = self.eval(s.value, env)
            if slot[2]:  # whole-array assignment
                v = self.broadcast(v, len(slot[1]))
                if len(v) != len(slot[1]):
                    raise LangError(f"line {s.line}: array size mismatch")
                slot[1] = [coerce(slot[0], x, f"line {s.line}") for x in v]
            else:
                slot[1] = coerce(slot[0], v, f"line {s.line}")
        else:  # element assignment
            slot = self.lookup(t.name, env, s.line)
            if not slot[2]:
                raise LangError(f"line {s.line}: '{t.name}' is not an array")
            idx = self.eval(t.args[0], env)
            self.check_index(idx, slot[1], s.line)
            slot[1][idx - 1] = coerce(slot[0], self.eval(s.value, env), f"line {s.line}")

    def check_index(self, idx, arr, line):
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise LangError(f"line {line}: array index must be integer")
        if not 1 <= idx <= len(arr):
            raise LangError(f"line {line}: index {idx} out of bounds 1..{len(arr)}")

    # -- expressions
    def eval(self, e, env):
        k = type(e)
        if k in (Num, Str, Bool): return e.value
        if k is Var: return self.lookup(e.name, env)[1]
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
            if not slot[2]:
                raise LangError(f"line {e.line}: '{e.name}' is not an array")
            arr, a = slot[1], e.args[0]
            if isinstance(a, Slice):
                lo = self.eval(a.lo, env) if a.lo else 1
                hi = self.eval(a.hi, env) if a.hi else len(arr)
                self.check_index(lo, arr, e.line); self.check_index(hi, arr, e.line)
                return arr[lo - 1:hi]
            idx = self.eval(a, env)
            self.check_index(idx, arr, e.line)
            return arr[idx - 1]
        args = [self.eval(a, env) for a in e.args]
        if e.name in self.unit.functions:
            return self.call(self.unit.functions[e.name], args, e.line)
        if e.name in self.builtins:
            return self.builtins[e.name](*args)
        raise LangError(f"line {e.line}: unknown function or array '{e.name}'")

    def call(self, f, args, line):
        if len(args) != len(f.params):
            raise LangError(f"line {line}: {f.name} expects {len(f.params)} arguments, got {len(args)}")
        env = {}
        for (typ, name), v in zip(f.params, args):
            is_arr = isinstance(v, list)
            env[name] = [typ, v if is_arr else coerce(typ, v, f"line {line}: argument '{name}'"), is_arr]
        try:
            self.exec_block(f.body, env)
        except _Return as r:
            if f.ret and r.value is None:
                raise LangError(f"function {f.name} must return a {f.ret}")
            return coerce(f.ret, r.value, f"function {f.name}") if f.ret else None
        if f.ret:
            raise LangError(f"function {f.name} ended without returning a {f.ret}")
        return None
