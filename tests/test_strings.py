import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, check_syntax, conv, fixture, read, run_bor, todos
from boreas.verify import same_output
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


class TrimTests(unittest.TestCase):
    def test_trim_and_len_trim(self):
        self.assertEqual(out('  print "[" + trim("ab  ") + "]", len_trim("ab  "), len_trim("")'), ["[ab] 2 0"])
        fails(self, "  print trim(3)", "needs a string")


class FixedLengthCharacterTests(unittest.TestCase):
    def text(self, decls, body):
        text, findings = conv(body, decls)
        return text, findings

    def test_assignment_pads_and_cuts_literals_at_conversion_time(self):
        text, findings = self.text("  character(len=5) :: a\n  character(len=2) :: b", "  a = 'ab'\n  b = 'wxyz'")
        self.assertEqual(todos(findings), [])
        self.assertIn('a = "ab   "', text)
        self.assertIn('b = "wx"', text)

    def test_other_values_are_fitted_with_left(self):
        text, _ = self.text("  character(len=4) :: a\n  character(len=9) :: s", "  a = s")
        self.assertIn("a = left(s, 4)", text)

    def test_declaration_initialiser_and_named_length(self):
        text, _ = self.text("  integer, parameter :: n = 3\n  character(len=n) :: a = 'x'", "")
        self.assertIn('string :: a = "x  "', text)

    def test_array_elements_but_not_substrings(self):
        text, _ = self.text("  character(len=3) :: names(2)\n  character(len=9) :: s", "  names(1) = s\n  s(1:1) = 'x'")
        self.assertIn("names(1) = left(s, 3)", text)
        self.assertIn('s(1:1) = "x"', text)

    def test_comparisons_and_select_ignore_trailing_blanks(self):
        text, _ = self.text("  character(len=5) :: a", "  if (a == 'abc  ') print *, 1\n  select case (a)\n"
                            "  case ('x')\n    print *, 2\n  end select")
        self.assertIn('if trim(a) == "abc" then', text)
        self.assertIn("select case trim(a)", text)

    def test_len_is_the_declared_length(self):
        text, _ = self.text("  character(len=5) :: a\n  integer :: n", "  n = len(a)")
        self.assertIn("n = 5", text)

    def test_read_into_a_fixed_length_variable_fits_it(self):
        text, _ = self.text("  character(len=3) :: a", "  read(*,*) a")
        self.assertIn("a = left(a, 3)", text)

    def test_length_forms(self):
        for decl in ("character(3)", "character(len=3)", "character(3, 1)", "character(kind=1, len=3)"):
            with self.subTest(decl=decl):
                text, _ = self.text(f"  {decl} :: a\n  character(len=9) :: s", "  a = s")
                self.assertIn("a = left(s, 3)", text)
        text, _ = self.text("  character :: a*4\n  character(len=9) :: s", "  a = s")
        self.assertIn("a = left(s, 4)", text)
        text, _ = self.text("  character :: a\n  character(len=9) :: s", "  a = s")
        self.assertIn("a = left(s, 1)", text)               # no length means 1

    def test_assumed_length_is_not_tracked(self):
        _, findings = convert("subroutine s(c)\n  character(len=*) :: c\n  c = 'x'\nend subroutine\n")
        self.assertTrue(any("length not tracked" in f[2] for f in findings))


@unittest.skipUnless(GFORTRAN, "gfortran not installed")
class CharsFixtureTests(unittest.TestCase):
    def test_chars_fixture_matches_gfortran(self):
        path, stdin = fixture("chars.f90"), read(fixture("chars.in"))
        check_syntax(path)
        text, findings = convert(read(path), "chars.f90")
        self.assertEqual(todos(findings), [])
        out_lines = []
        Interp(parse(text), out_lines.append, stdin=stdin).run()
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "a.out")
            subprocess.run([GFORTRAN, os.path.abspath(path), "-o", exe], check=True, capture_output=True, cwd=tmp)
            want = subprocess.run([exe], input=stdin, capture_output=True, text=True).stdout
        self.assertTrue(same_output("\n".join(out_lines), want), f"\nBoreas:\n{chr(10).join(out_lines)}\n\ngfortran:\n{want}")
        self.assertIn("abc  |", out_lines)                    # padded exactly, not just up to spacing


if __name__ == "__main__":
    unittest.main()
