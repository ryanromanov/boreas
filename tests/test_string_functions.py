"""s[i], upper, lower, replace, contains, starts_with, ends_with, join, scan, verify, adjustl, adjustr, index back."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor
from boreas.lexer import LangError
from boreas.verify import same_output


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


def fails(case, body, message):
    with case.assertRaises(LangError) as cm:
        show(body)
    case.assertIn(message, str(cm.exception))


S = '  string :: s = "hello"\n'


class CharacterTests(unittest.TestCase):
    def test_one_character(self):
        self.assertEqual(show(S + "  println(s[1], s[5], s[2:3])"), ["h o el"])

    def test_assign_one_character(self):
        self.assertEqual(show(S + '  s[1] = "J"\n  println(s)'), ["Jello"])

    def test_bounds(self):
        fails(self, S + "  println(s[0])", "index 0 out of bounds 1..5")
        fails(self, S + "  println(s[6])", "index 6 out of bounds 1..5")
        fails(self, S + "  println(s[1.5])", "must be an integer")

    def test_messages_use_brackets(self):
        fails(self, S + '  s[2:2] = "XY"', "s[2:2] holds 1 character;")
        fails(self, S + "  println(s[-1:3])", "substring s[-1:3] is outside")


class FunctionTests(unittest.TestCase):
    def test_case_and_replace(self):
        self.assertEqual(show('  println(upper("abC"), lower("AbC"), replace("a-b-c", "-", "+"))'), ["ABC abc a+b+c"])

    def test_tests(self):
        self.assertEqual(show('  println(contains("hello", "ell"), starts_with("hello", "he"), ends_with("hello", "he"))'),
                         ["true true false"])

    def test_join(self):
        self.assertEqual(show('  string[3] :: w = ["a", "b", "c"]\n  println(join(w, ", "))'), ["a, b, c"])
        self.assertEqual(show('  println(join(split("a b c", " "), "-"))'), ["a-b-c"])

    def test_mistakes(self):
        fails(self, '  println(replace("abc", "", "x"))', "must not be empty")
        fails(self, '  println(join([1, 2], ","))', "needs an array of strings")
        fails(self, "  println(upper(3))", "needs a string, got int")
        fails(self, '  println(contains("a"))', "takes 2 arguments, got 1")


class FortranFunctionTests(unittest.TestCase):
    def test_index_scan_verify(self):
        self.assertEqual(show('  println(index("hello", "l"), index("hello", "l", true), index("hello", "z"))'), ["3 4 0"])
        self.assertEqual(show('  println(scan("hello", "lo"), scan("hello", "lo", true), scan("abc", "z"))'), ["3 5 0"])
        self.assertEqual(show('  println(verify("hello", "he"), verify("hello", "helo"), verify("hello", "he", true))'), ["3 0 5"])

    def test_adjust(self):
        self.assertEqual(show('  println("[" + adjustl("  ab  ") + "]", "[" + adjustr("  ab  ") + "]")'), ["[ab    ] [    ab]"])


@unittest.skipUnless(GFORTRAN, "needs gfortran")
class ConversionTests(unittest.TestCase):
    SRC = '''program t
  implicit none
  character(len=8) :: s
  s = "  hello "
  print *, index(s, "l"), index(s, "l", back=.true.), index(string=s, substring="e")
  print *, scan(s, "lo"), scan(s, "lo", .true.), verify(s, " h"), verify(s, " h", back=.true.)
  print *, "[", adjustl(s), "]", "[", adjustr(s), "]"
end program
'''

    def test_it_converts_and_matches_gfortran(self):
        text, findings = converted(self.SRC)
        self.assertEqual([f for f in findings if f[1] == "todo"], [])
        got = run_bor(text)
        want = fortran_output(self.SRC)
        self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")


class NegateTests(unittest.TestCase):
    def test_the_message_is_not_garbled(self):
        fails(self, "  int32 :: i = -2147483647 - 1\n  println(-i)", "negating -2147483648")


if __name__ == "__main__":
    unittest.main()
