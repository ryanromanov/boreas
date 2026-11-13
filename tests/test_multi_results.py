"""Multiple results: `-> (integer :: q, integer :: r)`, `return a, b`, `(q, r) = f()` (D57)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

DIVMOD = """function divmod(integer :: a, integer :: b) -> (integer :: q, integer :: r)
  return a / b, mod(a, b)
end function
"""


def run(body, defs=DIVMOD):
    return run_bor(defs + "\nprogram t\n" + body + "\nend program\n")


class UnpackTests(unittest.TestCase):
    def test_a_function_gives_back_two_values(self):
        self.assertEqual(run("integer :: q\ninteger :: r\n(q, r) = divmod(17, 5)\nprintln(q, r)"), ["3 2"])

    def test_an_underscore_throws_a_result_away(self):
        self.assertEqual(run("integer :: q\n(q, _) = divmod(100, 7)\nprintln(q)"), ["14"])
        self.assertEqual(run("integer :: r\n(_, r) = divmod(100, 7)\nprintln(r)"), ["2"])

    def test_targets_can_be_elements_and_fields(self):
        defs = DIVMOD + "record pair\n  integer :: a\n  integer :: b\nend record\n"
        body = "integer[2] :: v\npair :: p\n(v[2], p.b) = divmod(9, 4)\nprintln(v, p)"
        self.assertEqual(run(body, defs), ["[0, 2] pair(a=0, b=1)"])

    def test_results_are_converted_like_a_single_result(self):
        defs = "function f() -> (real :: x, integer :: n)\n  return 1, 2\nend function\n"
        self.assertEqual(run("real :: x\ninteger :: n\n(x, n) = f()\nprintln(x, n)", defs), ["1.0 2"])

    def test_a_result_is_assigned_to_its_target_like_any_assignment(self):
        defs = "function f() -> (real :: x, real :: y)\n  return 1.5, 2.5\nend function\n"
        with self.assertRaisesRegex(LangError, "integer"):
            run("integer :: a\ninteger :: b\n(a, b) = f()", defs)

    def test_the_arguments_can_be_expressions(self):
        self.assertEqual(run("integer :: q\ninteger :: r\n(q, r) = divmod(2 * 25, 3 + 4)\nprintln(q, r)"), ["7 1"])


class MarkTests(unittest.TestCase):
    def test_a_visible_target_carries_its_own_mark(self):
        defs = DIVMOD + """subroutine report(integer :: n, out integer :: quotient)
  integer :: rest
  (@quotient, rest) = divmod(n, 5)
  println("rest", rest)
end subroutine
"""
        self.assertEqual(run("integer :: x\ncall report(23, x)\nprintln(x)", defs), ["rest 3", "4"])

    def test_a_missing_mark_is_an_error(self):
        defs = DIVMOD + "subroutine report(integer :: n, out integer :: quotient)\n  integer :: rest\n" \
                        "  (quotient, rest) = divmod(n, 5)\nend subroutine\n"
        with self.assertRaisesRegex(LangError, "'quotient' is an out parameter.*write '@quotient = ...'"):
            parse(defs + "program t\nend program\n")

    def test_a_mark_on_a_local_is_an_error(self):
        with self.assertRaisesRegex(LangError, "'@' is for out and inout parameters and module variables, but 'q'"):
            parse(DIVMOD + "program t\ninteger :: q\ninteger :: r\n(@q, r) = divmod(1, 1)\nend program\n")

    def test_a_function_cannot_write_a_module_variable_this_way(self):
        defs = """module m
  public integer :: total = 0
  function divmod(integer :: a, integer :: b) -> (integer :: q, integer :: r)
    return a / b, mod(a, b)
  end function
  public function f(integer :: n) -> integer
    integer :: r
    (@total, r) = divmod(n, 2)
    return r
  end function
end module
"""
        with self.assertRaisesRegex(LangError, "cannot change the module variable 'total'"):
            parse(defs + "program t\nend program\n")

    def test_a_subroutine_can_unpack_into_a_module_variable(self):
        defs = """module m
  public integer :: total = 0
  function divmod(integer :: a, integer :: b) -> (integer :: q, integer :: r)
    return a / b, mod(a, b)
  end function
  public subroutine put(integer :: n)
    integer :: r
    (@total, r) = divmod(n, 2)
  end subroutine
end module
"""
        self.assertEqual(run("use m: total, put\ncall put(9)\nprintln(total)", defs), ["4"])

    def test_the_same_variable_twice_is_an_error(self):
        with self.assertRaisesRegex(LangError, "'x' is assigned twice"):
            parse(DIVMOD + "program t\ninteger :: x\n(x, x) = divmod(1, 1)\nend program\n")

    def test_an_underscore_cannot_be_marked(self):
        with self.assertRaisesRegex(LangError, "nothing to mark"):
            parse(DIVMOD + "program t\ninteger :: x\n(x, @_) = divmod(1, 1)\nend program\n")


class SameRulesAsAssignmentTests(unittest.TestCase):
    def test_a_constant_cannot_be_a_target(self):
        with self.assertRaisesRegex(LangError, "'c' is a constant and cannot be assigned"):
            run("const integer :: c = 1\ninteger :: r\n(c, r) = divmod(1, 1)")

    def test_an_in_parameter_cannot_be_a_target(self):
        defs = DIVMOD + "function f(integer :: n) -> integer\n  integer :: r\n  (n, r) = divmod(n, 2)\n  return r\nend function\n"
        with self.assertRaisesRegex(LangError, "'n' is an 'in' parameter and cannot be assigned"):
            run("println(f(4))", defs)

    def test_an_index_out_of_bounds_is_an_error(self):
        with self.assertRaisesRegex(LangError, "index 5 out of bounds"):
            run("integer[3] :: v\ninteger :: r\n(v[5], r) = divmod(1, 1)")

    def test_two_results_that_are_the_same_record_are_separate_copies(self):
        defs = "record pt\n  real :: x\nend record\nfunction f() -> (pt :: a, pt :: b)\n  pt :: p = pt(0.0)\n" \
               "  return p, p\nend function\n"
        self.assertEqual(run("pt :: a\npt :: b\n(a, b) = f()\na.x = 5.0\nprintln(a.x, b.x)", defs), ["5.0 0.0"])


class MisuseTests(unittest.TestCase):
    def test_a_multi_result_call_is_not_a_value(self):
        for body in ("integer :: x\nx = divmod(1, 1)", "println(divmod(1, 1))", "divmod(1, 1)",
                     "println(max(divmod(1, 1), 2))"):
            with self.subTest(body=body):
                with self.assertRaisesRegex(LangError, "'divmod' returns 2 results; unpack them"):
                    run(body)

    def test_a_nested_call_is_not_unpacked_by_the_outer_statement(self):
        defs = DIVMOD + "function twice(integer :: a, integer :: b) -> (integer :: x, integer :: y)\n" \
                        "  return a, b\nend function\n"
        with self.assertRaisesRegex(LangError, "'divmod' returns 2 results; unpack them"):
            run("integer :: x\ninteger :: y\n(x, y) = twice(divmod(1, 1), 2)", defs)

    def test_the_number_of_targets_must_match(self):
        with self.assertRaisesRegex(LangError, "'divmod' returns 2 results, but 3 targets are listed"):
            run("integer :: a\ninteger :: b\ninteger :: c\n(a, b, c) = divmod(1, 1)")

    def test_a_single_result_function_cannot_be_unpacked(self):
        defs = "function one() -> integer\n  return 1\nend function\n"
        with self.assertRaisesRegex(LangError, "'one' returns one result"):
            run("integer :: a\ninteger :: b\n(a, b) = one()", defs)

    def test_the_right_side_must_be_a_call(self):
        with self.assertRaisesRegex(LangError, "call of a function that returns several results"):
            parse("program t\ninteger :: a\ninteger :: b\n(a, b) = 3\nend program\n")

    def test_one_target_is_not_an_unpacking(self):
        with self.assertRaisesRegex(LangError, "two or more targets"):
            parse(DIVMOD + "program t\ninteger :: a\n(a) = divmod(1, 1)\nend program\n")

    def test_a_result_list_needs_two_results(self):
        with self.assertRaisesRegex(LangError, "two or more results"):
            parse("function f() -> (integer :: n)\n  return 1\nend function\nprogram t\nend program\n")

    def test_result_names_cannot_repeat(self):
        with self.assertRaisesRegex(LangError, "result name 'q' is used twice"):
            parse("function f() -> (integer :: q, integer :: q)\n  return 1, 2\nend function\nprogram t\nend program\n")


class ReturnTests(unittest.TestCase):
    def test_return_must_give_every_value(self):
        with self.assertRaisesRegex(LangError, "f returns 2 results, but 3 values are given"):
            parse("function f() -> (integer, integer)\n  return 1, 2, 3\nend function\nprogram t\nend program\n")

    def test_a_single_value_is_an_error_for_a_list(self):
        with self.assertRaisesRegex(LangError, "f returns 2 results: write 'return a, b'"):
            parse("function f() -> (integer, integer)\n  return 1\nend function\nprogram t\nend program\n")

    def test_several_values_need_a_result_list(self):
        with self.assertRaisesRegex(LangError, "only a function declared with a result list"):
            parse("function f() -> integer\n  return 1, 2\nend function\nprogram t\nend program\n")
        with self.assertRaisesRegex(LangError, "only a function declared with a result list"):
            parse("subroutine s()\n  return 1, 2\nend subroutine\nprogram t\nend program\n")

    def test_falling_off_the_end_is_an_error(self):
        defs = "function f() -> (integer, integer)\n  integer :: x = 1\nend function\n"
        with self.assertRaisesRegex(LangError, "function f must return 2 values"):
            run("integer :: a\ninteger :: b\n(a, b) = f()", defs)

    def test_a_plain_return_is_an_error_for_a_list(self):
        defs = "function f() -> (integer, integer)\n  return\nend function\n"
        with self.assertRaisesRegex(LangError, "function f must return 2 values"):
            run("integer :: a\ninteger :: b\n(a, b) = f()", defs)

    def test_names_are_optional(self):
        defs = "function f() -> (integer, real)\n  return 1, 2.5\nend function\n"
        self.assertEqual(run("integer :: a\nreal :: b\n(a, b) = f()\nprintln(a, b)", defs), ["1 2.5"])


class TypesTests(unittest.TestCase):
    def test_an_array_result_and_a_count(self):
        defs = """function evens(integer :: n) -> (integer[] :: values, integer :: count)
  allocatable integer[] :: out
  allocate out[n]
  integer :: i
  do i = 1, n
    out[i] = 2 * i
  end do
  return out, n
end function
"""
        body = "allocatable integer[] :: v\ninteger :: n\n(v, n) = evens(3)\nprintln(v, n)"
        self.assertEqual(run(body, defs), ["[2, 4, 6] 3"])

    def test_an_array_result_must_have_the_declared_rank(self):
        defs = "function f() -> (integer[,] :: m, integer :: n)\n  return [1, 2], 1\nend function\n"
        with self.assertRaisesRegex(LangError, "must return a 2-dimensional"):
            run("allocatable integer[,] :: m\ninteger :: n\n(m, n) = f()", defs)

    def test_optional_results(self):
        defs = "function find(integer :: n) -> (integer? :: where, logical :: found)\n" \
               "  if n > 0 then\n    return n, true\n  end if\n  return null, false\nend function\n"
        body = "integer? :: w\nlogical :: ok\n(w, ok) = find(3)\nprintln(w, ok)\n(w, ok) = find(0)\nprintln(w, ok)"
        self.assertEqual(run(body, defs), ["3 true", "null false"])

    def test_record_and_string_results(self):
        defs = "record pt\n  real :: x\n  real :: y\nend record\n" \
               "function f() -> (pt :: p, string :: s)\n  return pt(1.0, 2.0), \"hi\"\nend function\n"
        self.assertEqual(run("pt :: p\nstring :: s\n(p, s) = f()\nprintln(p.y, s)", defs), ["2.0 hi"])


class MethodsAndInterfacesTests(unittest.TestCase):
    DEFS = """interface sized
  function bounds(self) -> (real :: lo, real :: hi)
end interface

record span is sized
  real :: a = 1.0
  real :: b = 4.0
  function bounds(self) -> (real :: lo, real :: hi)
    return self.a, self.b
  end function
end record
"""

    def test_a_record_function_gives_several_results(self):
        self.assertEqual(run("span :: s\nreal :: lo\nreal :: hi\n(lo, hi) = s.bounds()\nprintln(lo, hi)", self.DEFS),
                         ["1.0 4.0"])

    def test_through_an_interface(self):
        body = "sized :: s = span(2.0, 3.0)\nreal :: lo\nreal :: hi\n(lo, hi) = s.bounds()\nprintln(lo, hi)"
        self.assertEqual(run(body, self.DEFS), ["2.0 3.0"])

    def test_names_are_not_compared_but_types_are(self):
        other = self.DEFS.replace("function bounds(self) -> (real :: lo, real :: hi)\n    return",
                                  "function bounds(self) -> (real :: low, real :: high)\n    return")
        self.assertEqual(run("sized :: s = span()\nprintln(\"ok\")", other), ["ok"])
        wrong = self.DEFS.replace("function bounds(self) -> (real :: lo, real :: hi)\n    return",
                                  "function bounds(self) -> (real :: lo, integer :: hi)\n    return")
        with self.assertRaises(LangError):
            run("sized :: s = span()\nprintln(\"ok\")", wrong)

    def test_a_single_result_does_not_fit_a_list(self):
        wrong = self.DEFS.replace("function bounds(self) -> (real :: lo, real :: hi)\n    return self.a, self.b",
                                  "function bounds(self) -> real\n    return self.a")
        with self.assertRaises(LangError):
            run("sized :: s = span()\nprintln(\"ok\")", wrong)


class CallbackTests(unittest.TestCase):
    def test_a_callback_cannot_return_several_results_yet(self):
        with self.assertRaisesRegex(LangError, "cannot return several results yet"):
            parse("function g(function(real) -> (real, real) :: f) -> real\n  return 1.0\nend function\n"
                  "program t\nend program\n")

    def test_a_multi_result_function_does_not_match_a_callback(self):
        defs = "function two(real :: x) -> (real :: a, real :: b)\n  return x, x\nend function\n" \
               "function g(function(real) -> real :: f) -> real\n  return f(1.0)\nend function\n"
        with self.assertRaises(LangError):
            run("println(g(two))", defs)


class ToolingTests(unittest.TestCase):
    SRC = DIVMOD + """program t
  integer :: q
  integer :: r
  (q, _) = divmod(17, 5)
  (q, r) = divmod(17, 5)
end program
"""

    def test_printing_and_reparsing_is_stable(self):
        once = to_source(parse(self.SRC))
        self.assertIn("-> (integer :: q, integer :: r)", once)
        self.assertIn("return a / b, mod(a, b)", once)
        self.assertIn("(q, _) = divmod(17, 5)", once)
        self.assertEqual(to_source(parse(once)), once)

    def test_a_mark_survives_printing(self):
        src = DIVMOD + "subroutine s(out integer :: x)\n  integer :: y\n  (@x, y) = divmod(1, 1)\nend subroutine\nprogram t\nend program\n"
        self.assertIn("(@x, y) = divmod(1, 1)", to_source(parse(src)))

    def test_check_reports_undeclared_names_in_an_unpacking(self):
        errors = check_errors(DIVMOD + "program t\n  integer :: q\n  (q, nope) = divmod(1, 1)\nend program\n")
        self.assertTrue(any("nope" in e[2] for e in errors), errors)

    def test_check_reports_an_undefined_function(self):
        errors = check_errors("program t\n  integer :: q\n  integer :: r\n  (q, r) = missing(1)\nend program\n")
        self.assertTrue(any("missing" in e[2] for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
