import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.lexer import LangError
from boreas.verify import same_output


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


class CshiftTests(unittest.TestCase):
    def test_positive_shift_moves_toward_the_start(self):
        self.assertEqual(show("  println(cshift([1, 2, 3, 4, 5], 1))"), ["[2, 3, 4, 5, 1]"])
        self.assertEqual(show("  println(cshift([1, 2, 3, 4, 5], 2))"), ["[3, 4, 5, 1, 2]"])

    def test_negative_shift_and_wrapping(self):
        self.assertEqual(show("  println(cshift([1, 2, 3, 4, 5], -1))"), ["[5, 1, 2, 3, 4]"])
        self.assertEqual(show("  println(cshift([1, 2, 3, 4, 5], 5), cshift([1, 2, 3, 4, 5], 7))"),
                         ["[1, 2, 3, 4, 5] [3, 4, 5, 1, 2]"])

    def test_matrix_dimensions(self):
        m = "  integer[2, 3] :: m = reshape([1, 2, 3, 4, 5, 6], [2, 3])\n"      # rows [1 3 5] [2 4 6]
        self.assertEqual(show(m + "  println(cshift(m, 1))"), ["[[2, 4, 6], [1, 3, 5]]"])
        self.assertEqual(show(m + "  println(cshift(m, 1, 2))"), ["[[3, 5, 1], [4, 6, 2]]"])

    def test_works_on_any_element_type(self):
        self.assertEqual(show('  println(cshift(["a", "b", "c"], 1))'), ["[b, c, a]"])
        self.assertEqual(show("  println(cshift([1.5, 2.5], 1))"), ["[2.5, 1.5]"])

    def test_does_not_change_its_argument(self):
        self.assertEqual(show("  integer[3] :: a = [1, 2, 3]\n  integer[3] :: b\n  b = cshift(a, 1)\n  println(a, b)"),
                         ["[1, 2, 3] [2, 3, 1]"])

    def test_errors(self):
        for body, text in [("println(cshift(3, 1))", "needs an array"),
                           ("println(cshift([1, 2], 1.5))", "shift must be an integer"),
                           ("println(cshift([1, 2], 1, 2))", "dimension must be 1..1"),
                           ("println(cshift(reshape([1, 2, 3, 4, 5, 6, 7, 8], [2, 2, 2]), 1))", "rank-1 or 2")]:
            with self.subTest(body):
                with self.assertRaises(LangError) as cm:
                    show("  " + body)
                self.assertIn(text, str(cm.exception))


class CshiftConversionTests(unittest.TestCase):
    def check(self, body):
        src = "program t\n" + body + "\nend program\n"
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_positional_and_keyword_forms(self):
        text = self.check("  integer :: a(5)\n  a = [1, 2, 3, 4, 5]\n  print *, cshift(a, 2)\n"
                          "  print *, cshift(a, shift=2)\n  print *, cshift(array=a, shift=-1)\n"
                          "  print *, cshift(shift=1, array=a)")
        self.assertNotIn("shift=", text)
        self.assertNotIn("shift =", text)

    def test_matrix_with_dim(self):
        loop = "  do j = 1, 3\n    do i = 1, 2\n      print *, r(i, j)\n    end do\n  end do\n"
        self.check("  integer :: m(2, 3), r(2, 3), i, j\n  m = reshape([1, 2, 3, 4, 5, 6], [2, 3])\n"
                   "  r = cshift(m, 1, 2)\n" + loop + "  r = cshift(m, shift=1, dim=2)\n" + loop
                   + "  r = cshift(m, dim=1, shift=1)\n" + loop)

    def test_a_skipped_middle_argument_is_a_todo(self):
        text, findings = converted("program t\n  integer :: a(3)\n  a = [1, 2, 3]\n  print *, cshift(a, dim=1)\nend program\n")
        self.assertEqual(len(todos(findings)), 1)


if __name__ == "__main__":
    unittest.main()
