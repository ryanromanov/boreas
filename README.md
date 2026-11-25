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
      int[3] :: ys = [1, 2, 3]
      println("squares:", ys * ys, square(2.5))
    end program

## Declarations

Each variable is declared on its own line, with an optional initial value:

    int :: i
    int :: count = 0
    real[3] :: v = [1.0, 2.0, 3.0]

A variable you don't give a value starts at its type's default: `0` for `int`, `0.0` for `real`,
`0.0 + 0.0i` for `complex`, `false` for `logical` and `""` for `string`. Arrays and records start that way element by element
and field by field. The one exception is an `out` parameter, which starts with no value: producing
it is the subroutine's job, so reading it first, or never assigning it, is an error.

`int :: i, j` is an error ("declare one variable per line"). That way every
variable can carry its own comment, and a change touches only the line it is
about. `boreas convert` splits Fortran's `real :: x, y, z` into separate lines.

## Number kinds

Integers and reals come in fixed widths. `int` and `real` are the full-size ones, 64 bits; the narrower ones are named
by their size:

| type | what it is |
|---|---|
| `int` (also `int64`) | signed 64-bit integer, the default |
| `int8`, `int16`, `int32` | signed integers of 8, 16 and 32 bits |
| `real` (also `real64`) | double precision floating point, the default |
| `real32` | single precision floating point |

    int :: big = 3000000000
    real32 :: x = 0.1
    println(big * 3, x * 3.0)          ! 9000000000 0.3

- **Overflow is an error.** `int` is 64 bits, so `9223372036854775807 + 1` stops the program ("integer
  overflow"); an `int32` stops at 2147483647 ("does not fit in int32") where Fortran wraps around silently.
- **Wrapping is an operator.** `+%`, `-%` and `*%` wrap at the width of their operands, for the
  hashes and random-number generators that rely on it: `seed = (seed *% 1103515245) +% 12345`
  with `int32 :: seed`. Everything else is checked.
- **A whole-number literal next to an `int32` is an `int32`**, as `2.0` next to a `real32` is a `real32`: `n + 1` and
  `seed *% 1103515245` keep their 32 bits. A literal that does not fit in 32 bits stays an `int`.
- **`integer` is now `int`.** Older programs that say `integer :: n` get an error that names the new spellings;
  `python3 tools/rename_integer.py FILE...` rewrites them (`--int32` keeps the old 32-bit meaning, for a program
  that relied on it, such as a hash that wraps).
- **Mixing widths widens** to the bigger one; an integer next to a real gives that real. Assigning to
  a narrower integer works when the value fits (`int8 :: b = 100`) and is an error when it does not.
  A real never goes into an integer without `int(x)`, `int32(x)`, ...; a `real` rounds into a
  `real32` on assignment.
- **`real32` is real single precision**: every operation is rounded to 24 bits, so the results match
  Fortran's `real`. A real literal next to a `real32` is a `real32` (`x * 2.0`), as in Fortran.
  Values print with the shortest text that reads back (`0.1`, not `0.10000000149`).
- `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2`, `sinh`, `cosh`, `tanh`, `asinh`, `acosh`, `atanh`, `exp`,
  `log`, `log10`, `hypot`, `gamma`, `log_gamma`, `erf` and `erfc` of `real32` values call the C library's
  single-precision versions (`sinf`, `expf`, ...), which is what Fortran does, so the last bit matches
  gfortran on the same machine (a double-precision `sin` rounded to single is off by one bit in a few
  percent of cases). `x ** n` with a whole exponent multiplies in single precision and `x ** y` calls `powf`,
  as gfortran does. Another platform's library can differ in the last bit, as with Fortran. If the library
  cannot be loaded, Boreas falls back to double precision rounded to single.
- Conversion functions: `int8(x)`, `int16(x)`, `int32(x)`, `int(x)` / `int64(x)` (truncate and check
  the range), `real32(x)`, `real64(x)` / `real(x)`. `huge`, `epsilon` and `tiny` work for every kind.
- Single precision is slower than double: each result is rounded and tagged with its width.
- **Whole numbers from reals.** `floor`, `ceiling` and `nint` give an `int` for a `real` and an `int32` for a `real32`
  (the width follows the argument, as `sqrt` does), so `floor(1.0e10)` is fine and `int32 :: n = floor(1.0e10)` stops
  with "does not fit in int32". A NaN or an answer beyond 64 bits is an error. For a 32-bit result from a `real`,
  write `int32(floor(x))`.
- **Real overflow is an error too.** `1.0e308 * 10.0` stops the program ("too large for a real") unless the file
  says `use ieee`; so does a negative number to a fractional power (`(-8.0) ** (1.0 / 3.0)`, which has no real
  answer: write `cmplx(-8.0, 0.0) ** (1.0 / 3.0)` for the complex root).

## Strings and the system

A `string` is text of any length. Join with `+`. Take one character with `s[i]` and part of one with `s[lo:hi]`,
counting from 1 and including both ends (either end of a part may be left off). Assign to a character or a part
with `s[i] = "x"` or `s[lo:hi] = text`; the text must have exactly that many characters. `len(s)` is the length, `trim(s)` removes trailing blanks (`len_trim(s)` is the length without them), `achar(n)` the character with code
`n` and `iachar(c)` the code of a one-character string.

More string functions (none of them changes its argument):

- `upper(s)`, `lower(s)`: the text in capitals or small letters.
- `replace(s, old, new)`: every `old` replaced by `new` (`old` cannot be empty).
- `contains(s, sub)`, `starts_with(s, sub)`, `ends_with(s, sub)`: true or false.
- `join(parts, sep)`: the strings of an array with `sep` between them, the opposite of `split`:
  `join(split("a b c", " "), "-")` is `a-b-c`.
- `index(s, sub)` is where `sub` first occurs, 0 for none; a last argument of `true` gives the last occurrence.
  `scan(s, chars)` is the first position holding any of `chars`, `verify(s, chars)` the first holding none of them
  (0 for none; `true` as the last argument counts from the end). `adjustl(s)` and `adjustr(s)` move the text left or
  right inside the same length, as in Fortran.

`random()` gives a real in `[0, 1)`. `argument_count()` is the number of command-line arguments after the
program file and `argument(n)` the n-th one (`""` past the end; `argument(0)` is the program file):
`boreas prog.bor --version`.

## Input, output and stopping

`println(a, b)` writes the values to the screen and ends the line; `print(a, b)` is the same without the
final newline, for prompts. `write(f, a, b)` and `writeln(f, a, b)` do the same to a `file` value `f`.
`println()` writes an empty line.
Strings can use `"` or `'` quotes, so `'say "hi"'` works.

Input is a line of text: `read_line()` returns the next whole line from the keyboard as a `string?` (`null`
at the end of input), and `read_line(f)` does the same for a file. Cut it up and turn the pieces into numbers:

    allocatable string[] :: p
    p = fields(read_line() ?? "")         ! "  1.5   2.0 3.7 " -> ["1.5", "2.0", "3.7"]
    x = to_real(p[1]) ?? 0.0              ! null when the text is not a number
    p = split("a,,b", ",")                ! ["a", "", "b"]: cuts at exactly the separator
    p = fields("3, 4.5", ",")             ! ["3", "4.5"]: blanks and commas both separate

`split(text, sep)` cuts at every `sep` (not empty), so empty pieces stay; `split(text, " ")` cuts at each
single blank. `fields(text)` treats a run of blanks as one gap and never gives an empty piece;
`fields(text, chars)` also treats each character of `chars` as a gap. `field(text, k)` (or
`field(text, k, chars)`) is just the k-th of those pieces, or `null` when there is none or `text` is `null`. `to_integer(text)`, `to_real(text)` and
`to_logical(text)` (`true`, `false`, `T`, `F`, `.true.`, `.false.`) give the value the text spells (blanks around
it are ignored) or `null`: `to_integer("3.5")` is `null`, `to_real("3")` is `3.0`. Assigning that `null` to a
plain `int` or `real` stops the program, so a bad value is an error unless you give a fallback with `??`.
The `to_` functions also accept `null` and give `null`, so `to_integer(field(read_line(), 1))` is one line.

`stop` ends the program; `stop 3` ends it with exit code 3.

Functions may call themselves. A program can have up to 10000 calls open at once; beyond that it stops with
"nested more than 10000 calls deep", which is nearly always a recursion with no way out.

## Formatting text

Output is built as a string and printed; there is no format mini-language.

    fixed(3.14159, 3)         ! "3.142"    fixed(x, decimals [, width])
    right(42, 5)              ! "   42"    right(x, width [, padding])
    left("ab", 5)             ! "ab   "
    right(42, 5, "0")         ! "00042"    right(-42, 5, "0") is "-0042"
    repeat("-", 5)            ! "-----"
    text(2.5)                 ! "2.5"        any value, as print shows it
    scientific(12345.678, 4)  ! "1.2346E+04"   scientific(x, digits [, width [, style]])
    general(12345.678, 4)     ! "1.235E+04"    general(x, digits [, width [, style]])
    general(123.0, 4)         ! "123"          the shorter of plain and scientific, 4 significant digits

`right` and `left` take text, a number or a logical. Text that is too long is cut to its first characters; a number
that does not fit shows as stars (`*****`), as in Fortran. A width of 0 gives just the text.

`text(x)` turns any value into the text `println` would show for it, with no width and no padding, so a number can
go inside a longer line: `"n = " + text(n) + ", done = " + text(true)`. Strings come back unchanged, and arrays
read as `[1, 2, 3]`. `fixed(x, d, w)` right-aligns in `w`
characters, dropping the leading 0 if that makes it fit (`.500`).

`tf(flag)` is `"T"` or `"F"`, the letters Fortran's `L` prints: `right(tf(done), 5)`. `right` and `left` also
take a logical on its own (`right(true, 6)` is `  true`).

`scientific` and `general` take the same optional width and the same star rule. Both also accept the style
`"fortran"`, which prints what Fortran's `Ew.d` and `Gw.d` do, so a converted program prints exactly what the
original did: `scientific(12345.678, 4, 12, "fortran")` is `  0.1235E+05` (a leading `0.`, and no `E` once the
exponent has three digits), and `general(123.0, 4, 12, "fortran")` is `   123.0    ` (fixed layout with four
blanks where the exponent would go, or the E form for very large and small values). Without a style they print
`1.2346E+04`, and `general` drops trailing zeros.

## Files

A `file` value stands for a text file. `open` gives you one, and `write`, `writeln`, `read_line`
and `close` use it:

    file :: log = open("report.txt", "write")      ! "read", "write" (create or replace) or "append"
    writeln(log, "Range:", fixed(r, 1))            ! ends the line
    write(log, "no newline")
    close log

    file :: data = open("numbers.txt", "read")
    string? :: line
    line = read_line(data)                         ! the next whole line, or null at the end
    close data

- A failed `open`, or `read_line` on a closed file or one opened for writing, stops the program with a message.
  `try_open(name, mode)` returns a `file?` that is `null` when the file cannot be opened, and
  `exists("name")` says whether a file is there.
- A file value is a handle, not a number: it cannot be mixed up with an int, it can be passed to
  a procedure or kept in a module variable, and using it after `close` (or before `open`) is an error.
  Files still open when the program ends are closed for you.
- Text files read or written one line at a time only; there is no binary or direct access yet.

## Infinity and NaN

By default Boreas stops on real arithmetic that has no ordinary answer: `1.0 / 0.0`, `sqrt(-1.0)`,
`log(0.0)`, `asin(2.0)`, `exp(1000.0)` and a result too large for its type are errors. A file can ask for
IEEE arithmetic, as Fortran has it, with one line at the top:

    use ieee

    program demo
      real :: zero = 0.0
      println(1.0 / zero, sqrt(-1.0), log(zero))     ! Infinity NaN -Infinity
      real :: x = asin(2.0)
      if x /= x then                              ! NaN is the only value that is not equal to itself
        println("no solution")
      end if
    end program

With `use ieee` those operations give `NaN`, `Infinity` or `-Infinity` and the program carries on. Comparisons
follow IEEE (every comparison with NaN is false except `/=`). `isnan(x)` and `isinf(x)` test a value (also
element by element on arrays), and `nan()` and `infinity()` make one on purpose; those four work with or
without `use ieee`. Printing writes `NaN`, `Infinity` and `-Infinity` as gfortran does, and `fixed` pads them
like Fortran's `F8.2`. Integer division by zero and turning NaN into an integer stop the program either way.
`boreas convert` writes `use ieee` into every converted program, since Fortran programs may rely on it.

## Constants

    const real :: golden = 1.6180339887
    const int[3] :: primes = [2, 3, 5]

`pi` is always there (3.141592653589793, a `real`; `real32 :: x = pi` rounds it). A program that declares its
own `pi` gets its own, so older code keeps working. A `const` needs a value and can never be assigned, used as a `do` variable, or
passed as an `out`/`inout` argument. Fortran's `parameter` converts to `const`.

## Complex numbers

`complex` holds a real part and an imaginary part, both doubles. Build one with `cmplx(re, im)`;
there is no literal syntax (`cmplx(re)` or an integer or real value widens when assigned to a
`complex`):

    complex :: z = cmplx(3.0, 4.0)
    const complex :: i = cmplx(0.0, 1.0)
    println(abs(z), real(z), aimag(z), conjg(z))       ! 5.0 3.0 4.0 (3.0, -4.0)
    println(z * z + 1, z ** 2, exp(i * 3.141592653589793))

- `+ - * / **` work, with integers and reals mixed in; `==` and `/=` compare. A complex cannot be
  ordered with `<` (compare `abs` or the parts), and assigning a complex to a `real` is an error
  (take `real(z)` or `aimag(z)`), where Fortran would silently drop the imaginary part.
- `abs` (the modulus), `real`, `aimag`, `conjg`, `cmplx`, `sqrt`, `exp`, `log`, `log10`, the
  trigonometric and hyperbolic functions and their inverses accept complex values, as do `sum`,
  `product`, `matmul` and `transpose`. `dot_product(a, b)` conjugates `a`, like Fortran's.
  `sqrt` of a negative *real* is still an error: write `sqrt(cmplx(x, 0.0))`.
- A complex prints as `(re, im)`. Complex values can be fields, parameters, results and array elements.

## Arrays

Arrays are indexed, declared and allocated with square brackets; parentheses only mean a call or grouping:
`a[i]`, `m[i, j]`, `a[2:5]`, `real[3] :: v`, `allocate g[n, 2]`, `allocatable real[,] :: m` (a comma for each
dimension after the first), `t.tags[2]` for a record's array field, and `s[2:3]` for part of a string. A
parameter says its rank: `real[,] :: m` is a two-dimensional array, `real[] :: v` a one-dimensional one, and
`real :: x` one number; the rank is checked when the call is made, and callback signatures use the same forms
(`function(real[]) -> real`). The older Fortran-style spellings (`a(i)`, `real :: v(3)`, `allocate g(n)`,
`allocatable real :: m(:, :)`, an array passed to a bare `real :: m`) are errors that name the bracket form;
`boreas check` finds the indexing ones without running the program.

Arrays have any number of dimensions, 1-based and stored column-major like
Fortran. A scalar subscript drops that dimension; `:` keeps it.

    real[2, 3] :: m = [1.0, 3.0, 5.0;
                       2.0, 4.0, 6.0]
    println(m[1, :], m[:, 2], m[1:2, 2:3])    ! row, column, block
    m[2, :] = 0.0                          ! sections can be assigned
    println(matmul(m, transpose(m)))

A section can take a step: `a[lo:hi:step]` picks `lo`, `lo + step`, ... up to `hi` (included when the step lands on it).
Either end may be left off with a positive step (`a[::2]`, `a[2::3]`, `a[:5:2]`), each dimension has its own step
(`m[::2, ::2]`), and a section can be assigned to (`a[1:9:2] = 0`). A negative step runs backwards and needs both
ends written (`a[10:1:-1]` is `a` reversed; `a[::-1]` is an error, because in Fortran an omitted end stays at the
array's own bound and the section is empty). A step of zero is an error, and a section with no elements is fine.

Operators work elementwise and need matching shapes (scalars broadcast).

Your own functions work the same way. A function whose parameters are single values (`real :: x`) can be
called with an array, and it runs once for each element: `shrink(v, 5.0)` caps every element of `v`
at 5.0. Arrays passed together need the same shape, and single values are reused for every element.
A parameter declared as an array (`real[] :: a`) still receives the whole array. A function that returns
an array or several results cannot be applied this way.

`where` changes only the elements a test selects:

```
where a > 0.0 then
  b = a * 2
elsewhere
  b = -1
end where
```

The test must be an array of logicals, and every assignment inside must have a target of the same shape.
Only assignments and other `where` blocks go inside (use `if` or a loop for anything else), and there is at most one
`elsewhere`. A `where` inside another works on the elements both masks select, and one inside an `elsewhere` on the
elements the first mask skipped, so `elsewhere (mask2)` in Fortran becomes a `where` inside `elsewhere`.
The test is worked out once, before any assignment. Each right-hand side is computed for every element,
like any array expression, and only the selected elements are stored. `where` is a statement only when the
line ends in `then`, so a variable called `where` still works.

An array literal can contain one-dimensional arrays, which are spliced in: with `x = [1, 2, 3]`, `[x, 4]` is
`[1, 2, 3, 4]` and `[0, x, x]` is `[0, 1, 2, 3, 1, 2, 3]`. A nested array is not a matrix: `[[1, 2], [3, 4]]` is
`[1, 2, 3, 4]`. Numbers of different kinds in a literal are all the widest of them (`[1, 2.5]` is `[1.0, 2.5]`),
and a literal next to an `int32` or a `real32` takes that kind, as in an operation.

A **matrix literal** has `;` between its rows: `[1, 2, 3; 4, 5, 6]` is two rows of three, written the way the matrix
reads. Every row needs the same number of values (anything else is an error), the values are single numbers, strings
or logicals, and a line break after `;` needs no `&`, so a matrix can be laid out as on paper:

    real[3, 3] :: id = [1.0, 0.0, 0.0;
                        0.0, 1.0, 0.0;
                        0.0, 0.0, 1.0]

`[1; 2; 3]` is a column, a 3 x 1 matrix (not the same as the vector `[1, 2, 3]`). A matrix is stored down its
columns, as in Fortran, but a literal and `println` both go by rows, so what you write is what you see. For the
storage order (Fortran's `print *, m`), `flatten(m)` gives the elements as one vector: `flatten([1, 2, 3; 4, 5, 6])`
is `[1, 4, 2, 5, 3, 6]`. Arrays of three or more dimensions are built with `reshape`, which fills down the columns.

A **generator** builds an array from a loop, inside an array literal: `[i * i do i = 1, 4]` is `[1, 4, 9, 16]`,
and `[i do i = 10, 1, -3]` is `[10, 7, 4, 1]` (the bounds and step work as in a `do` loop; an empty range gives
an empty array). The variable exists only inside the generator, needs no declaration, and hides any variable of
the same name. To mix a generator with other items, put it in parentheses: `[x, (0.0 do i = 1, n)]` is `x`
followed by `n` zeros. The values before `do` can be several, and each may be a number or a one-dimensional
array, which is spliced in: `[(x[k], y[k] do k = 1, n)]` is `x[1], y[1], x[2], y[2], ...`. Generators nest, inner
one first, which is how Fortran writes it: `[(a[i, j] do i = 1, n) do j = 1, m]` lists a matrix column by column.
A generator is only allowed directly inside an array literal.

`cshift(a, shift)` rotates a one-dimensional array and `cshift(m, shift, dim)` the columns (`dim` 1, the
default) or rows (`dim` 2) of a matrix. Elements pushed off one end come back at the other. It follows Fortran:
`result[i]` is `a[i + shift]`, so a positive shift moves elements toward the start
(`cshift([1, 2, 3, 4, 5], 2)` is `[3, 4, 5, 1, 2]`) and a negative one toward the end.
Built in: `size`, `shape`, `reshape`, `matmul`, `transpose`, `cshift`, `dot_product`,
`sum`, `product`, `maxval`, `minval`, `any`, `all`, `count`, `maxloc`, `minloc`, `findloc`, `pack`, `merge`,
`spread`, `norm2`, `lbound`, `ubound`, `flatten`, `max`, `min`, plus these, which apply to every element of an array:

- `sqrt`, `abs`, `exp`, `log`, `log10`, `gamma`, `log_gamma`, `erf`, `erfc`, `hypot(a, b)`
- `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `atan2(y, x)` (`atan(y, x)` works too), `sinh`,
  `cosh`, `tanh`, `asinh`, `acosh`, `atanh`
- `real`, `int` (truncates), `floor`, `ceiling`, `nint` (halves round away from zero), `aint`,
  `anint`
- `mod` (sign of the first argument), `modulo` (sign of the second), `sign(a, b)`, `dim(a, b)`

`count(mask)` is how many elements of a logical array are true (`count(v > 0)`). `merge(a, b, mask)` takes `a`
where the mask is true and `b` where it is not, element by element (`merge(v, 0, v > 0)`; each of the three is an
array or a single value, `a` and `b` must have the same type). `pack(a, mask)` is the elements of `a` where the mask is
true, down the columns, as a one-dimensional array (`pack(v, v > 0)`; a single `true` keeps everything). `maxloc(v)` and
`minloc(v)` are the position (counting from 1) of the first largest or smallest element of a one-dimensional array of
numbers or strings; unlike Fortran they give a plain int, not a one-element array. For a matrix, `maxloc(m)` gives
the subscripts as an array (`[row, col]`), and `maxloc(m, 1)` / `maxloc(m, 2)` give one position per column / per row.
A last argument of `true` (`maxloc(v, 1, true)`, `findloc(v, x, 1, true)`) picks the last of equal elements instead of the
first, Fortran's `back=`; a `dim` of 0 means the whole array, so `maxloc(m, 0, true)` is the whole-matrix answer.

`max(a, b, ...)` and `min(a, b, ...)` take two or more numbers or two or more strings (not a mix, and not
logicals), and work element by element when one is an array: `max(v, 0.0)` raises every negative element to 0.0.
Mixed numbers widen to the widest kind, so `max(3, 2.5)` is `3.0` and the answer's type never depends on which
argument wins; a real literal next to a `real32` is a `real32`, in a call as in an operation.
Functions of numbers (`sqrt`, `abs`, `floor`, `sum`, ...) refuse logicals and strings, and `==` or `/=` between a
string, a logical and a number is an error rather than `false`.

`sum` adds from the left, one element at a time, on every Python and in compiled programs, as Fortran does (the sum of a
real array is never compensated). `sum`, `product`, `maxval`, `minval`, `any`, `all` and `count` take a second argument `dim` for a matrix: `sum(m, 1)`
adds down each column (one answer per column) and `sum(m, 2)` along each row, as in Fortran; for a one-dimensional
array `dim` is 1 and the answer is a single value.

Whole numbers can be taken apart bit by bit, in the width of the number (`int` 64 bits, `int32` 32, and so
on): `iand`, `ior`, `ieor` and `bitnot` (Fortran's `not`, which is a keyword here), `ishft(i, n)` (left for a positive
`n`, right with zeros for a negative one), `shiftl`, `shiftr` (zeros come in) and `shifta` (the sign bit comes in),
`btest(i, pos)`, `ibset`, `ibclr`, `ibits(i, pos, len)`, `popcnt`, `leadz`, `trailz` and `bit_size`. Bits count
from 0 at the right. They work on arrays element by element. `bessel_j0`, `bessel_j1`, `bessel_y0`, `bessel_y1`
and `bessel_jn(n, x)`, `bessel_yn(n, x)` are the Bessel functions, from the same C library gfortran uses.

`norm2(x)` is the length of a real array (the square root of the sum of its squares). `spread(source, dim, n)` repeats
`source` (a single value or an array) `n` times along a new dimension `dim`: `spread([1, 2, 3], 1, 2)` is two rows of
`[1, 2, 3]`. `findloc(v, x)` is the position of the first element equal to `x`, or 0 (down the columns of a matrix, giving
`[row, col]`, or `[0, 0]` when there is none; `findloc(m, x, 1)` is one row per column).
`lbound(a)` and `ubound(a)` are the lowest and highest subscript of every dimension (`lbound` is always 1, since
Boreas arrays start at 1), and `lbound(a, k)` / `ubound(a, k)` give one dimension.

`huge(x)`, `epsilon(x)` and `tiny(x)` give the limits of `x`'s type (see Number kinds). A math error such as `asin(2.0)`
or `modulo(1, 0)` stops the program with a message, where Fortran would give NaN.

## Labeled loops

A loop can be given a label so that `exit` and `cycle` can target an outer loop
from inside an inner one:

    outer: do i = 1, n
      do j = 1, n
        if grid[i, j] == target then
          exit outer          ! leave the whole nest
        end if
        if skip[j] then
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
int, string or logical (not real).

    select case grade
      case 90:100
        println("A")
      case 80:89
        println("B")
      case 1, 3, 5
        println("odd and small")
      case default
        println("other")
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
        println("bronze")
      case 2
        println("silver")
        break
      case 1
        println("gold")
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

    allocatable int[] :: a
    allocatable real[,] :: m
    n = 5
    println(allocated(a))          ! false
    allocate a[n]               ! elements start at 0 / 0.0 / false / ""
    allocate m[n, 2 * n]
    println(allocated(a), size(a)) ! true 5
    deallocate a                ! may be allocated again, with any size

Allocatable arrays are locals of functions, subroutines and the program, and have no
initializer and no `const`. Anything but `allocated(a)` and `allocate` on an unallocated array
is a run-time error (reading, writing, `size(a)`, passing it, printing it, sections, whole-array
assignment). `allocate` on an allocated array and `deallocate` on an unallocated one are errors.
Extents must be non-negative integers (zero is fine). Assigning a whole array to an allocatable one
allocates it, or resizes it, to the shape of the value (`a = [1, 2, 3]`, `parts = split(line, ",")`); a
scalar value is only accepted once the array is allocated. A fixed-size array must match the shape. An allocated array can be passed to any procedure taking an array parameter; parameters
cannot themselves be `allocatable`, so a callee cannot allocate its caller's array. A leftover
allocation is freed at the end of its scope.

A module can hold an allocatable array too (`public allocatable int[] :: data`). Because every
importer sees it, allocating or freeing it is a write and says so with `@`, in a subroutine
(a function cannot): `allocate @data[n]`, `deallocate @data`, `@data[i] = x`.
Reading needs nothing special. `allocate` on a local array never takes `@`.

## Functions that return arrays

A function can return an array whose length is only known when it runs. Write `[]` after the element type in
the result (a comma for each extra dimension: `[,]` is two dimensions) and `return` an array:

    function frequencies(int :: n) -> int[]
      allocatable int[] :: out
      allocate out[n]
      do i = 1, n
        out[i] = i - 1
      end do
      return out
    end function

    allocatable int[] :: f
    f = frequencies(8)              ! allocates f, as with split
    println(sum(frequencies(4)))

Any element type works (numbers, complex, strings, records), and each element is converted to it
(`return [1, 2]` from a `-> real[]` function gives reals). The rank has to match, and the array
returned must be allocated. Procedure signatures (`function(real) -> real`) cannot return arrays yet.

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
    println(p.x + q.y, p)

- A record is a value: assigning one, passing it, or returning it copies it, so two variables never
  share fields. Records can hold other records and fixed-size array fields (`int[3] :: tags`,
  with a literal size), and you can make arrays of records (`point[10] :: pts`).
- Fields start at their type's default value (see Declarations), nested records and array fields
  included, so `point :: p` is a usable zero point. A field may declare its own default with a
  constant expression (literals, `const` names from the same module, arithmetic, array literals,
  record constructors, and built-in functions such as `sqrt`; not variables or your own functions):

      record config
        int :: count = 5
        real :: scale = 1.0
        logical :: verbose
      end record

      config :: c                    ! count = 5, scale = 1.0, verbose = false
      config :: d = config(scale = 2.5)

  A constructor overrides defaults, and fields it leaves out keep them.
- `const point :: origin = point(0.0, 0.0)` makes a read-only record. Writing a field of a `const`
  record or an `in` parameter is an error.
- A write to a field of an `out` or `inout` parameter needs `@`, like any write the caller sees:
  `@p.x = p.x + dx`. An `out` record parameter starts with no values, except for fields that
  declare a default, and must have every field assigned before the subroutine returns. Two fields of one record may be passed as separate `out`/`inout` arguments;
  the same field twice (or a record and one of its fields) is an aliasing error.
- Records cannot be compared or used in arithmetic (compare the fields), cannot be `select` subjects,
  and cannot be `allocatable` yet. In a module, a record is private unless marked `public`, and is
  imported by name: `use geometry: point`. See `examples/records.bor`.

## Functions in records, interfaces and `select type`

A record can hold the functions that work on it. The first parameter is `self`, which has the record's type; write
`inout self` for a subroutine that changes the record. Call them with a dot:

    record circle
      real :: r = 1.0

      function area(self) -> real
        return 3.14159 * self.r ** 2
      end function

      subroutine grow(inout self, real :: factor)
        @self.r = self.r * factor
      end subroutine
    end record

    circle :: c = circle(r = 2.0)
    println(c.area())
    call @c.grow(2.0)              ! c.r is now 4.0

A function in a record is an ordinary function otherwise: the same parameter modes, `@` rules and `const`
checks apply (`call @c.grow(2.0)` on a `const` record is an error). Two records can both have an `area`. A function
and a field of one record cannot share a name, and a function can only be declared inside its record.

An **interface** lists the functions a caller needs, with `self` standing for the value passed. A variable, array
element, parameter or result whose type is an interface can hold any record that has those functions, and a call
runs the function of the record it holds:

    interface figure
      function area(self) -> real
    end interface

    record rect is figure          ! `is` is optional; when written it is checked
      real :: w = 1.0
      real :: h = 1.0
      function area(self) -> real
        return self.w * self.h
      end function
    end record

    subroutine report(figure :: f)
      println(f.area())
    end subroutine

    figure[2] :: all
    all[1] = circle(r = 2.0)
    all[2] = rect(w = 3.0, h = 4.0)
    call report(all[2])

- A record fits an interface if it has every listed function with the same parameters (not counting `self`), modes
  and result type. It may have more. `record rect is figure` says so out loud and is checked when the program loads;
  without it the check happens when a value is assigned or passed.
- Only the listed functions are available through an interface value (a variable, element, parameter or function
  result), and its fields are not (`f.w` is an error). If you need a value, add a function that returns it.
  `println` of an interface value still shows the whole record.
- An interface value is a copy, like any record. An element that was never assigned cannot be used.
- Assigning an interface value to a variable of a record type works when it holds that record right now, and is an
  error when it holds another; `select type` is the way to ask first.
- A variable of a record type can be passed to an `inout` or `out` parameter of an interface type (as Fortran allows
  for `class(shape), intent(inout)`). The routine can change the record, or put in a new one of the same kind, but it
  is an error if it puts in a different kind (a `rect` into a `circle` variable).
- A record field can have an interface type (`figure :: highlight`, `figure[2] :: items`). It starts unassigned,
  and using it before it is set is an error, as for an interface variable. Only the interface's functions are
  available through it (`sc.items[1].area()`). See `examples/scene.bor`.
- An interface cannot be named like a built-in (`shape` is one) or a record. Not supported yet: an
  `allocatable` array of an interface type, and optional interface values (`figure?`).
- Interfaces are exported and imported by name from a module (`public interface figure`, `use shapes: figure`).

`select type` finds out which record an interface value holds:

    select type all[2] as p
      case rect
        println("width", p.w)      ! p is a copy of the value, as a rect
      case circle
        println("radius", p.r)
      case default
        println("something else")
    end select

`case default` is required, because a new record can fit an interface at any time. The default may be empty. A case
that names a record matches exactly that record. A case that names an interface matches any record that fits it, and
inside it `p` has only that interface's functions:

    select type all[2] as p
      case circle                 ! a circle always takes this case
        println("radius", p.r)
      case sized                  ! any other record with sized's functions
        println("area", p.area())
      case default
        println("something else")
    end select

Record cases come first, then the first interface case that fits. In the default case `p` has the interface's type. `p` is a
copy: write it back (`all[2] = p`) to change the original. `break`, `exit` and `cycle` work as in `select case`.

To test for one type, an `if` can ask directly:

    if all[2] is rect as r then
      println("width", r.w)         ! r is a copy of the value, as a rect
    else if all[2] is sized as s then
      println("area", s.area())     ! an interface name matches any record that fits it
    else
      println("something else")
    end if

`as r` is optional (`if x is rect then` only tests). The name exists only in its own branch, and it is a copy, so write
it back to change the original. No `else` is needed. The test is only allowed as the whole condition of an `if` or
`else if`; combine it with other conditions by nesting a second `if`. `is` and `as` are only special in this position,
so variables with those names still work.

## Optional values

A plain `int`, `real`, `logical`, `string` or record always holds a value. Add `?` to the type
and the variable may instead be absent (`null`):

    int? :: n                       ! starts as null
    println(present(n), n == null)         ! false true
    n = 4
    println(n ?? 10)                       ! 4; `a ?? b` is a, or b when a is null
    n = null

- `null` only goes into a `?` type; assigning it to a plain one is an error. An optional variable
  starts as `null`, where a plain one starts at its type's zero.
- Using an absent value is an error: arithmetic, ordering, conditions and passing it to a plain
  parameter all fail with a message. What you can do with one: test it (`present(x)`,
  `x == null`, `x /= null`), give it a fallback (`x ?? y`), store it, print it (`null`) and pass it
  to another optional parameter. `??` is the loosest operator (`n ?? 0 + 1` is `n ?? (0 + 1)`),
  chains left to right, and only evaluates its right side when the left is null.
  A number on the right takes the kind of the optional on the left: `real32? :: x` with `x ?? 0.5` is a `real32`.
- `in` parameters may be optional, and such arguments can be left off the end of a call
  (`step_or()` for `function step_or(int? :: step)`); they arrive as `null`. Only trailing ones can be left
  off; to skip one in the middle, pass `null`.
- A function may return `-> int?` to say "no result" (`return null`).
- Record fields can be optional (`address? :: home`); they start as `null`. Reading a field
  through a null record is an error (`p.home.city` when `p.home` is null). A record still cannot
  contain itself, even optionally.
- Not supported: optional `out`/`inout` parameters, arrays of optional values, `?.` chaining.

## Maps and sets

A map looks values up by key; a set remembers which members it has. Keys and members are integers or strings,
and a map's values are single values (numbers, strings, logicals). They start empty and grow by themselves:

    map[string, int] :: age
    set[string] :: seen

    age["ann"] = 31                    ! insert, or replace the value of an existing key
    println(age["ann"])                ! 31; a key that is not there is an error
    println(get(age, "bob") ?? 0)      ! get gives int? (null when missing), so ?? supplies a default
    println(has(age, "ann"), size(age))
    call remove(@age, "ann")           ! removing a key that is not there is an error: test with has first

    call add(@seen, "x")               ! adding a member that is already there does nothing
    println(has(seen, "x"), size(seen))

- Loop with `do key, value in age` (a set takes one variable: `do word in seen`). The variables are declared
  first, like any loop variable. The order is the order the keys were first added, so a program prints the same
  every run. The loop walks a snapshot, so the body can add to or remove from the collection safely. `exit`,
  `cycle` and labels work as in any `do`:

      string :: name
      int :: years
      do name, years in age
        println(name, years)
      end do

- `do x in a` also walks an array's elements (column-major, like everything else), so `do name in keys(age)` and
  `do v in values(age)` loop over just the keys or just the values. For the position as well, use
  `do i = 1, size(a)`. `keys(age)` and `values(age)` give the arrays in the same order (`keys(seen)` gives the
  members), for when you want the whole list at once, to sort it or to index it.
- `println(age)` shows `{ann: 31}` and a set `{x, y}`. Counting words: `count[w] = (get(count, w) ?? 0) + 1`.
- Assigning copies, like an array: after `b = a`, changing `b` leaves `a` alone. A declaration can start from a
  copy (`map[string, int] :: b = a`). `==` and `/=` compare two maps (or two sets) of the same type by
  contents, in any order: the same keys with equal values, or the same members. They cannot be ordered with `<`, or used in arithmetic.
- A literal builds a whole collection in one go: `age = {"ann": 31, "bob": 27}` for a map, `seen = {"x", "y"}`
  for a set, and `{}` for an empty one (it is a map or a set, whichever the variable is). A literal takes the type
  of what it is stored in, so keys and values are checked and converted as in an assignment (`{"pi": 3}` into a
  `map[string, real]`). It works wherever a value is stored: an assignment, an argument, a result, a map value
  (`groups["fruit"] = {"apple", "pear"}`, or nested, `{"fruit": {"apple"}, "none": {}}`) and a comparison
  (`age == {}`). A key or member listed twice is an error. A literal on its own, like `println({"a": 1})`, shows
  as written. A record field cannot have one as a default (it starts empty).
- `add` and `remove` are subroutines built in, so they are called with `call`, and mark the collection they change, like `call swap(@x, @y)`. `map` and
  `set` are not reserved: they are types only before `[` and a type name, so variables called `map` still work.
- A map or set can be a parameter, a function result and a record field, as well as a local variable. A
  parameter is read-only by default (`in`) and is not copied; `inout` changes the caller's map (`@m[k] = v`, or
  `call add(@m, k)`), and an `out` one starts empty. A result is copied into the variable it is assigned to. A
  record field starts empty and is used like a variable (`b.stock["pen"] = 3`, `call add(@b.tags, "x")`);
  copying a record copies its maps and sets too. They also work in a callback's signature
  (`function(map[string, int]) -> int :: f`).
- One entry of a map can be passed to an `inout` or `out` parameter: `call bump(@count[w])` changes that entry
  (`inout` needs the key to exist; `out` creates it). Two entries with different keys are fine in one call; the
  same entry twice is refused as aliasing.
- A module can hold a map or a set as a module variable (`public map[string, int] :: hits`). It starts empty,
  and the usual module-variable rules apply: a subroutine changes it with `@hits[k] = v` or `call add(@seen, x)`,
  a function may only read it, and other code imports it with `use`.
- A map's value can also be a set or an array: `map[string, set[string]]` (a graph, or words grouped by letter)
  or `map[string, int[]]` (`int[,]` for a matrix). A missing outer key is an error, as always, so
  store one first and then change it in place:

      map[string, set[string]] :: groups
      groups["fruit"] = {"apple"}            ! the map keeps its own copy (here, built from a literal)
      call add(@groups["fruit"], "pear")      ! changes the stored set
      println(size(groups["fruit"]), has(groups["fruit"], "apple"))
      do w in groups["fruit"]
        println(w)
      end do

  `groups["fruit"]` can be read, measured, searched and looped over, and passed to an `inout` or `out`
  parameter (`call double(@lists["a"])`). An entry that is an array can be indexed in place with a second
  subscript: `lists["a"][2] = 99`, `x = lists["a"][1]`, `lists["a"][1:2]`, `lists["m"][i, j]` for a matrix,
  and `call bump(@lists["a"][2])`. This is the only place a second subscript is allowed (there are no arrays
  of arrays, so `a[1][2]` on an ordinary array is an error: write `a[1, 2]`). Storing and reading a whole entry
  always copy, so the map never shares a set or array with a variable. `values(m)` is refused
  for these maps (it would be an array of sets); loop with `do k in keys(m)`. A map cannot hold maps or
  optional values, and a set's members stay single values.
- A map or set can be `const`, with a literal or another collection as its value
  (`const map[string, int] :: limits = {"a": 1, "b": 2}`, also `public const` in a module). It can be read,
  looped over and copied, but not assigned to, added to or removed from, or passed as `inout` or `out`.
  `allocatable` makes no sense (a collection is always there), so it is refused.
- Not supported yet: arrays of them and an optional one. See `examples/collections.bor`, and
  `examples/collections_limits.bor` for each refusal with its exact error message and what to write instead.

## Subroutines and parameter modes

Which to use:

- To hand back new values, write a function with results (see "Functions with several results" below):
  `(q, r) = divmod(17, 5)`.
- To change something that already has a value (swap, sort, update a record), use `inout`.
- Use `out` when the caller owns the storage, such as an output array it already has, and for converted
  Fortran, where `intent(out)` is everywhere.

A subroutine changes the caller's variables through `out` / `inout` parameters:

    subroutine swap(inout int :: a, inout int :: b)
      int :: t
      t = a
      @a = b
      @b = t
    end subroutine

    call swap(@x, @y)

`@` marks what changes, in both places. Every write to an `out` / `inout` parameter is marked with `@` in
front of the variable: `@a = b` changes the caller's `a`, while `t = a` only changes a local. And the call
marks the arguments that get changed: `call swap(@x, @y)` says that `x` and `y` are updated, so a reader of
the call does not have to open `swap` to find out. An argument for an `out` / `inout` parameter must carry
`@`, and no other argument may; a module variable's writes carry the same mark. Using `@` on anything else,
or leaving it off, is an error (`boreas check` finds these without running). `call` stays on every call
statement: a subroutine can also print, change a module variable or stop the program, none of which shows in
its arguments. (The old keyword `update` is an error that points to `@`.) For a record's function the record
is the first argument, so it is marked when the function changes it: `call @c.grow(2.0)`.
Parameters default to `in` and cannot be assigned. Functions only take
`in` parameters. An output variable may not be passed twice in one call, and an
`out` parameter must be assigned before the subroutine returns.

## Functions with several results

A function can give back more than one value. List the results in the header, `return` that many values, and
unpack them at the call:

    function divmod(int :: a, int :: b) -> (int :: quotient, int :: remainder)
      return a / b, mod(a, b)
    end function

    int :: q
    int :: r
    (q, r) = divmod(17, 5)            ! q is 3, r is 2
    (q, _) = divmod(100, 7)           ! `_` throws a result away: only the quotient

- The names in the result list are documentation (they can be left off: `-> (int, real)`) and are not
  compared. At least two results; for one, write `-> int` as before.
- The unpacking is the only place that can receive several results: `x = divmod(1, 1)`, `println(divmod(1, 1))`,
  an argument, or a call on its own line are errors that tell you to unpack. It needs exactly as many targets
  as the function has results, and the same variable cannot appear twice. `boreas check` and the editor report
  these mistakes without running the program (for a plain call of a function it can see; a call on a
  record or through a callback is only checked when it runs).
- Each result is converted to its declared type, as for a single result, and then assigned like any
  assignment (an array result into an `allocatable` target allocates it).
- A target the caller sees (an `out` / `inout` parameter or a module variable) carries its own `@`, exactly
  as a single assignment does: `(@quotient, rest) = divmod(n, 5)`. A function cannot write a module variable.
- Results can be any type: numbers, strings, records, arrays (`-> (real[] :: values, int :: n)`) or optional
  (`int?`). They work for functions in records and in interfaces (a record fits when the result types
  match in order, names aside): `(lo, hi) = s.bounds()`.
- A callback's signature can list several results too (see Procedure parameters below). Not supported:
  `return g()` to pass on another function's results, and subroutines (which keep `out` / `inout`). See
  `examples/multiple_results.bor`.

## Procedure parameters (callbacks)

A parameter can be a procedure. Its signature is written inline, like a header without the name; the
parameter names are optional documentation:

    function integrate(function(real :: x) -> real :: f, real :: a, real :: b) -> real
      ...
      total = total + f(a + i * h) * h        ! call it like any function
    end function

    subroutine run_all(subroutine(string, inout int) :: report, inout int :: n)
      call report("done", @n)
    end subroutine

    function square(real :: x) -> real
      return x * x
    end function

    println(integrate(square, 0.0, 1.0))      ! pass the bare name of a procedure

- The match is exact: the same number of parameters, the same types and modes, the same result type, and a
  function for `function(...)`, a subroutine for `subroutine(...)`. The error names what differs.
- Only `in` parameters can be procedures. Inside a signature only a subroutine may have `out` / `inout`
  parameters, and no parameter may be optional (`?`) or another procedure.
- A bare name that is also a variable means the variable, so it must itself be a procedure parameter (which
  lets you pass a callback on to another procedure).
- A built-in such as `sin` cannot be passed directly: write a small function that calls it and pass that.
- A callback can return an array or several results. Write the result as in a function header:
  `function(real :: t) -> real[] :: f` (an array: the element type and the rank, `real[,]`, must match, never the
  size) or `function(real :: x) -> (real :: lo, real :: hi) :: g`. A procedure passed to it must have the same
  result type, or the same number and types of results (names are documentation). A callback with several
  results is called like any such function, by unpacking: `(a, b) = g(3.0)`; `boreas check` finds a call that
  uses it as a single value.
- A procedure cannot be stored in a variable yet, only passed as an argument.

## Modules

A module groups constants and procedures. Everything in it is private unless
marked `public`, and a program (or another module, or a single procedure) imports
exactly the names it uses:

    module geometry
      public const real :: tau = 6.283185307179586

      public function area(real :: r) -> real
        return tau / 2 * r ** 2
      end function
    end module

    program demo
      use geometry: area, tau
      println(area(2.0))
    end program

Nothing is visible without a `use`. Importing a private or missing name is an
error, an imported constant is read-only, and modules cannot import each other in
a cycle. See `examples/modules.bor`.

A module can also hold variables. They keep their value between calls, and every write
to one is marked with `@`, the same mark that marks a write to an `out` parameter:

    module counter
      public int :: hits = 0

      public subroutine hit()
        @hits = hits + 1
      end subroutine
    end module

    program demo
      use counter: hits, hit
      call hit()
      call hit()
      println(hits)          ! 2
      @hits = 0
    end program

Any code that imports a variable can read it, and can write it with `@`. A function
cannot change a module variable (only a subroutine can), so functions stay free of side effects. That includes
passing a module variable to an `out` or `inout` parameter, and calling a subroutine that changes one, even through other
subroutines: both are refused when the program loads.
A module variable can be `allocatable`; see Allocatable arrays above.

## Compiling to a native program (so far)

    boreas build prog.bor -o prog          compile with the system C compiler (cc, clang or gcc)
    boreas emit-c prog.bor                 print the C it writes

The compiler handles the numeric core of the language so far: `int`, `int32`, `real`, `real32` and `logical` values and arrays of
them (up to three dimensions, element by element or whole: `a = b * 2.0 + c`, bounds checked), `if`, `do`, `do while`,
`exit`, `cycle`, `stop`, `return`, `print` and `println`, and functions and subroutines whose parameters are single
values or arrays, passed `in`, `inout` or `out` (the last two in subroutines); allocatable arrays (`allocate`, `deallocate`,
`allocated`, and `a = <array>` resizing them); array sections (`a[2:4]`, `m[:, 1]`, `a[8:1:-2]`, read and assigned, and as
`in` arguments); also `size`, `maxval`, `minval`, `sum`, `product`,
`dot_product` and `matmul` (two real, two real32 or two int arrays), the inverse and hyperbolic trigonometric functions, `atan2`,
`erf`, `floor`, `ceiling`, `nint`, `aint`, `anint`, `sign`, `modulo`, `dim`, `int32`, `int64`, `real64`, `huge`, `epsilon`, `tiny`,
and `reshape` (with the shape written out), `transpose`, `flatten`, `shape`, `merge`, `any`, `all`, `count`, `maxloc`,
`minloc` (of vectors and matrices), `findloc`, `pack`, `spread`, `lbound`, `ubound` and `norm2` (added exactly, as the interpreter does); the bit functions (`iand`, `ishft`, `btest`, `popcnt`, ... on `int` and `int32`), `to_integer`, `to_real` and `to_logical`, `nan`, `infinity`, `isnan` and `isinf`; and records whose fields are numbers, logicals, text or other records (defaults, constructors with
positional or named values, `p.x = ...`, `a.b.c`, printing, as `in` or `inout` parameters and as function results, and a
record's own functions: `x.area()`, `call @x.grow(2)`). Text (ASCII only): `string` variables, parameters and results, `+`, the
comparisons, `s[i]`, `s[lo:hi]`, `len`, `len_trim`, `trim`, `upper`, `lower`, `repeat`, `index`, `contains`, `starts_with`,
`ends_with`, `scan`, `verify`, `replace`, `adjustl`, `adjustr`, `iachar`, and the formatting functions `text`, `tf`, `right`, `left`, `fixed`, `scientific` and `general` (with the
`"fortran"` style). Fixed-size arrays of records (one dimension: elements, `a[i].x`, whole-array copies, literals, `in` and `inout` parameters, `size`) work too. `select case` (statement and expression) works on whole numbers, text and logicals with literal case values. Records may have fixed-size array fields (`real[3] :: v`, `figure[2] :: items`; elements only, not the whole array) and interface-typed fields. Modules work: constants and variables of numbers, logicals and text (a variable is written with `@`), public and private procedures, records and interfaces, and `use` at the top of the program or inside a procedure; array or `allocatable` module variables are not compiled yet. Complex numbers work with Python's own arithmetic (`+ - * / **` and `==`, mixed with whole numbers and reals; `abs`, `real`, `aimag`, `conjg`, `cmplx`; `sum`, `product`, `dot_product`, `matmul`, `transpose` of arrays that certainly have an element), as scalars, array elements, record fields, parameters and results; `sqrt`, `exp`, `log`, `log10`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `sinh`, `cosh`, `tanh`, `asinh`, `acosh` and `atanh` of a complex number or array use CPython's own cmath code; `complex?` and complex module variables are not compiled yet. `where mask then ... elsewhere ... end where` works (nested, on whole arrays or sections of numbers and logicals; the expressions inside are variables, literals, operators, subscripts and math functions). The Bessel functions (`bessel_j0` ... `bessel_yn`, of single values), `exists`, `argument_count` and `argument(i)` (from 1) work. Procedure parameters (callbacks) work: pass the name of a function or subroutine whose signature is exactly the parameter's (numbers, text, arrays, maps, sets, records, several results), call it inside, or pass it on; a callback that takes another procedure is not compiled yet. Functions may return an array of numbers or logicals (`-> real[]`, `-> int[,]`; the value must have that rank). Functions with several results work for numbers and logicals (`(q, r) = divmod(a, b)`, `(q, _) = ...`, targets such as `x`, `a[i]`, `@out`); those of a record or interface, and results that are text, arrays, records or optional values, are not compiled yet. Maps and sets with whole-number or text keys (`map[string, int]`, `set[int]`, `map[int, logical]`, ...) work as local variables and `in`, `inout` and `out` parameters: `m[k]`, `m[k] = v`, `has`, `get`, `size`, `keys` and `values` (of whole-number keys and non-text values), `call add` and `call remove`, literals, copies, `==`, `do key, value in m` and `do x in a`, printed and ordered as in the interpreter; they also work as fields of a record (empty at first, copied and printed with it; `r.m[k]`, `has(r.m, k)`, `call add(@r.m, x)`, `do k in r.m`); a map's values may be sets or arrays of whole numbers or reals (`map[string, set[string]]`, `map[string, int[]]`: `g[k]` is changed in place with `call add(@g[k], x)` and `m[k][i] = v`, and stored and read as copies); an entry (`m[k]`, `r.m[k]`) or an element of an array entry (`m[k][i]`) can be passed as an `inout` or `out` argument (`call bump(@m[k])`), also two entries of one map (equal keys are an error), and functions may return a map or a set; maps and sets as module variables, `const` ones, `do ... in` over a map with such values, and `==` of maps of arrays are not compiled yet. Optional values (`int? :: n`, `address? :: home`, `null`, `present()`, `??`, `==` and `/=` against `null`, optional `in` parameters that may be left off, `-> int?` results, optional fields) work for numbers, logicals, text and records; an optional value in arithmetic stops with the interpreter's message when absent. Interfaces work for variables, `in` and `inout` parameters, results and fixed arrays (`f.area()`, `call @f.grow(2)`, `select type` and `x is circle as c` on a variable or element). A program that uses anything else (arrays or maps of text,
interfaces, modules, functions that return arrays, ...) is refused as a
whole, with a message that names the construct and its line ("not supported by the compiler yet: field 's' of record
'p' (string[2]) (line 3)"): run it with the
interpreter instead, or wait for the next slice. Nothing is ever compiled halfway.

A `real32` in a compiled program is worked out as the interpreter works it out: `+ - * /` between singles, or between a
single and a whole number, are done in double precision and rounded to single once (the same bits as single-precision
hardware for two singles), `**` multiplies for a whole-number exponent and calls `powf` otherwise, `sin cos tan exp log
log10` call the C library's `sinf`, `expf`, ... and `sqrt` is correctly rounded. `sum`, `product`, `dot_product` and
`matmul` of `real32` values round to single at every step, left to right, as gfortran does (`sum` of ten `0.1` is
`1.0000001`; sum into a `real` for more accuracy). A `real32` prints as the shortest text that reads back
as the same single (`100.0`, `0.1`, `1e-05`).

A compiled program prints exactly what the interpreter prints, and fails the way it fails: the same message on stderr
(with the same line number, also inside a function) and the same exit code. Numbers print the same, digit for digit;
operands and arguments are evaluated left to right, and `and` / `or` evaluate both sides, as in the interpreter.
Whole-number overflow, division by zero, a domain error such as `sqrt(-1.0)`, and calls nested more than 10000 deep are
all still errors. A 120 by 120 matrix product written with loops takes 0.04 seconds compiled (about what gfortran -O2 takes) and
about 18 seconds in the interpreter; a loop of two million steps with a square root in it, 0.06 and 28.
`python3 tools/bench.py` times both against the interpreter and gfortran.

`--release` leaves out the whole-number overflow checks and the call depth check (the answers then wrap around, as in
Fortran), and the program is a little faster; everything else is unchanged. `--keep` also writes `prog.c`. The
tests compare the compiler with the interpreter on every program in `tests/compiled/`, and `python3
tools/compile_oracle.py` does the same for every program the test suite runs.

## Checking a file

    boreas check FILE.bor [--json]

Parses and checks a file without running it. Besides syntax errors it reports every name
that is used but never declared, every unknown function or subroutine, and every misuse of a function with
several results (outside an unpacking, or with the wrong number of targets). A syntax error
stops the check, so it is reported alone. It also reports mistakes of type it is sure of, in the interpreter's own
words: `cannot assign real to int`, `operator '+' needs numbers, got string`, `cannot compare int with string using '<'`,
`condition must be logical, got int`, `array index must be integer`, a call with the wrong number of arguments or an
argument of the wrong type, a result of the wrong type, and a field a record does not have. (A narrowing between
whole-number widths is allowed here, as it is when running, and checked against the value then.) Errors print as `file:line:col: message`
(column 1 when the error has no column) and the exit status is 1; a clean file prints
nothing and exits 0. `--json` prints `{"file": ..., "errors": [{"line", "col", "message"}]}`.

Common slips from Fortran, C and Python get a message that says what to write instead: `.eq.` and `.and.`
(write `==` and `and`), `!=` (`!` starts a comment here; write `/=`), `&&`, `||`, `%`, `^` and `//` (write `and`,
`or`, `mod(a, b)`, `**` and `+`), `x += 1` and `x++`, `print *`, `endif` and `elseif` (two words each), `while` and
`for` loops, `continue` (write `cycle`), `real*8`, a stray `;`, and `return` in a program (write `stop`). A name that is
almost right gets "did you mean ...?". These are only looked up after a parse has failed, so they never change what a
correct program means.

## Formatting

    boreas fmt [--check] [--indent N | --tabs] FILE.bor [FILE...]

Re-indents each file in place by its blocks: 2 spaces a level unless `--indent` or `--tabs`
says otherwise. A `case` line sits one level inside its `select`, and its body one level
deeper. Only the whitespace at the start and end of lines changes: comments, blank lines,
`&` line breaks, the spacing inside a line and the capitalisation of names stay as written,
and a line continued after `&` keeps how far it was indented past the statement's first line.
`--check` changes nothing; it lists the files that would change and exits 1 if there are any.

## Language server

    boreas lsp

Speaks the Language Server Protocol on stdin/stdout. It reports the errors `boreas check`
finds as diagnostics when a document is opened or changed, and suggests completions:
keywords, types, built-ins, the names declared in the file, and record fields after `.`.
It also answers go to definition (a name's declaration in the file, including record fields
and imported module names) and hover (the declaration, or "built-in function"), and formats
a document the way `boreas fmt` does, with the indent width and tabs or spaces the editor sends.
Point any editor with LSP support at that command for `.bor` files.
[Graupel](https://github.com/ryanromanov/graupel), a Mac editor, finds it by itself;
`editors/graupel` has its Run, Check and Convert scripts.

## Converting Fortran

    boreas convert legacy.f90 -o legacy.bor

Reads free-form Fortran (not fixed-form `.f`) and writes Boreas source plus a
report on stderr. Translations that change meaning are flagged, never silent:

- `! TODO(convert): ...` comments quote any line that could not be translated
  (`goto`, `read`, generic bindings, ...).
- `parameter` constants become `const`.
- `allocatable` locals, `allocate` / `deallocate` (one statement per array) and `allocated()` are
  translated. `stat=`, `errmsg=`, `source=`, `mold=`, non-1 lower bounds, allocatable
  dummy arguments and results become TODOs. Allocatable module variables work: `@` is added to their
  `allocate`, `deallocate` and element writes, and a function that allocates one becomes a subroutine. A whole-array assignment to an
  allocatable with no earlier `allocate` gets a note, since Fortran allocated it automatically.
- Derived types become records, with `%` turned into `.` and `type(point)` into `point`. Component
  defaults carry over (parameters are folded in unless they are module constants), constructors
  keep their positional and keyword forms, and a type defined in a program or procedure moves to
  the top level. An `intent(out)` record that the body does not fully assign becomes `inout`.
  Allocatable or pointer components, generic, final or `nopass`/`pass(arg)` bindings, parameterized
  types, or array sizes that aren't constants become a TODO for the whole type, and code that uses it
  becomes TODOs too.
- Object-oriented Fortran becomes Boreas records and interfaces (see "Functions in records, interfaces and
  `select type`"). An extension (`extends`) is a record with its parent's components first. Each type-bound
  procedure becomes a function inside the record, **copied into every extension that inherits it** (an
  extension's own version replaces it), so `self` is always the record itself and dynamic dispatch picks the right
  one. An abstract type, and a normal type that others extend and that is used as `class(T)`, become an interface
  (for a normal type the record keeps its name and the interface is `T_any`); `class(T)` is that interface, and
  `x%f()` is `x.f()`. `allocate(s, source=e)` and `allocate(T :: s)` are assignments, `select type (p => x)` is
  `select type x as p` (with an empty `case default` added when Fortran has none, and `x = p` written back after a
  case that changes `p`; a select with a single `type is` and no default becomes `if x is T as p then`), and a
  type that only holds `class(T), allocatable` (the wrapper Fortran needs for an array of mixed types) is dropped:
  `type(wrapper) :: all(3)` is `figure[3] :: all` and `all(i)%s` is `all[i]`.
  A type called like a Boreas built-in (`shape`) is renamed (`shape_`). `class is (T)` becomes
  `case T_any` when its body only calls functions, and otherwise one case for each type that extends `T`.
  An inherited procedure whose parent is in another module is copied into the child's module when everything it
  uses is public there (the converter adds the imports); a private name it needs is a TODO that names it.
  A `final` binding is dropped with a TODO (Boreas has no destructors); the procedure stays and is not called
  automatically.
  A type that holds a `class(T), allocatable` component next to other components, or an array of the wrapper type,
  becomes a record with a field of the interface type (`type(slot) :: items(2)` is `shape_[2] :: items`).
  `procedure, pass(c) :: add` moves the argument `c` to the front as `self` (calls do not change).
  A `select type` case that changes `p` writes it back at its end and before each `exit`, `cycle` and `return`.
  Still TODOs: `class(*)`, `nopass`, `generic` bindings, and `allocated()` and `deallocate()` of a `class(T)`
  value. See `examples/shapes.f90`.
- A local that Fortran keeps between calls (declared `save`, named in a `save` statement, or
  initialised in its declaration) matters only when the procedure changes it and can read the old value.
  If the procedure is called from just one place that runs once, a fresh start each call behaves the same,
  so it stays a local (with a note). Otherwise the variable moves to a module of its own
  (`<procedure>_state`) as a public module variable, and writes to it are marked `@`. A declaration
  that uses other names (so it cannot move) stays a TODO. Ones that are never changed, or always assigned
  before they are read, convert without a note.
- Kinds become fixed-width types: `real` and `real(4)` are `real32` (Fortran's default real is single
  precision), `real(8)`, `real(kind=8)`, `real*8` and `double precision` are `real`, `integer(1/2/4/8)`
  are `int8`/`int16`/`int32`/`int` (Fortran's default integer is 32 bits, so it becomes `int32`: the same arithmetic,
  wrap-around included). Kind constants work (`integer, parameter :: dp = kind(1.0d0)`,
  `selected_real_kind(15)`, `selected_int_kind(9)`, `use iso_fortran_env`), also as literal suffixes
  (`1.0_dp`, `5_int64`). Single-precision literals that are not exact (`0.1`) become `real32(0.1)`, since
  Fortran rounds them to single; `real(x)` is `real32(x)` (or the real part, for a complex `x`),
  `real(x, dp)` is `real64(x)`, `int(x, 8)` is `int(x)`, and a bare `int(x)` is `int32(x)`. `real(16)` and `integer(16)` are TODOs.
- `complex`, `complex(kind=8)`, `complex*16` and `double complex` become `complex`; a literal
  `(re, im)` becomes `cmplx(re, im)`; `dcmplx`, `dimag`, `dconjg`, `cabs`, `csqrt`, `cexp`, ... map to
  the generic functions.
- Old type-specific intrinsic names (`dsqrt`, `dabs`, `dsin`, `datan2`, `amax1`, `alog10`, `idint`, ...)
  become the generic function. Intrinsics Boreas lacks (Bessel functions, complex numbers, ...) stay TODOs.
- A function that changes its arguments (`integer function f(n)` with `intent(inout) :: n`) becomes a
  subroutine whose last parameter is the result (`out`), because Boreas functions only take `in` arguments.
  Its calls move in front of their statement: `r = 10 * f(k) + 1` becomes `call f(k, @f_value)` then
  `r = 10 * f_value + 1`, and a plain `r = f(k)` becomes `call f(k, @r)`. Calls that Fortran evaluates only
  conditionally (after `.and.`/`.or.`, in an `else if` or `do while` condition) are TODOs.
- `data a, b /1, 2/, c /3*0/` becomes initialisers on the declarations (arrays fill in column order, `n*value` repeats
  a value; a 2-D array becomes a matrix literal; `reshape` of plain numbers, with or without `order=`, does too). Elements, sections and implied-do loops in a `data` list are TODOs.
- Array constructors with implied-do loops become generators: `[x, (0.0, i=1, n)]` is `[x, (0.0 do i = 1, n)]`, and
  `[(i*i, i=1, 4)]` is `[i * i do i = 1, 4]` (steps, nested loops and several values per step work too).
- `character(len=n)` variables: assigning to one pads with blanks or cuts to `n` (`a = left(x, n)`; literals are
  padded in the converted source), comparisons and `select case` ignore trailing blanks (`trim(a) == "abc"`),
  and `len(a)` becomes `n`. Lengths that are not known numbers (`len=*`), record fields and function results
  keep Boreas's variable-length strings, with a note.
- Formats become string expressions. `write(*,'(A,I5,F8.3)') s, n, x` (or a labelled `format` statement,
  before or after the write) turns into `println(s + right(n, 5) + fixed(x, 3, 8))`. Translated: `A`, `Aw`,
  `Iw`, `Iw.m`, `Fw.d`, `nX`, `/`, literal text, repeat counts and groups, `advance='no'`, and implied-do
  lists with literal bounds (unrolled). More values than descriptors start the format again; a format
  `Ew.d` and `Gw.d` (as `scientific` / `general` with the `"fortran"` style) and `Lw` (as `right(tf(x), w)`), with groups that would have to repeat, and `ES`, `P`, an exponent width (`E12.4E3`), are TODOs. `Tn` pads with blanks up
  to column `n` (moving back over text already written is a TODO).
- `read(*,*) a, b` becomes `read_parts = fields(read_line() ?? "", ",")` and one `to_integer` / `to_real` /
  `to_logical` per value (text is taken as it is). The values must be on one line, separated by blanks or commas
  (Fortran also continues on the next line and accepts quoted text and repeat counts such as `3*0`), which
  the converter says in a note. With `iostat=ios` nothing is stored unless every value is good. When `ios` is only compared with -1, 0 or 1
  (the usual retry and end-of-input checks) the variable disappears and each comparison becomes a test of the
  values (`ios /= 0` becomes `not present(ios_integer)`, `ios < 0` becomes `not present(ios_line)`); otherwise it
  stays, with 0 for success, -1 at the end of input and 1 for a bad or missing value. Reads into arrays, sections and record fields are TODOs. `stop`,
  `stop 3` and `error stop` become `stop`, `stop 3` and `stop 1` (a final plain `stop` is dropped; a stop
  message goes to stderr in Fortran, so it is dropped with a note). `read(*,'(A)') text` reads a whole line into
  one character variable; other formatted reads are TODOs.
- Fixed-form source (`.f`, `.for`, `.f77`, `.ftn`) is read as such: labels in columns 1-5, continuation marks in
  column 6, statements in columns 7-72. Boreas has no `goto`, so the converter rewrites jumps: a test at the top
  with a jump back becomes `do while`, a jump back becomes a loop with `cycle`, a jump forward becomes a
  one-pass loop with `exit`, and jumps that cross each other become a small state machine (`select case
  fallthrough` in a loop). Computed `goto`, the arithmetic `if`, labelled `do` loops (a shared last statement
  too) and the `parameter` statement are translated; `common` and `block data` are TODOs.
- Callbacks: an interface block describes procedures, which Boreas finds by name, so the block is dropped, but a
  procedure it describes keeps its signature: `procedure(select_t) :: select` becomes the parameter
  `function(real :: wr, real :: wi) -> logical :: select_`. A signature whose intents the interface leaves out
  takes them from the procedures the program passes. An intrinsic passed as an argument (`call integrate(dsin, ...)`)
  goes through a small generated function (`sin_fn`). Procedure variables and pointers are TODOs. An `external` dummy argument (no interface) takes its signature from the procedures
  callers pass to it (`subroutine integrate(f, ...)` called with `square` gets `function(real :: x) -> real :: f`);
  it is a TODO when no call passes a known procedure, when callers disagree, or when the body uses it as a
  function but a subroutine is passed.
- Generic interfaces (`interface to_text` / `module procedure int_text, real_text`): Boreas has no overloading, so the
  converter drops the block and rewrites each call to the specific procedure, keeping the names the Fortran gave
  (`to_text(k)` becomes `int_text(k)`). A generic with one specific needs no checking; with several, it rules out each
  specific that a known argument contradicts in type, kind or rank (literals, declared variables and array
  elements, conversions, and arithmetic on those; `k * 2 + 1` is an integer, `k * 2.5` a real). If it cannot narrow
  the call to one, a TODO lists the candidates. A public generic makes its specifics public. Operator overloading
  (`interface operator(+)`) is a TODO.
- Submodules: Fortran splits a module into headers (the module) and bodies (its submodules) to save recompiling,
  which a program that runs from source does not need. Give the converter the parent and its submodules together
  (`boreas convert fftpack.f90 fftpack_fft.f90 ...`, in any order) and it moves each submodule's declarations and
  procedures into the parent module, one module in one file, with a `! from submodule NAME (of PARENT)` comment at
  each merged part. A submodule's own (non-`module`) procedures stay private; one whose name another part also uses
  is renamed with the submodule's name in front (`check` becomes `circ_check`). A `module procedure NAME ... end
  procedure` body gets its header and dummy declarations from the interface in the parent.
- A function result that is an array becomes `-> real[]` (`[,]` for two dimensions). An allocatable result is kept
  as an allocatable local; a fixed-size one (`real :: y(size(x))`, `integer, dimension(n) :: out`) becomes an
  allocatable local allocated to that size at the top of the function, with a note. A function that returns an
  array and also changes its arguments or module variables is a TODO, since it cannot become a subroutine.
- Preprocessor lines (`#if`, `#ifdef`, `#ifndef`, `#elif`, `#else`, `#define`, `#include`) in any file are evaluated
  as a preprocessing build would: the converter keeps the branch the conditions pick, given the macros you pass
  with `--define NAME` or `--define NAME=VALUE` and those the file `#define`s; a name not defined counts as
  undefined. `#include "file"` pulls in a file from the same directory. A condition it cannot evaluate keeps its
  first branch and is a TODO, and `--verify` builds the original with `-cpp` and the same `-D` flags.
- File input and output: `open(unit=9, file=name, status='replace')` becomes `unit_9 = open(name, "write")`,
  `write(9,*)` / `write(9,fmt)` become `writeln(unit_9, ...)`, `read(9,*)` takes its line from `read_line(unit_9)`,
  `read(9,'(A)')` reads a whole line, `close(9)` becomes `close unit_9`, and `inquire(file=f, exist=e)`
  becomes `e = exists(f)`. Each unit number gets a file variable in one generated module `file_units`, so
  every procedure sees the same open file. The mode comes from how the program uses the unit (only
  written: `"write"`, only read: `"read"`, `position='append'`: `"append"`); a unit used both ways, a
  unit number that is not a constant, `scratch` files, direct access, `rewind`, `backspace` and the
  other `inquire` forms are TODOs. `iostat=` on `open` uses `try_open`.
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
  they are defined). Module variables stay module variables; every write to one gets `@`,
  and a function that changes one becomes a subroutine with an `out` result (like a function that
  changes its arguments). A `use` of a module that isn't defined earlier in the same file, or an
  import that renames, is a TODO.
- `common /block/ a, b` becomes a module `common_block` of variables, and `block data` supplies their
  first values. The names come from the first unit that mentions the block; other units that call the
  variables something else are renamed to match (by position). A block seen with a different number of
  variables, or different types, is a TODO.
- `equivalence` between names of the same type becomes one name: `equivalence (a, b)` renames `b` to `a`,
  and `equivalence (v(3), x)` makes `x` the element `v(3)`. Arrays that overlap at an offset get their
  subscripts shifted. Overlaying different types (type punning), overlays that neither name covers, and
  whole-array uses of a shifted array are TODOs.
- Converted programs start with `use ieee`, so real arithmetic gives NaN and Infinity as Fortran does.
- Missing declarations are filled in using Fortran's implicit typing rules.
- Dummy arguments without `intent` get `in`, `out` or `inout` inferred from the
  body, with a note above the procedure.
- A function whose name is its result variable gets an explicit result variable.
- Calls to procedures that exist nowhere in the file are marked TODO, because they would fail. A program split
  over several files converts as one: `boreas convert main.f lib.f90 -o out.bor` (free and fixed form can be
  mixed; findings name the file and line).
- Names that are Boreas keywords (`in`, `out`, `string`, ...) are renamed.

### Checking a conversion

    boreas convert --verify [--timeout SECONDS] [--input FILE] FILE.f90 [FILE...] [-o OUT]

`--verify` converts the file as usual, then compiles the original with gfortran
(set `FC` to use another compiler), runs it and the converted program with empty
stdin (empty, or the contents of `--input FILE`) and a timeout (default 10 s), and compares what they print. Spacing and
commas are ignored, `T`/`F` equal `true`/`false`, and numbers match within 1e-9 (single-precision
output, which Fortran prints with nine digits, within half a single-precision step).

It prints "verified: ... (N values compared)" and exits 0 on a match. On a
mismatch it shows the first differing value from each program and exits 1.
Common causes are integer arithmetic that wraps around in Fortran but stops with an error in Boreas
(use `+%`/`*%` on `int32` values), single-precision library functions that differ in the last digit on another platform, an expression
mixing single and double precision, and constructs reported as TODO. Exit status 2
means it could not verify (gfortran missing, the original does not compile, or
it timed out).

The converted file is checked by the test suite against real gfortran output.
Try it on `examples/number_toys.f90`.

## License

MIT. See [LICENSE](LICENSE).
