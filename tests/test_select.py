import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas import run_source
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse, Select, SelectExpr
from boreas.printer import to_source, without_comments
from test_fortran import (GFORTRAN, fixture, read, run_bor, run_gfortran, same_output,
                          assert_round_trips, conv, todos)
import subprocess


def run(body, funcs=""):
    out = []
    run_source(f"{funcs}\nprogram t\n{body}\nend program t\n", out.append)
    return out


def fails(body, pattern, funcs=""):
    with unittest.TestCase().assertRaisesRegex(LangError, pattern):
        run(body, funcs)


def stable(src):
    """parse -> print -> parse -> print gives the same text; returns the printed text."""
    once = to_source(parse(src))
    twice = to_source(parse(once))
    assert once == twice, f"{once}\n---\n{twice}"
    return once


GRADES = """integer :: g = {g}
select case fallthrough g
case 90:100
  print "A"
  break
case 80:89
  print "B"
  break
case 1, 3, 5
  print "odd"
case 7
  print "seven"
  break
case default
  print "other"
end select"""


class StatementTests(unittest.TestCase):
    def grade(self, g):
        return run(GRADES.format(g=g))

    def test_ranges_and_lists(self):
        self.assertEqual(self.grade(95), ["A"])
        self.assertEqual(self.grade(100), ["A"])
        self.assertEqual(self.grade(85), ["B"])
        self.assertEqual(self.grade(3), ["odd", "seven"])
        self.assertEqual(self.grade(7), ["seven"])
        self.assertEqual(self.grade(50), ["other"])

    def test_no_match_without_default_does_nothing(self):
        self.assertEqual(run("select case 5\ncase 1\nprint 1\nend select\nprint 2"), ["2"])

    def test_open_ranges(self):
        body = "integer :: i\ndo i = 0, 4\nselect case i\ncase :1\nprint \"lo\"\ncase 3:\nprint \"hi\"\ncase default\nprint \"mid\"\nend select\nend do"
        self.assertEqual(run(body), ["lo", "lo", "mid", "hi", "hi"])

    def test_negative_values(self):
        self.assertEqual(run("select case -3\ncase -5:-1\nprint \"neg\"\ncase default\nprint \"no\"\nend select"), ["neg"])

    def test_string_selector_and_ranges(self):
        body = 'string :: s = "cat"\nselect case s\ncase "dog", "wolf"\nprint 1\ncase "a":"d"\nprint 2\ncase default\nprint 3\nend select'
        self.assertEqual(run(body), ["2"])
        self.assertEqual(run(body.replace('"cat"', '"wolf"')), ["1"])
        self.assertEqual(run(body.replace('"cat"', '"zebra"')), ["3"])

    def test_logical_selector(self):
        body = "logical :: f = {f}\nselect case f\ncase true\nprint \"y\"\ncase false\nprint \"n\"\nend select"
        self.assertEqual(run(body.format(f="true")), ["y"])
        self.assertEqual(run(body.format(f="false")), ["n"])

    def test_const_names_as_case_values(self):
        body = "const integer :: lo = 10\nconst integer :: hi = 20\nselect case 15\ncase lo:hi\nprint \"in\"\ncase default\nprint \"out\"\nend select"
        self.assertEqual(run(body), ["in"])

    def test_const_names_from_a_module(self):
        src = ("module m\n  public const integer :: k = 4\nend module m\n"
               "program t\n  use m: k\n  select case 4\n  case k\n    print \"four\"\n  end select\nend program t\n")
        out = []
        run_source(src, out.append)
        self.assertEqual(out, ["four"])

    def test_keywords_case_insensitive(self):
        self.assertEqual(run("SELECT CASE FALLTHROUGH 2\nCASE 1, 2\nprint \"x\"\nBREAK\nCASE DEFAULT\nprint \"y\"\nEND SELECT"), ["x"])

    def test_fallthrough_chain_and_into_default(self):
        body = ("select case fallthrough 1\ncase 1\nprint \"a\"\ncase default\nprint \"d\"\n"
                "case 2\nprint \"b\"\nend select")
        self.assertEqual(run(body), ["a", "d", "b"])

    def test_fallthrough_starts_at_the_matching_case(self):
        body = "select case fallthrough {n}\ncase 3\nprint \"bronze\"\ncase 2\nprint \"silver\"\nbreak\ncase 1\nprint \"gold\"\nend select"
        self.assertEqual(run(body.format(n=3)), ["bronze", "silver"])
        self.assertEqual(run(body.format(n=2)), ["silver"])
        self.assertEqual(run(body.format(n=1)), ["gold"])
        self.assertEqual(run(body.format(n=9)), [])

    def test_without_the_mode_cases_do_not_flow(self):
        self.assertEqual(run("select case 3\ncase 3\nprint \"a\"\ncase 2\nprint \"b\"\nend select"), ["a"])

    def test_default_in_the_middle_flows_in_source_order(self):
        body = "select case fallthrough {n}\ncase 1\nprint \"one\"\ncase default\nprint \"d\"\ncase 2\nprint \"two\"\nend select"
        self.assertEqual(run(body.format(n=1)), ["one", "d", "two"])
        self.assertEqual(run(body.format(n=7)), ["d", "two"])
        self.assertEqual(run(body.format(n=2)), ["two"])

    def test_break_without_the_mode_is_an_early_exit(self):
        body = "select case 1\ncase 1\nprint \"a\"\nbreak\nprint \"b\"\nend select\nprint \"c\""
        self.assertEqual(run(body), ["a", "c"])

    def test_break_inside_if_in_a_case(self):
        body = ("select case fallthrough {n}\ncase 1\nif {n} == 1 then\nbreak\nend if\nprint \"a\"\n"
                "case 2\nprint \"b\"\nend select\nprint \"end\"")
        self.assertEqual(run(body.format(n=1)), ["end"])

    def test_break_leaves_only_the_innermost_select(self):
        body = ("select case fallthrough 1\ncase 1\nselect case 1\ncase 1\nbreak\nend select\nprint \"inner done\"\n"
                "case 2\nprint \"flowed\"\nend select")
        self.assertEqual(run(body), ["inner done", "flowed"])

    def test_break_in_a_select_inside_a_loop_does_not_leave_the_loop(self):
        body = ("integer :: i\ndo i = 1, 3\nselect case i\ncase 2\nbreak\nend select\nprint i\nend do")
        self.assertEqual(run(body), ["1", "2", "3"])

    def test_exit_and_cycle_in_a_flowing_select(self):
        body = ("integer :: i\ndo i = 1, 5\nselect case fallthrough i\ncase 2\ncycle\ncase 4\nexit\n"
                "case default\nprint i\nend select\nprint \"after\", i\nend do")
        self.assertEqual(run(body), ["1", "after 1", "3", "after 3"])

    def test_default_may_come_first(self):
        body = "select case {n}\ncase default\nprint \"d\"\ncase 1\nprint \"one\"\nend select"
        self.assertEqual(run(body.format(n=1)), ["one"])
        self.assertEqual(run(body.format(n=2)), ["d"])

    def test_exit_and_cycle_refer_to_the_loop(self):
        body = ("integer :: i\ndo i = 1, 6\nselect case i\ncase 2\ncycle\ncase 4\nexit\nend select\nprint i\nend do\nprint \"done\"")
        self.assertEqual(run(body), ["1", "3", "done"])

    def test_return_inside_select(self):
        f = ("function kind(integer :: n) -> string\n select case n\n case 0\n return \"zero\"\n"
             " case default\n return \"other\"\n end select\n return \"unreachable\"\nend function")
        self.assertEqual(run("print kind(0), kind(5)", f), ["zero other"])

    def test_nested_select(self):
        body = ("select case 1\ncase 1\nselect case \"b\"\ncase \"a\"\nprint \"a\"\ncase \"b\"\nprint \"b\"\nend select\n"
                "print \"outer\"\nend select")
        self.assertEqual(run(body), ["b", "outer"])

    def test_declaration_inside_case(self):
        self.assertEqual(run("select case 1\ncase 1\ninteger :: z = 4\nprint z\nend select"), ["4"])

    def test_selector_is_evaluated_once(self):
        f = "function bump(integer :: n) -> integer\n print \"called\"\n return n\nend function"
        self.assertEqual(run("select case bump(2)\ncase 1\nprint 1\ncase 2\nprint 2\nend select", f), ["called", "2"])

    def test_blank_lines_and_comments_inside(self):
        body = "select case 1 ! pick\n\n  ! first\ncase 1\n\n  print 1\n\nend select"
        self.assertEqual(run(body), ["1"])

    def test_empty_case_body(self):
        self.assertEqual(run("select case 1\ncase 1\ncase 2\nprint 2\nend select\nprint 3"), ["3"])


class ErrorTests(unittest.TestCase):
    def test_real_selector(self):
        fails("select case 1.5\ncase 1\nprint 1\nend select", "integer, string or logical value, got real")

    def test_array_selector(self):
        fails("select case [1, 2]\ncase 1\nprint 1\nend select", "got array")

    def test_type_mismatch(self):
        fails("select case 1\ncase \"a\"\nprint 1\nend select", "is string, but the select value is integer")
        fails("select case \"a\"\ncase 1\nprint 1\nend select", "is integer, but the select value is string")
        fails("select case true\ncase 1\nprint 1\nend select", "is integer, but the select value is logical")

    def test_mixed_literal_types_rejected_at_parse_time(self):
        with self.assertRaisesRegex(LangError, "mix integer and string"):
            parse("program t\nselect case 1\ncase 1\nprint 1\ncase \"a\"\nprint 2\nend select\nend program t\n")

    def test_real_case_value(self):
        with self.assertRaisesRegex(LangError, "cannot be real"):
            parse("program t\nselect case 1\ncase 1.5\nprint 1\nend select\nend program t\n")

    def test_logical_range(self):
        with self.assertRaisesRegex(LangError, "logical case value cannot be a range"):
            parse("program t\nselect case true\ncase false:true\nprint 1\nend select\nend program t\n")

    def test_empty_range(self):
        with self.assertRaisesRegex(LangError, "range 5:1 is empty"):
            parse("program t\nselect case 1\ncase 5:1\nprint 1\nend select\nend program t\n")

    def test_overlap_literals_at_parse_time(self):
        for cases in ("case 1, 2\nprint 1\ncase 2\nprint 2", "case 1:5\nprint 1\ncase 3:9\nprint 2",
                      "case :5\nprint 1\ncase 5:\nprint 2", "case 1, 1\nprint 1"):
            with self.subTest(cases=cases), self.assertRaisesRegex(LangError, "overlap"):
                parse(f"program t\nselect case 1\n{cases}\nend select\nend program t\n")

    def test_adjacent_ranges_do_not_overlap(self):
        self.assertEqual(run("select case 5\ncase :4\nprint 1\ncase 5:\nprint 2\nend select"), ["2"])

    def test_overlap_of_strings(self):
        with self.assertRaisesRegex(LangError, "overlap"):
            parse('program t\nselect case "a"\ncase "a":"m"\nprint 1\ncase "k"\nprint 2\nend select\nend program t\n')

    def test_overlap_with_const_names_found_at_run_time(self):
        fails("const integer :: a = 3\nselect case 1\ncase a\nprint 1\ncase 1:5\nprint 2\nend select", "overlap")

    def test_runtime_overlap_is_checked_even_if_selector_matches_earlier(self):
        fails("const integer :: a = 1\nselect case 1\ncase 1\nprint 1\ncase a\nprint 2\nend select", "overlap")

    def test_non_constant_case_values(self):
        for v in ("n + 1", "abs(2)", "[1, 2]"):
            with self.subTest(v=v), self.assertRaisesRegex(LangError, "literal or the name of a const"):
                parse(f"program t\nselect case 1\ncase {v}\nprint 1\nend select\nend program t\n")

    def test_name_must_be_a_const_at_run_time(self):
        fails("integer :: n = 2\nselect case 2\ncase n\nprint 1\nend select", "'n' is not a scalar const")
        fails("select case 2\ncase nope\nprint 1\nend select", "not declared")
        fails("const integer :: a(2) = 1\nselect case 2\ncase a\nprint 1\nend select", "not a scalar const")

    def test_two_defaults(self):
        with self.assertRaisesRegex(LangError, "'case default' appears more than once"):
            parse("program t\nselect case 1\ncase default\nprint 1\ncase default\nprint 2\nend select\nend program t\n")

    def test_no_cases(self):
        with self.assertRaisesRegex(LangError, "at least one case"):
            parse("program t\nselect case 1\nend select\nend program t\n")

    def test_statement_before_first_case(self):
        with self.assertRaisesRegex(LangError, "expected 'case' or 'end select'"):
            parse("program t\nselect case 1\nprint 1\ncase 1\nprint 2\nend select\nend program t\n")

    def test_missing_end_select(self):
        with self.assertRaisesRegex(LangError, "missing 'end select'"):
            parse("program t\nselect case 1\ncase 1\nprint 1\n")

    def test_fallthrough_misuse(self):
        wrap = "program t\n{}\nend program t\n"
        cases = {
            "as a statement in a case": "select case 1\ncase 1\nfallthrough\ncase 2\nprint 2\nend select",
            "after the selector": "select case 1 fallthrough\ncase 1\nprint 1\nend select",
            "outside a select": "fallthrough",
            "in a loop": "do while true\nfallthrough\nend do",
        }
        for name, body in cases.items():
            with self.subTest(name), self.assertRaises(LangError):
                parse(wrap.format(body))
        with self.assertRaisesRegex(LangError, "only allowed right after 'select case'"):
            parse(wrap.format("fallthrough"))

    def test_break_misuse(self):
        wrap = "program t\n{}\nend program t\n"
        with self.assertRaisesRegex(LangError, "'break' outside a select"):
            parse(wrap.format("break"))
        with self.assertRaisesRegex(LangError, "'break' outside a select"):
            parse(wrap.format("if true then\nbreak\nend if"))
        with self.assertRaisesRegex(LangError, "'break' outside a select"):
            parse("subroutine f()\nbreak\nend subroutine\nprogram t\nend program t\n")
        loop = "use 'exit'"
        for body in ("select case 1\ncase 1\ndo while true\nbreak\nend do\nend select",
                     "select case 1\ncase 1\ndo i = 1, 2\nif true then\nbreak\nend if\nend do\nend select",
                     "do while true\nselect case 1\ncase 1\nend select\nbreak\nend do"):
            with self.subTest(body), self.assertRaisesRegex(LangError, loop):
                parse(wrap.format(body))
        # a loop closed before the break is fine
        parse(wrap.format("select case 1\ncase 1\ndo while true\nexit\nend do\nbreak\nend select"))

    def test_break_in_an_expression(self):
        with self.assertRaisesRegex(LangError, "'break' cannot be used in an expression"):
            parse("program t\nprint select case 1\n case 1 -> break\n case default -> 0\nend select\nend program t\n")

    def test_case_outside_select(self):
        with self.assertRaisesRegex(LangError, "'case' outside a select"):
            parse("program t\ncase 1\nend program t\n")

    def test_exit_outside_loop_propagates(self):
        # select is not a loop: exit in a select outside any loop is not swallowed by the select
        with self.assertRaises(Exception):
            run("select case 1\ncase 1\nexit\nend select\nprint \"after\"")


class ExpressionTests(unittest.TestCase):
    EXPR = """integer :: g = {g}
string :: label
label = select case g
  case 90:100 -> "A"
  case 80:89 -> "B"
  case 1, 3 -> "small"
  case default -> "other"
end select
print label"""

    def test_assignment(self):
        for g, want in ((95, "A"), (85, "B"), (3, "small"), (50, "other")):
            self.assertEqual(run(self.EXPR.format(g=g)), [want])

    def test_print_argument_and_arithmetic(self):
        body = ("integer :: n = 2\nprint \"n is\", select case n\n case 1 -> \"one\"\n case default -> \"many\"\nend select\n"
                "print 10 * select case n\n case 2 -> 3\n case default -> 0\nend select + 1")
        self.assertEqual(run(body), ["n is many", "31"])

    def test_call_argument(self):
        f = "function twice(integer :: x) -> integer\n return x * 2\nend function"
        body = "print twice(select case 4\n case 4 -> 21\n case default -> 0\nend select)"
        self.assertEqual(run(body, f), ["42"])

    def test_inside_parentheses_and_if_condition(self):
        body = ("if (select case 1\n case 1 -> true\n case default -> false\n end select) then\nprint \"yes\"\nend if")
        self.assertEqual(run(body), ["yes"])

    def test_single_line_arms_and_blank_lines(self):
        body = "print select case 2\n\n case 1 -> 10\n\n case 2 -> 20\n case default -> 30\nend select"
        self.assertEqual(run(body), ["20"])

    def test_ampersand_continuation_inside_arm(self):
        body = 'print select case 2\n case 2 -> 1 + &\n 2\n case default -> 0\nend select'
        self.assertEqual(run(body), ["3"])

    def test_only_the_chosen_arm_is_evaluated(self):
        self.assertEqual(run("print select case 1\n case 1 -> 5\n case default -> 1 / 0\nend select"), ["5"])

    def test_const_names_and_string_and_logical(self):
        body = ('const string :: s = "x"\nprint select case "x"\n case s -> 1\n case default -> 2\nend select\n'
                'print select case false\n case true -> "t"\n case false -> "f"\n case default -> "?"\nend select')
        self.assertEqual(run(body), ["1", "f"])

    def test_nested_expressions(self):
        body = ("print select case 1\n case 1 -> select case 2\n  case 2 -> \"inner\"\n  case default -> \"x\"\n end select\n"
                " case default -> \"outer\"\nend select")
        self.assertEqual(run(body), ["inner"])

    def test_selector_may_be_an_expression_with_select(self):
        body = ("print select case select case 1\n case 1 -> 5\n case default -> 0\nend select\n case 5 -> \"five\"\n case default -> \"no\"\nend select")
        self.assertEqual(run(body), ["five"])

    def test_integer_arms_widen_to_real(self):
        body = "print select case 1\n case 1 -> 2\n case default -> 0.5\nend select\nprint select case 2\n case 1 -> 2\n case default -> 0.5\nend select"
        self.assertEqual(run(body), ["2.0", "0.5"])

    def test_default_is_required(self):
        with self.assertRaisesRegex(LangError, "must end with a 'case default ->"):
            parse("program t\nprint select case 1\n case 1 -> 2\nend select\nend program t\n")

    def test_arm_type_mismatch_between_literals_is_a_parse_error(self):
        with self.assertRaisesRegex(LangError, "different types"):
            parse('program t\nprint select case 1\n case 1 -> 2\n case default -> "x"\nend select\nend program t\n')

    def test_arm_type_mismatch_with_computed_arm_is_a_run_time_error(self):
        fails('integer :: n = 1\nprint select case 1\n case 1 -> n\n case default -> "x"\nend select', "different types")

    def test_then_is_not_an_arrow(self):
        with self.assertRaisesRegex(LangError, "use '->' instead of 'then'"):
            parse("program t\nprint select case 1\n case 1 then 2\n case default -> 0\nend select\nend program t\n")

    def test_missing_arrow(self):
        with self.assertRaisesRegex(LangError, "expected '->'"):
            parse("program t\nprint select case 1\n case 1 2\n case default -> 0\nend select\nend program t\n")

    def test_fallthrough_not_allowed(self):
        with self.assertRaisesRegex(LangError, "cannot fall through"):
            parse("program t\nprint select case fallthrough 1\n case 1 -> 2\n case default -> 0\nend select\nend program t\n")

    def test_expression_overlap_and_types(self):
        with self.assertRaisesRegex(LangError, "overlap"):
            parse("program t\nprint select case 1\n case 1 -> 2\n case 1 -> 3\n case default -> 0\nend select\nend program t\n")
        fails('print select case "a"\n case 1 -> 2\n case default -> 0\nend select', "is integer, but the select value is string")
        fails("print select case 1.5\n case 1 -> 2\n case default -> 0\nend select", "got real")


class UpdateTests(unittest.TestCase):
    SUB = """subroutine bump(integer :: k, out integer :: r, inout integer :: total)
  select case k
  case 1
    update r = 10
  case default
    update r = 0
    update total = total + 1
  end select
end subroutine"""

    def test_update_in_select_bodies(self):
        body = "integer :: r\ninteger :: t = 0\ncall bump(1, r, t)\nprint r, t\ncall bump(2, r, t)\nprint r, t"
        self.assertEqual(run(body, self.SUB), ["10 0", "0 1"])

    def test_update_in_a_flowing_select_with_break(self):
        sub = ("subroutine f(integer :: k, inout integer :: t)\n select case fallthrough k\n case 1\n update t = t + 1\n"
               " case 2\n update t = t + 10\n break\n case 3\n update t = t + 100\n end select\nend subroutine")
        body = "integer :: t = 0\ncall f(1, t)\nprint t\ncall f(2, t)\nprint t\ncall f(3, t)\nprint t"
        self.assertEqual(run(body, sub), ["11", "21", "121"])
        bad = sub.replace("update t = t + 100", "t = t + 100")
        with self.assertRaisesRegex(LangError, "write 'update t = ...'"):
            parse(bad + "\nprogram p\nend program p\n")

    def test_missing_update_in_select_body(self):
        sub = "subroutine f(out integer :: r)\n select case 1\n case 1\n r = 2\n end select\nend subroutine"
        with self.assertRaisesRegex(LangError, "write 'update r = ...'"):
            parse(sub + "\nprogram t\nend program t\n")

    def test_update_of_local_in_select_body(self):
        sub = "subroutine f(integer :: n)\n integer :: x\n select case n\n case 1\n update x = 2\n end select\nend subroutine"
        with self.assertRaisesRegex(LangError, "'update' is for out and inout"):
            parse(sub + "\nprogram t\nend program t\n")

    def test_loop_variable_cannot_be_parameter_inside_select(self):
        sub = "subroutine f(inout integer :: i)\n select case 1\n case 1\n do i = 1, 2\n end do\n end select\nend subroutine"
        with self.assertRaisesRegex(LangError, "loop variable"):
            parse(sub + "\nprogram t\nend program t\n")


class PrinterTests(unittest.TestCase):
    def test_statement_layout(self):
        src = "program t\n" + GRADES.format(g=3) + "\nend program t\n"
        want = """program t
  integer :: g = 3
  select case fallthrough g
  case 90:100
    print "A"
    break
  case 80:89
    print "B"
    break
  case 1, 3, 5
    print "odd"
  case 7
    print "seven"
    break
  case default
    print "other"
  end select
end program t
"""
        self.assertEqual(to_source(parse(src)), want)
        self.assertEqual(stable(src), want)

    def test_open_ranges_negatives_strings_logicals_consts(self):
        src = ("program t\nconst integer :: k = 2\nselect case 1\ncase :-3, -2:-1, 0, k:, 9\nprint 1\nend select\n"
               "select case \"a\"\ncase \"b\":\"c\", \":\"\nprint 2\nend select\n"
               "select case true\ncase true\nprint 3\nend select\nend program t\n")
        text = stable(src)
        self.assertIn("case :-3, -2:-1, 0, k:, 9", text)
        self.assertIn('case "b":"c", ":"', text)

    def test_expression_layout_and_round_trip(self):
        src = ('program t\nstring :: label\nlabel = select case 1\ncase 90:100 -> "A"\ncase default -> "o"\nend select\n'
               'if true then\nprint "x", select case 2\ncase 1, 2 -> 1\ncase default -> 2\nend select\nend if\nend program t\n')
        want = """program t
  string :: label
  label = select case 1
    case 90:100 -> "A"
    case default -> "o"
  end select
  if true then
    print "x", select case 2
      case 1, 2 -> 1
      case default -> 2
    end select
  end if
end program t
"""
        self.assertEqual(stable(src), want)

    def test_nested_expressions_round_trip(self):
        src = ("program t\nprint select case 1\n case 1 -> select case 2\n  case 2 -> \"inner\"\n  case default -> \"x\"\n end select\n"
               " case default -> \"outer\"\nend select, 1 + select case 3\n case 3 -> 4\n case default -> 5\nend select\nend program t\n")
        text = stable(src)
        out1, out2 = [], []
        from boreas.interp import Interp
        Interp(parse(src), out1.append).run()
        Interp(parse(text), out2.append).run()
        self.assertEqual(out1, out2)
        self.assertEqual(out1, ["inner 5"])

    def test_nested_statements_and_inside_procedures(self):
        src = ("function f(integer :: n) -> integer\n  select case n\n  case 1\n    select case n + 1\n    case 2\n      return 5\n    end select\n"
               "  end select\n  return 0\nend function\nprogram t\n  print f(1)\nend program t\n")
        text = stable(src)
        self.assertIn("      return 5", text)

    def test_without_comments_reaches_into_select(self):
        from boreas.parser import Comment
        unit = parse("program t\nselect case 1\ncase 1\nprint 1\nend select\nend program t\n")
        unit.program.body[0].cases[0].body.insert(0, Comment("hi"))
        stripped = without_comments(unit)
        self.assertEqual(len(stripped.program.body[0].cases[0].body), 1)

    def test_ast_shapes(self):
        unit = parse("program t\nselect case 1\ncase 1\nprint 1\nend select\nprint select case 1\ncase default -> 2\nend select\nend program t\n")
        self.assertIsInstance(unit.program.body[0], Select)
        self.assertIsInstance(unit.program.body[1].args[0], SelectExpr)


class ConverterTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = fixture("select.f90")
        if GFORTRAN:
            subprocess.run([GFORTRAN, "-fsyntax-only", "-std=f2018", path], check=True, capture_output=True)
        text, findings = convert(read(path), "select.f90")
        self.assertEqual(todos(findings), [])
        assert_round_trips(read(path))
        parse(text)
        got = run_bor(text)
        self.assertIn("feline", got)
        self.assertEqual(got[5:10], ["A", "B", "C", "F", "?"])
        self.assertEqual(got[-1], "6")
        if GFORTRAN:
            want = run_gfortran(path)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")

    def test_basic_translation(self):
        text, findings = conv("  select case (n)\n  case (1, 2)\n    print *, 'a'\n  case (3:5)\n    print *, 'b'\n"
                              "  case (:0)\n    print *, 'c'\n  case (9:)\n    print *, 'd'\n  case default\n    print *, 'e'\n  end select",
                              "  integer :: n = 4")
        self.assertEqual(todos(findings), [])
        for line in ("select case n", "case 1, 2", "case 3:5", "case :0", "case 9:", "case default", "end select"):
            self.assertIn(line, text)
        self.assertNotIn("fallthrough", text)
        self.assertNotIn("break", text)
        self.assertEqual(run_bor(text), ["b"])

    def test_construct_names_dropped_and_endselect_spelling(self):
        text, findings = conv("  pick: select case (n)\n  case (1) pick\n    print *, 'one'\n  case default pick\n    print *, 'other'\n  endselect pick",
                              "  integer :: n = 1")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["one"])

    def test_comments_between_cases(self):
        text, findings = conv("  select case (n)\n  ! about one\n  case (1)\n    ! inside\n    print *, 'one'\n  end select",
                              "  integer :: n = 1")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["one"])

    def test_parameter_case_values(self):
        text, findings = conv("  select case (n)\n  case (lo:hi)\n    print *, 'in'\n  end select",
                              "  integer, parameter :: lo = 1, hi = 9\n  integer :: n = 5")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["in"])

    def test_module_parameter_case_value(self):
        src = ("module m\n  integer, parameter :: k = 3\nend module m\nprogram p\n  use m\n  implicit none\n"
               "  select case (3)\n  case (k)\n    print *, 'three'\n  end select\nend program p\n")
        assert_round_trips(src)
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["three"])

    def test_non_parameter_case_value_falls_back_to_todo(self):
        text, findings = conv("  select case (n)\n  case (m)\n    print *, 'x'\n  end select\n  print *, 'after'",
                              "  integer :: n = 1, m = 1")
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("TODO(convert)", text)
        self.assertNotIn("select case", text.split("TODO(convert)")[-1].split("\n", 1)[1])
        self.assertEqual(run_bor(text), ["after"])

    def test_real_and_computed_case_values_fall_back(self):
        for sel in ("case (1.5)", "case (1 + 1)"):
            with self.subTest(sel=sel):
                text, findings = conv(f"  select case (n)\n  {sel}\n    print *, 'x'\n  end select\n  print *, 'after'",
                                      "  integer :: n = 1")
                self.assertEqual(len(todos(findings)), 1)
                self.assertEqual(run_bor(text), ["after"])

    def test_select_type_falls_back_to_todo(self):
        text, findings = conv("  select type (n)\n  type is (integer)\n    print *, 'x'\n  end select\n  print *, 'after'",
                              "  integer :: n = 1")
        self.assertEqual(len(todos(findings)), 1)
        self.assertEqual(run_bor(text), ["after"])

    def test_nested_fallback_leaves_outer_select(self):
        body = ("  select case (n)\n  case (1)\n    select case (n)\n    case (m)\n      print *, 'bad'\n    end select\n"
                "    print *, 'kept'\n  end select")
        text, findings = conv(body, "  integer :: n = 1, m = 1")
        self.assertEqual(len(todos(findings)), 1)
        self.assertEqual(run_bor(text), ["kept"])

    def test_reserved_words_renamed(self):
        text, _ = conv("  case = 1\n  default = case + 1\n  select = default\n  fallthrough = select\n  break = fallthrough\n  print *, break",
                       "  integer :: case, default, select, fallthrough, break")
        self.assertIn("case_ = 1", text)
        self.assertIn("break_ = fallthrough_", text)
        self.assertEqual(run_bor(text), ["2"])

    def test_intent_inference_counts_writes_in_select_as_conditional(self):
        src = ("subroutine setit(k, r)\n  integer, intent(in) :: k\n  integer :: r\n  select case (k)\n  case (1)\n    r = 5\n"
               "  end select\nend subroutine\nprogram p\n  integer :: v\n  v = 0\n  call setit(1, v)\n  print *, v\nend program\n")
        text, _ = convert(src)
        self.assertIn("inout integer :: r", text)      # not always written, so it cannot be out
        self.assertIn("update r = 5", text)
        self.assertEqual(run_bor(text), ["5"])

    def test_write_in_every_branch_is_still_only_conditional(self):
        src = ("subroutine setit(k, r)\n  integer, intent(in) :: k\n  integer :: r\n  select case (k)\n  case default\n    r = 5\n"
               "  end select\nend subroutine\nprogram p\n  integer :: v\n  call setit(1, v)\n  print *, v\nend program\n")
        text, _ = convert(src)
        self.assertIn("update r = 5", text)

    def test_function_result_assigned_in_select_and_return_inside(self):
        src = ("integer function f(n)\n  integer, intent(in) :: n\n  select case (n)\n  case (1)\n    f = 10\n    return\n"
               "  case default\n    f = 0\n  end select\nend function\nprogram p\n  print *, f(1), f(2)\nend program\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["10 0"])

    def test_dummy_loop_variable_inside_select_is_flagged(self):
        src = ("subroutine f(i)\n  integer, intent(inout) :: i\n  select case (1)\n  case (1)\n    do i = 1, 2\n    end do\n  end select\nend subroutine\n"
               "program p\n  integer :: k\n  call f(k)\nend program\n")
        _, findings = convert(src)
        self.assertTrue(any("loop variable" in f[2] for f in todos(findings)))

    def test_case_variable_name_is_not_mistaken_for_a_case_line(self):
        text, _ = conv("  case(1) = 4\n  print *, case(1)", "  integer :: case(2)")
        self.assertIn("case_(1) = 4", text)


if __name__ == "__main__":
    unittest.main()
