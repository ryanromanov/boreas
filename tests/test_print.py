import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.cli import run_source
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def run(body):
    out = []
    run_source(f"program t\n{body}\nend program t\n", out.append)
    return out


class OutputTests(unittest.TestCase):
    def test_println_ends_the_line_and_print_does_not(self):
        self.assertEqual(run('  print("a")\n  print("b", 1)\n  println("c")\n  println("d")'), ["ab 1c", "d"])

    def test_empty_println_is_a_blank_line(self):
        self.assertEqual(run("  println()"), [""])

    def test_empty_print_is_an_error(self):
        for src in ("  print()", "  write(f)"):
            with self.assertRaisesRegex(LangError, "needs at least one value"):
                parse(f"program t\n{src}\nend program t\n")

    def test_write_takes_the_file_first(self):
        path = os.path.join(tempfile.mkdtemp(), "o.txt")
        run(f'  file :: f = open("{path}", "write")\n  write(f, "a", 1)\n  writeln(f, "b")\n  writeln(f)\n  close f')
        with open(path) as fh:
            self.assertEqual(fh.read(), "a 1b\n\n")

    def test_write_needs_a_file(self):
        with self.assertRaises(LangError):
            run("  write(1)\n  integer :: n")

    def test_old_statement_forms_are_gone(self):
        for src in ('print "a"', 'write "a"', 'print to f, "a"', 'println "a"'):
            with self.assertRaisesRegex(LangError, "expected '\\('"):
                parse(f"program t\n{src}\nend program t\n")

    def test_printer_round_trip(self):
        src = 'program t\n  file :: f = open("x", "write")\n  print("a")\n  println("b", 1)\n  write(f, "c")\n  writeln(f, "d")\n  println()\nend program t\n'
        text = to_source(parse(src))
        self.assertEqual(text, src)
