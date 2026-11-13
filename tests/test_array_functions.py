"""count, merge, maxloc, minloc, pack, norm2, spread, findloc, lbound and ubound."""
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


class Norm2Tests(unittest.TestCase):
    def test_the_length_of_a_vector_and_a_matrix(self):
        self.assertEqual(show("  println(norm2([3.0, 4.0]), norm2(reshape([1.0, 2.0, 2.0, 0.0], [2, 2])))"), ["5.0 3.0"])

    def test_keeps_single_precision(self):
        self.assertEqual(show("  real32[2] :: v = [3.0, 4.0]\n  real32 :: n = norm2(v)\n  println(n)"), ["5.0"])

    def test_needs_reals(self):
        with self.assertRaisesRegex(LangError, "norm2\\(\\) needs an array of reals"):
            show("  println(norm2([1, 2]))")


class SpreadTests(unittest.TestCase):
    def test_a_scalar_becomes_a_vector(self):
        self.assertEqual(show("  println(spread(7, 1, 3))"), ["[7, 7, 7]"])

    def test_a_vector_becomes_a_matrix_along_either_dimension(self):
        self.assertEqual(show("  println(spread([1, 2, 3], 1, 2))"), ["[[1, 2, 3], [1, 2, 3]]"])
        self.assertEqual(show("  println(spread([1, 2, 3], 2, 2))"), ["[[1, 1], [2, 2], [3, 3]]"])
        self.assertEqual(show("  println(shape(spread([1, 2, 3], 2, 4)))"), ["[3, 4]"])

    def test_zero_copies_and_errors(self):
        self.assertEqual(show("  println(size(spread([1, 2], 1, 0)))"), ["0"])
        for body, text in [("spread([1, 2], 3, 2)", "dimension must be 1..2"), ("spread([1, 2], 1, -1)", "non-negative"),
                           ("spread(1, 2, 2)", "dimension must be 1..1")]:
            with self.subTest(body):
                with self.assertRaisesRegex(LangError, text):
                    show("  println(" + body + ")")


class FindlocTests(unittest.TestCase):
    def test_the_first_match_or_zero(self):
        self.assertEqual(show('  println(findloc([4, 9, 9], 9), findloc([4, 9, 9], 5), findloc(["a", "b"], "b"))'), ["2 0 2"])

    def test_errors(self):
        for body, text in [("findloc([1, 2], 1, 2)", "dimension must be 1"), ("findloc(3, 3)", "needs an array"),
                           ("findloc([1, 2], [1])", "single value")]:
            with self.subTest(body):
                with self.assertRaisesRegex(LangError, text):
                    show("  println(" + body + ")")


class BoundsTests(unittest.TestCase):
    M = "  integer[2, 3] :: m\n"

    def test_all_dimensions_or_one(self):
        self.assertEqual(show(self.M + "  println(lbound(m), ubound(m), ubound(m, 2), lbound(m, 1))"), ["[1, 1] [2, 3] 3 1"])

    def test_errors(self):
        with self.assertRaisesRegex(LangError, "dimension must be 1..2"):
            show(self.M + "  println(ubound(m, 3))")
        with self.assertRaisesRegex(LangError, "needs an array"):
            show("  println(ubound(5))")


class ConversionTests2(unittest.TestCase):
    def test_the_second_batch_converts_and_matches_gfortran(self):
        text, findings = converted("program t\n  double precision :: a(4)\n  integer :: b(3)\n  integer :: i\n"
                                   "  a = [3.0d0, 4.0d0, 12.0d0, 84.0d0]\n  b = [5, 7, 7]\n"
                                   "  print *, norm2(a)\n  print *, norm2(x=a)\n  i = findloc(b, 7, dim=1)\n  print *, i\n"
                                   "  print *, findloc(b, value=9, dim=1)\n  print *, ubound(b, 1), lbound(b, dim=1)\n"
                                   "  print *, size(spread(b, 2, 3)), sum(spread(b, dim=1, ncopies=2))\nend program\n")
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output("program t\n  double precision :: a(4)\n  integer :: b(3)\n  integer :: i\n"
                                  "  a = [3.0d0, 4.0d0, 12.0d0, 84.0d0]\n  b = [5, 7, 7]\n"
                                  "  print *, norm2(a)\n  print *, norm2(x=a)\n  i = findloc(b, 7, dim=1)\n  print *, i\n"
                                  "  print *, findloc(b, value=9, dim=1)\n  print *, ubound(b, 1), lbound(b, dim=1)\n"
                                  "  print *, size(spread(b, 2, 3)), sum(spread(b, dim=1, ncopies=2))\nend program\n")
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")


if __name__ == "__main__":
    unittest.main()
