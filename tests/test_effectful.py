import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, run_bor, todos
from boreas.fortran import convert

# integer function bump(n) changes n and returns the old value
BUMP = ("integer function bump(n)\n  integer, intent(inout) :: n\n  bump = n\n  n = n + 1\nend function\n")


def program(body, decls="  integer :: k, r\n  logical :: ok"):
    return BUMP + f"program p\n  implicit none\n{decls}\n  k = 5\n{body}\nend program\n"


def run(body, decls="  integer :: k, r\n  logical :: ok"):
    text, findings = convert(program(body, decls))
    return text, findings, run_bor(text) if not todos(findings) else None


class EffectfulFunctionTests(unittest.TestCase):
    def test_assignment_passes_the_target_directly(self):
        text, findings, out = run("  r = bump(k)\n  print *, r, k")
        self.assertEqual(todos(findings), [])
        self.assertIn("call bump(k, r)", text)
        self.assertEqual(out, ["5 6"])

    def test_assignment_to_an_element_uses_a_temporary(self):
        text, _, out = run("  v(2) = bump(k)\n  print *, v(2), k", "  integer :: k\n  integer :: v(3)")
        self.assertIn("bump_value", text)
        self.assertEqual(out, ["5 6"])

    def test_call_inside_an_expression_and_in_print(self):
        _, _, out = run("  r = 10 * bump(k) + 1\n  print *, r, bump(k), k")
        self.assertEqual(out, ["51 6 7"])

    def test_if_condition_and_single_line_if(self):
        _, _, out = run("  if (bump(k) == 5) print *, 'five'\n  if (bump(k) == 6) then\n    print *, 'six'\n  end if\n  print *, k")
        self.assertEqual(out, ["five", "six", "7"])

    def test_loop_bounds_and_select_subject(self):
        _, _, out = run("  do r = 1, bump(k)\n    print *, r\n  end do\n  select case (bump(k))\n"
                        "  case (6)\n    print *, 'six'\n  end select")
        self.assertEqual(out, ["1", "2", "3", "4", "5", "six"])

    def test_calls_keep_left_to_right_order(self):
        _, _, out = run("  print *, bump(k), bump(k), bump(k)")
        self.assertEqual(out, ["5 6 7"])

    def test_call_argument(self):
        extra = "subroutine show(n)\n  integer, intent(in) :: n\n  print *, n\nend subroutine\n"
        text, _ = convert(extra + program("  call show(bump(k))"))
        self.assertEqual(run_bor(text), ["5"])

    def test_recursive_function(self):
        src = ("recursive integer function depth(n, d) result(r)\n  integer, intent(inout) :: n\n  integer, intent(in) :: d\n"
               "  n = n + 1\n  if (d == 0) then\n    r = 0\n  else\n    r = 1 + depth(n, d - 1)\n  end if\nend function\n"
               "program p\n  integer :: count\n  count = 0\n  print *, depth(count, 3), count\nend program\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["3 4"])

    def test_caller_dummy_becomes_inout(self):
        src = (BUMP + "subroutine twice(m)\n  integer :: m\n  integer :: t\n  t = bump(m)\n  t = bump(m)\nend subroutine\n"
               "program p\n  integer :: k\n  k = 1\n  call twice(k)\n  print *, k\nend program\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertIn("subroutine twice(inout integer :: m)", text)
        self.assertEqual(run_bor(text), ["3"])

    def test_conditional_evaluation_is_a_todo(self):
        for stmt in ("  ok = (k > 0) .and. (bump(k) == 5)\n", "  ok = (k > 0) .or. (bump(k) == 5)\n"):
            with self.subTest(stmt=stmt):
                _, findings, _ = run(stmt)
                self.assertEqual(len(todos(findings)), 1)
                self.assertIn("evaluated only conditionally", todos(findings)[0][2])

    def test_else_if_and_do_while_conditions_are_todos(self):
        _, findings, _ = run("  if (k > 10) then\n    r = 1\n  else if (bump(k) == 5) then\n    r = 2\n  end if")
        self.assertEqual(len(todos(findings)), 1)
        _, findings, _ = run("  do while (bump(k) < 8)\n    r = 1\n  end do")
        self.assertEqual(len(todos(findings)), 1)

    def test_functions_that_do_not_modify_arguments_stay_functions(self):
        text, _ = convert("integer function twice(n)\n  integer, intent(in) :: n\n  twice = 2 * n\nend function\n"
                          "program p\n  print *, twice(4)\nend program\n")
        self.assertIn("function twice(integer :: n) -> integer", text)
        self.assertEqual(run_bor(text), ["8"])


class EffectfulFixtureTests(unittest.TestCase):
    check = FixtureTests.check

    def test_fixture_matches_gfortran(self):
        got = self.check("effectful.f90")
        self.assertEqual(got[0], "5 6")


if __name__ == "__main__":
    unittest.main()
