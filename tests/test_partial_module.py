import unittest

from boreas.fortran import convert


class PartialModuleTests(unittest.TestCase):
    def test_module_that_stops_half_way_keeps_its_earlier_procedures(self):
        src = """module m
contains
  subroutine good(x)
    real, intent(out) :: x
    x = 1.0
  end subroutine
  subroutine bad(*)
  end subroutine
end module
"""
        text, findings = convert(src, "m.f90")
        self.assertIn("subroutine good", text)
        self.assertTrue(any(k == "todo" for _, k, _ in findings))


if __name__ == "__main__":
    unittest.main()
