! A dummy argument with no intent that is only set conditionally: it must come
! out as inout, because the caller's value survives when nothing is found.
program search
  implicit none
  integer :: idx, v(5)
  v = (/ 4, 8, 15, 16, 23 /)
  idx = -1
  call find(v, 5, 15, idx)
  print *, 'found at', idx
  call find(v, 5, 99, idx)
  print *, 'still', idx
contains
  subroutine find(a, n, wanted, pos)
    integer, intent(in) :: n, wanted
    integer, intent(in) :: a(n)
    integer :: pos
    integer :: i
    do i = 1, n
      if (a(i) == wanted) then
        pos = i
        return
      end if
    end do
  end subroutine find
end program search
