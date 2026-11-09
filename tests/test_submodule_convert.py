import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.fortran import convert_files

TOP = """module shapes
  implicit none
  integer, parameter :: rk = kind(1.0d0)
  interface area
    module function circle(r) result(a)
      real(rk), intent(in) :: r
      real(rk) :: a
    end function
  end interface
contains
  subroutine hello()
    print *, 'shapes'
  end subroutine
end module
"""
CIRC = """submodule(shapes) circ
contains
  module function circle(r) result(a)
    real(rk), intent(in) :: r
    real(rk) :: a
    call check(r)
    a = 3.0_rk * r * r
  end function
  subroutine check(x)
    real(rk), intent(in) :: x
    if (x < 0) print *, 'circle: negative'
  end subroutine
end submodule
"""
SQ = """submodule(shapes) sq
contains
  module function square(s) result(a)
    real(rk), intent(in) :: s
    real(rk) :: a
    call check(s)
    a = s * s
  end function
  subroutine check(x)
    real(rk), intent(in) :: x
    if (x < 0) print *, 'square: negative'
  end subroutine
end submodule
"""
MAIN = """program p
  use shapes
  implicit none
  call hello()
  print *, area(2.0d0), square(3.0d0)
  print *, area(-1.0d0)
end program
"""


def convert(*texts):
    text, findings, _ = convert_files([(f"f{i}.f90", t) for i, t in enumerate(texts)])
    return text, findings


class SubmoduleTests(unittest.TestCase):
    def test_merged_into_the_parent_and_runs(self):
        text, findings = convert(TOP, CIRC, SQ, MAIN)
        self.assertEqual([f for f in findings if f[1] == "todo"], [])
        self.assertNotIn("submodule", text.replace("! from submodule", ""))
        self.assertEqual(run_bor(text), ["shapes", "12.0 9.0", "circle: negative", "3.0"])

    def test_order_of_files_does_not_matter(self):
        a, _ = convert(TOP, CIRC, SQ, MAIN)
        b, _ = convert(SQ, CIRC, TOP, MAIN)
        self.assertEqual(run_bor(a), run_bor(b))

    def test_origin_comments(self):
        text, _ = convert(TOP, CIRC, SQ, MAIN)
        self.assertIn("! from submodule circ (of shapes)", text)
        self.assertIn("! from submodule sq (of shapes)", text)

    def test_clashing_private_helpers_are_renamed_and_hidden(self):
        text, _ = convert(TOP, CIRC, SQ, MAIN)
        self.assertIn("circ_check", text)
        self.assertIn("sq_check", text)
        self.assertNotIn("subroutine check", text)
        self.assertNotIn("public subroutine circ_check", text)

    def test_unique_private_helper_keeps_its_name(self):
        circ = CIRC.replace("check", "tell")
        text, _ = convert(TOP, circ, SQ, MAIN)
        self.assertIn("subroutine tell(", text)
        self.assertNotIn("circ_tell", text)

    def test_module_procedure_body_takes_the_interface_header(self):
        circ = """submodule(shapes) circ
contains
  module procedure circle
    a = 3.0_rk * r * r
  end procedure
end submodule
"""
        main = MAIN.replace("area(2.0d0), square(3.0d0)", "area(2.0d0)").replace("area(-1.0d0)", "area(1.5d0)")
        text, findings = convert(TOP, circ, main)
        self.assertEqual([f for f in findings if f[1] == "todo"], [])
        self.assertEqual(run_bor(text), ["shapes", "12.0", "6.75"])

    def test_missing_parent_is_a_todo(self):
        text, findings = convert(CIRC)
        self.assertTrue(any(f[1] == "todo" and "parent module 'shapes'" in f[2] for f in findings))


if __name__ == "__main__":
    unittest.main()
