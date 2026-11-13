"""The where statement: assign only where a mask is true (D61)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output

A = "  real[5] :: a = [-2.0, 3.0, 0.0, 4.0, -1.0]\n  real[5] :: b = 0.0\n"


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


class RunTests(unittest.TestCase):
    def test_then_and_elsewhere(self):
        out = show(A + "  where a > 0.0 then\n    b = a * 2\n  elsewhere\n    b = -1\n  end where\n  println(b)")
        self.assertEqual(out, ["[-1.0, 6.0, -1.0, 8.0, -1.0]"])

    def test_without_elsewhere_the_rest_is_untouched(self):
        out = show(A + "  where a < 0.0 then\n    a = 0.0\n  end where\n  println(a)")
        self.assertEqual(out, ["[0.0, 3.0, 0.0, 4.0, 0.0]"])

    def test_several_assignments(self):
        out = show(A + "  where a > 0.0 then\n    b = 1.0\n    a = 9.0\n  end where\n  println(a, b)")
        self.assertEqual(out, ["[-2.0, 9.0, 0.0, 9.0, -1.0] [0.0, 1.0, 0.0, 1.0, 0.0]"])

    def test_the_mask_is_computed_once(self):
        out = show("  integer[3] :: n = [1, 2, 3]\n  where n > 1 then\n    n = 0\n    n = n + 5\n  end where\n  println(n)")
        self.assertEqual(out, ["[1, 5, 5]"])

    def test_a_matrix(self):
        out = show("  integer[2, 2] :: m = reshape([1, 2, 3, 4], [2, 2])\n  where m > 2 then\n    m = 0\n"
                   "  end where\n  println(m)")
        self.assertEqual(out, ["[[1, 0], [2, 0]]"])

    def test_a_section_as_the_target(self):
        out = show("  integer[6] :: v = [1, 2, 3, 4, 5, 6]\n  where v[1:3] > 1 then\n    v[1:3] = 0\n  end where\n  println(v)")
        self.assertEqual(out, ["[1, 0, 0, 4, 5, 6]"])

    def test_the_right_side_can_be_an_array(self):
        out = show(A + "  where a > 0.0 then\n    b = a + 1.0\n  end where\n  println(b)")
        self.assertEqual(out, ["[0.0, 4.0, 0.0, 5.0, 0.0]"])

    def test_where_is_still_a_name(self):
        self.assertEqual(show("  integer :: where = 4\n  where = where + 1\n  println(where)"), ["5"])


class ErrorTests(unittest.TestCase):
    def fails(self, body, message):
        with self.assertRaises(LangError) as cm:
            show(body)
        self.assertIn(message, str(cm.exception))

    def test_the_condition_must_be_an_array_of_logicals(self):
        self.fails(A + "  where 1 then\n    b = 1.0\n  end where", "needs an array of logicals")

    def test_the_target_must_match_the_mask(self):
        self.fails(A + "  real[2] :: c = 0.0\n  where a > 0.0 then\n    c = 1.0\n  end where", "mask's shape")

    def test_only_assignments_are_allowed(self):
        self.fails(A + "  where a > 0.0 then\n    println(a)\n  end where", "holds only assignments")

    def test_one_elsewhere(self):
        self.fails(A + "  where a > 0.0 then\n    b = 1.0\n  elsewhere\n    b = 2.0\n  elsewhere\n    b = 3.0\n  end where",
                   "only one 'elsewhere'")

    def test_a_missing_end(self):
        self.fails(A + "  where a > 0.0 then\n    b = 1.0\n", "missing 'end where'")

    def test_a_write_to_an_inout_parameter_needs_its_mark(self):
        sub = "subroutine clip(inout real[] :: a)\n  where a < 0.0 then\n    %sa = 0.0\n  end where\nend subroutine\n"
        prog = "program t\n  real[3] :: v = [1.0, -1.0, 2.0]\n  call clip(v)\n  println(v)\nend program\n"
        self.assertEqual(run_bor(sub % "@" + prog), ["[1.0, 0.0, 2.0]"])
        with self.assertRaises(LangError):
            run_bor(sub % "" + prog)


class ToolTests(unittest.TestCase):
    SRC = "program t\n  real[3] :: a = [1.0, -1.0, 2.0]\n  where a < 0.0 then\n    a = 0.0\n  elsewhere\n    a = a + 1.0\n  end where\n  println(a)\nend program\n"

    def test_printing_and_parsing_round_trip(self):
        once = to_source(parse(self.SRC))
        self.assertEqual(to_source(parse(once)), once)
        self.assertIn("elsewhere", once)

    def test_check_looks_inside(self):
        errors = check_errors(self.SRC.replace("a + 1.0", "nope + 1.0"))
        self.assertTrue(any("nope" in e[2] for e in errors), errors)


class ConversionTests(unittest.TestCase):
    def test_where_converts_and_matches_gfortran(self):
        src = ("program t\n  implicit none\n  real :: a(5), b(5)\n  a = [-2.0, 3.0, 0.0, 4.0, -1.0]\n  b = 0.0\n"
               "  where (a > 0.0)\n    b = a * 2\n  elsewhere\n    b = -1\n  end where\n  where (a < 0.0) a = 0.0\n"
               "  print *, b\n  print *, a\nend program\n")
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        self.assertIn("where a > 0.0 then", text)
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")

    def test_a_masked_elsewhere_is_left_as_a_todo(self):
        src = ("program t\n  real :: a(3)\n  a = 1.0\n  where (a > 5.0)\n    a = 0.0\n  elsewhere (a > 0.5)\n"
               "    a = 2.0\n  end where\nend program\n")
        _, findings = converted(src)
        self.assertTrue(any("elsewhere" in f[2] for f in findings), findings)

    def test_a_dummy_argument_changed_under_a_mask_is_inout(self):
        src = ("subroutine clip(a)\n  real :: a(3)\n  where (a < 0.0) a = 0.0\nend subroutine\n"
               "program t\n  real :: v(3)\n  v = [1.0, -1.0, 2.0]\n  call clip(v)\n  print *, v\nend program\n")
        text, findings = converted(src)
        self.assertIn("inout", text)
        got = run_bor(text)
        self.assertEqual(got, ["[1.0, 0.0, 2.0]"])
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")


if __name__ == "__main__":
    unittest.main()
