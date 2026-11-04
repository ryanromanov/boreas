program eqs
  implicit none
  real :: a, b
  real :: v(5), x, w(3)
  integer :: m(4), n(4), i, j, k
  equivalence (a, b)
  equivalence (v(2), x)
  equivalence (v(3), w(1))
  equivalence (m, n)
  a = 1.5
  print *, b
  b = b + 1.0
  print *, a
  do i = 1, 5
    v(i) = i * 10.0
  end do
  print *, x
  x = -1.0
  print *, v
  do i = 1, 3
    print *, w(i)
  end do
  w(3) = 99.0
  print *, v(5)
  m(2) = 7
  print *, n(2)
  j = 1
  k = j + 1
  print *, w(k)
end program
