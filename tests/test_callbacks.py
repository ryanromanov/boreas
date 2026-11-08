import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import ProcType, parse
from boreas.printer import to_source

LIB = """function integrate(function(real :: x) -> real :: f, real :: a) -> real
  return f(a) + f(a + 1.0)
end function

function square(real :: x) -> real
  return x * x
end function

function cube(integer :: n) -> real
  return 1.0
end function

subroutine bump(string :: label, inout integer :: n)
  update n = n + 1
  println(label, n)
end subroutine

subroutine twice(subroutine(string, inout integer) :: cb, inout integer :: n)
  call cb("a", n)
  call cb("b", n)
end subroutine

"""


def run(main):
    return run_bor(LIB + "program p\n" + main + "end program\n")


def error(main, lib=LIB):
    with_lib = lib + "program p\n" + main + "end program\n"
    try:
        run_bor(with_lib)
    except LangError as e:
        return str(e)
    raise AssertionError("expected an error")


class PassingTests(unittest.TestCase):
    def test_a_function_is_passed_and_called(self):
        self.assertEqual(run("  println(integrate(square, 2.0))\n"), ["13.0"])

    def test_a_subroutine_with_an_inout_argument(self):
        self.assertEqual(run("  integer :: n = 0\n  call twice(bump, n)\n  println(n)\n"), ["a 1", "b 2", "2"])

    def test_a_procedure_parameter_can_be_passed_on(self):
        text = LIB + ("function outer(function(real) -> real :: g) -> real\n  return integrate(g, 1.0)\nend function\n"
                      "program p\n  println(outer(square))\nend program\n")
        self.assertEqual(run_bor(text), ["5.0"])

    def test_names_in_the_signature_are_optional(self):
        text = ("function go(function(real, real) -> real :: f) -> real\n  return f(3.0, 4.0)\nend function\n"
                "function add(real :: a, real :: b) -> real\n  return a + b\nend function\n"
                "program p\n  println(go(add))\nend program\n")
        self.assertEqual(run_bor(text), ["7.0"])

    def test_a_module_procedure_keeps_its_module_constants(self):
        text = ("module m\n  const real :: scale = 10.0\n  public function scaled(real :: x) -> real\n"
                "    return x * scale\n  end function\nend module\n"
                "function apply(function(real) -> real :: f) -> real\n  return f(2.0)\nend function\n"
                "program p\n  use m: scaled\n  println(apply(scaled))\nend program\n")
        self.assertEqual(run_bor(text), ["20.0"])


class MismatchTests(unittest.TestCase):
    def test_wrong_parameter_type(self):
        self.assertIn("'cube' is function(integer :: n) -> real", error("  println(integrate(cube, 1.0))\n"))

    def test_function_where_a_subroutine_is_wanted(self):
        self.assertIn("is subroutine(", error("  integer :: n = 0\n  call twice(square, n)\n"))

    def test_wrong_intent(self):
        lib = LIB + "subroutine plain(string :: s, integer :: n)\n  println(s)\nend subroutine\n"
        self.assertIn("argument 'cb' of twice", error("  integer :: n = 0\n  call twice(plain, n)\n", lib))

    def test_wrong_return_type(self):
        lib = LIB + "function whole(real :: x) -> integer\n  return 1\nend function\n"
        self.assertIn("-> integer", error("  println(integrate(whole, 1.0))\n", lib))


class NameTests(unittest.TestCase):
    def test_a_variable_with_the_name_wins(self):
        self.assertIn("but 'square' is a variable",
                      error("  real :: square = 1.0\n  println(integrate(square, 1.0))\n"))

    def test_a_built_in_must_be_wrapped(self):
        self.assertIn("'sin' is a built-in", error("  println(integrate(sin, 1.0))\n"))

    def test_something_that_is_not_a_name(self):
        self.assertIn("must be the name of a procedure", error("  println(integrate(1.0 + 2.0, 1.0))\n"))

    def test_an_unknown_name(self):
        self.assertIn("'nothing' is not a procedure", error("  println(integrate(nothing, 1.0))\n"))

    def test_a_procedure_parameter_is_not_a_value(self):
        lib = "function f(function(real) -> real :: g) -> real\n  return g\nend function\n" + LIB
        self.assertIn("is a procedure parameter", error("  println(f(square))\n", lib))


class ParseTests(unittest.TestCase):
    def bad(self, header):
        with self.assertRaises(LangError) as c:
            parse(header + "\nend subroutine\nprogram p\nend program\n")
        return str(c.exception)

    def test_not_out_or_inout(self):
        self.assertIn("cannot be out or inout", self.bad("subroutine s(out function(real) -> real :: f)"))

    def test_a_function_signature_needs_its_result(self):
        self.assertIn("needs its result type", self.bad("subroutine s(function(real) :: f)"))

    def test_no_optional_or_nested_procedures(self):
        self.assertIn("optional", self.bad("subroutine s(function(real?) -> real :: f)"))
        self.assertIn("cannot take a procedure", self.bad("subroutine s(function(function(real) -> real) -> real :: f)"))

    def test_only_a_subroutine_signature_has_inout(self):
        self.assertIn("only a subroutine signature", self.bad("subroutine s(function(inout real) -> real :: f)"))

    def test_a_procedure_variable_is_not_supported(self):
        with self.assertRaises(LangError):
            parse("program p\n  function(real) -> real :: g\nend program\n")

    def test_the_signature_compares_without_names(self):
        a = parse("subroutine s(function(real :: x) -> real :: f)\nend subroutine\nprogram p\nend program\n")
        b = parse("subroutine s(function(real) -> real :: f)\nend subroutine\nprogram p\nend program\n")
        self.assertEqual(a.functions["s"].params[0][0], b.functions["s"].params[0][0])
        self.assertIsInstance(a.functions["s"].params[0][0], ProcType)

    def test_printing_round_trips(self):
        src = LIB + "program p\nend program\n"
        out = to_source(parse(src))
        self.assertEqual(to_source(parse(out)), out)
        self.assertIn("function integrate(function(real :: x) -> real :: f, real :: a) -> real", out)


class CheckTests(unittest.TestCase):
    def test_passing_a_procedure_name_is_not_an_undeclared_name(self):
        self.assertEqual(check_errors(LIB + "program p\n  println(integrate(square, 1.0))\nend program\n"), [])

    def test_a_call_through_a_parameter_is_known(self):
        self.assertEqual(check_errors(LIB + "program p\nend program\n"), [])

    def test_a_real_undeclared_name_is_still_reported(self):
        errors = check_errors(LIB + "program p\n  println(integrate(square, missing))\nend program\n")
        self.assertTrue(any("'missing' is not declared" in e[2] for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
