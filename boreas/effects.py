"""A function stays free of side effects on module variables. `check_writes` (in the parser) already stops a
function from assigning to one. This pass, which runs once everything is linked, stops the two ways around it:
passing a module variable to an `out` or `inout` parameter, and calling a subroutine that changes one (directly
or through other subroutines)."""
from .lexer import LangError
from .parser import (Allocate, Apply, Assign, Call, Deallocate, FieldAccess, MultiAssign, Var, root_name,
                     sub_bodies)

CHANGES = ("out", "inout")


def statements(body):
    for s in body:
        if isinstance(s, list):
            yield from statements(s)
        else:
            yield s
            for inner in sub_bodies(s):
                yield from statements(inner)


def root_of(arg):
    """The variable an argument names (`x`, `x[i]`, `x.f`), or None for any other expression."""
    if isinstance(arg, Var) or (isinstance(arg, Apply) and arg.bracket):
        return arg.name
    if isinstance(arg, FieldAccess):
        return root_name(arg)
    return None


class Effects:
    def __init__(self, interp):
        self.interp = interp
        self.direct = {}          # id(f) -> {id(slot): name} of the module variables f changes itself
        self.calls = {}           # id(f) -> [(callee, line)]
        self.passes = {}          # id(f) -> [(line, variable, callee, intent)]

    def module_vars(self, f, scope):
        return {n: slot for n, slot in scope.consts.items()
                if slot[3] == "module" and n not in {name for _, name, _ in f.params}}

    def scan(self, f):
        scope = self.interp.scopes[id(f)]
        shared = self.module_vars(f, scope)
        direct, calls, passes = {}, [], []
        for s in statements(f.body):
            if isinstance(s, Assign):
                targets = [s.target]
            elif isinstance(s, MultiAssign):
                targets = [t for t, _ in s.targets if t is not None]
            elif isinstance(s, (Allocate, Deallocate)):
                if s.name in shared:
                    direct[id(shared[s.name])] = s.name
                continue
            elif isinstance(s, Call):
                if s.name in ("add", "remove") and s.receiver is None and s.args:
                    name = root_of(s.args[0])
                    if name in shared:
                        direct[id(shared[name])] = name
                    continue
                g = None if s.receiver is not None or s.name in scope.consts else scope.funcs.get(s.name)
                if g is not None and g.is_sub:
                    calls.append((g, s.line))
                    for (typ, _, intent), arg in zip(g.params, s.args):
                        name = root_of(arg)
                        if intent in CHANGES and name in shared:
                            direct[id(shared[name])] = name
                            passes.append((s.line, name, g, intent))
                continue
            else:
                continue
            for t in targets:
                name = root_of(t)
                if name in shared:
                    direct[id(shared[name])] = name
        self.direct[id(f)], self.calls[id(f)], self.passes[id(f)] = direct, calls, passes

    def check(self, functions):
        for f in functions:
            self.scan(f)
        writes = {id(f): dict(self.direct[id(f)]) for f in functions}
        changed = True
        while changed:                                   # a subroutine also changes what the ones it calls change
            changed = False
            for f in functions:
                for g, _ in self.calls[id(f)]:
                    for k, name in writes.get(id(g), {}).items():
                        if k not in writes[id(f)]:
                            writes[id(f)][k] = name
                            changed = True
        for f in functions:
            if f.is_sub:
                continue
            for line, name, g, intent in self.passes[id(f)]:
                raise LangError(f"line {line}: a function cannot change the module variable '{name}', which is "
                                f"passed to the {intent} parameter of '{g.name}'; make '{f.name}' a subroutine")
            for g, line in self.calls[id(f)]:
                if writes.get(id(g)):
                    name = next(iter(writes[id(g)].values()))
                    raise LangError(f"line {line}: a function cannot call '{g.name}', which changes the module "
                                    f"variable '{name}'; make '{f.name}' a subroutine")


def check_effects(interp):
    unit = interp.unit
    functions = list(unit.functions.values())
    for mod in unit.modules.values():
        functions += mod.functions.values()
        for r in mod.records.values():
            functions += r.functions.values()
    for r in unit.records.values():
        functions += r.functions.values()
    Effects(interp).check([f for f in functions if id(f) in interp.scopes])
