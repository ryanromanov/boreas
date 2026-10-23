import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas import run_source
from boreas.lexer import LangError


def run(body, funcs=""):
    out = []
    run_source(f"{funcs}\nprogram t\n{body}\nend program t\n", out.append)
    return out


class Tests(unittest.TestCase):
    def test_arith_and_int_division(self):
        self.assertEqual(run("print 7 / 2, -7 / 2, 2 ** 3 ** 2, 7.0 / 2"), ["3 -3 512 3.5"])

    def test_case_insensitive(self):
        self.assertEqual(run("INTEGER :: X = 4\nPrint x"), ["4"])

    def test_whole_array_ops(self):
        self.assertEqual(run("integer :: a(3) = [1,2,3]\nprint a * a + 1"), ["[2, 5, 10]"])

    def test_function(self):
        f = "function add(integer :: a, integer :: b) -> integer\n return a + b\nend function"
        self.assertEqual(run("print add(2, 3)", f), ["5"])

    def test_undeclared_variable(self):
        with self.assertRaisesRegex(LangError, "not declared"):
            run("x = 1")

    def test_no_implicit_real_to_integer(self):
        with self.assertRaisesRegex(LangError, "cannot assign real to integer"):
            run("integer :: n\nn = 2.5")

    def test_bounds_checked(self):
        with self.assertRaisesRegex(LangError, "out of bounds"):
            run("integer :: a(3)\na(4) = 1")

    def test_non_logical_condition(self):
        with self.assertRaisesRegex(LangError, "must be logical"):
            run("integer :: n = 1\nif n then\nprint 1\nend if")


SWAP = """subroutine swap(integer, intent(inout) :: a, integer, intent(inout) :: b)
  integer :: t
  t = a
  a = b
  b = t
end subroutine"""

AREA = """subroutine circle(real, intent(in) :: r, real, intent(out) :: area)
  area = 3.0 * r * r
end subroutine"""


class SubroutineTests(unittest.TestCase):
    def test_inout(self):
        self.assertEqual(run("integer :: x = 1, y = 2\ncall swap(x, y)\nprint x, y", SWAP), ["2 1"])

    def test_out_and_in(self):
        self.assertEqual(run("real :: a\ncall circle(2.0, a)\nprint a", AREA), ["12.0"])

    def test_in_widens_to_real(self):
        self.assertEqual(run("real :: a\ncall circle(2, a)\nprint a", AREA), ["12.0"])

    def test_array_element_and_whole_array(self):
        sub = """subroutine bump(integer, intent(inout) :: v)
  v = v + 1
end subroutine"""
        self.assertEqual(run("integer :: a(3) = [1, 2, 3]\ncall bump(a(2))\nprint a", sub), ["[1, 3, 3]"])

    def test_whole_array_inout(self):
        sub = """subroutine double(integer, intent(inout) :: xs)
  xs = xs * 2
end subroutine"""
        self.assertEqual(run("integer :: a(3) = [1, 2, 3]\ncall double(a)\nprint a", sub), ["[2, 4, 6]"])

    def test_no_arguments(self):
        sub = "subroutine hi()\n print \"hi\"\nend subroutine"
        self.assertEqual(run("call hi()\ncall hi", sub), ["hi", "hi"])

    def test_cannot_assign_intent_in(self):
        sub = "subroutine bad(integer, intent(in) :: n)\n n = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "intent\\(in\\)"):
            run("integer :: x = 0\ncall bad(x)", sub)

    def test_default_intent_is_in(self):
        sub = "subroutine bad(integer :: n)\n n = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "intent\\(in\\)"):
            run("integer :: x = 0\ncall bad(x)", sub)

    def test_out_must_be_assigned(self):
        sub = "subroutine lazy(integer, intent(out) :: n)\nend subroutine"
        with self.assertRaisesRegex(LangError, "never assigned"):
            run("integer :: x\ncall lazy(x)", sub)

    def test_out_cannot_be_read_first(self):
        sub = "subroutine peek(integer, intent(out) :: n)\n n = n + 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "before it is assigned"):
            run("integer :: x\ncall peek(x)", sub)

    def test_out_argument_must_be_variable(self):
        with self.assertRaisesRegex(LangError, "variable or array element"):
            run("call swap(1, 2)", SWAP)

    def test_aliasing_rejected(self):
        with self.assertRaisesRegex(LangError, "alias"):
            run("integer :: x = 1\ncall swap(x, x)", SWAP)

    def test_aliasing_with_in_argument_rejected(self):
        sub = "subroutine add(integer, intent(in) :: a, integer, intent(inout) :: b)\n b = b + a\nend subroutine"
        with self.assertRaisesRegex(LangError, "alias"):
            run("integer :: x = 1\ncall add(x, x)", sub)

    def test_distinct_elements_do_not_alias(self):
        self.assertEqual(run("integer :: a(2) = [1, 2]\ncall swap(a(1), a(2))\nprint a", SWAP), ["[2, 1]"])

    def test_type_mismatch_on_out(self):
        with self.assertRaisesRegex(LangError, "but 'b' is integer"):
            run("real :: a = 1.0\ninteger :: b = 1\ncall circle(1.0, b)", AREA)

    def test_wrong_argument_count(self):
        with self.assertRaisesRegex(LangError, "expects 2 arguments"):
            run("integer :: x = 1\ncall swap(x)", SWAP)

    def test_function_cannot_have_out(self):
        f = "function f(integer, intent(out) :: n) -> integer\n return 1\nend function"
        with self.assertRaisesRegex(LangError, "only subroutines"):
            run("print 1", f)

    def test_subroutine_in_expression_rejected(self):
        with self.assertRaisesRegex(LangError, "is a subroutine"):
            run("integer :: x = 1, y = 2\nprint swap(x, y)", SWAP)

    def test_call_on_function_rejected(self):
        f = "function one() -> integer\n return 1\nend function"
        with self.assertRaisesRegex(LangError, "is a function"):
            run("call one()", f)

    def test_subroutine_cannot_return_value(self):
        sub = "subroutine s()\n return 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "cannot return a value"):
            run("call s()", sub)

    def test_early_return(self):
        sub = "subroutine s(integer, intent(out) :: n)\n n = 1\n return\n n = 2\nend subroutine"
        self.assertEqual(run("integer :: x\ncall s(x)\nprint x", sub), ["1"])

    def test_unassigned_variable_rejected(self):
        with self.assertRaisesRegex(LangError, "before it is assigned"):
            run("integer :: x\nprint x")


if __name__ == "__main__":
    unittest.main()
