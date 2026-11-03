import ctypes, ctypes.util, os, struct, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests
from boreas import kinds
from boreas.interp import Interp
from boreas.kinds import Real32, single_function
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.verify import tokens_equal


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def run(body, ieee=False):
    out = []
    src = ("use ieee\n\n" if ieee else "") + f"program t\n{body}\nend program\n"
    Interp(parse(src), out.append).run()
    return out


def fails(test, body, message, ieee=False):
    with test.assertRaises(LangError) as cm:
        run(body, ieee)
    test.assertIn(message, str(cm.exception))


def value(lines):
    """The single-precision number a printed line stands for."""
    return f32(float(lines[0].split()[0]))


@unittest.skipIf(single_function("sin") is None, "no C library with single-precision functions")
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.libm = ctypes.CDLL(ctypes.util.find_library("m"))

    def c_function(self, name, n=1):
        fn = getattr(self.libm, name)
        fn.restype = ctypes.c_float
        fn.argtypes = [ctypes.c_float] * n
        return fn

    def test_real32_functions_use_the_c_library(self):
        atanf = self.c_function("atanf")
        for x in (0.3, 1.7, 12.5, 0.001, 0.77):
            self.assertEqual(value(run(f"  real32 :: x = {x}\n  print atan(x)")), f32(atanf(f32(x))), x)

    def test_a_double_argument_still_uses_double_precision(self):
        import math
        self.assertEqual(float(run("  real :: x = 0.3\n  print atan(x)")[0]), math.atan(0.3))

    def test_integer_powers_multiply_in_single_precision(self):
        x = f32(1.1)
        self.assertEqual(value(run("  real32 :: x = 1.1\n  print x ** 3")), f32(f32(x * x) * x))
        x2 = f32(x * x)
        x4 = f32(x2 * x2)
        self.assertEqual(value(run("  real32 :: x = 1.1\n  print x ** 5")), f32(x * x4))

    def test_negative_and_zero_exponents(self):
        self.assertEqual(run("  real32 :: x = 2.0\n  print x ** (-2)"), ["0.25"])
        self.assertEqual(run("  real32 :: x = 2.0\n  print x ** 0"), ["1.0"])

    def test_real_exponents_use_powf(self):
        powf = self.c_function("powf", 2)
        want = f32(powf(f32(1.7), f32(0.37)))
        self.assertEqual(value(run("  real32 :: x = 1.7\n  real32 :: y = 0.37\n  print x ** y")), want)

    def test_results_stay_real32_and_work_on_arrays(self):
        sinf = self.c_function("sinf")
        out = run("  real32 :: x = 0.5\n  print sin([x, x * 2.0])")[0]
        first, second = (f32(float(t)) for t in out.strip("[]").split(", "))
        self.assertEqual((first, second), (f32(sinf(f32(0.5))), f32(sinf(f32(1.0)))))


class StrictTests(unittest.TestCase):
    def test_domain_and_range_errors_still_stop_the_program(self):
        fails(self, "  real32 :: x = 2.0\n  print asin(x)", "domain error")
        fails(self, "  real32 :: x = 0.0\n  print log(x)", "domain error")
        fails(self, "  real32 :: x = -1.0\n  print sqrt(x)", "domain error")
        fails(self, "  real32 :: x = 100.0\n  print exp(x)", "range error")
        fails(self, "  real32 :: x = 2.0\n  print x ** 200", "too large")
        fails(self, "  real32 :: x = -2.0\n  real32 :: y = 0.5\n  print x ** y", "not a real number")

    def test_use_ieee_gives_nan_and_infinity(self):
        self.assertEqual(run("  real32 :: x = 2.0\n  print asin(x)", True), ["NaN"])
        self.assertEqual(run("  real32 :: x = 0.0\n  print log(x)", True), ["-Infinity"])
        self.assertEqual(run("  real32 :: x = 100.0\n  print exp(x)", True), ["Infinity"])
        self.assertEqual(run("  real32 :: x = 2.0\n  print x ** 200", True), ["Infinity"])
        self.assertEqual(run("  real32 :: x = -2.0\n  real32 :: y = 0.5\n  print x ** y", True), ["NaN"])

    def test_nan_in_gives_nan_out_even_in_strict_mode(self):
        self.assertEqual(run("  real32 :: x = nan()\n  print sin(x)"), ["NaN"])


class FallbackTests(unittest.TestCase):
    def test_without_the_library_double_precision_is_rounded(self):
        saved = dict(kinds._single_cache)
        try:
            kinds._single_cache.update({n: None for n in list(kinds.SINGLE_UNARY) + list(kinds.SINGLE_BINARY)})
            import math
            self.assertEqual(value(run("  real32 :: x = 0.3\n  print atan(x)")), f32(math.atan(f32(0.3))))
            self.assertEqual(value(run("  real32 :: x = 1.7\n  real32 :: y = 0.37\n  print x ** y")),
                             f32(f32(1.7) ** f32(0.37)))
        finally:
            kinds._single_cache.clear()
            kinds._single_cache.update(saved)


class SingleFixtureTests(unittest.TestCase):
    check = FixtureTests.check

    def test_fixture_matches_gfortran(self):
        got = self.check("single.f90")
        self.assertEqual(len(got), 180)


class CompareTests(unittest.TestCase):
    def test_single_precision_compare_allows_half_a_step_in_the_upper_half_of_a_binade(self):
        self.assertTrue(tokens_equal("1.151115", "1.15111494"))          # the same single-precision number
        self.assertFalse(tokens_equal("1.1511149", "1.15111506"))        # one step apart


if __name__ == "__main__":
    unittest.main()
