! Loops, arithmetic, an external function, and subroutines with intents.
function hyp(a, b) result(h)
  implicit none
  double precision, intent(in) :: a, b
  double precision :: h
  h = sqrt(a * a + b * b)
end function hyp

subroutine swap_ints(a, b)
  implicit none
  integer, intent(inout) :: a, b
  integer :: t
  t = a
  a = b
  b = t
end subroutine swap_ints

subroutine stats(v, n, mean, biggest)
  implicit none
  integer, intent(in) :: n
  double precision, intent(in) :: v(n)
  double precision, intent(out) :: mean, biggest
  integer :: i
  mean = 0.0d0
  biggest = v(1)
  do i = 1, n
    mean = mean + v(i)
    if (v(i) > biggest) biggest = v(i)
  end do
  mean = mean / n
end subroutine stats

program basic
  implicit none
  double precision :: hyp
  integer, parameter :: n = 5
  integer :: squares(n), i, total, a, b
  double precision :: vals(4), m, big
  logical :: flag

  total = 0
  do i = 1, n
    squares(i) = i * i
    total = total + squares(i)
  end do
  print *, 'squares:', squares
  print *, 'total:', total
  print *, 'hyp:', hyp(3.0d0, 4.0d0)

  a = 1
  b = 2
  call swap_ints(a, b)
  print *, 'swapped:', a, b

  vals = (/ 2.5d0, 1.5d0, 4.0d0, 0.5d0 /)
  call stats(vals, 4, m, big)
  print *, 'mean, max:', m, big

  flag = (total > 50) .and. .not. (a == b)
  print *, flag
  i = 10
  do while (i > 0)
    i = i - 3
  end do
  print *, 'i =', i
  print *, 'mod:', mod(17, 5), 2 ** 10, 7 / 2
  print *, 'done'
end program basic
