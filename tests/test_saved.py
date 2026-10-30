import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, run_bor, run_gfortran, todos
from boreas.fortran import convert
from boreas.verify import same_output

TWICE = "program p\n  implicit none\n  integer :: k\n  call s(k)\n  call s(k)\n  print *, k\nend program\n"
ONCE = "program p\n  implicit none\n  integer :: k\n  call s(k)\n  print *, k\nend program\n"


def conv(sub, main=TWICE):
    return convert(sub + main)


def run_both(test, sub, main=TWICE):
    """Convert, run, and (if gfortran is there) compare with the original."""
    text, findings = conv(sub, main)
    test.assertEqual(todos(findings), [])
    got = run_bor(text)
    if GFORTRAN:
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".f90", delete=False) as f:
            f.write(sub + main)
        try:
            test.assertTrue(same_output("\n".join(got), run_gfortran(f.name)), f"{got} vs gfortran")
        finally:
            os.unlink(f.name)
    return text, got


class SavedLocalTests(unittest.TestCase):
    def test_counter_initialised_in_its_declaration_moves_to_a_module(self):
        text, got = run_both(self, "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: calls = 0\n"
                                   "  calls = calls + 1\n  n = calls\nend subroutine\n")
        self.assertEqual(got, ["2"])
        self.assertIn("module s_state", text)
        self.assertIn("public integer :: calls = 0", text)
        self.assertIn("use s_state: calls", text)
        self.assertIn("update calls = calls + 1", text)

    def test_save_attribute_without_a_value(self):
        _, got = run_both(self, "subroutine s(n)\n  integer, intent(out) :: n\n  integer, save :: total\n"
                                "  total = total + 1\n  n = total\nend subroutine\n")
        self.assertEqual(got, ["2"])

    def test_save_statement_with_names(self):
        text, got = run_both(self, "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: total, other\n"
                                   "  save total\n  other = 5\n  total = total + other\n  n = total\nend subroutine\n")
        self.assertEqual(got, ["10"])
        self.assertIn("public integer :: total", text)
        self.assertNotIn("public integer :: other", text)

    def test_bare_save_moves_only_the_locals_that_keep_a_value(self):
        text, got = run_both(self, "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: a, b, c\n  save\n"
                                   "  a = a + 1\n  b = 2\n  c = 3\n  n = a + b + c\nend subroutine\n")
        self.assertEqual(got, ["7"])
        self.assertIn("public integer :: a", text)
        self.assertNotIn("public integer :: b", text)            # overwritten before it is read

    def test_array_element_updates_count(self):
        text, got = run_both(self, "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: hist(3) = 0\n"
                                   "  hist(2) = hist(2) + 1\n  n = hist(2)\nend subroutine\n")
        self.assertEqual(got, ["2"])
        self.assertIn("update hist(2) = hist(2) + 1", text)

    def test_passing_the_variable_to_an_inout_argument_counts(self):
        sub = ("subroutine bump(m)\n  integer, intent(inout) :: m\n  m = m + 1\nend subroutine\n"
               "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: c = 0\n  call bump(c)\n  n = c\nend subroutine\n")
        _, got = run_both(self, sub)
        self.assertEqual(got, ["2"])

    def test_module_procedures_use_the_state_through_their_module(self):
        sub = ("module m\n  implicit none\ncontains\n  subroutine s(n)\n    integer, intent(out) :: n\n"
               "    integer :: calls = 0\n    calls = calls + 1\n    n = calls\n  end subroutine s\nend module m\n")
        main = "program p\n  use m\n  implicit none\n  integer :: k\n  call s(k)\n  call s(k)\n  print *, k\nend program\n"
        text, got = run_both(self, sub, main)
        self.assertEqual(got, ["2"])

    def test_a_function_that_changes_its_saved_local_becomes_a_subroutine(self):
        sub = "function next()\n  integer :: next\n  integer :: n = 0\n  n = n + 1\n  next = n\nend function\n"
        main = "program p\n  integer :: next, a\n  a = next()\n  a = a + next()\n  print *, a\nend program\n"
        text, got = run_both(self, sub, main)
        self.assertEqual(got, ["3"])
        self.assertIn("subroutine next(out integer :: next_result)", text)

    def test_a_declaration_that_uses_other_names_stays_a_todo(self):
        sub = ("module cfg\n  integer, parameter :: start = 5\nend module\n"
               "subroutine s(n)\n  use cfg\n  integer, intent(out) :: n\n  integer :: calls = start\n"
               "  calls = calls + 1\n  n = calls\nend subroutine\n")
        _, findings = conv(sub)
        self.assertEqual(len([f for f in todos(findings) if "keeps its value between calls" in f[2]]), 1)


class CalledOnceTests(unittest.TestCase):
    SUB = "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: calls = 0\n  calls = calls + 1\n  n = calls\nend subroutine\n"

    def converted(self, main):
        text, findings = conv(self.SUB, main)
        self.assertEqual(todos(findings), [])
        return text

    def test_a_procedure_called_once_keeps_its_local(self):
        text = self.converted(ONCE)
        self.assertNotIn("s_state", text)
        self.assertEqual(run_bor(text), ["1"])

    def test_a_call_in_a_loop_is_not_once(self):
        main = "program p\n  integer :: k, i\n  do i = 1, 3\n    call s(k)\n  end do\n  print *, k\nend program\n"
        text = self.converted(main)
        self.assertIn("s_state", text)
        self.assertEqual(run_bor(text), ["3"])

    def test_two_call_sites_are_not_once(self):
        self.assertIn("s_state", self.converted(TWICE))

    def test_called_once_through_a_procedure_called_once(self):
        sub = self.SUB + "subroutine outer(n)\n  integer, intent(out) :: n\n  call s(n)\nend subroutine\n"
        main = "program p\n  integer :: k\n  call outer(k)\n  print *, k\nend program\n"
        text, findings = conv(sub, main)
        self.assertNotIn("s_state", text)

    def test_called_from_a_procedure_called_twice_is_not_once(self):
        sub = self.SUB + "subroutine outer(n)\n  integer, intent(out) :: n\n  call s(n)\nend subroutine\n"
        main = "program p\n  integer :: k\n  call outer(k)\n  call outer(k)\n  print *, k\nend program\n"
        text, findings = conv(sub, main)
        self.assertIn("s_state", text)
        self.assertEqual(run_bor(text), ["2"])


class HarmlessTests(unittest.TestCase):
    def test_initialised_but_never_changed_is_not_moved(self):
        text, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: base = 10\n"
                              "  n = base + 1\nend subroutine\n")
        self.assertEqual(todos(findings), [])
        self.assertNotIn("s_state", text)
        self.assertEqual(run_bor(text), ["11"])

    def test_assigned_before_it_is_read_is_not_moved(self):
        text, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: t = 0\n"
                              "  t = 4\n  t = t + 1\n  n = t\nend subroutine\n")
        self.assertNotIn("s_state", text)
        self.assertEqual(run_bor(text), ["5"])

    def test_loop_variable_with_an_initializer_is_not_moved(self):
        text, _ = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: i = 0\n"
                       "  n = 0\n  do i = 1, 3\n    n = n + i\n  end do\nend subroutine\n")
        self.assertNotIn("s_state", text)
        self.assertEqual(run_bor(text), ["6"])

    def test_conditional_first_write_is_moved(self):
        text, _ = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: last = 0\n"
                       "  if (n > 100) last = 5\n  n = last\nend subroutine\n")
        self.assertIn("s_state", text)

    def test_parameters_and_main_program_variables_are_not_saved(self):
        src = ("program p\n  implicit none\n  integer, parameter :: big = 100\n  integer :: count = 0\n"
               "  count = count + big\n  print *, count\nend program\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["100"])

    def test_dummy_arguments_and_results_are_not_saved(self):
        text, findings = conv("function f(x)\n  integer, intent(in) :: x\n  integer :: f\n  f = x + 1\nend function\n"
                              "subroutine s(n)\n  integer, intent(out) :: n\n  n = f(1)\nend subroutine\n")
        self.assertEqual(todos(findings), [])


if __name__ == "__main__":
    unittest.main()
