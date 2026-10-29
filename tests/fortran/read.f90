module reading
  implicit none
contains
  subroutine read_int(n)
    integer, intent(out) :: n
    integer :: ios
    read(*,*,iostat=ios) n
    do while (ios /= 0)
      write(*,*) 'not a number, again'
      read(*,*,iostat=ios) n
    end do
  end subroutine read_int
end module reading

program readme
  use reading
  implicit none
  integer :: a, b, ios
  real :: x
  character(len=8) :: w
  logical :: flag
  call read_int(a)
  print *, 'a =', a
  read(*,*) b, x
  print *, b, x
  read *, w, flag
  print *, w, flag
  read(*,*,iostat=ios) a
  print *, 'ios positive:', ios > 0
  read(*,*,iostat=ios) a
  print *, 'ios negative:', ios < 0
  print *, 'say "hi"'
  stop 2
end program readme
