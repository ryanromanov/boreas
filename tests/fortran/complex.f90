module phasors
  implicit none
  complex(kind=8), parameter :: ii = (0.0d0, 1.0d0)
contains
  function rotate(z, angle) result(r)
    complex(kind=8), intent(in) :: z
    double precision, intent(in) :: angle
    complex(kind=8) :: r
    r = z * exp(ii * angle)
  end function

  subroutine roots(a, b, c, r1, r2)
    double precision, intent(in) :: a, b, c
    complex(kind=8), intent(out) :: r1, r2
    complex(kind=8) :: d
    d = sqrt(cmplx(b * b - 4.0d0 * a * c, 0.0d0, kind=8))
    r1 = (-b + d) / (2.0d0 * a)
    r2 = (-b - d) / (2.0d0 * a)
  end subroutine
end module

program complex_demo
  use phasors
  implicit none
  complex(kind=8) :: z, w, r1, r2, v(3)
  double precision :: pi
  integer :: k
  pi = acos(-1.0d0)
  z = (3.0d0, 4.0d0)
  w = cmplx(1.0d0, -2.0d0, kind=8)
  print *, z, w
  print *, abs(z), real(z), aimag(z), conjg(z), aimag(w)
  print *, z + w, z - w, z * w, z / w, z ** 2, -z
  print *, z == w, z /= w, z == (3.0d0, 4.0d0)
  print *, rotate((1.0d0, 0.0d0), pi / 2.0d0)
  call roots(1.0d0, 2.0d0, 5.0d0, r1, r2)
  print *, r1, r2
  call roots(1.0d0, -3.0d0, 2.0d0, r1, r2)
  print *, r1, r2
  print *, sqrt((-4.0d0, 0.0d0)), exp(ii * pi), log(z), sin(z), cos(w)
  do k = 1, 3
    v(k) = cmplx(dble(k), -dble(k))
  end do
  print *, v
  print *, sum(v), product(v), conjg(v), abs(v)
  print *, dot_product(v, v), z * 2, 2.5d0 * z, z + 1
end program
