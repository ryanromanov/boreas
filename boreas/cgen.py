"""Compiles the numeric part of Boreas to C (D76, D81).

`compile_unit(unit, interp)` returns the text of one self-contained C file: the runtime (cruntime.h), then the program.
It reads the types and conversions the type checker worked out (typecheck.py) and never works them out again. A program
that uses anything outside the subset is refused as a whole, with a message that names the construct and its line: no
partial code is ever written. Every operation is emitted as three-address code (each part into a temporary, in source
order), so the order of evaluation, and of any output or error, is the interpreter's.

The subset so far: `int`, `int32`, `real`, `real32` and `logical` values and arrays of them (up to three dimensions, element by
element or whole), `if`, `do`, `do while`, `exit`, `cycle`, `stop`, `return`, `print`/`println`, and functions and
subroutines whose parameters are single values or arrays, passed `in` or (subroutines) `inout`."""
import os
import shutil
import subprocess
import tempfile
from .parser import (Apply, ArrayLit, Assign, Bool, Binary, Call, Comment, Cycle, Decl, Do, DoWhile, Exit, If, IsType,
                     MatrixLit, Num, Print, Return, Slice, Stop, Str, Unary, Var)
from .tree import statements_in, walk
from .typecheck import infer
from .types import Type

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("int", "int32", "real", "real32", "logical")
CSCALAR = {"int": "int64_t", "int32": "int32_t", "real": "double", "real32": "float", "logical": "bool"}
BUILTINS = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "mod", "min", "max", "real", "real32", "int",
            "size", "maxval", "minval", "sum", "product", "matmul", "dot_product"}
ELEMENTAL1 = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "real", "real32"}
SINGLE_LIBM = {"sin": "sinf", "cos": "cosf", "tan": "tanf", "exp": "expf", "log": "logf", "log10": "log10f"}
WRITERS = {"int": "rt_write_int", "int32": "rt_write_int", "real": "rt_write_real", "real32": "rt_write_real32",
           "logical": "rt_write_bool", "string": "rt_write_str"}
MAX_RANK = 3


class NotCompiled(Exception):
    """The program uses something the compiler does not handle yet."""
    def __init__(self, what, line=None):
        self.what, self.line = what, line
        super().__init__(f"not supported by the compiler yet: {what}" + (f" (line {line})" if line else ""))


def c_name(name):
    return "v_" + name


def f_name(name):
    return "f_" + name


def ctype(t):
    return CSCALAR[t.kind] if t.rank == 0 else f"rt_{t.kind}_arr"


def c_string(text):
    out = []
    for ch in text:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif 32 <= ord(ch) < 127:
            out.append(ch)
        else:
            out.extend(f"\\{b:03o}" for b in ch.encode("utf-8"))
    return '"' + "".join(out) + '"'


class V:
    """A computed value: the type the program wants it as (`t`), a C atom, and (for an array whose stored kind is not the
    wanted one) the kind its elements are stored as (`src`)."""
    __slots__ = ("t", "atom", "src")

    def __init__(self, t, atom, src=None):
        self.t, self.atom, self.src = t, atom, src


class Compiler:
    def __init__(self, unit, interp, release=False):
        self.unit, self.interp, self.release = unit, interp, release
        self.checker = infer(unit, interp)
        self.types, self.conv = self.checker.types, self.checker.conv
        self.lines, self.depth, self.n, self.line = [], 1, 0, 0
        self.loops = []                 # innermost last: (label name or None, id)
        self.func = None
        self.vars = {}                  # name -> (Type, C lvalue)
        self.arrays = []                # the local arrays of the function being written (freed at its end)
        self.temps = []                 # array temporaries of the statement being written (freed after it)

    # -- refusing what is not handled ---------------------------------------------------------------------------
    def refuse(self, what, node=None, line=None):
        raise NotCompiled(what, line or getattr(node, "line", None) or None)

    def supported(self, t, what, node=None):
        if t is None or not t.known or t.kind not in KINDS or not 0 <= t.rank <= MAX_RANK:
            self.refuse(what if t is not None and t.known else f"an expression of unknown type", node)

    def check(self):
        u = self.unit
        if u.modules:
            self.refuse("modules")
        if u.records:
            self.refuse("records")
        if u.interfaces:
            self.refuse("interfaces")
        if u.program is None:
            self.refuse("a file without a program")
        if self.checker.errors:
            line, _, message, _ = self.checker.errors[0]
            raise NotCompiled(f"the program has a type error ({message})", line)
        for f in u.functions.values():
            if f.ret is not None:
                rt = self.param_type(f.ret)
                if rt is None or rt.rank != 0:
                    self.refuse(f"function '{f.name}' returns {f.ret if isinstance(f.ret, str) else 'several values'}")
            for typ, name, intent in f.params:
                pt = self.param_type(typ)
                if pt is None:
                    self.refuse(f"parameter '{name}' of '{f.name}' ({typ if isinstance(typ, str) else 'a procedure'})")
                if intent == "out":
                    self.refuse(f"out parameter '{name}' of '{f.name}'")
                if intent == "inout" and f.ret is not None:
                    self.refuse(f"inout parameter '{name}' of the function '{f.name}'")
        for f in [*u.functions.values(), None]:
            self.check_body((f.body if f else u.program.body), f)

    def param_type(self, typ):
        """The Type of a parameter or result written `real`, `int[]`, `real[,]`, or None when it is not handled."""
        from .types import parse
        if not isinstance(typ, str):
            return None
        t = parse(typ, None)
        if not t.known or t.kind not in KINDS or t.optional or not 0 <= t.rank <= MAX_RANK:
            return None
        return t

    def check_body(self, body, f):
        strings = set()
        fixed = {name for typ, name, intent in f.params if intent == "in"} if f is not None else set()
        fixed |= {d.name for d in statements_in(body) if isinstance(d, Decl) and d.const}
        for s in statements_in(body):
            kind = type(s)
            if kind is Return:
                if f is None or (s.value is None) != (f.ret is None):
                    self.refuse("a return that does not match its procedure", s)
                continue
            if kind is Do and s.var in fixed:
                self.refuse(f"a loop over the constant or parameter '{s.var}'", s)
            if kind in (If, Do, DoWhile, Stop, Exit, Cycle, Comment):
                continue
            if kind is Decl:
                t = self.decl_type(s)
                if t is None:
                    what = "allocatable " if s.allocatable else ""
                    self.refuse(f"the declaration of '{s.name}' ({what}{s.type})", s)
            elif kind is Assign:
                root = s.target
                if isinstance(root, Apply):
                    if not root.bracket or any(isinstance(a, Slice) for a in root.args):
                        self.refuse("an assignment to part of an array", s)
                elif not isinstance(root, Var):
                    self.refuse("an assignment to anything but a variable or an array element", s)
                if root.name in fixed:
                    self.refuse(f"an assignment to the constant or parameter '{root.name}'", s)
            elif kind is Print:
                if s.to is not None:
                    self.refuse("printing to a file", s)
                strings |= {id(a) for a in s.args if isinstance(a, Str)}
            elif kind is Call:
                if s.receiver is not None or s.name not in self.unit.functions:
                    self.refuse(f"the call of '{s.name}'", s)
                self.check_aliasing(s, fixed)
            else:
                self.refuse(f"a {kind.__name__} statement", s)
        for node in walk(body):
            self.check_expr(node, strings)

    def check_call_args(self, f, args, node):
        for a, (typ, _, _) in zip(args, f.params):
            at, pt = self.types.get(id(a)), self.param_type(typ)
            if at is None or pt is None or (at.rank != pt.rank) or (at.kind != pt.kind and at.rank > 0):
                self.refuse(f"the call of '{f.name}' with an argument that is not what the parameter takes", node)

    def check_aliasing(self, s, fixed=()):
        f = self.unit.functions[s.name]
        self.check_call_args(f, s.args, s)
        roots = [getattr(a, "name", None) for a in s.args]
        for (typ, pname, intent), root in zip(f.params, roots):
            if intent == "inout" and root in fixed:
                self.refuse(f"the constant or parameter '{root}' passed as inout", s)
            if intent == "inout" and root is not None and roots.count(root) > 1:
                self.refuse(f"'{root}' passed as inout and as another argument of the same call", s)

    def decl_type(self, d):
        if d.allocatable or d.type not in KINDS:
            return None
        rank = len(d.dims) if d.dims is not None else 0
        if rank > MAX_RANK:
            return None
        return Type(d.type, rank)

    STATEMENTS = (Decl, Assign, If, Do, DoWhile, Print, Call, Return, Stop, Exit, Cycle, Comment)

    def check_expr(self, e, strings):
        kind = type(e)
        if kind in self.STATEMENTS:
            return
        if kind is Str:
            if id(e) not in strings:
                self.refuse("text outside a print", e)
            return
        if kind not in (Num, Bool, Var, Unary, Binary, Apply, ArrayLit, MatrixLit):
            if kind is IsType:
                self.refuse("a type test", e)
            self.refuse(f"a {kind.__name__}", e)
        if kind is Unary and e.op not in ("-", "+", "not"):
            self.refuse(f"the operator '{e.op}'", e)
        if kind is Binary and e.op not in ("+", "-", "*", "/", "**", "==", "/=", "<", "<=", ">", ">=", "and", "or"):
            self.refuse(f"the operator '{e.op}'", e)
        if kind in (ArrayLit, MatrixLit):
            items = e.items if kind is ArrayLit else [x for row in e.rows for x in row]
            if any(self.types.get(id(x)) is None or self.types[id(x)].rank != 0 for x in items) or not items:
                self.refuse("an array literal of anything but single values", e)
        if kind is Apply:
            if e.chain:
                self.refuse("a chained subscript", e)
            if any(isinstance(a, Slice) for a in e.args):
                self.refuse("part of an array", e)
            if e.bracket:
                pass
            elif e.name in self.unit.functions:
                self.check_call_args(self.unit.functions[e.name], e.args, e)
            elif e.name not in BUILTINS:
                self.refuse(f"the function '{e.name}'", e)
            else:
                self.check_builtin(e)
        t = self.types.get(id(e))
        if t is None or not t.known or t.kind not in KINDS or not 0 <= t.rank <= MAX_RANK:
            self.refuse(f"an expression of type {t if t is not None else 'unknown'}", e)

    def check_builtin(self, e):
        name = e.name
        ranks = [self.types[id(a)].rank for a in e.args]
        if name in ELEMENTAL1:
            if len(e.args) != 1:
                self.refuse(f"{name}() with {len(e.args)} arguments", e)
        elif name == "size":
            if len(e.args) not in (1, 2) or ranks[0] < 1 or (len(e.args) == 2 and not isinstance(e.args[1], Num)):
                self.refuse("size() of anything but an array and a literal dimension", e)
        elif name in ("maxval", "minval", "sum", "product"):
            if len(e.args) != 1 or ranks[0] < 1:
                self.refuse(f"{name}() of anything but a whole array", e)
        elif name in ("matmul", "dot_product"):
            ts = [self.types[id(a)] for a in e.args]
            ok = len(ts) == 2 and ts[0].kind == ts[1].kind and ts[0].kind in ("real", "real32", "int")
            if name == "dot_product":
                ok = ok and ts[0].rank == 1 and ts[1].rank == 1
            else:
                ok = ok and ts[0].rank in (1, 2) and ts[1].rank in (1, 2)
            if not ok:
                self.refuse(f"{name}() of anything but two real or two int arrays of the right rank", e)
        elif any(r != 0 for r in ranks):
            self.refuse(f"{name}() of an array", e)
        if name in ("mod", "min", "max", "int") and any(self.types[id(a)].kind in ("logical",) for a in e.args):
            self.refuse(f"{name}() of a logical", e)

    # -- the generated text -----------------------------------------------------------------------------------------
    def emit(self, text):
        self.lines.append("    " * self.depth + text)

    def tmp(self, ctype_, expr):
        self.n += 1
        name = f"t{self.n}"
        self.emit(f"{ctype_} {name} = {expr};")
        return name

    def tmp_array(self, kind, rank):
        """A new array temporary of the given kind and rank (allocated by the caller), freed after the statement."""
        self.n += 1
        name = f"t{self.n}"
        self.emit(f"rt_{kind}_arr {name} = {{0}};")
        self.temps.append((kind, name))
        return name

    def cast(self, atom, kind, to):
        if kind == to or to is None:
            return atom
        if to == "real" and kind in ("int", "int32", "real32"):
            return f"((double){atom})"
        if to == "real32" and kind in ("int", "int32"):         # through a double, as the interpreter does (not int64 to float)
            return f"((float)(double){atom})"
        if to == "real32" and kind == "real":                    # a literal next to a real32, or a real stored in one: checked
            return f"rt_r32({atom}, {self.line})"
        if to == "int" and kind == "int32":
            return f"((int64_t){atom})"
        if to == "int32" and kind == "int":                    # only a literal next to an int32 (the checker says so)
            return f"((int32_t){atom})"
        raise NotCompiled(f"a conversion from {kind} to {to}")

    def narrow(self, atom, kind, to, line, what=""):
        """A whole number stored where an int32 is wanted is checked against its value."""
        if kind == "int" and to == "int32":
            if what.startswith("line "):          # the interpreter's own message already starts with the line
                what = ""
            return f"rt_narrow32({atom}, {line}, {c_string(what)})"
        return self.cast(atom, kind, to)

    # -- values ---------------------------------------------------------------------------------------------------------
    def value(self, e, exact=False):
        """The value of `e` (after the conversion the checker recorded for it). `exact`: a whole number that the checker
        converts to real32 stays a whole number, because the interpreter works `int op real32` out in double precision
        and rounds once (16777217 + 0.5 is 16777218.0, not 16777216.0)."""
        v = self.raw(e)
        target = self.conv.get(id(e))
        if exact and target is not None and target.kind == "real32" and v.t.kind in ("int", "int32"):
            return v
        if target is not None and target.kind != v.t.kind:
            if v.t.rank == 0:
                return V(Type(target.kind), self.cast(v.atom, v.t.kind, target.kind))
            return V(Type(target.kind, v.t.rank), v.atom, v.src or v.t.kind)
        return v

    def scalar(self, e, to=None, what=""):
        """The scalar value of `e` as a C atom, converted to the kind `to` (a whole number into an int32 is checked)."""
        v = self.value(e)
        if v.t.rank != 0:
            raise NotCompiled("an array where a single value is needed", self.line)
        if to is not None and v.t.kind != to:
            return self.narrow(v.atom, v.t.kind, to, self.line, what)
        return v.atom

    def elem(self, v, k):
        """Element `k` of the array value `v` as a C expression, in the kind the program wants."""
        stored = v.src or v.t.kind
        return self.cast(f"{v.atom}.data[{k}]", stored, v.t.kind)

    def raw(self, e):
        t = type(e)
        if t is Num:
            if isinstance(e.value, float):
                return V(Type("real"), repr(e.value))
            return V(Type("int"), f"INT64_C({e.value})")
        if t is Bool:
            return V(Type("logical"), "true" if e.value else "false")
        if t is Var:
            if e.name == "pi" and "pi" not in self.vars:         # the language's own, until a program declares one
                return V(Type("real"), "3.141592653589793")
            return V(self.vars[e.name][0], self.vars[e.name][1])
        if t is Unary:
            return self.unary(e)
        if t is Binary:
            return self.binary(e)
        if t is Apply:
            return self.apply(e)
        if t in (ArrayLit, MatrixLit):
            return self.array_literal(e)
        raise NotCompiled(f"a {t.__name__}", getattr(e, "line", None))

    # -- operators ------------------------------------------------------------------------------------------------------
    def scalar_op(self, op, kind, la, ra, flags, line):
        """The C expression for `la op ra` on two single values of the same `kind` (int, int32 or real)."""
        if kind == "int":
            call = {"+": "rt_add", "-": "rt_sub", "*": "rt_mul", "/": "rt_div", "**": "rt_ipow"}[op]
            return f"{call}({la}, {ra}, {line})"
        if kind == "int32":
            if op == "**":
                raise NotCompiled("a power of int32 values", line)
            call = {"+": "rt_add32", "-": "rt_sub32", "*": "rt_mul32", "/": "rt_div32"}[op]
            return f"{call}({la}, {ra}, {line})"
        call = {"+": "rt_fadd", "-": "rt_fsub", "*": "rt_fmul", "/": "rt_fdiv", "**": "rt_fpow"}[op]
        return f"{call}({la}, {ra}, {flags}, {line})"

    def unary(self, e):
        v = self.value(e.operand)
        line = self.line
        kind = v.t.kind
        if v.t.rank == 0:
            if e.op == "not":
                return V(Type("logical"), self.tmp("bool", f"!{v.atom}"))
            if e.op == "+":
                return v
            call = {"int": f"rt_neg({v.atom}, {line})", "int32": f"rt_neg32({v.atom}, {line})", "real": f"-{v.atom}",
                    "real32": f"-{v.atom}"}[kind]
            return V(Type(kind), self.tmp(CSCALAR[kind], call))
        out_kind = "logical" if e.op == "not" else kind
        res = self.array_like(v, out_kind)
        k = self.loop_var()
        x = self.elem(v, k)
        expr = {("not", "logical"): f"!{x}", ("+", kind): x, ("-", "real"): f"-{x}", ("-", "real32"): f"-{x}",
                ("-", "int"): f"rt_neg({x}, {line})", ("-", "int32"): f"rt_neg32({x}, {line})"}[(e.op, kind)]
        self.emit(f"for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {expr};")
        return V(Type(out_kind, v.t.rank), res)

    def loop_var(self):
        self.n += 1
        return f"k{self.n}"

    def array_like(self, v, kind):
        """A new temporary array of `kind` with the shape of the array value `v`."""
        res = self.tmp_array(kind, v.t.rank)
        self.emit(f"rt_like_{kind}(&{res}, {v.atom}.shape, {v.atom}.rank, {self.line});")
        return res

    def binary(self, e):
        op, line = e.op, self.line
        flags = (1 if self.types[id(e.left)].kind == "int" else 0) | (2 if self.types[id(e.right)].kind == "int" else 0)
        lv, rv = self.value(e.left, exact=True), self.value(e.right, exact=True)
        compare = op in ("==", "/=", "<", "<=", ">", ">=")
        logic = op in ("and", "or")
        if lv.t.rank == 0 and rv.t.rank == 0:
            la, ra = lv.atom, rv.atom
            if logic:                                    # no short circuit: both sides are already computed
                return V(Type("logical"), self.tmp("bool", f"({la} {'&&' if op == 'and' else '||'} {ra})"))
            if compare:
                if lv.t.kind != rv.t.kind:                # whole numbers next to reals compare as reals
                    kind = "real" if {"real", "real32"} & {lv.t.kind, rv.t.kind} else "int"
                    la, ra = self.cast(la, lv.t.kind, kind), self.cast(ra, rv.t.kind, kind)
                return V(Type("logical"), self.tmp("bool", f"({la} {'!=' if op == '/=' else op} {ra})"))
            kind = lv.t.kind if lv.t.kind == rv.t.kind else self.widest(lv.t.kind, rv.t.kind)
            return V(Type(kind), self.tmp(CSCALAR[kind], self.arith(op, kind, lv.t.kind, la, rv.t.kind, ra, flags, line)))
        # whole arrays, element by element: the shapes must agree, then each operation runs over every element
        arrays = [v for v in (lv, rv) if v.t.rank > 0]
        if len(arrays) == 2:
            self.emit(f"rt_same_shape({lv.atom}.shape, {lv.atom}.rank, {rv.atom}.shape, {rv.atom}.rank, 0, {line});")
        shape_of = arrays[0]
        if logic or compare:
            kind = "logical"
        else:
            kind = lv.t.kind if lv.t.kind == rv.t.kind else self.widest(lv.t.kind, rv.t.kind)
        res = self.array_like(shape_of, kind)
        k = self.loop_var()
        la = self.elem(lv, k) if lv.t.rank > 0 else lv.atom
        ra = self.elem(rv, k) if rv.t.rank > 0 else rv.atom
        if logic:
            expr = f"({la} {'&&' if op == 'and' else '||'} {ra})"
        elif compare:
            lk, rk = lv.t.kind, rv.t.kind
            if lk != rk:
                common = "real" if {"real", "real32"} & {lk, rk} else "int"
                la, ra = self.cast(la, lk, common), self.cast(ra, rk, common)
            expr = f"({la} {'!=' if op == '/=' else op} {ra})"
        else:
            expr = self.arith(op, kind, lv.t.kind, la, rv.t.kind, ra, flags, line)
        self.emit(f"for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {expr};")
        return V(Type(kind, shape_of.t.rank), res)

    def arith(self, op, kind, lk, la, rk, ra, flags, line):
        """The C expression for `la op ra` where the operands are of kinds `lk` and `rk` and the answer is of `kind`."""
        if kind != "real32":
            return self.scalar_op(op, kind, self.cast(la, lk, kind), self.cast(ra, rk, kind), flags, line)
        if op == "**":                                       # a real32 base, to a whole number or to a real32
            if lk != "real32":
                raise NotCompiled("a real32 power whose base is not a real32", line)
            if rk in ("int", "int32"):
                return f"rt_spow_i({la}, (int64_t){ra}, {line})"
            return f"rt_spow_f({la}, {ra}, {line})"
        # worked out in double precision (exact for two singles, as is a whole number), then rounded to single once
        d = self.scalar_op(op, "real", self.cast(la, lk, "real"), self.cast(ra, rk, "real"), flags, line)
        return f"rt_r32({d}, {line})"

    @staticmethod
    def widest(a, b):
        order = ["int32", "int", "real32", "real"]
        return order[max(order.index(a), order.index(b))]

    # -- array literals and elements ----------------------------------------------------------------------------------
    def array_literal(self, e):
        rank = 1 if isinstance(e, ArrayLit) else 2
        kind = self.types[id(e)].kind
        if isinstance(e, ArrayLit):
            items, rows, cols = e.items, len(e.items), 1
        else:
            items, rows, cols = [x for row in e.rows for x in row], len(e.rows), len(e.rows[0])
        res = self.tmp_array(kind, rank)
        shape = f"{rows}, {cols}, 1" if rank == 2 else f"{rows}, 1, 1"
        self.emit(f"rt_alloc_{kind}(&{res}, {rank}, {shape}, {self.line});")
        values = [self.scalar(x, kind) for x in items]             # in order, then stored
        for n, atom in enumerate(values):
            pos = n if rank == 1 else (n // cols) + (n % cols) * rows
            self.emit(f"{res}.data[{pos}] = {atom};")
        return V(Type(kind, rank), res)

    def subscripts(self, e, arr):
        """The offset of element `e` of the array `arr` (a C lvalue of an array struct), with every subscript checked
        as soon as it is evaluated."""
        rank = self.vars[e.name][0].rank
        idx = []
        for d, a in enumerate(e.args):
            i = self.tmp("int64_t", self.scalar(a, "int"))
            self.emit(f"rt_check_index({i}, {arr}.shape[{d}], {d + 1}, {rank}, {self.line});")
            idx.append(i)
        off = f"({idx[-1]} - 1)"
        for d in range(rank - 2, -1, -1):
            off = f"(({idx[d]} - 1) + {arr}.shape[{d}] * {off})"
        return self.tmp("int64_t", off)

    def element(self, e):
        t = self.vars[e.name][0]
        arr = self.vars[e.name][1]
        off = self.subscripts(e, arr)
        return V(Type(t.kind), self.tmp(CSCALAR[t.kind], f"{arr}.data[{off}]"))

    # -- calls and built-in functions -----------------------------------------------------------------------------------
    def call_args(self, f, args):
        """The C arguments of a call of `f`: values for `in`, addresses for an `inout` single value, arrays as they are."""
        out = []
        for a, (typ, pname, intent) in zip(args, f.params):
            pt = self.param_type(typ)
            if intent == "inout" and pt.rank == 0:
                out.append("&" + self.lvalue(a))
            elif pt.rank == 0:
                out.append(self.scalar(a, pt.kind, f"argument '{pname}'"))
            else:
                v = self.value(a)
                out.append(v.atom)
        return out

    def lvalue(self, target):
        """A C lvalue for a variable or an array element (evaluating and checking its subscripts first)."""
        if isinstance(target, Var):
            return self.vars[target.name][1]
        arr = self.vars[target.name][1]
        return f"{arr}.data[{self.subscripts(target, arr)}]"

    def apply(self, e):
        line = self.line
        if e.bracket:
            return self.element(e)
        if e.name in self.unit.functions:
            f = self.unit.functions[e.name]
            if len(e.args) != len(f.params):
                raise NotCompiled(f"a call of '{e.name}' with optional or missing arguments", line)
            args = self.call_args(f, e.args)
            self.emit(f'rt_call_in({line}, "{e.name}");')
            rt = self.param_type(f.ret)
            result = self.tmp(CSCALAR[rt.kind], f"{f_name(e.name)}({', '.join(args)})")
            self.emit("rt_call_out();")
            return V(rt, result)
        return self.builtin(e)

    def elemental(self, name, x, kind, was_int, line):
        """The C expression of the one-argument function `name` of the single value `x` (of `kind`), and its result kind."""
        if name == "abs":
            if kind == "int":
                return "int", f"rt_abs({x}, {line})"
            if kind == "int32":
                return "int32", f"rt_abs32({x}, {line})"
            if kind == "real32":
                return "real32", f"fabsf({x})"
            return "real", f"fabs({x})"
        if name == "real32":
            return "real32", self.cast(x, kind, "real32")
        r = self.cast(x, kind, "real")
        if name == "real":
            return "real", r
        if kind == "real32":                                 # single precision: sqrt is exact in double then rounded; the
            if name == "sqrt":                               # rest are the C library's sinf, expf, ... (as the interpreter's)
                return "real32", f"((float)rt_sqrt({r}, 0, {line}))"
            if name in ("log", "log10"):
                return "real32", f'rt_logf({x}, {SINGLE_LIBM[name]}, "{name}", {line})'
            return "real32", f'rt_math1f("{name}", {SINGLE_LIBM[name]}({x}), {x}, {line})'
        if name == "sqrt":
            return "real", f"rt_sqrt({r}, {was_int}, {line})"
        if name == "exp":
            return "real", f"rt_exp({r}, {was_int}, {line})"
        if name in ("log", "log10"):
            return "real", f'rt_log({r}, {name}, "{name}", {was_int}, {line})'
        return "real", f'rt_math1("{name}", {name}({r}), {r}, {was_int}, {line})'

    def builtin(self, e):
        line, name = self.line, e.name
        vals = [self.value(a, exact=name in ("mod", "min", "max")) for a in e.args]
        if name in ELEMENTAL1:
            v = vals[0]
            if v.t.rank == 0:
                kind, expr = self.elemental(name, v.atom, v.t.kind, 1 if v.t.kind in ("int", "int32") else 0, line)
                return V(Type(kind), self.tmp(CSCALAR[kind], expr))
            kind_out = self.types[id(e)].kind
            res = self.array_like(v, kind_out)
            k = self.loop_var()
            x = self.elem(v, k)
            _, expr = self.elemental(name, x, v.t.kind, 1 if v.t.kind in ("int", "int32") else 0, line)
            self.emit(f"char sh{k}[96]; rt_set_shown(sh{k}, {v.atom}.shape, {v.atom}.rank);   /* messages name the whole array */")
            self.emit(f"for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {expr};")
            self.emit("rt_shown_override = NULL;")
            return V(Type(kind_out, v.t.rank), res)
        if name == "size":
            a = vals[0]
            if len(vals) == 1:
                return V(Type("int"), self.tmp("int64_t", f"{a.atom}.n"))
            d = e.args[1].value
            if not isinstance(d, int) or not 1 <= d <= a.t.rank:
                raise NotCompiled(f"size() with the dimension {d}", line)
            return V(Type("int"), self.tmp("int64_t", f"{a.atom}.shape[{d - 1}]"))
        if name in ("maxval", "minval", "sum", "product"):
            return self.reduction(name, vals[0], line)
        if name in ("matmul", "dot_product"):
            return self.products(name, vals[0], vals[1], line)
        kinds = [v.t.kind for v in vals]
        atoms = [v.atom for v in vals]
        was = [1 if k in ("int", "int32") else 0 for k in kinds]
        if name == "mod" and len(atoms) == 2:
            if kinds == ["int", "int"]:
                return V(Type("int"), self.tmp("int64_t", f"rt_mod({atoms[0]}, {atoms[1]}, {line})"))
            if kinds == ["int32", "int32"]:
                return V(Type("int32"), self.tmp("int32_t", f"rt_mod32({atoms[0]}, {atoms[1]}, {line})"))
            if all(k in ("int", "int32") for k in kinds):
                a, b = (self.cast(atoms[i], kinds[i], "int") for i in (0, 1))
                return V(Type("int"), self.tmp("int64_t", f"rt_mod({a}, {b}, {line})"))
            a, b = (self.cast(atoms[i], kinds[i], "real") for i in (0, 1))
            m = f"rt_fmod({a}, {b}, {was[0]}, {was[1]}, {line})"
            if self.types[id(e)].kind == "real32":             # the remainder of two singles is a single (exactly)
                return V(Type("real32"), self.tmp("float", f"rt_r32({m}, {line})"))
            return V(Type("real"), self.tmp("double", m))
        if name in ("min", "max") and len(atoms) >= 2:
            kind = self.types[id(e)].kind
            op = '>' if name == 'max' else '<'
            if kind == "real32" and any(k != "real32" for k in kinds):     # compared as the interpreter does: in double
                xs = [self.cast(a, k, "real") for a, k in zip(atoms, kinds)]
                best = self.tmp("double", xs[0])
                for x in xs[1:]:
                    self.emit(f"if ({x} {op} {best}) {best} = {x};")
                return V(Type(kind), self.tmp("float", f"rt_r32({best}, {line})"))
            xs = [self.cast(a, k, kind) for a, k in zip(atoms, kinds)]
            best = self.tmp(CSCALAR[kind], xs[0])
            for x in xs[1:]:
                self.emit(f"if ({x} {op} {best}) {best} = {x};")
            return V(Type(kind), best)
        if name == "int" and len(atoms) == 1:
            if kinds[0] in ("int", "int32"):
                return V(Type("int"), self.cast(atoms[0], kinds[0], "int"))
            return V(Type("int"), self.tmp("int64_t", f"rt_trunc_int({self.cast(atoms[0], kinds[0], 'real')}, {line})"))
        raise NotCompiled(f"the function '{name}' with {len(atoms)} arguments", line)

    def reduction(self, name, a, line):
        kind = a.t.kind
        k = self.loop_var()
        ct = CSCALAR[kind]
        x = self.elem(a, k)
        if name in ("maxval", "minval"):
            self.emit(f'if ({a.atom}.n == 0) rt_fail({line}, "{name}(): the array is empty");')
            best = self.tmp(ct, f"{a.atom}.data[0]")
            self.emit(f"for (int64_t {k} = 1; {k} < {a.atom}.n; {k}++) if ({x} {'>' if name == 'maxval' else '<'} {best}) {best} = {x};")
            return V(Type(kind), best)
        if kind in ("real", "real32"):                      # plain multiplication or addition, one at a time from the left
            op = '*' if name == 'product' else '+'
            if kind == "real32":                             # every step rounded to single, as in Fortran
                acc = self.tmp("float", "1.0f" if name == "product" else "0.0f")
                self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) {acc} = rt_r32((double){acc} {op} (double){x}, {line});")
                return V(Type(kind), acc)
            acc = self.tmp("double", "1.0" if name == "product" else "0.0")
            self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) {acc} = {acc} {op} {x};")
            return V(Type(kind), acc)
        wide = self.tmp("rt_i128", "1" if name == "product" else "0")   # exact, then checked once, like the interpreter
        op = "*" if name == "product" else "+"
        self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) {wide} = {wide} {op} (rt_i128){x};")
        limit = "INT64_MIN, INT64_MAX" if kind == "int" else "INT32_MIN, INT32_MAX"
        self.emit(f'rt_check_range({wide}, {limit}, "{name}", {1 if kind == "int" else 32}, {line});')
        return V(Type(kind), self.tmp(ct, f"({ct}){wide}"))

    def products(self, name, a, b, line):
        """dot_product and matmul of two real arrays (added one at a time from the left, as the interpreter does) or of two
        int arrays (worked out exactly, checked once per element)."""
        kind = a.t.kind
        ct = CSCALAR[kind]
        if name == "dot_product":
            self.emit(f"rt_dot_shapes({a.atom}.shape, {b.atom}.shape, {line});")
            k = self.loop_var()
            if kind == "real32":
                acc = self.tmp("float", "0.0f")
                self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) {acc} = rt_r32((double){acc} + (double)rt_r32((double){a.atom}.data[{k}] * (double){b.atom}.data[{k}], {line}), {line});")
                return V(Type(kind), acc)
            if kind == "real":
                acc = self.tmp("double", "0.0")
                self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) {acc} = {acc} + {a.atom}.data[{k}] * {b.atom}.data[{k}];")
                return V(Type(kind), acc)
            wide = self.tmp("rt_i128", "0")
            self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) {wide} = {wide} + (rt_i128){a.atom}.data[{k}] * {b.atom}.data[{k}];")
            self.emit(f'rt_check_range({wide}, INT64_MIN, INT64_MAX, "dot_product", 1, {line});')
            return V(Type(kind), self.tmp(ct, f"({ct}){wide}"))
        n, kk, m = (self.tmp("int64_t", x) for x in (
            f"({a.atom}.rank == 2 ? {a.atom}.shape[0] : 1)",
            f"({a.atom}.rank == 2 ? {a.atom}.shape[1] : {a.atom}.shape[0])",
            f"({b.atom}.rank == 2 ? {b.atom}.shape[1] : 1)"))
        self.emit(f"rt_matmul_check({kk}, ({b.atom}.rank == 2 ? {b.atom}.shape[0] : {b.atom}.shape[0]), {line});")
        rank = 2 if a.t.rank == 2 and b.t.rank == 2 else 1
        res = self.tmp_array(kind, rank)
        shape = (f"{n}, {m}, 1" if rank == 2 else (f"{n}, 1, 1" if a.t.rank == 2 else f"{m}, 1, 1"))
        self.emit(f"rt_alloc_{kind}(&{res}, {rank}, {shape}, {line});")
        i, j, t = self.loop_var(), self.loop_var(), self.loop_var()
        self.emit(f"for (int64_t {j} = 0; {j} < {m}; {j}++) for (int64_t {i} = 0; {i} < {n}; {i}++) {{")
        self.depth += 1
        term = f"{a.atom}.data[{i} + {t} * {n}] * {b.atom}.data[{t} + {j} * {kk}]"
        if kind == "real32":
            self.emit("float acc = 0.0f;")
            self.emit(f"for (int64_t {t} = 0; {t} < {kk}; {t}++) acc = rt_r32((double)acc + (double)rt_r32((double){a.atom}.data[{i} + {t} * {n}] * (double){b.atom}.data[{t} + {j} * {kk}], {line}), {line});")
            self.emit(f"{res}.data[{i} + {j} * {n}] = acc;")
        elif kind == "real":
            self.emit("double acc = 0.0;")
            self.emit(f"for (int64_t {t} = 0; {t} < {kk}; {t}++) acc = acc + {term};")
            self.emit(f"{res}.data[{i} + {j} * {n}] = acc;")
        else:
            self.emit("rt_i128 acc = 0;")
            self.emit(f"for (int64_t {t} = 0; {t} < {kk}; {t}++) acc = acc + (rt_i128){a.atom}.data[{i} + {t} * {n}] * {b.atom}.data[{t} + {j} * {kk}];")
            self.emit(f'rt_check_range(acc, INT64_MIN, INT64_MAX, "matmul", 1, {line});')
            self.emit(f"{res}.data[{i} + {j} * {n}] = (int64_t)acc;")
        self.depth -= 1
        self.emit("}")
        return V(Type(kind, rank), res)

    # -- statements ---------------------------------------------------------------------------------------------------
    def block(self, body):
        for s in body:
            if isinstance(s, list):
                self.block(s)
            else:
                self.statement(s)

    def statement(self, s):
        self.line = getattr(s, "line", 0) or self.line
        kind = type(s)
        if kind is Comment:
            return
        self.emit("{")
        self.depth += 1
        saved, self.temps = self.temps, []
        getattr(self, "st_" + kind.__name__.lower())(s)
        for k, name in self.temps:
            self.emit(f"rt_free_{k}(&{name});")
        self.temps = saved
        self.depth -= 1
        self.emit("}")

    def copy_into(self, dst, dst_kind, src, line, what):
        """Store the array value `src` in the array variable `dst`, element by element (shapes already agree)."""
        k = self.loop_var()
        x = self.narrow(self.elem(src, k), src.t.kind, dst_kind, line, what)
        self.emit(f"for (int64_t {k} = 0; {k} < {dst}.n; {k}++) {dst}.data[{k}] = {x};")

    def st_decl(self, s):
        t = self.vars[s.name][0]
        name = c_name(s.name)
        line = self.line
        if t.rank == 0:
            if s.init is not None:
                self.emit(f"{name} = {self.scalar(s.init, t.kind, f'line {line}')};")
            else:
                self.emit(f"{name} = {'false' if t.kind == 'logical' else '0'};")
            return
        dims = [self.scalar(d, "int") for d in s.dims]
        dims = [self.tmp("int64_t", d) for d in dims]
        padded = dims + ["1"] * (3 - len(dims))
        self.emit(f"rt_alloc_{t.kind}(&{name}, {t.rank}, {', '.join(padded)}, {line});")
        if s.init is not None:
            v = self.value(s.init)
            if v.t.rank == 0:                                           # one value in every element
                atom = self.narrow(v.atom, v.t.kind, t.kind, line, f"line {line}")
                k = self.loop_var()
                self.emit(f"for (int64_t {k} = 0; {k} < {name}.n; {k}++) {name}.data[{k}] = {atom};")
            else:
                self.emit(f'rt_init_shape_error({v.atom}.shape, {v.atom}.rank, {name}.shape, {name}.rank, "{s.name}", {line});')
                self.copy_into(name, t.kind, v, line, f"line {line}")

    def st_assign(self, s):
        line = self.line
        target = s.target
        t = self.vars[target.name][0]
        if isinstance(target, Apply):                                   # an element: its subscripts first
            lv = self.lvalue(target)
            self.emit(f"{lv} = {self.scalar(s.value, t.kind, f'line {line}')};")
            return
        name = self.vars[target.name][1]
        if t.rank == 0:
            self.emit(f"{name} = {self.scalar(s.value, t.kind, f'line {line}')};")
            return
        v = self.value(s.value)
        if v.t.rank == 0:
            atom = self.narrow(v.atom, v.t.kind, t.kind, line, f"line {line}")
            k = self.loop_var()
            self.emit(f"for (int64_t {k} = 0; {k} < {name}.n; {k}++) {name}.data[{k}] = {atom};")
            return
        self.emit(f"rt_same_shape({name}.shape, {name}.rank, {v.atom}.shape, {v.atom}.rank, 1, {line});")
        self.copy_into(name, t.kind, v, line, f"line {line}")

    def st_if(self, s):
        self.if_chain(s.branches, s.orelse)

    def if_chain(self, branches, orelse):
        (cond, body), rest = branches[0], branches[1:]
        c = self.scalar(cond, "logical")
        self.emit(f"if ({c}) {{")
        self.depth += 1
        self.block(body)
        self.depth -= 1
        if rest or orelse:
            self.emit("} else {")
            self.depth += 1
            if rest:
                self.emit("{")
                self.depth += 1
                self.line = getattr(rest[0][0], "line", 0) or self.line
                self.if_chain(rest, orelse)
                self.depth -= 1
                self.emit("}")
            else:
                self.block(orelse)
            self.depth -= 1
        self.emit("}")

    def new_loop(self, s):
        self.n += 1
        entry = (s.label, self.n)
        self.loops.append(entry)
        return entry

    def st_do(self, s):
        t = self.vars[s.var][0]
        var, line = self.vars[s.var][1], self.line
        if t.kind not in ("int", "int32") or t.rank != 0:
            raise NotCompiled("a do loop whose variable is not a whole number", line)
        for part in (s.lo, s.hi, s.step):
            if part is not None and self.types[id(part)].kind not in ("int", "int32"):
                raise NotCompiled("a do loop with bounds that are not whole numbers", line)
        lo = self.scalar(s.lo, "int")
        hi = self.scalar(s.hi, "int")
        step = self.scalar(s.step, "int") if s.step is not None else "INT64_C(1)"
        label, ident = self.new_loop(s)
        self.emit(f"int64_t lo{ident} = {lo}, hi{ident} = {hi}, st{ident} = {step};")
        self.emit(f"rt_check_step(st{ident}, {line});")
        self.emit(f"int64_t i{ident} = lo{ident};")
        self.emit(f"while (st{ident} > 0 ? i{ident} <= hi{ident} : i{ident} >= hi{ident}) {{")
        self.depth += 1
        self.emit(f"{var} = {self.narrow(f'i{ident}', 'int', t.kind, line, f'line {line}')};")
        self.block(s.body)
        self.emit(f"cycle{ident}: ;")
        self.emit(f"i{ident} = i{ident} + st{ident};")
        self.depth -= 1
        self.emit("}")
        self.emit(f"{var} = {self.narrow(f'i{ident}', 'int', t.kind, line, f'line {line}')};")    # one step past the range
        self.emit(f"exit{ident}: ;")
        self.loops.pop()

    def st_dowhile(self, s):
        label, ident = self.new_loop(s)
        self.emit("for (;;) {")
        self.depth += 1
        self.emit("{")
        self.depth += 1
        c = self.scalar(s.cond, "logical")
        self.emit(f"if (!{c}) goto exit{ident};")
        self.depth -= 1
        self.emit("}")
        self.block(s.body)
        self.emit(f"cycle{ident}: ;")
        self.depth -= 1
        self.emit("}")
        self.emit(f"exit{ident}: ;")
        self.loops.pop()

    def loop_for(self, label, what, line):
        if not self.loops:
            raise NotCompiled(f"'{what}' outside a loop", line)
        if label is None:
            return self.loops[-1][1]
        for name, ident in reversed(self.loops):
            if name == label:
                return ident
        raise NotCompiled(f"'{what} {label}'", line)

    def st_exit(self, s):
        self.emit(f"goto exit{self.loop_for(s.label, 'exit', s.line)};")

    def st_cycle(self, s):
        self.emit(f"goto cycle{self.loop_for(s.label, 'cycle', s.line)};")

    def st_return(self, s):
        if self.func is not None and self.func.ret is not None:
            rt = self.param_type(self.func.ret)
            self.emit(f"ret = {self.scalar(s.value, rt.kind, f'function {self.func.name}')};")
        self.emit("goto fn_end;")

    def st_stop(self, s):
        if s.code is None:
            self.emit("rt_stop(0);")
        else:
            self.emit(f"rt_stop((int){self.scalar(s.code, 'int')});")

    def st_print(self, s):
        parts = []
        for a in s.args:
            if isinstance(a, Str):
                parts.append(("string", 0, c_string(a.value)))
            else:
                v = self.value(a)
                parts.append((v.t.kind, v.t.rank, v.atom if v.t.rank == 0 else v))
        for i, (kind, rank, atom) in enumerate(parts):
            if i:
                self.emit("rt_write_sep();")
            if rank == 0:
                self.emit(f"{WRITERS[kind]}({atom});")
            elif atom.src is None or atom.src == kind:
                self.emit(f"rt_write_{kind}_arr(&{atom.atom});")
            else:                                                     # stored as another kind: convert while writing
                tmp = self.tmp_array(kind, rank)
                self.emit(f"rt_like_{kind}(&{tmp}, {atom.atom}.shape, {atom.atom}.rank, {self.line});")
                k = self.loop_var()
                self.emit(f"for (int64_t {k} = 0; {k} < {tmp}.n; {k}++) {tmp}.data[{k}] = {self.elem(atom, k)};")
                self.emit(f"rt_write_{kind}_arr(&{tmp});")
        if s.newline:
            self.emit("rt_write_nl();")

    def st_call(self, s):
        f = self.unit.functions[s.name]
        if f.ret is not None:
            raise NotCompiled(f"'{s.name}' is a function; use it in an expression", s.line)
        if len(s.args) != len(f.params):
            raise NotCompiled(f"a call of '{s.name}' with optional or missing arguments", s.line)
        args = self.call_args(f, s.args)
        self.emit(f'rt_call_in({s.line}, "{s.name}");')
        self.emit(f"{f_name(s.name)}({', '.join(args)});")
        self.emit("rt_call_out();")

    # -- whole functions -------------------------------------------------------------------------------------------
    def declare_vars(self, params, body):
        self.vars = {}
        for typ, name, intent in params:
            t = self.param_type(typ)
            access = c_name(name) if (intent == "in" or t.rank > 0) else f"(*{c_name(name)})"
            self.vars[name] = (t, access)
        seen = set()
        for d in statements_in(body):
            if isinstance(d, Decl) and d.name not in seen:
                seen.add(d.name)
                self.vars[d.name] = (self.decl_type(d), c_name(d.name))

    def c_params(self, f):
        out = []
        for typ, name, intent in f.params:
            t = self.param_type(typ)
            if t.rank > 0:
                out.append(f"rt_{t.kind}_arr {c_name(name)}")
            elif intent == "inout":
                out.append(f"{CSCALAR[t.kind]} *{c_name(name)}")
            else:
                out.append(f"{CSCALAR[t.kind]} {c_name(name)}")
        return ", ".join(out) or "void"

    def local_declarations(self, params, body):
        declared = {name for _, name, _ in params}
        seen = set()
        for d in statements_in(body):
            if isinstance(d, Decl) and d.name not in seen and d.name not in declared:
                seen.add(d.name)
                t = self.vars[d.name][0]
                if t.rank == 0:
                    self.emit(f"{CSCALAR[t.kind]} {c_name(d.name)};")
                else:
                    self.emit(f"rt_{t.kind}_arr {c_name(d.name)} = {{0}};")
                    self.arrays.append((t.kind, c_name(d.name)))

    def function(self, f):
        self.func = f
        self.declare_vars(f.params, f.body)
        rt = self.param_type(f.ret) if f.ret else None
        head = f"static {CSCALAR[rt.kind] if rt else 'void'} {f_name(f.name)}({self.c_params(f)})"
        self.lines, self.depth, self.line, self.arrays = [], 1, 0, []
        if rt:
            self.emit(f"{CSCALAR[rt.kind]} ret = {'false' if rt.kind == 'logical' else '0'};")
        self.local_declarations(f.params, f.body)
        self.block(f.body)
        if f.ret is not None:
            self.emit(f'rt_fail(0, "function {f.name} must return a {self.param_type(f.ret).kind}");')
        self.emit("fn_end: ;")
        for k, name in self.arrays:
            self.emit(f"rt_free_{k}(&{name});")
        self.emit("return ret;" if rt else "return;")
        return head + ";", "\n".join([head + " {"] + self.lines + ["}"])

    def program(self):
        self.check()
        protos, defs = [], []
        for f in self.unit.functions.values():
            proto, text = self.function(f)
            protos.append(proto)
            defs.append(text)
        self.func = None
        prog = self.unit.program
        self.lines, self.depth, self.line, self.arrays = [], 1, 0, []
        self.declare_vars([], prog.body)
        self.local_declarations([], prog.body)
        self.block(prog.body)
        self.emit("fn_end: ;")
        self.emit("fflush(stdout);")
        self.emit("return 0;")
        main = ["int main(void) {"] + self.lines + ["}"]
        runtime = open(os.path.join(HERE, "cruntime.h")).read()
        flags = []
        if self.release:
            flags.append("#define BOREAS_RELEASE 1")
        if self.unit.ieee:
            flags.append("#define BOREAS_IEEE 1")
        return "\n".join(["/* generated by `boreas emit-c`: do not edit */"] + flags + [runtime] + protos + [""] + defs + [""] + main) + "\n"


def compile_unit(unit, interp, release=False):
    return Compiler(unit, interp, release).program()


def find_compiler():
    for name in (os.environ.get("CC"), "cc", "clang", "gcc"):
        if name and shutil.which(name):
            return name
    return None


def build(src_c, output, release=False, keep=False):
    """Compile the C text to the executable `output`; returns None, or the compiler's complaint."""
    cc = find_compiler()
    if cc is None:
        return "no C compiler found (set CC, or install cc, clang or gcc)"
    # -fno-builtin-sinf ...: the single-precision library functions must run at run time, as the interpreter's do. A compiler
    # that knows sinf may work a constant argument out itself, correctly rounded, where the C library is a bit off now and then.
    flags = ["-O2", "-std=gnu11", "-ffp-contract=off", "-fwrapv", "-w",
             *(f"-fno-builtin-{name}" for name in (*SINGLE_LIBM.values(), "powf"))]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "prog.c")
        with open(path, "w") as f:
            f.write(src_c)
        if keep:
            shutil.copy(path, output + ".c")
        r = subprocess.run([cc, *flags, "-o", output, path, "-lm"], capture_output=True, text=True)
    return None if r.returncode == 0 else r.stderr.strip() or "the C compiler failed"


def install_corpus_dump(interp_class, directory):
    """Development only (BOREAS_COMPILE_CORPUS=<directory>): every program an interpreter runs is also written there as
    source, so that tools/compile_oracle.py can try to compile each one and compare the two."""
    import hashlib
    from .printer import to_source
    os.makedirs(directory, exist_ok=True)
    original = interp_class.run_program

    def run_program(self):
        try:
            text = to_source(self.unit)
            name = hashlib.sha1(text.encode()).hexdigest()[:12] + ".bor"
            path = os.path.join(directory, name)
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write(text)
        except Exception:
            pass
        return original(self)

    interp_class.run_program = run_program
