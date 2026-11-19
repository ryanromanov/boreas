program bench
  implicit none
  integer :: i, j, k, n
  double precision :: a(120,120), b(120,120), c(120,120), s
  n = 120
  do j = 1, n
    do i = 1, n
      a(i,j) = dble(i + j) / 100.0d0
      b(i,j) = dble(i - j) / 50.0d0
    end do
  end do
  do j = 1, n
    do i = 1, n
      s = 0.0d0
      do k = 1, n
        s = s + a(i,k) * b(k,j)
      end do
      c(i,j) = s
    end do
  end do
  print *, c(1,1), c(60,60), c(120,120), c(7,99)
end program
