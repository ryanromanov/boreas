program chars
  implicit none
  integer, parameter :: n = 6
  character(len=5) :: name = 'abc'
  character(len=n) :: big
  character(len=3) :: cut
  character(len=3) :: names(2)
  character(len=3) :: ans
  character(len=8) :: animal
  integer :: i
  big = 'xy'
  cut = 'abcdef'
  names(1) = 'a'
  names(2) = 'bcd'
  write(*,'(A5,A)') name, '|'
  write(*,'(A,A)') big, '|'
  write(*,'(A,A)') cut, '|'
  write(*,'(A,A,A)') names(1), names(2), '|'
  print *, len(name), len(big), len(cut), len_trim(name), len(trim(name))
  print *, name == 'abc', name == 'abc  ', name /= 'abd', 'abc' == name
  print *, trim(name) // '!'
  read(*,*) ans
  print *, ans == 'yes', len(ans)
  animal = 'cat'
  select case (animal)
  case ('dog')
    print *, 'dog'
  case ('cat')
    print *, 'cat'
  end select
  do i = 1, 2
    if (names(i) == 'a') print *, 'found a at', i
  end do
end program chars
