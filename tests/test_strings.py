import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse


def out(body, extra=""):
    return run_bor(f"{extra}program t\n{body}\nend program\n")


def fails(test, body, message, extra=""):
    with test.assertRaises(LangError) as cm:
        out(body, extra)
    test.assertIn(message, str(cm.exception))


class SubstringTests(unittest.TestCase):
    def test_read_a_substring(self):
        self.assertEqual(out('  string :: s = "Hello"\n  print s(1:2), s(:3), s(4:), s(2:2) == "e"'),
                         ["He Hel lo true"])

    def test_assign_a_substring(self):
        self.assertEqual(out('  string :: s = "Hello"\n  s(1:1) = "J"\n  s(4:5) = "ly"\n  print s'), ["Jelly"])

    def test_update_a_substring_of_an_inout_parameter(self):
        extra = 'subroutine up(inout string :: s)\n  update s(1:1) = achar(iachar(s(1:1)) - 32)\nend subroutine\n'
        self.assertEqual(out('  string :: w = "hi"\n  call up(w)\n  print w', extra), ["Hi"])

    def test_substring_must_stay_inside_the_string(self):
        fails(self, '  string :: s = "Hi"\n  print s(0:1)', "outside the string")
        fails(self, '  string :: s = "Hi"\n  print s(2:3)', "outside the string")

    def test_assigned_text_must_fit_exactly(self):
        fails(self, '  string :: s = "Hello"\n  s(1:2) = "J"', "holds 2 characters")

    def test_a_single_index_is_not_a_substring(self):
        fails(self, '  string :: s = "Hi"\n  print s(1)', "take part of it with")


class StringFunctionTests(unittest.TestCase):
    def test_len_achar_iachar(self):
        self.assertEqual(out('  print len("abc"), iachar("A"), achar(66)'), ["3 65 B"])

    def test_wrong_types(self):
        fails(self, "  print len(3)", "needs a string")
        fails(self, '  print iachar("ab")', "needs a one-character string")


class SystemTests(unittest.TestCase):
    def test_random_is_in_range(self):
        self.assertEqual(out("  real :: r = random()\n  print r >= 0.0 and r < 1.0"), ["true"])

    def test_arguments(self):
        lines = []
        Interp(parse('program t\n  print argument_count(), argument(1), argument(2) == ""\nend program\n'),
               lines.append, ["prog", "--version"]).run()
        self.assertEqual(lines, ["1 --version true"])


class ConvertTests(unittest.TestCase):
    def test_intrinsic_subroutines(self):
        text, findings = convert(
            "program p\n  character(len=16) :: arg\n  real :: r\n  call random_init(.false., .false.)\n"
            "  call get_command_argument(1, arg)\n  call random_number(r)\n"
            "  print *, ichar('A'), char(66), command_argument_count()\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("arg = argument(1)", text)
        self.assertIn("r = random()", text)
        self.assertIn("print iachar(\"A\"), achar(66), argument_count()", text)
        self.assertNotIn("random_init", text)

    def test_random_number_of_an_array_is_a_todo(self):
        _, findings = convert("program p\n  real :: a(3)\n  call random_number(a)\nend program\n")
        self.assertEqual(len(todos(findings)), 1)


if __name__ == "__main__":
    unittest.main()
