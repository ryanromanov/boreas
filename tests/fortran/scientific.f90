program ef
  implicit none
  real :: x(8)
  real(8) :: d
  integer :: i
  x = [12345.678, 0.000123456, 0.0, -1.5, 1.0e30, 99999.99, 123.0, 0.05]
  do i = 1, 8
    write(*,'(A,E12.4,A,G12.4,A,E10.3,A,G9.2)') '|', x(i), '|', x(i), '|', x(i), '|', x(i)
  end do
  d = 1.0d200
  write(*,'(A,E12.4,A,G12.4)') '|', d, '|', d
  write(*,'(A,E9.4,A,E8.4,A)') '|', 12345.678, '|', 12345.678, '|'
  write(*,100) 2.5, 2.5
100 format(' x=', E12.4, ' y=', G12.4)
end program
