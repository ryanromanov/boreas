import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, run_bor, todos
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

RECORDS = ("record address\n  string :: city\nend record\n"
           "record person\n  string :: name\n  address? :: home\nend record\n")


def program(body, extra=""):
    return f"{RECORDS}{extra}program t\n{body}\nend program\n"


def out(body, extra=""):
    return run_bor(program(body, extra))


def fails(test, body, message, extra=""):
    with test.assertRaises(LangError) as cm:
        run_bor(program(body, extra))
    test.assertIn(message, str(cm.exception))


class OptionalValueTests(unittest.TestCase):
    def test_an_optional_variable_starts_absent(self):
        self.assertEqual(out("  integer? :: n\n  print n, present(n), n == null"), ["null false true"])

    def test_assigning_a_value_and_null(self):
        self.assertEqual(out("  integer? :: n\n  n = 4\n  print present(n), n\n  n = null\n  print present(n)"),
                         ["true 4", "false"])

    def test_declared_with_a_value(self):
        self.assertEqual(out("  real? :: x = 2\n  print x"), ["2.0"])

    def test_a_plain_variable_cannot_hold_null(self):
        fails(self, "  integer :: n\n  n = null", "cannot assign null to integer")

    def test_a_plain_variable_keeps_its_zero_default(self):
        self.assertEqual(out("  integer :: n\n  print n"), ["0"])

    def test_coalesce(self):
        self.assertEqual(out("  integer? :: n\n  print n ?? 7\n  n = 2\n  print n ?? 7"), ["7", "2"])

    def test_coalesce_chains_and_is_loosest(self):
        self.assertEqual(out("  integer? :: a\n  integer? :: b\n  print a ?? b ?? 3 + 1"), ["4"])

    def test_coalesce_only_evaluates_the_right_side_when_needed(self):
        extra = "function boom() -> integer\n  return 1 / 0\nend function\n"
        self.assertEqual(out("  integer? :: n\n  n = 1\n  print n ?? boom()", extra), ["1"])

    def test_comparing_with_null(self):
        self.assertEqual(out("  integer? :: n\n  print n == null, n /= null\n  n = 0\n  print n == null, n /= null"),
                         ["true false", "false true"])

    def test_arithmetic_on_null_is_an_error(self):
        fails(self, "  integer? :: n\n  print n + 1", "cannot be used with null")

    def test_ordering_null_is_an_error(self):
        fails(self, "  integer? :: n\n  print n < 1", "cannot be used with null")

    def test_condition_on_null_is_an_error(self):
        fails(self, "  logical? :: b\n  if b then\n    print 1\n  end if", "got null")

    def test_negating_null_is_an_error(self):
        fails(self, "  integer? :: n\n  print -n", "cannot be used with null")

    def test_integer_widens_into_optional_real(self):
        self.assertEqual(out("  real? :: x\n  x = 3\n  print x"), ["3.0"])

    def test_wrong_type_into_optional(self):
        fails(self, '  integer? :: n\n  n = "a"', "cannot assign string to integer")

    def test_arrays_of_optional_values_are_rejected(self):
        with self.assertRaises(LangError):
            parse(program("  integer? :: a(3)"))


class OptionalRecordTests(unittest.TestCase):
    def test_optional_field_starts_absent(self):
        self.assertEqual(out("  person :: p\n  print present(p.home)"), ["false"])

    def test_set_and_read_an_optional_field(self):
        self.assertEqual(out('  person :: p\n  p.home = address("Pittsburgh")\n  print present(p.home), p.home.city'),
                         ["true Pittsburgh"])

    def test_reading_through_null_is_an_error(self):
        fails(self, "  person :: p\n  print p.home.city", "'p.home' is null")

    def test_coalesce_a_field(self):
        self.assertEqual(out('  person :: p\n  address :: a\n  a = p.home ?? address("none")\n  print a.city'), ["none"])

    def test_construct_with_null(self):
        self.assertEqual(out('  person :: p\n  p = person("Ann", null)\n  print present(p.home)'), ["false"])

    def test_value_semantics_are_kept(self):
        body = '  person :: p\n  person :: q\n  p.home = address("A")\n  q = p\n  p.home = null\n  print q.home.city'
        self.assertEqual(out(body), ["A"])

    def test_a_record_cannot_hold_itself_even_optionally(self):
        with self.assertRaises(LangError):
            run_bor("record node\n  integer :: v\n  node? :: next\nend record\nprogram t\nend program\n")

    def test_optional_field_with_a_null_default(self):
        self.assertEqual(out("  box :: b\n  print present(b.n)", "record box\n  integer? :: n = null\nend record\n"),
                         ["false"])

    def test_out_record_must_assign_optional_fields(self):
        extra = ("subroutine fill(out person :: p)\n  update p.name = \"x\"\nend subroutine\n")
        fails(self, "  person :: p\n  call fill(p)", "was not fully assigned", extra)


class OptionalParameterTests(unittest.TestCase):
    STEP = "function step_or(integer? :: step) -> integer\n  return step ?? 1\nend function\n"

    def test_trailing_optional_arguments_may_be_left_off(self):
        self.assertEqual(out("  print step_or(), step_or(3)", self.STEP), ["1 3"])

    def test_explicit_null(self):
        self.assertEqual(out("  print step_or(null)", self.STEP), ["1"])

    def test_present_on_a_parameter(self):
        extra = ("subroutine show(integer :: a, integer? :: b)\n  if present(b) then\n    print a, b\n  else\n"
                 "    print a, \"-\"\n  end if\nend subroutine\n")
        self.assertEqual(out("  call show(1)\n  call show(1, 2)", extra), ["1 -", "1 2"])

    def test_required_argument_cannot_be_left_off(self):
        extra = "function f(integer :: a, integer? :: b) -> integer\n  return a\nend function\n"
        fails(self, "  print f()", "f expects 1 to 2 arguments, got 0", extra)

    def test_too_many_arguments(self):
        fails(self, "  print step_or(1, 2)", "step_or expects 0 to 1 arguments, got 2", self.STEP)

    def test_optional_in_the_middle_still_needs_a_value(self):
        extra = "function f(integer? :: a, integer :: b) -> integer\n  return b\nend function\n"
        fails(self, "  print f(1)", "f expects 2 arguments, got 1", extra)

    def test_null_for_a_required_parameter(self):
        extra = "function f(integer :: a) -> integer\n  return a\nend function\n"
        fails(self, "  integer? :: n\n  print f(n)", "argument 'a': cannot assign null to integer", extra)

    def test_a_value_flows_through_to_a_required_parameter(self):
        extra = "function f(integer :: a) -> integer\n  return a + 1\nend function\n"
        self.assertEqual(out("  integer? :: n\n  n = 4\n  print f(n)", extra), ["5"])

    def test_optional_out_and_inout_are_rejected(self):
        for mode in ("out", "inout"):
            with self.assertRaises(LangError, msg=mode):
                parse(f"subroutine s({mode} integer? :: x)\nend subroutine\nprogram t\nend program\n")


class OptionalReturnTests(unittest.TestCase):
    FIND = ("function find(integer :: x) -> integer?\n  if x > 3 then\n    return x\n  end if\n"
            "  return null\nend function\n")

    def test_function_may_return_null(self):
        self.assertEqual(out("  print find(1), find(7)\n  print find(1) ?? -1", self.FIND), ["null 7", "-1"])

    def test_null_from_a_plain_function_is_an_error(self):
        extra = "function f() -> integer\n  return null\nend function\n"
        fails(self, "  print f()", "cannot assign null to integer", extra)


class OptionalPrintingTests(unittest.TestCase):
    def test_round_trip(self):
        src = ("function f(integer :: a, integer? :: b) -> integer?\n  if present(b) then\n    return b ?? a\n"
               "  end if\n  return null\nend function\n\n"
               "program t\n  integer? :: n\n  n = null\n  print n ?? 1 + 2, (n ?? 1) + 2\nend program t\n")
        printed = to_source(parse(src))
        self.assertEqual(printed, src)
        self.assertEqual(to_source(parse(printed)), printed)

    def test_right_nested_coalesce_keeps_its_parentheses(self):
        src = "program t\n  integer? :: a\n  print a ?? (a ?? 1)\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)


class ConvertOptionalTests(unittest.TestCase):
    check = FixtureTests.check

    def test_optional_fixture_matches_gfortran(self):
        got = self.check("optional.f90")
        self.assertEqual(got[0], "7")

    def test_optional_dummy_becomes_an_optional_parameter(self):
        text, findings = convert(
            "subroutine s(a, b)\n  integer, intent(in) :: a\n  integer, intent(in), optional :: b\n"
            "  if (present(b)) print *, a + b\nend subroutine\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("subroutine s(integer :: a, integer? :: b)", text)
        self.assertIn("if present(b) then", text)

    def test_modified_optional_argument_is_a_todo(self):
        text, findings = convert(
            "subroutine s(n)\n  integer, intent(out), optional :: n\n  if (present(n)) n = 3\nend subroutine\n")
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("optional and modified", todos(findings)[0][2])
        self.assertIn("subroutine s(out integer :: n)", text)

    def test_optional_array_argument_is_a_todo(self):
        _, findings = convert(
            "subroutine s(a)\n  real, intent(in), optional :: a(:)\n  print *, present(a)\nend subroutine\n")
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("optional array", todos(findings)[0][2])

    def test_optional_record_argument(self):
        text, findings = convert(
            "module m\n  type pt\n    integer :: x\n  end type\ncontains\n  subroutine s(p)\n"
            "    type(pt), intent(in), optional :: p\n    if (present(p)) print *, p%x\n  end subroutine\nend module\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("pt? :: p", text)


if __name__ == "__main__":
    unittest.main()
