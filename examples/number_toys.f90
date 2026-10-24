! Primes, gcd, and a little linear algebra, in free-form Fortran.
! Try it:  boreas convert examples/number_toys.f90 -o number_toys.bor
!          boreas number_toys.bor

integer function gcd(a, b) result(g)
  implicit none
  integer, intent(in) :: a, b
  integer :: x, y, t
  x = a
  y = b
  do while (y /= 0)
    t = mod(x, y)
    x = y
    y = t
  end do
  g = x
end function gcd

! Sieve of Eratosthenes: is_prime(i) is .true. when i is prime.
subroutine sieve(n, is_prime)
  implicit none
  integer, intent(in) :: n
  logical, intent(out) :: is_prime(n)
  integer :: i, j
  is_prime = .true.
  is_prime(1) = .false.
  do i = 2, n
    if (is_prime(i)) then
      do j = i * i, n, i
        is_prime(j) = .false.
      end do
    end if
  end do
end subroutine sieve

program number_toys
  implicit none
  integer, parameter :: limit = 30
  logical :: flags(limit)
  integer :: primes(limit)
  integer :: i, found, gcd
  double precision :: m(2, 2), v(2), w(2)

  call sieve(limit, flags)
  found = 0
  do i = 1, limit
    if (flags(i)) then
      found = found + 1
      primes(found) = i
    end if
  end do
  print *, 'primes up to', limit, ':', found
  print *, primes(1:found)

  print *, 'gcd(84, 36) =', gcd(84, 36)
  print *, 'lcm(4, 6) =', 4 * 6 / gcd(4, 6)

  ! m = [2 1; 0 3], stored column by column
  m = reshape((/ 2.0d0, 0.0d0, 1.0d0, 3.0d0 /), (/ 2, 2 /))
  v = (/ 1.0d0, 2.0d0 /)
  w = matmul(m, v)
  print *, 'm * v =', w(1), w(2)
  print *, 'v . w =', dot_product(v, w)
end program number_toys
