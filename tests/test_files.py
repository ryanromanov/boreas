import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, run_bor, todos
from boreas.fortran import convert_files

ROOT = os.path.join(os.path.dirname(__file__), "..")
MAIN = "      PROGRAM M\n      INTEGER TWICE\n      CALL HELLO(3)\n      PRINT *, TWICE(4)\n      END\n"
LIB = ("subroutine hello(n)\n  integer, intent(in) :: n\n  print *, 'hello', n\nend subroutine\n"
       "function twice(n)\n  integer :: twice, n\n  twice = 2 * n\nend function\n")


class ManyFilesTests(unittest.TestCase):
    def test_files_make_one_program(self):
        text, findings, where = convert_files([("main.f", MAIN), ("lib.f90", LIB)])
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["hello 3", "8"])

    def test_alone_the_main_file_has_unknown_calls(self):
        _, findings, _ = convert_files([("main.f", MAIN)])
        self.assertGreaterEqual(len(todos(findings)), 1)

    def test_findings_point_into_the_right_file(self):
        bad = "subroutine odd()\n  integer a, b\n  equivalence (a, b)\nend subroutine\n"
        _, findings, where = convert_files([("main.f", MAIN), ("lib.f90", LIB), ("odd.f90", bad)])
        line = next(f[0] for f in findings if "equivalence" in f[2])
        self.assertEqual(where(line), (2, 3))
        self.assertEqual(where(1), (0, 1))

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, text in (("main.f", MAIN), ("lib.f90", LIB)):
                paths.append(os.path.join(tmp, name))
                with open(paths[-1], "w") as f:
                    f.write(text)
            r = subprocess.run([sys.executable, "-m", "boreas", "convert", *paths], capture_output=True, text=True,
                               cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("subroutine hello", r.stdout)
            self.assertIn("0 TODO(s)", r.stderr)
            if GFORTRAN:
                r = subprocess.run([sys.executable, "-m", "boreas", "convert", "--verify", *paths],
                                   capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("verified", r.stderr)


if __name__ == "__main__":
    unittest.main()
