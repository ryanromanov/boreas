! File input and output through unit numbers.
module logs
  implicit none
  integer, parameter :: log_unit = 12
contains
  subroutine note(msg)
    character(len=*), intent(in) :: msg
    write(log_unit, '(A)') msg
  end subroutine
end module

program files
  use logs
  implicit none
  integer :: ios, a, b, i
  logical :: there
  character(len=24) :: name
  character(len=30) :: line
  real :: x

  name = 'boreas_files_demo.txt'
  inquire(file=name, exist=there)
  print *, 'exists before:', there

  open(unit=10, file=name, status='replace', action='write')
  write(10, *) 'first', 42
  write(10, '(A, I4)') 'second', 7
  write(10, '(2I5)') 3, 4
  write(10, *) 2.5
  close(10)

  inquire(file=name, exist=there)
  print *, 'exists after:', there

  open(unit=11, file=name, status='old', action='read')
  read(11, '(A)') line
  print *, 'line 1: ', trim(line)
  read(11, '(A)') line
  print *, 'line 2: ', trim(line)
  read(11, *) a, b
  print *, a, b
  read(11, *, iostat=ios) x
  print *, x, ios
  read(11, *, iostat=ios) x
  print *, 'at the end:', ios
  close(11)

  open(unit=log_unit, file='boreas_files_log.txt', status='replace')
  do i = 1, 3
    call note('entry')
  end do
  close(log_unit)
  open(unit=13, file='boreas_files_log.txt', status='old', iostat=ios)
  print *, 'open ok:', ios
  read(13, '(A)') line
  print *, trim(line)
  close(13)
  open(unit=14, file='boreas_no_such_file.txt', status='old', iostat=ios)
  print *, 'open of a missing file fails:', ios /= 0
end program
