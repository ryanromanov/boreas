import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from test_interfaces import DEFS
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

DEFS2 = DEFS + """
interface sized
  function area(self) -> real
end interface
"""


def run(body, defs=DEFS2):
    return run_bor(defs + "\nprogram t\n" + body + "\nend program\n")


class InlineIsTests(unittest.TestCase):
    def test_the_branch_runs_with_the_value_as_that_record(self):
        body = """  figure :: f = circle(r = 2.0)
  if f is circle as c then
    println("radius", c.r)
  else
    println("something else")
  end if"""
        self.assertEqual(run(body), ["radius 2.0"])

    def test_else_runs_for_any_other_record(self):
        body = """  figure :: f = rect(w = 2.0, h = 3.0)
  if f is circle as c then
    println("radius", c.r)
  else
    println("something else")
  end if"""
        self.assertEqual(run(body), ["something else"])

    def test_no_else_means_nothing_happens(self):
        body = """  figure :: f = rect()
  if f is circle as c then
    println("circle")
  end if
  println("done")"""
        self.assertEqual(run(body), ["done"])

    def test_else_if_chains_test_each_type(self):
        body = """  figure :: f = rect(w = 2.0, h = 3.0)
  if f is circle as c then
    println("radius", c.r)
  else if f is rect as r then
    println("width", r.w)
  else
    println("other")
  end if"""
        self.assertEqual(run(body), ["width 2.0"])

    def test_the_test_may_leave_out_the_name(self):
        body = """  figure :: f = circle()
  if f is circle then
    println("a circle")
  end if"""
        self.assertEqual(run(body), ["a circle"])

    def test_an_interface_matches_any_record_that_fits_it(self):
        body = """  figure :: f = rect(w = 2.0, h = 3.0)
  if f is sized as p then
    println("area", p.area())
  end if"""
        self.assertEqual(run(body), ["area 6.0"])

    def test_through_an_interface_name_only_its_functions_are_available(self):
        body = """  figure :: f = rect()
  if f is sized as p then
    println(p.name())
  end if"""
        with self.assertRaises(LangError) as cm:
            run(body)
        self.assertIn("not part of sized", str(cm.exception))

    def test_the_name_is_a_copy_and_goes_back_by_assignment(self):
        body = """  figure :: f = circle(r = 1.0)
  if f is circle as c then
    c.r = 5.0
    println(f.area())
    f = c
  end if
  println(f.area())"""
        self.assertEqual(run(body), ["3.0", "75.0"])

    def test_the_name_exists_only_in_its_branch(self):
        src = DEFS2 + "\nprogram t\n  figure :: f = circle()\n  if f is circle as c then\n    println(c.r)\n  end if\n  println(c.r)\nend program\n"
        messages = [m for _, _, m, _ in check_errors(src)]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("'c'", messages[0])

    def test_an_unknown_type_is_reported(self):
        src = DEFS2 + "\nprogram t\n  figure :: f = circle()\n  if f is blob as b then\n    println(1)\n  end if\nend program\n"
        messages = [m for _, _, m, _ in check_errors(src)]
        self.assertEqual(messages, ["no record or interface called 'blob'"])

    def test_testing_something_that_is_not_a_record_is_an_error(self):
        with self.assertRaises(LangError) as cm:
            run("  integer :: n = 3\n  if n is circle then\n    println(1)\n  end if")
        self.assertIn("'is' needs a record or interface value", str(cm.exception))

    def test_an_unassigned_value_is_an_error(self):
        with self.assertRaises(LangError) as cm:
            run("  figure :: f\n  if f is circle then\n    println(1)\n  end if")
        self.assertIn("used before it is assigned", str(cm.exception))

    def test_it_works_on_a_field_and_inside_a_loop(self):
        defs = DEFS2 + "\nrecord scene\n  figure[2] :: items\nend record\n"
        body = """  scene :: sc
  sc.items[1] = circle(r = 2.0)
  sc.items[2] = rect(w = 1.0, h = 1.0)
  integer :: i
  do i = 1, 2
    if sc.items[i] is circle as c then
      println("circle", c.r)
    else
      println("not a circle")
    end if
  end do"""
        self.assertEqual(run(body, defs), ["circle 2.0", "not a circle"])

    def test_printing_round_trips(self):
        src = DEFS2 + "\nprogram t\n  figure :: f = circle()\n  if f is circle as c then\n    println(c.r)\n  else if f is sized then\n    println(1)\n  end if\nend program\n"
        text = to_source(parse(src))
        self.assertIn("if f is circle as c then", text)
        self.assertIn("else if f is sized then", text)
        self.assertEqual(to_source(parse(text)), text)

    def test_names_called_is_and_as_still_work(self):
        self.assertEqual(run("  integer :: is = 1\n  integer :: as = 2\n  if is < as then\n    println(is + as)\n  end if"), ["3"])

    def test_an_is_test_cannot_be_combined_with_other_conditions(self):
        for cond in ("ok and f is circle as c", "not f is circle", "f is circle as c and c.r > 1.0"):
            src = DEFS2 + f"\nprogram t\n  logical :: ok = true\n  figure :: f = circle()\n  if {cond} then\n    println(1)\n  end if\nend program\n"
            with self.assertRaises(LangError, msg=cond):
                run_bor(src)
            errors = check_errors(src)
            self.assertEqual(len(errors), 1, cond)


class SceneExampleTests(unittest.TestCase):
    def test_the_scene_example_runs_and_checks_clean(self):
        path = os.path.join(os.path.dirname(__file__), "..", "examples", "scene.bor")
        with open(path) as f:
            src = f.read()
        self.assertEqual(check_errors(src), [])
        self.assertEqual(run_bor(src), ["circle area = 12.566", "rect area = 12.000", "circle area = 3.142",
                                        "total = 27.708", "first radius: 2.0", "second is not a circle"])
