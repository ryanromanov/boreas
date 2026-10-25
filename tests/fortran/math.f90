program math_functions
  implicit none
  double precision :: x(3)
  double precision, parameter :: pi = 3.141592653589793d0
  integer :: k
  x = [0.5d0, -2.5d0, 3.5d0]
  print *, tan(0.5d0), asin(0.5d0), acos(0.5d0), atan(1.0d0), atan2(1.0d0, -1.0d0)
  print *, dtan(0.25d0), datan2(2.0d0, 1.0d0), dsqrt(2.0d0), dabs(-1.5d0)
  print *, sinh(1.0d0), cosh(1.0d0), tanh(1.0d0), log10(1000.0d0), alog10(100.0)
  print *, 4.0d0 * atan(1.0d0) - pi, acos(-1.0d0) - pi
  print *, floor(-2.5d0), ceiling(-2.5d0), nint(2.5d0), nint(-2.5d0), nint(0.49999d0)
  print *, aint(-2.7d0), anint(2.5d0), anint(-0.5d0)
  print *, sign(3.0d0, -1.0d0), sign(3, -2), sign(-3, 5), modulo(-7, 3), mod(-7, 3), modulo(-7.5d0, 2.0d0)
  print *, dim(5, 3), dim(3, 5), hypot(3.0d0, 4.0d0), gamma(5.0d0), erf(0.0d0)
  print *, nint(x), floor(x), sum(abs(x))
  print *, huge(1), epsilon(1.0d0) > 0.0d0, tiny(1.0d0) > 0.0d0, product([1, 2, 3, 4])
  print *, max(1, 5, 3), min(2.5d0, 1.5d0), max(2, 7), min(3, 1)
  do k = 1, 3
    print *, k, sinh(0.1d0 * k) / cosh(0.1d0 * k) - tanh(0.1d0 * k)
  end do
end program
