"""Finds names that are used but never declared, for `boreas check` and `boreas lsp`, without
running the program. It follows the interpreter's rules for what each procedure can see.

A name declared anywhere in a procedure counts as declared, even if the use comes first or the
declaration is in a branch: only the program's path decides whether that fails, and an error
reported here should always be a real one. The interpreter still catches those cases when it
runs them."""
from .interp import BUILTINS, ELEMENTAL
from .parser import (Allocate, Apply, ArrayLit, Assign, Binary, Call, Close, Decl, Do, DoWhile,
                     ExprStmt, FieldAccess, Gen, If, IsType, NamedArg, Print, Return, Select, SelectExpr, SelectType, Slice, Stop,
                     Unary, Var, sub_bodies)


def indexable_in(body):
    """The names a statement list declares as arrays or strings, which are indexed with brackets."""
    names = set()
    for s in body:
        if isinstance(s, list):
            names |= indexable_in(s)
        elif isinstance(s, Decl):
            if s.dims is not None or s.allocatable or s.type == "string":
                names.add(s.name)
        else:
            for inner in sub_bodies(s):
                names |= indexable_in(inner)
    return names


def indexable_params(params):
    return {name for typ, name, _ in params if isinstance(typ, str) and (typ == "string" or typ.endswith("]"))}


def declared_in(body):
    """Every name a statement list declares, at any depth."""
    names = set()
    for s in body:
        if isinstance(s, list):
            names |= declared_in(s)
        elif isinstance(s, Decl):
            names.add(s.name)
        else:
            for inner in sub_bodies(s):
                names |= declared_in(inner)
    return names


class Walker:
    """Checks one procedure or program body against what it can see."""
    def __init__(self, scope, variables, indexable=()):
        self.scope, self.variables, self.errors = scope, variables, []
        self.indexable = set(indexable)               # arrays and strings: written name[...], never name(...)

    def report(self, name, line, col, message):
        self.errors.append((line, col, message, len(name)))

    def variable(self, name, line, col):
        if name not in self.variables:
            self.report(name, line, col, f"'{name}' is not declared")

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
                self.report(e.name, e.line, e.col, f"unknown function or array '{e.name}'")
            self.arguments(e.args)
        elif isinstance(e, NamedArg):       # the name is a record field; only the value is looked up
            self.expr(e.value)
        elif isinstance(e, Slice):
            self.expr([e.lo, e.hi])
        elif isinstance(e, FieldAccess):    # the field depends on the value's type, so it is not checked
            self.expr(e.base)
            self.expr(e.args)
        elif isinstance(e, Unary):
            self.expr(e.operand)
        elif isinstance(e, Binary):
            self.expr([e.left, e.right])
        elif isinstance(e, ArrayLit):
            self.expr(e.items)
        elif isinstance(e, Gen):                  # its variable exists only inside the generator
            self.expr([e.lo, e.hi, e.step])
            added = e.var not in self.variables
            self.variables.add(e.var)
            self.expr(e.body)
            if added:
                self.variables.discard(e.var)
        elif isinstance(e, SelectExpr):
            self.expr(e.subject)
            for arm in e.arms:
                self.expr(arm.values)
                self.expr(arm.value)

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
        elif isinstance(s, Decl):
            self.expr(s.dims)
            self.expr(s.init)
        elif isinstance(s, Assign):
            self.expr([s.target, s.value])
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
            self.expr([s.lo, s.hi, s.step])
            self.statements(s.body)
        elif isinstance(s, DoWhile):
            self.expr(s.cond)
            self.statements(s.body)
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
        elif isinstance(s, Select):
            self.expr(s.subject)
            for case in s.cases:
                self.expr(case.values)
                self.statements(case.body)
        elif isinstance(s, Print):
            self.expr(s.args)
            self.expr(s.to)
        elif isinstance(s, Close):
            self.expr(s.file)
        elif isinstance(s, Stop):
            self.expr(s.code)
        elif isinstance(s, Return):
            self.expr(s.value)
        elif isinstance(s, ExprStmt):
            self.expr(s.expr)
        elif isinstance(s, Call):
            if s.receiver is not None:              # a record's function: the record decides which
                self.expr(s.receiver)
            elif s.name not in self.scope.funcs and s.name not in self.variables:
                self.report(s.name, s.line, s.col, f"unknown subroutine '{s.name}'")
            self.arguments(s.args)
        elif isinstance(s, Allocate):
            self.expr(s.dims)           # the parser has already checked the array's name


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
                        indexable_in(f.body) | indexable_params(f.params))
        walker.statements(f.body)
        errors += walker.errors
    if unit.program:
        scope = interp.program_scope
        walker = Walker(scope, set(scope.consts) | declared_in(unit.program.body), indexable_in(unit.program.body))
        walker.statements(unit.program.body)
        errors += walker.errors
    return sorted(errors, key=lambda err: err[:2])
