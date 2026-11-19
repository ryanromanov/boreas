"""Compiles the numeric part of Boreas to C (D76).

`compile_unit(unit, interp)` returns the text of one self-contained C file: the runtime (cruntime.h), then the program.
It reads the types and conversions the type checker worked out (typecheck.py) and never works them out again. A program
that uses anything outside the subset is refused as a whole, with a message that names the construct and its line: no
partial code is ever written. Every operation is emitted as three-address code (each part into a temporary, in source
order), so the order of evaluation, and of any output or error, is the interpreter's.

The subset so far: `int`, `real` and `logical` values, `if`, `do`, `do while`, `exit`, `cycle`, `stop`, `return`,
`println`/`print`, and functions and subroutines whose parameters are single values passed `in`."""
import os
import shutil
import subprocess
import sys
import tempfile
from .parser import (Apply, Assign, Bool, Binary, Call, Comment, Cycle, Decl, Do, DoWhile, Exit, If, IsType, Num, Print,
                     Return, Stop, Str, Unary, Var)
from .tree import statements_in, walk
from .typecheck import infer

HERE = os.path.dirname(os.path.abspath(__file__))
CTYPE = {"int": "int64_t", "real": "double", "logical": "bool"}
SCALARS = ("int", "real", "logical")
BUILTINS = {"sqrt", "abs", "sin", "cos", "tan", "exp", "log", "log10", "mod", "min", "max", "real", "int", "pi"}


class NotCompiled(Exception):
    """The program uses something the compiler does not handle yet."""
    def __init__(self, what, line=None):
        self.what, self.line = what, line
        super().__init__(f"not supported by the compiler yet: {what}" + (f" (line {line})" if line else ""))


def c_name(name):
    return "v_" + name


def f_name(name):
    return "f_" + name


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


class Compiler:
    def __init__(self, unit, interp, release=False):
        self.unit, self.interp, self.release = unit, interp, release
        self.checker = infer(unit, interp)
        self.types, self.conv = self.checker.types, self.checker.conv
        self.lines, self.depth, self.n, self.line = [], 1, 0, 0
        self.loops = []                 # innermost last: (label name or None, id, exit target, cycle target)
        self.func = None

    # -- refusing what is not handled ---------------------------------------------------------------------------
    def refuse(self, what, node=None, line=None):
        raise NotCompiled(what, line or getattr(node, "line", None) or None)

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
            if f.ret is not None and (not isinstance(f.ret, str) or f.ret not in SCALARS):
                self.refuse(f"function '{f.name}' returns {f.ret if isinstance(f.ret, str) else 'several values'}")
            for typ, name, intent in f.params:
                if not isinstance(typ, str) or typ not in SCALARS:
                    self.refuse(f"parameter '{name}' of '{f.name}' is not a single int, real or logical")
                if intent != "in":
                    self.refuse(f"{intent} parameter '{name}' of '{f.name}'")
        bodies = [(f, f.body) for f in u.functions.values()] + [(None, u.program.body)]
        for f, body in bodies:
            self.check_body(body, f)

    def check_body(self, body, f):
        strings = set()
        fixed = {name for _, name, _ in f.params} if f is not None else set()     # `in` parameters cannot be assigned
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
                if s.type not in SCALARS or s.dims is not None or s.allocatable:
                    what = "an array" if s.dims is not None else str(s.type)
                    self.refuse(f"the declaration of '{s.name}' ({what})", s)
            elif kind is Assign:
                if not isinstance(s.target, Var) or s.update:
                    self.refuse("an assignment to anything but a plain variable", s)
                if s.target.name in fixed:
                    self.refuse(f"an assignment to the constant or parameter '{s.target.name}'", s)
            elif kind is Print:
                if s.to is not None:
                    self.refuse("printing to a file", s)
                strings |= {id(a) for a in s.args if isinstance(a, Str)}
            elif kind is Call:
                if s.receiver is not None or s.name not in self.unit.functions:
                    self.refuse(f"the call of '{s.name}'", s)
            else:
                self.refuse(f"a {kind.__name__} statement", s)
        for node in walk(body):
            self.check_expr(node, strings)

    STATEMENTS = (Decl, Assign, If, Do, DoWhile, Print, Call, Return, Stop, Exit, Cycle, Comment)

    def check_expr(self, e, strings):
        kind = type(e)
        if kind in self.STATEMENTS:
            return
        if kind is Str:
            if id(e) not in strings:
                self.refuse("text outside a print", e)
            return
        if kind not in (Num, Bool, Var, Unary, Binary, Apply):
            if kind is IsType:
                self.refuse("a type test", e)
            self.refuse(f"a {kind.__name__}", e)
        if kind is Unary and e.op not in ("-", "+", "not"):
            self.refuse(f"the operator '{e.op}'", e)
        if kind is Binary and e.op not in ("+", "-", "*", "/", "**", "==", "/=", "<", "<=", ">", ">=", "and", "or"):
            self.refuse(f"the operator '{e.op}'", e)
        if kind is Apply:
            if e.bracket or e.chain:
                self.refuse("an array element", e)
            if e.name not in self.unit.functions and e.name not in BUILTINS:
                self.refuse(f"the function '{e.name}'", e)
        t = self.types.get(id(e))
        if t is None or not t.known or t.rank != 0 or t.kind not in SCALARS:
            self.refuse(f"an expression of type {t if t is not None else 'unknown'}", e)

    # -- the generated text -----------------------------------------------------------------------------------------
    def emit(self, text):
        self.lines.append("    " * self.depth + text)

    def tmp(self, ctype, expr):
        self.n += 1
        name = f"t{self.n}"
        self.emit(f"{ctype} {name} = {expr};")
        return name

    def kind_of(self, e):
        return self.types[id(e)].kind

    def cast(self, atom, kind, to):
        if kind == to or to is None:
            return atom
        if kind == "int" and to == "real":
            return f"((double){atom})"
        raise NotCompiled(f"a conversion from {kind} to {to}")

    def value(self, e):
        """The value of `e` (after the conversion the checker recorded for it): (kind, atom) with a C atom."""
        kind, atom = self.raw(e)
        target = self.conv.get(id(e))
        if target is not None and target.kind != kind:
            return target.kind, self.cast(atom, kind, target.kind)
        return kind, atom

    def want(self, e, kind):
        k, atom = self.value(e)
        return self.cast(atom, k, kind) if k != kind else atom

    def raw(self, e):
        t = type(e)
        if t is Num:
            if isinstance(e.value, float):
                return "real", repr(e.value) if "inf" not in repr(e.value) and "nan" not in repr(e.value) else "0.0"
            return "int", f"INT64_C({e.value})"
        if t is Bool:
            return "logical", "true" if e.value else "false"
        if t is Var:
            if e.name == "pi" and "pi" not in self.var_kinds:        # the language's own, until a program declares one
                return "real", "3.141592653589793"
            return self.kind_of(e), c_name(e.name)
        if t is Unary:
            return self.unary(e)
        if t is Binary:
            return self.binary(e)
        if t is Apply:
            return self.apply(e)
        raise NotCompiled(f"a {t.__name__}", getattr(e, "line", None))

    def unary(self, e):
        kind, atom = self.value(e.operand)
        line = self.line
        if e.op == "not":
            return "logical", self.tmp("bool", f"!{atom}")
        if e.op == "+":
            return kind, atom
        if kind == "int":
            return kind, self.tmp("int64_t", f"rt_neg({atom}, {line})")
        return kind, self.tmp("double", f"-{atom}")

    def binary(self, e):
        op, line = e.op, self.line
        flags = (1 if self.kind_of(e.left) == "int" else 0) | (2 if self.kind_of(e.right) == "int" else 0)
        lk, la = self.value(e.left)
        rk, ra = self.value(e.right)
        if op in ("and", "or"):                      # no short circuit: both sides are already computed
            return "logical", self.tmp("bool", f"({la} {'&&' if op == 'and' else '||'} {ra})")
        if op in ("==", "/=", "<", "<=", ">", ">="):
            if lk != rk:                             # whole numbers next to reals: compare as reals
                la, ra, lk = self.cast(la, lk, "real"), self.cast(ra, rk, "real"), "real"
            return "logical", self.tmp("bool", f"({la} {'!=' if op == '/=' else op} {ra})")
        kind = lk if lk == rk else "real"
        la, ra = self.cast(la, lk, kind), self.cast(ra, rk, kind)
        if kind == "int":
            call = {"+": "rt_add", "-": "rt_sub", "*": "rt_mul", "/": "rt_div", "**": "rt_ipow"}[op]
            return kind, self.tmp("int64_t", f"{call}({la}, {ra}, {line})")
        call = {"+": "rt_fadd", "-": "rt_fsub", "*": "rt_fmul", "/": "rt_fdiv", "**": "rt_fpow"}[op]
        return kind, self.tmp("double", f"{call}({la}, {ra}, {flags}, {line})")

    def args(self, e):
        out = []
        for a in e.args:
            out.append(self.value(a))
        return out

    def apply(self, e):
        line = self.line
        if e.name in self.unit.functions:
            f = self.unit.functions[e.name]
            if len(e.args) != len(f.params):
                raise NotCompiled(f"a call of '{e.name}' with optional or missing arguments", line)
            args = [self.want(a, typ) for a, (typ, _, _) in zip(e.args, f.params)]
            self.emit(f'rt_call_in({line}, "{e.name}");')
            result = self.tmp(CTYPE[f.ret], f"{f_name(e.name)}({', '.join(args)})")
            self.emit("rt_call_out();")
            return f.ret, result
        name = e.name
        vals = self.args(e)
        kinds = [k for k, _ in vals]
        atoms = [a for _, a in vals]
        was = [1 if k == "int" else 0 for k in kinds]
        if name in ("sqrt", "exp", "sin", "cos", "tan", "log", "log10") and len(atoms) == 1:
            x = self.cast(atoms[0], kinds[0], "real")
            if kinds[0] not in ("int", "real"):
                raise NotCompiled(f"{name}() of {kinds[0]}", line)
            if name == "sqrt":
                call = f"rt_sqrt({x}, {was[0]}, {line})"
            elif name == "exp":
                call = f"rt_exp({x}, {was[0]}, {line})"
            elif name in ("log", "log10"):
                call = f'rt_log({x}, {name}, "{name}", {was[0]}, {line})'
            else:
                call = f'rt_math1("{name}", {name}({x}), {x}, {was[0]}, {line})'
            return "real", self.tmp("double", call)
        if name == "abs" and len(atoms) == 1:
            if kinds[0] == "int":
                return "int", self.tmp("int64_t", f"rt_abs({atoms[0]}, {line})")
            return "real", self.tmp("double", f"fabs({atoms[0]})")
        if name == "mod" and len(atoms) == 2:
            if kinds == ["int", "int"]:
                return "int", self.tmp("int64_t", f"rt_mod({atoms[0]}, {atoms[1]}, {line})")
            a, b = (self.cast(atoms[i], kinds[i], "real") for i in (0, 1))
            return "real", self.tmp("double", f"rt_fmod({a}, {b}, {was[0]}, {was[1]}, {line})")
        if name in ("min", "max") and len(atoms) >= 2:
            kind = "real" if "real" in kinds else "int"
            if any(k not in ("int", "real") for k in kinds):
                raise NotCompiled(f"{name}() of {kinds}", line)
            xs = [self.cast(a, k, kind) for a, k in zip(atoms, kinds)]
            best = self.tmp(CTYPE[kind], xs[0])
            for x in xs[1:]:
                self.emit(f"if ({x} {'>' if name == 'max' else '<'} {best}) {best} = {x};")
            return kind, best
        if name == "real" and len(atoms) == 1:
            return "real", self.cast(atoms[0], kinds[0], "real") if kinds[0] == "int" else atoms[0]
        if name == "int" and len(atoms) == 1:
            if kinds[0] == "int":
                return "int", atoms[0]
            return "int", self.tmp("int64_t", f"rt_trunc_int({atoms[0]}, {line})")
        raise NotCompiled(f"the function '{name}' with {len(atoms)} arguments", line)

    # -- statements ---------------------------------------------------------------------------------------------------
    def block(self, body):
        for s in body:
            if isinstance(s, list):
                self.block(s)
            else:
                self.statement(s)

    def statement(self, s):
        self.line = getattr(s, "line", 0) or getattr(self, "line", 0)
        kind = type(s)
        if kind is Comment:
            return
        self.emit("{")
        self.depth += 1
        getattr(self, "st_" + kind.__name__.lower())(s)
        self.depth -= 1
        self.emit("}")

    def st_decl(self, s):
        default = {"int": "INT64_C(0)", "real": "0.0", "logical": "false"}[s.type]
        if s.init is not None:
            self.emit(f"{c_name(s.name)} = {self.want(s.init, s.type)};")
        else:
            self.emit(f"{c_name(s.name)} = {default};")

    def st_assign(self, s):
        kind = self.types[id(s.target)].kind
        self.emit(f"{c_name(s.target.name)} = {self.want(s.value, kind)};")

    def st_if(self, s):
        self.if_chain(s.branches, s.orelse)

    def if_chain(self, branches, orelse):
        (cond, body), rest = branches[0], branches[1:]
        c = self.want(cond, "logical")
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
        var, line = c_name(s.var), self.line
        if self.var_kind(s.var) != "int":
            raise NotCompiled("a do loop whose variable is not an int", line)
        for part in (s.lo, s.hi, s.step):
            if part is not None and self.kind_of(part) != "int":
                raise NotCompiled("a do loop with bounds that are not whole numbers", line)
        lo = self.want(s.lo, "int")
        hi = self.want(s.hi, "int")
        step = self.want(s.step, "int") if s.step is not None else "INT64_C(1)"
        label, ident = self.new_loop(s)
        self.emit(f"int64_t lo{ident} = {lo}, hi{ident} = {hi}, st{ident} = {step};")
        self.emit(f"rt_check_step(st{ident}, {line});")
        self.emit(f"int64_t i{ident} = lo{ident};")
        self.emit(f"while (st{ident} > 0 ? i{ident} <= hi{ident} : i{ident} >= hi{ident}) {{")
        self.depth += 1
        self.emit(f"{var} = i{ident};")
        self.block(s.body)
        self.emit(f"cycle{ident}: ;")
        self.emit(f"i{ident} = i{ident} + st{ident};")
        self.depth -= 1
        self.emit("}")
        self.emit(f"{var} = i{ident};")             # like Fortran: the variable ends one step past the range
        self.emit(f"exit{ident}: ;")
        self.loops.pop()

    def st_dowhile(self, s):
        label, ident = self.new_loop(s)
        self.emit("for (;;) {")
        self.depth += 1
        self.emit("{")
        self.depth += 1
        c = self.want(s.cond, "logical")
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
        if self.func is None or self.func.ret is None:
            self.emit("return;" if self.func is not None else "return 0;")
        else:
            self.emit(f"return {self.want(s.value, self.func.ret)};")

    def st_stop(self, s):
        if s.code is None:
            self.emit("rt_stop(0);")
        else:
            self.emit(f"rt_stop((int){self.want(s.code, 'int')});")

    def st_print(self, s):
        parts = []
        for a in s.args:
            if isinstance(a, Str):
                parts.append(("string", c_string(a.value)))
            else:
                parts.append(self.value(a))
        writers = {"int": "rt_write_int", "real": "rt_write_real", "logical": "rt_write_bool", "string": "rt_write_str"}
        for i, (kind, atom) in enumerate(parts):
            if i:
                self.emit("rt_write_sep();")
            self.emit(f"{writers[kind]}({atom});")
        if s.newline:
            self.emit("rt_write_nl();")

    def st_call(self, s):
        f = self.unit.functions[s.name]
        if f.ret is not None:
            raise NotCompiled(f"'{s.name}' is a function; use it in an expression", s.line)
        if len(s.args) != len(f.params):
            raise NotCompiled(f"a call of '{s.name}' with optional or missing arguments", s.line)
        args = [self.want(a, typ) for a, (typ, _, _) in zip(s.args, f.params)]
        self.emit(f'rt_call_in({s.line}, "{s.name}");')
        self.emit(f"{f_name(s.name)}({', '.join(args)});")
        self.emit("rt_call_out();")

    # -- whole functions -------------------------------------------------------------------------------------------
    def var_kind(self, name):
        return self.var_kinds.get(name)

    def function(self, f):
        self.func = f
        self.var_kinds = {name: typ for typ, name, _ in f.params}
        self.hoist(f.body)
        params = ", ".join(f"{CTYPE[t]} {c_name(n)}" for t, n, _ in f.params) or "void"
        head = f"static {CTYPE[f.ret] if f.ret else 'void'} {f_name(f.name)}({params})"
        self.lines, self.depth, self.line = [], 1, 0
        self.declare_locals(f.body)
        self.block(f.body)
        if f.ret is not None:
            self.emit(f'rt_fail(0, "function {f.name} must return a {f.ret}");')
        return head + ";", "\n".join([head + " {"] + self.lines + ["}"])

    def hoist(self, body):
        for d in statements_in(body):
            if isinstance(d, Decl):
                self.var_kinds[d.name] = d.type

    def declare_locals(self, body):
        seen = set()
        for d in statements_in(body):
            if isinstance(d, Decl) and d.name not in seen:
                seen.add(d.name)
                self.emit(f"{CTYPE[d.type]} {c_name(d.name)};")

    def program(self):
        self.check()
        protos, defs = [], []
        for f in self.unit.functions.values():
            proto, text = self.function(f)
            protos.append(proto)
            defs.append(text)
        self.func = None
        prog = self.unit.program
        self.lines, self.depth, self.line = [], 1, 0
        self.var_kinds = {}
        self.hoist(prog.body)
        self.declare_locals(prog.body)
        self.block(prog.body)
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
    flags = ["-O2", "-std=gnu11", "-ffp-contract=off", "-fwrapv", "-w"]
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
