import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, conv, fixture, read, run_bor, run_gfortran, todos
from boreas.fortran import convert
from boreas.verify import same_output


class EquivalenceTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = fixture("equivalence.f90")
        text, findings = convert(read(path), "equivalence.f90")
        self.assertEqual(todos(findings), [])
        if GFORTRAN:
            self.assertTrue(same_output("\n".join(run_bor(text)), run_gfortran(path)))

    def test_same_type_scalars_become_one_variable(self):
        text, findings = conv("a = 2.0\n  print *, b", "real :: a, b\n  equivalence (a, b)")
        self.assertEqual(todos(findings), [])
        self.assertNotIn("real32 :: b", text)
        self.assertIn("println(a)", text)

    def test_scalar_becomes_an_array_element(self):
        text, findings = conv("x = 4\n  print *, v(3)", "integer :: v(5), x\n  equivalence (v(3), x)")
        self.assertEqual(todos(findings), [])
        self.assertIn("v[3] = 4", text)

    def test_array_overlapping_at_an_offset_shifts_its_subscripts(self):
        text, findings = conv("w(2) = 9\n  print *, v(5)", "integer :: v(6), w(2)\n  equivalence (v(4), w(1))")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["9"])

    def test_different_types_stay_a_todo(self):
        text, findings = conv("", "integer :: a\n  real :: b\n  equivalence (a, b)")
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("different types", todos(findings)[0][2])

    def test_storage_the_larger_name_does_not_cover_is_a_todo(self):
        _, findings = conv("", "integer :: v(3), w(3)\n  equivalence (v(3), w(1))")
        self.assertEqual(len(todos(findings)), 1)

    def test_whole_use_of_a_shifted_array_is_a_todo(self):
        _, findings = conv("print *, w", "integer :: v(6), w(2)\n  equivalence (v(4), w(1))")
        self.assertEqual(len(todos(findings)), 1)

    def test_arguments_cannot_share_storage(self):
        text, findings = convert("subroutine s(a)\n  integer :: a, b\n  equivalence (a, b)\nend subroutine\n", "s.f90")
        self.assertEqual(len(todos(findings)), 1)


if __name__ == "__main__":
    unittest.main()
