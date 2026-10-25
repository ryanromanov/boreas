"""`boreas check FILE [--json]`: parse and link without running."""
import json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.cli import check_source

ROOT = os.path.join(os.path.dirname(__file__), "..")
GOOD = 'program t\n  print "hi"\nend program\n'


def check(src, *flags):
    with tempfile.NamedTemporaryFile("w", suffix=".bor", delete=False) as f:
        f.write(src)
    try:
        p = subprocess.run([sys.executable, "-m", "boreas", "check", f.name, *flags],
                           capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    finally:
        os.unlink(f.name)
    return p, f.name


class CheckTests(unittest.TestCase):
    def test_clean_file_exits_0_and_is_silent(self):
        p, _ = check(GOOD)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_does_not_run_the_program(self):
        p, _ = check('program t\n  print "ran"\nend program\n')
        self.assertNotIn("ran", p.stdout)

    def test_parse_error_has_line_and_column(self):
        p, name = check("program t\n  integer :: i = 1\n  print i +\nend program\n")
        self.assertEqual(p.returncode, 1)
        self.assertTrue(p.stdout.startswith(f"{name}:3:12: expected an expression"), p.stdout)

    def test_lexer_error_has_column(self):
        p, name = check("program t\n  integer :: i = 1 @\nend program\n")
        self.assertTrue(p.stdout.startswith(f"{name}:2:20: unexpected character"), p.stdout)

    def test_link_time_error_is_reported(self):
        p, name = check("record r\n  integer :: a\n  integer :: a\nend record\n" + GOOD)
        self.assertEqual(p.returncode, 1)
        self.assertIn("has two fields called 'a'", p.stdout)

    def test_error_without_a_column_falls_back_to_column_1(self):
        p, name = check("function f()\nend function\n")
        self.assertEqual(p.returncode, 1)
        self.assertRegex(p.stdout, r":\d+:\d+: ")

    def test_json(self):
        p, name = check("program t\n  print 1 +\nend program\n", "--json")
        self.assertEqual(p.returncode, 1)
        d = json.loads(p.stdout)
        self.assertEqual(d["file"], name)
        self.assertEqual((d["errors"][0]["line"], d["errors"][0]["col"]), (2, 12))
        p, _ = check(GOOD, "--json")
        self.assertEqual((p.returncode, json.loads(p.stdout)["errors"]), (0, []))

    def test_missing_file_and_bad_usage(self):
        p = subprocess.run([sys.executable, "-m", "boreas", "check", "/nonexistent.bor"],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(p.returncode, 1)
        self.assertIn("error:", p.stderr)
        p = subprocess.run([sys.executable, "-m", "boreas", "check"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(p.returncode, 1)
        self.assertIn("usage", p.stderr)

    def test_check_source(self):
        self.assertIsNone(check_source(GOOD))
        line, col, msg = check_source("program t\n  print 1 +\nend program\n")
        self.assertEqual((line, col), (2, 12))


if __name__ == "__main__":
    unittest.main()
