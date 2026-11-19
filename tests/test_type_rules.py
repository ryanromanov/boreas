"""The four rules that made every expression's type depend on types, never on values: floor, ceiling and nint follow
the width of their argument; max and min widen mixed numbers; the fallback of `??` takes the kind of the optional;
and an array literal of mixed numbers is all the widest kind."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


def fails(case, body, message):
    with case.assertRaises(LangError) as cm:
        show(body)
    case.assertIn(message, str(cm.exception))


class WholeNumberTests(unittest.TestCase):
    def test_a_real_gives_an_int(self):
        self.assertEqual(show("  real :: x = 1.0e10\n  int :: n = floor(x)\n  println(n, floor(-2.5), ceiling(2.1), nint(2.5))"),
                         ["10000000000 -3 3 3"])

    def test_a_real32_gives_an_int32(self):
        self.assertEqual(show("  real32 :: s = 2.5\n  println(floor(s) + 2147483645, nint(s))"), ["2147483647 3"])
        fails(self, "  real32 :: s = 3.0e9\n  println(floor(s))", "does not fit in int32")

    def test_it_is_an_int32_in_arithmetic(self):
        fails(self, "  real32 :: s = 2.5\n  println(floor(s) + 2147483646)", "does not fit in int32")

    def test_a_whole_number_stays_as_it_is(self):
        self.assertEqual(show("  int32 :: n = 4\n  println(floor(n) + 2147483643)"), ["2147483647"])

    def test_arrays_work_element_by_element(self):
        self.assertEqual(show("  println(floor([1.5, -1.5]), nint([0.5, 1.5]))"), ["[1, -2] [1, 2]"])

    def test_not_a_number_and_too_big(self):
        fails(self, "  println(floor(nan()))", "no whole number")
        fails(self, "  println(nint(1.0e20))", "does not fit in 64 bits")


class MaxMinTests(unittest.TestCase):
    def test_mixed_numbers_widen(self):
        self.assertEqual(show("  println(max(3, 2.5), max(3, 2.0), min(2.5, 3), max(1, 2))"), ["3.0 3.0 2.5 2"])

    def test_the_widest_integer(self):
        self.assertEqual(show("  int32 :: n = 5\n  int :: m = 4\n  println(max(n, m), max(int8(3), int16(2)))"), ["5 3"])
        self.assertEqual(show("  int32 :: n = 5\n  println(max(n, 2147483647))"), ["2147483647"])

    def test_a_literal_next_to_a_real32_is_a_real32(self):
        self.assertEqual(show("  real32 :: s = 1.5\n  println(max(s, 2.0), min(s, 0.1))"), ["2.0 0.1"])

    def test_strings_still_work(self):
        self.assertEqual(show('  println(max("a", "b"), min("pear", "apple"))'), ["b apple"])


class FallbackTests(unittest.TestCase):
    def test_the_fallback_takes_the_kind_of_the_optional(self):
        self.assertEqual(show("  real32? :: x = null\n  println(x ?? 0.1)"), ["0.1"])
        self.assertEqual(show("  real32? :: x = null\n  real32 :: y = x ?? 1\n  println(y + 0.5)"), ["1.5"])
        self.assertEqual(show("  real? :: x = null\n  println(x ?? 1)"), ["1.0"])

    def test_a_set_value_is_untouched(self):
        self.assertEqual(show("  int32? :: n = 7\n  println(n ?? 99999999999)"), ["7"])

    def test_a_fallback_that_does_not_fit_is_an_error(self):
        fails(self, "  int32? :: n = null\n  println(n ?? 99999999999)", "does not fit in int32")

    def test_other_cases_keep_working(self):
        self.assertEqual(show('  println(to_integer("x") ?? -1, to_real("2.5") ?? 0.0)'), ["-1 2.5"])


class ArrayLiteralTests(unittest.TestCase):
    def test_mixed_numbers_are_the_widest(self):
        self.assertEqual(show("  println([1, 2.5], [1, 2; 3.5, 4])"), ["[1.0, 2.5] [[1.0, 2.0], [3.5, 4.0]]"])
        self.assertEqual(show("  int8 :: a = 1\n  println([a, 300])"), ["[1, 300]"])

    def test_a_literal_next_to_an_int32_is_an_int32(self):
        self.assertEqual(show("  int32 :: n = 5\n  println(sum([n, 1]))"), ["6"])
        fails(self, "  int32 :: n = 5\n  println(sum([n, 2147483647]))", "does not fit in int32")

    def test_a_spliced_array_keeps_its_kind(self):
        self.assertEqual(show("  int32[2] :: x = [1, 2]\n  println(sum([x, 4]))"), ["7"])
        fails(self, "  int32[2] :: x = [2147483646, 1]\n  println(sum([x, 4]))", "does not fit in int32")

    def test_a_literal_next_to_a_real32_is_a_real32(self):
        self.assertEqual(show("  real32 :: s = 0.1\n  real32[2] :: a = [s, 0.2]\n  println(a)"), ["[0.1, 0.2]"])

    def test_text_and_logicals_are_left_alone(self):
        self.assertEqual(show('  println(["a", "b"], [true, false])'), ["[a, b] [true, false]"])


if __name__ == "__main__":
    unittest.main()
