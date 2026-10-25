! Allocatable arrays: sized at run time, queried, freed and allocated again.
module helpers
contains
subroutine fill(v)
  implicit none
  real, intent(inout) :: v(:)
  integer :: i
  do i = 1, size(v)
    v(i) = i * 1.5
  end do
end subroutine fill

function total(v) result(s)
  implicit none
  real, intent(in) :: v(:)
  real :: s
  s = sum(v)
end function total
end module helpers

program allocs
  use helpers
  implicit none
  integer, allocatable :: squares(:)
  real, allocatable :: grid(:, :)
  real, allocatable, dimension(:) :: line
  integer :: n, rows, cols, i, j

  n = 3 + 2
  print *, allocated(squares)
  allocate(squares(n))
  print *, allocated(squares), size(squares)
  do i = 1, n
    squares(i) = i * i
  end do
  print *, squares

  rows = n - 2
  cols = 2 * rows
  allocate(grid(rows, cols))
  do j = 1, cols
    do i = 1, rows
      grid(i, j) = i * 10 + j
    end do
  end do
  print *, shape(grid), grid(rows, cols), sum(grid)

  deallocate(squares)
  print *, allocated(squares)
  allocate(squares(2 * n))
  squares = 7
  squares(1:3) = [1, 2, 3]
  print *, size(squares), squares(1:4), sum(squares)

  allocate(line(4))
  call fill(line)
  print *, line, total(line)
  deallocate(line, grid)
  if (.not. allocated(grid)) then
    allocate(grid(1, 1), line(0))
    print *, size(grid), size(line)
  end if
end program allocs
