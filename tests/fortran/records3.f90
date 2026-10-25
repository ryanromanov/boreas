module vec
  implicit none
  type :: v2
    double precision :: x = 0.0d0, y = 0.0d0
  end type v2
contains
  type(v2) function add(a, b)
    type(v2), intent(in) :: a, b
    add%x = a%x + b%x
    add%y = a%y + b%y
  end function add

  function total(vs) result(t)
    type(v2), intent(in) :: vs(:)
    type(v2) :: t
    integer :: i
    t = v2()
    do i = 1, size(vs)
      t = add(t, vs(i))
    end do
  end function total

  subroutine scale(vs, k)
    type(v2), intent(inout) :: vs(:)
    double precision, intent(in) :: k
    integer :: i
    do i = 1, size(vs)
      vs(i)%x = vs(i)%x * k
      vs(i)%y = vs(i)%y * k
    end do
  end subroutine scale
end module vec

program main
  use vec
  implicit none
  type(v2) :: ps(3), s
  integer :: i
  do i = 1, 3
    ps(i) = v2(dble(i), dble(i) * 10.0d0)
  end do
  call scale(ps, 2.0d0)
  s = total(ps)
  print *, s%x, s%y
  print *, ps(3)
end program main
