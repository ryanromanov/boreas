import os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, fixture, read, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.parser import parse
from boreas.verify import same_output


def in_directory(path, work):
    old = os.getcwd()
    os.chdir(work)
    try:
        return work_result(path)
    finally:
        os.chdir(old)


def work_result(path):
    text, findings = convert(read(path), os.path.basename(path))
    out = []
    Interp(parse(text), out.append).run()
    return text, findings, out


class FileConversionTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = os.path.abspath(fixture("files.f90"))
        with tempfile.TemporaryDirectory() as tmp:
            text, findings, got = in_directory(path, tmp)
            self.assertEqual(todos(findings), [])
            self.assertEqual(got[0].strip(), "exists before: false")
            if GFORTRAN:
                with tempfile.TemporaryDirectory() as tmp2:
                    exe = os.path.join(tmp2, "a.out")
                    subprocess.run([GFORTRAN, path, "-o", exe], check=True, capture_output=True, cwd=tmp2)
                    want = subprocess.run([exe], check=True, capture_output=True, text=True, cwd=tmp2).stdout
                self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")

    def test_units_live_in_one_module(self):
        text, _ = convert(read(fixture("files.f90")), "files.f90")
        self.assertIn("module file_units", text)
        self.assertIn("update unit_10 = open(name, \"write\")", text.replace("trim(name)", "name"))

    def test_mode_comes_from_how_the_unit_is_used(self):
        src = ("program p\n  open(7, file='a.txt')\n  write(7, *) 1\n  close(7)\n"
               "  open(8, file='a.txt')\n  read(8, *) i\n  close(8)\n  open(9, file='a.txt', position='append')\n"
               "  write(9, *) 2\n  close(9)\nend program\n")
        text, findings = convert(src)
        self.assertIn('unit_7 = open("a.txt", "write")', text)
        self.assertIn('unit_8 = open("a.txt", "read")', text)
        self.assertIn('unit_9 = open("a.txt", "append")', text)

    def test_both_directions_is_a_todo(self):
        src = ("program p\n  open(7, file='a.txt')\n  write(7, *) 1\n  read(7, *) i\n  close(7)\nend program\n")
        _, findings = convert(src)
        self.assertTrue(any("both reading and writing" in f[2] for f in todos(findings)))

    def test_unsupported_forms_are_todos(self):
        for stmt in ("open(7, file='a', status='scratch')", "open(7, file='a', access='direct')",
                     "close(7, status='delete')", "inquire(unit=7, opened=ok)", "open(7)"):
            _, findings = convert(f"program p\n  logical ok\n  {stmt}\nend program\n")
            self.assertEqual(len(todos(findings)), 1, stmt)


if __name__ == "__main__":
    unittest.main()
