import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, check_syntax, conv, fixture, read, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output


def run(body, stdin="", extra=""):
    out = []
    code = Interp(parse(f"{extra}program t\n{body}\nend program\n"), out.append, stdin=stdin).run()
    return out, code


def fails(test, body, message, stdin="", extra=""):
    with test.assertRaises(LangError) as cm:
        run(body, stdin, extra)
    test.assertIn(message, str(cm.exception))


class ReadTests(unittest.TestCase):
    """Input is a line from `read_line()`, cut up by `fields` and turned into numbers by `to_*`."""

    def test_reads_values_by_type(self):
        body = ("  allocatable string :: p(:)\n  p = fields(read_line() ?? \"\")\n"
                "  integer :: n = to_integer(p(1))\n  real :: x = to_real(p(2))\n  logical :: b = to_logical(p(3))\n"
                "  string :: s = p(4)\n  real32 :: y = real32(to_real(p(5)))\n  println(n, x, b, s, y)")
        self.assertEqual(run(body, "7 2.5d0 T hello 0.1\n")[0], ["7 2.5 true hello 0.1"])

    def test_commas_separate_values_when_asked(self):
        body = ("  allocatable string :: p(:)\n  p = fields(read_line() ?? \"\", \",\")\n  println(size(p), p(1), to_integer(p(2)))")
        self.assertEqual(run(body, "two, 5\n")[0], ["2 two 5"])

    def test_bad_input_stops_the_program(self):
        fails(self, "  integer :: n = to_integer(\"abc\")", "cannot assign null to integer")
        fails(self, "  real :: x = to_real(\"1_0\")", "cannot assign null to real")
        fails(self, "  integer :: n = to_integer(\"3.5\")", "cannot assign null to integer")

    def test_end_of_input_gives_null(self):
        self.assertEqual(run("  println(present(read_line()))", "")[0], ["false"])
        fails(self, "  allocatable string :: p(:)\n  p = fields(read_line())", "needs a string", "")

    def test_the_old_read_statement_is_gone(self):
        for src in ("integer :: n\nread n", "integer :: n\nupdate read n", "integer :: n\nread n status n"):
            with self.assertRaises(LangError):
                parse(f"program t\n{src}\nend program t\n")

    def test_read_line(self):
        body = ("  string? :: s\n  s = read_line()\n  println(s)\n  s = read_line()\n  println(s ?? 'none')\n"
                "  s = read_line()\n  println(present(s))")
        self.assertEqual(run(body, "hello there\n\n")[0], ["hello there", "", "false"])


class WriteAndStopTests(unittest.TestCase):
    def test_write_prints_without_a_newline(self):
        out, _ = run("  print('a')\n  print('b', 1)\n  println('c')\n  print('tail')")
        self.assertEqual(out, ["ab 1c", "tail"])

    def test_stop_with_and_without_a_code(self):
        self.assertEqual(run("  println(1)\n  stop\n  println(2)"), (["1"], 0))
        self.assertEqual(run("  println(1)\n  stop 4\n  println(2)"), (["1"], 4))

    def test_stop_inside_a_procedure(self):
        extra = "subroutine quit()\n  stop 3\nend subroutine\n"
        self.assertEqual(run("  call quit()\n  println(1)", extra=extra), ([], 3))

    def test_stop_code_must_be_an_integer(self):
        fails(self, "  stop 'x'", "exit code")

    def test_single_quoted_strings(self):
        self.assertEqual(run("  println('say \"hi\"', \"it\")")[0], ['say "hi" it'])
        src = "program t\n  println('say \"hi\"', \"plain\")\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)
        from boreas.parser import Print, Str
        with self.assertRaises(LangError):                    # both kinds of quote cannot be written
            from boreas.printer import expr
            expr(Str("it's \"odd\""))

    def test_round_trip_of_new_statements(self):
        src = ("subroutine get(out integer :: n)\n  update n = 1\nend subroutine\n\n"
               "program t\n  integer :: a\n  print(\"x\")\n  stop 2\nend program t\n")
        self.assertEqual(to_source(parse(src)), src)


def run_converted(text, stdin=""):
    out = []
    code = Interp(parse(text), out.append, stdin=stdin).run()
    return out, code


class ConvertTests(unittest.TestCase):
    def test_read_forms(self):
        text, findings = convert(
            "program p\n  integer :: a, b, ios\n  read(*,*) a\n  read *, a, b\n  read(5,*,iostat=ios) a\n"
            "  stop 'bye'\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn('read_parts = fields(read_line() ?? "", ",")\n  a = to_integer(read_parts(1))\n', text)
        self.assertIn("  b = to_integer(read_parts(2))\n", text)
        self.assertIn("ios = -1", text)
        self.assertEqual(len([f for f in findings if f[1] == "note" and f[2].startswith("read:")]), 1)

    def test_read_of_arrays_and_sections_are_todos(self):
        for body in ("  read(*,*) v", "  read(*,*) v(1:2)", "  read(*,*,iostat=ios) a, v"):
            with self.subTest(body=body):
                _, findings = convert(f"program p\n  integer :: a, b, ios, v(3)\n{body}\nend program\n")
                self.assertEqual(len(todos(findings)), 1)

    def test_several_values_with_iostat_run_as_fortran_would(self):
        src = ("program p\n  integer :: a, b, ios\n  real :: x\n  a = 0\n  b = 0\n"
               "  read(*,*,iostat=ios) a, b\n  print *, a, b, ios\n"
               "  read(*,*,iostat=ios) a, b\n  print *, a, b, ios\n"
               "  read(*,*,iostat=ios) a, x\n  print *, a, x, ios\n"
               "  read(*,*,iostat=ios) a, b\n  print *, ios\nend program\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        out, _ = run_converted(text, "3, 4\n5 zz\n7 2.5\n")
        self.assertEqual(out, ["3 4 0", "3 4 1", "7 2.5 0", "-1"])

    def test_formatted_read_is_a_todo(self):
        _, findings = convert("program p\n  integer :: a\n  read(*,'(I5)') a\nend program\n")
        self.assertEqual(len(todos(findings)), 1)

    def test_read_into_a_dummy_argument_is_an_out_parameter_marked_update(self):
        text, findings = convert(
            "subroutine get(n)\n  integer :: n\n  read(*,*) n\nend subroutine\n"
            "program p\n  integer :: k\n  call get(k)\n  print *, k\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("subroutine get(out integer :: n)", text)
        self.assertIn("update n = to_integer(read_parts(1))", text)

    def test_stop_forms(self):
        text, _ = convert("program p\n  integer :: i\n  i = 1\n  if (i == 1) stop 3\n  print *, i\nend program\n")
        self.assertIn("stop 3", text)

    def test_a_string_with_both_kinds_of_quote_is_a_todo(self):
        _, findings = convert("program p\n  print *, 'say \"it'\'\'\'s\"'\nend program\n")
        self.assertEqual(len(todos(findings)), 1)

    def test_strings_with_quotes_convert(self):
        text, findings = convert("program p\n  print *, 'say \"hi\"'\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("'say \"hi\"'", text)


@unittest.skipUnless(GFORTRAN, "gfortran not installed")
class ReadFixtureTests(unittest.TestCase):
    def test_read_fixture_matches_gfortran_with_the_same_input(self):
        path, stdin = fixture("read.f90"), read(fixture("read.in"))
        check_syntax(path)
        text, findings = convert(read(path), "read.f90")
        self.assertEqual(todos(findings), [])
        out = []
        code = Interp(parse(text), out.append, stdin=stdin).run()
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "a.out")
            subprocess.run([GFORTRAN, os.path.abspath(path), "-o", exe], check=True, capture_output=True, cwd=tmp)
            r = subprocess.run([exe], input=stdin, capture_output=True, text=True)
        self.assertTrue(same_output("\n".join(out), r.stdout), f"\nBoreas:\n{chr(10).join(out)}\n\ngfortran:\n{r.stdout}")
        self.assertEqual(code, r.returncode)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
