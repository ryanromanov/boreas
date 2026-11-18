"""The shared tree walker (boreas/tree.py): every node kind is classified, passes see inside every construct, and
the helpers the old per-pass walkers provided still give the same answers."""
import dataclasses, os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from boreas import gotos, parser, tree
from boreas.cli import check_errors
from boreas.parser import parse


class ClassificationTests(unittest.TestCase):
    def test_every_node_class_is_classified(self):
        classified = set(tree.STATEMENTS) | set(tree.EXPRESSIONS) | set(tree.STRUCTURE)
        for name, cls in vars(parser).items():
            if isinstance(cls, type) and dataclasses.is_dataclass(cls) and cls.__module__ == parser.__name__ \
                    and not cls.__dataclass_params__.frozen:
                self.assertIn(cls, classified, f"{name} is a syntax-tree node: add it to STATEMENTS, EXPRESSIONS or "
                                               f"STRUCTURE in boreas/tree.py")

    def test_the_converters_statements_are_registered(self):
        self.assertIn(gotos.Goto, tree.STATEMENTS)
        self.assertIn(gotos.LabelMark, tree.STATEMENTS)


def program(body, extra=""):
    return extra + "program t\n" + body + "\nend program\n"


# One undeclared name, `nope`, hidden inside each construct: the name check must find exactly that one.
INSIDE = {
    "assignment": "  integer :: x\n  x = nope",
    "if": "  integer :: x = 1\n  if x == 1 then\n    println(nope)\n  end if",
    "else": "  integer :: x = 1\n  if x == 1 then\n    println(x)\n  else\n    println(nope)\n  end if",
    "do": "  integer :: i\n  do i = 1, nope\n    println(i)\n  end do",
    "do body": "  integer :: i\n  do i = 1, 2\n    println(nope)\n  end do",
    "do each": "  integer[3] :: a = [1, 2, 3]\n  integer :: v\n  do v in a\n    println(nope)\n  end do",
    "do while": "  integer :: i = 0\n  do while i < nope\n    i = i + 1\n  end do",
    "where": "  integer[2] :: a = [1, 2]\n  where a > nope then\n    a = 0\n  end where",
    "where else": "  integer[2] :: a = [1, 2]\n  where a > 1 then\n    a = 0\n  elsewhere\n    a = nope\n  end where",
    "select": "  integer :: x = 1\n  select case x\n  case 1\n    println(nope)\n  end select",
    "select value": "  integer :: x = 1\n  select case x\n  case nope\n    println(1)\n  end select",
    "print": "  println(1, nope)",
    "stop": "  stop nope",
    "generator": "  integer[3] :: a = [(nope do i = 1, 3)]",
    "matrix literal": "  integer[2, 2] :: a = [1, 2; 3, nope]",
    "array literal": "  integer[2] :: a = [1, nope]",
    "map literal": '  map[string, integer] :: m = {"a": nope}',
    "declaration": "  integer[nope] :: a",
    "slice": "  integer[3] :: a = [1, 2, 3]\n  println(a[1:nope])",
    "allocate": "  allocatable integer[] :: a\n  allocate a[nope]",
    "call argument": "  call show(nope)",
    "select expression": "  integer :: x = 1\n  integer :: y = select case x\n    case 1 -> nope\n    case default -> 0\n  end select",
}
SUBS = "subroutine show(integer :: x)\n  println(x)\nend subroutine\n"


class InsideEveryConstructTests(unittest.TestCase):
    def test_the_name_check_looks_inside_each_construct(self):
        for what, body in INSIDE.items():
            try:
                errors = check_errors(program(body, SUBS))
            except Exception as err:            # a parse error in the sample itself is a mistake in this test
                self.fail(f"{what}: {err}")
            names = [e for e in errors if "nope" in e[2]]
            self.assertEqual(len(names), 1, f"{what}: {errors}")


class WalkerTests(unittest.TestCase):
    SRC = ("program t\n  integer :: x = 1\n  integer :: i\n  if x == 1 then\n    println([1, 2; 3, 4])\n  end if\n"
           "  do i = 1, 3\n    call s(x)\n  end do\n  select case x\n  case 1\n    println(x)\n  end select\nend program\n")

    def test_statements_in_finds_nested_ones(self):
        got = [type(s).__name__ for s in tree.statements_in(parse(self.SRC).program.body)]
        self.assertEqual(got, ["Decl", "Decl", "If", "Print", "Do", "Call", "Select", "Print"])

    def test_walk_reaches_expressions(self):
        kinds = {type(n).__name__ for n in tree.walk(parse(self.SRC).program.body)}
        self.assertTrue({"MatrixLit", "Binary", "Var", "Num"} <= kinds)

    def test_bodies_follow_the_conventions(self):
        ifs = [s for s in tree.statements_in(parse(self.SRC).program.body) if isinstance(s, parser.If)][0]
        self.assertEqual(len(tree.bodies(ifs)), 2)               # the then-branch and the (empty) else
        select = [s for s in tree.statements_in(parse(self.SRC).program.body) if isinstance(s, parser.Select)][0]
        self.assertEqual(len(tree.bodies(select)), 1)

    def test_rewrite_changes_nodes_in_place(self):
        unit = parse(self.SRC)
        tree.rewrite(unit, lambda n: parser.Num(0) if isinstance(n, parser.Var) else n)
        self.assertFalse([n for n in tree.walk(unit.program.body) if isinstance(n, parser.Var)])


if __name__ == "__main__":
    unittest.main()
