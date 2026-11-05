import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, fixture, read, run_bor, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.parser import parse


def run(expr):
    out = []
    Interp(parse(f"program t\n  println({expr})\nend program t\n"), out.append).run()
    return out[0].rstrip("\n")


class TfTests(unittest.TestCase):
    def test_letters(self):
        self.assertEqual(run("tf(true) + tf(false)"), "TF")
        self.assertEqual(run('"|" + right(tf(3 > 2), 5) + "|" + left(tf(false), 3) + "|"'), "|    T|F  |")

    def test_needs_a_logical(self):
        with self.assertRaisesRegex(Exception, "needs a logical"):
            run("tf(1)")

    def test_right_and_left_take_logicals(self):
        self.assertEqual(run("right(true, 6)"), "  true")
        self.assertEqual(run("left(false, 7) + \"|\""), "false  |")


class ConvertTests(unittest.TestCase):
    def test_l_descriptor_converts(self):
        text, findings = convert(read(fixture("logical.f90")), "logical.f90")
        self.assertEqual(todos(findings), [])
        self.assertIn("right(tf(a), 5)", text)

    @unittest.skipUnless(GFORTRAN, "gfortran not installed")
    def test_output_is_what_gfortran_prints(self):
        path = fixture("logical.f90")
        text, _ = convert(read(path), "logical.f90")
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "a.out")
            subprocess.run([GFORTRAN, "-w", path, "-o", exe], check=True, cwd=tmp)
            want = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(run_bor(text), want)


if __name__ == "__main__":
    unittest.main()
