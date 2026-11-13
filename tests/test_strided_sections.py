"""Array sections with a step: a[lo:hi:step] (D59)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output

A = "  integer[10] :: a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\n"


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


class ReadTests(unittest.TestCase):
    def test_every_second_element(self):
        self.assertEqual(show(A + "  println(a[1:10:2])"), ["[1, 3, 5, 7, 9]"])

    def test_the_end_is_included_when_the_step_lands_on_it(self):
        self.assertEqual(show(A + "  println(a[1:10:3], a[1:9:4])"), ["[1, 4, 7, 10] [1, 5, 9]"])

    def test_omitted_ends(self):
        self.assertEqual(show(A + "  println(a[::3])\n  println(a[2::4])\n  println(a[:5:2])"),
                         ["[1, 4, 7, 10]", "[2, 6, 10]", "[1, 3, 5]"])

    def test_a_negative_step_runs_backwards(self):
        self.assertEqual(show(A + "  println(a[10:1:-3])\n  println(a[10:1:-1])\n  println(a[5:2:-1])"),
                         ["[10, 7, 4, 1]", "[10, 9, 8, 7, 6, 5, 4, 3, 2, 1]", "[5, 4, 3, 2]"])

    def test_a_negative_step_needs_both_ends(self):
        for section in ("a[::-1]", "a[5::-1]", "a[:2:-1]"):
            with self.subTest(section):
                with self.assertRaisesRegex(LangError, "negative step needs both ends: write a\\[10:1:-1\\]"):
                    show(A + f"  println({section})")

    def test_an_empty_section_is_fine(self):
        self.assertEqual(show(A + "  println(a[3:1:2], size(a[3:1:2]), size(a[1:3:-1]))"), ["[] 0 0"])

    def test_a_step_of_one_is_a_plain_section(self):
        self.assertEqual(show(A + "  println(a[2:4:1])"), ["[2, 3, 4]"])

    def test_each_dimension_has_its_own_step(self):
        m = "  integer[3, 4] :: m = reshape([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], [3, 4])\n"
        self.assertEqual(show(m + "  println(m[::2, ::2])"), ["[[1, 7], [3, 9]]"])
        self.assertEqual(show(m + "  println(m[:, 4:1:-2])"), ["[[10, 4], [11, 5], [12, 6]]"])
        self.assertEqual(show(m + "  println(m[2, ::2])"), ["[2, 8]"])

    def test_the_bounds_are_expressions(self):
        self.assertEqual(show(A + "  integer :: k = 3\n  println(a[k:2 * k + 1:k - 1])"), ["[3, 5, 7]"])


class WriteTests(unittest.TestCase):
    def test_assign_a_value_to_a_strided_section(self):
        self.assertEqual(show(A + "  a[1:9:2] = 0\n  println(a)"), ["[0, 2, 0, 4, 0, 6, 0, 8, 0, 10]"])

    def test_assign_an_array_in_the_order_of_the_section(self):
        self.assertEqual(show(A + "  a[10:1:-2] = [1, 2, 3, 4, 5]\n  println(a)"), ["[1, 5, 3, 4, 5, 3, 7, 2, 9, 1]"])

    def test_the_shape_must_match(self):
        with self.assertRaisesRegex(LangError, "shape"):
            show(A + "  a[1:9:2] = [1, 2, 3]")

    def test_two_interleaved_sections_are_not_aliased(self):
        defs = "subroutine swap(inout integer[] :: x, inout integer[] :: y)\n  integer[5] :: t\n  t = x\n  @x = y\n  @y = t\nend subroutine\n"
        out = run_bor(defs + "program t\n" + A + "  call swap(a[1:9:2], a[2:10:2])\n  println(a)\nend program\n")
        self.assertEqual(out, ["[2, 1, 4, 3, 6, 5, 8, 7, 10, 9]"])

    def test_overlapping_sections_are_still_aliased(self):
        defs = "subroutine swap(inout integer[] :: x, inout integer[] :: y)\n  integer[5] :: t\n  t = x\n  @x = y\n  @y = t\nend subroutine\n"
        with self.assertRaisesRegex(LangError, "must not alias"):
            run_bor(defs + "program t\n" + A + "  call swap(a[1:9:2], a[1:5:1])\nend program\n")


class ErrorTests(unittest.TestCase):
    def test_a_zero_step(self):
        with self.assertRaisesRegex(LangError, "step of a section cannot be zero"):
            show(A + "  println(a[1:5:0])")

    def test_a_step_must_be_an_integer(self):
        with self.assertRaisesRegex(LangError, "array index must be integer"):
            show(A + "  println(a[1:5:1.5])")

    def test_out_of_bounds(self):
        with self.assertRaisesRegex(LangError, "slice 1:11:2 out of bounds 1..10"):
            show(A + "  println(a[1:11:2])")
        with self.assertRaisesRegex(LangError, "slice 0:5:2 out of bounds"):
            show(A + "  println(a[0:5:2])")

    def test_a_missing_step_is_a_parse_error(self):
        with self.assertRaisesRegex(LangError, "step goes after the second"):
            parse("program t\n  integer[3] :: a\n  println(a[1:3:])\nend program\n")
        with self.assertRaisesRegex(LangError, "step goes after the second"):
            parse("program t\n  integer[3] :: a\n  println(a[1::])\nend program\n")

    def test_a_string_has_no_step(self):
        with self.assertRaisesRegex(LangError, "a substring has no step"):
            show('  string :: s = "abcdef"\n  println(s[1:5:2])')


class ToolingTests(unittest.TestCase):
    def test_printing_and_reparsing_is_stable(self):
        src = "program t\n  integer[10] :: a\n  println(a[1:9:2], a[::2], a[2::3], a[:5:2], a[10:1:-1], a[3:1:-1])\nend program\n"
        once = to_source(parse(src))
        for text in ("a[1:9:2]", "a[::2]", "a[2::3]", "a[:5:2]", "a[10:1:-1]", "a[3:1:-1]"):
            self.assertIn(text, once)
        self.assertEqual(to_source(parse(once)), once)

    def test_check_looks_inside_the_step(self):
        errors = check_errors("program t\n  integer[10] :: a\n  println(a[1:9:nope])\nend program\n")
        self.assertTrue(any("nope" in e[2] for e in errors), errors)


class ConversionTests(unittest.TestCase):
    def test_strided_sections_convert_and_match_gfortran(self):
        src = ("program t\n  integer :: a(10)\n  integer :: m(3, 4)\n  integer :: i\n  a = [(i, i = 1, 10)]\n"
               "  m = reshape([(i, i = 1, 12)], [3, 4])\n  print *, a(1:10:2)\n  print *, a(::3)\n  print *, a(10:1:-3)\n"
               "  print *, a(10:1:-1)\n  a(1:9:2) = 0\n  print *, a\n  a(10:1:-2) = [1, 2, 3, 4, 5]\n  print *, a\n"
               "  print *, m(::2, 2)\n  print *, sum(a(2::4))\nend program\n")
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        self.assertIn("a[1:10:2]", text)
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")

    def test_a_negative_stride_with_an_omitted_bound_is_left_alone(self):
        _, findings = converted("program t\n  integer :: a(10)\n  print *, a(::-1)\nend program\n")
        self.assertTrue(any("negative stride with an omitted bound" in f[2] for f in todos(findings) or findings), findings)


if __name__ == "__main__":
    unittest.main()
