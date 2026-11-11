import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

CIRCLE = """record circle
  string :: label = "circle"
  real :: r = 1.0

  function area(self) -> real
    return 3.0 * self.r ** 2
  end function

  function twice(self) -> real
    return 2.0 * self.area()
  end function

  subroutine grow(inout self, real :: factor)
    update self.r = self.r * factor
  end subroutine

  subroutine show(self)
    println(self.label, self.r)
  end subroutine
end record
"""


def program(body, defs=CIRCLE):
    return defs + "\nprogram t\n" + body + "\nend program\n"


def run(body, defs=CIRCLE):
    return run_bor(program(body, defs))


class MethodTests(unittest.TestCase):
    def test_a_function_in_a_record_is_called_with_a_dot(self):
        self.assertEqual(run("  circle :: c = circle(r = 2.0)\n  println(c.area(), c.twice())"), ["12.0 24.0"])

    def test_a_subroutine_changes_the_record_it_is_called_on(self):
        self.assertEqual(run("  circle :: c = circle(r = 2.0)\n  call c.grow(3.0)\n  println(c.r)"), ["6.0"])

    def test_a_subroutine_with_only_in_self(self):
        self.assertEqual(run("  circle :: c\n  call c.show()"), ["circle 1.0"])

    def test_array_elements_and_fields_can_be_receivers(self):
        body = ("  circle[2] :: all\n  all[2].r = 2.0\n  call all[2].grow(2.0)\n  println(all[2].r, all[2].area())\n"
                "  record_holder :: h\n  h.c.r = 3.0\n  call h.c.grow(2.0)\n  println(h.c.r)")
        defs = CIRCLE + "\nrecord record_holder\n  circle :: c\nend record\n"
        self.assertEqual(run(body, defs), ["4.0 48.0", "6.0"])

    def test_the_receiver_of_a_function_is_copied_not_shared(self):
        self.assertEqual(run("  circle :: c\n  real :: a\n  a = c.area()\n  c.r = 5.0\n  println(a)"), ["3.0"])

    def test_a_function_result_can_be_the_receiver(self):
        defs = CIRCLE + "\nfunction make(real :: r) -> circle\n  return circle(r = r)\nend function\n"
        self.assertEqual(run("  println(make(2.0).area())", defs), ["12.0"])

    def test_two_records_may_use_the_same_function_name(self):
        defs = CIRCLE + "\nrecord square\n  real :: s = 2.0\n  function area(self) -> real\n    return self.s ** 2\n  end function\nend record\n"
        self.assertEqual(run("  circle :: c\n  square :: q\n  println(c.area(), q.area())", defs), ["3.0 4.0"])

    def test_a_function_with_a_procedure_parameter(self):
        defs = """function dbl(real :: x) -> real
  return 2.0 * x
end function

record box
  real :: v = 3.0
  function apply(self, function(real) -> real :: f) -> real
    return f(self.v)
  end function
end record
"""
        self.assertEqual(run("  box :: b\n  println(b.apply(dbl))", defs), ["6.0"])

    def test_functions_in_a_module_record_travel_with_the_record(self):
        src = ("module geometry\n  public record shape\n    real :: w = 2.0\n    function area(self) -> real\n"
               "      return self.w * self.w\n    end function\n  end record\nend module\n\n"
               "program t\n  use geometry: shape\n  shape :: s\n  println(s.area())\nend program\n")
        self.assertEqual(run_bor(src), ["4.0"])


class MethodErrorTests(unittest.TestCase):
    def fails(self, body, text, defs=CIRCLE):
        with self.assertRaises(LangError) as cm:
            run(body, defs)
        self.assertIn(text, str(cm.exception))

    def test_a_function_must_take_self_first(self):
        with self.assertRaises(LangError) as cm:
            parse("record r\n  real :: x\n  function f(real :: y) -> real\n    return y\n  end function\nend record\nprogram t\nend program\n")
        self.assertIn("takes 'self' first", str(cm.exception))

    def test_names_must_be_unique(self):
        for body in ("  real :: area\n  function area(self) -> real\n    return 1.0\n  end function\n",
                     "  function f(self) -> real\n    return 1.0\n  end function\n  function f(self) -> real\n    return 2.0\n  end function\n",
                     "  function f(self) -> real\n    return 1.0\n  end function\n  real :: f\n"):
            with self.subTest(body):
                with self.assertRaises(LangError):
                    parse("record r\n" + body + "end record\nprogram t\nend program\n")

    def test_a_record_without_that_function(self):
        self.fails("  circle :: c\n  println(c.perimeter())", "no field or function 'perimeter'")
        self.fails("  circle :: c\n  call c.perimeter()", "has no function 'perimeter'")

    def test_a_subroutine_is_called_with_call_and_a_function_in_an_expression(self):
        self.fails("  circle :: c\n  println(c.grow(2.0))", "is a subroutine")
        self.fails("  circle :: c\n  call c.area()", "is a function")

    def test_changing_an_in_or_const_record_is_an_error(self):
        self.fails("  const circle :: c = circle(r = 2.0)\n  call c.grow(2.0)", "cannot be passed as inout")
        defs = CIRCLE + "\nsubroutine f(circle :: c)\n  call c.grow(2.0)\nend subroutine\n"
        self.fails("  circle :: c\n  call f(c)", "cannot be passed as inout", defs)

    def test_wrong_argument_count(self):
        self.fails("  circle :: c\n  println(c.area(1.0))", "expects 1 arguments")

    def test_a_non_record_has_no_functions(self):
        self.fails("  real :: x = 1.0\n  call x.grow(2.0)", "has no function 'grow'")

    def test_old_array_indexing_still_gets_its_own_message(self):
        defs = "record r\n  integer[3] :: v\nend record\n"
        self.fails("  r :: a\n  println(a.v(2))", "index with brackets", defs)


class MethodCheckAndPrintTests(unittest.TestCase):
    def test_check_finds_undeclared_names_inside_a_record_function(self):
        src = program("  circle :: c\n  println(c.area())",
                      "record circle\n  real :: r = 1.0\n  function area(self) -> real\n    return k * self.r\n  end function\nend record\n")
        self.assertEqual([e[2] for e in check_errors(src)], ["'k' is not declared"])

    def test_check_accepts_calls_through_records(self):
        self.assertEqual(check_errors(program("  circle :: c\n  call c.grow(2.0)\n  println(c.area())")), [])

    def test_prints_back_the_same(self):
        src = program("  circle :: c\n  call c.grow(2.0)\n  println(c.area())")
        text = to_source(parse(src), [])
        self.assertIn("function area(self) -> real", text)
        self.assertIn("subroutine grow(inout self, real :: factor)", text)
        self.assertIn("call c.grow(2.0)", text)
        self.assertEqual(to_source(parse(text), []), text)
        self.assertEqual(run_bor(text), run_bor(src))


if __name__ == "__main__":
    unittest.main()
