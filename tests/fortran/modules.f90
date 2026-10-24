! Modules: public/private, use with and without `only`, a module using a module.
module geometry
  implicit none
  private
  public :: pi, area, ratio, tally
  double precision, parameter :: pi = 3.14159d0
  double precision, parameter :: unit_scale = 2.0d0    ! private
contains
  function area(r)
    double precision, intent(in) :: r
    double precision :: area
    area = pi * r ** 2
  end function area

  function ratio(r)
    double precision, intent(in) :: r
    double precision :: ratio
    ratio = area(r) / scale_of(unit_scale)
  end function ratio

  function scale_of(x)                       ! private helper
    double precision, intent(in) :: x
    double precision :: scale_of
    scale_of = x * 1.0d0
  end function scale_of

  subroutine tally(n, evens, tau)
    integer, intent(in) :: n
    integer, intent(out) :: evens
    double precision, intent(out) :: tau
    integer :: i
    evens = 0
    do i = 1, n
      if (mod(i, 2) == 0) evens = evens + 1
    end do
    tau = 2.0d0 * pi
  end subroutine tally
end module geometry

module counting
  use geometry, only: pi
  implicit none
  integer, parameter, public :: limit = 10
contains
  integer function triangle(n)
    integer, intent(in) :: n
    integer :: k
    triangle = 0
    do k = 1, n
      triangle = triangle + k
    end do
  end function triangle

  double precision function circle_sum(n)
    integer, intent(in) :: n
    circle_sum = pi * n
  end function circle_sum
end module counting

program demo
  use geometry
  use counting, only: triangle, limit, circle_sum
  implicit none
  integer :: e
  double precision :: t
  print *, area(2.0d0), pi
  print *, ratio(1.0d0)
  call tally(limit, e, t)
  print *, e, t
  print *, triangle(limit), circle_sum(2)
end program demo
