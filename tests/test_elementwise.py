"""A function of single values, called with an array, runs once per element (D60)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError

SHRINK = ("function shrink(real :: x, real :: limit) -> real\n  if x > limit then\n    return limit\n"
          "  end if\n  return x\nend function\n")
V = "  real[4] :: v = [0.5, 3.0, 9.0, 1.5]\n"


def show(body, defs=SHRINK):
    return run_bor(defs + "program t\n" + body + "\nend program\n")


class ApplyTests(unittest.TestCase):
    def test_an_array_and_a_single_value(self):
        self.assertEqual(show(V + "  println(shrink(v, 5.0))"), ["[0.5, 3.0, 5.0, 1.5]"])

    def test_two_arrays_of_the_same_shape(self):
        self.assertEqual(show(V + "  real[4] :: w = [1.0, 1.0, 2.0, 2.0]\n  println(shrink(v, w))"),
                         ["[0.5, 1.0, 2.0, 1.5]"])

    def test_a_single_value_still_gives_a_single_value(self):
        self.assertEqual(show("  println(shrink(9.0, 5.0))"), ["5.0"])

    def test_a_matrix_keeps_its_shape(self):
        out = show("  real[2, 2] :: m = reshape([1.0, 9.0, 9.0, 1.0], [2, 2])\n  real[2, 2] :: r = shrink(m, 5.0)\n  println(shape(r))")
        self.assertEqual(out, ["[2, 2]"])

    def test_the_result_is_an_array_value(self):
        self.assertEqual(show(V + "  real[4] :: r = shrink(v, 2.0)\n  println(sum(r))"), ["6.0"])

    def test_integers_and_converted_results(self):
        defs = "function twice(integer :: n) -> integer\n  return n * 2\nend function\n"
        self.assertEqual(show("  println(twice([1, 2, 3]))", defs), ["[2, 4, 6]"])

    def test_used_inside_another_expression(self):
        self.assertEqual(show(V + "  println(sum(shrink(v, 2.0)))"), ["6.0"])

    def test_a_parameter_that_wants_an_array_still_gets_the_whole_array(self):
        defs = "function total(real[] :: a) -> real\n  return sum(a)\nend function\n"
        self.assertEqual(show(V + "  println(total(v))", defs), ["14.0"])


class StillAnErrorTests(unittest.TestCase):
    def fails(self, body, message, defs=SHRINK):
        with self.assertRaises(LangError) as cm:
            show(body, defs)
        self.assertIn(message, str(cm.exception))

    def test_the_result_cannot_go_into_a_single_value(self):
        self.fails(V + "  real :: y = shrink(v, 5.0)", "cannot assign array to real")

    def test_arrays_must_have_the_same_shape(self):
        self.fails(V + "  real[2] :: w = [1.0, 2.0]\n  println(shrink(v, w))", "different shapes")

    def test_a_function_with_several_results_cannot_be_applied(self):
        defs = "function two(real :: x) -> (real :: a, real :: b)\n  return x, x\nend function\n"
        self.fails(V + "  (p, q) = two(v)", "cannot be applied", defs)

    def test_a_function_returning_an_array_cannot_be_applied(self):
        defs = "function pair(real :: x) -> real[]\n  return [x, x]\nend function\n"
        self.fails(V + "  println(pair(v))", "cannot be applied", defs)


if __name__ == "__main__":
    unittest.main()
