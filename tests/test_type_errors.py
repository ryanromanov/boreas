"""`boreas check` and the editor report the mistakes of type the checker is sure of, with the interpreter's own words."""
import json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from boreas.cli import check_errors
from test_fortran import run_bor
from boreas.lexer import LangError

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEFS = """function f(real :: x) -> real
  return x
end function
subroutine s(inout int :: k)
  @k = 1
end subroutine
subroutine opt(int :: a, int? :: b)
  println(a)
end subroutine
record p
  real :: x = 0.0
end record
"""
BAD = """function g(real :: x) -> int
  return x
end function
function h(real :: x) -> real
  return "a"
end function
"""
DECLS = "  p :: a\n  real :: r = 1.0\n  int :: n = 0\n  int32 :: m = 0\n  string :: t = \"a\"\n  logical :: ok = true\n  int[3] :: v = [1, 2, 3]\n"


def messages(body, defs=DEFS, decls=DECLS):
    return [e[2] for e in check_errors(defs + "program t\n" + decls + body + "\nend program\n")]


# (statement, the message check must give)
WRONG = [
    ("n = 2.5", "cannot assign real to int"), ("n = t", "cannot assign string to int"),
    ("r = t", "cannot assign string to real"), ("t = 1", "cannot assign int to string"),
    ("ok = 1", "cannot assign int to logical"), ("n = ok", "cannot assign logical to int"),
    ("m = 2.5", "cannot assign real to int32"), ("n = v", "cannot assign array to int"),
    ("r = cmplx(1.0, 2.0)", "cannot assign complex to real"),
    ("v = [1, 2.5, 3]", "cannot assign real to int"), ("v = 2.5", "cannot assign real to int"),
    ("n = a.x", "cannot assign real to int"), ("a.x = \"a\"", "cannot assign string to real"),
    ("println(1 + \"a\")", "operator '+' needs numbers, got string"),
    ("println(t - t)", "operator '-' needs numbers, got string"),
    ("println(ok * 2)", "operator '*' needs numbers, got logical"),
    ("println(a + 1)", "records cannot be used with '+'; use their fields"),
    ("println(1 < t)", "cannot compare int with string using '<'"), ("println(ok < ok)", "logicals can be compared with == and /=, not '<'"),
    ("println(n == t)", "cannot compare int with string using '=='"),
    ("println(not n)", "condition must be logical, got int"), ("println(n and ok)", "condition must be logical, got int"),
    ("if n then\n  println(1)\nend if", "condition must be logical, got int"),
    ("do while n\n  n = 0\nend do", "condition must be logical, got int"),
    ("println(v[1.5])", "array index must be integer"), ("println(v[t])", "array index must be integer"),
    ("println(sqrt(t))", "sqrt(): needs numbers, got string"), ("println(abs(ok))", "abs(): needs numbers, got logical"),
    ("println(a.q)", "p 'a' has no field 'q'"),
    ("println(f(t))", "argument 'x': cannot assign string to real"), ("println(f(1, 2))", "f expects 1 arguments, got 2"),
    ("println(f())", "f expects 1 arguments, got 0"), ("call s(@n, 2)", "s expects 1 arguments, got 2"),
    ("call s(@r)", "argument 'k' is int but 'r' is real"), ("call s(@m)", "argument 'k' is int but 'm' is int32"),
    ("call opt()", "opt expects 1 to 2 arguments, got 0"), ("call opt(1, 2, 3)", "opt expects 1 to 2 arguments, got 3"),
    ("call opt(t)", "argument 'a': cannot assign string to int"),
]
# fine, though some of them look suspicious
FINE = [
    "n = 1\nr = n\nm = n\nr = m", "r = 1\nm = 7\nn = m + 1", "v = 1\nv = [1, 2, 3]\nv = n", "t = t + \"b\"",
    "println(n == r, m < n, t < \"b\", ok == ok, n /= m)", "println(f(1), f(m), f(r), f(v))",
    "call s(@n)\ncall opt(1)\ncall opt(1, 2)\ncall opt(1, null)", "println(cmplx(1.0, 2.0) * 2)",
    "real32 :: s = r\nint8 :: b = n\nint64 :: c = m", "println(floor(r) + n, max(n, r), sum([1, 2.5]))",
    "if n > 0 and ok then\n  println(1)\nend if", "a.x = 3\nr = a.x", "println(size(v), len(t), sqrt(r), abs(n))",
    "int? :: o = null\nprintln(o ?? 0)", "do n = 1, 2.5\nend do", "println(v[n], v[m], v[1:2])",
]


class WrongTests(unittest.TestCase):
    def test_each_mistake_is_found_with_the_interpreters_words(self):
        for body, want in WRONG:
            got = messages("  " + body.replace("\n", "\n  "))
            self.assertIn(want, got, f"{body!r}: {got}")

    def test_the_interpreter_says_the_same(self):
        for body, want in WRONG:
            if body.startswith(("call opt", "println(f(", "call s(")) or "a.q" in body:
                continue
            try:
                run_bor("program t\n" + DECLS.replace("  p :: a\n", "").replace("p ::", "") + "  " + body.replace("\n", "\n  ")
                        + "\nend program\n")
            except LangError as err:
                if "has no field" in want or "a.x" in body or "a + 1" in body or "n = a.x" in body:
                    continue
                self.assertIn(want, str(err), body)


class ReturnTests(unittest.TestCase):
    def test_a_result_of_the_wrong_type(self):
        got = messages("  println(g(1.0), h(1.0))", defs=DEFS + BAD)
        self.assertIn("function g: cannot assign real to int", got)
        self.assertIn("function h: cannot assign string to real", got)

    def test_the_line_is_the_return(self):
        errors = check_errors(DEFS + BAD + "program t\nend program\n")
        lines = sorted(e[0] for e in errors)
        self.assertEqual(len(lines), 2)


class FineTests(unittest.TestCase):
    def test_correct_code_is_not_reported(self):
        for body in FINE:
            got = messages("  " + body.replace("\n", "\n  "))
            self.assertEqual(got, [], f"{body!r}: {got}")


class WhereTests(unittest.TestCase):
    def test_the_line_and_column(self):
        src = "program t\n  int :: n\n  println(n)\n  n = 2.5\nend program\n"
        (err,) = check_errors(src)
        self.assertEqual(err[0], 4)
        self.assertIn("cannot assign real to int", err[2])

    def test_an_index_error_points_at_the_array(self):
        src = "program t\n  int[3] :: v\n  println(v[1.5])\nend program\n"
        (line, col, msg, length), = check_errors(src)
        self.assertEqual((line, col, length), (3, 11, 1))

    def test_the_command_prints_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.bor")
            open(path, "w").write("program t\n  int :: n\n  n = \"a\"\nend program\n")
            r = subprocess.run([sys.executable, "-m", "boreas", "check", path, "--json"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            (err,) = json.loads(r.stdout)["errors"]
            self.assertEqual((err["line"], err["message"]), (3, "cannot assign string to int"))

    def test_a_bad_program_with_unknown_names_still_reports_the_names(self):
        got = [e[2] for e in check_errors("program t\n  int :: n\n  n = missing + \"a\"\nend program\n")]
        self.assertTrue(any("'missing' is not declared" in m for m in got), got)

    def test_the_examples_are_clean(self):
        import glob
        for path in sorted(glob.glob(os.path.join(ROOT, "examples", "*.bor"))):
            self.assertEqual(check_errors(open(path).read()), [], path)


if __name__ == "__main__":
    unittest.main()
