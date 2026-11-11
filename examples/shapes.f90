! Object-oriented Fortran: an abstract type, extensions, type-bound procedures, a wrapper type for
! an array of mixed shapes, allocate with source=, and select type.
! `boreas convert examples/shapes.f90` turns it into Boreas interfaces, records and `select type`
! (compare examples/shapes.bor, which is the same program written by hand).
module shapes
  implicit none
  private
  public :: shape, circle, rect, shape_ref

  type, abstract :: shape
    character(len=16) :: name = "shape"
  contains
    procedure(area_i), deferred :: area          ! every shape must provide one
    procedure :: describe                        ! shared behavior, written once
  end type

  abstract interface
    function area_i(self) result(a)
      import :: shape
      class(shape), intent(in) :: self
      real :: a
    end function
  end interface

  type, extends(shape) :: circle
    real :: r = 1.0
  contains
    procedure :: area => circle_area
  end type

  type, extends(shape) :: rect
    real :: w = 1.0, h = 1.0
  contains
    procedure :: area => rect_area
  end type

  type :: shape_ref                              ! Fortran cannot make an array of class(shape),
    class(shape), allocatable :: s               ! so each element is wrapped
  end type

contains

  function circle_area(self) result(a)
    class(circle), intent(in) :: self
    real :: a
    a = 3.14159 * self%r**2
  end function

  function rect_area(self) result(a)
    class(rect), intent(in) :: self
    real :: a
    a = self%w * self%h
  end function

  subroutine describe(self)
    class(shape), intent(in) :: self
    print '(a, f8.3)', trim(self%name) // " area =", self%area()   ! dynamic dispatch
  end subroutine
end module

program demo
  use shapes
  implicit none
  type(shape_ref) :: all(3)
  real :: total
  integer :: i

  allocate(all(1)%s, source=circle(name="circle", r=2.0))
  allocate(all(2)%s, source=rect(name="rect", w=3.0, h=4.0))
  allocate(all(3)%s, source=circle(name="unit", r=1.0))

  total = 0.0
  do i = 1, size(all)
    call all(i)%s%describe()
    total = total + all(i)%s%area()
  end do
  print '(a, f8.3)', "total =", total

  select type (p => all(2)%s)                    ! recover the concrete type when needed
  type is (rect)
    print '(a, f6.1)', "width of the rect:", p%w
  end select
end program
