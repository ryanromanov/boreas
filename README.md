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

## Constants

    const real :: pi = 3.14159
    const integer :: primes(3) = [2, 3, 5]

A `const` needs a value and can never be assigned, used as a `do` variable, or
passed as an `out`/`inout` argument. Fortran's `parameter` converts to `const`.

## Arrays

Arrays have any number of dimensions, 1-based and stored column-major like
Fortran. A scalar subscript drops that dimension; `:` keeps it.

    real :: m(2, 3) = reshape([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [2, 3])
    print m(1, :), m(:, 2), m(1:2, 2:3)    ! row, column, block
    m(2, :) = 0.0                          ! sections can be assigned
    print matmul(m, transpose(m))

Operators work elementwise and need matching shapes (scalars broadcast).
Built in: `size`, `shape`, `reshape`, `matmul`, `transpose`, `dot_product`,
`sum`, `maxval`, `minval`, `any`, `all`; `sqrt`, `abs`, `sin`, `cos`, `exp`,
`log`, `real`, `int`, `mod` apply to every element.

## Labeled loops

A loop can be given a label so that `exit` and `cycle` can target an outer loop
from inside an inner one:

    outer: do i = 1, n
      do j = 1, n
        if grid(i, j) == target then
          exit outer          ! leave the whole nest
        end if
        if skip(j) then
          cycle outer         ! next i
        end if
      end do
    end do outer

`do while` loops can be labeled too (`scan: do while cond ... end do scan`).
A labeled loop must close with `end do <label>`, and an unlabeled loop closes
with plain `end do`. `exit <label>` and `cycle <label>` must name a loop that
encloses them within the same procedure; an unknown label, or reusing a label
inside a loop that already has it, is an error. Sibling loops may share a
label, and labels live in their own namespace (they never clash with variables).
After `exit outer`, `i` keeps the value it had when the loop stopped, exactly as
with a plain `exit`.

## Subroutines and parameter modes

Results come back through `out` / `inout` parameters:

    subroutine swap(inout integer :: a, inout integer :: b)
      integer :: t
      t = a
      update a = b
      update b = t
    end subroutine

    call swap(x, y)

Every write to an `out` / `inout` parameter is marked with `update`, so a reader
can see which assignments change something outside the subroutine. Using
`update` on anything else, or leaving it off for such a parameter, is an error.
Parameters default to `in` and cannot be assigned. Functions only take
`in` parameters. An output variable may not be passed twice in one call, and an
`out` parameter must be assigned before the subroutine returns.

## Modules

A module groups constants and procedures. Everything in it is private unless
marked `public`, and a program (or another module, or a single procedure) imports
exactly the names it uses:

    module geometry
      public const real :: pi = 3.14159

      public function area(real :: r) -> real
        return pi * r ** 2
      end function
    end module

    program demo
      use geometry: area, pi
      print area(2.0)
    end program

Nothing is visible without a `use`. Importing a private or missing name is an
error, an imported constant is read-only, and modules cannot import each other in
a cycle. Modules hold only constants and procedures for now (no mutable module
variables). See `examples/modules.bor`.

## Converting Fortran

    boreas convert legacy.f90 -o legacy.bor

Reads free-form Fortran (not fixed-form `.f`) and writes Boreas source plus a
report on stderr. Translations that change meaning are flagged, never silent:

- `! TODO(convert): ...` comments quote any line that could not be translated
  (`select case`, `allocate`, derived types, `goto`, `read`, ...).
- `parameter` constants become `const`.
- Loop names become labels (`outer: do` ... `end do outer`), and `exit name` / `cycle name`
  are translated. Names on `if` / `select` / `block` constructs are dropped.
- Modules become Boreas modules. Fortran is public by default, so names get `public`
  unless the Fortran says `private`. `use m` becomes an explicit `use m: a, b` listing
  the names the code actually uses (names a module re-exports are imported from where
  they are defined). Module variables are mutable shared state, which Boreas modules
  don't have yet, so they become TODOs, as does code that uses them. A `use` of a module
  that isn't defined earlier in the same file, or an import that renames, is a TODO too.
- Missing declarations are filled in using Fortran's implicit typing rules.
- Dummy arguments without `intent` get `in`, `out` or `inout` inferred from the
  body, with a note above the procedure.
- A function whose name is its result variable gets an explicit result variable.
- Calls to procedures that exist nowhere in the file are marked TODO, because they would fail.
- Names that are Boreas keywords (`in`, `out`, `string`, ...) are renamed.

### Checking a conversion

    boreas convert --verify [--timeout SECONDS] FILE.f90 [-o OUT]

`--verify` converts the file as usual, then compiles the original with gfortran
(set `FC` to use another compiler), runs it and the converted program with empty
stdin and a timeout (default 10 s), and compares what they print. Spacing and
commas are ignored, `T`/`F` equal `true`/`false`, and numbers match within 1e-9.

It prints "verified: ... (N values compared)" and exits 0 on a match. On a
mismatch it shows the first differing value from each program and exits 1.
Common causes are Fortran's 32-bit default real and integer versus Boreas's
doubles and unbounded integers, and constructs reported as TODO. Exit status 2
means it could not verify (gfortran missing, the original does not compile, or
it timed out).

The converted file is checked by the test suite against real gfortran output.
Try it on `examples/number_toys.f90`.

## License

MIT. See [LICENSE](LICENSE).
