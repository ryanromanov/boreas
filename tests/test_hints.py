"""Hints for mistakes made out of habit from other languages, and `return` in a program."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError


def message(body):
    try:
        run_bor("program t\n" + body + "\nend program\n")
    except LangError as err:
        return str(err)
    raise AssertionError("no error: " + body)


IF = "  integer :: x = 1\n  if %s then\n    println(x)\n  end if"

CASES = [
    ('  print *, "hi"', "line 2:", "there is no 'print *'; write println"),
    ("  write(*, *) 1", "line 2:", "there is no 'write(*, *)'"),
    (IF % "x .eq. 1", "line 3:", "'.eq.' is Fortran's; write '=='"),
    (IF % "x == 1 .and. x > 0", "line 3:", "'.and.' is Fortran's; write 'and'"),
    (IF % "x .ne. 1", "line 3:", "write '/='"),
    ("  logical :: b = .true.", "line 2:", "'.true.' is Fortran's; write true"),
    (IF % "x != 1", "line 3:", "'!=' is cut off; not-equal is '/='"),
    (IF % "x <> 1", "line 3:", "not-equal is '/='"),
    (IF.replace("if %s then", "if x = 1 then"), "line 3:", "a condition compares with '=='"),
    (IF % "x == 1 && x > 0", "line 3:", "'&&' is C's; write 'and'"),
    (IF % "x == 1 || x > 0", "line 3:", "write 'or'"),
    ("  integer :: x = 7\n  println(x % 2)", "line 3:", "the remainder is mod(a, b)"),
    ("  println(2 ^ 3)", "line 2:", "power is '**'"),
    ('  println("a" // "b")', "line 2:", "join strings with '+'"),
    ("  integer :: x = 1\n  x += 1", "line 3:", "there is no '+='; write x = x + ..."),
    ("  integer :: x = 1\n  x++", "line 3:", "there is no '++'; write x = x + 1"),
    ("  integer :: x = 1\n  x := 2", "line 3:", "assignment is '=', not ':='"),
    ("  integer :: x = 1\n  x = 2;", "line 3:", "a statement ends at the end of the line"),
    ("  real*8 :: x", "line 2:", "real64, real32"),
    ("  integer :: x = 1\n  while x < 3\n    x = x + 1\n  end while", "line 3:", "do while cond"),
    ("  integer :: i\n  for i = 1, 3\n    println(i)\n  end for", "line 3:", "do i = 1, n"),
    ('  println("abc)', "line 2:", "unterminated string"),
    ("  integer :: x = 1\n  if x == 1 then\n    println(1)\n  endif", "line 5:", "'endif' is written 'end if' (two words)"),
    ("  integer :: i\n  do i = 1, 2\n    println(i)\n  enddo", "line 5:", "'enddo' is written 'end do'"),
    ("  integer :: x = 1\n  if x == 1 then\n    println(1)\n  elseif (x == 2) then\n    println(2)\n  end if",
     "line 5:", "'elseif' is written 'else if'"),
    ("  integer :: i\n  do i = 1, 3\n    continue\n  end do", "line 4:", "write `cycle`"),
    ("  println(1)\n  return", "line 3:", "'return' leaves a function or subroutine; to end the program early, write 'stop'"),
]


class HintTests(unittest.TestCase):
    def test_each_habit_gets_a_hint_on_the_right_line(self):
        for body, line, text in CASES:
            got = message(body)
            self.assertTrue(got.startswith(line), (body, got))
            self.assertIn(text, got, body)

    def test_a_misspelled_name_suggests_the_close_one(self):
        self.assertIn("did you mean 'count'?", message("  integer :: count = 1\n  println(cuont)"))
        self.assertIn("did you mean 'println'?", message('  printn("a")'))
        self.assertIn("did you mean 'sqrt'?", message("  println(sqr(4.0))"))

    def test_no_hint_when_nothing_is_close(self):
        self.assertNotIn("did you mean", message("  println(zzzzz)"))

    def test_return_in_a_nested_block_of_the_program(self):
        got = message("  integer :: x = 1\n  if x == 1 then\n    return\n  end if")
        self.assertIn("'return' leaves a function or subroutine", got)

    def test_return_in_a_function_still_works(self):
        src = "function f() -> integer\n  return 1\nend function\nprogram t\n  println(f())\nend program\n"
        self.assertEqual(run_bor(src), ["1"])

    def test_check_gives_the_hint_and_the_column(self):
        errors = check_errors("program t\n  integer :: x = 1\n  if x .eq. 1 then\n    println(x)\n  end if\nend program\n")
        self.assertTrue(errors and "Fortran's; write '=='" in errors[0][2], errors)
        self.assertEqual(errors[0][0], 3)

    def test_correct_programs_are_untouched(self):
        body = ("  integer[2, 2] :: m = [1, 2;\n                        3, 4]\n  integer :: x = 3\n"
                "  if x /= 1 and x > 0 then\n    println(m, x ** 2, mod(x, 2))\n  end if")
        self.assertEqual(run_bor("program t\n" + body + "\nend program\n"), ["[[1, 2], [3, 4]] 9 1"])


if __name__ == "__main__":
    unittest.main()
