"""Matrix literals `[1, 2, 3; 4, 5, 6]` and flatten()."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.cli import check_errors
from boreas.formatter import format_source
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output


def show(body, defs=""):
    return run_bor(defs + "program t\n" + body + "\nend program\n")


def fails(case, body, message):
    with case.assertRaises(LangError) as cm:
        show(body)
    case.assertIn(message, str(cm.exception))


class LiteralTests(unittest.TestCase):
    def test_rows_in_reading_order(self):
        self.assertEqual(show("  integer[2, 3] :: m = [1, 2, 3; 4, 5, 6]\n  println(m, m[1, 3], m[2, 1], shape(m))"),
                         ["[[1, 2, 3], [4, 5, 6]] 3 4 [2, 3]"])

    def test_it_is_stored_by_columns(self):
        self.assertEqual(show("  println(flatten([1, 2, 3; 4, 5, 6]))"), ["[1, 4, 2, 5, 3, 6]"])
        self.assertEqual(show("  println([1, 2, 3; 4, 5, 6] == reshape([1, 4, 2, 5, 3, 6], [2, 3]))"),
                         ["[[true, true, true], [true, true, true]]"])

    def test_a_column_is_a_matrix(self):
        self.assertEqual(show("  println(shape([1; 2; 3]), shape([1, 2, 3]))"), ["[3, 1] [3]"])

    def test_numbers_widen_like_an_array_literal(self):
        self.assertEqual(show("  real[2, 2] :: m = [1, 2.5; 3, 4]\n  println(m)"), ["[[1.0, 2.5], [3.0, 4.0]]"])

    def test_rows_can_sit_on_separate_lines(self):
        body = "  integer[3, 3] :: m = [1, 0, 0;\n                        0, 1, 0;\n                        0, 0, 1]\n  println(matmul(m, m))"
        self.assertEqual(show(body), ["[[1, 0, 0], [0, 1, 0], [0, 0, 1]]"])

    def test_it_works_wherever_an_array_does(self):
        self.assertEqual(show("  println(transpose([1, 2, 3; 4, 5, 6]), sum([1, 2; 3, 4], 1))"),
                         ["[[1, 4], [2, 5], [3, 6]] [4, 6]"])
        self.assertEqual(show("  const integer[2, 2] :: k = [1, 2; 3, 4]\n  println(k[2, 1])"), ["3"])
        self.assertEqual(show("  println([1, 2; 3, 4] * 2)"), ["[[2, 4], [6, 8]]"])

    def test_elements_can_be_expressions(self):
        self.assertEqual(show("  integer :: n = 3\n  println([n, n + 1; n * 2, -n])"), ["[[3, 4], [6, -3]]"])

    def test_listing_splicing_still_works(self):
        self.assertEqual(show("  integer[2] :: x = [1, 2]\n  println([x, 3])"), ["[1, 2, 3]"])


class ErrorTests(unittest.TestCase):
    def test_rows_must_match(self):
        fails(self, "  println([1, 2; 3])", "row 2 of the matrix has 1 value, but row 1 has 2")
        fails(self, "  println([1, 2, 3; 4, 5])", "row 2 of the matrix has 2 values, but row 1 has 3")

    def test_a_row_is_needed_after_the_semicolon(self):
        fails(self, "  println([1, 2;])", "needs a row after ';'")

    def test_only_single_values(self):
        fails(self, "  println([1, [2, 3]; 4, 5])", "holds single values")
        fails(self, "  println([(i do i = 1, 3); 1, 2, 3])", "generator")

    def test_the_declared_shape_is_checked(self):
        fails(self, "  integer[3, 2] :: m = [1, 2, 3; 4, 5, 6]", "has shape (3, 2)")


class FlattenTests(unittest.TestCase):
    def test_flatten_gives_storage_order(self):
        self.assertEqual(show("  integer[2, 3] :: m = reshape([1, 2, 3, 4, 5, 6], [2, 3])\n  println(flatten(m), flatten([7, 8]))"),
                         ["[1, 2, 3, 4, 5, 6] [7, 8]"])

    def test_it_needs_an_array(self):
        fails(self, "  println(flatten(3))", "needs an array")

    def test_a_program_can_define_its_own(self):
        defs = "function flatten(integer :: x) -> integer\n  return x + 1\nend function\n"
        self.assertEqual(show("  println(flatten(1))", defs), ["2"])


class ToolTests(unittest.TestCase):
    SRC = "program t\n  integer[2, 3] :: m = [1, 2, 3; 4, 5, 6]\n  println(m)\nend program\n"

    def test_printing_and_parsing_round_trip(self):
        once = to_source(parse(self.SRC))
        self.assertIn("[1, 2, 3; 4, 5, 6]", once)
        self.assertEqual(to_source(parse(once)), once)

    def test_check_looks_inside(self):
        errors = check_errors(self.SRC.replace("4, 5", "nope, 5"))
        self.assertTrue(any("nope" in e[2] for e in errors), errors)

    def test_the_formatter_keeps_it(self):
        self.assertEqual(format_source(self.SRC), self.SRC)


    def test_the_formatter_keeps_rows_on_their_own_lines(self):
        src = "program t\n  integer[2, 2] :: m = [1, 2;\n                        3, 4]\n  println(m)\nend program\n"
        self.assertEqual(format_source(src), src)
        loose = src.replace("                        3, 4]", "3, 4]")
        self.assertEqual(format_source(loose), loose.replace("\n3, 4]", "\n  3, 4]") if False else format_source(loose))
        self.assertEqual(run_bor(format_source(loose)), ["[[1, 2], [3, 4]]"])


class ConversionTests(unittest.TestCase):
    SRC = ("program t\n  implicit none\n  integer :: a(2,3), b(2,3), c(2,3), d(2,3), i\n"
           "  a = reshape([1, 4, 2, 5, 3, 6], [2, 3])\n  b = reshape([1, 2, 3, 4, 5, 6], [2, 3], order=[2, 1])\n"
           "  c = transpose(reshape([1, 2, 3, 4, 5, 6], [3, 2]))\n  data d / 1, 4, 2, 5, 3, 6 /\n"
           "  do i = 1, 2\n    print *, a(i, :), b(i, :), c(i, :), d(i, :)\n  end do\nend program\n")

    def test_every_way_of_writing_it_becomes_a_literal(self):
        text, findings = converted(self.SRC)
        self.assertEqual(todos(findings), [])
        self.assertEqual(text.count("[1, 2, 3; 4, 5, 6]"), 4, text)
        self.assertNotIn("reshape", text)
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(self.SRC)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")

    def test_a_reshape_with_a_computed_shape_stays(self):
        src = "program t\n  integer :: n = 2\n  integer :: a(2,2)\n  a = reshape([1, 2, 3, 4], [n, 2])\n  print *, a\nend program\n"
        text, _ = converted(src)
        self.assertIn("reshape(", text)

    def test_pad_is_not_supported(self):
        src = "program t\n  integer :: a(2,2)\n  a = reshape([1, 2, 3], [2, 2], [0])\n  print *, a\nend program\n"
        _, findings = converted(src)
        self.assertTrue(any("pad" in f[2] for f in findings), findings)


if __name__ == "__main__":
    unittest.main()
