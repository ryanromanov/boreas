import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, run_bor, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def run(body, ieee=True, decls=""):
    out = []
    src = ("use ieee\n\n" if ieee else "") + f"program t\n{decls}\n{body}\nend program\n"
    Interp(parse(src), out.append).run()
    return out


def fails(test, body, message, ieee=False, decls=""):
    with test.assertRaises(LangError) as cm:
        run(body, ieee, decls)
    test.assertIn(message, str(cm.exception))


class StrictByDefaultTests(unittest.TestCase):
    def test_bad_real_arithmetic_stops_the_program(self):
        fails(self, "  println(1.0 / 0.0)", "division by zero")
        fails(self, "  println(sqrt(-1.0))", "domain error")
        fails(self, "  println(log(0.0))", "domain error")
        fails(self, "  println(exp(1000.0))", "range error")
        fails(self, "  println(10.0 ** 400)", "too large")

    def test_a_nan_made_on_purpose_is_allowed_anywhere(self):
        self.assertEqual(run("  real :: x = nan()\n  println(x, isnan(x), x == x)", False), ["NaN true false"])


class IeeeTests(unittest.TestCase):
    ZERO = "  real :: z = 0.0\n"

    def test_division(self):
        self.assertEqual(run("  println(1.0 / z, -1.0 / z, z / z, 5 / z)", decls=self.ZERO), ["Infinity -Infinity NaN Infinity"])

    def test_math_functions(self):
        self.assertEqual(run("  println(sqrt(-1.0), asin(2.0), acos(-3.0), log(z), log(-1.0), exp(1000.0), atanh(1.0))",
                             decls=self.ZERO), ["NaN NaN NaN -Infinity NaN Infinity Infinity"])

    def test_power_and_modulo(self):
        self.assertEqual(run("  println(10.0 ** 400, z ** (-1), mod(5.0, z))", decls=self.ZERO), ["Infinity Infinity NaN"])

    def test_comparisons_follow_ieee(self):
        body = "  real :: x = nan()\n  println(x == x, x /= x, x < 1.0, x > 1.0, x <= x)"
        self.assertEqual(run(body), ["false true false false false"])

    def test_isnan_isinf_and_arrays(self):
        body = "  println(isnan(sqrt(-1.0)), isinf(1.0 / z), isinf(1.0), isnan([1.0, nan()]), sqrt([4.0, -4.0]))"
        self.assertEqual(run(body, decls=self.ZERO), ["true true false [false, true] [2.0, NaN]"])

    def test_single_precision(self):
        self.assertEqual(run("  real32 :: y = 0.0\n  println(1.0 / y, sqrt(-1.0 + y), y / y)"),
                         ["Infinity NaN NaN"])
        self.assertEqual(run("  real32 :: y = 3.0e38\n  println(y * 10.0)"), ["Infinity"])

    def test_formatted_output_of_special_values(self):
        body = ("  real :: x = nan()\n  real :: y = infinity()\n"
                '  println(fixed(x, 2, 8) + "|" + fixed(y, 2, 10) + "|" + fixed(-y, 2, 6) + "|" + fixed(-y, 2, 9) + "|" + '
                'fixed(y, 2, 4) + "|" + fixed(y, 2, 2) + "|" + right(x, 5) + "|" + fixed(y, 2))')
        self.assertEqual(run(body), ["     NaN|  Infinity|  -Inf|-Infinity| Inf|**|  NaN|Infinity"])

    def test_things_that_still_stop(self):
        fails(self, "  println(1 / 0)", "integer division by zero", ieee=True)
        fails(self, "  println(int(nan()))", "", ieee=True)
        fails(self, "  integer :: n\n  n = nint(infinity())", "", ieee=True)

    def test_the_flag_belongs_to_the_run(self):
        run("  println(1.0 / z)", decls=self.ZERO)
        fails(self, "  real :: z = 0.0\n  println(1.0 / z)", "division by zero")


class VerifyCompareTests(unittest.TestCase):
    def test_special_values_compare(self):
        from boreas.verify import same_output
        self.assertTrue(same_output("NaN Infinity -Infinity", "  NaN   Infinity  -Infinity"))
        self.assertFalse(same_output("Infinity", "-Infinity"))
        self.assertFalse(same_output("NaN", "1.0"))
        self.assertFalse(same_output("Infinity", "1.0"))


class SyntaxTests(unittest.TestCase):
    def test_use_ieee_round_trips(self):
        src = "use ieee\n\nprogram t\n  println(1)\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)
        self.assertTrue(parse(src).ieee)
        self.assertFalse(parse("program t\nend program\n").ieee)

    def test_use_ieee_takes_no_names(self):
        with self.assertRaises(LangError):
            parse("use ieee: x\nprogram t\nend program\n")


class ConvertTests(unittest.TestCase):
    check = FixtureTests.check

    def test_fixture_matches_gfortran(self):
        got = self.check("ieee.f90")
        self.assertEqual(got[0], "Infinity -Infinity NaN")

    def test_converted_programs_say_use_ieee(self):
        text, _ = convert("program p\n  print *, 1\nend program\n")
        self.assertIn("use ieee", text)
        self.assertTrue(parse(text).ieee)

    def test_the_nan_test_idiom_converts(self):
        src = "program p\n  real :: x\n  x = asin(2.0)\n  if (x .ne. x) print *, 'nan'\nend program\n"
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["nan"])


if __name__ == "__main__":
    unittest.main()
