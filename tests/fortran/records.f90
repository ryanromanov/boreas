module shapes
  implicit none
  type :: point
    double precision :: x = 0.0d0
    double precision :: y = 0.0d0
  end type point
  type :: segment
    type(point) :: a, b
    integer :: tag(3) = 0
  end type segment
contains
  function length(s)
    type(segment), intent(in) :: s
    double precision :: length
    length = sqrt((s%b%x - s%a%x)**2 + (s%b%y - s%a%y)**2)
  end function length

  subroutine shift(p, dx)
    type(point), intent(inout) :: p
    double precision, intent(in) :: dx
    p%x = p%x + dx
  end subroutine shift

  subroutine make(p)
    type(point), intent(out) :: p
    p%x = 1.0d0
    p%y = 2.0d0
  end subroutine make
end module shapes

program demo
  use shapes
  implicit none
  type(point) :: p, q
  type(segment) :: s
  type(point) :: pts(3)
  integer :: i
  p = point(3.0d0, 4.0d0)
  q = point(y = 8.0d0)
  call shift(p, 1.0d0)
  call make(q)
  s%a = point(0.0d0, 0.0d0)
  s%b = p
  s%tag(2) = 7
  print *, length(s)
  print *, p%x, q%y, s%tag
  do i = 1, 3
    pts(i)%x = i
    pts(i)%y = pts(i)%x * 2.0d0
  end do
  print *, pts(2)%y, pts(3)
  print *, p
end program demo
