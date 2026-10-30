import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, fixture, read, run_bor, run_gfortran, todos
from boreas.fixedform import fixed_to_free, is_fixed_form
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.verify import same_output


def body(code, decls="integer :: i, j, n"):
    """Convert free-form statements and run the result."""
    text, findings = convert(f"program t\n  implicit none\n  {decls}\n{code}\nend program t\n")
    parse(text)
    return text, findings, run_bor(text)


class FixedFormTests(unittest.TestCase):
    def test_columns(self):
        src = "C COMMENT\n  100 X = 1\n     +    + 2\n      Y = 3 ! TRAILING\n*ANOTHER\n"
        free = fixed_to_free(src).splitlines()
        self.assertEqual(free[0], "! COMMENT")
        self.assertTrue(free[1].startswith("100 "))
        self.assertTrue(free[1].endswith("&") and free[2].startswith("&"))
        self.assertEqual(free[4], "!ANOTHER")

    def test_text_past_column_72_is_ignored(self):
        line = "      X = 1" + " " * 62 + "SEQ00001"
        self.assertEqual(fixed_to_free(line).strip(), "X = 1")

    def test_tab_form(self):
        free = fixed_to_free("10\tX = 1\n\tY = 2\n\t1Z = 3\n")
        self.assertEqual([l.strip() for l in free.splitlines()], ["10 X = 1", "Y = 2 &", "& Z = 3"])

    def test_continuation_with_nothing_before_it(self):
        with self.assertRaises(LangError):
            fixed_to_free("     +X\n")

    def test_extensions(self):
        self.assertTrue(is_fixed_form("a.F") and is_fixed_form("a.for"))
        self.assertFalse(is_fixed_form("a.f90"))

    def test_fixture_matches_gfortran(self):
        path = fixture("labels.f")
        text, findings = convert(read(path), "labels.f")
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        self.assertEqual(got[0], "total 100")
        if GFORTRAN:
            self.assertTrue(same_output("\n".join(got), run_gfortran(path)))

    def test_continuation_lines_in_a_statement(self):
        text, findings = convert("      PROGRAM P\n      PRINT *, 'a',\n     +   'b'\n      END\n", "p.f")
        self.assertEqual(run_bor(text), ["a b"])


class LabelTests(unittest.TestCase):
    def test_labelled_do_with_continue_and_a_statement(self):
        _, f, out = body("  n = 0\n  do 10 i = 1, 3\n    n = n + i\n10 continue\n"
                         "  do 20 i = 1, 2\n20 n = n * 2\n  print *, n")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["24"])

    def test_shared_terminator(self):
        _, _, out = body("  n = 0\n  do 10 i = 1, 3\n  do 10 j = 1, 3\n10 n = n + 1\n  print *, n")
        self.assertEqual(out, ["9"])

    def test_loop_that_tests_at_the_top_becomes_do_while(self):
        text, _, out = body("  n = 1\n10 if (n > 50) goto 20\n  n = n * 3\n  goto 10\n20 continue\n  print *, n")
        self.assertIn("do while not n > 50", text)
        self.assertNotIn("again", text)
        self.assertEqual(out, ["81"])

    def test_jump_back_becomes_a_loop(self):
        text, _, out = body("  n = 0\n10 n = n + 1\n  if (n < 5) goto 10\n  print *, n")
        self.assertEqual(out, ["5"])
        self.assertIn("cycle", text)

    def test_jump_forward_leaves_nested_loops(self):
        text, _, out = body("  n = 0\n  do i = 1, 5\n    do j = 1, 5\n      n = n + 1\n"
                            "      if (n == 7) goto 30\n    end do\n  end do\n30 print *, n")
        self.assertIn("exit skip_30", text)
        self.assertEqual(out, ["7"])

    def test_crossing_jumps_make_a_state_machine(self):
        text, _, out = body("  n = 0\n  i = 0\n"
                            "10 i = i + 1\n  if (i > 6) goto 30\n  if (mod(i, 2) == 0) goto 20\n  n = n + 10\n  goto 10\n"
                            "20 n = n + 1\n  if (i < 100) goto 10\n30 print *, n, i")
        self.assertEqual(out, ["33 7"])

    def test_computed_goto_and_arithmetic_if(self):
        _, f, out = body("  do i = 1, 4\n    goto (10, 20), i\n    print *, 'other'\n    goto 30\n"
                         "10  print *, 'one'\n    goto 30\n20  print *, 'two'\n"
                         "30  if (i - 2) 31, 32, 33\n31  print *, '<'\n    goto 40\n32  print *, '='\n    goto 40\n"
                         "33  print *, '>'\n40  continue\n  end do")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["one", "<", "two", "=", "other", ">", "other", ">"])

    def test_a_goto_to_a_missing_label_is_a_todo(self):
        text, f = convert("program t\n  goto 99\nend program t\n")
        self.assertEqual(len(todos(f)), 1)
        self.assertIn("goto 99", text)
        parse(text)

    def test_labels_nobody_jumps_to_disappear(self):
        text, f, out = body("  n = 1\n10 continue\n  print *, n")
        self.assertEqual(out, ["1"])
        self.assertNotIn("10", text)


class ParameterAndStringTests(unittest.TestCase):
    def test_parameter_statement(self):
        _, f, out = body("  parameter (n = 4)\n  print *, n * 2", "integer :: n")
        self.assertEqual(out, ["8"])

    def test_assumed_length_character_parameter(self):
        _, f, out = body("  character*(*) s\n  parameter (s = 'abc')\n  print *, len(s), s", "")
        self.assertEqual(todos(f), [])
        self.assertEqual(out, ["3 abc"])

    def test_index(self):
        _, _, out = body("  print *, index('hello', 'll'), index('hello', 'z')")
        self.assertEqual(out, ["3 0"])

    def test_whole_line_read(self):
        text, f = convert("program t\n  character*3 w\n  integer ios\n  read (*, '(A)', iostat=ios) w\n"
                          "  print *, w, ios\nend program t\n")
        self.assertEqual(todos(f), [])
        from boreas.interp import Interp
        out = []
        Interp(parse(text), out.append, stdin="yesterday\n").run()
        self.assertEqual(out, ["yes 0"])
        out = []
        Interp(parse(text), out.append, stdin="").run()
        self.assertEqual(out, [" -1"])


if __name__ == "__main__":
    unittest.main()
