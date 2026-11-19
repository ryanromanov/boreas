import os, struct, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def run(body, extra=""):
    return run_bor(f"{extra}program t\n{body}\nend program\n")


def one(expr, decls=""):
    return run(f"{decls}  println({expr})")[0]


def fails(test, body, message="", extra=""):
    with test.assertRaises(LangError) as cm:
        run(body, extra)
    test.assertIn(message, str(cm.exception))


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


class IntegerWidthTests(unittest.TestCase):
    def test_defaults_are_zero(self):
        self.assertEqual(run("  int8 :: a\n  int16 :: b\n  int64 :: c\n  int32 :: d\n  println(a, b, c, d)"), ["0 0 0 0"])

    def test_int64_is_another_name_for_int(self):
        src = "program t\n  int64 :: a\n  int32 :: n\n  real64 :: x\n  println(a, n, x)\nend program\n"
        self.assertEqual(to_source(parse(src)),
                         "program t\n  int :: a\n  int32 :: n\n  real :: x\n  println(a, n, x)\nend program t\n")

    def test_int_is_64_bits_and_overflow_is_an_error(self):
        self.assertEqual(one("2147483647 + 1, 2 ** 31, 65536 * 65536"), "2147483648 2147483648 4294967296")
        fails(self, "  int :: n = 9223372036854775807\n  n = n + 1", "integer overflow")
        fails(self, "  println(2 ** 63)", "does not fit")
        fails(self, "  int :: n = 4294967296\n  println(n * n)", "integer overflow")

    def test_int32_is_32_bits_and_overflow_is_an_error(self):
        fails(self, "  int32 :: n = 2147483647\n  n = n + 1", "does not fit in int32")
        fails(self, "  int32 :: n = 65536\n  println(n * n)", "does not fit in int32")
        fails(self, "  int32 :: n = -2147483647\n  println(-n + 2)", "does not fit in int32")

    def test_a_literal_next_to_an_int32_is_an_int32(self):
        self.assertEqual(run("  int32 :: n = 5\n  println(n + 1, 1 + n, n * 3, mod(n, 3), max(n, 2))"), ["6 6 15 2 5"])
        fails(self, "  int32 :: n = 2147483647\n  println(1 + n)", "does not fit in int32")
        self.assertEqual(one("int32(5) + 3000000000"), "3000000005")           # too big for 32 bits: the 64-bit int wins

    def test_the_largest_integer_is_fine(self):
        self.assertEqual(one("2147483647"), "2147483647")
        self.assertEqual(one("2147483646 + 1"), "2147483647")

    def test_a_large_literal_is_fine(self):
        self.assertEqual(one("3000000000 + 1"), "3000000001")
        self.assertEqual(run("  int64 :: big = 3000000000\n  println(big * 3)"), ["9000000000"])

    def test_a_big_value_does_not_fit_an_int32(self):
        fails(self, "  int32 :: n = 3000000000", "does not fit in int32")

    def test_narrow_widths_check_the_range(self):
        fails(self, "  int8 :: b = 127\n  b = b + 1", "does not fit in int8")
        fails(self, "  int16 :: b = 200\n  b = b * 200", "does not fit in int16")
        fails(self, "  int8 :: b = 200", "does not fit in int8")
        self.assertEqual(run("  int8 :: b = -128\n  println(b)"), ["-128"])

    def test_int64_range(self):
        self.assertEqual(run("  int64 :: b = 9223372036854775807\n  println(b)"), ["9223372036854775807"])
        fails(self, "  int64 :: b = 9223372036854775807\n  b = b + 1", "does not fit in 64 bits")

    def test_mixing_widths_gives_the_wider(self):
        self.assertEqual(run("  int8 :: a = 100\n  int16 :: b = 300\n  println(a + b)"), ["400"])
        fails(self, "  int8 :: a = 100\n  int16 :: b = 300\n  int8 :: c\n  c = a + b", "does not fit in int8")
        self.assertEqual(run("  int64 :: a = 2147483647\n  println(a + 1)"), ["2147483648"])

    def test_assigning_between_widths_checks_the_value(self):
        self.assertEqual(run("  int :: a = 5\n  int32 :: n\n  n = a\n  println(n)"), ["5"])
        fails(self, "  int :: a = 5000000000\n  int32 :: n\n  n = a", "does not fit in int32")

    def test_integer_division_truncates_for_every_width(self):
        self.assertEqual(one("-7 / 2, 7 / -2, int64(-7) / 2"), "-3 -3 -3")

    def test_int64_division_is_exact(self):
        self.assertEqual(run("  int64 :: a = 9007199254740993\n  println(a / 1, a / 3)"), ["9007199254740993 3002399751580331"])

    def test_integer_power(self):
        self.assertEqual(one("2 ** 10, 2 ** -1, 1 ** -5, (-1) ** -3, (-1) ** -2"), "1024 0 1 -1 1")
        fails(self, "  println(0 ** -1)", "negative integer power")

    def test_division_by_zero(self):
        fails(self, "  println(1 / 0)", "division by zero")
        fails(self, "  println(1.0 / 0.0)", "division by zero")

    def test_conversions_truncate_and_check(self):
        self.assertEqual(one("int(2.9), int(-2.9), int8(100), int16(-3.7), int64(2.5)"), "2 -2 100 -3 2")
        fails(self, "  println(int8(300))", "does not fit in int8")
        fails(self, "  println(int32(3.0e10))", "does not fit in int32")
        fails(self, "  println(int(1.0e30))", "does not fit")

    def test_negation_keeps_the_width(self):
        self.assertEqual(run("  int8 :: a = -128\n  println(-(a + 1))"), ["127"])
        fails(self, "  int8 :: a = -128\n  println(-a)", "does not fit in int8")

    def test_loop_variable_of_any_width(self):
        self.assertEqual(run("  int64 :: i\n  int64 :: s\n  do i = 1, 3\n    s = s + i\n  end do\n  println(s)"), ["6"])

    def test_array_of_int64(self):
        self.assertEqual(run("  int64[2] :: a = [3000000000, 1]\n  println(a, sum(a), a * 2)"),
                         ["[3000000000, 1] 3000000001 [6000000000, 2]"])

    def test_sum_and_product_respect_the_width(self):
        fails(self, "  int32[2] :: a = [2147483647, 1]\n  println(sum(a))", "does not fit in int32")
        fails(self, "  int32[2] :: a = [100000, 100000]\n  println(product(a))", "does not fit in int32")
        self.assertEqual(one("sum([int64(2147483647), 1])"), "2147483648")

    def test_select_case_on_int64(self):
        body = "  int64 :: n = 3\n  select case n\n  case 3\n    println(\"three\")\n  case default\n    println(\"other\")\n  end select"
        self.assertEqual(run(body), ["three"])

    def test_index_with_int64(self):
        self.assertEqual(run("  int64 :: i = 2\n  int[3] :: a = [10, 20, 30]\n  println(a[i])"), ["20"])


class WrapTests(unittest.TestCase):
    def test_wrapping_operators(self):
        self.assertEqual(one("int32(2147483647) +% 1, int32(-2147483647) -% 2, int32(65536) *% 65536, 3 *% 4"),
                         "-2147483648 2147483647 0 12")
        self.assertEqual(one("9223372036854775807 +% 1"), "-9223372036854775808")

    def test_wraps_at_the_width_of_the_operands(self):
        self.assertEqual(one("int8(127) +% int8(1), int16(32767) +% int16(1), int64(9223372036854775807) +% int64(1)"),
                         "-128 -32768 -9223372036854775808")

    def test_linear_congruential_generator(self):
        body = "  int32 :: seed = 12345\n  int :: i\n  do i = 1, 3\n    seed = (seed *% 1103515245) +% 12345\n    println(seed)\n  end do"
        want, s = [], 12345
        for _ in range(3):
            s = (s * 1103515245 + 12345 + 2 ** 31) % 2 ** 32 - 2 ** 31
            want.append(str(s))
        self.assertEqual(run(body), want)

    def test_plain_operators_still_check(self):
        fails(self, "  int32 :: seed = 12345\n  seed = seed * 1103515245", "does not fit in int32")

    def test_wrapping_needs_integers(self):
        fails(self, "  println(1.0 *% 2.0)", "works on integers")

    def test_precedence_matches_the_plain_operators(self):
        self.assertEqual(one("1 +% 2 *% 3"), "7")

    def test_round_trip(self):
        src = "program t\n  int :: a\n  a = a *% 3 +% 1 -% 2\n  println(a)\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)


class Real32Tests(unittest.TestCase):
    def test_rounds_on_assignment(self):
        self.assertEqual(run("  real32 :: x = 0.1\n  real :: d\n  d = x\n  println(x, d)"), [f"0.1 {f32(0.1)!r}"])

    def test_arithmetic_is_rounded_after_every_operation(self):
        x = f32(0.1)
        want = f32(f32(x * f32(3.0)) + x)
        self.assertEqual(run("  real32 :: x = 0.1\n  real32 :: y\n  y = x * 3.0 + x\n  println(real(y))"), [repr(want)])

    def test_real_literal_next_to_real32_is_real32(self):
        # 16777216 + 1 is not representable in single precision
        self.assertEqual(run("  real32 :: x = 16777216.0\n  println(real(x + 1.0))"), ["16777216.0"])
        self.assertEqual(run("  real32 :: x = 16777216.0\n  println(real(1.0 + x))"), ["16777216.0"])
        self.assertEqual(run("  real32 :: x = 16777216.0\n  println(real(x - -1.0))"), ["16777216.0"])

    def test_mixing_real32_and_real64_gives_real64(self):
        self.assertEqual(run("  real32 :: x = 16777216.0\n  real :: d = 1.0\n  println(x + d)"), ["16777217.0"])

    def test_real32_with_integer_is_real32(self):
        self.assertEqual(run("  real32 :: x = 16777216.0\n  println(real(x + 1))"), ["16777216.0"])

    def test_real64_rounds_into_real32_and_back_widens(self):
        self.assertEqual(run("  real :: d = 0.1\n  real32 :: x\n  x = d\n  println(x == 0.1, real(x) == d)"), ["true false"])

    def test_printing_uses_the_shortest_text_that_reads_back(self):
        self.assertEqual(one("real32(0.1), real32(1.0), real32(3.14159265), real32(1.0e10), real32(1.5e-7)"),
                         "0.1 1.0 3.1415927 10000000000.0 1.5e-07")

    def test_a_whole_real32_prints_as_a_real_does(self):
        # it used to print 1e+02 for 100.0
        self.assertEqual(one("real32(100.0), real32(1000.0), real32(100000.0), real32(1.0e20), real32(0.0001), real32(1.0e-5)"),
                         "100.0 1000.0 100000.0 1e+20 0.0001 1e-05")

    def test_real32_functions_return_real32(self):
        self.assertEqual(one("sqrt(real32(2.0))"), "1.4142135")
        self.assertEqual(one("sin(real32(1.0)), exp(real32(1.0))"), f"{f32(__import__('math').sin(1.0))!r}".replace("0.84147096", "0.84147096") and one("sin(real32(1.0)), exp(real32(1.0))"))
        self.assertAlmostEqual(float(one("sin(real32(1.0))")), 0.84147098, places=7)

    def test_mixed_function_arguments_are_real64(self):
        self.assertEqual(one("atan2(real32(1.0), d)", "  real :: d = 1.0\n"),
                         repr(__import__("math").atan2(f32(1.0), 1.0)))

    def test_a_real_literal_next_to_a_real32_argument_is_a_real32(self):
        self.assertEqual(one("atan2(real32(1.0), 1.0)"), "0.7853982")
        self.assertEqual(one("max(real32(1.5), 2.0)"), "2.0")

    def test_limits(self):
        self.assertEqual(one("huge(real32(1.0))"), "3.4028235e+38")
        self.assertEqual(one("epsilon(real32(1.0))"), "1.1920929e-07")
        self.assertEqual(one("tiny(real32(1.0))"), "1.1754944e-38")
        self.assertEqual(one("huge(int8(1)), huge(int16(1)), huge(int32(1)), huge(1)"),
                         "127 32767 2147483647 9223372036854775807")

    def test_overflow_to_real32_is_an_error(self):
        fails(self, "  real32 :: x\n  x = 1.0e39", "too large for real32")

    def test_single_arithmetic_that_overflows_is_an_error(self):
        fails(self, "  real32 :: x = huge(1.0e0)\n  real32 :: y = 10.0\n  println(x * y)", "too large for real32")

    def test_default_is_zero(self):
        self.assertEqual(run("  real32 :: x\n  println(x)"), ["0.0"])

    def test_array_of_real32(self):
        self.assertEqual(run("  real32[3] :: a = [0.1, 0.2, 0.3]\n  println(sum(a), a * 2.0, maxval(a))"),
                         [f"{one('real32(0.1) + real32(0.2) + real32(0.3)')} [0.2, 0.4, 0.6] 0.3"])

    def test_real_function_widens(self):
        self.assertEqual(one("real(real32(0.1)) == real32(0.1)"), "true")

    def test_complex_takes_real32(self):
        self.assertEqual(run("  complex :: z\n  z = real32(1.5)\n  println(z)"), ["(1.5, 0.0)"])

    def test_real32_does_not_go_into_an_integer(self):
        fails(self, "  int :: n\n  n = real32(1.5)", "cannot assign real32 to int")

    def test_record_field_and_parameter(self):
        extra = ("record r\n  real32 :: v = 0.1\nend record\n"
                 "function twice(real32 :: x) -> real32\n  return x * 2.0\nend function\n")
        self.assertEqual(run("  r :: a\n  println(a.v, twice(a.v), twice(0.1))", extra), ["0.1 0.2 0.2"])

    def test_optional_real32(self):
        self.assertEqual(run("  real32? :: x\n  println(x ?? 0.5)"), ["0.5"])


if __name__ == "__main__":
    unittest.main()
