import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor, todos
from boreas.fortran import convert
from boreas.parser import parse


def run(decls, code):
    text, findings = convert(f"program t\n  implicit none\n{decls}\n{code}\nend program t\n")
    parse(text)
    return text, findings, run_bor(text)


class DataStatementTests(unittest.TestCase):
    def test_implied_do_fills_a_row(self):
        text, f, out = run("  integer :: m(3, 4)",
                           "  data (m(1, j), j = 1, 4) / 11, 12, 13, 14 /\n"
                           "  data (m(2, j), j = 1, 4) / 21, 22, 23, 24 /\n"
                           "  data (m(3, j), j = 1, 4) / 31, 32, 33, 34 /\n"
                           "  print *, m(1, 1), m(2, 3), m(3, 4)")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["11 23 34"])

    def test_elements_one_at_a_time(self):
        _, f, out = run("  integer :: a(3)", "  data a(1) /5/, a(3) /7/\n  data a(2) /6/\n  print *, a")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["[5, 6, 7]"])

    def test_section_and_repeat_count(self):
        _, f, out = run("  integer :: a(5)", "  data a(1:2) /2*9/, a(3:5) /1, 2, 3/\n  print *, a")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["[9, 9, 1, 2, 3]"])

    def test_nested_implied_do(self):
        _, f, out = run("  integer :: m(2, 2)",
                        "  data ((m(i, j), i = 1, 2), j = 1, 2) / 1, 2, 3, 4 /\n  print *, m(1, 1), m(2, 1), m(1, 2), m(2, 2)")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["1 2 3 4"])

    def test_loop_with_a_step(self):
        _, f, out = run("  integer :: a(4)",
                        "  data (a(i), i = 1, 4, 2) / 1, 3 /, (a(i), i = 2, 4, 2) / 2, 4 /\n  print *, a")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["[1, 2, 3, 4]"])

    def test_whole_array_and_scalars_still_work(self):
        _, f, out = run("  integer :: n\n  real :: x(2)", "  data n /3/, x /1.5, 2.5/\n  print *, n, x")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["3 [1.5, 2.5]"])

    def test_extents_may_be_parameters(self):
        _, f, out = run("  integer, parameter :: n = 2\n  integer :: a(n, 2)",
                        "  data (a(1, j), j = 1, 2) /1, 3/, (a(2, j), j = 1, 2) /2, 4/\n  print *, a(2, 1), a(1, 2)")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["2 3"])

    def test_an_array_not_completely_filled_is_a_todo(self):
        text, f, _ = run("  integer :: a(3)", "  data a(1) /5/\n  print *, a(1)")
        self.assertTrue(any("fills only 1 elements of 'a'" in x[2] for x in todos(f)), todos(f))

    def test_too_few_values_is_a_todo(self):
        text, f = convert("program t\n  integer :: a(3)\n  data a /1, 2/\nend program t\n")
        self.assertTrue(todos(f))


if __name__ == "__main__":
    unittest.main()
