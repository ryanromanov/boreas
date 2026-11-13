import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

DEFS = """interface figure
  function name(self) -> string
  function area(self) -> real
end interface

record circle is figure
  string :: label = "circle"
  real :: r = 1.0

  function name(self) -> string
    return self.label
  end function

  function area(self) -> real
    return 3.0 * self.r ** 2
  end function

  subroutine grow(inout self, real :: factor)
    update self.r = self.r * factor
  end subroutine
end record

record rect
  string :: label = "rect"
  real :: w = 1.0
  real :: h = 1.0

  function name(self) -> string
    return self.label
  end function

  function area(self) -> real
    return self.w * self.h
  end function
end record

subroutine describe(figure :: f)
  println(f.name(), f.area())
end subroutine
"""


def program(body, defs=DEFS):
    return defs + "\nprogram t\n" + body + "\nend program\n"


def run(body, defs=DEFS):
    return run_bor(program(body, defs))


class InterfaceTests(unittest.TestCase):
    def test_the_acceptance_program_matches_the_fortran_one(self):
        # the same program in Fortran prints 12.566, 12.000, 3.142 and a total of 27.708 (checked with gfortran)
        body = """  figure[3] :: all
  real :: total = 0.0
  integer :: i
  all[1] = circle(label = "circle", r = 2.0)
  all[2] = rect(label = "rect", w = 3.0, h = 4.0)
  all[3] = circle(label = "unit")
  do i = 1, size(all)
    call describe(all[i])
    total = total + all[i].area()
  end do
  println("total =", fixed(total, 3))"""
        self.assertEqual(run(body), ["circle 12.0", "rect 12.0", "unit 3.0", "total = 27.000"])

    def test_dispatch_picks_the_function_of_the_value(self):
        self.assertEqual(run("  figure :: f = circle(r = 2.0)\n  println(f.area())\n  f = rect(w = 2.0, h = 5.0)\n  println(f.area())"),
                         ["12.0", "10.0"])

    def test_a_record_that_never_says_is_still_fits_by_its_functions(self):
        self.assertEqual(run("  call describe(rect(w = 2.0, h = 3.0))"), ["rect 6.0"])

    def test_a_parameter_takes_any_record_that_fits(self):
        self.assertEqual(run("  call describe(circle())\n  call describe(rect())"), ["circle 3.0", "rect 1.0"])

    def test_an_interface_value_is_a_copy(self):
        body = "  circle :: c = circle(r = 1.0)\n  figure :: f = c\n  c.r = 5.0\n  println(f.area(), c.area())"
        self.assertEqual(run(body), ["3.0 75.0"])

    def test_a_function_can_return_an_interface(self):
        defs = DEFS + "\nfunction pick(logical :: round) -> figure\n  if round then\n    return circle()\n  end if\n  return rect(w = 2.0)\nend function\n"
        self.assertEqual(run("  println(pick(true).area(), pick(false).area())", defs), ["3.0 2.0"])

    def test_an_interface_can_list_a_subroutine_that_changes_the_value(self):
        defs = DEFS.replace("  function area(self) -> real\nend interface", "  function area(self) -> real\n  subroutine grow(inout self, real :: factor)\nend interface", 1)
        self.assertEqual(run("  figure :: f = circle(r = 1.0)\n  call f.grow(2.0)\n  println(f.area())", defs), ["12.0"])

    def test_interfaces_can_be_imported_from_a_module(self):
        src = ("module shapes\n  public interface figure\n    function area(self) -> real\n  end interface\n"
               "  public record sq is figure\n    real :: s = 2.0\n    function area(self) -> real\n      return self.s ** 2\n    end function\n  end record\nend module\n\n"
               "program t\n  use shapes: figure, sq\n  figure :: f = sq()\n  println(f.area())\nend program\n")
        self.assertEqual(run_bor(src), ["4.0"])

    def test_a_record_can_satisfy_several_interfaces(self):
        defs = DEFS + "\ninterface named\n  function name(self) -> string\nend interface\n"
        defs = defs.replace("record circle is figure", "record circle is figure, named", 1)
        self.assertEqual(run("  named :: n = circle()\n  println(n.name())", defs), ["circle"])


class InterfaceErrorTests(unittest.TestCase):
    def fails(self, body, text, defs=DEFS):
        with self.assertRaises(LangError) as cm:
            run(body, defs)
        self.assertIn(text, str(cm.exception))

    def test_a_function_the_interface_does_not_list_is_not_available(self):
        self.fails("  figure :: f = circle()\n  call f.grow(2.0)", "'grow' is not part of figure")
        self.fails("  figure :: f = circle()\n  println(f.r)", "not available through figure")
        self.fails("  figure :: f = circle()\n  f.r = 2.0", "not available through figure")

    def test_a_record_that_does_not_fit(self):
        defs = DEFS + "\nrecord blob\n  real :: x\nend record\n"
        self.fails("  figure :: f = blob(x = 1.0)", "blob cannot be used as figure: it has no function 'name'", defs)
        self.fails("  figure :: f = 3.0", "cannot assign real to figure")

    def test_a_function_with_the_wrong_signature(self):
        defs = DEFS + "\nrecord odd\n  function name(self) -> string\n    return \"odd\"\n  end function\n  function area(self) -> integer\n    return 1\n  end function\nend record\n"
        self.fails("  figure :: f = odd()", "its 'area' is function area() -> integer, but figure needs function area() -> real", defs)

    def test_is_is_checked_when_written(self):
        defs = DEFS.replace("record rect\n", "record rect is figure, figure2\n", 1)
        with self.assertRaises(LangError) as cm:
            run("  println(1)", defs)
        self.assertIn("'figure2'", str(cm.exception))
        defs = DEFS.replace("  function area(self) -> real\n    return self.w * self.h\n  end function\n", "", 1).replace("record rect\n", "record rect is figure\n", 1)
        with self.assertRaises(LangError) as cm:
            run("  println(1)", defs)
        self.assertIn("record 'rect' says it is figure, but it has no function 'area'", str(cm.exception))

    def test_an_element_nobody_assigned(self):
        self.fails("  figure[2] :: all\n  println(all[1].area())", "used before it is assigned")

    def test_names_and_places_an_interface_cannot_be_used(self):
        with self.assertRaises(LangError) as cm:
            run("  println(1)", "interface shape\n  function area(self) -> real\nend interface\n")
        self.assertIn("built-in function", str(cm.exception))
        run("  println(1)", DEFS + "\nrecord holder\n  figure :: f\nend record\n")     # a field may have an interface type
        self.fails("  figure? :: f", "cannot be optional")
        self.fails("  allocatable figure[] :: f", "not supported")

    def test_an_interface_lists_only_signatures(self):
        with self.assertRaises(LangError):
            parse("interface f\n  real :: x\nend interface\nprogram t\nend program\n")
        with self.assertRaises(LangError):
            parse("interface f\n  function area(real :: x) -> real\nend interface\nprogram t\nend program\n")


PICK = "\nfunction pick() -> figure\n  return circle()\nend function\n"


class InterfaceEdgeTests(unittest.TestCase):
    def fails(self, body, text, defs=DEFS):
        with self.assertRaises(LangError) as cm:
            run(body, defs)
        self.assertIn(text, str(cm.exception))

    def test_a_function_result_of_an_interface_type_has_only_the_listed_functions(self):
        self.fails("  println(pick().r)", "'r' is not available through figure", DEFS + PICK)
        self.fails("  println(pick().grow(2.0))", "'grow' is not part of figure", DEFS + PICK)
        self.assertEqual(run("  println(pick().area())", DEFS + PICK), ["3.0"])

    def test_an_out_parameter_of_an_interface_type_starts_unassigned(self):
        defs = DEFS + "\nsubroutine make(out figure :: g)\n  update g = rect(w = 2.0, h = 2.0)\nend subroutine\n"
        self.assertEqual(run("  figure :: f\n  call make(f)\n  println(f.area())", defs), ["4.0"])
        defs = DEFS + "\nsubroutine lazy(out figure :: g)\n  println(1)\nend subroutine\n"
        self.fails("  figure :: f\n  call lazy(f)", "was never assigned", defs)

    def test_an_inout_parameter_can_replace_the_value(self):
        defs = DEFS + "\nsubroutine swap_in(inout figure :: g)\n  update g = rect(w = 2.0, h = 5.0)\nend subroutine\n"
        self.assertEqual(run("  figure :: f = circle()\n  call swap_in(f)\n  println(f.area())", defs), ["10.0"])

    def test_a_record_variable_can_be_passed_as_inout_to_an_interface_parameter(self):
        # like Fortran's class(figure), intent(inout) given a type(circle): the routine may change the circle
        defs = DEFS.replace("  function area(self) -> real\nend interface", "  function area(self) -> real\n  subroutine grow(inout self, real :: factor)\nend interface", 1)
        defs += "\nsubroutine double_it(inout figure :: g)\n  call g.grow(2.0)\nend subroutine\n"
        self.assertEqual(run("  circle :: c = circle(r = 1.0)\n  call double_it(c)\n  println(c.r)", defs), ["2.0"])

    def test_the_routine_may_replace_the_value_with_one_of_the_same_kind(self):
        defs = DEFS + "\nsubroutine reset(inout figure :: g)\n  update g = circle(r = 9.0)\nend subroutine\n"
        self.assertEqual(run("  circle :: c\n  call reset(c)\n  println(c.r)", defs), ["9.0"])

    def test_the_routine_may_not_change_what_kind_of_record_it_is(self):
        defs = DEFS + "\nsubroutine swap_in(inout figure :: g)\n  update g = rect()\nend subroutine\n"
        self.fails("  circle :: c\n  call swap_in(c)", "put a rect in 'g'", defs)
        defs = DEFS + "\nsubroutine make(out figure :: g)\n  update g = rect()\nend subroutine\n"
        self.fails("  circle :: c\n  call make(c)", "can change the value, not what kind of record it is", defs)

    def test_a_record_that_does_not_fit_is_still_refused(self):
        defs = DEFS + "\nrecord blob\n  real :: x\nend record\nsubroutine f(inout figure :: g)\n  println(1)\nend subroutine\n"
        self.fails("  blob :: b\n  call f(b)", "'b' is a blob, which cannot be used as figure", defs)

    def test_the_fit_check_survives_freed_objects(self):
        import gc
        bad = DEFS.replace("record rect\n", "record rect is figure\n", 1).replace(
            "  function area(self) -> real\n    return self.w * self.h\n  end function\n", "", 1)
        good = DEFS.replace("record rect\n", "record rect is figure\n", 1)
        for _ in range(40):                     # a language server parses the same text again and again
            self.assertEqual(len(check_errors(program("  println(1)", bad))), 1)
            gc.collect()
            self.assertEqual(check_errors(program("  println(1)", good)), [])
            gc.collect()


class InterfaceCheckAndPrintTests(unittest.TestCase):
    def test_check_is_clean_for_a_valid_program(self):
        self.assertEqual(check_errors(program("  figure[2] :: all\n  all[1] = circle()\n  println(all[1].area())")), [])

    def test_check_reports_a_record_that_says_it_is_but_is_not(self):
        defs = DEFS.replace("record rect\n", "record rect is figure\n", 1).replace(
            "  function area(self) -> real\n    return self.w * self.h\n  end function\n", "", 1)
        errors = check_errors(program("  println(1)", defs))
        self.assertEqual(len(errors), 1)
        self.assertIn("record 'rect' says it is figure", errors[0][2])

    def test_prints_back_the_same(self):
        src = program("  figure[2] :: all\n  all[1] = circle()\n  call describe(all[1])")
        text = to_source(parse(src), [])
        self.assertIn("interface figure", text)
        self.assertIn("record circle is figure", text)
        self.assertIn("  function name(self) -> string", text)
        self.assertEqual(to_source(parse(text), []), text)
        self.assertEqual(run_bor(text), run_bor(src))


if __name__ == "__main__":
    unittest.main()
