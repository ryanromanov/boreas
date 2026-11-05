import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, fixture, read, run_bor, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse


def run(expr):
    out = []
    Interp(parse(f"program t\n  print {expr}\nend program t\n"), out.append).run()
    return out[0].rstrip("\n")


class ScientificTests(unittest.TestCase):
    def test_plain_style_has_a_nonzero_leading_digit(self):
        self.assertEqual(run("scientific(12345.678, 4)"), "1.2346E+04")
        self.assertEqual(run("scientific(0.000123456, 3)"), "1.235E-04")
        self.assertEqual(run("scientific(-1.5, 4)"), "-1.5000E+00")
        self.assertEqual(run("scientific(0.0, 4)"), "0.0000E+00")
        self.assertEqual(run("scientific(1e200, 4)"), "1.0000E+200")

    def test_width_pads_on_the_left_and_stars_when_too_narrow(self):
        self.assertEqual(run('"|" + scientific(12345.678, 4, 12) + "|"'), "|  1.2346E+04|")
        self.assertEqual(run("scientific(12345.678, 4, 8)"), "********")

    def test_fortran_style_matches_ew_d(self):
        self.assertEqual(run('scientific(12345.678, 4, 12, "fortran")'), "  0.1235E+05")
        self.assertEqual(run('scientific(-1.5, 4, 12, "fortran")'), " -0.1500E+01")
        self.assertEqual(run('scientific(0.0, 4, 12, "fortran")'), "  0.0000E+00")
        self.assertEqual(run('scientific(1e200, 4, 12, "fortran")'), "  0.1000+201")      # three-digit exponent: no E
        self.assertEqual(run('scientific(12345.678, 4, 9, "fortran")'), ".1235E+05")       # the leading 0 goes first
        self.assertEqual(run('scientific(-12345.678, 4, 9, "fortran")'), "*********")

    def test_integers_and_special_values(self):
        self.assertEqual(run("scientific(5, 2)"), "5.00E+00")
        self.assertEqual(run("scientific(-5, 2, 10)"), " -5.00E+00")

    def test_bad_arguments(self):
        for expr, msg in [('scientific(1.0, 2, 8, "c")', "style must be"), ("scientific(1.0, -1)", "whole number"),
                          ('scientific(1.0, 0, 8, "fortran")', "at least 1 digit"), ('scientific("a", 2)', "needs a real")]:
            with self.subTest(expr=expr), self.assertRaisesRegex(Exception, msg):
                run(expr)


class GeneralTests(unittest.TestCase):
    def test_plain_style_is_the_shorter_form(self):
        for expr, want in [("general(123.0, 4)", "123"), ("general(0.5, 4)", "0.5"), ("general(12345.678, 4)", "1.235E+04"),
                           ("general(0.05, 4)", "0.05"), ("general(0.00001234, 4)", "1.234E-05"), ("general(0, 4)", "0"),
                           ("general(-1.5, 4)", "-1.5"), ("general(1234.5, 4)", "1234"), ("general(1234.5, 5)", "1234.5")]:
            with self.subTest(expr=expr):
                self.assertEqual(run(expr), want)

    def test_fortran_style_matches_gw_d(self):
        for x, want in [("123.0", "   123.0    "), ("0.5", "  0.5000    "), ("-1.5", "  -1.500    "),
                        ("0.0", "   0.000    "), ("0.05", "  0.5000E-01"), ("12345.678", "  0.1235E+05"),
                        ("0.099996", "  0.1000    "), ("0.09999", "  0.9999E-01"), ("9999.5", "  0.1000E+05"),
                        ("9999.4", "   9999.    "), ("999.95", "   1000.    ")]:
            with self.subTest(x=x):
                self.assertEqual(run(f'general({x}, 4, 12, "fortran")'), want)

    def test_fortran_style_too_narrow(self):
        self.assertEqual(run('general(123.0, 4, 6, "fortran")'), "******")
        self.assertEqual(run('general(99999.5, 2, 8, "fortran")'), "0.10E+06")


class ConvertTests(unittest.TestCase):
    def test_e_and_g_descriptors_convert(self):
        text, findings = convert("program p\n  real :: x\n  x = 2.5\n  write(*,'(E12.4,G12.4)') x, x\nend program p\n")
        self.assertEqual(todos(findings), [])
        self.assertIn('scientific(x, 4, 12, "fortran") + general(x, 4, 12, "fortran")', text)

    def test_exponent_widths_stay_todos(self):
        for fmt in ("E12.4E3", "G12.4E3", "E12", "ES12.4"):
            with self.subTest(fmt=fmt):
                _, findings = convert(f"program p\n  real :: x\n  x = 2.5\n  write(*,'({fmt})') x\nend program p\n")
                self.assertEqual(len(todos(findings)), 1)

    @unittest.skipUnless(GFORTRAN, "gfortran not installed")
    def test_output_is_byte_for_byte_what_gfortran_prints(self):
        path = fixture("scientific.f90")
        text, findings = convert(read(path), "scientific.f90")
        self.assertEqual(todos(findings), [])
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "a.out")
            subprocess.run([GFORTRAN, "-w", path, "-o", exe], check=True, cwd=tmp)
            want = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual([l.rstrip("\n") for l in run_bor(text)], want)

    @unittest.skipUnless(GFORTRAN, "gfortran not installed")
    def test_many_values_match_gfortran(self):
        values = [0.0, 1.0, -1.0, 0.5, 0.05, 0.1, 0.099996, 0.09999, 9.99, 99.99, 999.95, 9999.4, 9999.5, 12345.678,
                  1.0e-10, 1.0e10, 123456789.0, 0.000123456, 2.5, 0.125, 0.375, 5.5, 1e100, 1.5e-100, 1e200, -3.14159]
        lines = ["program p", "  implicit none", "  real(8) :: v"]
        for v in values:
            lines += [f"  v = {v:.17e}".replace("e", "d"),
                      "  write(*,'(E14.5,G14.5,E10.2,G10.3,E20.10,G11.1)') v, v, v, v, v, v"]
        lines.append("end program p")
        src = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            f90 = os.path.join(tmp, "p.f90")
            with open(f90, "w") as fh:
                fh.write(src)
            subprocess.run([GFORTRAN, "-w", f90, "-o", os.path.join(tmp, "p")], check=True, cwd=tmp)
            want = subprocess.run([os.path.join(tmp, "p")], capture_output=True, text=True, check=True).stdout.splitlines()
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), want)


if __name__ == "__main__":
    unittest.main()
