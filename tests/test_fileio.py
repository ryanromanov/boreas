import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "data.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def run_body(self, body, extra="", stdin=None):
        out = []
        src = f"{extra}program t\n{body.replace('PATH', self.path)}\nend program\n"
        Interp(parse(src), out.append, stdin=stdin).run()
        return out

    def fails(self, body, message, extra=""):
        with self.assertRaises(LangError) as cm:
            self.run_body(body, extra)
        self.assertIn(message, str(cm.exception))

    def write_file(self, text):
        with open(self.path, "w") as f:
            f.write(text)

    def test_write_then_read_back(self):
        body = ('  file :: f = open("PATH", "write")\n  writeln(f, "Range:", 3.5)\n  write(f, "x")\n'
                '  write(f, "y")\n  writeln(f, "")\n  close f\n'
                '  file :: g = open("PATH", "read")\n  string :: a\n  real :: r\n  read from g, a, r\n  println(a, r)\n'
                '  close g')
        self.assertEqual(self.run_body(body), ["Range: 3.5"])
        with open(self.path) as f:
            self.assertEqual(f.read(), "Range: 3.5\nxy\n")

    def test_append(self):
        self.write_file("one\n")
        self.run_body('  file :: f = open("PATH", "append")\n  writeln(f, "two")\n  close f')
        with open(self.path) as f:
            self.assertEqual(f.read(), "one\ntwo\n")

    def test_write_replaces_an_existing_file(self):
        self.write_file("old old old\n")
        self.run_body('  file :: f = open("PATH", "write")\n  writeln(f, "new")\n  close f')
        with open(self.path) as f:
            self.assertEqual(f.read(), "new\n")

    def test_read_status_and_end_of_file(self):
        self.write_file("1 2\nx\n")
        body = ('  file :: f = open("PATH", "read")\n  integer :: a\n  integer :: b\n  integer :: ios\n'
                '  read from f, a, b status ios\n  println(a, b, ios)\n  read from f, a status ios\n  println(ios)\n'
                '  read from f, a status ios\n  println(ios)')
        self.assertEqual(self.run_body(body), ["1 2 0", "1", "-1"])

    def test_end_of_file_without_status_stops(self):
        self.write_file("")
        self.fails('  file :: f = open("PATH", "read")\n  integer :: a\n  read from f, a', "end of file reached")

    def test_read_line_from_a_file(self):
        self.write_file("first line\nsecond\n")
        body = ('  file :: f = open("PATH", "read")\n  println(read_line(f))\n  println(read_line(f))\n'
                '  println(present(read_line(f)))')
        self.assertEqual(self.run_body(body), ["first line", "second", "false"])

    def test_the_terminal_is_unchanged(self):
        self.write_file("5\n")
        body = ('  file :: f = open("PATH", "read")\n  integer :: n\n  integer :: m\n  read n\n  read from f, m\n'
                '  println(n, m)')
        self.assertEqual(self.run_body(body, stdin="9\n"), ["9 5"])

    def test_missing_file(self):
        self.fails('  file :: f = open("PATH", "read")', "cannot open")

    def test_try_open_gives_null(self):
        body = '  file? :: f = try_open("PATH", "read")\n  println(present(f))\n  println(exists("PATH"))'
        self.assertEqual(self.run_body(body), ["false", "false"])

    def test_exists(self):
        self.write_file("")
        self.assertEqual(self.run_body('  println(exists("PATH"))'), ["true"])

    def test_wrong_mode_is_an_error(self):
        self.write_file("1\n")
        self.fails('  file :: f = open("PATH", "read")\n  writeln(f, 1)', "opened for read")
        self.fails('  file :: f = open("PATH", "write")\n  integer :: n\n  read from f, n', "opened for write")
        self.fails('  file :: f = open("PATH", "append")\n  integer :: n\n  read from f, n', "opened for append")

    def test_bad_mode(self):
        self.fails('  file :: f = open("PATH", "rw")', "open mode must be")

    def test_closed_and_unopened(self):
        self.write_file("1\n")
        self.fails('  file :: f = open("PATH", "read")\n  close f\n  integer :: n\n  read from f, n', "is closed")
        self.fails('  file :: f\n  writeln(f, 1)', "never opened")
        self.fails('  file :: f = open("PATH", "read")\n  close f\n  close f', "is closed")

    def test_files_are_not_numbers(self):
        self.write_file("")
        self.fails('  file :: f = open("PATH", "read")\n  integer :: n\n  n = f', "cannot assign file to integer")
        self.fails('  file :: f = 3', "cannot assign integer to file")

    def test_a_file_can_be_passed_to_a_subroutine(self):
        extra = "subroutine log_line(file :: f, string :: s)\n  writeln(f, s)\nend subroutine\n"
        self.run_body('  file :: f = open("PATH", "write")\n  call log_line(f, "hi")\n  close f', extra)
        with open(self.path) as f:
            self.assertEqual(f.read(), "hi\n")

    def test_a_file_in_a_module_variable(self):
        extra = ("module logging\n  public file :: log\n  public subroutine start(string :: name)\n"
                 "    update log = open(name, \"write\")\n  end subroutine\n"
                 "  public subroutine say(string :: s)\n    writeln(log, s)\n  end subroutine\nend module\n")
        self.run_body('  use logging: start, say, log\n  call start("PATH")\n  call say("one")\n  call say("two")\n  close log',
                      extra)
        with open(self.path) as f:
            self.assertEqual(f.read(), "one\ntwo\n")

    def test_files_are_closed_when_the_program_ends(self):
        self.run_body('  file :: f = open("PATH", "write")\n  writeln(f, "kept")')
        with open(self.path) as f:
            self.assertEqual(f.read(), "kept\n")

    def test_to_and_from_still_work_as_names(self):
        body = "  integer :: to = 1\n  integer :: from = 2\n  println(to, from)\n  println(to + from)"
        self.assertEqual(self.run_body(body), ["1 2", "3"])

    def test_round_trip(self):
        src = ('program t\n  file :: f = open("x", "write")\n  writeln(f, 1, "a")\n  write(f, 2)\n  close f\n'
               '  file :: g = open("x", "read")\n  integer :: n\n  integer :: ios\n  read from g, n status ios\n'
               '  read from g, n\n  close g\nend program t\n')
        printed = to_source(parse(src))
        self.assertEqual(to_source(parse(printed)), printed)
        self.assertIn('writeln(f, 1, "a")', printed)
        self.assertIn("read from g, n status ios", printed)


if __name__ == "__main__":
    unittest.main()
