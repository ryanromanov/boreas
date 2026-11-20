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
import sys
import subprocess
import tempfile
from .parser import (Allocate, Apply, ArrayLit, Assign, Bool, Binary, Call, Comment, Cycle, Deallocate, Decl, Do, DoWhile, Exit, FieldAccess, If, IsType,
                     MatrixLit, NamedArg, Num, Print, Return, Slice, Stop, Str, Unary, Var)
from .tree import statements_in, walk
from .typecheck import infer
from .types import Type

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("int", "int32", "real", "real32", "logical")
CSCALAR = {"int": "int64_t", "int32": "int32_t", "real": "double", "real32": "float", "logical": "bool"}
BUILTINS = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "mod", "min", "max", "real", "real32", "int",
            "size", "maxval", "minval", "sum", "product", "matmul", "dot_product", "allocated"}
ELEMENTAL1 = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "real", "real32", "real64", "asin", "acos", "atan",
              "sinh", "cosh", "tanh", "asinh", "acosh", "atanh", "erf", "erfc", "floor", "ceiling", "nint", "aint", "anint",
              "int32", "int64"}
ELEMENTAL2 = {"atan2", "sign", "modulo", "dim"}
CONSTANTS = {"huge", "epsilon", "tiny"}
ARRAY_FUNCTIONS = {"reshape", "transpose", "flatten", "shape", "merge", "any", "all", "count", "maxloc", "minloc", "cshift"}
BUILTINS |= ELEMENTAL1 | ELEMENTAL2 | CONSTANTS | ARRAY_FUNCTIONS
SINGLE_LIBM = {"sin": "sinf", "cos": "cosf", "tan": "tanf", "exp": "expf", "log": "logf", "log10": "log10f", "asin": "asinf",
               "acos": "acosf", "atan": "atanf", "sinh": "sinhf", "cosh": "coshf", "tanh": "tanhf", "asinh": "asinhf",
               "acosh": "acoshf", "erf": "erff", "erfc": "erfcf"}
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
    return cscalar(t) if t.rank == 0 else f"rt_{t.kind}_arr"


def cscalar(t):
    """The C type of a single value of type `t` (a record is a struct)."""
    return f"r_{t.name}" if t.kind == "record" else CSCALAR[t.kind]


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
        self.records = {}               # the record types the program defines (all of them are handled, or it is refused)
        self.rec_order = []             # their names, fields before the records that hold them

    # -- refusing what is not handled ---------------------------------------------------------------------------
    def refuse(self, what, node=None, line=None):
        raise NotCompiled(what, line or getattr(node, "line", None) or None)

    def kind_ok(self, t):
        """Is `t` a type the compiler handles: a single value or array of a number or logical, or one record?"""
        if t is None or not t.known:
            return False
        if t.kind == "record":
            return t.rank == 0 and not t.optional and t.name in self.records
        return t.kind in KINDS and 0 <= t.rank <= MAX_RANK

    def check_records(self):
        """The records of the program: single numbers and logicals and other records only (no functions, arrays or
        text in them yet). Fills `self.records` and `self.rec_order` (fields before the records that hold them)."""
        todo = dict(self.unit.records)
        for name, r in todo.items():
            if r.functions or r.implements:
                self.refuse(f"record '{name}' with functions", line=r.line)
            for fd in r.fields:
                if fd.dims is not None or fd.allocatable or (fd.type not in KINDS and fd.type not in todo):
                    self.refuse(f"field '{fd.name}' of record '{name}' ({fd.type})", line=fd.line)
                if fd.init is not None:
                    self.field_default(fd, todo)
        visiting = set()

        def visit(name):
            if name in self.records:
                return
            if name in visiting:
                self.refuse(f"record '{name}' inside itself", line=todo[name].line)
            visiting.add(name)
            for fd in todo[name].fields:
                if fd.type in todo:
                    visit(fd.type)
            visiting.discard(name)
            self.records[name] = todo[name]
            self.rec_order.append(name)
        for name in todo:
            visit(name)

    def field_default(self, fd, todo):
        """The C text of the default of field `fd` (a literal of the field's own kind, or a whole number for a real)."""
        e = fd.init
        neg = False
        if isinstance(e, Unary) and e.op == "-" and isinstance(e.operand if hasattr(e, "operand") else None, Num):
            neg, e = True, e.operand
        if isinstance(e, Bool) and fd.type == "logical":
            return "true" if e.value else "false"
        if not isinstance(e, Num) or isinstance(e.value, bool):
            self.refuse(f"the default of field '{fd.name}'", line=fd.line)
        v = -e.value if neg else e.value
        if isinstance(v, int):
            if fd.type == "int" and -2 ** 63 < v < 2 ** 63:
                return f"INT64_C({v})"
            if fd.type == "int32" and -2 ** 31 <= v < 2 ** 31:
                return f"((int32_t){v})"
            if fd.type == "real" and abs(v) < 2 ** 53:
                return f"{float(v)!r}"
            if fd.type == "real32" and abs(v) < 2 ** 24:
                return f"{float(v)!r}f"
        elif fd.type == "real" and v == v and abs(v) < 1e300:
            return repr(v)
        self.refuse(f"the default of field '{fd.name}'", line=fd.line)

    def supported(self, t, what, node=None):
        if t is None or not t.known or t.kind not in KINDS or not 0 <= t.rank <= MAX_RANK:
            self.refuse(what if t is not None and t.known else f"an expression of unknown type", node)

    def check(self):
        u = self.unit
        if u.modules:
            self.refuse("modules")
        if u.interfaces:
            self.refuse("interfaces")
        if u.program is None:
            self.refuse("a file without a program")
        self.check_records()
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
                if intent in ("out", "inout") and f.ret is not None:
                    self.refuse(f"{intent} parameter '{name}' of the function '{f.name}'")
        for f in [*u.functions.values(), None]:
            self.check_body((f.body if f else u.program.body), f)

    def param_type(self, typ):
        """The Type of a parameter or result written `real`, `int[]`, `real[,]`, or None when it is not handled."""
        from .types import parse
        if not isinstance(typ, str):
            return None
        if typ in self.records:
            return Type("record", 0, False, typ)
        t = parse(typ, None)
        if not t.known or t.kind not in KINDS or t.optional or not 0 <= t.rank <= MAX_RANK:
            return None
        return t

    def check_body(self, body, f):
        self.allocs = self.allocs_of(body, f)
        self.skip = {id(x) for d in statements_in(body) if isinstance(d, Decl) and d.allocatable for x in d.dims}
        strings = set()
        fixed = {name for typ, name, intent in f.params if intent == "in"} if f is not None else set()
        fixed |= {d.name for d in statements_in(body) if isinstance(d, Decl) and d.const}
        for s in statements_in(body):
            kind = type(s)
            if kind is Return:
                if f is None or (s.value is None) != (f.ret is None):
                    self.refuse("a return that does not match its procedure", s)
                if f.ret in self.records:
                    self.same_record(s.value, f.ret, s)
                continue
            if kind is Do and s.var in fixed:
                self.refuse(f"a loop over the constant or parameter '{s.var}'", s)
            if kind in (If, Do, DoWhile, Stop, Exit, Cycle, Comment):
                continue
            if kind in (Allocate, Deallocate):
                if s.name not in self.allocs_of(body, f):
                    self.refuse(f"'{s.name}' is not an allocatable array", s)
                continue
            if kind is Decl:
                if f is not None and s.name in {p[1] for p in f.params}:
                    self.refuse(f"a declaration of the parameter '{s.name}'", s)
                t = self.decl_type(s)
                if t is None:
                    what = "allocatable " if s.allocatable else ""
                    self.refuse(f"the declaration of '{s.name}' ({what}{s.type})", s)
                if t.kind == "record" and s.init is not None:
                    self.same_record(s.init, s.type, s)
            elif kind is Assign:
                root = s.target
                if isinstance(root, FieldAccess):
                    name = self.field_root(root, s)
                    if self.types.get(id(root)) is None or not self.kind_ok(self.types[id(root)]):
                        self.refuse("a field of a type that is not handled", s)
                    ft = self.types[id(root)]
                    if ft.kind == "record":
                        self.same_record(s.value, ft.name, s)
                    else:
                        self.fits(s.value, ft.kind, f"the field '{root.field}'", s)
                    if name in fixed:
                        self.refuse(f"an assignment to a field of the parameter '{name}'", s)
                    continue
                if isinstance(root, Apply):
                    if not root.bracket:
                        self.refuse("an assignment to anything but a variable or an array element", s)
                elif not isinstance(root, Var):
                    self.refuse("an assignment to anything but a variable or an array element", s)
                if root.name in fixed:
                    self.refuse(f"an assignment to the constant or parameter '{root.name}'", s)
                tt = self.vars_type(root.name, body, f)
                if tt is not None and tt.kind == "record":
                    if isinstance(root, Apply):
                        self.refuse("a subscript on a record", s)
                    self.same_record(s.value, tt.name, s)
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

    def vars_type(self, name, body, f):
        """The Type of the parameter or declared variable `name` (None if there is none)."""
        if f is not None:
            for typ, pname, _ in f.params:
                if pname == name:
                    return self.param_type(typ)
        for d in statements_in(body):
            if isinstance(d, Decl) and d.name == name:
                return self.decl_type(d)
        return None

    def same_record(self, value, name, node):
        """`value` must be a record of the type `name` (anything else is the interpreter's runtime error: refused)."""
        t = self.types.get(id(value))
        if t is None or t.kind != "record" or t.name != name or t.rank != 0:
            self.refuse(f"a value that is not a {name}", node)

    FITS = {"int": ("int", "int32"), "int32": ("int", "int32"), "real": ("int", "int32", "real", "real32"),
            "real32": ("int", "int32", "real", "real32"), "logical": ("logical",)}

    def fits(self, value, kind, what, node):
        """A single value of the kind `value` has can be stored in `what` (a field) of `kind`, as the interpreter does."""
        t = self.types.get(id(value))
        if t is None or t.rank != 0 or t.kind not in self.FITS.get(kind, ()):
            self.refuse(f"a value that does not fit {what}", node)

    def field_root(self, e, node):
        """The variable a chain of fields `a.b.c` starts at (anything but a plain variable is refused)."""
        while isinstance(e, FieldAccess):
            if e.args is not None or e.call or e.chain:
                self.refuse("a field with subscripts or a record's function", node)
            e = e.base
        if not isinstance(e, Var):
            self.refuse("a field of a computed value or of an element", node)
        return e.name

    def check_call_args(self, f, args, node):
        for a, (typ, _, _) in zip(args, f.params):
            at, pt = self.types.get(id(a)), self.param_type(typ)
            if pt is not None and pt.kind == "record":
                if at is None or at.kind != "record" or at.name != pt.name or at.rank != 0:
                    self.refuse(f"the call of '{f.name}' with an argument that is not a {pt.name}", node)
                continue
            if at is None or pt is None or (at.rank != pt.rank) or (at.kind != pt.kind and at.rank > 0):
                self.refuse(f"the call of '{f.name}' with an argument that is not what the parameter takes", node)

    def check_aliasing(self, s, fixed=()):
        f = self.unit.functions[s.name]
        self.check_call_args(f, s.args, s)
        roots = [self.root_name(a) for a in s.args]
        for a, (typ, pname, intent) in zip(s.args, f.params):
            if intent == "out" and isinstance(a, FieldAccess):
                self.refuse("a field passed as out", s)
            if intent == "inout" and isinstance(a, FieldAccess):
                self.field_root(a, s)
            if intent == "out" and (self.param_type(typ) or Type("int")).kind == "record":
                self.refuse(f"a record passed as out", s)
            if intent in ("out", "inout") and isinstance(a, Apply) and any(isinstance(x, Slice) for x in a.args):
                self.refuse(f"a section of '{a.name}' passed as {intent}", s)
        for (typ, pname, intent), root in zip(f.params, roots):
            if intent in ("out", "inout") and root in fixed:
                self.refuse(f"the constant or parameter '{root}' passed as {intent}", s)
            if intent in ("out", "inout") and root is not None and roots.count(root) > 1:
                self.refuse(f"'{root}' passed as {intent} and as another argument of the same call", s)

    @staticmethod
    def root_name(a):
        while isinstance(a, FieldAccess):
            a = a.base
        return getattr(a, "name", None)

    def decl_type(self, d):
        if d.type in self.records:
            return None if d.dims is not None or d.allocatable or d.const else Type("record", 0, False, d.type)
        if d.type not in KINDS:
            return None
        rank = len(d.dims) if d.dims is not None else 0
        if rank > MAX_RANK:
            return None
        return Type(d.type, rank)

    STATEMENTS = (Decl, Assign, If, Do, DoWhile, Print, Call, Return, Stop, Exit, Cycle, Comment, Allocate, Deallocate)

    @staticmethod
    def allocs_of(body, f):
        return {d.name for d in statements_in(body) if isinstance(d, Decl) and d.allocatable}

    def check_expr(self, e, strings):
        kind = type(e)
        if id(e) in self.skip:                          # the `:` of an allocatable array's declaration
            return
        if kind in self.STATEMENTS:
            return
        if kind is Str:
            if id(e) not in strings:
                self.refuse("text outside a print", e)
            return
        if kind is Slice:                               # the parts of a section: checked as the expressions they are
            return
        if kind is NamedArg:                            # `x = 1.0` in a record's constructor: its value is checked itself
            return
        if kind is FieldAccess:
            self.field_root(e, e)
            t = self.types.get(id(e))
            if not self.kind_ok(t) or t.rank != 0:
                self.refuse(f"a field of type {t if t is not None else 'unknown'}", e)
            return
        if kind not in (Num, Bool, Var, Unary, Binary, Apply, ArrayLit, MatrixLit):
            if kind is IsType:
                self.refuse("a type test", e)
            self.refuse(f"a {kind.__name__}", e)
        if kind in (Unary, Binary):
            for x in ((e.operand,) if kind is Unary else (e.left, e.right)):
                xt = self.types.get(id(x))
                if xt is not None and xt.kind == "record":
                    self.refuse(f"a record with the operator '{e.op}'", e)
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
            if any(isinstance(a, Slice) for a in e.args) and not e.bracket:
                self.refuse("part of an array", e)
            if e.bracket:
                pass
            elif e.name in self.records:
                self.check_constructor(e)
            elif e.name in self.unit.functions:
                self.check_call_args(self.unit.functions[e.name], e.args, e)
            elif e.name not in BUILTINS:
                self.refuse(f"the function '{e.name}'", e)
            else:
                for a in e.args:
                    at = self.types.get(id(a))
                    if at is not None and at.kind == "record":
                        self.refuse(f"a record as an argument of '{e.name}'", e)
                self.check_builtin(e)
        t = self.types.get(id(e))
        if not self.kind_ok(t):
            self.refuse(f"an expression of type {t if t is not None else 'unknown'}", e)

    def check_constructor(self, e):
        """`point(1.0, 2.0)` / `point(x = 1.0)`: only the calls that cannot go wrong at run time (every field given
        once, or left to its default) are compiled."""
        r = self.records[e.name]
        pos = [a for a in e.args if not isinstance(a, NamedArg)]
        named = [a for a in e.args if isinstance(a, NamedArg)]
        names = [f.name for f in r.fields]
        if len(pos) > len(r.fields) or (not named and any(f.init is None for f in r.fields[len(pos):])):
            self.refuse(f"a call of {e.name}() with the wrong number of values", e)
        given = names[:len(pos)]
        for a in named:
            if a.name not in names or a.name in given:
                self.refuse(f"a call of {e.name}() naming a field wrongly", e)
            given.append(a.name)
        by_name = {f.name: f for f in r.fields}
        for fname, a in zip(given, pos + named):
            value = a.value if isinstance(a, NamedArg) else a
            ft = by_name[fname].type
            if ft in self.records:
                self.same_record(value, ft, e)
            else:
                self.fits(value, ft, f"the field '{fname}'", e)

    def check_builtin(self, e):
        name = e.name
        if name == "allocated":
            if len(e.args) != 1 or not isinstance(e.args[0], Var) or e.args[0].name not in self.allocs:
                self.refuse("allocated() of anything but the name of an allocatable array", e)
            return
        ranks = [self.types[id(a)].rank for a in e.args]
        if name == "atan" and len(e.args) == 2:
            if any(r != 0 for r in ranks):
                self.refuse("atan() of an array and a second argument", e)
        elif name in ELEMENTAL1:
            if len(e.args) != 1:
                self.refuse(f"{name}() with {len(e.args)} arguments", e)
        elif name in ELEMENTAL2:
            if len(e.args) != 2 or any(r != 0 for r in ranks):
                self.refuse(f"{name}() of anything but two single values", e)
            if name == "modulo" and self.unit.ieee:
                self.refuse("modulo() in a file that says `use ieee`", e)
        elif name in ARRAY_FUNCTIONS:
            self.check_array_function(e, ranks)
        elif name in CONSTANTS:
            if len(e.args) != 1 or ranks[0] != 0 or (name != "huge" and self.types[id(e.args[0])].kind not in ("real", "real32")):
                self.refuse(f"{name}() of anything but a single real{' or whole number' if name == 'huge' else ''}", e)
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
        if name in ("mod", "min", "max", "int", "sign", "modulo", "dim", "atan2", "atan") and any(self.types[id(a)].kind in ("logical",) for a in e.args):
            self.refuse(f"{name}() of a logical", e)

    def merge_kind(self, e):
        """The kind merge(a, b, mask) gives, or None when a and b differ (a real literal next to a real32 is a real32, a
        whole-number literal next to an int32 an int32, as anywhere in a call)."""
        a, b = e.args[0], e.args[1]
        ka, kb = self.types[id(a)].kind, self.types[id(b)].kind
        if ka == kb:
            return ka
        for lit, other, kl, ko in ((a, b, ka, kb), (b, a, kb, ka)):
            if isinstance(lit, Num) and (kl, ko) in (("real", "real32"), ("int", "int32")):
                return ko
        return None

    def check_array_function(self, e, ranks):
        name, ts = e.name, [self.types[id(a)] for a in e.args]
        out = self.types[id(e)]
        if name == "reshape":
            if len(e.args) != 2 or not isinstance(e.args[1], ArrayLit) or ranks[0] < 1 or not 1 <= out.rank <= MAX_RANK \
                    or ts[1].kind not in ("int", "int32"):
                self.refuse("reshape() of anything but an array and a shape written out as a list of whole numbers (at most three)", e)
        elif name == "transpose":
            if len(e.args) != 1 or ranks[0] != 2:
                self.refuse("transpose() of anything but a matrix", e)
        elif name in ("flatten", "shape"):
            if len(e.args) != 1 or ranks[0] < 1:
                self.refuse(f"{name}() of anything but an array", e)
        elif name == "merge":
            if len(e.args) != 3 or ts[2].kind != "logical" or self.merge_kind(e) is None:
                self.refuse("merge() of values of different types", e)
        elif name in ("any", "all", "count"):
            if len(e.args) != 1 or ranks[0] < 1 or ts[0].kind != "logical":
                self.refuse(f"{name}() of anything but a whole array of logicals", e)
        elif name in ("maxloc", "minloc"):
            if len(e.args) != 1 or ranks[0] != 1 or ts[0].kind == "logical":
                self.refuse(f"{name}() of anything but a vector", e)
        elif name == "cshift":
            if len(e.args) not in (2, 3) or ranks[0] not in (1, 2) or ranks[1] != 0 or ts[1].kind not in ("int", "int32"):
                self.refuse("cshift() of anything but a vector or matrix and a whole-number shift", e)
            if len(e.args) == 3 and not (isinstance(e.args[2], Num) and isinstance(e.args[2].value, int)):
                self.refuse("cshift() with a dimension that is not a literal", e)

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
            if e.name in self.allocs:
                self.emit(f'rt_need_alloc({self.vars[e.name][1]}.data, "{e.name}", {self.line});')
            if e.name in self.outs:
                self.emit(f'if (!*set_{e.name}) rt_fail({self.line}, "\'{e.name}\' is used before it is assigned");')
            return V(self.vars[e.name][0], self.vars[e.name][1])
        if t is Unary:
            return self.unary(e)
        if t is Binary:
            return self.binary(e)
        if t is Apply:
            return self.apply(e)
        if t is FieldAccess:
            ft = self.types[id(e)]
            return V(ft, self.tmp(cscalar(ft), self.field_lvalue(e)))
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
        if e.name in self.allocs:                      # checked before the subscripts are evaluated
            self.emit(f'rt_need_alloc({arr}.data, "{e.name}", {self.line});')
        if len(e.args) != rank:                        # the interpreter stops here, before any subscript is evaluated
            self.emit(f'rt_fail({self.line}, "\'{e.name}\' has {rank} dimension(s) but {len(e.args)} subscript(s) were given");')
            return "0"
        idx = []
        for d, a in enumerate(e.args):
            i = self.tmp("int64_t", self.scalar(a, "int"))
            self.emit(f"rt_check_index({i}, {arr}.shape[{d}], {d + 1}, {rank}, {self.line});")
            idx.append(i)
        off = f"({idx[-1]} - 1)"
        for d in range(rank - 2, -1, -1):
            off = f"(({idx[d]} - 1) + {arr}.shape[{d}] * {off})"
        return self.tmp("int64_t", off)

    def section_plan(self, e):
        """Evaluate and check the subscripts of the section `e` (one dimension at a time, as the interpreter does) and say what
        each dimension picks: ("i", index) for a whole number, ("s", start, step, count) for a range."""
        arr = self.vars[e.name][1]
        rank, line = self.vars[e.name][0].rank, self.line
        if e.name in self.allocs:
            self.emit(f'rt_need_alloc({arr}.data, "{e.name}", {line});')
        plan = []
        if len(e.args) != rank:
            raise NotCompiled(f"a section of '{e.name}' with the wrong number of subscripts", line)
        for d, a in enumerate(e.args):
            n = f"{arr}.shape[{d}]"
            if isinstance(a, Slice):
                step = self.tmp("int64_t", self.scalar(a.step, "int")) if a.step is not None else "INT64_C(1)"
                if a.step is not None:
                    self.emit(f"rt_check_step_nonzero({step}, {line});")
                    if a.lo is None or a.hi is None:
                        self.emit(f'rt_check_backwards({step}, "{e.name}", {n}, {line});')
                lo = self.tmp("int64_t", self.scalar(a.lo, "int")) if a.lo is not None else "INT64_C(1)"
                hi = self.tmp("int64_t", self.scalar(a.hi, "int")) if a.hi is not None else self.tmp("int64_t", n)
                count = self.tmp("int64_t", f"rt_slice_count({lo}, {hi}, {step}, {n}, {d + 1}, {rank}, {1 if a.step is not None else 0}, {line})")
                plan.append(("s", lo, step, count))
            else:
                i = self.tmp("int64_t", self.scalar(a, "int"))
                self.emit(f"rt_check_index({i}, {n}, {d + 1}, {rank}, {line});")
                plan.append(("i", i))
        return plan

    def section_loops(self, e, plan, body):
        """Emit loops over the picked elements in the section's own column-major order; `body(source_offset, k)` writes
        the statement for one element (`k` counts them from 0)."""
        arr = self.vars[e.name][1]
        strides, stride = [], "1"
        for d in range(len(plan)):
            strides.append(stride)
            stride = f"({stride} * {arr}.shape[{d}])"
        slices = [d for d, p in enumerate(plan) if p[0] == "s"]
        fixed = " + ".join(f"({plan[d][1]} - 1) * {strides[d]}" for d, p in enumerate(plan) if p[0] == "i") or "0"
        js = {d: self.loop_var() for d in slices}
        k = self.loop_var()
        self.emit(f"int64_t {k} = 0;")
        for d in reversed(slices):                       # the last dimension is the outermost loop, the first the fastest
            self.emit(f"for (int64_t {js[d]} = 0; {js[d]} < {plan[d][3]}; {js[d]}++) {{")
            self.depth += 1
        pos = " + ".join([f"({fixed})"] + [f"({plan[d][1]} + {js[d]} * {plan[d][2]} - 1) * {strides[d]}" for d in slices])
        body(pos, k)
        self.emit(f"{k}++;")
        for _ in slices:
            self.depth -= 1
            self.emit("}")

    def section(self, e):
        t = self.vars[e.name][0]
        plan = self.section_plan(e)
        slices = [p for p in plan if p[0] == "s"]
        res = self.tmp_array(t.kind, len(slices))
        counts = [p[3] for p in slices] + ["1"] * (3 - len(slices))
        self.emit(f"rt_alloc_{t.kind}(&{res}, {len(slices)}, {', '.join(counts)}, {self.line});")
        arr = self.vars[e.name][1]
        self.section_loops(e, plan, lambda pos, k: self.emit(f"{res}.data[{k}] = {arr}.data[{pos}];"))
        return V(Type(t.kind, len(slices)), res)

    def element(self, e):
        if any(isinstance(a, Slice) for a in e.args):
            return self.section(e)
        t = self.vars[e.name][0]
        arr = self.vars[e.name][1]
        off = self.subscripts(e, arr)
        return V(Type(t.kind), self.tmp(CSCALAR[t.kind], f"{arr}.data[{off}]"))

    # -- calls and built-in functions -----------------------------------------------------------------------------------
    def call_args(self, f, args):
        """The C arguments of a call of `f`: values for `in`, addresses for an `inout` or `out` single value (an `out` one
        with a flag that the callee sets when it assigns it), arrays as they are. Also sets `self.after_call`: what to do
        once the call has returned (the out parameters that were never assigned are an error then)."""
        out = []
        self.after_call = []
        for a, (typ, pname, intent) in zip(args, f.params):
            pt = self.param_type(typ)
            if intent in ("inout", "out") and pt.rank == 0:
                if isinstance(a, Var) and a.name in self.outs and intent == "inout":     # an unassigned out parameter is read
                    self.emit(f'if (!*set_{a.name}) rt_fail({self.line}, "\'{a.name}\' is used before it is assigned");')
                ref = "&" + self.lvalue(a)
                if intent == "out":
                    self.n += 1
                    flag = f"set{self.n}"
                    self.emit(f"bool {flag} = false;")
                    out.append(ref)
                    out.append("&" + flag)
                    self.after_call.append(f'if (!{flag}) rt_fail({self.line}, "out parameter \'{pname}\' was never assigned in {f.name}");')
                    if isinstance(a, Var) and a.name in self.outs:                       # now assigned in this function too
                        self.after_call.append(f"*set_{a.name} = true;")
                else:
                    out.append(ref)
            elif pt.rank == 0:
                out.append(self.scalar(a, pt.kind, f"argument '{pname}'"))
            else:
                v = self.value(a)
                out.append(v.atom)
        return out

    def field_lvalue(self, e):
        """The C lvalue of `a.b.c` (a chain of fields of a variable)."""
        if isinstance(e, Var):
            return self.vars[e.name][1]
        return f"{self.field_lvalue(e.base)}.m_{e.field}"

    def constructor(self, e):
        """`point(1.0, 2.0)`: the defaults, then the given fields in order (positional ones first), each stored as the
        interpreter stores it (checked when it has to be)."""
        r = self.records[e.name]
        self.n += 1
        res = f"t{self.n}"
        self.emit(f"r_{e.name} {res} = d_{e.name}();")
        pos = [a for a in e.args if not isinstance(a, NamedArg)]
        named = [a for a in e.args if isinstance(a, NamedArg)]
        names = [f.name for f in r.fields]
        by_name = {f.name: f for f in r.fields}
        for fname, a in zip(names[:len(pos)] + [a.name for a in named], pos + named):
            value = a.value if isinstance(a, NamedArg) else a
            ft = by_name[fname].type
            if ft in self.records:
                atom = self.value(value).atom
            else:
                atom = self.scalar(value, ft, f"field '{fname}' of {e.name}")
            self.emit(f"{res}.m_{fname} = {atom};")
        return V(Type("record", 0, False, e.name), res)

    def lvalue(self, target):
        """A C lvalue for a variable or an array element (evaluating and checking its subscripts first)."""
        if isinstance(target, Var):
            return self.vars[target.name][1]
        if isinstance(target, FieldAccess):
            return self.field_lvalue(target)
        arr = self.vars[target.name][1]
        return f"{arr}.data[{self.subscripts(target, arr)}]"

    def apply(self, e):
        line = self.line
        if e.bracket:
            return self.element(e)
        if e.name in self.records:
            return self.constructor(e)
        if e.name in self.unit.functions:
            f = self.unit.functions[e.name]
            if len(e.args) != len(f.params):
                raise NotCompiled(f"a call of '{e.name}' with optional or missing arguments", line)
            args = self.call_args(f, e.args)
            self.emit(f'rt_call_in({line}, "{e.name}");')
            rt = self.param_type(f.ret)
            result = self.tmp(cscalar(rt), f"{f_name(e.name)}({', '.join(args)})")
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
        if name in ("floor", "ceiling", "nint"):             # a whole number stays as it is; a real gives an int (an int32 for a real32)
            if kind in ("int", "int32"):
                return kind, x
            op = ("floor", "ceiling", "nint").index(name)
            if kind == "real32":
                return "int32", f"((int32_t)rt_whole((double){x}, {op}, 32, {line}))"
            return "int", f"rt_whole({x}, {op}, 64, {line})"
        if name in ("int32", "int64"):
            if kind in ("int", "int32"):
                if name == "int64":
                    return "int", self.cast(x, kind, "int")
                return "int32", f'rt_narrow32({self.cast(x, kind, "int")}, {line}, "int32()")'
            if name == "int64":
                return "int", f'rt_to_whole({self.cast(x, kind, "real")}, 64, "int64", {line})'
            return "int32", f'((int32_t)rt_to_whole({self.cast(x, kind, "real")}, 32, "int32", {line}))'
        r = self.cast(x, kind, "real")
        if name in ("real", "real64"):
            return "real", r
        if name in ("aint", "anint"):
            call = f"rt_{name}({r}, {line})"
            return ("real32", f"((float){call})") if kind == "real32" else ("real", call)
        if kind == "real32":                                 # single precision: sqrt is exact in double then rounded; the
            if name == "sqrt":                               # rest are the C library's sinf, expf, ... (as the interpreter's)
                return "real32", f"((float)rt_sqrt({r}, 0, {line}))"
            if name in ("log", "log10"):
                return "real32", f'rt_logf({x}, {SINGLE_LIBM[name]}, "{name}", {line})'
            if name == "atanh":
                return "real32", f"rt_atanhf({x}, {line})"
            return "real32", f'rt_math1f("{name}", {SINGLE_LIBM[name]}({x}), {x}, {line})'
        if name == "atanh":
            return "real", f"rt_atanh({r}, {was_int}, {line})"
        if name == "sqrt":
            return "real", f"rt_sqrt({r}, {was_int}, {line})"
        if name == "exp":
            return "real", f"rt_exp({r}, {was_int}, {line})"
        if name in ("log", "log10"):
            return "real", f'rt_log({r}, {name}, "{name}", {was_int}, {line})'
        return "real", f'rt_math1("{name}", {name}({r}), {r}, {was_int}, {line})'

    def array_function(self, e, vals, line):
        name = e.name
        out = self.types[id(e)]
        a = vals[0]
        if name == "reshape":
            shp = vals[1]
            r = out.rank
            kind = a.t.kind
            dims = [self.tmp("int64_t", self.cast(f"{shp.atom}.data[{d}]", shp.t.kind, "int")) for d in range(r)]
            self.n += 1
            sh = f"rs{self.n}"
            self.emit(f"int64_t {sh}[3] = {{{', '.join(dims + ['1'] * (3 - r))}}};")
            self.emit(f"rt_reshape_check({sh}, {r}, {a.atom}.n, {line});")
            res = self.tmp_array(kind, r)
            self.emit(f"rt_alloc_{kind}(&{res}, {r}, {sh}[0], {sh}[1], {sh}[2], {line});")
            k = self.loop_var()
            self.emit(f"for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {self.elem(a, k)};")
            return V(Type(kind, r), res)
        if name in ("transpose", "flatten", "shape"):
            kind = "int" if name == "shape" else a.t.kind
            if name == "shape":
                res = self.tmp_array("int", 1)
                self.emit(f"rt_alloc_int(&{res}, 1, {a.t.rank}, 1, 1, {line});")
                for d in range(a.t.rank):
                    self.emit(f"{res}.data[{d}] = {a.atom}.shape[{d}];")
                return V(Type("int", 1), res)
            res = self.tmp_array(kind, 1 if name == "flatten" else 2)
            if name == "flatten":
                self.emit(f"rt_alloc_{kind}(&{res}, 1, {a.atom}.n, 1, 1, {line});")
                k = self.loop_var()
                self.emit(f"for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {self.elem(a, k)};")
                return V(Type(kind, 1), res)
            self.emit(f"rt_alloc_{kind}(&{res}, 2, {a.atom}.shape[1], {a.atom}.shape[0], 1, {line});")
            i, j = self.loop_var(), self.loop_var()
            src = self.elem(V(a.t, a.atom, a.src), f"({i} + {j} * {a.atom}.shape[0])")
            self.emit(f"for (int64_t {j} = 0; {j} < {a.atom}.shape[1]; {j}++) for (int64_t {i} = 0; {i} < {a.atom}.shape[0]; {i}++) "
                      f"{res}.data[{j} + {i} * {a.atom}.shape[1]] = {src};")
            return V(Type(kind, 2), res)
        if name in ("any", "all", "count"):
            k = self.loop_var()
            if name == "count":
                acc = self.tmp("int64_t", "0")
                self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) if ({a.atom}.data[{k}]) {acc}++;")
                return V(Type("int"), acc)
            acc = self.tmp("bool", "false" if name == "any" else "true")
            test = f"{a.atom}.data[{k}]" if name == "any" else f"!{a.atom}.data[{k}]"
            self.emit(f"for (int64_t {k} = 0; {k} < {a.atom}.n; {k}++) if ({test}) {{ {acc} = {'true' if name == 'any' else 'false'}; break; }}")
            return V(Type("logical"), acc)
        if name in ("maxloc", "minloc"):
            k = self.loop_var()
            self.emit(f'rt_check_nonempty({a.atom}.n, "{name}", {line});')
            best = self.tmp("int64_t", "0")
            op = ">" if name == "maxloc" else "<"
            x, bx = self.elem(a, k), self.elem(a, best)
            self.emit(f"for (int64_t {k} = 1; {k} < {a.atom}.n; {k}++) if ({x} {op} {bx}) {best} = {k};")
            return V(Type("int"), self.tmp("int64_t", f"{best} + 1"))
        if name == "merge":
            b, mask = vals[1], vals[2]
            kind = self.merge_kind(e)
            arrays = [v for v in vals if v.t.rank > 0]
            if not arrays:
                return V(Type(kind), self.tmp(CSCALAR[kind], f"({mask.atom} ? {self.cast(a.atom, a.t.kind, kind)} : {self.cast(b.atom, b.t.kind, kind)})"))
            if len(arrays) == 2:
                self.emit(f"rt_same_shape({arrays[0].atom}.shape, {arrays[0].atom}.rank, {arrays[1].atom}.shape, {arrays[1].atom}.rank, 0, {line});")
            elif len(arrays) == 3:
                self.emit(f"rt_same_shape3({', '.join(f'{v.atom}.shape, {v.atom}.rank' for v in arrays)}, {line});")
            res = self.array_like(arrays[0], kind)
            k = self.loop_var()
            pick = lambda v: self.cast(self.elem(v, k) if v.t.rank > 0 else v.atom, v.t.kind, kind)
            m = f"{mask.atom}.data[{k}]" if mask.t.rank > 0 else mask.atom
            self.emit(f"for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {m} ? {pick(a)} : {pick(b)};")
            return V(Type(kind, arrays[0].t.rank), res)
        if name == "cshift":
            shift = self.cast(vals[1].atom, vals[1].t.kind, "int")
            dim = e.args[2].value if len(e.args) == 3 else 1
            if not 1 <= dim <= a.t.rank:
                self.emit(f'rt_fail({line}, "cshift(): dimension must be 1..{a.t.rank}");')
                return V(a.t, a.atom, a.src)
            kind = a.t.kind
            res = self.array_like(V(a.t, a.atom), kind)
            n = f"{a.atom}.shape[{dim - 1}]"
            stride = "1" if dim == 1 else f"{a.atom}.shape[0]"
            k = self.loop_var()
            src = self.elem(V(a.t, a.atom, a.src), f"rt_cshift_source({k}, {shift}, {n}, {stride})")
            self.emit(f"if ({n} > 0) for (int64_t {k} = 0; {k} < {res}.n; {k}++) {res}.data[{k}] = {src};")
            return V(Type(kind, a.t.rank), res)
        raise NotCompiled(f"{name}()", line)

    def constant(self, e):
        """huge(x), epsilon(x), tiny(x): a constant of the kind of x (which is still evaluated)."""
        name, kind = e.name, self.types[id(e.args[0])].kind
        self.value(e.args[0])
        if name == "huge":
            text = {"int": "INT64_MAX", "int32": "INT32_MAX", "real": repr(sys.float_info.max),
                    "real32": "3.4028234663852886e+38f"}.get(kind)
        elif name == "epsilon":
            text = {"real": repr(sys.float_info.epsilon), "real32": "1.1920928955078125e-07f"}.get(kind)
        else:
            text = {"real": repr(sys.float_info.min), "real32": "1.1754943508222875e-38f"}.get(kind)
        if text is None:
            raise NotCompiled(f"{name}() of a {kind}", self.line)
        return V(Type(kind), self.tmp(CSCALAR[kind], text))

    def elemental2(self, name, e, vals, line):
        """atan2, sign, modulo and dim of two single values."""
        (a, b), kinds = vals, [v.t.kind for v in vals]
        out = self.types[id(e)].kind
        was = [1 if k in ("int", "int32") else 0 for k in kinds]
        ints = all(k in ("int", "int32") for k in kinds)
        d = [self.cast(v.atom, v.t.kind, "real") for v in vals]
        if name == "atan2":
            if kinds == ["real32", "real32"]:
                return V(Type("real32"), self.tmp("float", f"atan2f({a.atom}, {b.atom})"))
            r = f"atan2({d[0]}, {d[1]})"
            return V(Type(out), self.tmp(CSCALAR[out], f"rt_r32({r}, {line})" if out == "real32" else r))
        if ints:
            ia, ib = (self.cast(v.atom, v.t.kind, "int") for v in vals)
            if out == "int32":
                if name == "sign":
                    return V(Type("int32"), self.tmp("int32_t", f"rt_sign_int32({a.atom}, {b.atom}, {line})"))
                if name == "dim":
                    return V(Type("int32"), self.tmp("int32_t", f"((int32_t)rt_dim_int({ia}, {ib}, 32, {line}))"))
                return V(Type("int32"), self.tmp("int32_t", f"((int32_t)rt_modulo({ia}, {ib}, {line}))"))
            if name == "sign":
                return V(Type("int"), self.tmp("int64_t", f"rt_sign_int({ia}, {ib}, {line})"))
            if name == "dim":
                return V(Type("int"), self.tmp("int64_t", f"rt_dim_int({ia}, {ib}, 64, {line})"))
            return V(Type("int"), self.tmp("int64_t", f"rt_modulo({ia}, {ib}, {line})"))
        if name == "sign":
            r = f"copysign(fabs({d[0]}), {d[1]})"
        elif name == "dim":
            r = f"rt_dim_real({d[0]}, {d[1]})"
        else:
            r = f"rt_fmodulo({d[0]}, {d[1]}, {was[0]}, {was[1]}, {line})"
        if out == "real32":
            return V(Type("real32"), self.tmp("float", f"rt_r32({r}, {line})"))
        return V(Type("real"), self.tmp("double", r))

    def builtin(self, e):
        line, name = self.line, e.name
        if name == "allocated":
            return V(Type("logical"), self.tmp("bool", f"({self.vars[e.args[0].name][1]}.data != NULL)"))
        if name == "atan" and len(e.args) == 2:
            name = "atan2"
        if name in CONSTANTS:
            return self.constant(e)
        vals = [self.value(a, exact=name in ("mod", "min", "max") or name in ELEMENTAL2) for a in e.args]
        if name in ELEMENTAL2:
            return self.elemental2(name, e, vals, line)
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
        if name in ARRAY_FUNCTIONS:
            return self.array_function(e, vals, line)
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
        self.emit(f"if (decl_{s.name}) rt_fail({line}, \"'{s.name}' already declared\"); decl_{s.name} = true;")
        if t.rank == 0:
            if s.init is not None:
                self.emit(f"{name} = {self.scalar(s.init, t.kind, f'line {line}')};")
            elif t.kind == "record":
                self.emit(f"{name} = d_{t.name}();")
            else:
                self.emit(f"{name} = {'false' if t.kind == 'logical' else '0'};")
            return
        if s.allocatable:                                              # not allocated (again, if this runs in a loop)
            self.emit(f"rt_free_{t.kind}(&{name});")
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
        if isinstance(target, FieldAccess):
            ft = self.types[id(target)]
            lv = self.field_lvalue(target)
            self.emit(f"{lv} = {self.scalar(s.value, ft.kind, f'line {line}')};")
            return
        t = self.vars[target.name][0]
        if isinstance(target, Apply) and any(isinstance(a, Slice) for a in target.args):
            self.assign_section(s, t)
            return
        if isinstance(target, Apply):                                   # an element: its subscripts first
            lv = self.lvalue(target)
            self.emit(f"{lv} = {self.scalar(s.value, t.kind, f'line {line}')};")
            return
        name = self.vars[target.name][1]
        if t.rank == 0:
            self.emit(f"{name} = {self.scalar(s.value, t.kind, f'line {line}')};")
            if target.name in self.outs:
                self.emit(f"*set_{target.name} = true;")
            return
        v = self.value(s.value)
        if target.name in self.allocs:
            if v.t.rank == 0:
                self.emit(f'rt_need_alloc({name}.data, "{target.name}", {line});')
        if v.t.rank == 0:
            atom = self.narrow(v.atom, v.t.kind, t.kind, line, f"line {line}")
            k = self.loop_var()
            self.emit(f"for (int64_t {k} = 0; {k} < {name}.n; {k}++) {name}.data[{k}] = {atom};")
            return
        if target.name in self.allocs:                                  # becomes the value's shape (allocated or resized)
            if v.t.rank != t.rank:
                self.emit(f'rt_fail({line}, "\'{target.name}\' has {t.rank} dimension(s) but the value has {v.t.rank}");')
                return
            new = self.tmp_array(t.kind, t.rank)
            self.emit(f"rt_like_{t.kind}(&{new}, {v.atom}.shape, {v.atom}.rank, {line});")
            self.copy_into(new, t.kind, v, line, f"line {line}")
            self.emit(f"rt_free_{t.kind}(&{name});")
            self.emit(f"{name} = {new};")
            self.emit(f"{new}.data = NULL;                                  /* now owned by {name} */")
            return
        self.emit(f"rt_same_shape({name}.shape, {name}.rank, {v.atom}.shape, {v.atom}.rank, 1, {line});")
        self.copy_into(name, t.kind, v, line, f"line {line}")

    def assign_section(self, s, t):
        """`a[2:4, j] = value`: the section is worked out first, then the value, then the shapes must agree (a single
        value fills the section)."""
        line, target = self.line, s.target
        plan = self.section_plan(target)
        slices = [p for p in plan if p[0] == "s"]
        v = self.value(s.value)
        arr = self.vars[target.name][1]
        what = f"line {line}"
        if v.t.rank == 0:
            atom = self.narrow(v.atom, v.t.kind, t.kind, line, what)
            self.section_loops(target, plan, lambda pos, k: self.emit(f"{arr}.data[{pos}] = {atom};"))
            return
        self.n += 1
        shape = f"sh{self.n}"
        counts = ", ".join([p[3] for p in slices] + ["1"] * (3 - len(slices)))
        self.emit(f"int64_t {shape}[3] = {{{counts}}};")
        self.emit(f"rt_same_shape({shape}, {len(slices)}, {v.atom}.shape, {v.atom}.rank, 1, {line});")
        x = lambda k: self.narrow(self.elem(v, k), v.t.kind, t.kind, line, what)
        self.section_loops(target, plan, lambda pos, k: self.emit(f"{arr}.data[{pos}] = {x(k)};"))

    def st_allocate(self, s):
        line, t = self.line, self.vars[s.name][0]
        name = self.vars[s.name][1]
        self.emit(f"if ({name}.data) rt_fail({line}, \"cannot allocate '{s.name}': it is already allocated (deallocate it first)\");")
        if len(s.dims) != t.rank:
            self.emit(f'rt_fail({line}, "\'{s.name}\' has {t.rank} dimension(s) but {len(s.dims)} extent(s) were given to allocate");')
            return
        dims = []
        for d in s.dims:
            v = self.value(d)
            dims.append(self.tmp("int64_t", v.atom) if v.t.kind in ("int", "int32") and v.t.rank == 0 else None)
        for d in dims:
            if d is None:
                self.emit(f'rt_fail({line}, "array extents must be non-negative integers");')
                return
        for d in dims:
            self.emit(f"rt_check_extent({d}, {line});")
        padded = dims + ["1"] * (3 - len(dims))
        self.emit(f"rt_alloc_{t.kind}(&{name}, {t.rank}, {', '.join(padded)}, {line});")

    def st_deallocate(self, s):
        t, name = self.vars[s.name][0], self.vars[s.name][1]
        self.emit(f"if (!{name}.data) rt_fail({self.line}, \"cannot deallocate '{s.name}': it is not allocated\");")
        self.emit(f"rt_free_{t.kind}(&{name});")

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
                parts.append(("rec:" + v.t.name if v.t.kind == "record" else v.t.kind, v.t.rank, v.atom if v.t.rank == 0 else v))
        for i, (kind, rank, atom) in enumerate(parts):
            if i:
                self.emit("rt_write_sep();")
            if rank == 0:
                self.emit(f"{'w_' + kind[4:] if kind.startswith('rec:') else WRITERS[kind]}({atom});")
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
        after = self.after_call
        self.emit(f'rt_call_in({s.line}, "{s.name}");')
        self.emit(f"{f_name(s.name)}({', '.join(args)});")
        self.emit("rt_call_out();")
        for text in after:
            self.emit(text)

    # -- whole functions -------------------------------------------------------------------------------------------
    def declare_vars(self, params, body):
        self.vars = {}
        self.outs = set()                                   # out parameters that are single values (they start unassigned)
        self.allocs = self.allocs_of(body, None)
        for typ, name, intent in params:
            t = self.param_type(typ)
            access = c_name(name) if (intent == "in" or t.rank > 0) else f"(*{c_name(name)})"
            self.vars[name] = (t, access)
            if intent == "out" and t.rank == 0:
                self.outs.add(name)
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
                out.append(f"{cscalar(t)} *{c_name(name)}")
            elif intent == "out":
                out.append(f"{cscalar(t)} *{c_name(name)}, bool *set_{name}")
            else:
                out.append(f"{cscalar(t)} {c_name(name)}")
        return ", ".join(out) or "void"

    def local_declarations(self, params, body):
        declared = {name for _, name, _ in params}
        seen = set()
        for d in statements_in(body):
            if isinstance(d, Decl) and d.name not in seen and d.name not in declared:
                seen.add(d.name)
                t = self.vars[d.name][0]
                self.emit(f"bool decl_{d.name} = false;")
                if t.rank == 0:
                    self.emit(f"{cscalar(t)} {c_name(d.name)};")
                else:
                    self.emit(f"rt_{t.kind}_arr {c_name(d.name)} = {{0}};")
                    self.arrays.append((t.kind, c_name(d.name)))

    def function(self, f):
        self.func = f
        self.declare_vars(f.params, f.body)
        rt = self.param_type(f.ret) if f.ret else None
        head = f"static {cscalar(rt) if rt else 'void'} {f_name(f.name)}({self.c_params(f)})"
        self.lines, self.depth, self.line, self.arrays = [], 1, 0, []
        if rt:
            start = f"d_{rt.name}()" if rt.kind == "record" else "false" if rt.kind == "logical" else "0"
            self.emit(f"{cscalar(rt)} ret = {start};")
            self.emit("int call_line = rt_call_line;")
        self.local_declarations(f.params, f.body)
        self.block(f.body)
        if f.ret is not None:
            self.emit(f'rt_fail(call_line, "function {f.name} must return a {f.ret if f.ret in self.records else self.param_type(f.ret).kind}");')
        self.emit("fn_end: ;")
        for k, name in self.arrays:
            self.emit(f"rt_free_{k}(&{name});")
        self.emit("return ret;" if rt else "return;")
        return head + ";", "\n".join([head + " {"] + self.lines + ["}"])

    def record_code(self):
        """The C struct of each record type, the function that makes one holding its default values, and the one that
        prints it (`point(x=1.0, y=2.0)`)."""
        out = []
        for name in self.rec_order:
            r = self.records[name]
            out.append("typedef struct { " + " ".join(
                f"{f'r_{fd.type}' if fd.type in self.records else CSCALAR[fd.type]} m_{fd.name};" for fd in r.fields)
                + f" }} r_{name};")
        for name in self.rec_order:
            r = self.records[name]
            body = []
            for fd in r.fields:
                if fd.init is not None:
                    value = self.field_default(fd, self.records)
                elif fd.type in self.records:
                    value = f"d_{fd.type}()"
                else:
                    value = "false" if fd.type == "logical" else "0"
                body.append(f"r.m_{fd.name} = {value};")
            out.append(f"static r_{name} d_{name}(void) {{ r_{name} r; " + " ".join(body) + " return r; }")
            parts = []
            for i, fd in enumerate(r.fields):
                writer = f"w_{fd.type}" if fd.type in self.records else WRITERS[fd.type]
                parts.append(f'rt_write_str({c_string((", " if i else "") + fd.name + "=")}); {writer}(r.m_{fd.name});')
            out.append(f"static void w_{name}(r_{name} r) {{ rt_write_str({c_string(name + '(')}); " + " ".join(parts)
                       + ' rt_write_str(")"); }')
        return out

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
        return "\n".join(["/* generated by `boreas emit-c`: do not edit */"] + flags + [runtime] + self.record_code() + protos + [""] + defs + [""] + main) + "\n"


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
