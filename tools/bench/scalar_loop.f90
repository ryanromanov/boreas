program bench
  implicit none
  integer(8) :: i, s
  double precision :: x
  s = 0
  x = 0.0d0
  do i = 1, 2000000
    s = s + mod(i, 7_8)
    x = x + sqrt(dble(i)) / 3.0d0
  end do
  print *, s, x
end program
