module util
contains
  function scaled(x, factor) result(r)
    double precision, intent(in) :: x
    double precision, intent(in), optional :: factor
    double precision :: r
    if (present(factor)) then
      r = x * factor
    else
      r = x
    end if
  end function

  subroutine bump(n, by)
    integer, intent(inout) :: n
    integer, intent(in), optional :: by
    if (present(by)) then
      n = n + by
    else
      n = n + 1
    end if
  end subroutine

end module

program main
  use util
  integer :: k
  k = 1
  call bump(k)
  call bump(k, 5)
  print *, k
  print *, scaled(2.0d0), scaled(2.0d0, 3.0d0)
end program
