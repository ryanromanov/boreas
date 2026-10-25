import math, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, run_bor, todos
from boreas.fortran import convert
from boreas.lexer import LangError


def value(expr):
    """Evaluate one Boreas expression and give the single printed word."""
    return run_bor(f"program t\n  print {expr}\nend program\n")[0]


def number(expr):
    return float(value(expr))


class FunctionTests(unittest.TestCase):
    def test_trigonometry_matches_math(self):
        for name, arg in [("tan", 0.5), ("asin", 0.5), ("acos", 0.5), ("atan", 1.0), ("sinh", 1.0),
                          ("cosh", 1.0), ("tanh", 1.0), ("asinh", 1.0), ("acosh", 2.0), ("atanh", 0.5),
                          ("log10", 1000.0), ("gamma", 5.0), ("erf", 0.5), ("erfc", 0.5)]:
            with self.subTest(name):
                self.assertAlmostEqual(number(f"{name}({arg})"), getattr(math, name.replace("log_gamma", "lgamma"))(arg))

    def test_atan_with_two_arguments_is_atan2(self):
        self.assertAlmostEqual(number("atan(1.0, -1.0)"), math.atan2(1.0, -1.0))
        self.assertAlmostEqual(number("atan2(1.0, -1.0)"), math.atan2(1.0, -1.0))

    def test_floor_and_ceiling_give_integers(self):
        self.assertEqual(value("floor(-2.5)"), "-3")
        self.assertEqual(value("ceiling(-2.5)"), "-2")
        self.assertEqual(value("ceiling(2.1)"), "3")

    def test_nint_rounds_halves_away_from_zero(self):
        self.assertEqual(value("nint(2.5)"), "3")
        self.assertEqual(value("nint(-2.5)"), "-3")
        self.assertEqual(value("nint(0.49999)"), "0")
        self.assertEqual(value("nint(3.5)"), "4")

    def test_aint_and_anint_give_reals(self):
        self.assertEqual(value("aint(-2.7)"), "-2.0")
        self.assertEqual(value("anint(2.5)"), "3.0")
        self.assertEqual(value("anint(-0.5)"), "-1.0")

    def test_sign(self):
        self.assertEqual(value("sign(3.0, -1.0)"), "-3.0")
        self.assertEqual(value("sign(-3, 5)"), "3")
        self.assertEqual(value("sign(3, -2)"), "-3")
        self.assertEqual(value("sign(3, 0)"), "3")

    def test_modulo_takes_the_sign_of_the_divisor_and_mod_of_the_dividend(self):
        self.assertEqual(value("modulo(-7, 3)"), "2")
        self.assertEqual(value("mod(-7, 3)"), "-1")
        self.assertEqual(value("modulo(7, -3)"), "-2")
        self.assertEqual(value("modulo(-7.5, 2.0)"), "0.5")

    def test_modulo_by_zero_is_an_error(self):
        for expr in ("modulo(1, 0)", "modulo(1.0, 0.0)"):
            with self.assertRaises(LangError):
                value(expr)

    def test_dim_and_hypot(self):
        self.assertEqual(value("dim(5, 3)"), "2")
        self.assertEqual(value("dim(3, 5)"), "0")
        self.assertEqual(value("hypot(3.0, 4.0)"), "5.0")

    def test_product(self):
        self.assertEqual(value("product([1, 2, 3, 4])"), "24")
        self.assertEqual(value("product([0.5, 4.0])"), "2.0")

    def test_inquiry_functions(self):
        self.assertEqual(value("huge(1)"), "2147483647")
        self.assertEqual(number("huge(1.0)"), sys.float_info.max)
        self.assertEqual(number("epsilon(1.0)"), sys.float_info.epsilon)
        self.assertEqual(number("tiny(1.0)"), sys.float_info.min)

    def test_inquiry_needs_the_right_type(self):
        for expr in ("epsilon(1)", "tiny(1)", 'huge("a")'):
            with self.subTest(expr):
                with self.assertRaises(LangError):
                    value(expr)

    def test_domain_errors_are_reported(self):
        for expr in ("asin(2.0)", "log10(0.0)", "acosh(0.5)"):
            with self.subTest(expr):
                with self.assertRaises(LangError):
                    value(expr)

    def test_functions_apply_to_every_element(self):
        out = run_bor("program t\n  real :: x(3) = [0.5, -2.5, 3.5]\n  print nint(x), floor(x)\n"
                      "  print sign(1.0, x)\n  print atan2(x, 1.0)\nend program\n")
        self.assertEqual(out[0], "[1, -3, 4] [0, -3, 3]")
        self.assertEqual(out[1], "[1.0, -1.0, 1.0]")
        self.assertAlmostEqual(float(out[2].strip("[]").split(",")[0]), math.atan2(0.5, 1.0))

    def test_new_functions_are_allowed_in_field_defaults(self):
        out = run_bor("record r\n  real :: a = hypot(3.0, 4.0)\n  integer :: n = floor(2.7)\nend record\n"
                      "program t\n  r :: x\n  print x.a, x.n\nend program\n")
        self.assertEqual(out, ["5.0 2"])


class ConvertMathTests(unittest.TestCase):
    check = FixtureTests.check

    def test_math_fixture_matches_gfortran(self):
        self.check("math.f90")

    def test_specific_names_become_generic_functions(self):
        text, findings = convert("program p\n  double precision :: x\n  x = dsqrt(4.0d0) + dabs(-1.0d0) "
                                 "+ dsin(0.0d0) + datan2(1.0d0, 1.0d0) + amax1(1.0, 2.0)\n  print *, x\nend program\n")
        self.assertEqual(todos(findings), [])
        for generic in ("sqrt(", "abs(", "sin(", "atan2(", "max("):
            self.assertIn(generic, text)
        for specific in ("dsqrt", "dabs", "dsin", "datan2", "amax1"):
            self.assertNotIn(specific, text)

    def test_functions_that_are_still_missing_stay_todos(self):
        _, findings = convert("program p\n  print *, besj0(1.0d0)\nend program\n")
        self.assertEqual(len(todos(findings)), 1)


if __name__ == "__main__":
    unittest.main()
