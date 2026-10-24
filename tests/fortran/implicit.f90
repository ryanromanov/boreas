! No `implicit none`: undeclared names are typed by their first letter, and
! dummy arguments without `intent` must be inferred. Internal procedures follow `contains`.
program legacyish
  integer count
  character(len=20) :: greeting
  x = 2.5
  n = 4
  total = 0.0
  do i = 1, n
    total = total + i * x
  end do
  print *, total

  count = 0
  call bump(count)
  call bump(count)
  call setit(y)
  print *, count, y

  if (x < 1.0) then
    print *, 'small'
  else if (x < 3.0) then
    print *, 'medium'
  else
    print *, 'large'
  end if

  greeting = 'hello' // ' world'
  print *, greeting
  print *, fact(5), (x > 2.0 .or. n < 0), (n == 4 .eqv. x > 0.0)
contains
  subroutine bump(c)
    integer c
    c = c + 1
  end subroutine bump

  subroutine setit(v)
    real v
    v = 3.5
  end subroutine setit

  recursive integer function fact(m) result(r)
    integer, intent(in) :: m
    if (m <= 1) then
      r = 1
    else
      r = m * fact(m - 1)
    end if
  end function fact
end program legacyish
