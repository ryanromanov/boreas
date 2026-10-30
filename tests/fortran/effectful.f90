module counting
  implicit none
contains
  integer function bump(n)
    integer, intent(inout) :: n
    bump = n
    n = n + 1
  end function bump

  recursive integer function depth(n, d) result(r)
    integer, intent(inout) :: n
    integer, intent(in) :: d
    n = n + 1
    if (d == 0) then
      r = 0
    else
      r = 1 + depth(n, d - 1)
    end if
  end function depth

  subroutine twice(m)
    integer :: m
    integer :: t
    t = bump(m)
    t = bump(m)
  end subroutine twice
end module counting

program effectful
  use counting
  implicit none
  integer :: k, r, steps
  integer :: v(3)
  k = 5
  r = bump(k)
  print *, r, k
  v(2) = bump(k)
  print *, v(2), k
  r = 10 * bump(k) + 1
  print *, r, k
  if (bump(k) == 8) print *, 'eight'
  if (bump(k) == 0) then
    print *, 'zero'
  else
    print *, 'not zero', k
  end if
  steps = 0
  print *, depth(steps, 3), steps
  call twice(k)
  print *, k
end program effectful
