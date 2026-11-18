"""Finds names that are used but never declared, for `boreas check` and `boreas lsp`, without
running the program. It follows the interpreter's rules for what each procedure can see.

A name declared anywhere in a procedure counts as declared, even if the use comes first or the
declaration is in a branch: only the program's path decides whether that fails, and an error
reported here should always be a real one. The interpreter still catches those cases when it
runs them."""
from types import SimpleNamespace
from .effects import mark_problem, types_in
from .hints import name_hint
from .tree import STATEMENTS, children, statements_in
from .interp import BUILTINS, ELEMENTAL, PREDEFINED, SUBROUTINE_BUILTINS
from .parser import (Allocate, Apply, ArrayLit, Assign, MatrixLit, CollLit, Binary, Call, Close, Decl, Do, DoEach, DoWhile,
                     ExprStmt, FieldAccess, Gen, If, IsType, Marked, MapType, MultiAssign, NamedArg, Print, ProcType, Return, ReturnMany, ResultList, Select, SelectExpr, SelectType, SetType, Slice, Stop,
                     Unary, Var, Where, sub_bodies)


MISSING = object()                                  # a callee the check cannot identify


def indexable_in(body):
    """The names a statement list declares as arrays or strings, which are indexed with brackets."""
    return {s.name for s in statements_in(body) if isinstance(s, Decl) and (
        s.dims is not None or s.allocatable or s.type == "string" or isinstance(s.type, (MapType, SetType)))}


def indexable_params(params):
    return {name for typ, name, _ in params
            if isinstance(typ, (MapType, SetType)) or (isinstance(typ, str) and (typ == "string" or typ.endswith("]")))}


def callback_signature(name, proc):
    """A procedure parameter's ProcType, shaped like a Function so that mark_problem can check a call to it."""
    return SimpleNamespace(name=name, params=[(typ, f"argument {n}", intent)
                                              for n, (typ, intent) in enumerate(proc.params, 1)])


def declared_in(body):
    """Every name a statement list declares, at any depth."""
    return {s.name for s in statements_in(body) if isinstance(s, Decl)}


class Walker:
    """Checks one procedure or program body against what it can see."""
    def __init__(self, scope, variables, indexable=(), callbacks=None, types=None):
        self.scope, self.variables, self.errors = scope, variables, []
        self.types = types or {}                          # variable or parameter -> its declared type name
        self.callbacks = callbacks or {}                  # procedure parameter -> its ProcType
        self.indexable = set(indexable)               # arrays and strings: written name[...], never name(...)

    def method(self, receiver, name):
        """The function `name` of the record or interface a receiver was declared with, or None if unknown."""
        if isinstance(receiver, Var) or (isinstance(receiver, Apply) and receiver.bracket):
            kind = self.types.get(receiver.name)
            owner = self.scope.records.get(kind) or self.scope.interfaces.get(kind)
            return owner.functions.get(name) if owner is not None else None
        return None

    def known_names(self):
        return [*self.variables, *self.scope.funcs, *self.scope.records, *BUILTINS, *ELEMENTAL]

    def report(self, name, line, col, message):
        self.errors.append((line, col, message, len(name)))

    def variable(self, name, line, col):
        if name not in self.variables and name not in PREDEFINED:
            self.report(name, line, col, f"'{name}' is not declared" + name_hint(name, self.known_names()))

    def expr(self, e):
        if isinstance(e, list):
            for x in e:
                self.expr(x)
        elif isinstance(e, Var):
            self.variable(e.name, e.line, e.col)
        elif isinstance(e, Apply):
            if not e.bracket and e.name in self.indexable:
                self.report(e.name, e.line, e.col, f"index with brackets: write {e.name}[...], not {e.name}(...)")
            elif not (e.name in self.variables or e.name in self.scope.funcs or e.name in self.scope.records
                    or e.name in BUILTINS or e.name in ELEMENTAL or e.name in ("present", "allocated")):
                self.report(e.name, e.line, e.col, f"unknown function or array '{e.name}'" + name_hint(e.name, self.known_names()))
            else:
                many = self.results(e)
                if many:
                    names = ", ".join("abcdefgh"[:many])
                    self.report(e.name, e.line, e.col,
                                f"'{e.name}' returns {many} results; unpack them: ({names}) = {e.name}(...)")
            self.arguments(e.args)
            if e.chain is not None:
                self.expr(e.chain)
        elif isinstance(e, Marked):
            self.report("@", e.line, e.col, "'@' marks an argument of a call statement, as in call swap(@x, @y)")
        elif isinstance(e, Gen):                  # its variable exists only inside the generator
            self.expr([e.lo, e.hi, e.step])
            added = e.var not in self.variables
            self.variables.add(e.var)
            self.expr(e.body)
            if added:
                self.variables.discard(e.var)
        else:                                       # anything else: look inside it (a field's name is not looked up)
            self.inside(e)

    def inside(self, node):
        """Check what is directly inside `node`: statements as statements, the rest as expressions."""
        for child in children(node):
            if isinstance(child, tuple(STATEMENTS)):
                self.statement(child)
            else:
                self.expr(child)

    def return_of(self, e):
        """What the call `e` gives back: a type, a ResultList, or None for a subroutine; MISSING when the
        callee is not a procedure this check can see (an array element, a record's function, a variable)."""
        if e.bracket:
            return MISSING
        if e.name in self.callbacks:                        # a procedure parameter: its signature says
            return self.callbacks[e.name].ret
        if e.name in self.variables:
            return MISSING
        f = self.scope.funcs.get(e.name)
        return MISSING if f is None else f.ret

    def results(self, e):
        """How many results the function `e` calls returns, when it is a function with several; else 0."""
        ret = self.return_of(e)
        return len(ret.types) if isinstance(ret, ResultList) else 0

    def unpacking(self, s):
        """`(a, b) = f(...)`: f must be a function with exactly as many results as there are targets."""
        v = s.value
        self.expr([t for t, _ in s.targets if t is not None])
        ret = self.return_of(v) if isinstance(v, Apply) else MISSING
        if ret is MISSING:
            self.expr(v)                                   # not a plain call of a known procedure: nothing to count
            return
        want = len(s.targets)
        if isinstance(ret, ResultList):
            if len(ret.types) != want:
                self.report(v.name, v.line, v.col, f"'{v.name}' returns {len(ret.types)} results, "
                                                   f"but {want} targets are listed")
        elif ret is not None:
            self.report(v.name, v.line, v.col, f"'{v.name}' returns one result; an unpacking needs a "
                                               f"function with several results (write 'x = {v.name}(...)')")
        self.arguments(v.args)

    def arguments(self, args):
        """A bare name that is a procedure (and no variable) is how a call passes a callback."""
        for a in args:
            if not (isinstance(a, Var) and a.name not in self.variables and a.name in self.scope.funcs):
                self.expr(a)

    def statements(self, body):
        for s in body:
            self.statement(s)

    def statement(self, s):
        if isinstance(s, list):
            self.statements(s)
        elif isinstance(s, If):
            for cond, body in s.branches:
                added = False
                if isinstance(cond, IsType):                  # `x is circle as c`: c exists only in this branch
                    self.expr(cond.subject)
                    if cond.type not in self.scope.records and cond.type not in self.scope.interfaces:
                        self.report(cond.type, cond.line, cond.col, f"no record or interface called '{cond.type}'")
                    added = cond.var is not None and cond.var not in self.variables
                    if cond.var is not None:
                        self.variables.add(cond.var)
                else:
                    self.expr(cond)
                self.statements(body)
                if added:
                    self.variables.discard(cond.var)
            self.statements(s.orelse)
        elif isinstance(s, Do):
            self.variable(s.var, s.line, s.var_col)
            self.inside(s)
        elif isinstance(s, DoEach):
            for v, col in zip(s.vars, s.cols):
                self.variable(v, s.line, col)
            self.inside(s)
        elif isinstance(s, SelectType):               # `p` exists only inside the cases
            self.expr(s.subject)
            added = s.var not in self.variables
            self.variables.add(s.var)
            for case in s.cases:
                if case.values is not None and case.values not in self.scope.records \
                        and case.values not in self.scope.interfaces:
                    self.report(case.values, case.line, case.col, f"no record or interface called '{case.values}'")
                self.statements(case.body)
            if added:
                self.variables.discard(s.var)
        elif isinstance(s, MultiAssign):
            self.unpacking(s)
        elif isinstance(s, Call):
            if s.receiver is not None:              # a record's function: the record decides which
                self.expr(s.receiver)
                f = self.method(s.receiver, s.name)
                problem = mark_problem(f, s) if f is not None and f.is_sub else None
                if problem:
                    self.report(s.name, s.line, s.col, problem)
            elif s.name in self.callbacks and self.callbacks[s.name].is_sub:
                problem = mark_problem(callback_signature(s.name, self.callbacks[s.name]), s)
                if problem:
                    self.report(s.name, s.line, s.col, problem)
            elif s.name not in self.scope.funcs and s.name not in self.variables and s.name not in SUBROUTINE_BUILTINS:
                self.report(s.name, s.line, s.col, f"unknown subroutine '{s.name}'")
            elif s.name in SUBROUTINE_BUILTINS and s.name not in self.scope.funcs and s.name not in self.variables:
                marks = s.marks or [False] * len(s.args)
                if s.args and not marks[0]:
                    self.report(s.name, s.line, s.col, f"{s.name} changes its first argument, so mark it: "
                                                       f"call {s.name}(@..., ...)")
            elif s.name in self.scope.funcs and s.name not in self.variables and self.scope.funcs[s.name].is_sub:
                problem = mark_problem(self.scope.funcs[s.name], s)
                if problem:
                    self.report(s.name, s.line, s.col, problem)
            self.arguments(s.args)
        else:                                         # assignments, prints, plain loops, selects, ...: look inside
            self.inside(s)


def undeclared_names(unit, interp):
    """Every undeclared name in a linked unit, in source order, as (line, col, message, length)."""
    bodies = [(f, interp.scopes[id(f)]) for f in unit.functions.values()]
    for mod in unit.modules.values():
        bodies += [(f, interp.scopes[id(f)]) for f in mod.functions.values()]
    for rec in [*unit.records.values(), *(r for m in unit.modules.values() for r in m.records.values())]:
        bodies += [(f, interp.scopes[id(f)]) for f in rec.functions.values()]
    errors = []
    for f, scope in bodies:
        walker = Walker(scope, set(scope.consts) | {name for _, name, _ in f.params} | declared_in(f.body),
                        indexable_in(f.body) | indexable_params(f.params),
                        {name: typ for typ, name, _ in f.params if isinstance(typ, ProcType)},
                        {**types_in(f.body), **{name: typ for typ, name, _ in f.params if isinstance(typ, str)}})
        walker.statements(f.body)
        errors += walker.errors
    if unit.program:
        scope = interp.program_scope
        walker = Walker(scope, set(scope.consts) | declared_in(unit.program.body), indexable_in(unit.program.body),
                        types=types_in(unit.program.body))
        walker.statements(unit.program.body)
        errors += walker.errors
    return sorted(errors, key=lambda err: err[:2])
