"""A callback's signature can return an array or several results (D64)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

DEFS = """function pair(real :: t) -> real[]
  return [t, t * 2.0]
end function

function grid(real :: t) -> real[,]
  return reshape([t, t, t, t], [2, 2])
end function

function bounds(real :: x) -> (real :: lo, real :: hi)
  return x - 1.0, x + 1.0
end function

function value_and_slope(real :: x) -> (real, real)
  return x * x, 2.0 * x
end function

function one(real :: x) -> real
  return x
end function
"""


def show(body, defs=""):
    return run_bor(DEFS + defs + "program t\n" + body + "\nend program\n")


class ArrayResultTests(unittest.TestCase):
    USE = ("function use_it(function(real :: t) -> real[] :: f) -> real\n  allocatable real[] :: v\n  v = f(1.0)\n"
           "  return sum(v) + size(v)\nend function\n")

    def test_a_callback_can_return_an_array(self):
        self.assertEqual(show("  println(use_it(pair))", self.USE), ["5.0"])

    def test_the_result_is_used_like_any_array(self):
        defs = "function second(function(real) -> real[] :: f) -> real\n  allocatable real[] :: v\n  v = f(2.0)\n  return v[2]\nend function\n"
        self.assertEqual(show("  println(second(pair))", defs), ["4.0"])

    def test_a_matrix_result(self):
        defs = "function total(function(real) -> real[,] :: f) -> real\n  return sum(f(2.0))\nend function\n"
        self.assertEqual(show("  println(total(grid))", defs), ["8.0"])

    def test_the_rank_must_match(self):
        defs = "function total(function(real) -> real[,] :: f) -> real\n  return 1.0\nend function\n"
        with self.assertRaisesRegex(LangError, "'pair' is function"):
            show("  println(total(pair))", defs)

    def test_an_array_result_does_not_fit_a_single_result(self):
        with self.assertRaisesRegex(LangError, "'one' is function"):
            show("  println(use_it(one))", self.USE)

    def test_the_element_type_must_match(self):
        defs = "function ints(integer :: n) -> integer[]\n  return [n]\nend function\n"
        with self.assertRaisesRegex(LangError, "'ints' is function"):
            show("  println(use_it(ints))", defs + self.USE)

    def test_it_can_be_passed_on(self):
        defs = self.USE + "function relay(function(real) -> real[] :: g) -> real\n  return use_it(g)\nend function\n"
        self.assertEqual(show("  println(relay(pair))", defs), ["5.0"])


class SeveralResultsTests(unittest.TestCase):
    USE = ("function spread_of(function(real :: x) -> (real :: lo, real :: hi) :: g) -> real\n  real :: a\n  real :: b\n"
           "  (a, b) = g(3.0)\n  return b - a\nend function\n")

    def test_a_callback_can_return_several_results(self):
        self.assertEqual(show("  println(spread_of(bounds))", self.USE), ["2.0"])

    def test_the_names_are_documentation(self):
        self.assertEqual(show("  println(spread_of(value_and_slope))", self.USE), ["-3.0"])

    def test_a_signature_without_names(self):
        defs = ("function slope(function(real) -> (real, real) :: g) -> real\n  real :: v\n  real :: d\n"
                "  (v, d) = g(5.0)\n  return d\nend function\n")
        self.assertEqual(show("  println(slope(value_and_slope))", defs), ["10.0"])

    def test_discarding_a_result(self):
        defs = ("function value_of(function(real) -> (real, real) :: g) -> real\n  real :: v\n  (v, _) = g(4.0)\n"
                "  return v\nend function\n")
        self.assertEqual(show("  println(value_of(value_and_slope))", defs), ["16.0"])

    def test_the_count_and_types_must_match(self):
        for fn in ("one", "pair"):
            with self.subTest(fn):
                with self.assertRaisesRegex(LangError, f"'{fn}' is function"):
                    show(f"  println(spread_of({fn}))", self.USE)
        defs = "function mixed(real :: x) -> (real, integer)\n  return x, 1\nend function\n"
        with self.assertRaisesRegex(LangError, "'mixed' is function"):
            show("  println(spread_of(mixed))", defs + self.USE)

    def test_a_single_assignment_is_an_error_when_run(self):
        defs = "function bad(function(real) -> (real, real) :: g) -> real\n  return g(1.0)\nend function\n"
        with self.assertRaisesRegex(LangError, "returns 2 results; unpack them"):
            show("  println(bad(bounds))", defs)

    def test_an_unpacking_of_the_wrong_size_is_an_error_when_run(self):
        defs = ("function bad(function(real) -> (real, real) :: g) -> real\n  real :: a\n  real :: b\n  real :: c\n"
                "  (a, b, c) = g(1.0)\n  return a\nend function\n")
        with self.assertRaisesRegex(LangError, "returns 2 results, but 3 targets"):
            show("  println(bad(bounds))", defs)

    def test_it_can_be_passed_on(self):
        defs = self.USE + "function relay(function(real) -> (real, real) :: g) -> real\n  return spread_of(g)\nend function\n"
        self.assertEqual(show("  println(relay(bounds))", defs), ["2.0"])

    def test_one_result_in_parentheses_is_still_an_error(self):
        with self.assertRaisesRegex(LangError, "needs two or more results"):
            show("", "function f(function(real) -> (real) :: g) -> real\n  return 1.0\nend function\n")


class ToolTests(unittest.TestCase):
    SRC = ("function f(function(real :: x) -> real[] :: a, function(real) -> (real :: lo, real :: hi) :: b) -> real\n"
           "  real :: p\n  real :: q\n  (p, q) = b(1.0)\n  return sum(a(p)) + q\nend function\n")

    def test_printing_and_parsing_round_trip(self):
        once = to_source(parse(self.SRC + "program t\nend program\n"))
        self.assertEqual(to_source(parse(once)), once)
        self.assertIn("-> real[]", once)
        self.assertIn("-> (real :: lo, real :: hi)", once)

    def check(self, body):
        src = ("function f(function(real) -> (real :: lo, real :: hi) :: g) -> real\n  real :: a\n  real :: b\n"
               + body + "\n  return a\nend function\nprogram t\nend program\n")
        return [m for _, _, m, _ in check_errors(src)]

    def test_check_accepts_a_correct_unpacking(self):
        self.assertEqual(self.check("  (a, b) = g(1.0)"), [])

    def test_check_finds_a_callback_used_as_a_single_value(self):
        self.assertEqual(self.check("  a = g(1.0)"), ["'g' returns 2 results; unpack them: (a, b) = g(...)"])

    def test_check_finds_the_wrong_number_of_targets(self):
        self.assertEqual(self.check("  real :: c\n  (a, b, c) = g(1.0)"), ["'g' returns 2 results, but 3 targets are listed"])


if __name__ == "__main__":
    unittest.main()
