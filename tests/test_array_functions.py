"""count, merge, maxloc, minloc and pack."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.lexer import LangError
from boreas.verify import same_output

V = "  integer[6] :: v = [3, -1, 4, 1, -5, 9]\n"


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


class CountTests(unittest.TestCase):
    def test_counts_the_true_elements(self):
        self.assertEqual(show(V + "  println(count(v > 0), count(v > 100), count(v == v))"), ["4 0 6"])

    def test_works_on_a_matrix_and_an_empty_array(self):
        self.assertEqual(show("  integer[2, 2] :: m = reshape([1, 2, 3, 4], [2, 2])\n  println(count(m > 1))"), ["3"])

    def test_needs_logicals(self):
        with self.assertRaisesRegex(LangError, "count\\(\\) needs an array of logicals"):
            show("  println(count([1, 2]))")
        with self.assertRaisesRegex(LangError, "count\\(\\) needs an array"):
            show("  println(count(true))")


class MergeTests(unittest.TestCase):
    def test_picks_elementwise(self):
        self.assertEqual(show(V + "  println(merge(v, 0, v > 0))"), ["[3, 0, 4, 1, 0, 9]"])
        self.assertEqual(show(V + "  println(merge(v, -v, v > 0))"), ["[3, 1, 4, 1, 5, 9]"])

    def test_scalars(self):
        self.assertEqual(show("  println(merge(1, 2, true), merge(1, 2, false))"), ["1 2"])

    def test_strings_and_reals(self):
        self.assertEqual(show('  println(merge("a", "b", [true, false]))'), ["[a, b]"])
        self.assertEqual(show("  println(merge([1.5, 2.5], 0.0, [false, true]))"), ["[0.0, 2.5]"])

    def test_errors(self):
        for body, text in [("merge(1, 2.5, true)", "same type"), ("merge(1, 2, 3)", "mask must be logical"),
                           ("merge([1, 2], 0, [true])", "shape mismatch")]:
            with self.subTest(body):
                with self.assertRaisesRegex(LangError, text):
                    show("  println(" + body + ")")


class LocateTests(unittest.TestCase):
    def test_position_of_the_largest_and_smallest(self):
        self.assertEqual(show(V + "  println(maxloc(v), minloc(v))"), ["6 5"])

    def test_the_first_one_wins_a_tie(self):
        self.assertEqual(show("  println(maxloc([2.5, 0.5, 2.5]), minloc([1, 1, 2]))"), ["1 1"])

    def test_strings_and_the_dim_argument(self):
        self.assertEqual(show('  println(maxloc(["b", "c", "a"]), minloc(["b", "c", "a"], 1))'), ["2 3"])

    def test_errors(self):
        for body, text in [("maxloc([1, 2], 2)", "dimension must be 1"), ("minloc(reshape([1, 2, 3, 4], [2, 2]))", "rank-1"),
                           ("maxloc(5)", "needs an array"), ("maxloc([true, false])", "can be ordered")]:
            with self.subTest(body):
                with self.assertRaisesRegex(LangError, text):
                    show("  println(" + body + ")")

    def test_an_empty_array_is_an_error(self):
        with self.assertRaisesRegex(LangError, "the array is empty"):
            show(V + "  println(maxloc(pack(v, v > 100)))")


class PackTests(unittest.TestCase):
    def test_keeps_where_the_mask_is_true(self):
        self.assertEqual(show(V + "  println(pack(v, v > 0))"), ["[3, 4, 1, 9]"])

    def test_a_matrix_is_read_down_the_columns(self):
        self.assertEqual(show("  integer[2, 2] :: m = reshape([1, 2, 3, 4], [2, 2])\n  println(pack(m, m > 1))"), ["[2, 3, 4]"])

    def test_a_single_mask_and_an_empty_result(self):
        self.assertEqual(show(V + "  println(pack(v, true))"), ["[3, -1, 4, 1, -5, 9]"])
        self.assertEqual(show(V + "  println(pack(v, false), size(pack(v, v > 100)))"), ["[] 0"])

    def test_the_mask_must_match(self):
        with self.assertRaisesRegex(LangError, "mask's shape"):
            show(V + "  println(pack(v, [true, false]))")
        with self.assertRaisesRegex(LangError, "array of logicals"):
            show(V + "  println(pack(v, v))")


class ConversionTests(unittest.TestCase):
    def check(self, body):
        src = "program t\n" + body + "\nend program\n"
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_the_intrinsics_convert_and_match_gfortran(self):
        text = self.check("  integer :: a(6)\n  integer :: i\n  a = [3, -1, 4, 1, -5, 9]\n"
                          "  print *, count(a > 0)\n  print *, merge(a, 0, a > 0)\n"
                          "  print *, pack(a, a > 0)\n  i = maxloc(a, dim=1)\n  print *, i\n"
                          "  print *, minloc(a, dim=1)\n  print *, merge(tsource=a, fsource=-a, mask=a > 0)")
        self.assertNotIn("TODO", text)
        self.assertIn("merge(a, -a, a > 0)", text)


if __name__ == "__main__":
    unittest.main()
