import io, os, shutil, subprocess, sys, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas import cli, verify

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")
NEEDS_GFORTRAN = unittest.skipUnless(shutil.which("gfortran"), "gfortran is not installed")


def boreas(*args):
    return subprocess.run([sys.executable, "-m", "boreas", *args], capture_output=True, text=True,
                          cwd=ROOT)


def write(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w") as f:
        f.write(text)
    return path


def run_cli(*args):
    """Call the CLI in-process; returns (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    status = 0
    with mock.patch.object(sys, "argv", ["boreas", *args]), redirect_stdout(out), redirect_stderr(err):
        try:
            cli.main()
        except SystemExit as e:
            status = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                err.write(e.code)
    return status, out.getvalue(), err.getvalue()


class NormalizerTests(unittest.TestCase):
    def test_tolerance_and_spelling(self):
        self.assertTrue(verify.same_output(" 1 T 2.0\n", "1, true, 2.0000000001"))
        self.assertTrue(verify.same_output("[1, 2]", "1 2"))
        self.assertFalse(verify.same_output("0.300000012", "0.30000000000000004"))
        self.assertFalse(verify.same_output("1 2", "1 2 3"))

    def test_locate_difference(self):
        text = verify.describe_difference("a b\nc 5\n", "a b\nc 6\n")
        self.assertIn("value 4", text)
        self.assertIn("line 2, value 2", text)


@NEEDS_GFORTRAN
class VerifyTests(unittest.TestCase):
    def test_matching_programs(self):
        for path in ("examples/number_toys.f90", "tests/fortran/basic.f90"):
            with self.subTest(path=path):
                r = boreas("convert", "--verify", path)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("verified: the converted program printed the same output as the original (",
                              r.stderr)

    def test_options_in_any_order_and_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "o.bor")
            r = boreas("convert", "-o", out, "--timeout", "20", "--verify", "tests/fortran/basic.f90")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            self.assertTrue(os.path.exists(out))

    def test_precision_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = write(tmp, "p.f90", "program p\n  implicit none\n  real :: x\n  x = 0.1\n"
                                    "  print *, x * 3\nend program p\n")
            r = boreas("convert", "--verify", f)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("MISMATCH", r.stderr)
            self.assertIn("0.300000012", r.stderr)
            self.assertIn("0.30000000000000004", r.stderr)
            self.assertIn("32-bit", r.stderr)
            self.assertNotIn("not translated", r.stderr)

    def test_todo_makes_mismatch_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = write(tmp, "t.f90", "program t\n  implicit none\n  integer :: n\n  n = 3\n"
                                    "  print *, n\n  stop\n  print *, n + 1\nend program t\n")
            r = boreas("convert", "--verify", f)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("TODO", r.stderr)
            self.assertIn("not translated", r.stderr)

    def test_original_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = write(tmp, "s.f90", "program s\n  implicit none\n  integer :: i\n  i = 0\n"
                                    "  do\n    i = i + 1\n  end do\nend program s\n")
            r = boreas("convert", "--verify", "--timeout", "1", f)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("did not finish within 1 s", r.stderr)

    def test_reading_stdin_does_not_hang(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = write(tmp, "r.f90", "program r\n  implicit none\n  integer :: n\n"
                                    "  read *, n\n  print *, n\nend program r\n")
            r = boreas("convert", "--verify", "--timeout", "5", f)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("tried to read input", r.stderr)

    def test_compile_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = write(tmp, "bad.f90", "program bad\n  implicit none\n  integer :: n\n"
                                      "  n = = 2\nend program bad\n")
            status, _, err = run_cli("convert", "--verify", f)
            self.assertEqual(status, 2)
            self.assertIn("could not compile the original with gfortran", err)

    def test_temp_files_cleaned_up(self):
        made = []
        real = tempfile.TemporaryDirectory

        class Spy(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                made.append(self.name)

        with mock.patch.object(verify.tempfile, "TemporaryDirectory", Spy):
            status, _, _ = run_cli("convert", "--verify", os.path.join(ROOT, "tests/fortran/basic.f90"))
        self.assertEqual(status, 0)
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))


class NoGfortranTests(unittest.TestCase):
    def test_missing_gfortran_still_converts(self):
        with mock.patch.object(verify, "find_gfortran", lambda: None):
            status, out, err = run_cli("convert", "--verify", os.path.join(ROOT, "tests/fortran/basic.f90"))
        self.assertEqual(status, 2)
        self.assertIn("program", out)
        self.assertIn("gfortran was not found", err)
        self.assertIn("converted ", err)

    def test_fc_variable_names_the_compiler(self):
        with mock.patch.dict(os.environ, {"FC": "no-such-compiler-xyz"}):
            self.assertIsNone(verify.find_gfortran())


class UsageTests(unittest.TestCase):
    def test_unknown_option_and_missing_file(self):
        for args in (("--bogus", "x.f90"), ("--verify",), ("--timeout", "3", "x.f90")):
            with self.subTest(args=args):
                status, _, err = run_cli("convert", *args)
                self.assertNotEqual(status, 0)
                self.assertIn("usage:", err)

    def test_bad_timeout(self):
        status, _, err = run_cli("convert", "--verify", "--timeout", "soon", "x.f90")
        self.assertNotEqual(status, 0)
        self.assertIn("--timeout needs", err)

    def test_plain_convert_unchanged(self):
        status, out, err = run_cli("convert", os.path.join(ROOT, "tests/fortran/basic.f90"))
        self.assertEqual(status, 0)
        self.assertIn("program", out)
        self.assertNotIn("verified", err)


if __name__ == "__main__":
    unittest.main()
