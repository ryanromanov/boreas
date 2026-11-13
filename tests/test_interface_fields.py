import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from test_interfaces import DEFS
from boreas.lexer import LangError

SCENE = DEFS + """
record scene
  figure[2] :: items
  figure :: highlight
  integer :: count = 0
end record
"""


def run(body, defs=SCENE):
    return run_bor(defs + "\nprogram t\n" + body + "\nend program\n")


class InterfaceFieldTests(unittest.TestCase):
    def test_a_field_and_an_array_field_hold_records_that_fit(self):
        out = run("""  scene :: sc
  sc.items[1] = circle(r = 1.0)
  sc.items[2] = rect(w = 2.0, h = 3.0)
  sc.highlight = sc.items[2]
  sc.count = 2
  println(sc.items[1].area(), sc.items[2].area(), sc.highlight.name())""")
        self.assertEqual(out, ["3.0 6.0 rect"])

    def test_a_field_starts_unassigned(self):
        with self.assertRaises(LangError) as cm:
            run("  scene :: sc\n  println(sc.highlight.area())")
        self.assertIn("used before it is assigned", str(cm.exception))
        with self.assertRaises(LangError) as cm:
            run("  scene :: sc\n  println(sc.items[1].area())")
        self.assertIn("assigned", str(cm.exception))

    def test_a_record_that_does_not_fit_is_refused(self):
        defs = SCENE + "\nrecord blob\n  integer :: n = 1\nend record\n"
        with self.assertRaises(LangError) as cm:
            run("  scene :: sc\n  sc.highlight = blob()", defs)
        self.assertIn("figure", str(cm.exception))

    def test_only_the_interfaces_functions_are_available_through_a_field(self):
        with self.assertRaises(LangError) as cm:
            run("  scene :: sc\n  sc.items[1] = circle()\n  println(sc.items[1].r)")
        self.assertIn("not available through figure", str(cm.exception))
        with self.assertRaises(LangError) as cm:
            run("  scene :: sc\n  sc.highlight = circle()\n  call sc.highlight.grow(2.0)")
        self.assertIn("not part of figure", str(cm.exception))

    def test_copying_a_record_copies_the_values_in_it(self):
        out = run("""  scene :: a
  a.highlight = circle(r = 1.0)
  scene :: b = a
  b.highlight = rect(w = 2.0, h = 2.0)
  println(a.highlight.name(), b.highlight.name())""")
        self.assertEqual(out, ["circle rect"])

    def test_select_type_works_on_a_field(self):
        out = run("""  scene :: sc
  sc.items[1] = circle(r = 2.0)
  select type sc.items[1] as p
    case circle
      println("radius", p.r)
    case default
      println("other")
  end select""")
        self.assertEqual(out, ["radius 2.0"])

    def test_a_field_can_be_given_when_the_record_is_built(self):
        out = run("  scene :: sc = scene(highlight = circle(r = 3.0), count = 1)\n  println(sc.highlight.area(), sc.count)")
        self.assertEqual(out, ["27.0 1"])

    def test_an_interface_field_cannot_be_optional(self):
        defs = DEFS + "\nrecord holder\n  figure? :: f\nend record\n"
        with self.assertRaises(LangError) as cm:
            run("  println(1)", defs)
        self.assertIn("cannot be optional", str(cm.exception))

    def test_an_array_field_of_an_interface_needs_a_literal_size(self):
        defs = DEFS + "\nrecord holder\n  figure[n] :: f\nend record\n"
        with self.assertRaises(LangError) as cm:
            run("  println(1)", defs)
        self.assertIn("needs a literal size", str(cm.exception))

    def test_the_checker_accepts_interface_fields(self):
        from boreas.cli import check_errors
        src = SCENE + "\nprogram t\n  scene :: sc\n  sc.items[1] = circle()\n  println(sc.items[1].area())\nend program\n"
        self.assertEqual(check_errors(src), [])

    def test_an_interface_field_of_a_record_inside_an_array(self):
        out = run("""  scene[2] :: all
  all[2].highlight = rect(w = 1.0, h = 4.0)
  println(all[2].highlight.area())""")
        self.assertEqual(out, ["4.0"])

    def test_an_out_record_may_leave_an_interface_field_unassigned(self):
        out = run("""  scene :: sc
  call fill(sc)
  println(sc.count)""", SCENE + "\nsubroutine fill(out scene :: s)\n  @s.count = 4\nend subroutine\n")
        self.assertEqual(out, ["4"])

    def test_printing_a_record_with_an_unassigned_interface_field_is_an_error(self):
        with self.assertRaises(LangError) as cm:
            run("  scene :: sc\n  println(sc)")
        self.assertIn("has no value yet", str(cm.exception))
