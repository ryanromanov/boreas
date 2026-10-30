import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import conv, run_bor, todos


class DataStatementTests(unittest.TestCase):
    def run_it(self, decls, body):
        text, findings = conv(body, decls)
        return text, findings

    def test_scalars_and_arrays(self):
        text, findings = self.run_it("  integer :: n\n  real :: v(3)\n  data n /7/\n  data v /1.5, 2.5, 3.5/",
                                     "  print *, n, v")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["7 [1.5, 2.5, 3.5]"])

    def test_several_objects_and_groups(self):
        text, findings = self.run_it("  integer :: a, b, c\n  data a, b /1, 2/, c /3/", "  print *, a, b, c")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["1 2 3"])

    def test_repeat_counts_and_signs(self):
        text, _ = self.run_it("  integer :: v(5)\n  data v /2*0, -1, 2*+4/", "  print *, v")
        self.assertEqual(run_bor(text), ["[0, 0, -1, 4, 4]"])

    def test_two_dimensional_arrays_fill_in_column_order(self):
        text, _ = self.run_it("  integer :: m(2, 2)\n  data m /1, 2, 3, 4/", "  print *, m(2, 1), m(1, 2)")
        self.assertEqual(run_bor(text), ["2 3"])

    def test_strings_and_logicals(self):
        text, findings = self.run_it("  character(len=3) :: s\n  logical :: f\n  data s /'ab'/, f /.true./",
                                     "  print *, s, f")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["ab  true"])

    def test_wrong_counts_are_todos(self):
        _, findings = self.run_it("  integer :: v(3)\n  data v /1, 2/", "  print *, v")
        self.assertEqual(len(todos(findings)), 1)
        _, findings = self.run_it("  integer :: a\n  data a /1, 2/", "  print *, a")
        self.assertEqual(len(todos(findings)), 1)

    def test_elements_and_undeclared_names_are_todos(self):
        _, findings = self.run_it("  integer :: v(3)\n  data v(1) /5/", "  print *, v")
        self.assertEqual(len(todos(findings)), 1)

    def test_a_data_variable_that_changes_implies_save(self):
        _, findings = conv("n = n + 1\n  print *, n", "  integer :: n\n  data n /0/")
        self.assertEqual(len(todos(findings)), 0)             # in the main program: runs once
        src = ("subroutine bump()\n  integer :: n\n  data n /0/\n  n = n + 1\n  print *, n\nend subroutine\n"
               "program p\n  call bump()\n  call bump()\nend program\n")
        from boreas.fortran import convert
        _, findings = convert(src)
        self.assertEqual(len(todos(findings)), 1)


if __name__ == "__main__":
    unittest.main()
