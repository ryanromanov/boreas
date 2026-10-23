# Boreas

A small programming language with Fortran's look and stricter rules: every
variable is declared, arrays are bounds-checked, and conditions must be logical.
Currently a tree-walking interpreter written in Python.

## Install and run

    pip install .
    boreas examples/hello.bor

## Test

    python3 -m unittest discover -s tests

## Example

    function square(real :: x) -> real
      return x * x
    end function

    program demo
      integer :: ys(3) = [1, 2, 3]
      print "squares:", ys * ys, square(2.5)
    end program

## Subroutines and intent

Results come back through `intent(out)` / `intent(inout)` arguments:

    subroutine swap(integer, intent(inout) :: a, integer, intent(inout) :: b)
      integer :: t
      t = a
      a = b
      b = t
    end subroutine

    call swap(x, y)

Parameters default to `intent(in)` and cannot be assigned. Functions only take
`intent(in)`. An output variable may not be passed twice in one call, and an
`out` parameter must be assigned before the subroutine returns.

## License

MIT. See [LICENSE](LICENSE).
