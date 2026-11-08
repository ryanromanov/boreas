import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.verify import same_output

BASIC = """module strings
  implicit none
  interface to_num
    module procedure int_num, real_num, vec_num
  end interface
  interface norm
    module procedure norm_real
  end interface
contains
  function int_num(n) result(r)
    integer, intent(in) :: n
    integer :: r
    r = n + 1
  end function
  function real_num(x) result(r)
    double precision, intent(in) :: x
    integer :: r
    r = int(x * 10.0d0)
  end function
  function vec_num(v) result(r)
    double precision, intent(in) :: v(:)
    integer :: r
    r = size(v) * 100
  end function
  function norm_real(x) result(r)
    double precision, intent(in) :: x(:)
    double precision :: r
    r = sqrt(sum(x * x))
  end function
end module

program demo
  use strings
  integer :: k = 7
  double precision :: t = 2.5d0, v(3) = [1d0, 2d0, 2d0]
  print *, to_num(k)
  print *, to_num(3)
  print *, to_num(t)
  print *, to_num(k * 2 + 1)
  print *, to_num(t * 2)
  print *, to_num(v)
  print *, to_num(v(2))
  print *, norm(v)
end program
"""

KINDS = """module m
  implicit none
  interface which
    module procedure which_i4, which_i8, which_r4, which_r8
  end interface
contains
  function which_i4(x) result(r)
    integer(4), intent(in) :: x
    integer :: r
    r = 4
  end function
  function which_i8(x) result(r)
    integer(8), intent(in) :: x
    integer :: r
    r = 8
  end function
  function which_r4(x) result(r)
    real(4), intent(in) :: x
    integer :: r
    r = 44
  end function
  function which_r8(x) result(r)
    real(8), intent(in) :: x
    integer :: r
    r = 88
  end function
end module

program t
  use m
  integer(4) :: a = 1
  integer(8) :: b = 2
  real(4) :: c = 1.5
  real(8) :: d = 2.5d0
  print *, which(a), which(b), which(c), which(d)
  print *, which(1.5), which(1.5d0), which(a + b), which(c * d), which(a * 2)
end program t
"""

SUBROUTINES = """module m
  implicit none
  interface show
    module procedure show_int, show_flag
  end interface
contains
  subroutine show_int(n)
    integer, intent(in) :: n
    print *, 'int', n
  end subroutine
  subroutine show_flag(f)
    logical, intent(in) :: f
    print *, 'flag', f
  end subroutine
end module

program t
  use m, only: show
  call show(3)
  call show(.true.)
  call show(2 > 1)
end program t
"""

UNKNOWN = """module m
  implicit none
  interface pick
    module procedure pick_i, pick_r
  end interface
contains
  function pick_i(x) result(r)
    integer, intent(in) :: x
    integer :: r
    r = 1
  end function
  function pick_r(x) result(r)
    real(8), intent(in) :: x
    integer :: r
    r = 2
  end function
end module

program t
  use m
  print *, pick(mystery(3))
end program t

function mystery(n) result(r)
  integer :: n
  integer :: r
  r = n
end function mystery
"""

TWO_ARGS = """module m
  implicit none
  interface combine
    module procedure combine_ii, combine_ir
  end interface
contains
  function combine_ii(a, b) result(r)
    integer, intent(in) :: a, b
    integer :: r
    r = a + b
  end function
  function combine_ir(a, b) result(r)
    integer, intent(in) :: a
    real(8), intent(in) :: b
    integer :: r
    r = a * 100 + int(b)
  end function
end module

program t
  use m
  integer :: k = 3
  print *, combine(k, 4), combine(k, 4.0d0)
end program t
"""

PRIVATE_SPECIFICS = """module m
  implicit none
  private
  public :: size_of
  interface size_of
    module procedure size_of_int, size_of_real
  end interface
contains
  function size_of_int(x) result(r)
    integer, intent(in) :: x
    integer :: r
    r = 4
  end function
  function size_of_real(x) result(r)
    real(8), intent(in) :: x
    integer :: r
    r = 8
  end function
end module

program t
  use m, only: size_of
  print *, size_of(1), size_of(1.0d0)
end program t
"""

OPERATOR = """module m
  implicit none
  type pt
    integer :: x
  end type
  interface operator(+)
    module procedure add_pt
  end interface
contains
  function add_pt(a, b) result(r)
    type(pt), intent(in) :: a, b
    type(pt) :: r
    r%x = a%x + b%x
  end function
end module
"""


class GenericConversionTests(unittest.TestCase):
    def check(self, src):
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_calls_go_to_the_specific_the_arguments_select(self):
        text = self.check(BASIC)
        self.assertIn("int_num(k)", text)
        self.assertIn("real_num(t)", text)
        self.assertIn("vec_num(v)", text)
        self.assertIn("int_num(k * 2 + 1)", text)
        self.assertIn("real_num(t * 2)", text)
        self.assertIn("norm_real(v)", text)
        self.assertNotIn("to_num", text.replace("generic interface 'to_num'", ""))

    def test_the_kind_of_a_variable_or_literal_selects_the_specific(self):
        self.check(KINDS)

    def test_a_subroutine_generic_is_resolved_too(self):
        self.check(SUBROUTINES)

    def test_a_public_generic_makes_its_private_specifics_reachable(self):
        self.check(PRIVATE_SPECIFICS)

    def test_two_arguments_are_both_considered(self):
        self.check(TWO_ARGS)

    def test_an_argument_that_cannot_be_typed_is_a_todo_naming_the_candidates(self):
        _, findings = converted(UNKNOWN)
        msgs = [m[2] for m in todos(findings)]
        self.assertTrue(any("'pick'" in m and "pick_i" in m and "pick_r" in m for m in msgs), msgs)
        self.assertEqual(len([m for m in msgs if "not a Boreas built-in" in m and "pick" in m]), 0, msgs)

    def test_operator_overloading_is_a_todo_with_its_own_message(self):
        _, findings = converted(OPERATOR)
        self.assertTrue(any("operator overloading" in m[2] for m in todos(findings)), todos(findings))


if __name__ == "__main__":
    unittest.main()
