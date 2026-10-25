import os, sys, subprocess, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, check_syntax, fixture, read, run_bor, run_gfortran, same_output, todos
from boreas.fortran import convert
from boreas.parser import parse
from boreas.printer import to_source


def conv(src):
    text, findings = convert(src)
    again = to_source(parse(text))                # the output is well-formed Boreas
    assert to_source(parse(again)) == again       # and printing it is stable
    return text, findings


def messages(findings):
    return " | ".join(f[2] for f in findings)


POINT = ("  type :: point\n    real :: x\n    real :: y\n  end type point\n")


class FixtureTests(unittest.TestCase):
    def check(self, name):
        path = fixture(name)
        if GFORTRAN:
            check_syntax(path)
        text, findings = convert(read(path), name)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = run_gfortran(path)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_types_in_a_module_used_from_a_program(self):
        text = self.check("records.f90")
        self.assertIn("public record point", text)
        self.assertIn("use shapes: point, segment, length, shift, make", text)
        self.assertIn("update p.x = p.x + dx", text)

    def test_type_valued_functions_and_arrays_of_records_as_arguments(self):
        text = self.check("records3.f90")
        self.assertIn("public function add(v2 :: a, v2 :: b) -> v2", text)
        self.assertIn("update vs(i).x = vs(i).x * k", text)

    def test_types_in_a_program_are_hoisted_and_keep_their_defaults(self):
        text = self.check("records2.f90")
        self.assertTrue(text.index("record item") < text.index("program inventory"))
        self.assertIn("integer :: counts(3) = 0", text)          # the parameter n was folded into the size
        self.assertIn("one = item(7)", text)
        self.assertIn("subroutine fill(out item :: it)", text)    # every field has a default: still out


class TypeTests(unittest.TestCase):
    def test_defaults_become_field_defaults(self):
        src = ("program p\n  implicit none\n  type :: cfg\n    integer :: n = 5\n    real :: s = 2.0 * 3.0\n"
               "    logical :: v = .true.\n    integer :: k(2) = (/ 1, 2 /)\n  end type cfg\n  type(cfg) :: c\n"
               "  print *, c%n, c%v, c%k\nend program\n")
        text, findings = conv(src)
        self.assertIn("integer :: n = 5", text)
        self.assertIn("integer :: k(2) = [1, 2]", text)
        self.assertEqual(run_bor(text), ["5 true [1, 2]"])
        self.assertEqual(todos(findings), [])

    def test_module_parameters_stay_names_but_program_parameters_are_inlined(self):
        src = ("module m\n  implicit none\n  integer, parameter :: base = 10\n  type :: t\n    integer :: a = base\n"
               "  end type t\nend module m\n"
               "program p\n  use m\n  implicit none\n  integer, parameter :: seed = 3\n"
               "  type :: u\n    integer :: b = seed + 1\n  end type u\n  type(t) :: x\n  type(u) :: y\n"
               "  print *, x%a, y%b\nend program\n")
        text, _ = conv(src)
        self.assertIn("integer :: a = base", text)               # inside a module, the constant exists
        self.assertIn("integer :: b = 3 + 1", text)               # a program's parameter is inlined
        self.assertEqual(run_bor(text), ["10 4"])

    def test_default_that_boreas_cannot_evaluate_is_dropped_with_a_todo(self):
        src = ("program p\n  implicit none\n  integer :: v\n  type :: t\n    real :: a = 1.0\n    real :: b = besj0(3.0)\n"
               "  end type t\n  type(t) :: x\n  print *, x%a\nend program\n")
        text, findings = conv(src)
        self.assertIn("default of component 'b' dropped", messages(todos(findings)))
        self.assertIn("real :: b\n", text)
        self.assertEqual(run_bor(text), ["1.0"])

    def test_keyword_constructors_and_trailing_defaults(self):
        src = ("program p\n  implicit none\n  type :: t\n    integer :: a\n    integer :: b = 2\n    integer :: c = 3\n"
               "  end type t\n  type(t) :: x, y\n  x = t(a=1, c=9)\n  y = t(5)\n  print *, x%a, x%b, x%c, y%a, y%b, y%c\n"
               "end program\n")
        text, findings = conv(src)
        self.assertIn("x = t(a = 1, c = 9)", text)
        self.assertEqual(run_bor(text), ["1 2 9 5 2 3"])

    def test_component_names_that_are_boreas_keywords_are_renamed_everywhere(self):
        src = ("program p\n  implicit none\n  type :: t\n    integer :: in\n    integer :: out\n  end type t\n"
               "  type(t) :: x\n  x = t(in=1, out=2)\n  x%in = x%in + x%out\n  print *, x%in, x%out\nend program\n")
        text, _ = conv(src)
        self.assertEqual(run_bor(text), ["3 2"])

    def test_component_of_a_function_result_and_array_elements(self):
        src = ("module m\n  implicit none\n" + POINT + "contains\n  function mk(a)\n    real, intent(in) :: a\n"
               "    type(point) :: mk\n    mk = point(a, a * 2.0)\n  end function mk\nend module m\n"
               "program p\n  use m\n  implicit none\n  type(point) :: ps(2)\n  integer :: i\n"
               "  do i = 1, 2\n    ps(i) = mk(real(i))\n  end do\n  print *, ps(2)%y, mk(3.0)%y\nend program\n")
        text, findings = conv(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["4.0 6.0"])

    def test_array_component_indexing_and_nested_components(self):
        src = ("program p\n  implicit none\n" + POINT + "  type :: line\n    type(point) :: ends(2)\n    integer :: w(3)\n"
               "  end type line\n  type(line) :: l\n  l%ends(2)%y = 4.0\n  l%w(2) = 5\n  print *, l%ends(2)%y, l%w\nend program\n")
        text, findings = conv(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["4.0 [0, 5, 0]"])

    def test_private_components_sequence_and_bind_are_notes(self):
        src = ("module m\n  implicit none\n  type, bind(c) :: t\n    sequence\n    private\n    integer :: a\n  end type t\n"
               "end module m\nprogram p\n  use m\n  implicit none\n  type(t) :: x\n  x%a = 1\n  print *, x%a\nend program\n")
        text, findings = conv(src)
        self.assertEqual(todos(findings), [])
        self.assertIn("bind(c) ignored", messages(findings))
        self.assertEqual(run_bor(text), ["1"])


class UnsupportedTests(unittest.TestCase):
    def check_todo(self, typedef, reason):
        src = ("program p\n  implicit none\n  integer :: total\n" + typedef + "  type(thing) :: x\n  total = 3\n"
               "  x%v = 1\n  print *, total\nend program\n")
        text, findings = conv(src)
        self.assertIn(reason, messages(todos(findings)))
        self.assertIn("derived type 'thing' was not translated", messages(todos(findings)))
        self.assertEqual(run_bor(text), ["3"])         # the rest still converts and runs
        return text

    def test_type_bound_procedures(self):
        self.check_todo("  type :: thing\n    integer :: v\n  contains\n    procedure :: show\n  end type thing\n",
                        "type-bound procedures")

    def test_extends(self):
        self.check_todo("  type :: base\n    integer :: w\n  end type base\n  type, extends(base) :: thing\n"
                        "    integer :: v\n  end type thing\n", "'extends' (inheritance)")

    def test_allocatable_and_pointer_components(self):
        self.check_todo("  type :: thing\n    integer :: v\n    real, allocatable :: a(:)\n  end type thing\n",
                        "allocatable component")
        self.check_todo("  type :: thing\n    integer :: v\n    integer, pointer :: q\n  end type thing\n", "pointer")

    def test_array_size_that_is_not_a_constant(self):
        src = ("program p\n  implicit none\n  integer :: m\n  type :: thing\n    integer :: v(m)\n  end type thing\n"
               "  print *, 1\nend program\n")
        _, findings = conv(src)
        self.assertIn("array size that is not an integer constant", messages(todos(findings)))

    def test_component_of_an_array_section(self):
        src = ("program p\n  implicit none\n" + POINT + "  type(point) :: ps(3)\n  real :: s\n  ps%x = 1.0\n"
               "  s = sum(ps(1:2)%x)\n  print *, 1\nend program\n")
        text, findings = conv(src)
        self.assertIn("a component of a whole array (ps%...)", messages(todos(findings)))
        self.assertIn("a component of an array section", messages(todos(findings)))
        self.assertEqual(run_bor(text), ["1"])

    def test_type_from_another_file(self):
        src = "program p\n  implicit none\n  type(far_away) :: x\n  print *, 1\nend program\n"
        text, findings = conv(src)
        self.assertIn("derived type 'far_away' is not defined earlier in this file", messages(todos(findings)))
        self.assertEqual(run_bor(text), ["1"])


class IntentTests(unittest.TestCase):
    def test_intent_out_record_that_is_not_fully_assigned_becomes_inout(self):
        src = ("program p\n  implicit none\n" + POINT + "  type(point) :: a\n  a%y = 7.0\n  call setx(a)\n"
               "  print *, a%x, a%y\ncontains\n  subroutine setx(q)\n    type(point), intent(out) :: q\n    q%x = 1.0\n"
               "  end subroutine setx\nend program\n")
        text, findings = conv(src)
        self.assertIn("subroutine setx(inout point :: q)", text)
        self.assertIn("is not fully assigned; made inout", messages(findings))
        self.assertEqual(run_bor(text), ["1.0 7.0"])

    def test_intent_out_record_that_is_fully_assigned_stays_out(self):
        src = ("program p\n  implicit none\n" + POINT + "  type(point) :: a\n  call setxy(a)\n  print *, a%x, a%y\n"
               "contains\n  subroutine setxy(q)\n    type(point), intent(out) :: q\n    q%x = 1.0\n    q%y = 2.0\n"
               "  end subroutine setxy\nend program\n")
        text, _ = conv(src)
        self.assertIn("subroutine setxy(out point :: q)", text)
        self.assertEqual(run_bor(text), ["1.0 2.0"])

    def test_whole_assignment_counts_as_assigned(self):
        src = ("program p\n  implicit none\n" + POINT + "  type(point) :: a\n  call reset(a)\n  print *, a%x\n"
               "contains\n  subroutine reset(q)\n    type(point), intent(out) :: q\n    q = point(4.0, 5.0)\n"
               "  end subroutine reset\nend program\n")
        text, _ = conv(src)
        self.assertIn("subroutine reset(out point :: q)", text)

    def test_field_writes_make_an_unspecified_intent_inout(self):
        src = ("program p\n  implicit none\n" + POINT + "  type(point) :: a\n  call setx(a)\n  print *, a%x\n"
               "contains\n  subroutine setx(q)\n    type(point) :: q\n    q%x = 1.0\n  end subroutine setx\nend program\n")
        text, findings = conv(src)
        self.assertIn("subroutine setx(inout point :: q)", text)
        self.assertIn("update q.x = 1.0", text)
        self.assertEqual(run_bor(text), ["1.0"])

    def test_read_only_use_of_a_record_is_in(self):
        src = ("program p\n  implicit none\n" + POINT + "  type(point) :: a\n  a = point(1.0, 2.0)\n  print *, total(a)\n"
               "contains\n  function total(q)\n    type(point) :: q\n    real :: total\n    total = q%x + q%y\n"
               "  end function total\nend program\n")
        text, _ = conv(src)
        self.assertIn("function total(point :: q) -> real", text)
        self.assertEqual(run_bor(text), ["3.0"])


class ImportTests(unittest.TestCase):
    MOD = ("module geo\n  implicit none\n  private\n  public :: point, origin\n" + POINT +
           "  type :: hidden\n    integer :: n\n  end type hidden\n"
           "  type(point), parameter :: origin = point(0.0, 0.0)\nend module geo\n")

    def test_only_lists_and_private_types(self):
        text, findings = conv(self.MOD + "program p\n  use geo, only: point, origin\n  implicit none\n  type(point) :: a\n"
                              "  a = origin\n  print *, a%x\nend program\n")
        self.assertIn("use geo: point, origin", text)
        self.assertIn("record hidden", text)
        self.assertNotIn("public record hidden", text)
        self.assertIn("public record point", text)
        self.assertEqual(run_bor(text), ["0.0"])

    def test_a_private_type_cannot_be_imported(self):
        text, findings = conv(self.MOD + "program p\n  use geo, only: hidden\n  implicit none\n  print *, 1\nend program\n")
        self.assertIn("has no public 'hidden'", messages(todos(findings)))

    def test_types_pass_through_a_reexporting_module(self):
        src = ("module a\n  implicit none\n" + POINT + "end module a\n"
               "module b\n  use a\n  implicit none\nend module b\n"
               "program p\n  use b\n  implicit none\n  type(point) :: q\n  q%x = 2.0\n  print *, q%x\nend program\n")
        text, _ = conv(src)
        self.assertIn("use a: point", text)
        self.assertEqual(run_bor(text), ["2.0"])


class VerifyNormalizerTests(unittest.TestCase):
    def test_a_printed_record_compares_equal_to_fortrans_flat_list(self):
        self.assertTrue(same_output("point(x=1.0, y=2.5)", " 1.00000000 2.50000000"))
        self.assertTrue(same_output("segment(a=point(x=1.0, y=2.0), tag=[1, 2])", "1.0 2.0 1 2"))
        self.assertFalse(same_output("point(x=1.0, y=2.5)", "1.0 2.6"))


if __name__ == "__main__":
    unittest.main()
