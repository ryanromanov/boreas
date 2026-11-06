import unittest

from boreas.fortran import convert


class IntrinsicStatementTests(unittest.TestCase):
    def test_intrinsic_statement_is_ignored(self):
        src = """      subroutine s(a, b)
      double precision a, b
      intrinsic max, abs
      b = max(a, abs(a))
      end
"""
        text, findings = convert(src, "s.f")
        self.assertEqual([m for _, k, m in findings if k == "todo"], [])
        self.assertIn("max(", text)
        self.assertIn("intrinsic", " ".join(m for _, _, m in findings))

    def test_free_form_with_double_colon(self):
        src = "subroutine s(a, b)\n  real :: a, b\n  intrinsic :: abs, sqrt\n  b = sqrt(abs(a))\nend subroutine\n"
        _, findings = convert(src, "s.f90")
        self.assertEqual([m for _, k, m in findings if k == "todo"], [])

    def test_intrinsic_passed_as_an_argument_is_a_todo(self):
        src = ("subroutine s(r)\n  real :: r\n  intrinsic :: sin\n  call integrate(sin, r)\nend subroutine\n")
        _, findings = convert(src, "s.f90")
        todos = [m for _, k, m in findings if k == "todo"]
        self.assertTrue(any("'sin' is passed as an argument" in m for m in todos), todos)

    def test_intrinsic_called_normally_is_not(self):
        src = "subroutine s(r)\n  real :: r\n  intrinsic :: sin\n  r = sin(r)\nend subroutine\n"
        _, findings = convert(src, "s.f90")
        self.assertEqual([m for _, k, m in findings if k == "todo"], [])


if __name__ == "__main__":
    unittest.main()
