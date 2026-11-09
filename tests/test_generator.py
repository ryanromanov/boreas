import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.cli import check_errors
from boreas.verify import same_output


def show(body, decls=""):
    return run_bor("program t\n" + decls + body + "\nend program\n")


X = "  integer[3] :: x = [1, 2, 3]\n"


class GeneratorTests(unittest.TestCase):
    def test_a_formula_over_a_range(self):
        self.assertEqual(show("  println([i * i do i = 1, 4])"), ["[1, 4, 9, 16]"])

    def test_a_step_and_a_negative_step(self):
        self.assertEqual(show("  println([i do i = 1, 10, 4], [i do i = 10, 1, -3])"), ["[1, 5, 9] [10, 7, 4, 1]"])

    def test_empty_range_is_an_empty_array(self):
        self.assertEqual(show("  println(size([i do i = 1, 0]), size([i do i = 5, 1]))"), ["0 0"])

    def test_mixed_with_other_items(self):
        self.assertEqual(show(X + "  println([x, (0 do k = 1, 2)], [(k do k = 1, 2), x, 9])"),
                         ["[1, 2, 3, 0, 0] [1, 2, 1, 2, 3, 9]"])

    def test_several_values_per_step(self):
        self.assertEqual(show(X + "  println([(x[k], 10 * x[k] do k = 1, 3)])"), ["[1, 10, 2, 20, 3, 30]"])

    def test_nested_goes_inner_first_like_fortran(self):
        body = "  real[2, 2] :: a = reshape([1.0, 2.0, 3.0, 4.0], [2, 2])\n  println([(a[p, q] do p = 1, 2) do q = 1, 2])"
        self.assertEqual(show(body), ["[1.0, 2.0, 3.0, 4.0]"])
        self.assertEqual(show("  println([(10 * q + p do p = 1, 2) do q = 1, 2])"), ["[11, 12, 21, 22]"])

    def test_bounds_can_be_expressions(self):
        self.assertEqual(show("  integer :: n = 3\n  println([i + n do i = n - 1, 2 * n - 3])"), ["[5, 6]"])

    def test_the_variable_is_local_and_needs_no_declaration(self):
        self.assertEqual(show("  println([i do i = 1, 3], i)", "  integer :: i = 99\n"), ["[1, 2, 3] 99"])
        self.assertEqual(show("  println([j do j = 1, 2])"), ["[1, 2]"])

    def test_the_variable_is_not_visible_afterwards(self):
        errors = check_errors("program t\n  println([j do j = 1, 2], j)\nend program\n")
        self.assertEqual([e[2] for e in errors], ["'j' is not declared"])

    def test_errors(self):
        for body, text in [("println([i do i = 1.5, 3])", "must be an integer"),
                           ("println([i do i = 1, 3, 0])", "step cannot be zero")]:
            with self.subTest(body):
                with self.assertRaises(LangError) as cm:
                    show("  " + body)
                self.assertIn(text, str(cm.exception))

    def test_only_inside_an_array_literal(self):
        with self.assertRaises(LangError) as cm:
            parse("program t\n  integer[3] :: y\n  y = (i do i = 1, 3)\nend program\n")
        self.assertIn("belongs in an array literal", str(cm.exception))

    def test_several_items_need_parentheses(self):
        with self.assertRaises(LangError) as cm:
            parse("program t\n  println([1, i do i = 1, 3])\nend program\n")
        self.assertIn("write the generator in parentheses", str(cm.exception))

    def test_prints_back_the_same(self):
        for src in ["[i * i do i = 1, 4]", "[x, (0.0 do i = 1, n)]", "[(x[k], y[k] do k = 1, n, 2)]",
                    "[(a[p, q] do p = 1, 2) do q = 1, 2]"]:
            with self.subTest(src):
                text = "program t\n  integer[4] :: v\n  v = " + src + "\nend program\n"
                again = to_source(parse(text), [])
                self.assertIn(src, again)
                self.assertEqual(to_source(parse(again), []), again)


class ExampleTests(unittest.TestCase):
    def test_the_arrays_example_runs(self):
        path = os.path.join(os.path.dirname(__file__), "..", "examples", "arrays.bor")
        with open(path) as f:
            out = run_bor(f.read())
        self.assertEqual(out[0], "squares: [1, 4, 9, 16, 25]")
        self.assertIn("grid:    [11, 12, 13, 21, 22, 23]", out)
        self.assertIn("centered:     [-4, -3, -2, -1, 0, 1, 2, 3]", out)
        self.assertEqual(len(out), 13)


class GeneratorConversionTests(unittest.TestCase):
    def check(self, src):
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_the_fftpack_padding_case(self):
        text = self.check("program t\n  integer :: n, i\n  real :: x(3), y(6)\n  n = 3\n  x = [1.0, 2.0, 3.0]\n"
                          "  y = [x, (0.0, i=1, n)]\n  print *, y\nend program\n")
        self.assertIn("(0.0 do i = 1, n)", text)

    def test_formula_step_and_empty(self):
        self.check("program t\n  integer :: i, a(4), b(3), c(0)\n  a = [(i*i, i=1, 4)]\n  b = [(i, i=1, 9, 4)]\n"
                   "  print *, a\n  print *, b\n  print *, size([(i, i=3, 1)])\nend program\n")

    def test_several_values_per_step(self):
        self.check("program t\n  integer :: i, x(3), y(3), z(6)\n  x = [1, 2, 3]\n  y = [4, 5, 6]\n"
                   "  z = [(x(i), y(i), i=1, 3)]\n  print *, z\nend program\n")

    def test_nested_matches_fortran_order(self):
        self.check("program t\n  integer :: i, j, a(2, 2), z(4)\n  a = reshape([1, 2, 3, 4], [2, 2])\n"
                   "  z = [((a(i, j) * 10 + i, i=1, 2), j=1, 2)]\n  print *, z\nend program\n")

    def test_old_bracket_form_with_slashes(self):
        self.check("program t\n  integer :: i, a(3)\n  a = (/ (2*i, i=1, 3) /)\n  print *, a\nend program\n")

    def test_in_declaration_initializers(self):
        self.check("program t\n  integer :: i\n  integer :: a(3) = [(i, i=1, 3)]\n"
                   "  integer, parameter :: p(3) = [(2*i, i=1, 3)]\n  print *, a\n  print *, p\nend program\n")


if __name__ == "__main__":
    unittest.main()
