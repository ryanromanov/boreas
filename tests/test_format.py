import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, conv, todos
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.interp import Interp


def out(body):
    lines = []
    Interp(parse(f"program t\n{body}\nend program\n"), lines.append).run()
    return lines


def fails(test, body, message):
    with test.assertRaises(LangError) as cm:
        out(body)
    test.assertIn(message, str(cm.exception))


class FormatFunctionTests(unittest.TestCase):
    def test_fixed(self):
        self.assertEqual(out('  print fixed(3.14159, 3), fixed(2, 1), fixed(-0.5, 2), fixed(2.5, 0)'),
                         ["3.142 2.0 -0.50 2."])

    def test_fixed_with_a_width_right_aligns_and_uses_stars(self):
        self.assertEqual(out('  print "[" + fixed(3.14159, 2, 8) + "]", fixed(0.5, 3, 4), fixed(0.5, 3, 3), fixed(123.456, 1, 4)'),
                         ["[    3.14] .500 *** ****"])

    def test_right_and_left(self):
        self.assertEqual(out('  print "[" + right("ab", 5) + "][" + left("ab", 5) + "][" + right(42, 5) + "]"'),
                         ["[   ab][ab   ][   42]"])

    def test_pad_character(self):
        self.assertEqual(out('  print right(42, 5, "0"), right(-42, 5, "0"), right("ab", 5, "*"), left(7, 4, ".")'),
                         ["00042 -0042 ***ab 7..."])

    def test_too_long(self):
        self.assertEqual(out('  print right("abcdef", 3), left("abcdef", 3), right(1234567, 5), left(12345, 3)'),
                         ["abc abc ***** ***"])

    def test_width_zero_is_just_the_text(self):
        self.assertEqual(out('  print "<" + right(42, 0) + ">", "<" + left(real32(0.5), 0) + ">"'), ["<42> <0.5>"])

    def test_repeat(self):
        self.assertEqual(out('  print repeat("-", 5) + "|", repeat("ab", 0) + "x"'), ["-----| x"])

    def test_wrong_arguments(self):
        fails(self, '  print fixed("a", 2)', "needs a real or integer number")
        fails(self, '  print fixed(1.0, -1)', "number of decimals")
        fails(self, '  print right("a", 3, "ab")', "one-character")
        fails(self, '  print repeat(3, 2)', "needs a string")


class ConvertFormatTests(unittest.TestCase):
    def text(self, body, decls="  integer :: n\n  real(8) :: x\n  character(len=5) :: s\n  integer :: v(3)\n"):
        text, findings = conv(body, decls)
        return text, findings

    def test_descriptors(self):
        text, findings = self.text("  write(*,'(A,I5,F8.3,A5,I3.3)') 'n=', n, x, s, n")
        self.assertEqual(todos(findings), [])
        self.assertIn('print "n=" + right(n, 5) + fixed(x, 3, 8) + right(s, 5) + right(n, 3, "0")', text)

    def test_f0_and_i0_width(self):
        text, _ = self.text("  write(*,'(F0.2)') x")
        self.assertIn("print fixed(x, 2)", text)

    def test_x_literals_slash_and_repeat_groups(self):
        text, _ = self.text("  write(*,'(2(A,X),/,A)') 'a', 'b', 'c'")
        self.assertEqual([l.strip() for l in text.splitlines() if l.strip().startswith("print")],
                         ['print "a b"', 'print "c"'])

    def test_label_format_before_and_after_the_write(self):
        text, findings = self.text("  write(*,5) n\n5 format('n:', I3)\n  write(*,5) n + 1")
        self.assertEqual(todos(findings), [])
        self.assertEqual(text.count('print "n:" + right('), 2)
        self.assertNotIn("format", text.split("program")[1])

    def test_implied_do_and_arrays_under_a_format(self):
        text, findings = self.text("  write(*,'(3I3)') (v(n), n = 1, 3)\n  write(*,'(3I3)') v")
        self.assertEqual(todos(findings), [])
        self.assertEqual(text.count("right(v(1), 3) + right(v(2), 3) + right(v(3), 3)"), 2)

    def test_more_values_than_descriptors_start_a_new_line(self):
        text, _ = self.text("  write(*,'(I3)') 1, 2, 3")
        self.assertEqual([l.strip() for l in text.splitlines() if l.strip().startswith("print")],
                         ["print right(1, 3)", "print right(2, 3)", "print right(3, 3)"])

    def test_advance_no_is_a_write(self):
        text, _ = self.text("  write(*,'(A)',advance='no') 'x'")
        self.assertIn('write "x"', text)

    def test_unsupported_descriptors_are_todos(self):
        for fmt in ("E10.3", "G12.4", "L2", "T5", "ES10.2", "I"):
            with self.subTest(fmt=fmt):
                _, findings = self.text(f"  write(*,'({fmt})') n")
                self.assertEqual(len(todos(findings)), 1)

    def test_a_group_that_would_repeat_is_a_todo(self):
        _, findings = self.text("  write(*,'(2(I3))') 1, 2, 3, 4, 5")
        self.assertEqual(len(todos(findings)), 1)

    def test_print_with_a_format(self):
        text, findings = self.text("  print '(A,F4.1)', 'x=', x")
        self.assertEqual(todos(findings), [])
        self.assertIn('print "x=" + fixed(x, 1, 4)', text)


class FormatFixtureTests(unittest.TestCase):
    check = FixtureTests.check

    def test_format_fixture_matches_gfortran(self):
        got = self.check("format.f90")
        self.assertEqual(got[0], "n =   42")


if __name__ == "__main__":
    unittest.main()
