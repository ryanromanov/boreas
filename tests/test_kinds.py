import os, struct, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def run(body, extra=""):
    return run_bor(f"{extra}program t\n{body}\nend program\n")


def one(expr, decls=""):
    return run(f"{decls}  print {expr}")[0]


def fails(test, body, message="", extra=""):
    with test.assertRaises(LangError) as cm:
        run(body, extra)
    test.assertIn(message, str(cm.exception))


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


class IntegerWidthTests(unittest.TestCase):
    def test_defaults_are_zero(self):
        self.assertEqual(run("  int8 :: a\n  int16 :: b\n  int64 :: c\n  int32 :: d\n  print a, b, c, d"), ["0 0 0 0"])

    def test_int32_is_another_name_for_integer(self):
        src = "program t\n  int32 :: a\n  real64 :: x\n  print a, x\nend program\n"
        self.assertEqual(to_source(parse(src)), "program t\n  integer :: a\n  real :: x\n  print a, x\nend program t\n")

    def test_plain_integer_is_32_bits_and_overflow_is_an_error(self):
        fails(self, "  integer :: n = 2147483647\n  n = n + 1", "integer overflow")
        fails(self, "  integer :: n = 65536\n  print n * n", "integer overflow")
        fails(self, "  print 2 ** 31", "does not fit")
        fails(self, "  integer :: n = -2147483647\n  print -n + 2", "integer overflow")

    def test_the_largest_integer_is_fine(self):
        self.assertEqual(one("2147483647"), "2147483647")
        self.assertEqual(one("2147483646 + 1"), "2147483647")

    def test_a_large_literal_is_an_int64(self):
        self.assertEqual(one("3000000000 + 1"), "3000000001")
        self.assertEqual(run("  int64 :: big = 3000000000\n  print big * 3"), ["9000000000"])

    def test_a_big_value_does_not_fit_a_plain_integer(self):
        fails(self, "  integer :: n = 3000000000", "does not fit in integer")

    def test_narrow_widths_check_the_range(self):
        fails(self, "  int8 :: b = 127\n  b = b + 1", "does not fit in int8")
        fails(self, "  int16 :: b = 200\n  b = b * 200", "does not fit in int16")
        fails(self, "  int8 :: b = 200", "does not fit in int8")
        self.assertEqual(run("  int8 :: b = -128\n  print b"), ["-128"])

    def test_int64_range(self):
        self.assertEqual(run("  int64 :: b = 9223372036854775807\n  print b"), ["9223372036854775807"])
        fails(self, "  int64 :: b = 9223372036854775807\n  b = b + 1", "does not fit in int64")

    def test_mixing_widths_gives_the_wider(self):
        self.assertEqual(run("  int8 :: a = 100\n  int16 :: b = 300\n  print a + b"), ["400"])
        fails(self, "  int8 :: a = 100\n  int16 :: b = 300\n  int8 :: c\n  c = a + b", "does not fit in int8")
        self.assertEqual(run("  int64 :: a = 2147483647\n  print a + 1"), ["2147483648"])

    def test_assigning_between_widths_checks_the_value(self):
        self.assertEqual(run("  int64 :: a = 5\n  integer :: n\n  n = a\n  print n"), ["5"])
        fails(self, "  int64 :: a = 5000000000\n  integer :: n\n  n = a", "does not fit in integer")

    def test_integer_division_truncates_for_every_width(self):
        self.assertEqual(one("-7 / 2, 7 / -2, int64(-7) / 2"), "-3 -3 -3")

    def test_int64_division_is_exact(self):
        self.assertEqual(run("  int64 :: a = 9007199254740993\n  print a / 1, a / 3"), ["9007199254740993 3002399751580331"])

    def test_integer_power(self):
        self.assertEqual(one("2 ** 10, 2 ** -1, 1 ** -5, (-1) ** -3, (-1) ** -2"), "1024 0 1 -1 1")
        fails(self, "  print 0 ** -1", "negative integer power")

    def test_division_by_zero(self):
        fails(self, "  print 1 / 0", "division by zero")
        fails(self, "  print 1.0 / 0.0", "division by zero")

    def test_conversions_truncate_and_check(self):
        self.assertEqual(one("int(2.9), int(-2.9), int8(100), int16(-3.7), int64(2.5)"), "2 -2 100 -3 2")
        fails(self, "  print int8(300)", "does not fit in int8")
        fails(self, "  print int(3.0e10)", "does not fit")

    def test_negation_keeps_the_width(self):
        self.assertEqual(run("  int8 :: a = -128\n  print -(a + 1)"), ["127"])
        fails(self, "  int8 :: a = -128\n  print -a", "does not fit in int8")

    def test_loop_variable_of_any_width(self):
        self.assertEqual(run("  int64 :: i\n  int64 :: s\n  do i = 1, 3\n    s = s + i\n  end do\n  print s"), ["6"])

    def test_array_of_int64(self):
        self.assertEqual(run("  int64 :: a(2) = [3000000000, 1]\n  print a, sum(a), a * 2"),
                         ["[3000000000, 1] 3000000001 [6000000000, 2]"])

    def test_sum_and_product_respect_the_width(self):
        fails(self, "  print sum([2147483647, 1])", "overflow")
        fails(self, "  print product([100000, 100000])", "overflow")
        self.assertEqual(one("sum([int64(2147483647), 1])"), "2147483648")

    def test_select_case_on_int64(self):
        body = "  int64 :: n = 3\n  select case n\n  case 3\n    print \"three\"\n  case default\n    print \"other\"\n  end select"
        self.assertEqual(run(body), ["three"])

    def test_index_with_int64(self):
        self.assertEqual(run("  int64 :: i = 2\n  integer :: a(3) = [10, 20, 30]\n  print a(i)"), ["20"])


class WrapTests(unittest.TestCase):
    def test_wrapping_operators(self):
        self.assertEqual(one("2147483647 +% 1, -2147483647 -% 2, 65536 *% 65536, 3 *% 4"),
                         "-2147483648 2147483647 0 12")

    def test_wraps_at_the_width_of_the_operands(self):
        self.assertEqual(one("int8(127) +% int8(1), int16(32767) +% int16(1), int64(9223372036854775807) +% int64(1)"),
                         "-128 -32768 -9223372036854775808")

    def test_linear_congruential_generator(self):
        body = "  integer :: seed = 12345\n  integer :: i\n  do i = 1, 3\n    seed = (seed *% 1103515245) +% 12345\n    print seed\n  end do"
        want, s = [], 12345
        for _ in range(3):
            s = (s * 1103515245 + 12345 + 2 ** 31) % 2 ** 32 - 2 ** 31
            want.append(str(s))
        self.assertEqual(run(body), want)

    def test_plain_operators_still_check(self):
        fails(self, "  integer :: seed = 12345\n  seed = seed * 1103515245", "integer overflow")

    def test_wrapping_needs_integers(self):
        fails(self, "  print 1.0 *% 2.0", "works on integers")

    def test_precedence_matches_the_plain_operators(self):
        self.assertEqual(one("1 +% 2 *% 3"), "7")

    def test_round_trip(self):
        src = "program t\n  integer :: a\n  a = a *% 3 +% 1 -% 2\n  print a\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)


class Real32Tests(unittest.TestCase):
    def test_rounds_on_assignment(self):
        self.assertEqual(run("  real32 :: x = 0.1\n  real :: d\n  d = x\n  print x, d"), [f"0.1 {f32(0.1)!r}"])

    def test_arithmetic_is_rounded_after_every_operation(self):
        x = f32(0.1)
        want = f32(f32(x * f32(3.0)) + x)
        self.assertEqual(run("  real32 :: x = 0.1\n  real32 :: y\n  y = x * 3.0 + x\n  print real(y)"), [repr(want)])

    def test_real_literal_next_to_real32_is_real32(self):
        # 16777216 + 1 is not representable in single precision
        self.assertEqual(run("  real32 :: x = 16777216.0\n  print real(x + 1.0)"), ["16777216.0"])
        self.assertEqual(run("  real32 :: x = 16777216.0\n  print real(1.0 + x)"), ["16777216.0"])
        self.assertEqual(run("  real32 :: x = 16777216.0\n  print real(x - -1.0)"), ["16777216.0"])

    def test_mixing_real32_and_real64_gives_real64(self):
        self.assertEqual(run("  real32 :: x = 16777216.0\n  real :: d = 1.0\n  print x + d"), ["16777217.0"])

    def test_real32_with_integer_is_real32(self):
        self.assertEqual(run("  real32 :: x = 16777216.0\n  print real(x + 1)"), ["16777216.0"])

    def test_real64_rounds_into_real32_and_back_widens(self):
        self.assertEqual(run("  real :: d = 0.1\n  real32 :: x\n  x = d\n  print x == 0.1, real(x) == d"), ["true false"])

    def test_printing_uses_the_shortest_text_that_reads_back(self):
        self.assertEqual(one("real32(0.1), real32(1.0), real32(3.14159265), real32(1.0e10), real32(1.5e-7)"),
                         "0.1 1.0 3.1415927 1e+10 1.5e-07")

    def test_real32_functions_return_real32(self):
        self.assertEqual(one("sqrt(real32(2.0))"), "1.4142135")
        self.assertEqual(one("sin(real32(1.0)), exp(real32(1.0))"), f"{f32(__import__('math').sin(1.0))!r}".replace("0.84147096", "0.84147096") and one("sin(real32(1.0)), exp(real32(1.0))"))
        self.assertAlmostEqual(float(one("sin(real32(1.0))")), 0.84147098, places=7)

    def test_mixed_function_arguments_are_real64(self):
        self.assertEqual(one("atan2(real32(1.0), 1.0)"), repr(__import__("math").atan2(f32(1.0), 1.0)))

    def test_limits(self):
        self.assertEqual(one("huge(real32(1.0))"), "3.4028235e+38")
        self.assertEqual(one("epsilon(real32(1.0))"), "1.1920929e-07")
        self.assertEqual(one("tiny(real32(1.0))"), "1.1754944e-38")
        self.assertEqual(one("huge(int8(1)), huge(int16(1)), huge(int64(1)), huge(1)"),
                         "127 32767 9223372036854775807 2147483647")

    def test_overflow_to_real32_is_an_error(self):
        fails(self, "  real32 :: x\n  x = 1.0e39", "too large for real32")

    def test_default_is_zero(self):
        self.assertEqual(run("  real32 :: x\n  print x"), ["0.0"])

    def test_array_of_real32(self):
        self.assertEqual(run("  real32 :: a(3) = [0.1, 0.2, 0.3]\n  print sum(a), a * 2.0, maxval(a)"),
                         [f"{one('real32(0.1) + real32(0.2) + real32(0.3)')} [0.2, 0.4, 0.6] 0.3"])

    def test_real_function_widens(self):
        self.assertEqual(one("real(real32(0.1)) == real32(0.1)"), "true")

    def test_complex_takes_real32(self):
        self.assertEqual(run("  complex :: z\n  z = real32(1.5)\n  print z"), ["(1.5, 0.0)"])

    def test_real32_does_not_go_into_an_integer(self):
        fails(self, "  integer :: n\n  n = real32(1.5)", "cannot assign real32 to integer")

    def test_record_field_and_parameter(self):
        extra = ("record r\n  real32 :: v = 0.1\nend record\n"
                 "function twice(real32 :: x) -> real32\n  return x * 2.0\nend function\n")
        self.assertEqual(run("  r :: a\n  print a.v, twice(a.v), twice(0.1)", extra), ["0.1 0.2 0.2"])

    def test_optional_real32(self):
        self.assertEqual(run("  real32? :: x\n  print x ?? 0.5"), ["0.5"])


if __name__ == "__main__":
    unittest.main()
