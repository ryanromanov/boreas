"""Built-in errors speak Boreas, not Python, and a runtime error says which line it happened on."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError


def message(body, defs=""):
    try:
        run_bor(defs + "program t\n" + body + "\nend program\n")
    except LangError as err:
        return str(err)
    raise AssertionError("no error")


class WordingTests(unittest.TestCase):
    def test_a_wrong_number_of_arguments(self):
        self.assertIn("sqrt() takes 1 argument, got 0", message("  println(sqrt())"))
        self.assertIn("sqrt() takes 1 argument, got 2", message("  println(sqrt(1.0, 2.0))"))
        self.assertIn("size() takes 1 to 2 arguments, got 0", message("  println(size())"))
        self.assertIn("atan2() takes 2 arguments, got 1", message("  println(atan2(1.0))"))
        self.assertIn("sum() takes 1 to 2 arguments, got 3", message("  println(sum([1, 2], 1, 1))"))

    def test_out_of_range_arguments(self):
        self.assertIn("log(-1.0) has no real answer", message("  println(log(-1.0))"))
        self.assertIn("exp(710.0) is too large for a real", message("  println(exp(710.0))"))
        self.assertIn("mod(5, 0): division by zero", message("  println(mod(5, 0))"))

    def test_no_python_wording_survives(self):
        bad = ("<locals>", "positional", "object at 0x", "instances of", "unsupported operand", "iterable", "math domain")
        for body in ("sqrt()", "abs()", "achar([1, 2, 3])", 'left(0, [1, 2, 3])', 'max("a", 1)', "log(0)",
                     "gamma(0.0)", "cos(infinity())", 'dim("abc", 0)', "hypot()", "findloc([1, 2])", "cshift([1])",
                     "fixed(0, [1, 2, 3])", "mod(1.0, 0.0)"):
            text = message(f"  println({body})")
            for word in bad:
                self.assertNotIn(word, text, f"{body}: {text}")

    def test_arrays_in_messages_say_their_shape(self):
        self.assertIn("got an array of shape (3)", message("  println(achar([1, 2, 3]))"))


class LineTests(unittest.TestCase):
    def test_runtime_errors_name_their_line(self):
        for body, line in (("  int32 :: i = 2147483647\n  i = i + 1", 3),
                           ("  int :: a = 5\n  int :: b = 0\n  println(a / b)", 4),
                           ("  println(1 + \"a\")", 2),
                           ("  int[3] :: a = [1, 2, 3]\n  println(a + [1, 2])", 3),
                           ("  println(undefined_name)", 2),
                           ("  int :: x = 1\n  if x == 1 then\n    println(1.0 / 0.0)\n  end if", 4),
                           ("  int :: x = 1\n  if 1 / (x - 1) == 1 then\n    println(1)\n  end if", 3)):
            self.assertTrue(message(body).startswith(f"line {line}:"), (body, message(body)))

    def test_an_error_inside_a_function_names_the_line_there(self):
        defs = "function inv(int :: n) -> int\n  return 1 / n\nend function\n"
        self.assertTrue(message("  println(inv(0))", defs).startswith("line 2:"), message("  println(inv(0))", defs))

    def test_a_line_is_added_only_once(self):
        self.assertNotIn("line 3: line", message("  int :: a = 1\n  println(a / 0)"))


if __name__ == "__main__":
    unittest.main()
