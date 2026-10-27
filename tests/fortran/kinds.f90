module kinds
  implicit none
  integer, parameter :: sp = kind(1.0)
  integer, parameter :: dp = kind(1.0d0)
  integer, parameter :: i8 = selected_int_kind(18)
  integer, parameter :: i2 = selected_int_kind(4)
end module kinds

module stats
  use kinds, only: dp, sp
  implicit none
contains
  function mean(x) result(m)
    real(dp), intent(in) :: x(:)
    real(dp) :: m
    m = sum(x) / real(size(x), dp)
  end function

  function mean_single(x) result(m)
    real(sp), intent(in) :: x(:)
    real(sp) :: m
    m = sum(x) / real(size(x), sp)
  end function
end module stats

program kinds_demo
  use, intrinsic :: iso_fortran_env, only: int64
  use kinds
  use stats
  implicit none
  real(sp) :: a, s
  real(dp) :: b, t
  real :: c
  integer(i8) :: big
  integer(i2) :: small
  integer(kind=1) :: tiny_int
  integer :: i
  real(dp) :: v(4)
  real(sp) :: w(4)
  a = 0.1_sp
  b = 0.1_dp
  c = 0.1
  print *, a * 3, b * 3, c * 3
  big = 2_i8 ** 40
  print *, big, big * 3, huge(big)
  small = 200
  small = small * 100
  tiny_int = 100
  print *, small, tiny_int, huge(small), huge(tiny_int)
  print *, huge(a), huge(b), epsilon(a), epsilon(b), epsilon(1.0), tiny(a) > 0.0
  print *, sqrt(a), sqrt(b), real(big), real(big, dp), dble(a), real(a, dp), a + b
  s = 0.0
  t = 0.0_dp
  do i = 1, 1000
    s = s + 0.1
    t = t + 0.1_dp
  end do
  print *, s, t
  v = [1.0_dp, 2.0_dp, 3.0_dp, 4.5_dp]
  w = [1.0_sp, 2.0_sp, 3.0_sp, 4.5_sp]
  print *, mean(v), mean_single(w), real(int(b * 100.0_dp), dp), int(7.9_dp), int(3.5_sp, i8)
  print *, 1.0_dp / 3.0_dp, 1.0_sp / 3.0_sp, 1.0 / 3.0, 1.0d0 / 3.0d0
end program kinds_demo
