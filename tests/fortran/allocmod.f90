module grid
  implicit none
  real, allocatable :: cells(:, :)
  logical :: ready = .false.
contains
  function make(nx, ny) result(ok)
    integer, intent(in) :: nx, ny
    logical :: ok
    allocate(cells(nx, ny))
    cells = 1.5
    ready = .true.
    ok = .true.
  end function
  subroutine release()
    if (allocated(cells)) deallocate(cells)
    ready = .false.
  end subroutine
end module

program q
  use grid
  implicit none
  integer, allocatable :: tmp(:)
  logical :: r
  r = make(2, 3)
  cells(2, 3) = 9.0
  print *, sum(cells), size(cells, 1), ready
  call release()
  print *, allocated(cells)
  allocate(cells(1, 1))
  cells(1, 1) = 4.0
  print *, cells(1, 1)
  allocate(tmp(3))
  tmp = 7
  print *, tmp
end program
