! IEEE special values: Infinity and NaN instead of a stopped program.
program ieee
  implicit none
  real :: zero, x, y, big
  real(8) :: d
  integer :: i
  zero = 0.0
  big = 1.0e30
  print *, 1.0 / zero, -1.0 / zero, zero / zero
  print *, sqrt(-1.0 - zero), log(zero), exp(big), acos(2.0 + zero)
  x = asin(2.0 + zero)
  print *, x == x, x /= x, x < 1.0, x > 1.0
  y = 1.0 / zero
  print *, y > 1.0e38, y == y, -y < 0.0
  write(*, '(F8.2, F10.2, F6.2, F4.1)') x, y, -y, y
  d = 1.0d0
  d = d / (zero * 1.0d0)
  print *, d, d * 0.0d0
  x = 0.0
  do i = 1, 3
    x = x + 1.0 / (zero + i - 1)
  end do
  print *, x
  if (x /= x) then
    print *, 'not a number'
  else
    print *, 'a number'
  end if
end program
