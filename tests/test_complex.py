import cmath, math, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, run_bor, todos
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def run(body, extra=""):
    return run_bor(f"{extra}program t\n{body}\nend program\n")


def one(expr):
    return run(f"  print {expr}")[0]


def fails(test, body, message="", extra=""):
    with test.assertRaises(LangError) as cm:
        run(body, extra)
    test.assertIn(message, str(cm.exception))


class ComplexValueTests(unittest.TestCase):
    def test_construction_and_parts(self):
        self.assertEqual(one("cmplx(3.0, 4.0)"), "(3.0, 4.0)")
        self.assertEqual(one("real(cmplx(3.0, 4.0)), aimag(cmplx(3.0, 4.0))"), "3.0 4.0")
        self.assertEqual(one("cmplx(2)"), "(2.0, 0.0)")
        self.assertEqual(one("cmplx(cmplx(1.0, 2.0))"), "(1.0, 2.0)")

    def test_default_is_zero(self):
        self.assertEqual(run("  complex :: z\n  print z"), ["(0.0, 0.0)"])

    def test_real_and_integer_widen_on_assignment(self):
        self.assertEqual(run("  complex :: z\n  z = 5\n  print z\n  z = 2.5\n  print z"),
                         ["(5.0, 0.0)", "(2.5, 0.0)"])

    def test_complex_does_not_narrow(self):
        fails(self, "  complex :: z\n  real :: r\n  r = z", "cannot assign complex to real")
        fails(self, "  complex :: z\n  integer :: n\n  n = z", "cannot assign complex to integer")

    def test_arithmetic_matches_python(self):
        z, w = complex(3, 4), complex(1, -2)
        for op, want in [("+", z + w), ("-", z - w), ("*", z * w), ("/", z / w), ("**", z ** 2)]:
            rhs = "2" if op == "**" else "w"
            with self.subTest(op):
                out = run(f"  complex :: z = cmplx(3.0, 4.0)\n  complex :: w = cmplx(1.0, -2.0)\n  print z {op} {rhs}")
                self.assertEqual(out, [f"({want.real}, {want.imag})"])

    def test_mixed_with_real_and_integer(self):
        self.assertEqual(one("cmplx(1.0, 2.0) * 2"), "(2.0, 4.0)")
        self.assertEqual(one("2.5 * cmplx(1.0, 2.0)"), "(2.5, 5.0)")
        self.assertEqual(one("cmplx(1.0, 2.0) + 1"), "(2.0, 2.0)")
        self.assertEqual(one("-cmplx(1.0, 2.0)"), "(-1.0, -2.0)")

    def test_equality_but_no_ordering(self):
        self.assertEqual(one("cmplx(1, 2) == cmplx(1, 2), cmplx(1, 2) /= cmplx(1, 3), cmplx(1, 0) == 1"),
                         "true true true")
        for op in ("<", "<=", ">", ">="):
            fails(self, f"  print cmplx(1, 2) {op} cmplx(2, 3)", "cannot be ordered")

    def test_equality_does_not_trip_over_ordering(self):
        self.assertEqual(one("cmplx(1, 2) == cmplx(3, 4)"), "false")

    def test_comparing_mismatched_types_is_an_error_not_a_crash(self):
        fails(self, '  print "a" < 1', "cannot compare string with integer")

    def test_division_by_zero_complex(self):
        with self.assertRaises((LangError, ZeroDivisionError)):
            run("  print cmplx(1.0, 1.0) / cmplx(0.0, 0.0)")

    def test_abs_is_the_modulus(self):
        self.assertEqual(one("abs(cmplx(3.0, 4.0))"), "5.0")

    def test_conjg(self):
        self.assertEqual(one("conjg(cmplx(3.0, 4.0))"), "(3.0, -4.0)")

    def test_aimag_needs_a_complex(self):
        fails(self, "  print aimag(1.0)", "needs a complex number")

    def test_functions_agree_with_cmath(self):
        z = complex(0.5, 1.5)
        for name in ("sqrt", "exp", "log", "sin", "cos", "tan", "sinh", "cosh", "tanh", "asin", "acos", "atan",
                     "asinh", "acosh", "atanh", "log10"):
            with self.subTest(name):
                re, im = one(f"{name}(cmplx(0.5, 1.5))").strip("()").split(", ")
                got = complex(float(re), float(im))
                want = getattr(cmath, name)(z)
                self.assertAlmostEqual(got.real, want.real)
                self.assertAlmostEqual(got.imag, want.imag)

    def test_real_functions_still_reject_out_of_range_arguments(self):
        fails(self, "  print sqrt(-1.0)", "math domain error")
        fails(self, "  print log(0.0)")

    def test_sqrt_of_a_negative_via_complex(self):
        self.assertEqual(one("sqrt(cmplx(-4.0, 0.0))"), "(0.0, 2.0)")

    def test_euler_identity(self):
        out = run("  const complex :: i = cmplx(0.0, 1.0)\n  print abs(exp(i * 3.141592653589793) + 1)")
        self.assertLess(float(out[0]), 1e-15)

    def test_real_of_a_real_is_unchanged(self):
        self.assertEqual(one("real(3), real(2.5)"), "3.0 2.5")


class ComplexArrayTests(unittest.TestCase):
    DECL = "  complex :: a(2) = [cmplx(1.0, 1.0), cmplx(2.0, 0.0)]\n"

    def test_array_of_complex(self):
        self.assertEqual(run(self.DECL + "  print a"), ["[(1.0, 1.0), (2.0, 0.0)]"])

    def test_elementwise(self):
        self.assertEqual(run(self.DECL + "  print abs(a)"), ["[1.4142135623730951, 2.0]"])
        self.assertEqual(run(self.DECL + "  print a * 2"), ["[(2.0, 2.0), (4.0, 0.0)]"])
        self.assertEqual(run(self.DECL + "  print aimag(a)"), ["[1.0, 0.0]"])

    def test_sum_and_product(self):
        self.assertEqual(run(self.DECL + "  print sum(a), product(a)"), ["(3.0, 1.0) (2.0, 2.0)"])

    def test_dot_product_conjugates_its_first_argument(self):
        self.assertEqual(run(self.DECL + "  print dot_product(a, a)"), ["(6.0, 0.0)"])
        out = run("  complex :: a(1) = [cmplx(0.0, 1.0)]\n  complex :: b(1) = [cmplx(1.0, 0.0)]\n"
                  "  print dot_product(a, b)")
        self.assertEqual(out, ["(0.0, -1.0)"])

    def test_matmul_does_not_conjugate(self):
        out = run("  complex :: m(1, 1) = reshape([cmplx(0.0, 1.0)], [1, 1])\n  print matmul(m, m)")
        self.assertEqual(out, ["[[(-1.0, 0.0)]]"])

    def test_max_of_complex_is_an_error(self):
        fails(self, "  complex :: a(2) = [cmplx(1.0, 1.0), cmplx(2.0, 0.0)]\n  print maxval(a)")


class ComplexStructureTests(unittest.TestCase):
    def test_parameters_results_and_fields(self):
        extra = ("record wave\n  complex :: amp = cmplx(1.0, 0.0)\n  real :: freq\nend record\n"
                 "function scale(complex :: z, real :: k) -> complex\n  return z * k\nend function\n")
        out = run("  wave :: w\n  w.freq = 2.0\n  print w.amp, scale(w.amp, 3.0), w", extra)
        self.assertEqual(out, ["(1.0, 0.0) (3.0, 0.0) wave(amp=(1.0, 0.0), freq=2.0)"])

    def test_optional_complex(self):
        out = run("  complex? :: z\n  print z ?? cmplx(1.0, 1.0)")
        self.assertEqual(out, ["(1.0, 1.0)"])

    def test_update_a_complex_parameter(self):
        extra = "subroutine turn(inout complex :: z)\n  update z = z * cmplx(0.0, 1.0)\nend subroutine\n"
        self.assertEqual(run("  complex :: z = cmplx(1.0, 0.0)\n  call turn(z)\n  print z", extra), ["(0.0, 1.0)"])

    def test_round_trip(self):
        src = "program t\n  complex :: z = cmplx(1.0, 2.0)\n  print z * conjg(z)\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)


class ConvertComplexTests(unittest.TestCase):
    check = FixtureTests.check

    def test_complex_fixture_matches_gfortran(self):
        self.check("complex.f90")

    def test_gnu_extension_names_convert_too(self):
        text, findings = convert("program p\n  double complex :: z\n  z = dcmplx(1.0d0, 2.0d0)\n"
                                 "  print *, dimag(z), cdabs(z), dconjg(z)\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["2.0 2.23606797749979 (1.0, -2.0)"])

    def test_literal_and_declarations(self):
        text, findings = convert(
            "program p\n  complex(kind=8) :: a\n  complex*16 :: b\n  double complex :: c\n  complex(8) :: d\n"
            "  a = (1.0d0, 2.0d0)\n  b = a\n  c = b\n  d = c\n  print *, d\nend program\n")
        self.assertEqual(todos(findings), [])
        for name in "abcd":
            self.assertIn(f"complex :: {name}\n", text)
        self.assertIn("a = cmplx(1.0, 2.0)", text)

    def test_default_complex_notes_single_precision(self):
        _, findings = convert("program p\n  complex :: z\n  z = (1.0, 0.0)\n  print *, z\nend program\n")
        self.assertTrue(any("single precision" in f[2] and "complex" in f[2] for f in findings))

    def test_implied_do_is_not_mistaken_for_a_complex_literal(self):
        _, findings = convert("program p\n  integer :: i\n  integer :: a(3)\n"
                              "  print *, (a(i), i = 1, 3)\nend program\n")
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("implied-do", todos(findings)[0][2])

    def test_complex_literal_in_an_expression(self):
        text, _ = convert("program p\n  double complex :: z\n  z = 2.0d0 * (1.0d0, 1.0d0) + (0.0d0, 1.0d0)\n"
                          "  print *, z\nend program\n")
        self.assertEqual(run_bor(text), ["(2.0, 3.0)"])

    def test_kind_argument_of_cmplx_is_dropped(self):
        text, findings = convert("program p\n  complex(kind=8) :: z\n  z = cmplx(1.0d0, 2.0d0, kind=8)\n"
                                 "  print *, z\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["(1.0, 2.0)"])


if __name__ == "__main__":
    unittest.main()
