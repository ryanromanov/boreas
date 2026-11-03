! Single-precision library functions: Fortran calls sinf, atanf, powf, ... and so must Boreas.
program single
  implicit none
  real :: x, y
  integer :: i
  do i = 1, 60
    x = 0.037 * i
    y = x * 1.7 + 0.11
    print *, x, sin(x), cos(x), tan(x), atan(x), tanh(x), sinh(x), exp(x), log(x), log10(y)
    print *, asin(x / 3.0), acos(x / 3.0), atan2(x, y), hypot(x, y), x ** 2, x ** 3, x ** 5, x ** (-3), x ** 0.37, y ** x
    print *, gamma(y), log_gamma(y), erf(x), erfc(x), asinh(y), acosh(y + 1.0), atanh(x / 3.0)
  end do
end program
