"""Works out which arguments of a call statement need `@`: the ones for an `out` or `inout` parameter. Used by
`boreas convert` (Fortran does not mark them) and by the tool that rewrites older sources."""
from .effects import CHANGES, statements
from .parser import ProcType


def all_procedures(unit):
    """Every procedure in the unit: (function, owner kind) for top-level ones, module ones and record methods."""
    plain, methods = {}, {}
    def add(table, f):
        table.setdefault(f.name, []).append(f)
    for f in unit.functions.values():
        add(plain, f)
    records = list(unit.records.values())
    for mod in unit.modules.values():
        for f in mod.functions.values():
            add(plain, f)
        records += mod.records.values()
        for d in mod.interfaces.values():
            for f in d.functions.values():
                add(methods, f)
    for d in unit.interfaces.values():
        for f in d.functions.values():
            add(methods, f)
    for r in records:
        for f in r.functions.values():
            add(methods, f)
    return plain, methods


def intents_of(candidates):
    """The intent of each parameter, when every candidate procedure agrees; else None."""
    seen = {tuple(p[2] for p in f.params) for f in candidates if f.is_sub}
    return next(iter(seen)) if len(seen) == 1 else None


def bodies(unit):
    for f in unit.functions.values():
        yield f, f.body
    for mod in unit.modules.values():
        for f in mod.functions.values():
            yield f, f.body
        for r in mod.records.values():
            for f in r.functions.values():
                yield f, f.body
    for r in unit.records.values():
        for f in r.functions.values():
            yield f, f.body
    if unit.program:
        yield None, unit.program.body


def needed(call, owner, plain, methods):
    """For the call statement `call`: the list of intents of the callee's parameters (the receiver first, for
    a method), or None when the callee is not known. `owner` is the procedure the call is in."""
    from .parser import Call
    if call.receiver is not None:
        return intents_of(methods.get(call.name, []))
    if call.name in ("add", "remove") and call.name not in plain:
        return ["inout", "in"]
    if owner is not None:
        for typ, name, _ in owner.params:
            if name == call.name and isinstance(typ, ProcType):
                return [p[1] for p in typ.params]
    return intents_of(plain.get(call.name, []))


def fill_marks(unit):
    """Set the `@` marks of every call statement in the unit from the callee's intents. Returns the calls
    that changed."""
    from .parser import Call
    plain, methods = all_procedures(unit)
    changed = []
    for owner, body in bodies(unit):
        for s in statements(body):
            if not isinstance(s, Call):
                continue
            intents = needed(s, owner, plain, methods)
            if intents is None:
                continue
            if s.receiver is not None:
                want_recv, rest = intents[:1], intents[1:]
            else:
                want_recv, rest = [], intents
            marks = [(i in CHANGES) for i in rest[:len(s.args)]] + [False] * max(0, len(s.args) - len(rest))
            receiver = bool(want_recv) and want_recv[0] in CHANGES
            if marks != list(s.marks or [False] * len(s.args)) or receiver != s.receiver_mark:
                s.marks, s.receiver_mark = marks, receiver
                changed.append(s)
    return changed
