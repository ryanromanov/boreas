"""Rewrite Fortran `goto` into Boreas loops, `exit` and `cycle`.

The Fortran front end leaves `Goto(n)` and `LabelMark(n)` nodes in the statement lists. A label's
mark sits in some list S and every jump to it lies inside S's statements (Fortran forbids jumping
into a block), so each label is handled where its mark is:

  * a loop that tests at the top and jumps back at the bottom becomes `do while`;
  * a jump back to a label becomes a `do` loop around the code from the label to the jump, and the
    jump becomes `cycle`;
  * a jump forward becomes a one-pass `do` loop around the code up to the label, and the jump
    becomes `exit`;
  * labels whose regions cross each other (an explicit stack machine, say) turn the whole list into
    a state machine: a `select case fallthrough` inside a loop, one case per label.

Whatever cannot be rewritten stays as a TODO comment."""
from dataclasses import dataclass

from .parser import (Assign, Bool, Case, Comment, Cycle, Do, DoWhile, Exit, If, Num, Return, Select, Stop, Unary, Var)


@dataclass
class Goto:
    label: int
    line: int = 0


@dataclass
class LabelMark:
    label: int


def child_lists(s):
    if isinstance(s, If):
        return [b for _, b in s.branches] + [s.orelse]
    if isinstance(s, Select):
        return [c.body for c in s.cases]
    if isinstance(s, (Do, DoWhile)):
        return [s.body]
    return []


def walk(stmts):
    """Every statement in `stmts`, nested ones included."""
    for s in stmts:
        yield s
        for sub in child_lists(s):
            yield from walk(sub)


def holds(stmt, target):
    return any(n is target for n in walk([stmt]))


def top_index(stmts, target):
    for i, s in enumerate(stmts):
        if s is target or holds(s, target):
            return i
    raise ValueError("statement not found")


def gotos_to(stmts, label):
    return [s for s in walk(stmts) if isinstance(s, Goto) and s.label == label]


def leaves(stmts):
    """Does this list always end by jumping away, so that nothing after it can run?"""
    return bool(stmts) and isinstance(stmts[-1], (Exit, Cycle, Return, Stop))


def rebinds(stmts):
    """Does an unlabelled exit/cycle outside any inner loop sit in `stmts`? A new loop would capture it."""
    for s in stmts:
        if isinstance(s, (Exit, Cycle)) and s.label is None:
            return True
        if not isinstance(s, (Do, DoWhile)) and any(rebinds(sub) for sub in child_lists(s)):
            return True
    return False


def replace(stmts, label, make, depth=0, deep=None):
    """Replace each Goto(label) by the statements `make()` returns. `deep` collects the new
    exit/cycle nodes that sit inside an inner loop (they need the loop's label)."""
    deep = [] if deep is None else deep
    out = []
    for s in stmts:
        if isinstance(s, Goto) and s.label == label:
            new = make()
            if depth:
                deep.extend(n for n in new if isinstance(n, (Exit, Cycle)))
            out.extend(new)
            continue
        inner = depth + isinstance(s, (Do, DoWhile))
        for sub in child_lists(s):
            sub[:] = replace(sub, label, make, inner, deep)[0]
        out.append(s)
    return out, deep


def drop_marks(stmts):
    stmts[:] = [s for s in stmts if not isinstance(s, LabelMark)]
    for s in stmts:
        for sub in child_lists(s):
            drop_marks(sub)


class Structurer:
    def __init__(self, fresh_name, todo):
        self.fresh_name = fresh_name       # fresh_name("pc") -> a variable name free in the procedure
        self.todo = todo                   # todo(line, message)
        self.new_vars = []

    def run(self, body):
        self.restructure(body)
        for s in list(walk(body)):
            for sub in child_lists(s) + [body]:
                for i, x in enumerate(sub):
                    if isinstance(x, Goto):
                        self.todo(x.line, f"goto {x.label} could not be rewritten")
                        sub[i] = Comment(f"TODO(convert): goto {x.label} could not be rewritten")
        drop_marks(body)
        return body

    def restructure(self, stmts):
        for s in stmts:
            for sub in child_lists(s):
                self.restructure(sub)
        targets = []
        for s in stmts:
            if isinstance(s, LabelMark) and gotos_to(stmts, s.label) and s.label not in targets:
                targets.append(s.label)
        if not targets:
            return
        spans = {}
        for label in targets:
            m = top_index(stmts, next(s for s in stmts if isinstance(s, LabelMark) and s.label == label))
            where = [top_index(stmts, g) for g in gotos_to(stmts, label)]
            if all(g < m for g in where):
                spans[label] = ("forward", min(where), m - 1)
            elif all(g > m for g in where):
                spans[label] = ("back", m, max(where))
            else:
                spans[label] = ("mixed", m, m)
        if not self.nested(spans):
            return self.machine(stmts, targets)
        for label in sorted(targets, key=lambda l: spans[l][2] - spans[l][1]):
            if gotos_to(stmts, label) and not self.rewrite(stmts, label):
                break
        else:
            return
        if any(gotos_to(stmts, l) for l in targets):
            self.machine(stmts, [l for l in targets if gotos_to(stmts, l)])

    @staticmethod
    def nested(spans):
        items = list(spans.values())
        if any(kind == "mixed" for kind, _, _ in items):
            return False
        for i, (_, a1, b1) in enumerate(items):
            for _, a2, b2 in items[i + 1:]:
                if a1 < a2 <= b1 < b2 or a2 < a1 <= b2 < b1:
                    return False
        return True

    def rewrite(self, stmts, label):
        mark = next(s for s in stmts if isinstance(s, LabelMark) and s.label == label)
        m = top_index(stmts, mark)
        found = gotos_to(stmts, label)
        where = [top_index(stmts, g) for g in found]
        if all(g < m for g in where):
            a, b = min(where), m - 1
            region = stmts[a:b + 1]
            if rebinds(region):
                return False
            if self.while_loop(stmts, label, a, b):
                return True
            done, deep = replace(region, label, lambda: [Exit(None, 0)])
            loop = DoWhile(Bool(True), done if leaves(done) else done + [Exit(None, 0)], None, 0)
            self.name(loop, f"skip_{label}", deep)
            stmts[a:b + 1] = [loop]
            return True
        if all(g > m for g in where):
            a, b = m + 1, max(where)
            region = stmts[a:b + 1]
            if rebinds(region):
                return False
            ends = isinstance(region[-1], Goto) and region[-1].label == label
            if ends:
                region = region[:-1]
            done, deep = replace(region, label, lambda: [Cycle(None, 0)])
            loop = DoWhile(Bool(True), done if ends else done + [Exit(None, 0)], None, 0)
            self.name(loop, f"again_{label}", deep)
            stmts[m:b + 1] = [loop]
            return True
        return False

    def while_loop(self, stmts, exit_label, a, b):
        """`10 if (c) goto 20; ...; goto 10; 20 continue` -> `do while (not c)`; `a`..`b` is the region
        from the `if` through the closing `goto 10`."""
        first, last = stmts[a], stmts[b]
        if not (a > 0 and isinstance(stmts[a - 1], LabelMark) and isinstance(last, Goto)
                and isinstance(first, If) and len(first.branches) == 1 and not first.orelse):
            return False
        cond, body = first.branches[0]
        back = stmts[a - 1].label
        if not (len(body) == 1 and isinstance(body[0], Goto) and body[0].label == exit_label) \
                or len(gotos_to(stmts, back)) != len(gotos_to(stmts[a:b + 1], back)) or last.label != back:
            return False
        inner = stmts[a + 1:b]
        if rebinds(inner):
            return False
        inner, deep = replace(inner, back, lambda: [Cycle(None, 0)])
        inner, more = replace(inner, exit_label, lambda: [Exit(None, 0)])
        loop = DoWhile(Unary("not", cond), inner, None, 0)
        self.name(loop, f"loop_{back}", deep + more)
        stmts[a - 1:b + 1] = [loop]
        return True

    @staticmethod
    def name(loop, label, deep):
        if deep:
            loop.label = label
            for n in deep:
                n.label = label

    def machine(self, stmts, labels):
        if rebinds(stmts):
            return
        pc = self.fresh_name("pc")
        self.new_vars.append(pc)
        machine = "machine"
        number = {l: n for n, l in enumerate(labels, 1)}
        cases, current = [], []
        for s in stmts:
            if isinstance(s, LabelMark) and s.label in number:
                cases.append(Case([Num(len(cases))], current, 0))
                current = []
            else:
                current.append(s)
        cases.append(Case([Num(len(cases))], current, 0))
        deep = []
        for label, n in number.items():
            for case in cases:
                case.body[:], d = replace(case.body, label,
                                          lambda n=n: [Assign(Var(pc), Num(n), 0), Cycle(None, 0)])
                deep += d
        loop = DoWhile(Bool(True), [Select(Var(pc), cases, 0, True), Exit(None, 0)], None, 0)
        self.name(loop, machine, deep)
        stmts[:] = [Assign(Var(pc), Num(0), 0), loop]
