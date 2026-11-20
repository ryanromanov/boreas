"""The compiler (boreas build): every program must print what the interpreter prints, and fail the way it fails
(the same message on stderr, the same exit code). Skipped when there is no C compiler."""
import glob, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.cgen import find_compiler

ROOT = os.path.join(os.path.dirname(__file__), "..")
HAVE_CC = find_compiler() is not None


def boreas(*args, **kw):
    return subprocess.run([sys.executable, "-m", "boreas", *args], cwd=ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=300, **kw)


def same_as_interpreter(test, source, release=False, name="p"):
    """Compile `source`, run both, and compare stdout, stderr and the exit code."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name + ".bor")
        exe = os.path.join(tmp, name)
        with open(path, "w") as f:
            f.write(source)
        built = boreas("build", path, "-o", exe, *(["--release"] if release else []))
        test.assertEqual(built.returncode, 0, built.stderr)
        compiled = subprocess.run([exe], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
        interpreted = boreas(path)
        test.assertEqual(compiled.stdout, interpreted.stdout, "stdout")
        test.assertEqual(compiled.stderr, interpreted.stderr, "stderr")
        test.assertEqual(compiled.returncode, interpreted.returncode, "exit code")
        return interpreted


def prog(body, defs=""):
    return defs + "program t\n" + body + "\nend program\n"


@unittest.skipUnless(HAVE_CC, "needs a C compiler")
class ProgramTests(unittest.TestCase):
    def test_the_examples_in_tests_compiled(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "tests", "compiled", "*.bor"))):
            with self.subTest(os.path.basename(path)):
                same_as_interpreter(self, open(path).read())

    def test_the_same_in_release_mode(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "tests", "compiled", "*.bor"))):
            with self.subTest(os.path.basename(path)):
                same_as_interpreter(self, open(path).read(), release=True)


# real32 mistakes: each one stops the program, and under `use ieee` gives the IEEE answer instead (both compared)
REAL32_CASES = {
    "overflow in +": "  real32 :: x = 3.0e38\n  println(x + x)",
    "overflow in *": "  real32 :: x = 3.0e38\n  println(x * 10)",
    "overflow in a whole power": "  real32 :: x = 1.0e20\n  println(x ** 2)",
    "overflow in a long whole power": "  real32 :: x = 100.0\n  println(x ** 40)",
    "overflow in a real power": "  real32 :: x = 100.0\n  real32 :: y = 30.0\n  println(x ** y)",
    "a negative base to a fractional power": "  real32 :: x = -8.0\n  real32 :: y = 0.5\n  println(x ** y)",
    "zero to a negative power": "  real32 :: x = 0.0\n  println(x ** -2)",
    "a real stored in a real32": "  real :: r = 1.0e39\n  real32 :: x = r\n  println(x)",
    "a real assigned to a real32": "  real :: r = 1.0e39\n  real32 :: x\n  x = r\n  println(x)",
    "a literal too large for real32": "  real32 :: x = 1.0\n  println(x + 1.0e39)",
    "division by zero": "  real32 :: x = 1.0\n  real32 :: z = 0.0\n  println(x / z)",
    "sqrt of a negative": "  real32 :: x = -4.0\n  println(sqrt(x))",
    "sqrt of a negative element": "  real32[3] :: x = [1.0, -4.0, 9.0]\n  println(sqrt(x))",
    "log of zero": "  real32 :: x = 0.0\n  println(log(x))",
    "log of a negative array": "  real32[2] :: x = [2.0, -1.0]\n  println(log10(x))",
    "exp overflow": "  real32 :: x = 100.0\n  println(exp(x))",
    "real32 of a large real": "  real :: r = 1.0e39\n  println(real32(r))",
    "a sum too large": "  real32[2] :: x = 3.0e38\n  println(sum(x))",
    "a dot_product too large": "  real32[2] :: x = 1.0e20\n  println(dot_product(x, x))",
    "mod by zero": "  real32 :: x = 5.0\n  real32 :: z = 0.0\n  println(mod(x, z))",
}

OUT_DEFS = {
    "an out parameter read before it is assigned": "subroutine s(out int :: k)\n  println(k)\n  @k = 5\nend subroutine\n",
    "an out parameter never assigned": "subroutine s(out int :: k, int :: c)\n  if c > 0 then\n    @k = 5\n  end if\nend subroutine\n",
    "an out parameter used to start a value": "subroutine s(out int :: k)\n  int :: a = k\n  @k = 5\nend subroutine\n",
    "an unassigned out parameter passed as inout": "subroutine s(out int :: k)\n  call u(@k)\n  @k = 5\nend subroutine\nsubroutine u(inout int :: j)\n  @j = j + 1\nend subroutine\n",
    "an out parameter handed on to a callee that does not assign it": "subroutine s(out int :: k)\n  @k = 1\n  call u(@k)\nend subroutine\nsubroutine u(out int :: j)\n  println(1)\nend subroutine\n",
    "an out array given the wrong shape": "subroutine s(out int[] :: v)\n  @v = [7, 8, 9]\nend subroutine\n",
}

MATH_CASES = {
    'asin2': '  real :: x = 2.0\n  println(asin(x))',
    'acos2': '  int :: x = 2\n  println(acos(x))',
    'acosh0': '  real :: x = 0.5\n  println(acosh(x))',
    'atanh1': '  real :: x = 1.0\n  println(atanh(x))',
    'atanh2': '  real32 :: x = 2.0\n  println(atanh(x))',
    'sinhbig': '  real :: x = 1000.0\n  println(sinh(x))',
    'coshbig32': '  real32 :: x = 100.0\n  println(cosh(x))',
    'asinarr': '  real[3] :: v = [0.1, 5.0, 0.9]\n  println(asin(v))',
    'floorbig': '  real :: x = 1.0e30\n  println(floor(x))',
    'floorbig32': '  real32 :: x = 3.0e9\n  println(nint(x))',
    'aintinf': '  real :: x = 0.0\n  println(aint(1.0 / x))',
    'conv_big': '  real :: x = 1.0e10\n  println(int32(x))',
    'conv_big2': '  int :: n = 5000000000\n  println(int32(n))',
    'conv_big3': '  real :: x = 1.0e20\n  println(int64(x))',
    'signover': '  int :: a = -9223372036854775807 - 1\n  println(sign(a, 1))',
    'signover32': '  int32 :: a = -2147483647 - 1\n  println(sign(a, 1))',
    'modzero': '  int :: z = 0\n  println(modulo(5, z))',
    'modzero2': '  real :: z = 0.0\n  println(modulo(5.0, z))',
    'modzero3': '  real :: z = 0.0\n  println(modulo(5, z))',
    'dimover': '  int :: a = 9223372036854775807\n  int :: b = -5\n  println(dim(a, b))',
    'dimover32': '  int32 :: a = 2147483647\n  int32 :: b = -5\n  println(dim(a, b))',
    'conv_inf': '  real :: x = 0.0\n  println(int64(1.0 / x))',
    'conv_inf2': '  real :: x = 0.0\n  println(int32(-1.0 / x))',
}

RECORD_DEFS = 'record point\n  real :: x\n  real :: y = 2\nend record\nrecord small\n  int32 :: a\n  real32 :: r = 1\n  logical :: f\n  int :: n = -5\nend record\nrecord box\n  point :: lo\n  point :: hi\n  small :: s\nend record\nfunction mk(real :: a) -> point\n  return point(a, a * 2.0)\nend function\nfunction bump(point :: p) -> point\n  return point(p.x + 1.0, p.y)\nend function\nsubroutine grow(inout point :: p)\n  @p.x = p.x * 2.0\n  @p.y = p.y + 1\nend subroutine\nsubroutine set_small(inout small :: s, int :: v)\n  @s.a = v\nend subroutine\nfunction area(box :: b) -> real\n  return (b.hi.x - b.lo.x) * (b.hi.y - b.lo.y)\nend function\nfunction nothing(real :: a) -> point\n  if a > 0.0 then\n    return point(a, a)\n  end if\nend function\n'
RECORD_CASES = {'defaults': '  point :: p\n  small :: s\n  box :: b\n  println(p)\n  println(s)\n  println(b)', 'construct': '  point :: p = point(1, 2)\n  small :: s = small(7, 2, true)\n  println(p, s)\n  println(point(x = 3.0), small(f = true, a = 9))', 'int_into_real': '  point :: p\n  p.x = 3\n  p.y = p.x + 1\n  println(p)', 'real32_field': '  small :: s\n  s.r = 16777217\n  println(s.r)\n  s.r = 0.1\n  println(s.r, s.r * 2.0)', 'real32_over': '  small :: s\n  s.r = 1.0e39\n  println(s.r)', 'ctor_over': '  small :: s = small(5000000000, 1, true)\n  println(s)', 'ctor_over_named': '  small :: s = small(f = false, a = 5000000000)\n  println(s)', 'assign_over': '  small :: s\n  s.a = 2147483647\n  println(s.a)\n  s.a = 2147483648\n  println(s.a)', 'copy': '  point :: a = point(1.0, 2.0)\n  point :: b\n  b = a\n  a.x = 9.0\n  println(a, b)', 'nested': '  box :: b\n  b.lo.x = 1.0\n  b.hi = point(4.0, 6.0)\n  b.s.a = 3\n  println(b)\n  println(area(b))\n  b.lo = b.hi\n  b.hi.x = 0.0\n  println(b.lo, b.hi)', 'funcs': '  point :: p = mk(2.5)\n  println(p, bump(p), bump(bump(p)))', 'inout': '  point :: p = mk(1.0)\n  call grow(@p)\n  call grow(@p)\n  println(p)\n  box :: b\n  call grow(@b.lo)\n  println(b)', 'inout_small': '  small :: s\n  call set_small(@s, 5000000000)\n  println(s)', 'inout_small_ok': '  small :: s\n  call set_small(@s, 12)\n  println(s)', 'loop': '  point :: p\n  int :: i\n  do i = 1, 5\n    call grow(@p)\n  end do\n  println(p.x, p.y)', 'decl_in_loop': '  int :: i\n  do i = 1, 2\n    point :: p\n    println(p)\n  end do', 'no_return': '  println(nothing(1.0))\n  println(nothing(-1.0))', 'cond': '  point :: p = mk(3.0)\n  if p.x > 2.0 and p.y < 10.0 then\n    println("yes", p.x)\n  end if', 'neg_default': '  small :: s\n  println(s.n, -s.n)', 'logical': '  small :: s\n  println(s.f)\n  s.f = not s.f\n  println(s.f)', 'print_mix': '  point :: p\n  println(1, p, 2.5, p.x)', 'ieee_inf': '  point :: p\n  p.x = 1.0e308\n  p.x = p.x * 10.0\n  println(p)', 'div_zero': '  point :: p\n  println(p.y / p.x)'}
RECORD_DEFS_2 = 'record point\n  real :: x\n  real :: y = 2\nend record\nrecord other\n  int :: n\nend record\nsubroutine two(inout point :: p, inout real :: v)\n  @p.x = v\n  @v = 1.0\nend subroutine\nfunction fact(int :: n) -> int\n  if n <= 1 then\n    return 1\n  end if\n  return n * fact(n - 1)\nend function\nfunction flip(point :: p) -> point\n  return point(p.y, p.x)\nend function\n'
RECORD_CASES_2 = {'rec_to_real': '  point :: p\n  real :: x\n  x = p\n  println(x)', 'real_to_rec': '  point :: p\n  p = 3.0\n  println(p)', 'rec_to_other': '  point :: p\n  other :: o\n  o = p\n  println(o)', 'int_into_logical_field': '  point :: p\n  p.x = true\n  println(p)', 'real_into_int_field': '  other :: o\n  o.n = 2.5\n  println(o)', 'ctor_real_into_int': '  other :: o = other(2.5)\n  println(o)', 'ctor_too_many': '  point :: p = point(1.0, 2.0, 3.0)\n  println(p)', 'ctor_missing': '  other :: o = other()\n  println(o)', 'ctor_unknown_field': '  point :: p = point(z = 1.0)\n  println(p)', 'ctor_twice': '  point :: p = point(1.0, x = 2.0)\n  println(p)', 'no_such_field': '  point :: p\n  println(p.z)', 'alias': '  point :: p\n  call two(@p, @p.x)\n  println(p)', 'alias2': '  point :: p\n  call two(@p, p.x)\n  println(p)', 'recursion': '  println(fact(10), fact(20), fact(21))', 'flip': '  println(flip(flip(point(1.0, 5.0))))', 'op': '  point :: p\n  point :: q\n  println(p + q)', 'cmp': '  point :: p\n  point :: q\n  println(p == q)', 'neg': '  point :: p\n  println(-p)', 'abs_rec': '  point :: p\n  println(abs(p))', 'size_rec': '  point :: p\n  println(size(p))', 'field_of_call': '  println(flip(point(1.0, 2.0)).x)', 'index_rec': '  point :: p\n  println(p[1])', 'field_index': '  point :: p\n  println(p.x[1])', 'if_rec': '  point :: p\n  if p then\n    println(1)\n  end if', 'do_rec': '  point :: p\n  do p = 1, 3\n    println(1)\n  end do', 'redecl': '  point :: p\n  point :: p\n  println(p)', 'decl_diff_scope': '  point :: p = point(1.0, 2.0)\n  if p.x > 0.0 then\n    point :: q = flip(p)\n    println(q)\n  end if', 'assign_real32': '  point :: p\n  real32 :: r = 1.5\n  p.x = r\n  println(p)', 'int32_field_arith': '  point :: p\n  int32 :: k = 5\n  p.y = k\n  println(p)'}


RECORD_DEFS_3 = 'record acc\n  int32 :: n = 0\n  real32 :: r = 0.5\n\n  subroutine add(inout self, int :: by)\n    @self.n = self.n + by\n  end subroutine\n\n  function plus(self, int32 :: k) -> int32\n    return self.n + k\n  end function\n\n  function half(self) -> real32\n    return self.r / 2.0\n  end function\n\n  function copy_with(self, int :: m) -> acc\n    return acc(m, self.r)\n  end function\n\n  function down(self, int :: k) -> int\n    if k <= 0 then\n      return 0\n    end if\n    return k + self.down(k - 1)\n  end function\n\n  function never(self) -> int\n    if self.n > 100 then\n      return 1\n    end if\n  end function\nend record\n\nfunction add(int :: a, int :: b) -> int\n  return a + b\nend function\n\nfunction total(acc :: a) -> int\n  return add(a.n, 1) + a.plus(2)\nend function\n'
RECORD_CASES_3 = {'basic': '  acc :: a\n  call @a.add(5)\n  println(a.n, a.plus(3), a.half(), total(a))', 'overflow_method': '  acc :: a\n  call @a.add(2147483647)\n  call @a.add(1)\n  println(a)', 'plus_overflow': '  acc :: a\n  call @a.add(2147483647)\n  println(a.plus(1))', 'copy_with': '  acc :: a\n  acc :: b\n  b = a.copy_with(7)\n  println(a, b, b.copy_with(8).n)', 'chain': '  acc :: a\n  println(a.copy_with(3).copy_with(4).plus(1))', 'recursion': '  acc :: a\n  println(a.down(100))', 'deep': '  acc :: a\n  println(a.down(20000))', 'never': '  acc :: a\n  println(a.never())', 'never2': '  acc :: a\n  println(1)\n  println(a.never() + 1)', 'ieee_half': '  acc :: a\n  a.r = 3.0e38\n  println(a.half() * 4.0)', 'in_loop': '  acc :: a\n  int :: i\n  do i = 1, 10\n    call @a.add(i)\n  end do\n  println(a.n)', 'two_receivers': '  acc :: a\n  acc :: b\n  call @a.add(1)\n  call @b.add(a.plus(1))\n  println(a, b)', 'wrong_args': '  acc :: a\n  println(a.plus())', 'too_many': '  acc :: a\n  println(a.plus(1, 2))', 'no_method': '  acc :: a\n  println(a.nothing())', 'call_func': '  acc :: a\n  call a.plus(1)', 'unmarked': '  acc :: a\n  call a.add(1)', 'field_then_method': '  acc :: a\n  a.n = 5\n  println(a.half(), a.plus(a.n))'}


FAILING = {
    "too few subscripts": "  int[2, 3] :: m\n  println(m[1])",
    "too many subscripts": "  int[3] :: m\n  m[1, 2] = 0",
    "too few subscripts, array not allocated": "  allocatable int[,] :: m\n  println(m[1])",
    'array function: reshape_bad': '  int[6] :: v = [1, 2, 3, 4, 5, 6]\n  println(reshape(v, [4, 2]))',
    'array function: reshape_neg': '  int[6] :: v = [1, 2, 3, 4, 5, 6]\n  int :: n = -2\n  println(reshape(v, [n, 3]))',
    'array function: merge_shape': '  int[3] :: a = 1\n  int[2] :: b = 2\n  logical :: m = true\n  println(merge(a, b, m))',
    'array function: merge_shape2': '  int[3] :: a = 1\n  logical[2] :: m\n  println(merge(a, 0, m))',
    'array function: maxloc_empty': '  int[0] :: a\n  println(maxloc(a))',
    'array function: cshift_dim': '  int[2, 3] :: m = [1, 2, 3; 4, 5, 6]\n  println(cshift(m, 1, 3))',
    'array function: merge of three shapes': '  int[5] :: a = 1\n  int[3] :: b = 2\n  logical[2] :: m\n  println(merge(a, b, m))',

    "section: index below the range": "  int[6] :: a\n  println(a[0:3])",
    "section: end beyond the range": "  int[6] :: a\n  println(a[3:7])",
    "section: with a step, out of range": "  int[6] :: a\n  println(a[1:9:2])",
    "section: zero step": "  int[6] :: a\n  println(a[1:4:0])",
    "section: negative step without both ends": "  int[6] :: a\n  println(a[3::-1])",
    "section: out of range in the second dimension": "  int[2, 3] :: m\n  println(m[1, 2:5:2])",
    "section: out of range in the first dimension": "  int[2, 3] :: m\n  println(m[1:3, 1])",
    "section: whole number subscript out of range": "  int[2, 3] :: m\n  println(m[3, 1:2])",
    "section: evaluated left to right": "  int[4] :: a\n  int :: k = 0\n  println(a[1 / k:2])",
    "section: end evaluated after the start": "  int[4] :: a\n  println(a[7:1 / 0])",
    "section: of an unallocated array": "  allocatable int[] :: a\n  println(a[1:2])",
    "section: assigned to an unallocated array": "  allocatable int[] :: a\n  a[1:2] = 1",
    "section assigned a value of the wrong length": "  int[6] :: a\n  a[2:4] = [1, 2]",
    "section assigned a value of the wrong rank": "  int[2, 2] :: m\n  m[:, 1] = [1, 2, 3]",
    "section target checked before the value": "  int[6] :: a\n  a[7:8] = 1 / 0",
    "section value evaluated before the shapes are compared": "  int[6] :: a\n  a[2:4] = [1 / 0]",
    "section assigned into int32 too large": "  int32[3] :: a\n  int :: big = 5000000000\n  a[1:2] = big",
    "section of an array of real32 too large": "  real[2] :: big = 1.0e39\n  real32[4] :: a\n  a[1:2] = big",
    "unallocated: print": "  allocatable int[] :: a\n  println(a)",
    "unallocated: size": "  allocatable int[] :: a\n  println(size(a))",
    "unallocated: element read": "  allocatable int[] :: a\n  println(a[1 / 0])",
    "unallocated: element write checked before the value": "  allocatable int[] :: a\n  a[1] = 1 / 0",
    "unallocated: scalar assigned": "  allocatable int[] :: a\n  a = 5",
    "unallocated: in an operation": "  allocatable int[] :: a\n  int[2] :: f = 1\n  println(f + a)",
    "unallocated: copied": "  allocatable int[] :: a\n  int[3] :: f\n  f = a",
    "the value is evaluated before the shape is replaced": "  allocatable int[] :: a\n  a = [1 / 0]",
    "allocated twice": "  allocatable int[] :: a\n  allocate a[2]\n  allocate a[3]",
    "allocated twice, extent not evaluated": "  allocatable int[] :: a\n  allocate a[2]\n  allocate a[1 / 0]",
    "deallocated when not allocated": "  allocatable int[] :: a\n  deallocate a",
    "negative extent": "  allocatable int[] :: a\n  int :: n = -1\n  allocate a[n]",
    "extent that is not a whole number": "  allocatable int[] :: a\n  allocate a[2.5]",
    "index beyond the allocated size": "  allocatable int[] :: a\n  allocate a[3]\n  a[4] = 1",
    "int32 narrowing into an allocatable": "  allocatable int32[] :: a\n  int :: big = 5000000000\n  a = [big]",
    "declared twice (a loop)": "  int :: i\n  do i = 1, 2\n    int :: x = 1\n  end do",
    "dot_product shapes": "  real[3] :: a = 1.0\n  real[2] :: b = 1.0\n  println(dot_product(a, b))",
    "matmul inner dimensions": "  real[2, 3] :: a = 1.0\n  real[2, 3] :: b = 1.0\n  println(matmul(a, b))",
    "int matmul overflow": "  int[1, 1] :: a = 9223372036854775807\n  int[1, 1] :: b = 2\n  println(matmul(a, b))",
    "int dot_product overflow": "  int[2] :: a = 9223372036854775807\n  println(dot_product(a, a))",
    "index too large": "  int[3] :: a\n  println(a[4])",
    "index too small": "  int[3] :: a\n  int :: i = 0\n  println(a[i])",
    "matrix row index": "  real[2, 3] :: m\n  println(m[3, 1])",
    "matrix column index": "  real[2, 3] :: m\n  println(m[1, 4])",
    "an element written out of range": "  int[3] :: a\n  a[0] = 1",
    "the target is checked before the value": "  int[3] :: a\n  a[9] = 1 / 0",
    "shape mismatch in an operation": "  int[3] :: a = 1\n  int[2] :: b = 1\n  println(a + b)",
    "shape mismatch the other way": "  int[2] :: a = 1\n  int[3] :: b = 1\n  println(a + b)",
    "shape mismatch in an assignment": "  int[3] :: a\n  int[2] :: b\n  a = b",
    "initializer shape": "  int[3] :: a = [1, 2]",
    "negative extent": "  int :: n = -2\n  int[n] :: a",
    "int32 overflow": "  int32 :: n = 2147483647\n  n = n + 1",
    "int32 narrowing": "  int :: big = 5000000000\n  int32 :: n = big",
    "int32 array narrowing": "  int :: big = 5000000000\n  int32[2] :: a = big",
    "sum overflow": "  int[2] :: a = 9223372036854775807\n  println(sum(a))",
    "maxval of nothing": "  int[0] :: a\n  println(maxval(a))",
    "an error inside an array operation": "  int[3] :: a = 9223372036854775807\n  println(a + 1)",
    "sqrt of a negative element": "  real[3] :: a = [1.0, -4.0, 9.0]\n  println(sqrt(a))",
    "power too large": "  println(10.0 ** 400)",
    "negative int to a fractional power": "  println((-8) ** 0.5)",
    "int times a huge real": "  real :: x = 1.0e308\n  println(10 * x)",
    "add overflow": "  int :: n = 9223372036854775807\n  n = n + 1",
    "sub overflow": "  int :: n = -9223372036854775807\n  n = n - 2",
    "mul overflow": "  int :: n = 4294967296\n  println(n * n)",
    "neg of the smallest": "  int :: n = -9223372036854775807 - 1\n  println(-n)",
    "division by zero": "  int :: a = 5\n  int :: b = 0\n  println(a / b)",
    "the smallest over minus one": "  int :: n = -9223372036854775807 - 1\n  int :: m = -1\n  println(n / m)",
    "mod by zero": "  int :: a = 5\n  int :: b = 0\n  println(mod(a, b))",
    "real mod by zero": "  real :: a = 5.0\n  real :: b = 0.0\n  println(mod(a, b))",
    "power overflow": "  println(2 ** 63)",
    "power of zero": "  int :: z = 0\n  println(z ** -1)",
    "real overflow": "  real :: x = 1.0e308\n  println(x * 10.0)",
    "real division by zero": "  real :: x = 0.0\n  println(1.0 / x)",
    "sqrt of a negative": "  real :: x = -1.0\n  println(sqrt(x))",
    "sqrt of a negative int": "  int :: x = -4\n  println(sqrt(x))",
    "log of zero": "  real :: x = 0.0\n  println(log(x))",
    "exp too large": "  real :: x = 710.0\n  println(exp(x))",
    "negative fractional power": "  real :: x = -8.0\n  println(x ** (1.0 / 3.0))",
    "zero step": "  int :: i\n  int :: k = 0\n  do i = 1, 3, k\n  end do",
    "abs of the smallest": "  int :: n = -9223372036854775807 - 1\n  println(abs(n))",
    "int of a big real": "  real :: x = 1.0e30\n  println(int(x))",
    "an error after output": "  println(\"before\")\n  int :: z = 0\n  println(1 / z)",
}


@unittest.skipUnless(HAVE_CC, "needs a C compiler")
class ErrorTests(unittest.TestCase):
    def test_real32_errors_and_ieee_answers_are_the_interpreters(self):
        for name, body in REAL32_CASES.items():
            with self.subTest(name):
                done = same_as_interpreter(self, prog(body))
                self.assertEqual(done.returncode, 1, name)
                self.assertIn("error: line", done.stderr)
            with self.subTest(name + " (use ieee)"):
                same_as_interpreter(self, "use ieee\n" + prog(body))

    def test_out_parameter_mistakes_are_the_interpreters(self):
        for name, defs in OUT_DEFS.items():
            with self.subTest(name):
                arg, decl = ("a", "  int[2] :: a = [1, 2]") if "array" in name else ("n", "  int :: n = 3")
                done = same_as_interpreter(self, prog(f"{decl}\n  call s(@{arg}{', 0' if 'never' in name else ''})", defs))
                self.assertEqual(done.returncode, 1, name)
                self.assertIn("error: line", done.stderr)

    def test_math_function_errors_and_ieee_answers_are_the_interpreters(self):
        for name, body in MATH_CASES.items():
            with self.subTest(name):
                same_as_interpreter(self, prog(body))
            if not name.startswith("mod"):                       # (modulo is not compiled in a file that says `use ieee`)
                with self.subTest(name + " (use ieee)"):
                    same_as_interpreter(self, "use ieee\n" + prog(body))

    def test_records_behave_as_in_the_interpreter(self):
        for defs, cases in ((RECORD_DEFS, RECORD_CASES), (RECORD_DEFS_2, RECORD_CASES_2), (RECORD_DEFS_3, RECORD_CASES_3)):
            for name, body in cases.items():
                with self.subTest(name):
                    built = boreas("emit-c", self.write(prog(body, defs)))
                    if built.returncode != 0:                       # not compiled: refused as a whole, never partial
                        self.assertIn("not supported by the compiler yet", built.stderr)
                        continue
                    same_as_interpreter(self, prog(body, defs))
                    same_as_interpreter(self, "use ieee\n" + prog(body, defs))

    def write(self, source):
        handle, path = tempfile.mkstemp(suffix=".bor")
        self.addCleanup(os.remove, path)
        with os.fdopen(handle, "w") as f:
            f.write(source)
        return path

    def test_a_function_that_ends_without_returning_names_the_line_of_the_call(self):
        defs = "function f(real :: a) -> real\n  if a > 0.0 then\n    return a\n  end if\nend function\n"
        done = same_as_interpreter(self, prog("  println(f(1.0))\n  println(f(-1.0))", defs))
        self.assertIn("line 8:", done.stderr)

    def test_every_error_is_the_interpreters(self):
        for name, body in FAILING.items():
            with self.subTest(name):
                done = same_as_interpreter(self, prog(body))
                self.assertEqual(done.returncode, 1, name)
                self.assertIn("error: line", done.stderr)

    def test_an_error_inside_a_function_names_the_line_there(self):
        defs = "function inv(int :: n) -> int\n  return 100 / n\nend function\n"
        done = same_as_interpreter(self, prog("  println(inv(0))", defs))
        self.assertIn("line 2:", done.stderr)


@unittest.skipUnless(HAVE_CC, "needs a C compiler")
class OrderTests(unittest.TestCase):
    DEFS = "function f(int :: x) -> int\n  println(\"f\", x)\n  return x\nend function\n"

    def test_operands_and_arguments_go_left_to_right(self):
        same_as_interpreter(self, prog("  println(f(1) + f(2) * f(3), f(4) - f(5))", self.DEFS))
        same_as_interpreter(self, prog("  int :: z = 0\n  println(f(1), f(2) / z, f(3))", self.DEFS))

    def test_and_and_or_do_not_short_circuit(self):
        same_as_interpreter(self, prog("  if f(1) > 5 and f(2) > 0 then\n    println(\"yes\")\n  end if\n"
                                       "  if f(3) > 0 or f(4) > 0 then\n    println(\"no\")\n  end if", self.DEFS))

    def test_elif_conditions_are_only_evaluated_when_reached(self):
        same_as_interpreter(self, prog("  if f(1) > 5 then\n    println(\"a\")\n  else if f(2) > 5 then\n"
                                       "    println(\"b\")\n  else\n    println(\"c\")\n  end if", self.DEFS))


@unittest.skipUnless(HAVE_CC, "needs a C compiler")
class FeatureTests(unittest.TestCase):
    def test_recursion(self):
        defs = "function fib(int :: n) -> int\n  if n < 2 then\n    return n\n  end if\n  return fib(n - 1) + fib(n - 2)\nend function\n"
        same_as_interpreter(self, prog("  println(fib(20))", defs))

    def test_labeled_loops(self):
        same_as_interpreter(self, prog("  int :: i\n  int :: j\n  outer: do i = 1, 4\n    do j = 1, 4\n"
                                       "      if j == 3 then\n        cycle outer\n      end if\n      if i == 3 then\n"
                                       "        exit outer\n      end if\n      println(i, j)\n    end do\n  end do outer\n  println(i, j)"))

    def test_loop_variables_end_one_step_past(self):
        same_as_interpreter(self, prog("  int :: i\n  do i = 1, 3\n  end do\n  println(i)\n  do i = 5, 1, -2\n  end do\n"
                                       "  println(i)\n  do i = 3, 1\n  end do\n  println(i)\n  do i = 1, 9\n    if i == 4 then\n      exit\n    end if\n  end do\n  println(i)"))

    def test_use_ieee(self):
        same_as_interpreter(self, "use ieee\n" + prog("  real :: z = 0.0\n  println(1.0 / z, -1.0 / z, z / z, sqrt(-1.0), log(z), exp(1000.0))\n"
                                                       "  println(1.0e308 * 10.0, (-8.0) ** 0.5)"))

    def test_stop_codes(self):
        done = same_as_interpreter(self, prog("  println(\"a\")\n  stop 3\n  println(\"b\")"))
        self.assertEqual(done.returncode, 3)

    def test_numbers_print_like_python(self):
        same_as_interpreter(self, prog("  println(0.1 + 0.2, 1.0e22, 1.0e-7, 123456789012345.6, 5.0e-324, 1.7976931348623157e308, -0.0)"))

    def test_endless_recursion_stops_with_the_interpreters_message(self):
        done = same_as_interpreter(self, "subroutine down(int :: n)\n  call down(n + 1)\nend subroutine\nprogram t\n  call down(1)\nend program\n")
        self.assertIn("nested more than 10000 calls deep", done.stderr)
        defs = "function deep(int :: n) -> int\n  if n == 0 then\n    return 0\n  end if\n  return 1 + deep(n - 1)\nend function\n"
        same_as_interpreter(self, prog("  println(deep(9000))", defs))

    def test_a_program_that_declares_its_own_pi(self):
        same_as_interpreter(self, prog("  real :: pi = 3.5\n  pi = 4.0\n  println(pi)"))
        same_as_interpreter(self, prog("  const real :: pi = 3.0\n  println(pi * 2)"))
        same_as_interpreter(self, prog("  println(pi * 2)"))

    def test_error_messages_show_whole_numbers_as_whole_numbers(self):
        for body in ("  real :: x = 1.0e308\n  println(10 * x)", "  int :: n = 10\n  println(10.0 ** 400, n)",
                     "  println((-8) ** 0.5)", "  real :: z = 0.0\n  println(5 / z)"):
            same_as_interpreter(self, prog(body))

    def test_a_long_loop(self):
        same_as_interpreter(self, prog("  int :: i\n  int :: s = 0\n  do i = 1, 200000\n    s = s + mod(i, 7)\n  end do\n  println(s)"))

    def test_mixed_int_and_real(self):
        same_as_interpreter(self, prog("  int :: n = 3\n  real :: x = 2.5\n  println(n * x, n + x, n / x, x ** n, n ** 2, max(n, x), min(n, x), n < x)"))


@unittest.skipUnless(HAVE_CC, "needs a C compiler")
class RefusalTests(unittest.TestCase):
    def refused(self, source, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "p.bor")
            open(path, "w").write(source)
            done = boreas("emit-c", path)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("not supported by the compiler yet", done.stderr)
            self.assertIn(text, done.stderr)
            self.assertEqual(done.stdout, "")                  # never partial code

    def test_what_is_not_handled_is_refused_as_a_whole(self):
        self.refused(prog("  allocatable string[] :: a\n  println(1)"), "the declaration of 'a'")
        self.refused(prog("  int[2, 2, 2, 2] :: a\n  println(1)"), "the declaration of 'a'")
        self.refused(prog("  int[3] :: a = [1, 2, 3]\n  call s(@a[1:2])", "subroutine s(inout int[] :: v)\n  @v[1] = 0\nend subroutine\n"),
                     "a section of 'a' passed as inout")
        self.refused(prog("  real[3] :: a = [1.0, 2.0, 3.0]\n  int[3] :: b = [1, 2, 3]\n  println(matmul(a, b))"), "matmul()")
        self.refused(prog("  real :: a = 1.0\n  println(hypot(a, a))"), "the function 'hypot'")
        self.refused(prog("  int[2, 3] :: m\n  println(m[1:2])"), "wrong number of subscripts")
        self.refused("use ieee\n" + prog("  println(modulo(5, 3))"), "modulo() in a file that says `use ieee`")
        self.refused(prog("  real32 :: x = 1.0\n  int :: n = 2\n  println(n ** x)"), "a real32 power whose base is not a real32")
        self.refused(prog("  string :: s = \"a\"\n  println(s)"), "the declaration of 's' (string)")
        self.refused("record p\n  string :: s\nend record\nprogram t\nend program\n", "field 's' of record 'p'")
        self.refused("record p\n  real[2] :: x\nend record\nprogram t\nend program\n", "field 'x' of record 'p'")
        self.refused("module m\n  public int :: k = 1\nend module\nprogram t\nend program\n", "modules")
        self.refused(prog("  int :: n = 1\n  println(hypot(2.5, 1.0), n)"), "the function 'hypot'")
        self.refused(prog("  const int :: i = 1\n  do i = 1, 2\n  end do"), "constant or parameter")
        self.refused("function f(int :: n) -> int\n  n = 2\n  return n\nend function\nprogram t\nend program\n", "constant or parameter")
        self.refused(prog("  int :: n = 1\n  call s(@n, @n)", "subroutine s(out int :: k, out int :: j)\n  @k = 1\n  @j = 2\nend subroutine\n"),
                     "passed as out and as another argument")

    def test_calls_that_the_interpreter_rejects_are_refused(self):
        defs = "subroutine s(inout real[] :: x, real[] :: y)\n  @x[1] = 9.0\nend subroutine\n"
        self.refused(prog("  real[2] :: a = 1.0\n  call s(@a, a)", defs), "passed as inout and as another argument")
        self.refused(prog("  int :: n = 1\n  call s(n)", "subroutine s(inout int :: k)\n  @k = 2\nend subroutine\n"), "the interpreter rejects")
        self.refused(prog("  const int :: n = 1\n  call t(@n)", "subroutine t(inout int :: k)\n  @k = 2\nend subroutine\n"),
                     "constant or parameter")
        self.refused("function g(int[] :: a) -> int[]\n  return a\nend function\nprogram t\nend program\n", "returns")
        self.refused(prog("  int[3] :: a = [1, 2, 3]\n  int :: t = 0\n  println(mod(a, 2), t)"), "mod() of an array")

    def test_a_program_with_a_type_error_is_refused(self):
        self.refused(prog("  int :: n = 2.5"), "type error")

    def test_the_line_is_named(self):
        self.refused(prog("  int :: n = 1\n  println(hypot(2.5, 1.0), n)"), "(line 3)")


if __name__ == "__main__":
    unittest.main()
