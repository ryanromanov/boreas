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
                     MatrixLit, NamedArg, Num, Print, Return, Slice, Stop, Str, Unary, Var, Break, Case, SelectType, Select, SelectExpr, Arm, literal_value, NOT_LITERAL, Null)
from .effects import mark_problem
from .interp import conformance
from .tree import statements_in, walk
from .typecheck import infer
from .types import Type, NULL

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("int", "int32", "real", "real32", "logical")
CSCALAR = {"int": "int64_t", "int32": "int32_t", "real": "double", "real32": "float", "logical": "bool", "string": "rt_str"}
STRFUNCS = {"trim", "upper", "lower", "len", "len_trim", "repeat", "contains", "starts_with", "ends_with", "index"}
FORMATFUNCS = {"text", "tf", "right", "left", "fixed", "scientific", "general"}
BUILTINS = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "mod", "min", "max", "real", "real32", "int",
            "size", "maxval", "minval", "sum", "product", "matmul", "dot_product", "allocated", "present"}
ELEMENTAL1 = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "real", "real32", "real64", "asin", "acos", "atan",
              "sinh", "cosh", "tanh", "asinh", "acosh", "atanh", "erf", "erfc", "floor", "ceiling", "nint", "aint", "anint",
              "int32", "int64"}
ELEMENTAL2 = {"atan2", "sign", "modulo", "dim"}
CONSTANTS = {"huge", "epsilon", "tiny"}
ARRAY_FUNCTIONS = {"reshape", "transpose", "flatten", "shape", "merge", "any", "all", "count", "maxloc", "minloc", "cshift"}
BUILTINS |= STRFUNCS | FORMATFUNCS
BUILTINS |= ELEMENTAL1 | ELEMENTAL2 | CONSTANTS | ARRAY_FUNCTIONS
SINGLE_LIBM = {"sin": "sinf", "cos": "cosf", "tan": "tanf", "exp": "expf", "log": "logf", "log10": "log10f", "asin": "asinf",
               "acos": "acosf", "atan": "atanf", "sinh": "sinhf", "cosh": "coshf", "tanh": "tanhf", "asinh": "asinhf",
               "acosh": "acoshf", "erf": "erff", "erfc": "erfcf"}
WRITERS = {"int": "rt_write_int", "int32": "rt_write_int", "real": "rt_write_real", "real32": "rt_write_real32",
           "logical": "rt_write_bool", "string": "rt_write_string"}
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


def all_functions(c):
    """Every procedure of the program: the plain ones, then the functions of the records."""
    return [*c.unit.functions.values(), *c.methods.values()]


OPT = "opt__"      # the name an optional value's type goes by in C: r_opt__int, r_opt__string, r_opt__address, ...


def isrec(t):
    """Is `t` a record or an interface (both are structs in C and are copied, freed and printed the same way)? (An optional
    one is a struct of its own: see `base_name`.)"""
    return t is not None and t.kind in ("record", "interface") and not t.optional


def base_name(t):
    """The name of a type without its `?`: the kind, or the record's name."""
    return t.name if t.kind in ("record", "interface") else t.kind


def skind(t):
    """The name `store` and the free/copy functions know a single value of type `t` by."""
    if t.optional:
        return OPT + base_name(t)
    return t.name if isrec(t) else t.kind


def akind(t):
    """The name of the array runtime (rt_<name>_arr, rt_alloc_<name> ...) for an array of type `t`: the kind, or ra_<record>."""
    return f"ra_{t.name}" if isrec(t) else t.kind


def ctype(t):
    return cscalar(t) if t.rank == 0 else f"rt_{akind(t)}_arr"


def cscalar(t):
    """The C type of a single value of type `t` (a record is a struct)."""
    if t.optional:
        return f"r_{OPT}{base_name(t)}"
    return f"r_{t.name}" if isrec(t) else CSCALAR[t.kind]


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
        self.methods = {}               # (record name, function name) -> Function: a record's own functions
        self.cnames = {}                # id(Function) -> the C name it is compiled under
        self.ifaces = {}                # the interfaces the program defines: name -> InterfaceDef
        self.members = {}               # interface name -> the records that fit it (a value of the interface holds one, tagged 1, 2, ...)
        self.typevars = set()           # the names a select type or `is` gives to its copy
        self.tvars = {}                 # id(Case or IsType) -> (C name, Type) of that copy (declared at the start of the function)
        self.selects = []               # the select types being written (innermost last): their `break` labels
        self.type_order = []            # records and interfaces, each after the types it is made of
        self.has_iface = {}             # name -> can a value of it hold an interface value (which may have no value yet)
        self.optionals = []             # the types of which an optional value is used
        self.deep = {}                  # record name -> does it own text (so that copying and freeing it take work)

    # -- refusing what is not handled ---------------------------------------------------------------------------
    def refuse(self, what, node=None, line=None):
        raise NotCompiled(what, line or getattr(node, "line", None) or None)

    def kind_ok(self, t):
        """Is `t` a type the compiler handles: a single value or array of a number or logical, or one record?"""
        if t is None or not t.known:
            return False
        if t.optional:                                   # a single number, logical, text or record that may be absent
            return t.rank == 0 and (t.kind in KINDS or t.kind == "string" or
                                    (t.kind == "record" and t.name in self.records and not self.has_iface.get(t.name)))
        if isrec(t):
            return t.rank in (0, 1) and (t.name in self.records or t.name in self.ifaces)
        if t.kind == "string":
            return t.rank == 0
        return t.kind in KINDS and 0 <= t.rank <= MAX_RANK

    @staticmethod
    def fopt(fd):
        return fd.type.endswith("?")

    @staticmethod
    def fbase(fd):
        """The type of a field without its `?`."""
        return fd.type[:-1] if fd.type.endswith("?") else fd.type

    def ftype(self, fd):
        """The name a field's type goes by in the C text: the type, or opt__<type> for an optional one."""
        return OPT + fd.type[:-1] if fd.type.endswith("?") else fd.type

    def field_type(self, fd):
        """The Type of one value of the field `fd`."""
        base = self.fbase(fd)
        kind = "record" if base in self.records else "interface" if base in self.ifaces else base
        return Type(kind, 0, self.fopt(fd), base if kind in ("record", "interface") else "")

    def check_records(self):
        """The records of the program: single numbers and logicals and other records only (no functions, arrays or
        text in them yet). Fills `self.records` and `self.rec_order` (fields before the records that hold them)."""
        todo = dict(self.unit.records)
        for name, r in todo.items():
            for fname, f in r.functions.items():
                self.methods[(name, fname)] = f
                self.cnames[id(f)] = f"f_{name}__{fname}"
            for fd in r.fields:
                base = self.fbase(fd)
                iface = base in self.unit.interfaces
                if fd.allocatable or (base not in KINDS and base != "string" and base not in todo and not iface):
                    self.refuse(f"field '{fd.name}' of record '{name}' ({fd.type})", line=fd.line)
                if self.fopt(fd) and (iface or fd.dims is not None or fd.init is not None):
                    self.refuse(f"field '{fd.name}' of record '{name}' ({fd.type})", line=fd.line)
                if fd.dims is not None:                        # a fixed-size array of numbers, records or interface values
                    d = fd.dims[0] if len(fd.dims) == 1 else None
                    if not (isinstance(d, Num) and type(d.value) is int and d.value >= 0) or fd.type == "string" or \
                            (fd.init is not None and (iface or fd.type in todo)):
                        self.refuse(f"field '{fd.name}' of record '{name}' ({fd.type}[])", line=fd.line)
                if iface and fd.init is not None:
                    self.refuse(f"the default of field '{fd.name}'", line=fd.line)
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
                if self.fbase(fd) in todo:
                    visit(self.fbase(fd))
            visiting.discard(name)
            self.records[name] = todo[name]
            self.rec_order.append(name)
        for name in todo:
            visit(name)
        for name in self.rec_order:                                   # holds text, directly or in a record it holds
            self.deep[name] = any(fd.type == "string" or self.deep.get(fd.type) for fd in self.records[name].fields)

    def check_interfaces(self):
        """An interface value holds one of the records that fit the interface (tagged by its place in the list); its functions
        are called by looking at the tag. Only functions that return one value or nothing are handled."""
        for name, idef in self.unit.interfaces.items():
            self.ifaces[name] = idef
            self.deep[name] = True
            self.members[name] = [r for r in self.rec_order if not conformance(self.records[r], idef)]
            for fname, f in idef.functions.items():
                self.cnames[id(f)] = f"iv_{name}__{fname}"
                if f.ret is not None and not isinstance(f.ret, str):
                    self.refuse(f"function '{fname}' of interface '{name}' returns several values", line=idef.line)
        for name in self.records:                                # a record that says it fits must (the interpreter checks it too)
            for iname, _ in self.records[name].implements:
                if iname not in self.ifaces:
                    self.refuse(f"record '{name}' fits the unknown interface '{iname}'", line=self.records[name].line)
        self.order_types()

    def uses_optional(self):
        """The types (number kinds, text and records) of which an optional value is used anywhere in the program."""
        from .tree import walk as _walk
        bases = set()

        def note(typ):
            if isinstance(typ, str) and typ.endswith("?"):
                base = typ[:-1]
                if base in KINDS or base == "string" or base in self.records:
                    bases.add(base)
        for r in self.records.values():
            for fd in r.fields:
                note(fd.type)
        for f in all_functions(self):
            note(f.ret)
            for typ, _, _ in f.params:
                note(typ)
        for f in [*all_functions(self), None]:
            for n in _walk(f.body if f else self.unit.program.body):
                if isinstance(n, Decl):
                    note(n.type)
        return sorted(bases)

    def order_types(self):
        """Records and interfaces in the order their C types can be written: a record after the records and interfaces its
        fields hold, an interface after the records it can hold. A record that holds an interface value it can itself be (a
        `scene` with a `figure` field that fits `figure`) would be a struct inside itself: refused."""
        deps = {}
        for name, r in self.records.items():
            deps[name] = [self.ftype(fd) for fd in r.fields
                          if self.fbase(fd) in self.records or self.fbase(fd) in self.ifaces or self.fopt(fd)]
        for name in self.ifaces:
            deps[name] = list(self.members[name])
        self.optionals = self.uses_optional()
        for base in self.optionals:                     # the type of an optional value of each kind of record, number and text
            deps[OPT + base] = [base] if base in self.records else []
        done, visiting = [], set()

        def visit(n):
            if n in done:
                return
            if n in visiting:
                self.refuse(f"'{n}' holds a value that can be itself", line=getattr((self.records.get(n) or self.ifaces.get(n)), "line", None))
            visiting.add(n)
            for d in deps[n]:
                visit(d)
            visiting.discard(n)
            done.append(n)
        for n in deps:
            visit(n)
        self.type_order = done
        for n in done:                                           # owns text or an interface value; reaches an interface value
            if n in self.ifaces:
                self.deep[n] = self.has_iface[n] = True
            elif n.startswith(OPT):
                base = n[len(OPT):]
                self.deep[n] = base == "string" or bool(self.deep.get(base))
                self.has_iface[n] = bool(self.has_iface.get(base))
            else:
                fields = self.records[n].fields
                self.deep[n] = any(self.fbase(fd) == "string" or self.deep.get(self.ftype(fd)) for fd in fields)
                self.has_iface[n] = any(self.fbase(fd) in self.ifaces or self.has_iface.get(self.ftype(fd)) for fd in fields)

    def iface_function(self, iname, fname):
        return self.ifaces[iname].functions.get(fname)

    def field_default(self, fd, todo):
        """The C text of the default of field `fd` (a literal of the field's own kind, or a whole number for a real)."""
        e = fd.init
        if fd.type == "string":
            if not isinstance(e, Str) or not all(0 < ord(ch) < 128 for ch in e.value):
                self.refuse(f"the default of field '{fd.name}'", line=fd.line)
            return f"rt_str_new({c_string(e.value)}, {len(e.value)}, 0)"
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
        elif fd.type == "real32" and abs(v) <= 3.4028234663852886e38:       # rounded to single precision, as the interpreter does
            return f"((float){v!r})"
        self.refuse(f"the default of field '{fd.name}'", line=fd.line)

    def supported(self, t, what, node=None):
        if t is None or not t.known or t.kind not in KINDS or not 0 <= t.rank <= MAX_RANK:
            self.refuse(what if t is not None and t.known else f"an expression of unknown type", node)

    def check(self):
        u = self.unit
        if u.modules:
            self.refuse("modules")
        if u.program is None:
            self.refuse("a file without a program")
        self.check_records()
        self.check_interfaces()
        if self.checker.errors:
            line, _, message, _ = self.checker.errors[0]
            raise NotCompiled(f"the program has a type error ({message})", line)
        for f in u.functions.values():
            self.cnames[id(f)] = f_name(f.name)
        for f in all_functions(self):
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
        for f in [*all_functions(self), None]:
            self.check_body((f.body if f else u.program.body), f)

    def param_type(self, typ):
        """The Type of a parameter or result written `real`, `int[]`, `real[,]`, or None when it is not handled."""
        from .types import parse
        if not isinstance(typ, str):
            return None
        if typ in self.records:
            return Type("record", 0, False, typ)
        if typ in self.ifaces:
            return Type("interface", 0, False, typ)
        if typ.endswith("[]") and typ[:-2] in self.records:
            return Type("record", 1, False, typ[:-2])
        if typ.endswith("[]") and typ[:-2] in self.ifaces:
            return Type("interface", 1, False, typ[:-2])
        if typ.endswith("?") and typ[:-1] in self.records:                  # an optional record
            return Type("record", 0, True, typ[:-1])
        t = parse(typ, None)
        if t.known and t.kind == "string" and t.rank == 0:
            return t
        if t.known and t.optional:
            return t if t.kind in KINDS and t.rank == 0 else None
        if not t.known or t.kind not in KINDS or not 0 <= t.rank <= MAX_RANK:
            return None
        return t

    def check_select(self, subject, groups, node):
        """`select case`: the subject is a whole number, text or logical; every case value is a literal of that kind (a range of
        whole numbers or text, or one logical), as anything else is checked by the interpreter when the select runs."""
        t = self.types.get(id(subject))
        if t is None or t.rank != 0 or t.kind not in ("int", "int32", "string", "logical"):
            self.refuse("a select case on anything but a whole number, text or logical", node)
        want = {"int": int, "int32": int, "string": str, "logical": bool}[t.kind]
        for values in groups:
            for item in values or ():
                parts = [item.lo, item.hi] if isinstance(item, Slice) else [item]
                if isinstance(item, Slice) and (item.step is not None or t.kind == "logical"):
                    self.refuse("a range with a step, or of logicals, in a select case", node)
                for x in parts:
                    if x is None:
                        continue
                    v = literal_value(x)
                    if v is NOT_LITERAL or type(v) is not want or (want is str and not all(0 < ord(ch) < 128 for ch in v)):
                        self.refuse("a case value that is not a literal of the selector's kind", node)

    def check_type_subject(self, subject, node):
        """What a select type or `is` looks at: an interface variable, parameter or element (not a computed value, whose
        temporary a jump out of the statement would leave behind)."""
        t = self.types.get(id(subject))
        if t is None or t.kind != "interface" or t.rank != 0:
            self.refuse("a select type or `is` on anything but an interface value", node)
        place = isinstance(subject, Var) or self.record_element(subject) or (
            isinstance(subject, FieldAccess) and not self.is_method_call(subject) and self.field_source(subject, node) is not None)
        if not place:
            self.refuse("a select type or `is` on a computed value", node)

    def check_type_name(self, name, node):
        if name not in self.records and name not in self.ifaces:
            self.refuse(f"a select type or `is` naming '{name}'", node)

    def check_select_type(self, s):
        self.check_type_subject(s.subject, s)
        names = [c.values for c in s.cases]
        if None not in names or s.var is None:
            self.refuse("a select type without `case default` or without `as`", s)
        for c in s.cases:
            if c.values is not None:
                self.check_type_name(c.values, c)

    def check_type_test(self, e):
        self.check_type_subject(e.subject, e)
        self.check_type_name(e.type, e)

    def check_body(self, body, f):
        self.opt_ok = set()                                    # the nodes that may be an optional value (what is done with it is handled)
        self.typevars = {n.var for n in walk(body) if isinstance(n, SelectType) and n.var} | \
                        {n.var for n in walk(body) if isinstance(n, IsType) and n.var}
        self.allocs = self.allocs_of(body, f)
        self.skip = {id(x) for d in statements_in(body) if isinstance(d, Decl) and d.allocatable for x in d.dims}
        strings = ()
        fixed = {name for typ, name, intent in f.params if intent == "in"} if f is not None else set()
        fixed |= {d.name for d in statements_in(body) if isinstance(d, Decl) and d.const}
        for s in statements_in(body):
            kind = type(s)
            if kind is Return:
                if f is None or (s.value is None) != (f.ret is None):
                    self.refuse("a return that does not match its procedure", s)
                self.opt_ok.add(id(s.value))
                if f.ret.endswith("?") or self.is_optional_value(s.value):
                    self.check_store(s.value, self.param_type(f.ret), s)
                elif f.ret in self.records or f.ret in self.ifaces:
                    self.same_record(s.value, f.ret, s)
                continue
            if kind is Do and s.var in fixed:
                self.refuse(f"a loop over the constant or parameter '{s.var}'", s)
            if kind in (If, Do, DoWhile, Stop, Exit, Cycle, Comment, Break):
                continue
            if kind is SelectType:
                self.check_select_type(s)
                continue
            if kind is Select:
                self.check_select(s.subject, [c.values for c in s.cases], s)
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
                if s.init is not None:
                    self.opt_ok.add(id(s.init))
                if t.optional and s.init is not None:
                    self.check_store(s.init, t, s)
                elif isrec(t) and s.init is not None:
                    self.same_record(s.init, s.type, s, t.rank)
                elif s.init is not None and self.is_optional_value(s.init):
                    self.check_store(s.init, t, s)
            elif kind is Assign:
                root = s.target
                self.opt_ok.update((id(root), id(s.value)))
                if isinstance(root, FieldAccess):
                    name = self.field_root(root, s)
                    if self.types.get(id(root)) is None or not self.kind_ok(self.types[id(root)]):
                        self.refuse("a field of a type that is not handled", s)
                    ft = self.types[id(root)]
                    if ft.rank != 0:
                        self.refuse("an assignment to a whole array field", s)
                    if root.args is not None:
                        if not self.field_element(root):
                            self.refuse("a field with subscripts", s)
                    b = root.base
                    while isinstance(b, FieldAccess) or isinstance(b, Var):      # not through a record that may be absent
                        if self.types.get(id(b)) is not None and self.types[id(b)].optional:
                            self.refuse("an assignment through a record that may be absent", s)
                        if isinstance(b, Var):
                            break
                        b = b.base
                    if ft.optional or self.is_optional_value(s.value):
                        self.check_store(s.value, ft, s)
                    elif isrec(ft):
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
                if root.name in self.typevars:
                    self.refuse(f"an assignment to '{root.name}', the copy a select type or `is` made", s)
                tt = self.vars_type(root.name, body, f)
                if tt is not None and (tt.optional or self.is_optional_value(s.value)):
                    if isinstance(root, Apply):
                        self.refuse("a subscript on an optional value", s)
                    self.check_store(s.value, tt, s)
                elif isrec(tt):
                    if isinstance(root, Apply):
                        if tt.rank != 1 or not self.record_element(root):
                            self.refuse("a subscript on a record", s)
                        self.same_record(s.value, tt.name, s)
                    else:
                        self.same_record(s.value, tt.name, s, tt.rank)
                if tt is not None and tt.kind == "string" and not tt.optional and not self.is_optional_value(s.value):
                    vt = self.types.get(id(s.value))
                    if isinstance(root, Apply):
                        self.refuse("an assignment to part of a text", s)
                    if vt is None or vt.kind != "string" or vt.rank != 0:
                        self.refuse("a value that is not text stored in a text", s)
            elif kind is Print:
                if s.to is not None:
                    self.refuse("printing to a file", s)
                self.opt_ok.update(id(a) for a in s.args)
            elif kind is Call:
                callee, args = self.call_target(s)          # (not `f`: that is the procedure this body belongs to)
                if callee is None:
                    self.refuse(f"the call of '{s.name}'", s)
                if mark_problem(callee, s):
                    self.refuse(f"a call of '{s.name}' that the interpreter rejects", s)
                self.opt_ok.update(id(a) for a in args)
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

    def is_optional_value(self, e):
        t = self.types.get(id(e))
        return t is not None and (t.optional or t.kind == "null")

    def coalesce_ok(self, e):
        """`a ?? b`: `a` optional, and `b` of the same kind (or a literal number for a real or int32: converted as stored)."""
        lt, rt = self.types[id(e.left)], self.types[id(e.right)]
        if base_name(lt) != base_name(rt) and not (isinstance(e.right, Num) and lt.kind in ("real", "real32", "int32")
                                                    and rt.kind in ("int", "real")):
            return False
        return self.types[id(e)].kind == lt.kind

    def check_store(self, value, target, node):
        """`value` can be stored in a place of type `target` when one of them may be absent: `null` only into an optional place,
        an optional value only where the base type is the same (an absent one stops the program at run time, as the
        interpreter does), a plain value as it always could."""
        vt = self.types.get(id(value))
        if vt is None or vt.rank != 0 or not self.kind_ok(target):
            self.refuse("a value that does not fit an optional place", node)
        if vt.kind == "null":
            if not target.optional:
                self.refuse("null stored in a plain place", node)
            return
        if vt.optional:
            if base_name(vt) != base_name(target) or (target.kind not in ("record", "interface") and vt.kind != target.kind):
                self.refuse("an optional value of another type", node)
            return
        if target.kind == "record":
            self.same_record(value, target.name, node)
        else:
            self.fits(value, target.kind, "an optional place", node)

    def same_record(self, value, name, node, rank=0):
        """`value` must be a record of the type `name` (anything else is the interpreter's runtime error: refused). Stored in an
        array of records (rank 1) it may also be a single one, which fills the array."""
        t = self.types.get(id(value))
        if name in self.ifaces:
            ok = t is not None and t.rank == 0 and ((t.kind == "record" and t.name in self.members[name]) or
                                                   (t.kind == "interface" and t.name == name))
            if not ok or (rank == 1 and t.kind == "interface"):          # (an element's own value is what is stored, not a whole array)
                self.refuse(f"a value that is not a {name} (or a record that fits it)", node)
            return
        if t is None or t.kind != "record" or t.name != name or t.rank not in ((0, 1) if rank == 1 else (0,)):
            self.refuse(f"a value that is not a {name}", node)

    FITS = {"int": ("int", "int32"), "int32": ("int", "int32"), "real": ("int", "int32", "real", "real32"),
            "real32": ("int", "int32", "real", "real32"), "logical": ("logical",), "string": ("string",)}

    def fits(self, value, kind, what, node):
        """A single value of the kind `value` has can be stored in `what` (a field) of `kind`, as the interpreter does."""
        t = self.types.get(id(value))
        if t is None or t.rank != 0 or t.kind not in self.FITS.get(kind, ()):
            self.refuse(f"a value that does not fit {what}", node)

    @staticmethod
    def is_method_call(e):
        return isinstance(e, FieldAccess) and e.args is not None and not e.bracket

    def field_source(self, e, node):
        """What a chain of fields `a.b.c` read starts at: a variable (its name), or a call that gives a record."""
        while isinstance(e, FieldAccess) and not self.is_method_call(e):
            if e.chain or (e.args is not None and not self.field_element(e)):
                self.refuse("a field with subscripts", node)
            e = e.base
        if isinstance(e, Var):
            return e.name
        if self.is_method_call(e) or (isinstance(e, Apply) and not e.bracket):
            return None
        if self.record_element(e):
            return None
        self.refuse("a field of an element", node)

    def record_element(self, e):
        """Is `e` one element `a[i]` of an array of records (a variable `a`, a single subscript that is not a range)?"""
        if not (isinstance(e, Apply) and e.bracket and not e.chain and len(e.args) == 1 and not isinstance(e.args[0], Slice)):
            return False
        t = self.types.get(id(e))
        return isrec(t) and t.rank == 0

    def field_element(self, e):
        """Is `e` one element `a.f[i]` of an array field (a single subscript that is not a range)?"""
        return (isinstance(e, FieldAccess) and e.args is not None and e.bracket and not e.chain and len(e.args) == 1
                and not isinstance(e.args[0], Slice) and self.field_array(e))

    def field_array(self, e):
        """Is the field `e` names an array field of a record (its size is written in the record)?"""
        bt = self.types.get(id(e.base))
        return bt is not None and bt.kind == "record" and bt.rank == 0 and self.field_size(bt.name, e.field) is not None

    def field_root(self, e, node):
        """The variable a chain of fields `a.b.c` starts at (anything but a plain variable is refused)."""
        while isinstance(e, FieldAccess):
            if e.call or e.chain or (e.args is not None and not self.field_element(e)):
                self.refuse("a field with subscripts or a record's function", node)
            e = e.base
        if self.record_element(e):
            return e.name
        if not isinstance(e, Var):
            self.refuse("a field of a computed value or of an element", node)
        return e.name

    def check_call_args(self, f, args, node):
        for a, (typ, _, intent) in zip(args, f.params):
            at, pt = self.types.get(id(a)), self.param_type(typ)
            if pt is not None and (pt.optional or self.is_optional_value(a)):
                if intent != "in":
                    self.refuse(f"the call of '{f.name}' with an optional value passed as {intent}", node)
                self.check_store(a, pt, node)
                continue
            if pt is not None and (pt.kind == "string" or (at is not None and at.kind == "string")):
                if at is None or at.kind != pt.kind or at.rank != pt.rank:
                    self.refuse(f"the call of '{f.name}' with an argument that is not what the parameter takes", node)
                continue
            if pt is not None and pt.kind == "interface":
                if pt.rank == 1:                                 # a whole array: the same interface
                    ok = at is not None and at.kind == "interface" and at.name == pt.name and at.rank == 1
                else:
                    ok = at is not None and at.rank == 0 and ((at.kind == "record" and at.name in self.members[pt.name]) or
                                                              (at.kind == "interface" and at.name == pt.name))
                if not ok:
                    self.refuse(f"the call of '{f.name}' with an argument that is not a {pt.name} (or a record that fits it)", node)
                continue
            if pt is not None and pt.kind == "record":
                if at is None or at.kind != "record" or at.name != pt.name or at.rank != pt.rank:
                    self.refuse(f"the call of '{f.name}' with an argument that is not a {pt}", node)
                continue
            if at is None or pt is None or (at.rank != pt.rank) or (at.kind != pt.kind and at.rank > 0):
                self.refuse(f"the call of '{f.name}' with an argument that is not what the parameter takes", node)

    def call_target(self, s):
        """The procedure a call statement runs and its arguments (a record's function gets the record first, as `self`)."""
        if s.receiver is None:
            return self.unit.functions.get(s.name), list(s.args)
        rt = self.types.get(id(s.receiver))
        if rt is None or not isrec(rt) or rt.rank != 0:
            return None, []
        f = self.iface_function(rt.name, s.name) if rt.kind == "interface" else self.methods.get((rt.name, s.name))
        return f, [s.receiver] + list(s.args)

    @staticmethod
    def arity_ok(f, n):
        """A call gives every parameter a value, except that optional ones at the end may be left off."""
        return n == len(f.params) or (n < len(f.params) and all(isinstance(typ, str) and typ.endswith("?") for typ, _, _ in f.params[n:]))

    def check_aliasing(self, s, fixed=()):
        f, args = self.call_target(s)
        if not self.arity_ok(f, len(args)):
            self.refuse(f"a call of '{s.name}' with missing arguments", s)
        self.check_call_args(f, args, s)
        roots = [self.root_name(a) for a in args]
        for a, (typ, pname, intent) in zip(args, f.params):
            if intent in ("out", "inout") and not isinstance(a, (Var, FieldAccess, Apply)):
                self.refuse(f"a computed value passed as {intent}", s)
            if intent == "out" and isinstance(a, FieldAccess):
                self.refuse("a field passed as out", s)
            if intent == "inout" and isinstance(a, FieldAccess):
                self.field_root(a, s)
            if intent == "out" and (self.param_type(typ) or Type("int")).kind in ("record", "interface", "string"):
                self.refuse("a record or text passed as out", s)
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
        if d.type.endswith("?"):                                            # `int? :: n`, `address? :: home`
            base = d.type[:-1]
            if d.dims is not None or d.allocatable or d.const:
                return None
            if base in KINDS or base == "string":
                return Type(base, 0, True)
            return Type("record", 0, True, base) if base in self.records else None
        if d.type in self.ifaces:
            if d.dims is not None and len(d.dims) == 1 and not d.allocatable and not d.const:
                return Type("interface", 1, False, d.type)
            return None if d.dims is not None or d.allocatable or d.const else Type("interface", 0, False, d.type)
        if d.type in self.records:
            if d.dims is not None and len(d.dims) == 1 and not d.allocatable and not d.const:      # a fixed-size array of records
                return Type("record", 1, False, d.type)
            return None if d.dims is not None or d.allocatable or d.const else Type("record", 0, False, d.type)
        if d.type == "string":
            return None if d.dims is not None or d.allocatable or d.const else Type("string")
        if d.type not in KINDS:
            return None
        rank = len(d.dims) if d.dims is not None else 0
        if rank > MAX_RANK:
            return None
        return Type(d.type, rank)

    STATEMENTS = (Decl, Assign, If, Do, DoWhile, Print, Call, Return, Stop, Exit, Cycle, Comment, Allocate, Deallocate, SelectType, Select, Break)

    @staticmethod
    def allocs_of(body, f):
        return {d.name for d in statements_in(body) if isinstance(d, Decl) and d.allocatable}

    def check_expr(self, e, strings):
        kind = type(e)
        if id(e) in self.skip:                          # the `:` of an allocatable array's declaration
            return
        if kind in self.STATEMENTS:
            return
        t0 = self.types.get(id(e))
        if t0 is not None and (t0.optional or t0.kind == "null") and id(e) not in self.opt_ok:
            self.refuse("an optional value (or null) where a plain value is needed", e)
        if kind is Str:
            if not all(0 < ord(ch) < 128 for ch in e.value):
                self.refuse("text with a character outside ASCII", e)
            return
        if kind is Slice:                               # the parts of a section: checked as the expressions they are
            return
        if kind in (Case, Arm):                         # a case of a select (checked with the statement or expression)
            return
        if kind is SelectExpr:
            t = self.types.get(id(e))
            if t is None or t.rank != 0 or t.kind not in (*KINDS, "string") or any(
                    self.types.get(id(a.value)) != t for a in e.arms):
                self.refuse("a select expression whose arms are not all one kind of single value", e)
            self.check_select(e.subject, [a.values for a in e.arms], e)
            return
        if kind is IsType:
            self.check_type_test(e)
            return
        if kind is NamedArg:                            # `x = 1.0` in a record's constructor: its value is checked itself
            return
        if kind is FieldAccess and e.args is not None and not e.bracket:          # x.area(): a record's function
            bt = self.types.get(id(e.base))
            f = None
            if bt is not None and bt.optional:
                self.refuse("a function called on a record that may be absent", e)
            if bt is not None and bt.rank == 0 and bt.kind == "record":
                f = self.methods.get((bt.name, e.field))
            elif bt is not None and bt.rank == 0 and bt.kind == "interface":
                f = self.iface_function(bt.name, e.field)
            if f is None or f.ret is None or len(e.args) + 1 != len(f.params) or e.chain:
                self.refuse(f"the call of '{e.field}' on a record", e)
            self.check_call_args(f, [e.base] + list(e.args), e)
            t = self.types.get(id(e))
            if not self.kind_ok(t):
                self.refuse(f"a result of type {t if t is not None else 'unknown'}", e)
            return
        if kind is FieldAccess and not self.is_method_call(e) and self.types.get(id(e.base)) is not None and self.types[id(e.base)].optional:
            if self.dotted(e.base) is None:
                self.refuse("a field of an optional record that is not a variable or a field of one", e)
            self.opt_ok.add(id(e.base))
        if kind is FieldAccess:
            src = self.field_source(e, e)
            t = self.types.get(id(e))
            if not self.kind_ok(t) or t.rank != 0:
                if id(e) not in self.skip:
                    self.refuse(f"a field of type {t if t is not None else 'unknown'}", e)
                return
            if e.args is not None:                      # one element of an array field: the subscript is a whole number
                at = self.types.get(id(e.args[0]))
                if at is None or at.rank != 0 or at.kind not in ("int", "int32"):
                    self.refuse("an array field subscript that is not a whole number", e)
            if t.kind == "interface" and self.dotted(e) is None:
                self.refuse("an interface field of a computed value", e)
            return
        if kind is Null:
            return
        if kind not in (Num, Bool, Var, Unary, Binary, Apply, ArrayLit, MatrixLit):
            if kind is IsType:
                self.refuse("a type test", e)
            self.refuse(f"a {kind.__name__}", e)
        if kind in (Unary, Binary):
            operands = (e.operand,) if kind is Unary else (e.left, e.right)
            if any(self.is_optional_value(x) for x in operands):             # which operators take an optional operand
                kinds_ = {self.types[id(x)].kind for x in operands}
                numeric = all(self.types[id(x)].kind in ("int", "int32", "real", "real32") for x in operands)
                if kind is Unary:
                    ok = e.op in ("-", "not") and self.types[id(e.operand)].kind != "null"
                elif e.op == "??":
                    ok = self.types[id(e.left)].optional and self.types[id(e.right)].kind != "null" and \
                        self.coalesce_ok(e)
                elif e.op in ("==", "/="):
                    ok = "null" in kinds_ or (numeric or kinds_ <= {"string", "logical"})
                else:
                    ok = e.op in ("+", "-", "*", "/", "**", "<", "<=", ">", ">=") and numeric
                if not ok:
                    self.refuse(f"an optional operand of '{e.op}'", e)
                self.opt_ok.update(id(x) for x in operands)
            for x in ((e.operand,) if kind is Unary else (e.left, e.right)):
                xt = self.types.get(id(x))
                if isrec(xt) and e.op != "??":
                    self.refuse(f"a record with the operator '{e.op}'", e)
            kinds = {self.types[id(x)].kind if self.types.get(id(x)) is not None else None
                     for x in ((e.operand,) if kind is Unary else (e.left, e.right))}
            if "string" in kinds:                       # text: joined with +, compared; anything else is the checker's error
                ok = kind is Binary and e.op in ("+", "==", "/=", "<", "<=", ">", ">=", "??") and (kinds == {"string"} or (e.op in ("==", "/=", "??") and kinds <= {"string", "null"}))
                if not ok:
                    self.refuse(f"text with the operator '{e.op}'", e)
        if kind is Unary and e.op not in ("-", "+", "not"):
            self.refuse(f"the operator '{e.op}'", e)
        if kind is Binary and e.op not in ("+", "-", "*", "/", "**", "==", "/=", "<", "<=", ">", ">=", "and", "or", "??"):
            self.refuse(f"the operator '{e.op}'", e)
        if kind is MatrixLit and isrec(self.types.get(id(e))):
            self.refuse("a matrix of records", e)
        if kind in (ArrayLit, MatrixLit):
            items = e.items if kind is ArrayLit else [x for row in e.rows for x in row]
            if any(self.types.get(id(x)) is None or self.types[id(x)].rank != 0 or self.types[id(x)].kind == "string"
                   for x in items) or not items:
                self.refuse("an array literal of anything but single values", e)
        if kind is Apply:
            if e.chain:
                self.refuse("a chained subscript", e)
            if any(isinstance(a, Slice) for a in e.args) and not e.bracket:
                self.refuse("part of an array", e)
            if e.bracket:
                rt = self.types.get(id(e))
                if isrec(rt) and not self.record_element(e):
                    self.refuse("part of an array of records", e)
                if rt is not None and rt.kind == "string":                  # s[i] or s[lo:hi] of a text variable
                    a = e.args[0] if len(e.args) == 1 else None
                    if a is None or (isinstance(a, Slice) and a.step is not None):
                        self.refuse("a part of a text written any other way than s[i] or s[lo:hi]", e)
                    for x in ([a.lo, a.hi] if isinstance(a, Slice) else [a]):
                        xt = self.types.get(id(x)) if x is not None else None
                        if x is not None and (xt is None or xt.rank != 0 or xt.kind not in ("int", "int32")):
                            self.refuse("a text index that is not a whole number", e)
            elif e.name in self.records:
                self.opt_ok.update(id(a.value if isinstance(a, NamedArg) else a) for a in e.args)
                self.check_constructor(e)
            elif e.name in self.unit.functions:
                self.opt_ok.update(id(a) for a in e.args)
                self.check_call_args(self.unit.functions[e.name], e.args, e)
            elif e.name not in BUILTINS:
                self.refuse(f"the function '{e.name}'", e)
            elif e.name == "present":
                if len(e.args) != 1 or self.types.get(id(e.args[0])) is None or self.types[id(e.args[0])].rank != 0:
                    self.refuse("present() of anything but one single value", e)
                self.opt_ok.add(id(e.args[0]))
            else:
                for a in e.args:
                    at = self.types.get(id(a))
                    if isrec(at) and at.rank == 1 and e.name == "size":
                        continue
                    if at is not None and (isrec(at) or (at.kind == "string" and e.name not in STRFUNCS | FORMATFUNCS)):
                        self.refuse(f"{'a record' if at.kind == 'record' else 'text'} as an argument of '{e.name}'", e)
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
            ft = self.fbase(by_name[fname])
            if by_name[fname].dims is not None:
                self.refuse(f"a call of {e.name}() giving the array field '{fname}'", e)
            if self.fopt(by_name[fname]) or self.is_optional_value(value):
                if not self.fopt(by_name[fname]):
                    self.refuse(f"a call of {e.name}() giving an optional value to the plain field '{fname}'", e)
                self.check_store(value, self.field_type(by_name[fname]), e)
            elif ft in self.records or ft in self.ifaces:
                self.same_record(value, ft, e)
            else:
                self.fits(value, ft, f"the field '{fname}'", e)

    def check_string_function(self, e):
        """The text functions: the arguments must be exactly what the function takes (anything else is a run-time error
        the compiler does not reproduce)."""
        name = e.name
        kinds = [self.types[id(a)].kind if self.types.get(id(a)) is not None and self.types[id(a)].rank == 0 else None for a in e.args]
        shapes = {"trim": ["string"], "upper": ["string"], "lower": ["string"], "len": ["string"], "len_trim": ["string"],
                  "repeat": ["string", ("int", "int32")], "contains": ["string", "string"], "starts_with": ["string", "string"],
                  "ends_with": ["string", "string"], "index": ["string", "string"]}[name]
        if name == "index" and len(e.args) == 3:
            shapes = shapes + ["logical"]
        if len(kinds) != len(shapes) or any(k is None or k not in ((w,) if isinstance(w, str) else w) for k, w in zip(kinds, shapes)):
            self.refuse(f"{name}() of anything but what it takes", e)

    def check_format_function(self, e):
        """text, tf, right, left, fixed, scientific, general: single values of the kinds each one takes."""
        name, args = e.name, e.args
        kinds = [self.types[id(a)].kind if self.types.get(id(a)) is not None and self.types[id(a)].rank == 0 else None for a in args]
        ints, numbers = ("int", "int32"), ("int", "int32", "real", "real32")
        shown = ("int", "int32", "real", "real32", "logical", "string")
        shapes = {"text": [shown], "tf": [("logical",)], "right": [shown, ints, ("string",)], "left": [shown, ints, ("string",)],
                  "fixed": [numbers, ints, ints], "scientific": [numbers, ints, ints, ("string",)],
                  "general": [numbers, ints, ints, ("string",)]}[name]
        least = {"text": 1, "tf": 1, "right": 2, "left": 2, "fixed": 2, "scientific": 2, "general": 2}[name]
        if not least <= len(args) <= len(shapes) or any(k not in w for k, w in zip(kinds, shapes)):
            self.refuse(f"{name}() of anything but what it takes", e)
        if name in ("scientific", "general") and len(args) == 4 and not (isinstance(args[3], Str) and args[3].value == "fortran"):
            self.refuse(f"{name}() with a style other than \"fortran\"", e)

    def check_builtin(self, e):
        name = e.name
        if name == "allocated":
            if len(e.args) != 1 or not isinstance(e.args[0], Var) or e.args[0].name not in self.allocs:
                self.refuse("allocated() of anything but the name of an allocatable array", e)
            return
        ranks = [self.types[id(a)].rank for a in e.args]
        if name in STRFUNCS:
            self.check_string_function(e)
            return
        if name in FORMATFUNCS:
            self.check_format_function(e)
            return
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
            a = e.args[0]
            if isinstance(a, FieldAccess):                 # the array field of a record: its size is written in the record
                if a.args is not None or self.field_source(a, e) is None or not self.field_array(a):
                    self.refuse("size() of anything but a whole array field", e)
                self.skip.add(id(a))
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

    def tmp_str(self, expr):
        """A new text temporary that owns what `expr` gives, freed after the statement."""
        self.n += 1
        name = f"t{self.n}"
        self.emit(f"rt_str {name} = {expr};")
        self.temps.append(("str", name))
        return name

    def cond(self, e):
        """The condition `e` as a C boolean variable. Its temporaries live (and are freed) in a block of their own, because
        the conditions of an `elif` or a `do while` are evaluated in places the statement's own cleanup cannot reach."""
        self.n += 1
        name = f"c{self.n}"
        self.emit(f"bool {name};")
        self.emit("{")
        self.depth += 1
        saved, self.temps = self.temps, []
        if isinstance(e, IsType):
            self.emit(f"{name} = {self.type_test(e)};")
        else:
            self.emit(f"{name} = {self.scalar(e, 'logical')};")
        for k, t in self.temps:
            self.emit(f"rt_free_{k}(&{t});")
        self.temps = saved
        self.depth -= 1
        self.emit("}")
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
        if v.t.optional or v.t.kind == "null":
            return v
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
        if t is Null:
            return V(NULL, "0")
        if t is Str:
            return V(Type("string"), f"RT_LIT({c_string(e.value)})")
        if t is Var:
            if e.name == "pi" and "pi" not in self.vars:         # the language's own, until a program declares one
                return V(Type("real"), "3.141592653589793")
            if e.name in self.allocs:
                self.emit(f'rt_need_alloc({self.vars[e.name][1]}.data, "{e.name}", {self.line});')
            if e.name in self.outs:
                self.emit(f'if (!*set_{e.name}) rt_fail({self.line}, "\'{e.name}\' is used before it is assigned");')
            if self.vars[e.name][0].kind == "interface" and self.vars[e.name][0].rank == 0:
                self.check_assigned(self.vars[e.name][1], e.name, False)
            return V(self.vars[e.name][0], self.vars[e.name][1])
        if t is Unary:
            return self.unary(e)
        if t is Binary:
            return self.binary(e)
        if t is Apply:
            return self.apply(e)
        if t is FieldAccess and e.args is not None and not e.bracket:
            bt = self.types[id(e.base)]
            f = self.iface_function(bt.name, e.field) if bt.kind == "interface" else self.methods[(bt.name, e.field)]
            return self.call_function(f, [e.base] + list(e.args), self.line)
        if t is FieldAccess and self.types[id(e.base)].optional:         # a field of a record that may be absent
            ft = self.types[id(e)]
            b = self.value(e.base)
            message = "'" + self.dotted(e.base) + "' is null, so it has no fields (check with present())"
            self.emit(f"if (!{b.atom}.has) rt_fail({self.line}, {c_string(message)});")
            place = f"{b.atom}.v.m_{e.field}"
            if e.args is not None:
                i = self.tmp("int64_t", self.scalar(e.args[0], "int"))
                self.emit(f"rt_check_index({i}, {self.field_size(self.types[id(e.base)].name, e.field)}, 1, 1, {self.line});")
                place += f"[{i} - 1]"
            return V(ft, self.tmp(cscalar(ft), place))
        if t is FieldAccess:
            ft = self.types[id(e)]
            if self.field_source(e, e) is None:                    # a field of what a call gives
                base = self.value(e.base)
                place = f"{base.atom}.m_{e.field}"
                if e.args is not None:
                    i = self.tmp("int64_t", self.scalar(e.args[0], "int"))
                    self.emit(f"rt_check_index({i}, {self.field_size(self.types[id(e.base)].name, e.field)}, 1, 1, {self.line});")
                    place += f"[{i} - 1]"
                res = self.tmp(cscalar(ft), place)
            else:
                res = self.tmp(cscalar(ft), self.field_lvalue(e))
            if ft.kind == "interface":                              # a field that was never given a value
                self.emit(f'if ({res}.tag == 0) rt_fail({self.line}, "field \'{self.dotted(e)}\' is used before it is assigned");')
            return V(ft, res)
        if t in (ArrayLit, MatrixLit):
            return self.array_literal(e)
        if t is SelectExpr:
            return self.select_expr(e)
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
        if v.t.optional:
            v = self.unwrap(v, f"'{e.op}' cannot be used with null; check with present() or use ??")
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
        if op == "??":
            return self.coalesce(e)
        flags = (1 if self.types[id(e.left)].kind == "int" else 0) | (2 if self.types[id(e.right)].kind == "int" else 0)
        lv, rv = self.value(e.left, exact=True), self.value(e.right, exact=True)
        if any(v.t.optional or v.t.kind == "null" for v in (lv, rv)):
            return self.optional_binary(op, lv, rv, flags, line)
        return self.binary_values(op, lv, rv, flags, line)

    def optional_binary(self, op, lv, rv, flags, line):
        """An operator with an optional operand: `==` and `/=` say whether both are absent or both are present and equal; any
        other stops when an operand is absent."""
        has = lambda v: f"{v.atom}.has" if v.t.optional else ("false" if v.t.kind == "null" else "true")
        plain = lambda v: V(Type(v.t.kind, 0, False, v.t.name), f"{v.atom}.v") if v.t.optional else v
        if op in ("==", "/="):
            res = self.tmp("bool", "false")
            self.emit(f"if ({has(lv)} && {has(rv)}) {{")
            self.depth += 1
            if lv.t.kind != "null" and rv.t.kind != "null":
                inner = self.binary_values(op, plain(lv), plain(rv), flags, line)
                self.emit(f"{res} = {inner.atom};")
            self.depth -= 1
            self.emit(f"}} else {{ {res} = ({has(lv)} {'==' if op == '==' else '!='} {has(rv)}); }}")
            return V(Type("logical"), res)
        need = " && ".join(f"{v.atom}.has" for v in (lv, rv) if v.t.optional)
        self.emit(f'if (!({need})) rt_fail({line}, {c_string(f"operator {op!r} cannot be used with null; check with present() or use ??")});')
        return self.binary_values(op, plain(lv), plain(rv), flags, line)

    def coalesce(self, e):
        """`a ?? b`: `a` when it has a value, else `b` (which is only worked out then)."""
        rt, line = self.types[id(e)], self.line
        lv = self.value(e.left)
        if not lv.t.optional:                                  # a plain value is never absent
            return lv
        self.n += 1
        res = f"t{self.n}"
        sk = skind(rt)
        self.emit(f"{cscalar(rt)} {res} = {{0}};")
        if rt.kind == "string" and not rt.optional:
            self.temps.append(("str", res))
        elif self.deep.get(sk):
            self.temps.append((f"rec_{sk}", res))
        self.emit(f"if ({lv.atom}.has) {{")
        self.depth += 1
        self.store(res, lv.atom if rt.optional else f"{lv.atom}.v", sk)
        self.depth -= 1
        self.emit("} else {")
        self.depth += 1
        saved, self.temps = self.temps, []
        self.store(res, self.coerce(self.value(e.right), rt, "the fallback of ??"), sk)
        for k, name in self.temps:
            self.emit(f"rt_free_{k}(&{name});")
        self.temps = saved
        self.depth -= 1
        self.emit("}")
        return V(rt, res)

    def binary_values(self, op, lv, rv, flags, line):
        compare = op in ("==", "/=", "<", "<=", ">", ">=")
        logic = op in ("and", "or")
        if lv.t.kind == "string":
            if op == "+":
                return V(Type("string"), self.tmp_str(f"rt_str_cat({lv.atom}, {rv.atom}, {line})"))
            return V(Type("logical"), self.tmp("bool", f"(rt_str_cmp({lv.atom}, {rv.atom}) {'!=' if op == '/=' else op} 0)"))
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
        if kind == "record":
            name = self.types[id(e)].name
            res = self.tmp_array(f"ra_{name}", 1)
            self.emit(f"rt_alloc_ra_{name}(&{res}, 1, {len(e.items)}, 1, 1, {self.line});")
            for n, x in enumerate(e.items):                                 # worked out in order, then each stored as a copy
                atom = self.value(x).atom
                self.store(f"{res}.data[{n}]", atom, name)
            return V(Type("record", 1, False, name), res)
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
        if isrec(t):                                                # a view of the element, not a copy
            if t.kind == "interface":
                self.check_assigned(f"{arr}.data[{off}]", e.name, True)
            return V(Type(t.kind, 0, False, t.name), self.tmp(f"r_{t.name}", f"{arr}.data[{off}]"))
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
                lv = self.lvalue(a)
                if pt.kind == "interface":
                    self.check_assigned(lv, getattr(a, "name", None), isinstance(a, Apply))
                ref = "&" + lv
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
                out.append(self.typed(a, pt, f"argument '{pname}'"))
            else:
                v = self.value(a)
                out.append(v.atom)
        for typ, pname, intent in f.params[len(args):]:               # optional parameters left off arrive absent
            out.append(f"(({cscalar(self.param_type(typ))}){{0}})")
        return out

    def dotted(self, e):
        """`sc.items` for the field `items` of the variable `sc`, however it is subscripted (what the interpreter calls such a
        field in a message); None when the chain does not start at a variable or an element of one."""
        names = []
        while isinstance(e, FieldAccess):
            if self.is_method_call(e):
                return None
            names.append(e.field)
            e = e.base
        if isinstance(e, Var) or (isinstance(e, Apply) and e.bracket):
            return ".".join([e.name] + names[::-1])
        return None

    def field_lvalue(self, e):
        """The C lvalue of `a.b.c` (a chain of fields of a variable)."""
        if isinstance(e, Var):
            return self.vars[e.name][1]
        if isinstance(e, Apply):                                      # an element of an array of records
            return self.lvalue(e)
        base = self.field_lvalue(e.base)
        if e.args is not None:                                        # an element of an array field: checked against its size
            bt = self.types[id(e.base)]
            i = self.tmp("int64_t", self.scalar(e.args[0], "int"))
            self.emit(f"rt_check_index({i}, {self.field_size(bt.name, e.field)}, 1, 1, {self.line});")
            return f"{base}.m_{e.field}[{i} - 1]"
        return f"{base}.m_{e.field}"

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
            ftp = self.field_type(by_name[fname])
            atom = self.typed(value, ftp, f"field '{fname}' of {e.name}")
            self.store(f"{res}.m_{fname}", atom, skind(ftp))
        if self.deep[e.name]:
            self.temps.append((f"rec_{e.name}", res))
        return V(Type("record", 0, False, e.name), res)

    def convert(self, v, target):
        """The C value of `v` for a place of type `target`: a record that goes into an interface is wrapped with its tag
        (a view of the record, which the store then copies)."""
        if target.kind == "interface" and v.t.kind == "record":
            tag = self.members[target.name].index(v.t.name) + 1
            return f"((r_{target.name}){{.tag = {tag}, .u.m_{v.t.name} = {v.atom}}})"
        return v.atom

    def typed(self, e, target, what=""):
        """The C value of `e` for a place of type `target`."""
        return self.coerce(self.value(e), target, what)

    def coerce(self, v, target, what=""):
        """The C value of `v` for a place of type `target`: an optional place takes a value (wrapped), `null` or an optional
        value of its own type; a plain place takes a value, or an optional one that must not be absent."""
        if target.optional:
            ct = cscalar(target)
            if v.t.kind == "null":
                return f"(({ct}){{0}})"
            if v.t.optional:
                return v.atom
            atom = v.atom if v.t.kind == target.kind else self.narrow(v.atom, v.t.kind, target.kind, self.line, what)
            return f"(({ct}){{true, {atom}}})"
        if v.t.optional:
            name = base_name(target)
            prefix = what + ": " if what.startswith(("function ", "argument ")) else ""
            v = self.unwrap(v, f"{prefix}cannot assign null to {name} (declare it '{name}?' to allow null)",
                            "call_line" if what.startswith("function ") else None)       # (a result is checked at the call)
        if isrec(target):
            return self.convert(v, target)
        if v.t.rank != 0:
            raise NotCompiled("an array where a single value is needed", self.line)
        if v.t.kind != target.kind:
            return self.narrow(v.atom, v.t.kind, target.kind, self.line, what)
        return v.atom

    def unwrap(self, v, message, line=None):
        """The value inside the optional value `v`, after stopping with `message` if there is none."""
        self.emit(f"if (!{v.atom}.has) rt_fail({line or self.line}, {c_string(message)});")
        return V(Type(v.t.kind, 0, False, v.t.name), f"{v.atom}.v")

    def check_assigned(self, lv, name, element):
        """An interface value that was never given one cannot be used."""
        what = f"an element of '{name}'" if element else f"'{name}'"
        self.emit(f'if ({lv}.tag == 0) rt_fail({self.line}, "{what} is used before it is assigned");')

    def store(self, lv, atom, kind, line=0):
        """`lv = atom` for something of kind `kind` (a kind name, or a record name): a text or a record that owns text is
        copied, and what `lv` held is freed."""
        if kind == "string":
            self.emit(f"rt_str_set(&{lv}, {atom}, {line or self.line});")
        elif self.deep.get(kind):
            self.emit(f"rt_set_rec_{kind}(&{lv}, {atom});")
        else:
            self.emit(f"{lv} = {atom};")

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
        if e.bracket and self.types[id(e)].kind == "string":
            return self.string_part(e)
        if e.bracket:
            return self.element(e)
        if e.name in self.records:
            return self.constructor(e)
        if e.name in self.unit.functions:
            return self.call_function(self.unit.functions[e.name], e.args, line)
        return self.builtin(e)

    def string_part(self, e):
        """`s[i]` (one character) or `s[lo:hi]` (either end may be left off) of a text variable."""
        line = self.line
        base = self.vars[e.name][1]
        a = e.args[0]
        if not isinstance(a, Slice):
            i = self.scalar(a, "int")
            return V(Type("string"), self.tmp_str(f"rt_str_char({base}, {i}, {line})"))
        lo = self.tmp("int64_t", self.scalar(a.lo, "int") if a.lo is not None else "INT64_C(1)")
        hi = self.tmp("int64_t", self.scalar(a.hi, "int") if a.hi is not None else f"{base}.n")
        return V(Type("string"), self.tmp_str(f'rt_str_part({base}, {lo}, {hi}, "{e.name}", {line})'))

    def as_text(self, v):
        """(the text a single value shows as, whether the value was text)."""
        k, a, line = v.t.kind, v.atom, self.line
        if k == "string":
            return a, True
        call = {"int": f"rt_text_int({a}, {line})", "int32": f"rt_text_int({a}, {line})", "real": f"rt_text_real({a}, {line})",
                "real32": f"rt_text_real32({a}, {line})", "logical": f"rt_text_bool({a}, {line})"}[k]
        return self.tmp_str(call), False

    def format_function(self, e):
        """text, tf, right, left, fixed, scientific, general: the arguments are all worked out first, in order."""
        name, line = e.name, self.line
        fortran = len(e.args) == 4
        v = [self.value(a) for a in e.args[:3]]               # (a fourth argument is the style, a literal)
        if name == "text":
            t, is_text = self.as_text(v[0])
            return V(Type("string"), self.tmp_str(f"rt_str_copy({t}, {line})") if is_text else t)
        if name == "tf":
            return V(Type("string"), self.tmp_str(f'rt_str_cstr({v[0].atom} ? "T" : "F", {line})'))
        if name in ("right", "left"):
            t, is_text = self.as_text(v[0])
            width = self.cast(v[1].atom, v[1].t.kind, "int")
            fill = v[2].atom if len(v) == 3 else 'RT_LIT(" ")'
            return V(Type("string"), self.tmp_str(f'rt_pad("{name}", {t}, {"true" if is_text else "false"}, {width}, {fill}, {"true" if name == "right" else "false"}, {line})'))
        x = self.cast(v[0].atom, v[0].t.kind, "real")
        count = self.cast(v[1].atom, v[1].t.kind, "int")
        width = self.cast(v[2].atom, v[2].t.kind, "int") if len(v) > 2 else "INT64_MIN"
        if name == "fixed":
            return V(Type("string"), self.tmp_str(f"rt_fixed({x}, {count}, {width}, {line})"))
        return V(Type("string"), self.tmp_str(f"rt_{name}({x}, {count}, {width}, {'true' if fortran else 'false'}, {line})"))

    def string_function(self, e):
        """trim, upper, lower, repeat, len, len_trim, contains, starts_with, ends_with, index of text."""
        name, line = e.name, self.line
        v = [self.value(a) for a in e.args]
        if name in ("trim", "upper", "lower"):
            return V(Type("string"), self.tmp_str(f"rt_str_{name}({v[0].atom}, {line})"))
        if name == "repeat":
            return V(Type("string"), self.tmp_str(f"rt_str_repeat({v[0].atom}, {self.cast(v[1].atom, v[1].t.kind, 'int')}, {line})"))
        if name == "len":
            return V(Type("int"), self.tmp("int64_t", f"{v[0].atom}.n"))
        if name == "len_trim":
            return V(Type("int"), self.tmp("int64_t", f"rt_len_trim({v[0].atom})"))
        if name == "contains":
            return V(Type("logical"), self.tmp("bool", f"(rt_str_index({v[0].atom}, {v[1].atom}, false) > 0)"))
        if name in ("starts_with", "ends_with"):
            return V(Type("logical"), self.tmp("bool", f"rt_str_{'starts' if name == 'starts_with' else 'ends'}({v[0].atom}, {v[1].atom})"))
        back = v[2].atom if len(v) == 3 else "false"
        return V(Type("int"), self.tmp("int64_t", f"rt_str_index({v[0].atom}, {v[1].atom}, {back})"))

    def call_function(self, f, nodes, line):
        """A call of the function `f` (a record's function gets the record first) as a value."""
        if not self.arity_ok(f, len(nodes)):
            raise NotCompiled(f"a call of '{f.name}' with missing arguments", line)
        args = self.call_args(f, nodes)
        self.emit(f'rt_call_in({line}, "{f.name}");')
        rt = self.param_type(f.ret)
        call = f"{self.cnames[id(f)]}({', '.join(args)})"
        result = self.tmp_str(call) if rt.kind == "string" and not rt.optional else self.tmp(cscalar(rt), call)
        if (isrec(rt) or rt.optional) and self.deep[skind(rt)]:
            self.temps.append((f"rec_{skind(rt)}", result))
        self.emit("rt_call_out();")
        return V(rt, result)

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
        if name == "present":                                     # is there a value (a plain value always is)
            v = self.value(e.args[0])
            return V(Type("logical"), self.tmp("bool", f"{v.atom}.has" if v.t.optional else "false" if v.t.kind == "null" else "true"))
        if name == "size" and isinstance(e.args[0], FieldAccess) and e.args[0].args is None:       # the size written in the record
            a = e.args[0]
            n = self.field_size(self.types[id(a.base)].name, a.field)
            if len(e.args) == 1 or e.args[1].value == 1:
                return V(Type("int"), self.tmp("int64_t", f"INT64_C({n})"))
            raise NotCompiled("size() with that dimension", line)
        if name == "allocated":
            return V(Type("logical"), self.tmp("bool", f"({self.vars[e.args[0].name][1]}.data != NULL)"))
        if name == "atan" and len(e.args) == 2:
            name = "atan2"
        if name in CONSTANTS:
            return self.constant(e)
        if name in STRFUNCS:
            return self.string_function(e)
        if name in FORMATFUNCS:
            return self.format_function(e)
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

    def fill_records(self, dst, src, name):
        k = self.loop_var()
        self.emit(f"for (int64_t {k} = 0; {k} < {dst}.n; {k}++) {{")
        self.depth += 1
        self.store(f"{dst}.data[{k}]", src, name)
        self.depth -= 1
        self.emit("}")

    def copy_records(self, dst, src, name):
        """Store every element of the array of records `src` in `dst` (shapes already agree), each one a copy."""
        k = self.loop_var()
        self.emit(f"for (int64_t {k} = 0; {k} < {dst}.n; {k}++) {{")
        self.depth += 1
        self.store(f"{dst}.data[{k}]", f"{src}.data[{k}]", name)
        self.depth -= 1
        self.emit("}")

    def st_decl(self, s):
        t = self.vars[s.name][0]
        name = c_name(s.name)
        line = self.line
        self.emit(f"if (decl_{s.name}) rt_fail({line}, \"'{s.name}' already declared\"); decl_{s.name} = true;")
        if t.rank == 0:
            if t.optional:                                              # absent unless given a value
                atom = self.typed(s.init, t, f"line {line}") if s.init is not None else f"(({cscalar(t)}){{0}})"
                self.store(name, atom, skind(t))
            elif s.init is not None and t.kind == "string":
                self.emit(f"rt_str_set(&{name}, {self.typed(s.init, t, f'line {line}')}, {line});")
            elif s.init is not None:
                self.store(name, self.typed(s.init, t, f'line {line}'), skind(t))
            elif isrec(t) and self.deep[t.name]:
                self.emit(f"rt_free_rec_{t.name}(&{name}); {name} = d_{t.name}();")
            elif isrec(t):
                self.emit(f"{name} = d_{t.name}();")
            elif t.kind == "string":
                self.emit(f'rt_str_set(&{name}, RT_LIT(""), {line});')
            else:
                self.emit(f"{name} = {'false' if t.kind == 'logical' else '0'};")
            return
        if s.allocatable:                                              # not allocated (again, if this runs in a loop)
            self.emit(f"rt_free_{t.kind}(&{name});")
            return
        dims = [self.scalar(d, "int") for d in s.dims]
        dims = [self.tmp("int64_t", d) for d in dims]
        padded = dims + ["1"] * (3 - len(dims))
        self.emit(f"rt_alloc_{akind(t)}(&{name}, {t.rank}, {', '.join(padded)}, {line});")
        if isrec(t):
            if s.init is not None:
                v = self.value(s.init)
                if v.t.rank == 0:
                    self.fill_records(name, self.convert(v, t.element()), t.name)
                else:
                    self.emit(f'rt_init_shape_error({v.atom}.shape, {v.atom}.rank, {name}.shape, {name}.rank, "{s.name}", {line});')
                    self.copy_records(name, v.atom, t.name)
            return
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
            self.store(lv, self.typed(s.value, ft, f'line {line}'), skind(ft))
            return
        t = self.vars[target.name][0]
        if isinstance(target, Apply) and any(isinstance(a, Slice) for a in target.args):
            self.assign_section(s, t)
            return
        if isinstance(target, Apply):                                   # an element: its subscripts first
            lv = self.lvalue(target)
            self.store(lv, self.typed(s.value, t.element(), f'line {line}'), skind(t))
            return
        name = self.vars[target.name][1]
        if t.kind == "string" and not t.optional:
            self.emit(f"rt_str_set(&{name}, {self.typed(s.value, t, f'line {line}')}, {line});")
            return
        if t.rank == 0:
            self.store(name, self.typed(s.value, t, f'line {line}'), skind(t))
            if target.name in self.outs:
                self.emit(f"*set_{target.name} = true;")
            return
        v = self.value(s.value)
        if isrec(t) and v.t.rank == 0:                                  # one record in every element
            self.fill_records(name, self.convert(v, t.element()), t.name)
            return
        if isrec(t):                                          # an array of records: same shape, every element copied
            self.emit(f"rt_same_shape({name}.shape, {name}.rank, {v.atom}.shape, {v.atom}.rank, 1, {line});")
            self.copy_records(name, v.atom, t.name)
            return
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

    def with_copy(self, node, name, body):
        """Write `body` with `name` standing for the copy `node` made (a select type case or an `is` test)."""
        saved = self.vars.get(name)
        cname, t = self.tvars[id(node)]
        self.vars[name] = (t, cname)
        try:
            self.block(body)
        finally:
            if saved is None:
                del self.vars[name]
            else:
                self.vars[name] = saved

    def if_chain(self, branches, orelse):
        (cond, body), rest = branches[0], branches[1:]
        c = self.cond(cond)
        self.emit(f"if ({c}) {{")
        self.depth += 1
        if isinstance(cond, IsType) and cond.var:
            self.with_copy(cond, cond.var, body)
        else:
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

    def case_index(self, subject, groups):
        """Work out which group of case values the subject matches: a C int, the group's index, or -1 for none (a `default` group
        is the answer when nothing else matches). The subject's temporaries are freed here, before any body runs."""
        sel = self.tmp("int", "-1")
        self.emit("{")
        self.depth += 1
        saved, self.temps = self.temps, []
        v = self.value(subject)
        sv = self.tmp("int64_t" if v.t.kind in ("int", "int32") else CSCALAR[v.t.kind], f"(int64_t){v.atom}" if v.t.kind in ("int", "int32") else v.atom)
        default = None
        first = True
        for n, values in enumerate(groups):
            if values is None:
                default = n
                continue
            tests = []
            for item in values:
                tests.append(self.case_test(sv, v.t.kind, item))
            self.emit(f"{'' if first else 'else '}if ({' || '.join(tests)}) {sel} = {n};")
            first = False
        if default is not None:
            self.emit(f"{'' if first else 'else '}{sel} = {default};")
        for k, t in self.temps:
            self.emit(f"rt_free_{k}(&{t});")
        self.temps = saved
        self.depth -= 1
        self.emit("}")
        return sel

    def case_test(self, sv, kind, item):
        def lit(x):
            v = literal_value(x)
            if kind == "string":
                return f"RT_LIT({c_string(v)})"
            return "true" if v is True else "false" if v is False else f"INT64_C({v})"
        if kind == "string":
            cmp = lambda x, op: f"(rt_str_cmp({sv}, {lit(x)}) {op} 0)"
        elif kind == "logical":
            return f"({sv} == {lit(item)})"
        else:
            cmp = lambda x, op: f"({sv} {op} {lit(x)})"
        if not isinstance(item, Slice):
            return cmp(item, "==")
        parts = []
        if item.lo is not None:
            parts.append(cmp(item.lo, ">="))
        if item.hi is not None:
            parts.append(cmp(item.hi, "<="))
        return "(" + " && ".join(parts) + ")" if parts else "true"

    def st_select(self, s):
        sel = self.case_index(s.subject, [c.values for c in s.cases])
        self.n += 1
        label = f"brk{self.n}"
        self.selects.append(label)
        for i, c in enumerate(s.cases):
            guard = f"{sel} >= 0 && {sel} <= {i}" if s.flow else f"{sel} == {i}"     # fallthrough: the chosen body and each after it
            self.emit(f"if ({guard}) {{")
            self.depth += 1
            self.block(c.body)
            self.depth -= 1
            self.emit("}")
        self.selects.pop()
        self.emit(f"{label}: ;")

    def select_expr(self, e):
        t = self.types[id(e)]
        sel = self.case_index(e.subject, [a.values for a in e.arms])
        self.n += 1
        res = f"t{self.n}"
        if t.kind == "string":
            self.emit(f"rt_str {res} = {{0}};")
            self.temps.append(("str", res))
        else:
            self.emit(f"{CSCALAR[t.kind]} {res} = {'false' if t.kind == 'logical' else '0'};")
        for i, arm in enumerate(e.arms):               # only the chosen arm is worked out
            self.emit(f"if ({sel} == {i}) {{")
            self.depth += 1
            saved, self.temps = self.temps, []
            atom = self.scalar(arm.value, t.kind)
            if t.kind == "string":
                self.emit(f"rt_str_set(&{res}, {atom}, {self.line});")
            else:
                self.emit(f"{res} = {atom};")
            for k, name in self.temps:
                self.emit(f"rt_free_{k}(&{name});")
            self.temps = saved
            self.depth -= 1
            self.emit("}")
        return V(t, res)

    def tags_of(self, iname, name):
        """The tags (1, 2, ...) of the records in interface `iname` that `name` (a record or an interface) matches."""
        members = self.members[iname]
        if name in self.records:
            return [members.index(name) + 1] if name in members else []
        return [i + 1 for i, r in enumerate(members) if not conformance(self.records[r], self.ifaces[name])]

    def bind(self, var, t, sv, iname, tags):
        """Make the copy `var` (of type `t`) from the interface value `sv` (of interface `iname`), which holds one of `tags`."""
        if t.kind == "interface" and t.name == iname:
            self.store(var, sv, iname)
            return
        for tag in tags:
            r = self.members[iname][tag - 1]
            self.emit(f"if ({sv}.tag == {tag}) {{")
            self.depth += 1
            self.store(var, self.convert(V(Type("record", 0, False, r), f"{sv}.u.m_{r}"), t), t.name)
            self.depth -= 1
            self.emit("}")

    def type_test(self, e):
        """`x is circle as c`: whether `x` holds that record (or one that fits that interface); if so, `c` is made."""
        sub = self.value(e.subject)
        sv = self.tmp(f"r_{sub.t.name}", sub.atom)
        tags = self.tags_of(sub.t.name, e.type)
        holds = " || ".join(f"{sv}.tag == {t}" for t in tags) or "false"
        res = self.tmp("bool", f"({holds})")
        if e.var and tags:
            name, t = self.tvars[id(e)]
            self.emit(f"if ({res}) {{")
            self.depth += 1
            self.bind(name, t, sv, sub.t.name, tags)
            self.depth -= 1
            self.emit("}")
        return res

    def st_selecttype(self, s):
        sub = self.value(s.subject)
        iname = sub.t.name
        sv = self.tmp(f"r_{iname}", sub.atom)
        chosen = {}                                  # tag -> the case that runs: a record's own case, then the first interface it fits
        default = next(c for c in s.cases if c.values is None)
        for tag, r in enumerate(self.members[iname], 1):
            case = None
            for c in s.cases:
                if c.values == r:
                    case = c
            if case is None:
                for c in s.cases:
                    if c.values in self.ifaces and not conformance(self.records[r], self.ifaces[c.values]):
                        case = c
                        break
            chosen[tag] = case or default
        self.n += 1
        label = f"brk{self.n}"
        self.selects.append(label)
        first = True
        for c in s.cases:
            tags = [t for t, k in chosen.items() if k is c]
            if not tags and c is not default:
                continue
            if c is default:
                self.emit("{" if first else "} else {")
            else:
                cond = " || ".join(f"{sv}.tag == {t}" for t in tags)
                self.emit(f"{'' if first else '} else '}if ({cond}) {{")
            first = False
            self.depth += 1
            name, t = self.tvars[id(c)]
            self.bind(name, t, sv, iname, tags if c is not default else list(range(1, len(self.members[iname]) + 1)))
            self.with_copy(c, s.var, c.body)
            self.depth -= 1
        self.emit("}")
        self.selects.pop()
        self.emit(f"{label}: ;")

    def st_break(self, s):
        if not self.selects:
            raise NotCompiled("a break outside a select", getattr(s, "line", None))
        self.emit(f"goto {self.selects[-1]};")

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
        c = self.cond(s.cond)
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
            self.store("ret", self.typed(s.value, rt, f'function {self.func.name}'), skind(rt))
        for k, name in self.temps:                 # this statement's temporaries, which the jump would skip
            self.emit(f"rt_free_{k}(&{name});")
        self.emit("goto fn_end;")

    def st_stop(self, s):
        if s.code is None:
            self.emit("rt_stop(0);")
        else:
            self.emit(f"rt_stop((int){self.scalar(s.code, 'int')});")

    def st_print(self, s):
        parts = []
        for a in s.args:
            v = self.value(a)
            if v.t.optional:
                parts.append(("rec:" + skind(v.t), 0, v.atom))                    # w_opt__int ... print the value, or null
            elif v.t.kind == "null":
                parts.append(("null", 0, "0"))
            else:
                parts.append(("rec:" + v.t.name if isrec(v.t) else v.t.kind, v.t.rank, v.atom if v.t.rank == 0 else v))
        for kind, rank, atom in parts:                 # a record with an interface value that has none cannot be printed: before any output
            if kind.startswith("rec:") and self.has_iface[kind[4:]]:
                if rank == 0:
                    self.emit(f"chk_{kind[4:]}({atom}, {self.line});")
                else:
                    k = self.loop_var()
                    self.emit(f"for (int64_t {k} = 0; {k} < {atom.atom}.n; {k}++) chk_{kind[4:]}({atom.atom}.data[{k}], {self.line});")
        for i, (kind, rank, atom) in enumerate(parts):
            if i:
                self.emit("rt_write_sep();")
            if kind == "null":
                self.emit('rt_write_str("null");')
            elif rank == 0:
                self.emit(f"{'w_' + kind[4:] if kind.startswith('rec:') else WRITERS[kind]}({atom});")
            elif kind.startswith("rec:"):
                self.emit(f"rt_write_ra_{kind[4:]}_arr(&{atom.atom});")
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
        f, nodes = self.call_target(s)
        if f.ret is not None:
            raise NotCompiled(f"'{s.name}' is a function; use it in an expression", s.line)
        if not self.arity_ok(f, len(nodes)):
            raise NotCompiled(f"a call of '{s.name}' with missing arguments", s.line)
        args = self.call_args(f, nodes)
        after = self.after_call
        self.emit(f'rt_call_in({s.line}, "{s.name}");')
        self.emit(f"{self.cnames[id(f)]}({', '.join(args)});")
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
                out.append(f"rt_{akind(t)}_arr {c_name(name)}")
            elif intent == "inout":
                out.append(f"{cscalar(t)} *{c_name(name)}")
            elif intent == "out":
                out.append(f"{cscalar(t)} *{c_name(name)}, bool *set_{name}")
            else:
                out.append(f"{cscalar(t)} {c_name(name)}")
        return ", ".join(out) or "void"

    def type_copies(self, body):
        """Each select type case and `is` test that names its copy gets a variable of its own, declared at the start of the
        function (so that a jump out of the case cannot skip freeing it)."""
        for n in walk(body):
            if isinstance(n, SelectType):
                subject = self.types[id(n.subject)]
                for c in n.cases:
                    if c.values is None:
                        t = Type("interface", 0, False, subject.name)
                    else:
                        t = Type("interface" if c.values in self.ifaces else "record", 0, False, c.values)
                    self.new_copy(c, t)
            elif isinstance(n, IsType) and n.var:
                self.new_copy(n, Type("interface" if n.type in self.ifaces else "record", 0, False, n.type))

    def new_copy(self, node, t):
        self.n += 1
        name = f"sv{self.n}"
        self.tvars[id(node)] = (name, t)
        self.emit(f"r_{t.name} {name} = {{0}};")
        if self.deep[t.name]:
            self.arrays.append((f"rec_{t.name}", name))

    def local_declarations(self, params, body):
        self.type_copies(body)
        declared = {name for _, name, _ in params}
        seen = set()
        for d in statements_in(body):
            if isinstance(d, Decl) and d.name not in seen and d.name not in declared:
                seen.add(d.name)
                t = self.vars[d.name][0]
                self.emit(f"bool decl_{d.name} = false;")
                if t.optional:
                    self.emit(f"{cscalar(t)} {c_name(d.name)} = {{0}};")
                    if self.deep[skind(t)]:
                        self.arrays.append((f"rec_{skind(t)}", c_name(d.name)))
                elif t.kind == "string":
                    self.emit(f"rt_str {c_name(d.name)} = {{0}};")
                    self.arrays.append(("str", c_name(d.name)))
                elif isrec(t) and t.rank == 0 and self.deep[t.name]:
                    self.emit(f"r_{t.name} {c_name(d.name)} = {{0}};")
                    self.arrays.append((f"rec_{t.name}", c_name(d.name)))
                elif t.rank == 0:
                    self.emit(f"{cscalar(t)} {c_name(d.name)};")
                else:
                    self.emit(f"rt_{akind(t)}_arr {c_name(d.name)} = {{0}};")
                    self.arrays.append((akind(t), c_name(d.name)))

    def function(self, f):
        self.func = f
        self.declare_vars(f.params, f.body)
        rt = self.param_type(f.ret) if f.ret else None
        head = f"static {cscalar(rt) if rt else 'void'} {self.cnames[id(f)]}({self.c_params(f)})"
        self.lines, self.depth, self.line, self.arrays = [], 1, 0, []
        if rt:
            start = "{0}" if rt.optional else f"d_{rt.name}()" if isrec(rt) else "{0}" if rt.kind == "string" else "false" if rt.kind == "logical" else "0"
            self.emit(f"{cscalar(rt)} ret = {start};")
            self.emit("int call_line = rt_call_line;")
        self.local_declarations(f.params, f.body)
        self.block(f.body)
        if f.ret is not None:
            self.emit(f'rt_fail(call_line, "function {f.name} must return a {f.ret if f.ret in self.records or f.ret in self.ifaces or f.ret.endswith("?") else self.param_type(f.ret).kind}");')
        self.emit("fn_end: ;")
        for k, name in self.arrays:
            self.emit(f"rt_free_{k}(&{name});")
        self.emit("return ret;" if rt else "return;")
        return head + ";", "\n".join([head + " {"] + self.lines + ["}"])

    def field_ctype(self, fd):
        t = self.ftype(fd)
        return f"r_{t}" if t in self.records or t in self.ifaces or t.startswith(OPT) else CSCALAR[t]

    def field_size(self, rec, field):
        """The size of the array field `field` of record `rec`, or None when it is a single value."""
        for fd in self.records[rec].fields:
            if fd.name == field:
                return fd.dims[0].value if fd.dims else None
        return None

    def record_block(self, name):
        """The C struct of a record, how it is copied, freed, made with its defaults and printed (`point(x=1.0, y=2.0)`)."""
        r = self.records[name]
        out = []
        decls = [f"{self.field_ctype(fd)} m_{fd.name}{f'[{max(fd.dims[0].value, 1)}]' if fd.dims else ''};" for fd in r.fields]
        out.append("typedef struct { " + " ".join(decls) + f" }} r_{name};")
        if self.deep[name]:                                   # free, copy, and store (copy, then free the old)
            free, copy = [], []
            for fd in r.fields:
                ft = self.ftype(fd)
                own = ft == "string" or self.deep.get(ft)
                if not own:
                    continue
                m = f"m_{fd.name}"
                if ft == "string":
                    free.append(f"rt_free_str(&r->{m});")
                    copy.append(f"r.{m} = rt_str_new(rt_cs(a.{m}), a.{m}.n, 0);")
                elif fd.dims:
                    n = fd.dims[0].value
                    free.append(f"for (int i = 0; i < {n}; i++) rt_free_rec_{ft}(&r->{m}[i]);")
                    copy.append(f"for (int i = 0; i < {n}; i++) r.{m}[i] = rt_copy_rec_{ft}(a.{m}[i]);")
                else:
                    free.append(f"rt_free_rec_{ft}(&r->{m});")
                    copy.append(f"r.{m} = rt_copy_rec_{ft}(a.{m});")
            out.append(f"static void rt_free_rec_{name}(r_{name} *r) {{ {' '.join(free)} }}")
            out.append(f"static r_{name} rt_copy_rec_{name}(r_{name} a) {{ r_{name} r = a; {' '.join(copy)} return r; }}")
            out.append(f"static void rt_set_rec_{name}(r_{name} *dst, r_{name} src) {{ r_{name} c = rt_copy_rec_{name}(src); "
                       f"rt_free_rec_{name}(dst); *dst = c; }}")
        out.extend(self.array_code(name))
        body = []
        for fd in r.fields:
            m = f"r.m_{fd.name}"
            if self.ftype(fd) in self.records or self.ftype(fd) in self.ifaces or self.fopt(fd):
                value = f"d_{self.ftype(fd)}()"
            elif fd.init is not None:
                value = self.field_default(fd, self.records)
            elif fd.type == "string":
                value = 'rt_str_new("", 0, 0)'
            else:
                value = "false" if fd.type == "logical" else "0"
            if fd.dims:
                body.append(f"for (int i = 0; i < {fd.dims[0].value}; i++) {m}[i] = {value};")
            else:
                body.append(f"{m} = {value};")
        out.append(f"static r_{name} d_{name}(void) {{ r_{name} r; " + " ".join(body) + " return r; }")
        parts = []
        for i, fd in enumerate(r.fields):
            ft = self.ftype(fd)
            writer = f"w_{ft}" if ft in self.records or ft in self.ifaces or self.fopt(fd) else WRITERS[ft]
            label = f'rt_write_str({c_string((", " if i else "") + fd.name + "=")});'
            if fd.dims:
                parts.append(f'{label} rt_write_str("["); for (int i = 0; i < {fd.dims[0].value}; i++) {{ if (i) rt_write_str(", "); '
                             f'{writer}(r.m_{fd.name}[i]); }} rt_write_str("]");')
            else:
                parts.append(f"{label} {writer}(r.m_{fd.name});")
        out.append(f"static void w_{name}(r_{name} r) {{ rt_write_str({c_string(name + '(')}); " + " ".join(parts) + ' rt_write_str(")"); }')
        out.append(f"static void rt_write_ra_{name}_arr(const rt_ra_{name}_arr *a) {{ rt_write_str(\"[\"); "
                   f"for (int64_t i = 0; i < a->n; i++) {{ if (i) rt_write_str(\", \"); w_{name}(a->data[i]); }} rt_write_str(\"]\"); }}")
        if self.has_iface[name]:
            out.extend(self.unassigned_code(name))
        return out

    def unassigned_code(self, name):
        """For a type that can hold an interface value: `un_<name>` gives the dotted path of the first one that has no value
        yet (as the interpreter finds it when asked to print), and `chk_<name>` stops with its message about printing."""
        out = []
        reach = lambda t: t in self.ifaces or self.has_iface.get(t)
        if any(self.fopt(fd) and reach(self.fbase(fd)) for fd in self.records.get(name, type("", (), {"fields": []})).fields):
            self.refuse(f"an optional field of a record that can hold an interface value (in '{name}')")
        if name in self.ifaces:
            cases = " ".join(f"case {i + 1}: return un_{r}(&v->u.m_{r}, prefix, out);" for i, r in enumerate(self.members[name]) if self.has_iface[r])
            out.append(f"static const char *un_{name}(const r_{name} *v, const char *prefix, char *out) {{ switch (v->tag) {{ {cases} default: return NULL; }} }}")
            cases = " ".join(f"case {i + 1}: chk_{r}(v->u.m_{r}, line); break;" for i, r in enumerate(self.members[name]) if self.has_iface[r])
            out.append(f"static void chk_{name}(r_{name} v, int line) {{ switch (v.tag) {{ {cases} default: break; }} }}")
            return out
        body = []
        for fd in self.records[name].fields:
            if not reach(fd.type):
                continue
            n = fd.dims[0].value if fd.dims else 1
            loop = f"for (int i = 0; i < {n}; i++) " if fd.dims else ""
            item = f"r->m_{fd.name}[i]" if fd.dims else f"r->m_{fd.name}"
            one = ""
            if fd.type in self.ifaces:
                one += f'if ({item}.tag == 0) {{ snprintf(out, 256, "%s%s", prefix, "{fd.name}"); return out; }} '
            one += (f'{{ char p[256]; snprintf(p, 256, "%s%s.", prefix, "{fd.name}"); '
                    f'const char *x = un_{fd.type}(&{item}, p, out); if (x) return x; }}')
            body.append(f"{loop}{{ {one} }}")
        out.append(f"static const char *un_{name}(const r_{name} *r, const char *prefix, char *out) {{ {' '.join(body)} return NULL; }}")
        out.append(f'static void chk_{name}(r_{name} r, int line) {{ char out[256]; if (un_{name}(&r, "", out)) '
                   f'rt_fail(line, "cannot print a {name}: its field \'%s\' has no value yet", out); }}')
        return out

    def type_code(self):
        """All the C types: each record and interface after the types it is made of."""
        out = []
        for n in self.type_order:
            if n.startswith(OPT):
                out.extend(self.optional_block(n[len(OPT):]))
            else:
                out.extend(self.iface_block(n) if n in self.ifaces else self.record_block(n))
        return out

    def optional_block(self, base):
        """The struct of an optional value of type `base` (`has` says whether there is one), how it is copied and freed when the
        value owns text, and how it prints (`null` when absent)."""
        name = OPT + base
        ct = f"r_{base}" if base in self.records else CSCALAR[base]
        out = [f"typedef struct {{ bool has; {ct} v; }} r_{name};"]
        if self.deep[name]:
            if base == "string":
                free, copy = "rt_free_str(&r->v);", "c.v = rt_str_new(rt_cs(a.v), a.v.n, 0);"
            else:
                free, copy = f"rt_free_rec_{base}(&r->v);", f"c.v = rt_copy_rec_{base}(a.v);"
            out.append(f"static void rt_free_rec_{name}(r_{name} *r) {{ if (r->has) {{ {free} }} r->has = false; }}")
            out.append(f"static r_{name} rt_copy_rec_{name}(r_{name} a) {{ r_{name} c = a; if (a.has) {{ {copy} }} return c; }}")
            out.append(f"static void rt_set_rec_{name}(r_{name} *dst, r_{name} src) {{ r_{name} c = rt_copy_rec_{name}(src); "
                       f"rt_free_rec_{name}(dst); *dst = c; }}")
        writer = f"w_{base}" if base in self.records else WRITERS[base]
        out.append(f"static r_{name} d_{name}(void) {{ r_{name} r; memset(&r, 0, sizeof r); return r; }}")
        out.append(f'static void w_{name}(r_{name} o) {{ if (o.has) {writer}(o.v); else rt_write_str("null"); }}')
        return out

    def iface_block(self, name):
        """The struct of an interface (a tag, 0 for nothing assigned yet, and a union of the records that fit it), how it is
        copied, freed and printed, and its arrays."""
        types = []
        members = self.members[name]
        union = " ".join(f"r_{r} m_{r};" for r in members)
        types.append(f"typedef struct {{ int tag; union {{ {union or 'char none;'} }} u; }} r_{name};")
        deep = [(i + 1, r) for i, r in enumerate(members) if self.deep[r]]
        free = " ".join(f"case {i}: rt_free_rec_{r}(&r->u.m_{r}); break;" for i, r in deep)
        copy = " ".join(f"case {i}: c.u.m_{r} = rt_copy_rec_{r}(a.u.m_{r}); break;" for i, r in deep)
        types.append(f"static void rt_free_rec_{name}(r_{name} *r) {{ switch (r->tag) {{ {free} default: break; }} r->tag = 0; }}")
        types.append(f"static r_{name} rt_copy_rec_{name}(r_{name} a) {{ r_{name} c = a; switch (a.tag) {{ {copy} default: break; }} return c; }}")
        types.append(f"static void rt_set_rec_{name}(r_{name} *dst, r_{name} src) {{ r_{name} c = rt_copy_rec_{name}(src); "
                     f"rt_free_rec_{name}(dst); *dst = c; }}")
        types.append(f"static r_{name} d_{name}(void) {{ r_{name} r; memset(&r, 0, sizeof r); return r; }}")
        shown = " ".join(f"case {i + 1}: w_{r}(v.u.m_{r}); break;" for i, r in enumerate(members))
        types.append(f'static void w_{name}(r_{name} v) {{ switch (v.tag) {{ {shown} default: rt_write_str("None"); }} }}')
        types.extend(self.array_code(name))
        types.append(f'static void rt_write_ra_{name}_arr(const rt_ra_{name}_arr *a) {{ rt_write_str("["); '
                     f'for (int64_t i = 0; i < a->n; i++) {{ if (i) rt_write_str(", "); w_{name}(a->data[i]); }} rt_write_str("]"); }}')
        types.extend(self.unassigned_code(name))
        return types

    def interface_code(self):
        """For each function an interface lists, the function that looks at the tag and calls the function of the record held.
        Returns (the prototypes, the dispatching functions)."""
        protos, bodies = [], []
        for name, idef in self.ifaces.items():
            members = self.members[name]
            for fname, f in idef.functions.items():
                rt = self.param_type(f.ret) if f.ret is not None else None
                head = f"static {cscalar(rt) if rt else 'void'} {self.cnames[id(f)]}({self.c_params(f)})"
                protos.append(head + ";")
                self_p = f.params[0]
                inout = self_p[2] != "in"
                rest = []
                for typ, pname, intent in f.params[1:]:
                    rest.append(c_name(pname) + (f", set_{pname}" if intent == "out" else ""))
                cases = []
                for i, r in enumerate(members):
                    me = f"&{c_name(self_p[1])}->u.m_{r}" if inout else f"{c_name(self_p[1])}.u.m_{r}"
                    call = f"f_{r}__{fname}({', '.join([me] + rest)})"
                    cases.append(f"case {i + 1}: {'return ' if rt else ''}{call};{'' if rt else ' return;'}")
                tag = f"{c_name(self_p[1])}->tag" if inout else f"{c_name(self_p[1])}.tag"
                bodies.append(head + " {\n    switch (" + tag + ") {\n        " + "\n        ".join(cases) + "\n    }\n    abort();\n}")
        return protos, bodies

    def array_code(self, name):
        """The array of a record or interface `name`: elements start as `d_<name>()`, and are freed one by one if they own anything."""
        out = []
        free = f"for (int64_t i = 0; i < a->n; i++) rt_free_rec_{name}(&a->data[i]); " if self.deep[name] else ""
        out.append(f"typedef struct {{ r_{name} *data; int64_t shape[3]; int rank; int64_t n; }} rt_ra_{name}_arr;")
        out.append(f"static inline void rt_free_ra_{name}(rt_ra_{name}_arr *a) {{ {free}free(a->data); a->data = NULL; a->n = 0; }}")
        out.append(f"static r_{name} d_{name}(void);")
        out.append(f"static void rt_alloc_ra_{name}(rt_ra_{name}_arr *a, int rank, int64_t d0, int64_t d1, int64_t d2, int line) {{")
        out.append('    if (d0 < 0 || d1 < 0 || d2 < 0) rt_fail(line, "array dimensions must be non-negative integers");')
        out.append(f"    rt_free_ra_{name}(a);")
        out.append("    a->rank = rank; a->shape[0] = d0; a->shape[1] = d1; a->shape[2] = d2; a->n = d0 * d1 * d2;")
        out.append(f"    a->data = (r_{name} *)calloc(a->n ? (size_t)a->n : 1, sizeof(r_{name}));")
        out.append('    if (!a->data) rt_fail(line, "out of memory");')
        out.append(f"    for (int64_t i = 0; i < a->n; i++) a->data[i] = d_{name}();")
        out.append("}")
        return out

    def program(self):
        self.check()
        protos, defs = [], []
        for f in all_functions(self):
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
        iprotos, ibodies = self.interface_code()
        return "\n".join(["/* generated by `boreas emit-c`: do not edit */"] + flags + [runtime] + self.type_code() + protos
                         + iprotos + ibodies + [""] + defs + [""] + main) + "\n"


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
