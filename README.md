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

## Declarations

Each variable is declared on its own line, with an optional initial value:

    integer :: i
    integer :: count = 0
    real :: v(3) = [1.0, 2.0, 3.0]

A variable you don't give a value starts at its type's default: `0` for `integer`, `0.0` for `real`,
`false` for `logical` and `""` for `string`. Arrays and records start that way element by element
and field by field. The one exception is an `out` parameter, which starts with no value: producing
it is the subroutine's job, so reading it first, or never assigning it, is an error.

`integer :: i, j` is an error ("declare one variable per line"). That way every
variable can carry its own comment, and a change touches only the line it is
about. `boreas convert` splits Fortran's `real :: x, y, z` into separate lines.

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

## select case

Choose between values without a chain of `if`s. Case values are literals or
`const` names; ranges use `lo:hi`, `:hi` or `lo:`. The selector may be an
integer, string or logical (not real).

    select case grade
    case 90:100
      print "A"
    case 80:89
      print "B"
    case 1, 3, 5
      print "odd and small"
    case default
      print "other"
    end select

- Each case runs only its own body; there is no implicit fallthrough.
- `case default` is optional, may appear anywhere, at most once.
- Case values must not overlap. Literal overlaps are parse errors; overlaps
  involving `const` names are reported when the select runs.
- `break` leaves the nearest enclosing select early. It is a parse error
  outside a select, and also when a loop sits between the `break` and the
  select (use `exit` for the loop).
- `exit` and `cycle` inside a select refer to the enclosing loop; `return` works as usual.

To let cases flow into each other, put `fallthrough` after `select case`. Finishing
a case body then continues into the next case's body in source order (including
`case default`) until a `break` or the end of the select:

    select case fallthrough grade
    case 3
      print "bronze"
    case 2
      print "silver"
      break
    case 1
      print "gold"
    end select

`grade = 3` prints bronze and silver; `grade = 2` prints silver; `grade = 1` prints gold.

`select case` is also an expression. Each arm is `case <values> -> <expr>`
and `case default -> <expr>` is required, so the expression is always exhaustive.
It has no fallthrough mode and no `break`:

    label = select case grade
      case 90:100 -> "A"
      case 80:89 -> "B"
      case 1, 3 -> "small"
      case default -> "other"
    end select

It can be used anywhere an expression can. Only the chosen arm is evaluated. Arms
must have the same type (integer arms widen to real next to a real arm).
`select`, `case`, `default`, `fallthrough` and `break` are reserved words.

## Allocatable arrays

Arrays whose size is only known at run time are declared `allocatable` with one `:` per
dimension (the number of colons is the rank), given a size with `allocate`, and freed with
`deallocate`:

    allocatable integer :: a(:)
    allocatable real :: m(:, :)
    n = 5
    print allocated(a)          ! false
    allocate a(n)               ! elements start at 0 / 0.0 / false / ""
    allocate m(n, 2 * n)
    print allocated(a), size(a) ! true 5
    deallocate a                ! may be allocated again, with any size

Allocatable arrays are locals of functions, subroutines and the program, and have no
initializer and no `const`. Anything but `allocated(a)` and `allocate` on an unallocated array
is a run-time error (reading, writing, `size(a)`, passing it, printing it, sections, whole-array
assignment). `allocate` on an allocated array and `deallocate` on an unallocated one are errors.
Extents must be non-negative integers (zero is fine). Whole-array assignment never allocates or
resizes. An allocated array can be passed to any procedure taking an array parameter; parameters
cannot themselves be `allocatable`, so a callee cannot allocate its caller's array. A leftover
allocation is freed at the end of its scope.

## Records

A record groups named fields. Records are defined at the top level or inside a module, one
field per line, with no default values:

    record point
      real :: x
      real :: y
    end record

    point :: p = point(1.0, 2.0)           ! all fields, in order
    point :: q = point(y = 2.0, x = 1.0)   ! or by name, in any order
    point :: r = point(y = 5.0)            ! any subset: the others keep their defaults (0.0)
    p.x = 3.0
    print p.x + q.y, p

- A record is a value: assigning one, passing it, or returning it copies it, so two variables never
  share fields. Records can hold other records and fixed-size array fields (`integer :: tags(3)`,
  with a literal size), and you can make arrays of records (`point :: pts(10)`).
- Fields start at their type's default value (see Declarations), nested records and array fields
  included, so `point :: p` is a usable zero point. A field may declare its own default with a
  constant expression (literals, `const` names from the same module, arithmetic, array literals,
  record constructors, and built-in functions such as `sqrt`; not variables or your own functions):

      record config
        integer :: count = 5
        real :: scale = 1.0
        logical :: verbose
      end record

      config :: c                    ! count = 5, scale = 1.0, verbose = false
      config :: d = config(scale = 2.5)

  A constructor overrides defaults, and fields it leaves out keep them.
- `const point :: origin = point(0.0, 0.0)` makes a read-only record. Writing a field of a `const`
  record or an `in` parameter is an error.
- A write to a field of an `out` or `inout` parameter needs `update`, like any write the caller sees:
  `update p.x = p.x + dx`. An `out` record parameter starts with no values, except for fields that
  declare a default, and must have every field assigned before the subroutine returns. Two fields of one record may be passed as separate `out`/`inout` arguments;
  the same field twice (or a record and one of its fields) is an aliasing error.
- Records cannot be compared or used in arithmetic (compare the fields), cannot be `select` subjects,
  and cannot be `allocatable` yet. In a module, a record is private unless marked `public`, and is
  imported by name: `use geometry: point`. See `examples/records.bor`.

## Optional values

A plain `integer`, `real`, `logical`, `string` or record always holds a value. Add `?` to the type
and the variable may instead be absent (`null`):

    integer? :: n                       ! starts as null
    print present(n), n == null         ! false true
    n = 4
    print n ?? 10                       ! 4; `a ?? b` is a, or b when a is null
    n = null

- `null` only goes into a `?` type; assigning it to a plain one is an error. An optional variable
  starts as `null`, where a plain one starts at its type's zero.
- Using an absent value is an error: arithmetic, ordering, conditions and passing it to a plain
  parameter all fail with a message. What you can do with one: test it (`present(x)`,
  `x == null`, `x /= null`), give it a fallback (`x ?? y`), store it, print it (`null`) and pass it
  to another optional parameter. `??` is the loosest operator (`n ?? 0 + 1` is `n ?? (0 + 1)`),
  chains left to right, and only evaluates its right side when the left is null.
- `in` parameters may be optional, and such arguments can be left off the end of a call
  (`step_or()` for `function step_or(integer? :: step)`); they arrive as `null`. Only trailing ones can be left
  off; to skip one in the middle, pass `null`.
- A function may return `-> integer?` to say "no result" (`return null`).
- Record fields can be optional (`address? :: home`); they start as `null`. Reading a field
  through a null record is an error (`p.home.city` when `p.home` is null). A record still cannot
  contain itself, even optionally.
- Not supported: optional `out`/`inout` parameters, arrays of optional values, `?.` chaining.

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

## Checking a file

    boreas check FILE.bor [--json]

Parses and checks a file without running it. Errors print as `file:line:col: message`
(column 1 when the error has no column) and the exit status is 1; a clean file prints
nothing and exits 0. `--json` prints `{"file": ..., "errors": [{"line", "col", "message"}]}`.

## Converting Fortran

    boreas convert legacy.f90 -o legacy.bor

Reads free-form Fortran (not fixed-form `.f`) and writes Boreas source plus a
report on stderr. Translations that change meaning are flagged, never silent:

- `! TODO(convert): ...` comments quote any line that could not be translated
  (`goto`, `read`, type-bound procedures, ...).
- `parameter` constants become `const`.
- `allocatable` locals, `allocate` / `deallocate` (one statement per array) and `allocated()` are
  translated. `stat=`, `errmsg=`, `source=`, `mold=`, non-1 lower bounds, allocatable
  dummy arguments, results and module variables become TODOs. A whole-array assignment to an
  allocatable with no earlier `allocate` gets a note, since Fortran allocated it automatically.
- Derived types become records, with `%` turned into `.` and `type(point)` into `point`. Component
  defaults carry over (parameters are folded in unless they are module constants), constructors
  keep their positional and keyword forms, and a type defined in a program or procedure moves to
  the top level. An `intent(out)` record that the body does not fully assign becomes `inout`.
  Types with inheritance (`extends`), type-bound procedures, allocatable or pointer components,
  or array sizes that aren't constants become a TODO for the whole type, and code that uses it
  becomes TODOs too.
- A local that Fortran keeps between calls (declared `save`, named in a `save` statement, or
  initialised in its declaration) becomes a TODO when the procedure changes it and can read the old
  value, because Boreas locals start fresh on every call. Ones that are never changed, or always
  assigned before they are read, convert without a note.
- `optional` arguments become `T?` parameters and `present(x)` is kept, so a call that leaves off
  trailing optional arguments works unchanged. An optional argument the procedure modifies
  (`intent(out)`/`inout`) or an optional array becomes a TODO and a required argument.
- `select case` becomes Boreas `select case`. A case value that is not a literal or a
  `parameter` leaves that one construct as a TODO.
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
