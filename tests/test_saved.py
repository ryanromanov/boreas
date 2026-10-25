import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor, todos
from boreas.fortran import convert

MAIN = "program p\n  implicit none\n  integer :: k\n  call s(k)\n  call s(k)\n  print *, k\nend program\n"


def conv(sub):
    text, findings = convert(sub + MAIN)
    return text, findings


def saved_todos(findings):
    return [f for f in todos(findings) if "keeps its value between calls" in f[2]]


class SavedLocalTests(unittest.TestCase):
    def test_counter_initialised_in_its_declaration_is_a_todo(self):
        text, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: calls = 0\n"
                              "  calls = calls + 1\n  n = calls\nend subroutine\n")
        self.assertEqual(len(saved_todos(findings)), 1)
        msg = saved_todos(findings)[0][2]
        self.assertIn("'calls' (initialised in its declaration, which implies save)", msg)
        self.assertEqual(saved_todos(findings)[0][0], 3)                # the line of the declaration
        self.assertIn("! TODO(convert): 'calls'", text)                 # printed above the procedure
        self.assertIn("1 construct(s) could not be translated", text)   # and counted in the header

    def test_save_attribute_without_a_value(self):
        _, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer, save :: total\n"
                           "  total = total + 1\n  n = total\nend subroutine\n")
        self.assertIn("(declared save)", saved_todos(findings)[0][2])

    def test_save_statement_with_names(self):
        _, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: total, other\n  save total\n"
                           "  other = 5\n  total = total + other\n  n = total\nend subroutine\n")
        self.assertEqual(len(saved_todos(findings)), 1)
        self.assertIn("'total' (named in a save statement)", saved_todos(findings)[0][2])

    def test_bare_save_statement_flags_each_local_that_changes(self):
        _, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: a, b, c\n  save\n"
                           "  a = a + 1\n  b = 2\n  c = 3\n  n = a + b + c\nend subroutine\n")
        flagged = [f[2].split("'")[1] for f in saved_todos(findings)]
        self.assertEqual(flagged, ["a"])                  # b and c are overwritten before they are read

    def test_array_element_updates_count(self):
        _, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: hist(3) = 0\n"
                           "  hist(2) = hist(2) + 1\n  n = hist(2)\nend subroutine\n")
        self.assertEqual(len(saved_todos(findings)), 1)

    def test_passing_the_variable_to_an_inout_argument_counts(self):
        sub = ("subroutine bump(m)\n  integer, intent(inout) :: m\n  m = m + 1\nend subroutine\n"
               "subroutine s(n)\n  integer, intent(out) :: n\n  integer :: c = 0\n  call bump(c)\n  n = c\nend subroutine\n")
        _, findings = conv(sub)
        self.assertEqual(len(saved_todos(findings)), 1)

    def test_internal_procedures_are_checked_too(self):
        src = ("program p\n  implicit none\n  integer :: k\n  call s(k)\n  print *, k\ncontains\n"
               "  subroutine s(n)\n    integer, intent(out) :: n\n    integer :: calls = 0\n    calls = calls + 1\n"
               "    n = calls\n  end subroutine s\nend program\n")
        _, findings = convert(src)
        self.assertEqual(len(saved_todos(findings)), 1)

    def test_module_procedures_are_checked_too(self):
        src = ("module m\n  implicit none\ncontains\n  subroutine s(n)\n    integer, intent(out) :: n\n"
               "    integer :: calls = 0\n    calls = calls + 1\n    n = calls\n  end subroutine s\nend module m\n"
               "program p\n  use m\n  implicit none\n  integer :: k\n  call s(k)\n  print *, k\nend program\n")
        _, findings = convert(src)
        self.assertEqual(len(saved_todos(findings)), 1)


class HarmlessTests(unittest.TestCase):
    def test_initialised_but_never_changed_is_not_flagged_or_noted(self):
        text, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: base = 10\n"
                              "  n = base + 1\nend subroutine\n")
        self.assertEqual(saved_todos(findings), [])
        self.assertFalse(any("save" in f[2].lower() for f in findings))
        self.assertEqual(run_bor(text), ["11"])

    def test_assigned_before_it_is_read_is_not_flagged(self):
        text, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: t = 0\n"
                              "  t = 4\n  t = t + 1\n  n = t\nend subroutine\n")
        self.assertEqual(saved_todos(findings), [])
        self.assertEqual(run_bor(text), ["5"])

    def test_loop_variable_with_an_initializer_is_not_flagged(self):
        text, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: i = 0\n"
                              "  n = 0\n  do i = 1, 3\n    n = n + i\n  end do\nend subroutine\n")
        self.assertEqual(saved_todos(findings), [])
        self.assertEqual(run_bor(text), ["6"])

    def test_assignment_that_reads_the_variable_is_flagged_even_with_a_loop_after(self):
        _, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: i = 0\n  i = i + 1\n"
                           "  n = i\nend subroutine\n")
        self.assertEqual(len(saved_todos(findings)), 1)

    def test_conditional_first_write_is_flagged(self):
        _, findings = conv("subroutine s(n)\n  integer, intent(out) :: n\n  integer :: last = 0\n"
                           "  if (n > 100) last = 5\n  n = last\nend subroutine\n")
        self.assertEqual(len(saved_todos(findings)), 1)

    def test_parameters_and_main_program_variables_are_not_saved(self):
        src = ("program p\n  implicit none\n  integer, parameter :: big = 100\n  integer :: count = 0\n"
               "  count = count + big\n  print *, count\nend program\n")
        text, findings = convert(src)
        self.assertEqual(saved_todos(findings), [])
        self.assertEqual(run_bor(text), ["100"])

    def test_dummy_arguments_and_results_are_not_saved(self):
        text, findings = conv("function f(x)\n  integer, intent(in) :: x\n  integer :: f\n  f = x + 1\nend function\n"
                              "subroutine s(n)\n  integer, intent(out) :: n\n  n = f(1)\nend subroutine\n")
        self.assertEqual(saved_todos(findings), [])


if __name__ == "__main__":
    unittest.main()
