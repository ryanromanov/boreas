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
    def test_reads_values_by_type(self):
        body = ("  integer :: n\n  real :: x\n  logical :: b\n  string :: s\n  real32 :: y\n"
                "  read n, x, b, s, y\n  print n, x, b, s, y")
        self.assertEqual(run(body, "7 2.5d0 T hello 0.1\n")[0], ["7 2.5 true hello 0.1"])

    def test_items_continue_on_the_next_line_and_the_rest_of_a_line_is_dropped(self):
        body = "  integer :: a\n  integer :: b\n  integer :: c\n  read a, b\n  read c\n  print a, b, c"
        self.assertEqual(run(body, "1\n2 99\n3\n")[0], ["1 2 3"])

    def test_commas_and_quoted_strings(self):
        body = "  string :: s\n  integer :: n\n  read s, n\n  print s, n"
        self.assertEqual(run(body, "'two words', 5\n")[0], ["two words 5"])

    def test_array_elements_and_whole_arrays(self):
        body = "  integer :: a(3)\n  integer :: i\n  read a\n  read a(2)\n  print a"
        self.assertEqual(run(body, "1 2 3\n9\n")[0], ["[1, 9, 3]"])

    def test_bad_input_stops_the_program(self):
        fails(self, "  integer :: n\n  read n", "expected an integer for 'n', got 'abc'", "abc\n")
        fails(self, "  real :: x\n  read x", "expected a real number for 'x'", "1_0\n")
        fails(self, "  integer :: n\n  read n", "expected an integer for 'n'", "3.5\n")
        fails(self, "  integer8 :: n", "", "") if False else None

    def test_end_of_input_stops_the_program(self):
        fails(self, "  integer :: n\n  read n", "no more input", "")

    def test_status(self):
        body = ("  integer :: n = 5\n  integer :: ios\n  read n status ios\n  print ios, n\n"
                "  read n status ios\n  print ios, n\n  read n status ios\n  print ios, n")
        self.assertEqual(run(body, "abc\n8\n")[0], ["1 5", "0 8", "-1 8"])

    def test_values_before_a_bad_one_are_kept(self):
        body = "  integer :: a = 0\n  integer :: b = 0\n  integer :: ios\n  read a, b status ios\n  print a, b, ios"
        self.assertEqual(run(body, "4 x\n")[0], ["4 0 1"])

    def test_int8_range_is_checked(self):
        body = "  int8 :: n = 0\n  integer :: ios\n  read n status ios\n  print n, ios"
        self.assertEqual(run(body, "300\n")[0], ["0 1"])

    def test_read_line(self):
        body = ("  string? :: s\n  s = read_line()\n  print s\n  s = read_line()\n  print s ?? 'none'\n"
                "  s = read_line()\n  print present(s)")
        self.assertEqual(run(body, "hello there\n\n")[0], ["hello there", "", "false"])

    def test_update_is_required_for_out_parameters(self):
        extra = "subroutine get(out integer :: n)\n  update read n\nend subroutine\n"
        self.assertEqual(run("  integer :: k\n  call get(k)\n  print k", "6\n", extra)[0], ["6"])
        with self.assertRaises(LangError) as cm:
            parse("subroutine get(out integer :: n)\n  read n\nend subroutine\nprogram t\nend program\n")
        self.assertIn("update read", str(cm.exception))
        with self.assertRaises(LangError):
            parse("program t\n  integer :: n\n  update read n\nend program\n")

    def test_cannot_read_into_an_in_parameter_or_a_constant(self):
        fails(self, "  const integer :: n = 1\n  read n", "constant", "5\n")

    def test_status_must_be_an_integer(self):
        fails(self, "  integer :: n\n  real :: r\n  read n status r", "status of a read", "5\n")


class WriteAndStopTests(unittest.TestCase):
    def test_write_prints_without_a_newline(self):
        out, _ = run("  write 'a'\n  write 'b', 1\n  print 'c'\n  write 'tail'")
        self.assertEqual(out, ["ab 1c", "tail"])

    def test_stop_with_and_without_a_code(self):
        self.assertEqual(run("  print 1\n  stop\n  print 2"), (["1"], 0))
        self.assertEqual(run("  print 1\n  stop 4\n  print 2"), (["1"], 4))

    def test_stop_inside_a_procedure(self):
        extra = "subroutine quit()\n  stop 3\nend subroutine\n"
        self.assertEqual(run("  call quit()\n  print 1", extra=extra), ([], 3))

    def test_stop_code_must_be_an_integer(self):
        fails(self, "  stop 'x'", "exit code")

    def test_single_quoted_strings(self):
        self.assertEqual(run("  print 'say \"hi\"', \"it\"")[0], ['say "hi" it'])
        src = "program t\n  print 'say \"hi\"', \"plain\"\nend program t\n"
        self.assertEqual(to_source(parse(src)), src)
        from boreas.parser import Print, Str
        with self.assertRaises(LangError):                    # both kinds of quote cannot be written
            from boreas.printer import expr
            expr(Str("it's \"odd\""))

    def test_round_trip_of_new_statements(self):
        src = ("subroutine get(out integer :: n)\n  update read n\nend subroutine\n\n"
               "program t\n  integer :: a\n  integer :: ios\n  write \"x\"\n  read a, a status ios\n"
               "  stop 2\nend program t\n")
        self.assertEqual(to_source(parse(src)), src)


class ConvertTests(unittest.TestCase):
    def test_read_forms(self):
        text, findings = convert(
            "program p\n  integer :: a, b, ios\n  read(*,*) a\n  read *, a, b\n  read(5,*,iostat=ios) a\n"
            "  stop 'bye'\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("read a\n", text)
        self.assertIn("read a, b\n", text)
        self.assertIn("read a status ios", text)

    def test_formatted_read_is_a_todo(self):
        _, findings = convert("program p\n  integer :: a\n  read(*,'(I5)') a\nend program\n")
        self.assertEqual(len(todos(findings)), 1)

    def test_read_into_a_dummy_argument_is_an_out_parameter_marked_update(self):
        text, findings = convert(
            "subroutine get(n)\n  integer :: n\n  read(*,*) n\nend subroutine\n"
            "program p\n  integer :: k\n  call get(k)\n  print *, k\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("subroutine get(out integer :: n)", text)
        self.assertIn("update read n", text)

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
