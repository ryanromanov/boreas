import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, fixture, read, run_bor, run_gfortran, todos
from boreas.fortran import convert
from boreas.verify import same_output


class CommonTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = fixture("common.f")
        text, findings = convert(read(path), "common.f")
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        self.assertEqual(len(got), 3)
        if GFORTRAN:
            self.assertTrue(same_output("\n".join(got), run_gfortran(path)), "\n".join(got))

    def test_block_becomes_a_module_of_variables(self):
        text, _ = convert(read(fixture("common.f")), "common.f")
        self.assertIn("module common_state", text)
        self.assertIn("public integer :: seed = 7", text)
        self.assertIn("use common_state: seed, count, table", text)
        self.assertIn("update seed = seed + step", text)
        self.assertIn("module common_blank", text)

    def test_function_that_changes_common_becomes_a_subroutine(self):
        text, _ = convert(read(fixture("common.f")), "common.f")
        self.assertIn("subroutine next(out integer :: next_result)", text)

    def test_different_shapes_are_a_todo(self):
        src = ("subroutine a()\n  integer x, y\n  common /c/ x, y\n  x = 1\nend subroutine\n"
               "subroutine b()\n  integer p\n  common /c/ p\n  p = 2\nend subroutine\n")
        _, findings = convert(src)
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("/c/", todos(findings)[0][2])

    def test_different_types_are_a_todo(self):
        src = ("subroutine a()\n  integer x\n  common /c/ x\n  x = 1\nend subroutine\n"
               "subroutine b()\n  real p\n  common /c/ p\n  p = 2.0\nend subroutine\n")
        _, findings = convert(src)
        self.assertEqual(len(todos(findings)), 1)

    def test_common_in_a_program_and_free_form(self):
        src = ("program p\n  integer n\n  common /box/ n\n  n = 4\n  call show()\nend program\n"
               "subroutine show()\n  integer m\n  common /box/ m\n  print *, m\nend subroutine\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["4"])


if __name__ == "__main__":
    unittest.main()
