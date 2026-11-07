import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import run_bor, todos
from boreas.fortran import convert
from boreas.parser import parse

LIB = """module lib
  integer, parameter :: rk = kind(1.0d0)
  integer, parameter :: limit = 10
contains
  function twice(x) result(y)
    real(rk), intent(in) :: x
    real(rk) :: y
    y = 2.0_rk * x
  end function
  function inc(n) result(m)
    integer, intent(in) :: n
    integer :: m
    m = n + 1
  end function
end module
"""


def conv(main):
    text, findings = convert(LIB + main)
    parse(text)
    return text, findings


class RenamedImportTests(unittest.TestCase):
    def test_only_list_renames(self):
        text, f = conv("program p\n  use lib, only: dp => rk, lim => limit, double => twice\n"
                       "  real(dp) :: x\n  x = double(1.5_dp) + lim\n  print *, x\nend program\n")
        self.assertEqual(todos(f), [])
        self.assertIn("x = twice(1.5) + limit", text)
        self.assertEqual(run_bor(text), ["13.0"])             # what gfortran prints is 13.000000000000000

    def test_rename_list_without_only(self):
        text, f = conv("program p\n  use lib, plus => inc\n  print *, plus(1)\nend program\n")
        self.assertEqual(todos(f), [])
        self.assertEqual(run_bor(text), ["2"])

    def test_a_renamed_name_works_inside_a_module_procedure(self):
        text, f = conv("module m2\n  use lib, only: plus => inc\ncontains\n  function f(n) result(r)\n"
                       "    integer, intent(in) :: n\n    integer :: r\n    r = plus(n) * 2\n  end function\nend module\n"
                       "program p\n  use m2, only: f\n  print *, f(4)\nend program\n")
        self.assertEqual(todos(f), [])
        self.assertEqual(run_bor(text), ["10"])

    def test_a_kind_renamed_from_an_intrinsic_module_reaches_its_users(self):
        text, f = convert("module kinds\n  use, intrinsic :: iso_fortran_env, only: rk => real64\nend module\n"
                          "program p\n  use kinds, only: dp => rk\n  real(dp) :: x\n  x = 1.5_dp\n  print *, x\nend program\n")
        self.assertEqual(todos(f), [])
        self.assertEqual(run_bor(text), ["1.5"])

    def test_clash_with_a_local_declaration_is_a_todo(self):
        _, f = conv("program p\n  use lib, only: plus => inc\n  integer :: inc\n  inc = plus(1)\nend program\n")
        self.assertTrue(any("also declared here" in x[2] for x in todos(f)), todos(f))


if __name__ == "__main__":
    unittest.main()
