! Constructs the front end cannot translate yet: each should become a TODO
! comment without stopping the rest of the file from converting.
program mixed
  implicit none
  integer :: k, total
  integer, pointer :: buf(:)
  type point
    real :: x, y
  end type point
  type(point) :: p

  total = 0
  do k = 1, 3
    total = total + k
  end do
  print *, 'total:', total

  associate (t => total)
    print *, 'alias', t
  end associate

  allocate(buf(3))
  buf = 1
  p%x = 1.0
  print *, 'end'
end program mixed
