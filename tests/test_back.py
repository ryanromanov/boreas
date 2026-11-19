"""`back`: the last of equal elements for maxloc, minloc and findloc (the Fortran `back=` argument)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.lexer import LangError
from boreas.verify import same_output


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


A = "  int[5] :: a = [3, 9, 1, 9, 1]\n  int[2, 2] :: m = reshape([1, 5, 5, 2], [2, 2])\n"


class BackTests(unittest.TestCase):
    def test_the_last_of_equal_elements(self):
        self.assertEqual(show(A + "  println(maxloc(a, 1, true), maxloc(a, 1, false), minloc(a, 1, true), minloc(a, 1, false))"),
                         ["4 2 5 3"])
        self.assertEqual(show(A + "  println(findloc(a, 9, 1, true), findloc(a, 9, 1), findloc(a, 7, 1, true))"), ["4 2 0"])

    def test_dim_zero_is_the_whole_array(self):
        self.assertEqual(show(A + "  println(maxloc(a, 0, true), maxloc(m, 0, true), maxloc(m, 0, false), findloc(m, 5, 0, true))"),
                         ["4 [1, 2] [2, 1] [1, 2]"])

    def test_a_matrix_along_a_dimension(self):
        self.assertEqual(show(A + "  println(maxloc(m, 1, true), minloc(m, 2, true))"), ["[2, 1] [1, 2]"])

    def test_back_must_be_a_logical(self):
        with self.assertRaises(LangError) as cm:
            show(A + "  println(maxloc(a, 1, 2))")
        self.assertIn("back must be true or false", str(cm.exception))


class ConversionTests(unittest.TestCase):
    SRC = ("program t\n  integer :: a(5), m(2,2)\n  a = [3, 9, 1, 9, 1]\n  m = reshape([1, 5, 5, 2], [2, 2])\n"
           "  print *, maxloc(a, dim=1, back=.true.), maxloc(a, back=.true.), maxloc(a), minloc(a, back=.true.)\n"
           "  print *, findloc(a, 9, back=.true.), findloc(a, 9), findloc(a, 9, dim=1, back=.true.)\n"
           "  print *, maxloc(m, back=.true.)\n  print *, maxloc(m, dim=1, back=.true.)\nend program\n")

    def test_back_converts_and_matches_gfortran(self):
        text, findings = converted(self.SRC)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        self.assertEqual(got, ["4 4 2 5", "4 2 4", "[1, 2]", "[2, 1]"])
        if GFORTRAN:
            want = fortran_output(self.SRC)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")

    def test_mask_is_still_a_todo(self):
        src = "program t\n  integer :: a(3)\n  a = 1\n  print *, maxloc(a, 1, a > 0)\nend program\n"
        _, findings = converted(src)
        self.assertTrue(any("mask" in f[2] for f in findings), findings)


if __name__ == "__main__":
    unittest.main()
