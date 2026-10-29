program fmt
  implicit none
  integer :: i, n
  integer :: v(4)
  double precision :: x
  character(len=5) :: name
  n = 42
  x = 3.14159d0
  name = 'abcde'
  v = (/ 1, 22, 333, 4444 /)
  write(*,'(A,I5)') 'n =', n
  write(*,'(I5.5)') n
  write(*,'(F8.3)') x
  write(*,'(F0.2)') x
  write(*,'(A5,A)') name, '|'
  write(*,'(3I4)') 1, 2, 3
  write(*,'(I3)') 1, 2, 3
  write(*,'(4I5)') v
  write(*,'(2(A,I2,1X))') 'a', 1, 'b', 2
  write(*,'(A,/,A)') 'first', 'second'
  write(*,'(I1)') -5
  write(*,'(F6.1)') -0.04d0
  write(*,'(I2)') 1234
  write(*,'(F5.2)') 1234.5d0
  write(*,10) 7, 'seven'
  write(*,11)
  write(*,12) (v(i), i = 1, 3)
  write(*,'(A)',advance='no') 'no newline,'
  write(*,'(A)') ' then newline'
  print '(A,F4.1)', 'x = ', 2.25d0
10 format('<', I3, 1X, A, '>')
11 format(' ', 3('-'), 1X, 2('=') )
12 format(3I3)
end program fmt
