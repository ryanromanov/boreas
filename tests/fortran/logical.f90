program lf
  implicit none
  logical :: a, b(3)
  integer :: i
  a = .true.
  b = [.false., .true., .false.]
  write(*,'(A,L5,A,L1,A,L3)') 'a=', a, ' b=', .not. a, ' c=', a .and. b(2)
  write(*,'(3L2)') b
  write(*,'(A,L8)') 'x', 3 > 2
end program
