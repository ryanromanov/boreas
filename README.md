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
`0.0 + 0.0i` for `complex`, `false` for `logical` and `""` for `string`. Arrays and records start that way element by element
and field by field. The one exception is an `out` parameter, which starts with no value: producing
it is the subroutine's job, so reading it first, or never assigning it, is an error.

`integer :: i, j` is an error ("declare one variable per line"). That way every
variable can carry its own comment, and a change touches only the line it is
about. `boreas convert` splits Fortran's `real :: x, y, z` into separate lines.

## Number kinds

Integers and reals come in fixed widths, named by their size:

| type | what it is |
|---|---|
| `int8`, `int16`, `integer` (also `int32`), `int64` | signed integers of 8, 16, 32 and 64 bits |
| `real` (also `real64`), `real32` | double and single precision floating point |

    int64 :: big = 3000000000
    real32 :: x = 0.1
    print big * 3, x * 3.0          ! 9000000000 0.3

- **Overflow is an error.** `integer` is 32 bits, so `2147483647 + 1` stops the program ("integer
  overflow") where Fortran wraps around silently. Integer literals too big for 32 bits are `int64`.
- **Wrapping is an operator.** `+%`, `-%` and `*%` wrap at the width of their operands, for the
  hashes and random-number generators that rely on it: `seed = (seed *% 1103515245) +% 12345`.
  Everything else is checked.
- **Mixing widths widens** to the bigger one; an integer next to a real gives that real. Assigning to
  a narrower integer works when the value fits (`int8 :: b = 100`) and is an error when it does not.
  A real never goes into an integer without `int(x)`, `int64(x)`, ...; a `real` rounds into a
  `real32` on assignment.
- **`real32` is real single precision**: every operation is rounded to 24 bits, so the results match
  Fortran's `real`. A real literal next to a `real32` is a `real32` (`x * 2.0`), as in Fortran.
  Values print with the shortest text that reads back (`0.1`, not `0.10000000149`).
- Conversion functions: `int8(x)`, `int16(x)`, `int32(x)` / `int(x)`, `int64(x)` (truncate and check
  the range), `real32(x)`, `real64(x)` / `real(x)`. `huge`, `epsilon` and `tiny` work for every kind.
- Single precision is slower than double: each result is rounded and tagged with its width.

## Strings and the system

A `string` is text of any length. Join with `+`. Take part of one with `s(lo:hi)`, counting from 1 and
including both ends (either end may be left off). Assign to part of a string with `s(lo:hi) = text`; the
text must have exactly that many characters. `len(s)` is the length, `trim(s)` removes trailing blanks (`len_trim(s)` is the length without them), `achar(n)` the character with code
`n` and `iachar(c)` the code of a one-character string.

`random()` gives a real in `[0, 1)`. `argument_count()` is the number of command-line arguments after the
program file and `argument(n)` the n-th one (`""` past the end; `argument(0)` is the program file):
`boreas prog.bor --version`.

## Input, output and stopping

`print a, b` writes a line. `write a, b` is the same without the final newline, for prompts.
Strings can use `"` or `'` quotes, so `'say "hi"'` works.

`read a, b` reads list-directed input from the keyboard: values are separated by blanks or commas,
may continue on following lines, and the rest of the last line is discarded. Each value must suit
its variable (integer, real, logical, or string; a string with spaces needs quotes). Bad input or the
end of input stops the program with an error. To handle that yourself, add a status variable:

    read n status ios        ! ios is 0 for success, -1 at the end of input, 1 for a bad value

Values before a bad one are kept. When a target is an `out` or `inout` parameter the statement starts
with `update`, as for assignments: `update read n`. `read_line()` returns the next whole line as a
`string?` (`null` at the end of input).

`stop` ends the program; `stop 3` ends it with exit code 3.

## Formatting text

Output is built as a string and printed; there is no format mini-language.

    fixed(3.14159, 3)         ! "3.142"    fixed(x, decimals [, width])
    right(42, 5)              ! "   42"    right(x, width [, padding])
    left("ab", 5)             ! "ab   "
    right(42, 5, "0")         ! "00042"    right(-42, 5, "0") is "-0042"
    repeat("-", 5)            ! "-----"

`right` and `left` take text or a number. Text that is too long is cut to its first characters; a number
that does not fit shows as stars (`*****`), as in Fortran. A width of 0 gives just the text, which is how a
number becomes text inside a longer line: `"n = " + right(n, 0)`. `fixed(x, d, w)` right-aligns in `w`
characters, dropping the leading 0 if that makes it fit (`.500`).

## Constants

    const real :: pi = 3.14159
    const integer :: primes(3) = [2, 3, 5]

A `const` needs a value and can never be assigned, used as a `do` variable, or
passed as an `out`/`inout` argument. Fortran's `parameter` converts to `const`.

## Complex numbers

`complex` holds a real part and an imaginary part, both doubles. Build one with `cmplx(re, im)`;
there is no literal syntax (`cmplx(re)` or an integer or real value widens when assigned to a
`complex`):

    complex :: z = cmplx(3.0, 4.0)
    const complex :: i = cmplx(0.0, 1.0)
    print abs(z), real(z), aimag(z), conjg(z)       ! 5.0 3.0 4.0 (3.0, -4.0)
    print z * z + 1, z ** 2, exp(i * 3.141592653589793)

- `+ - * / **` work, with integers and reals mixed in; `==` and `/=` compare. A complex cannot be
  ordered with `<` (compare `abs` or the parts), and assigning a complex to a `real` is an error
  (take `real(z)` or `aimag(z)`), where Fortran would silently drop the imaginary part.
- `abs` (the modulus), `real`, `aimag`, `conjg`, `cmplx`, `sqrt`, `exp`, `log`, `log10`, the
  trigonometric and hyperbolic functions and their inverses accept complex values, as do `sum`,
  `product`, `matmul` and `transpose`. `dot_product(a, b)` conjugates `a`, like Fortran's.
  `sqrt` of a negative *real* is still an error: write `sqrt(cmplx(x, 0.0))`.
- A complex prints as `(re, im)`. Complex values can be fields, parameters, results and array elements.

## Arrays

Arrays have any number of dimensions, 1-based and stored column-major like
Fortran. A scalar subscript drops that dimension; `:` keeps it.

    real :: m(2, 3) = reshape([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [2, 3])
    print m(1, :), m(:, 2), m(1:2, 2:3)    ! row, column, block
    m(2, :) = 0.0                          ! sections can be assigned
    print matmul(m, transpose(m))

Operators work elementwise and need matching shapes (scalars broadcast).
Built in: `size`, `shape`, `reshape`, `matmul`, `transpose`, `dot_product`,
`sum`, `product`, `maxval`, `minval`, `any`, `all`, `max`, `min`, plus these, which apply to every
element of an array:

- `sqrt`, `abs`, `exp`, `log`, `log10`, `gamma`, `log_gamma`, `erf`, `erfc`, `hypot(a, b)`
- `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2(y, x)` (`atan(y, x)` works too), `sinh`,
  `cosh`, `tanh`, `asinh`, `acosh`, `atanh`
- `real`, `int` (truncates), `floor`, `ceiling`, `nint` (halves round away from zero), `aint`,
  `anint`
- `mod` (sign of the first argument), `modulo` (sign of the second), `sign(a, b)`, `dim(a, b)`

`huge(x)`, `epsilon(x)` and `tiny(x)` give the limits of `x`'s type (see Number kinds). A math error such as `asin(2.0)`
or `modulo(1, 0)` stops the program with a message, where Fortran would give NaN.

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

## Language server

    boreas lsp

Speaks the Language Server Protocol on stdin/stdout. For now it reports the errors
`boreas check` finds as diagnostics (squiggles) when a document is opened or changed,
one error at a time because the parser stops at the first. Point any editor with LSP
support at that command for `.bor` files.

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
- Kinds become fixed-width types: `real` and `real(4)` are `real32` (Fortran's default real is single
  precision), `real(8)`, `real(kind=8)`, `real*8` and `double precision` are `real`, `integer(1/2/4/8)`
  are `int8`/`int16`/`integer`/`int64`. Kind constants work (`integer, parameter :: dp = kind(1.0d0)`,
  `selected_real_kind(15)`, `selected_int_kind(9)`, `use iso_fortran_env`), also as literal suffixes
  (`1.0_dp`, `5_int64`). Single-precision literals that are not exact (`0.1`) become `real32(0.1)`, since
  Fortran rounds them to single; `real(x)` is `real32(x)` (or the real part, for a complex `x`),
  `real(x, dp)` is `real64(x)`, `int(x, 8)` is `int64(x)`. `real(16)` and `integer(16)` are TODOs.
- `complex`, `complex(kind=8)`, `complex*16` and `double complex` become `complex`; a literal
  `(re, im)` becomes `cmplx(re, im)`; `dcmplx`, `dimag`, `dconjg`, `cabs`, `csqrt`, `cexp`, ... map to
  the generic functions.
- Old type-specific intrinsic names (`dsqrt`, `dabs`, `dsin`, `datan2`, `amax1`, `alog10`, `idint`, ...)
  become the generic function. Intrinsics Boreas lacks (Bessel functions, complex numbers, ...) stay TODOs.
- `data a, b /1, 2/, c /3*0/` becomes initialisers on the declarations (arrays fill in column order, `n*value` repeats
  a value; a 2-D array uses `reshape`). Elements, sections and implied-do loops in a `data` list are TODOs.
- `character(len=n)` variables: assigning to one pads with blanks or cuts to `n` (`a = left(x, n)`; literals are
  padded in the converted source), comparisons and `select case` ignore trailing blanks (`trim(a) == "abc"`),
  and `len(a)` becomes `n`. Lengths that are not known numbers (`len=*`), record fields and function results
  keep Boreas's variable-length strings, with a note.
- Formats become string expressions. `write(*,'(A,I5,F8.3)') s, n, x` (or a labelled `format` statement,
  before or after the write) turns into `print s + right(n, 5) + fixed(x, 3, 8)`. Translated: `A`, `Aw`,
  `Iw`, `Iw.m`, `Fw.d`, `nX`, `/`, literal text, repeat counts and groups, `advance='no'`, and implied-do
  lists with literal bounds (unrolled). More values than descriptors start the format again; a format
  with groups that would have to repeat, and `E`, `G`, `L`, `T`, `ES`, `P`, are TODOs.
- `read(*,*) a, b` and `read(*,*,iostat=ios) a` become `read a, b` and `read a status ios`; `stop`,
  `stop 3` and `error stop` become `stop`, `stop 3` and `stop 1` (a final plain `stop` is dropped; a stop
  message goes to stderr in Fortran, so it is dropped with a note). Formatted `read` is a TODO.
- `random_number(x)` becomes `x = random()` (scalars; an array is a TODO), `get_command_argument(n, s)` becomes
  `s = argument(n)`, `char`/`ichar` become `achar`/`iachar`, and `random_init`/`random_seed` are dropped with a note
  (the random numbers differ from the original's).
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

    boreas convert --verify [--timeout SECONDS] [--input FILE] FILE.f90 [-o OUT]

`--verify` converts the file as usual, then compiles the original with gfortran
(set `FC` to use another compiler), runs it and the converted program with empty
stdin (empty, or the contents of `--input FILE`) and a timeout (default 10 s), and compares what they print. Spacing and
commas are ignored, `T`/`F` equal `true`/`false`, and numbers match within 1e-9 (single-precision
output, which Fortran prints with nine digits, within half a single-precision step).

It prints "verified: ... (N values compared)" and exits 0 on a match. On a
mismatch it shows the first differing value from each program and exits 1.
Common causes are integer arithmetic that wraps around in Fortran but stops with an error in Boreas
(use `+%`/`*%`), single-precision library functions that differ in the last digit, an expression
mixing single and double precision, and constructs reported as TODO. Exit status 2
means it could not verify (gfortran missing, the original does not compile, or
it timed out).

The converted file is checked by the test suite against real gfortran output.
Try it on `examples/number_toys.f90`.

## License

MIT. See [LICENSE](LICENSE).
