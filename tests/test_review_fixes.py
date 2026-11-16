"""Crashes and silent wrong answers found by the second review: deep recursion, powers of negative numbers,
real overflow, whole-number results too big for an integer, and values of the wrong type."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError


def show(body, defs=""):
    return run_bor(defs + "program t\n" + body + "\nend program\n")


def fails(case, body, message, defs=""):
    with case.assertRaises(LangError) as cm:
        show(body, defs)
    case.assertIn(message, str(cm.exception))


DEEP = "function deep(integer :: n) -> integer\n  if n == 0 then\n    return 0\n  end if\n  return 1 + deep(n - 1)\nend function\n"


class RecursionTests(unittest.TestCase):
    def test_a_few_thousand_levels_work(self):
        self.assertEqual(show("  println(deep(5000))", DEEP), ["5000"])

    def test_endless_recursion_is_an_error_not_a_traceback(self):
        fails(self, "  println(deep(-1))", "nested more than", DEEP)

    def test_a_subroutine_too(self):
        defs = "subroutine down(integer :: n)\n  call down(n + 1)\nend subroutine\n"
        fails(self, "  call down(1)", "nested more than", defs)


class PowerTests(unittest.TestCase):
    def test_a_negative_number_to_a_fractional_power(self):
        for body in ("println((-8.0) ** (1.0 / 3.0))", "real :: x = -2.0\n  println(x ** 0.5)", "println((-8) ** 0.5)"):
            fails(self, "  " + body, "is not a real number")

    def test_whole_powers_and_complex_roots_still_work(self):
        self.assertEqual(show("  println((-8.0) ** 3.0, (-8.0) ** 2, 2.0 ** 0.5)"), ["-512.0 64.0 1.4142135623730951"])
        self.assertEqual(show("  println(abs(cmplx(-4.0, 0.0) ** 0.5))"), ["2.0"])

    def test_ieee_gives_nan(self):
        self.assertEqual(run_bor("use ieee\nprogram t\n  println((-8.0) ** 0.5)\nend program\n"), ["NaN"])


class OverflowTests(unittest.TestCase):
    def test_real_overflow_is_an_error(self):
        for body in ("println(1.0e308 * 10.0)", "println(huge(1.0) * 2.0)", "println(1.0e308 + 1.0e308)",
                     "println(real32(1.0e38) * real32(10.0))"):
            fails(self, "  " + body, "too large")

    def test_ieee_gives_infinity(self):
        self.assertEqual(run_bor("use ieee\nprogram t\n  println(1.0e308 * 10.0)\nend program\n"), ["Infinity"])

    def test_floor_ceiling_and_nint_must_fit(self):
        fails(self, "  integer :: n = floor(1.0e10)", "does not fit in integer")
        fails(self, "  integer :: n = nint(3.0e9)", "does not fit in integer")
        fails(self, "  println(nint(1.0e20))", "does not fit in 64 bits")
        fails(self, "  println(floor(nan()))", "no whole number")
        self.assertEqual(show("  int64 :: n = floor(1.0e10)\n  println(n, floor(-2.5), ceiling(2.1), nint(2.5))"),
                         ["10000000000 -3 3 3"])


class WrongTypeTests(unittest.TestCase):
    def test_logicals_and_strings_are_not_numbers(self):
        for body in ("sqrt(true)", "abs(true)", "floor(true)", 'abs("abc")', "sum([true, true])", "maxval([true, false])"):
            fails(self, f"  println({body})", "needs numbers")

    def test_max_and_min_need_two_values_of_one_kind(self):
        fails(self, '  println(max("abc"))', "needs at least two values")
        fails(self, "  println(max(true, false))", "all numbers or all strings")
        fails(self, '  println(min(1, "a"))', "all numbers or all strings")
        self.assertEqual(show('  println(max(1, 2.5), max("a", "b"), min(3, 1, 2))'), ["2.5 b 1"])

    def test_max_and_min_work_on_arrays_elementwise(self):
        self.assertEqual(show("  println(max([1, 5, 3], 2), min([1, 5, 3], [2, 2, 9]))"), ["[2, 5, 3] [1, 2, 3]"])

    def test_comparing_different_kinds_is_an_error(self):
        fails(self, '  println("a" == 1)', "cannot compare string with integer")
        fails(self, "  println(true == 1)", "cannot compare logical with integer")
        fails(self, "  println(true < false)", "not '<'")
        self.assertEqual(show("  println(1 == 1.0, true == true, \"a\" /= \"b\")"), ["true true true"])

    def test_the_maximum_of_nothing(self):
        fails(self, "  integer[0] :: a\n  println(maxval(a))", "the array is empty")


class ConverterTests(unittest.TestCase):
    def test_nint_with_a_kind_converts(self):
        from test_callback_convert import converted
        src = ("program t\n  integer(8) :: n\n  double precision :: x = 1.0d10\n  n = nint(x, 8)\n  print *, n\n"
               "  n = floor(x, kind=8)\n  print *, n\nend program\n")
        text, findings = converted(src)
        self.assertEqual(run_bor(text), ["10000000000", "10000000000"])


if __name__ == "__main__":
    unittest.main()
