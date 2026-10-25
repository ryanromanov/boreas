! Derived types defined in a program: hoisted to the top level, defaults kept,
! a parameter used as an array size, a partly assigned intent(out) record.
program inventory
  implicit none
  integer, parameter :: n = 3
  type :: item
    integer :: id = 0
    double precision :: price = 1.5d0
    integer :: counts(n) = 0
    character(len=8) :: label = 'none'
  end type item
  type(item) :: shelf(2)
  type(item) :: one
  integer :: i

  one = item(7)
  shelf(1) = one
  shelf(2)%id = 9
  shelf(2)%counts(2) = 4
  call restock(shelf(2), 2)
  call fill(one)
  do i = 1, 2
    print *, shelf(i)%id, shelf(i)%price, shelf(i)%counts
  end do
  print *, one%label, one%id, one%price
contains
  subroutine restock(it, k)
    type(item), intent(inout) :: it
    integer, intent(in) :: k
    it%counts(k) = it%counts(k) + 10
  end subroutine restock

  subroutine fill(it)
    type(item), intent(out) :: it
    it%id = 42
  end subroutine fill
end program inventory
