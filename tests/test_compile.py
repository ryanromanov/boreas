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


RECORD_DEFS_4 = 'record person\n  string :: name = "anon"\n  int :: age = 3\n  string :: tag\n\n  function greet(self) -> string\n    return "hi " + self.name + "/" + self.tag\n  end function\n\n  function older(self, int :: by) -> person\n    return person(self.name + "+", self.age + by, upper(self.tag))\n  end function\n\n  subroutine rename(inout self, string :: s)\n    @self.name = s + self.name\n  end subroutine\nend record\n\nrecord team\n  person :: lead\n  person :: other\n  string :: title = "team"\n  int32 :: size = 2\nend record\n\nfunction make(string :: n) -> person\n  person :: p\n  p.name = n\n  p.tag = n + n\n  return p\nend function\n\nfunction swap_names(team :: t) -> team\n  team :: r = t\n  r.lead.name = t.other.name\n  r.other.name = t.lead.name\n  return r\nend function\n\nsubroutine grow(inout team :: t)\n  @t.title = t.title + "!"\n  @t.lead.name = t.lead.name + "?"\nend subroutine\n\nsubroutine grow2(inout string :: s)\n  @s = s + "<"\nend subroutine\n\nfunction lastname(person :: p) -> string\n  return p.name\nend function\n'
RECORD_CASES_4 = {'defaults': '  person :: p\n  println(p)\n  println(p.name, len(p.name), len(p.tag), p.tag == "")', 'construct': '  person :: p = person("bob", 41, "t")\n  println(p)\n  println(person(tag = "z"))\n  println(person(age = 9, name = "q"))', 'copy_semantics': '  person :: p = person("bob", 41, "t")\n  person :: q = p\n  q.name = "changed"\n  println(p, q)\n  p = q\n  p.age = 1\n  println(p, q)', 'self_assign': '  person :: p = person("bob", 41, "t")\n  p = p\n  println(p)\n  p.name = p.name + p.name\n  println(p)', 'method': '  person :: p = person("bob", 41, "t")\n  println(p.greet())\n  println(p.older(2))\n  println(p.older(2).older(3).greet())\n  call @p.rename("mr ")\n  println(p)', 'fn_return': '  person :: p = make("ann")\n  println(p, make("x").name, lastname(make("yy")), len(make("zzz").tag))\n  p = make(p.name + "2")\n  println(p)', 'nested': '  team :: t = team(make("a"), make("b"))\n  println(t)\n  team :: u = swap_names(t)\n  println(u, t)\n  call grow(@t)\n  println(t)\n  u = t\n  u.lead.tag = "new"\n  println(t.lead.tag, u.lead.tag)', 'loop': '  person :: p = person("", 0, "")\n  int :: i\n  do i = 1, 200\n    p = person(text(i) + "-" + p.name, i, text(i))\n    if len(p.name) > 20 then\n      p = person("", 0, "")\n    end if\n  end do\n  println(p)', 'loop_temps': '  int :: i\n  int :: s = 0\n  do i = 1, 300\n    s = s + len(make(text(i)).tag) + len(make("a").older(i).name)\n  end do\n  println(s)', 'cond_temps': '  person :: p = make("abc")\n  if make("x").name == "y" then\n    println(1)\n  else if p.older(1).name == "abc+" then\n    println(2)\n  end if\n  int :: i = 0\n  do while make(text(i)).age < 3 and i < 4\n    i = i + 1\n  end do\n  println(i)', 'field_text_ops': '  person :: p = make("hello")\n  println(upper(p.name), p.name + "/" + p.tag, p.name == p.tag, index(p.tag, "lo"), right(p.name, 9), len_trim(p.name))', 'inout_field': '  person :: p = make("hello")\n  call grow2(@p.name)\n  println(p)', 'in_param': '  team :: t = team(make("a"), make("b"))\n  println(lastname(t.other), lastname(t.lead))', 'print_in_print': '  println(make("abc"), person("m", 1, "n"))', 'local_in_fn': '  println(swap_names(team(make("a"), make("b"))))', 'missing_tag_ok': '  team :: t\n  println(t)\n  t.title = ""\n  println(t, t.size)'}
RECORD_DEFS_5 = 'record sp\n  real :: x = 1.5\n  int32 :: k = 2\n\n  subroutine bump(inout self, int :: by)\n    @self.k = self.k + by\n  end subroutine\n\n  function sum2(self) -> real\n    return self.x + self.k\n  end function\nend record\n\nrecord dp\n  string :: name = "a"\n  sp :: inner\n  int :: n = 0\n\n  function label(self) -> string\n    return self.name + "#" + text(self.n)\n  end function\nend record\n\nfunction total(sp[] :: v) -> real\n  real :: s = 0.0\n  int :: i\n  do i = 1, size(v)\n    s = s + v[i].x\n  end do\n  return s\nend function\n\nfunction names(dp[] :: v) -> string\n  string :: s = ""\n  int :: i\n  do i = 1, size(v)\n    s = s + v[i].name + ","\n  end do\n  return s\nend function\n\nsubroutine fill(inout sp[] :: v)\n  int :: i\n  do i = 1, size(v)\n    @v[i].x = i * 2.0\n    @v[i].k = i\n  end do\nend subroutine\n\nsubroutine fill_d(inout dp[] :: v)\n  int :: i\n  do i = 1, size(v)\n    @v[i].name = v[i].name + text(i)\n    @v[i] = dp(v[i].name + "!", v[i].inner, i)\n  end do\nend subroutine\n\nsubroutine one(inout sp :: p)\n  @p.x = p.x * 10.0\nend subroutine\n\nsubroutine one_d(inout dp :: p)\n  @p.name = p.name + "?"\nend subroutine\n\nsubroutine setx(inout real :: x)\n  @x = 99.0\nend subroutine\n\nfunction first_name(dp[] :: v) -> string\n  return v[1].name\nend function\n'
RECORD_CASES_5 = {'defaults': '  sp[3] :: a\n  dp[2] :: b\n  println(a, b, size(a), size(b))', 'elements': '  sp[3] :: a\n  a[2].x = 4.0\n  a[1] = sp(9.0, 7)\n  println(a, a[1].x, a[3].k, a[2])', 'elements_d': '  dp[3] :: a\n  a[2].name = "mid"\n  a[1] = dp("first", sp(1.0, 1), 5)\n  a[3].inner.x = 8.0\n  println(a, a[1].inner.k, a[2].label(), a[1].label())', 'copy': '  sp[3] :: a\n  sp[3] :: b\n  a[1].x = 5.0\n  b = a\n  a[2].x = 6.0\n  println(a, b)', 'copy_d': '  dp[2] :: a\n  dp[2] :: b\n  a[1].name = "one"\n  b = a\n  a[1].name = "changed"\n  b[2].name = "b2"\n  println(a, b)', 'self_copy': '  dp[2] :: a\n  a[1].name = "keep"\n  a = a\n  a[2] = a[1]\n  a[1].name = "x"\n  println(a)', 'elem_to_scalar': '  dp[2] :: a\n  a[1].name = "keep"\n  dp :: q = a[1]\n  q.name = "q"\n  println(a[1].name, q.name)\n  a[2] = q\n  q.name = "z"\n  println(a)', 'literal_init': '  sp[2] :: a = [sp(1.0, 1), sp(2.0, 2)]\n  println(a)', 'literal_init_d': '  dp[2] :: a = [dp("p", sp(), 1), dp("q", sp(3.0, 3), 2)]\n  println(a, names(a))', 'literal_assign': '  dp[2] :: a\n  a = [dp("p", sp(), 1), dp("q", sp(3.0, 3), 2)]\n  println(a)', 'literal_wrong_size': '  sp[3] :: a = [sp(1.0, 1), sp(2.0, 2)]\n  println(a)', 'assign_wrong_size': '  sp[3] :: a\n  sp[2] :: b\n  b = a\n  println(b)', 'broadcast': '  sp[3] :: a\n  a = sp(7.0, 7)\n  println(a)', 'broadcast_init': '  sp[2] :: a = sp(7.0, 7)\n  println(a)', 'oob': '  sp[3] :: a\n  println(a[4])', 'oob0': '  sp[3] :: a\n  a[0].x = 1.0', 'oob_write': '  sp[3] :: a\n  a[4] = sp()', 'oob_field_order': '  sp[3] :: a\n  int :: i = 5\n  a[i].x = a[1].x / 0.0', 'zero_len': '  sp[0] :: a\n  println(a, size(a))', 'neg_len': '  int :: n = -1\n  sp[n] :: a\n  println(1)', 'dims_expr': '  int :: n = 2\n  sp[n + 1] :: a\n  println(size(a))', 'params': '  sp[4] :: a\n  call fill(@a)\n  println(a, total(a))', 'params_d': '  dp[2] :: a\n  call fill_d(@a)\n  println(a, names(a), first_name(a))', 'inout_element': '  sp[2] :: a\n  call one(@a[2])\n  call @a[1].bump(3)\n  println(a, a[1].sum2(), a[2].sum2())', 'inout_element_d': '  dp[2] :: a\n  call one_d(@a[1])\n  call one_d(@a[1])\n  println(a)', 'inout_field': '  sp[2] :: a\n  call setx(@a[2].x)\n  println(a)', 'loop': '  sp[5] :: a\n  int :: i\n  do i = 1, 5\n    a[i] = sp(i * 1.0, i)\n    a[i].k = a[i].k + a[1].k\n  end do\n  println(a)', 'loop_temps': '  dp[3] :: a\n  int :: i\n  int :: s = 0\n  do i = 1, 400\n    a = [dp(text(i), sp(), i), dp("b", sp(1.0, 1), 2), a[1]]\n    a[2].name = a[2].name + a[1].name\n    s = s + len(a[2].name) + len(a[3].label())\n  end do\n  println(s, a)', 'nested_read': '  dp[2] :: a\n  a[2].inner.k = 6\n  println(a[2].inner.k + 1, a[2].inner.x * 2.0)', 'element_method': '  sp[2] :: a\n  a[1].x = 3.0\n  println(a[1].sum2() + a[2].sum2())', 'index_expr': '  sp[3] :: a\n  int :: i = 1\n  a[i + 1].x = 2.0\n  a[a[2].k].x = 7.0\n  println(a)', 'print_each': '  dp[2] :: a\n  println(a[1])\n  println(a[2].name, a[1].inner)', 'empty_literal': '  sp[1] :: a = [sp()]\n  println(a)', 'section': '  sp[3] :: a\n  println(a[1:2])', 'allocatable': '  allocatable sp[] :: a\n  allocate a[2]\n  println(size(a))', 'two_dim': '  sp[2, 2] :: a\n  println(a)', 'cmp': '  sp[2] :: a\n  sp[2] :: b\n  println(a == b)'}
IFACE_DEFS = 'interface figure\n  function name(self) -> string\n  function area(self) -> real\n  subroutine grow(inout self, real :: k)\n  function scaled(self, real :: k, string :: tag) -> string\nend interface\n\nrecord circle is figure\n  string :: label = "circle"\n  real :: r = 1.0\n  function name(self) -> string\n    return self.label\n  end function\n  function area(self) -> real\n    return 3.0 * self.r ** 2\n  end function\n  subroutine grow(inout self, real :: k)\n    @self.r = self.r * k\n    @self.label = self.label + "+"\n  end subroutine\n  function scaled(self, real :: k, string :: tag) -> string\n    return tag + text(self.r * k)\n  end function\nend record\n\nrecord rect\n  real :: w = 2.0\n  real :: h = 1.0\n  function name(self) -> string\n    return "rect"\n  end function\n  function area(self) -> real\n    return self.w * self.h\n  end function\n  subroutine grow(inout self, real :: k)\n    @self.w = self.w * k\n  end subroutine\n  function scaled(self, real :: k, string :: tag) -> string\n    return tag + text(self.w * k)\n  end function\nend record\n\nrecord other\n  int :: n = 1\n  function name(self) -> string\n    return "other"\n  end function\nend record\n\ninterface sized\n  function area(self) -> real\nend interface\n\nfunction make(int :: n) -> figure\n  if n > 0 then\n    return circle("c" + text(n), n * 1.0)\n  end if\n  return rect()\nend function\n\nfunction pick(figure :: a, figure :: b, logical :: first) -> figure\n  if first then\n    return a\n  end if\n  return b\nend function\n\nfunction total(figure[] :: v) -> real\n  real :: s = 0.0\n  int :: i\n  do i = 1, size(v)\n    s = s + v[i].area()\n  end do\n  return s\nend function\n\nsubroutine show(figure :: f)\n  println(f.name(), f.area())\nend subroutine\n\nsubroutine widen(inout figure :: f)\n  call @f.grow(2.0)\nend subroutine\n\nsubroutine fill(inout figure[] :: v)\n  @v[1] = circle()\n  @v[2] = rect(3.0, 4.0)\nend subroutine\n\nfunction by_size(sized :: s) -> real\n  return s.area()\nend function\n\nfunction none_ret(int :: n) -> figure\n  if n > 5 then\n    return rect()\n  end if\nend function\n'
IFACE_CASES = {'assign_print': '  figure :: f\n  circle :: c = circle("x", 5.0)\n  f = c\n  c.r = 1.0\n  println(f, f.area(), c)', 'init': '  figure :: f = rect(3.0, 4.0)\n  println(f, f.name(), f.area())', 'reassign_kinds': '  figure :: f = rect()\n  println(f)\n  f = circle("q", 2.0)\n  println(f)\n  f = rect(9.0, 9.0)\n  println(f)', 'method_sub': '  figure :: f = circle()\n  call @f.grow(2.0)\n  call @f.grow(2.0)\n  println(f)\n  f = rect()\n  call @f.grow(5.0)\n  println(f)', 'method_args': '  figure :: f = circle("c", 2.0)\n  println(f.scaled(3.0, "s="))\n  f = rect()\n  println(f.scaled(2.0, "w="))', 'copy_semantics': '  figure :: f = circle("c", 2.0)\n  figure :: g = f\n  call @f.grow(10.0)\n  println(f, g)\n  g = f\n  call @g.grow(2.0)\n  println(f, g)', 'results': '  figure :: f = make(2)\n  println(f, make(0), make(3).area(), make(1).name())\n  f = make(0)\n  println(f)', 'pick': '  figure :: a = circle()\n  figure :: b = rect()\n  println(pick(a, b, true), pick(a, b, false), pick(circle("z", 1.0), b, true).name())', 'param_record': '  circle :: c = circle("p", 3.0)\n  rect :: r = rect()\n  call show(c)\n  call show(r)\n  call show(circle())', 'array': '  figure[3] :: a\n  a[1] = circle()\n  a[2] = rect(1.0, 5.0)\n  a[3] = a[1]\n  call @a[3].grow(4.0)\n  println(a[1].area(), a[2].area(), a[3].area())\n  println(a)', 'array_unassigned_print': '  figure[2] :: a\n  println(a)\n  a[2] = rect()\n  println(a, size(a))', 'array_param': '  figure[2] :: a\n  call fill(@a)\n  println(total(a), a)', 'array_in_total': '  figure[2] :: a = rect()\n  println(total(a))', 'array_broadcast': '  figure[2] :: a\n  a = circle("b", 2.0)\n  call @a[1].grow(2.0)\n  println(a)', 'inout_param': '  figure :: f = circle()\n  call widen(@f)\n  println(f)', 'inout_param_elem': '  figure[2] :: a = rect()\n  call widen(@a[2])\n  println(a)', 'unassigned_print': '  figure :: g\n  println(g)', 'unassigned_assign': '  figure :: g\n  figure :: f\n  f = g', 'unassigned_pass': '  figure :: g\n  call show(g)', 'unassigned_call': '  figure :: g\n  println(g.area())', 'unassigned_sub': '  figure :: g\n  call @g.grow(2.0)', 'unassigned_init': '  figure :: g\n  figure :: h = g', 'unassigned_elem_read': '  figure[2] :: z\n  figure :: f = z[1]', 'unassigned_elem_call': '  figure[2] :: z\n  println(z[2].area())', 'unassigned_elem_sub': '  figure[2] :: z\n  call @z[1].grow(1.0)', 'unassigned_elem_pass': '  figure[2] :: z\n  call show(z[1])', 'unassigned_inout': '  figure :: g\n  call widen(@g)', 'index_oob': '  figure[2] :: z\n  z[3] = rect()', 'narrower_iface': '  println(by_size(circle("n", 2.0)), by_size(rect()))', 'loop_temps': '  figure :: f = rect()\n  int :: i\n  real :: s = 0.0\n  do i = 1, 300\n    f = make(i)\n    s = s + f.area() + len(f.scaled(1.0, "x"))\n    call @f.grow(1.5)\n  end do\n  println(s, f)', 'ret_never': '  println(none_ret(1))', 'deep_in_array_loop': '  figure[3] :: a\n  int :: i\n  do i = 1, 200\n    a[1] = make(i)\n    a[2] = a[1]\n    call @a[2].grow(2.0)\n    a[3] = make(0)\n  end do\n  println(a)'}
SELECT_DEFS = 'interface figure\n  function area(self) -> real\n  function name(self) -> string\nend interface\ninterface sized\n  function area(self) -> real\nend interface\ninterface tagged\n  function name(self) -> string\nend interface\n\nrecord circle is figure\n  string :: label = "circle"\n  real :: r = 1.0\n  function area(self) -> real\n    return 3.0 * self.r ** 2\n  end function\n  function name(self) -> string\n    return self.label\n  end function\nend record\n\nrecord rect is figure\n  real :: w = 2.0\n  real :: h = 1.0\n  function area(self) -> real\n    return self.w * self.h\n  end function\n  function name(self) -> string\n    return "rect"\n  end function\nend record\n\nrecord tri is figure\n  string :: note = "t"\n  real :: b = 1.0\n  function area(self) -> real\n    return self.b\n  end function\n  function name(self) -> string\n    return "tri" + self.note\n  end function\nend record\n\nfunction describe(figure :: f) -> string\n  select type f as p\n    case circle\n      return "circle " + p.label + text(p.r)\n    case rect\n      return "rect " + text(p.w)\n    case default\n      return "other " + p.name()\n  end select\n  return "unreachable"\nend function\n\nfunction widest(figure[] :: v) -> real\n  real :: best = 0.0\n  int :: i\n  do i = 1, size(v)\n    if v[i] is rect as r then\n      if r.w > best then\n        best = r.w\n      end if\n    end if\n  end do\n  return best\nend function\n\nfunction scan(figure[] :: v) -> int\n  int :: i\n  do i = 1, size(v)\n    select type v[i] as p\n      case tri\n        return i\n      case default\n        println("skip", i)\n    end select\n  end do\n  return 0\nend function\n'
SELECT_CASES = {'basic': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  do i = 1, 3\n    select type a[i] as p\n      case circle\n        println("circle", p.r, p.label, p.area())\n      case rect\n        println("rect", p.w)\n      case default\n        println("default", p.name(), p.area())\n    end select\n  end do', 'interface_case': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  do i = 1, 3\n    select type a[i] as p\n      case circle\n        println("circle")\n      case sized\n        println("sized", p.area())\n      case default\n        println("default")\n    end select\n  end do', 'record_beats_interface': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  select type a[1] as p\n    case sized\n      println("sized", p.area())\n    case circle\n      println("circle", p.r)\n    case default\n      println("default")\n  end select', 'interface_first_fit': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  select type a[2] as p\n    case tagged\n      println("tagged", p.name())\n    case sized\n      println("sized", p.area())\n    case default\n      println("default")\n  end select', 'empty_default': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  select type a[3] as p\n    case circle\n      println("circle")\n    case default\n  end select\n  println("after")', 'copy_modify': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  select type a[1] as p\n    case circle\n      p.r = 10.0\n      p.label = p.label + "!"\n      println(p.r, a[1].area())\n      a[1] = p\n    case default\n  end select\n  println(a[1].area(), a[1])', 'write_back_loop': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  do i = 1, 3\n    select type a[i] as p\n      case rect\n        p.w = p.w * 2.0\n        a[i] = p\n      case default\n    end select\n  end do\n  println(a)', 'break_stmt': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  do i = 1, 3\n    select type a[i] as p\n      case circle\n        println("a")\n        break\n        println("not printed")\n      case default\n        println("d", i)\n    end select\n    println("end", i)\n  end do', 'exit_cycle': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  do i = 1, 3\n    select type a[i] as p\n      case circle\n        cycle\n      case rect\n        println("rect")\n        exit\n      case default\n        println("d")\n    end select\n    println("not reached for rect", i)\n  end do\n  println("done")', 'nested': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  select type a[1] as p\n    case circle\n      select type a[2] as q\n        case rect\n          println(p.r, q.w)\n        case default\n          println("no")\n      end select\n      println("inner done", p.label)\n    case default\n      println("no")\n  end select', 'same_name_nested': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  select type a[1] as p\n    case circle\n      select type a[2] as p\n        case rect\n          println(p.w)\n        case default\n      end select\n      println(p.r)\n    case default\n  end select', 'funcs': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  println(describe(a[1]), describe(a[2]), describe(a[3]), describe(circle()))\n  println(widest(a), scan(a))', 'unassigned_subject': '  figure :: g\n  select type g as p\n    case default\n  end select', 'unassigned_elem': '  figure[2] :: g\n  select type g[1] as p\n    case default\n  end select', 'is_basic': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  do i = 1, 3\n    if a[i] is circle as c then\n      println("circle", c.r)\n    else if a[i] is rect as r then\n      println("rect", r.w)\n    else\n      println("neither")\n    end if\n  end do', 'is_no_as': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  if a[1] is circle then\n    println("yes")\n  end if\n  if a[2] is circle then\n    println("no")\n  else if a[2] is sized then\n    println("sized")\n  end if', 'is_interface': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  if a[3] is tagged as t then\n    println(t.name())\n  end if\n  if a[3] is sized as s then\n    println(s.area())\n  end if', 'is_same_name_twice': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  if a[1] is circle as x then\n    println(x.r)\n  end if\n  if a[2] is rect as x then\n    println(x.w)\n  end if', 'is_write_field': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  if a[2] is rect as r then\n    r.w = 100.0\n    a[2] = r\n  end if\n  println(a[2].area())', 'is_loop_temps': '  figure[3] :: a\n  a[1] = circle("cc", 2.0)\n  a[2] = rect(5.0, 2.0)\n  a[3] = tri("x", 4.0)\n  int :: i\n  real :: s = 0.0\n  do i = 1, 300\n    a[1] = circle(text(i), i * 1.0)\n    if a[1] is circle as c then\n      s = s + c.r + len(c.label)\n    end if\n    select type a[1] as p\n      case circle\n        s = s + len(p.label)\n      case default\n    end select\n  end do\n  println(s)', 'is_unassigned': '  figure[2] :: g\n  if g[1] is circle then\n    println(1)\n  end if'}
CASE_DEFS = 'function grade(int :: g) -> string\n  select case g\n    case 90:100\n      return "A"\n    case 80:89\n      return "B"\n    case 1, 3, 5\n      return "odd"\n    case :0\n      return "neg"\n    case 101:\n      return "over"\n    case default\n      return "other"\n  end select\n  return "unreachable"\nend function\n\nfunction kind_of(string :: s) -> int\n  select case trim(s)\n    case "a":"f"\n      return 1\n    case "x", "y"\n      return 2\n    case "m":"w"\n      return 3\n  end select\n  return 0\nend function\n\nfunction lab(int32 :: n) -> string\n  return select case n\n    case 1 -> "one"\n    case 2, 3 -> "few"\n    case default -> text(n) + "!"\n  end select\nend function\n'
CASE_CASES = {'ints': '  int :: i\n  do i = -2, 104\n    println(i, grade(i))\n  end do', 'strings': '  println(kind_of("b"), kind_of("x"), kind_of("zz"), kind_of("c "), kind_of(""), kind_of("Q"))', 'no_default_no_match': '  int :: g = 7\n  select case g\n    case 1\n      println("one")\n  end select\n  println("after")', 'logical': '  logical :: b = true\n  select case b\n    case true\n      println("t")\n    case false\n      println("f")\n  end select\n  b = false\n  select case b\n    case true\n      println("t")\n    case default\n      println("d")\n  end select', 'fallthrough': '  int :: g = 3\n  select case fallthrough g\n    case 3\n      println("bronze")\n    case 2\n      println("silver")\n      break\n    case 1\n      println("gold")\n  end select\n  g = 2\n  select case fallthrough g\n    case 3\n      println("bronze")\n    case 2\n      println("silver")\n      break\n    case 1\n      println("gold")\n  end select\n  g = 1\n  select case fallthrough g\n    case 3\n      println("bronze")\n    case 2\n      println("silver")\n      break\n    case 1\n      println("gold")\n  end select\n  g = 9\n  select case fallthrough g\n    case 3\n      println("bronze")\n    case default\n      println("def")\n    case 1\n      println("gold")\n  end select', 'break': '  int :: i\n  do i = 1, 4\n    select case i\n      case 2\n        println("two")\n        break\n        println("not printed")\n      case default\n        println("d", i)\n    end select\n    println("end", i)\n  end do', 'exit_cycle': '  int :: i\n  do i = 1, 6\n    select case i\n      case 2\n        cycle\n      case 5\n        exit\n    end select\n    println(i)\n  end do', 'default_middle': '  int :: g = 4\n  select case g\n    case 1\n      println("1")\n    case default\n      println("d")\n    case 4\n      println("4")\n  end select', 'int32_subject': '  int32 :: g = 5\n  select case g\n    case 1, 3, 5\n      println("odd")\n    case default\n      println("even")\n  end select', 'string_subject_temp_loop': '  string :: s = "ab"\n  int :: i\n  int :: n = 0\n  do i = 1, 300\n    select case upper(s) + text(i)\n      case "AB1":"AB9"\n        n = n + 1\n        cycle\n      case default\n        n = n + 2\n    end select\n  end do\n  println(n)', 'expr': '  println(lab(1), lab(3), lab(9))\n  int :: g = 85\n  real :: r = select case g\n    case 85 -> 1.5\n    case default -> 2.5\n  end select\n  println(r)\n  int :: k = select case g\n    case 85 -> g + 1\n    case default -> 0\n  end select\n  println(k, select case g\n    case 1 -> true\n    case default -> false\n  end select)', 'expr_only_chosen_arm': '  int :: g = 1\n  int :: z = 0\n  println(select case g\n    case 1 -> 5\n    case default -> 1 / z\n  end select)', 'expr_string_loop': '  int :: i\n  int :: n = 0\n  do i = 1, 300\n    n = n + len(select case i\n      case 1:100 -> text(i) + "abc"\n      case default -> "z"\n    end select)\n  end do\n  println(n)', 'nested': '  int :: a = 2\n  int :: b = 3\n  select case a\n    case 2\n      select case b\n        case 3\n          println("2,3")\n          break\n        case default\n          println("2,?")\n      end select\n      println("after inner")\n    case default\n      println("d")\n  end select', 'real_subject': '  real :: x = 1.5\n  select case x\n    case 1\n      println(1)\n  end select', 'const_value': '  const int :: k = 3\n  int :: g = 3\n  select case g\n    case k\n      println("k")\n    case default\n      println("d")\n  end select', 'expr_mixed_arms': '  int :: g = 1\n  real :: r = select case g\n    case 1 -> 1\n    case default -> 2.5\n  end select\n  println(r)'}
STRING_DEFS = 'function twice(string :: s) -> string\n  return s + s\nend function\nfunction first(string :: s) -> string\n  if len(s) == 0 then\n    return "(empty)"\n  end if\n  return s[1]\nend function\nfunction rev(string :: s) -> string\n  if len(s) <= 1 then\n    return s\n  end if\n  return rev(s[2:]) + s[1]\nend function\nfunction nothing(string :: s) -> string\n  if len(s) > 3 then\n    return s\n  end if\nend function\nsubroutine app(inout string :: s, string :: t)\n  @s = s + t\nend subroutine\nfunction shout(string :: s) -> string\n  return upper(s) + "!"\nend function\n'
STRING_CASES = {'concat_loop': '  string :: s = ""\n  int :: i\n  do i = 1, 50\n    s = s + "ab"\n  end do\n  println(s, len(s))', 'compare': '  println("a" < "b", "b" < "a", "ab" < "abc", "abc" <= "abc", "B" < "a", "" < "a", "a" == "a ", "a" /= "b", "z" >= "a", "x" > "x")', 'index0': '  string :: s = "abc"\n  println(s[0])', 'index4': '  string :: s = "abc"\n  println(s[4])', 'index_neg': '  string :: s = "abc"\n  int :: i = -1\n  println(s[i])', 'part_ok': '  string :: s = "abcdef"\n  println(s[2:4], s[:2], s[5:], s[1:6], s[3:2], s[7:6], s[:0], s[:])', 'part_hi': '  string :: s = "abc"\n  println(s[2:9])', 'part_lo': '  string :: s = "abc"\n  println(s[0:2])', 'part_rev': '  string :: s = "abc"\n  println(s[3:1])', 'part_empty_str': '  string :: s = ""\n  println(s[1:0], len(s[:]))\n  println(s[1])', 'repeat': '  println(repeat("ab", 3), repeat("x", 0), repeat("", 5) + "|")', 'repeat_neg': '  int :: n = -2\n  println(repeat("a", n))', 'funcs': '  println(twice("ab"), first("xyz"), first(""), rev("hello"), shout("a" + "b"))', 'funcs_nested': '  println(twice(twice(twice("a"))), rev(rev("abc")), first(twice("q")))', 'nothing': '  println(nothing("abcd"))\n  println(nothing("a"))', 'inout': '  string :: s = "a"\n  int :: i\n  do i = 1, 3\n    call app(@s, s)\n  end do\n  println(s)', 'inout_other': '  string :: s = "a"\n  string :: t = "b"\n  call app(@s, t)\n  call app(@t, "zz")\n  println(s, t)', 'if_elif': '  string :: s = "hello"\n  if upper(s) == "X" then\n    println(1)\n  else if contains(s, "ell") and len(s) > 2 then\n    println(2)\n  else\n    println(3)\n  end if', 'while_temp': '  string :: s = ""\n  do while len(s + "x") < 6\n    s = s + "y"\n  end do\n  println(s)', 'exit_loop': '  string :: s = ""\n  int :: i\n  do i = 1, 10\n    s = s + "a"\n    if len(s) == 3 then\n      exit\n    end if\n  end do\n  println(s, i)', 'cycle_loop': '  string :: s = ""\n  int :: i\n  do i = 1, 5\n    if mod(i, 2) == 0 then\n      cycle\n    end if\n    s = s + upper("ab")\n  end do\n  println(s)', 'quotes': '  println(\'say "hi"\', "100%", "%d %s", "it\'s")', 'empty_print': '  println("", "", "a")\n  println()', 'print_mix': '  string :: s = "s"\n  println(1, s, 2.5, true, s + s, len(s))', 'decl_loop': '  int :: i\n  do i = 1, 2\n    string :: s = "a"\n    println(s)\n  end do', 'index_fn': '  println(index("abcabc", "c"), index("abcabc", "c", true), index("abc", ""), index("abc", "", true), index("", ""), index("", "a"), index("ab", "abc"))', 'starts': '  println(starts_with("abc", ""), ends_with("abc", ""), starts_with("ab", "abc"), ends_with("ab", "abc"), contains("", ""), contains("abc", "bc"))', 'trim_ws': '  println("[" + trim("  a b  ") + "]", len_trim("   "), len(trim("   ")), "[" + trim("") + "]")', 'upper_lower': '  println(upper("aBc 1-z"), lower("AbC 1-Z"))', 'swap_via_tmp': '  string :: a = "x"\n  string :: b = "y"\n  string :: t = a\n  a = b\n  b = t\n  println(a, b)', 'self_assign': '  string :: s = "abc"\n  s = s\n  s = s[2:3] + s[1]\n  s = s + s\n  println(s)', 'int_in_text': '  string :: s = "a"\n  s = 5', 'cmp_mixed': '  println("a" == 1)', 'long': '  string :: s = repeat("abcdefghij", 10000)\n  println(len(s), s[99999:], index(s, "j", true))'}


FORMAT_CASES = {'grid': '  real[14] :: xs = [0.0, -0.0, 1.0, -1.0, 0.5, 0.125, 2.5, 12345.678, -0.04, 99999.5, 1.0e-5, 123456789.0, 1.0e22, 0.999]\n  int :: i\n  int :: d\n  int :: w\n  do i = 1, 14\n    do d = 0, 5\n      do w = 0, 14, 7\n        println(fixed(xs[i], d, w), scientific(xs[i], d, w), scientific(xs[i], d + 1, w, "fortran"), general(xs[i], d + 1, w), general(xs[i], d + 1, w, "fortran"))\n      end do\n      println(fixed(xs[i], d), scientific(xs[i], d), general(xs[i], d + 1))\n    end do\n  end do', 'text_kinds': '  int32 :: a = 7\n  real32 :: r = 0.1\n  println(text(a), text(r), text(1.0e22), text(1.0e-7), text(false), text(-5), text(""))', 'pad_cases': '  println("[" + right(5, 0) + "]", "[" + left(5, 0) + "]", "[" + right("ab", 5, "*") + "]", "[" + left("ab", 5, "*") + "]", "[" + right(true, 8) + "]", "[" + right(2.5, 2) + "]", "[" + right(-5, 4, "0") + "]", "[" + right(+5, 4, "0") + "]", "[" + left(-5, 4, "0") + "]", "[" + right("-ab", 6, "0") + "]")', 'width_neg': '  int :: w = -1\n  println(right(1, w))', 'width_neg_left': '  int :: w = -3\n  println(left("a", w))', 'fill_long': '  println(right(1, 4, "ab"))', 'fill_empty': '  println(left(1, 4, ""))', 'fixed_neg_dec': '  int :: d = -1\n  println(fixed(1.5, d))', 'fixed_neg_w': '  int :: w = -1\n  println(fixed(1.5, 2, w))', 'sci_neg': '  int :: d = -1\n  println(scientific(1.5, d))', 'sci_fortran_0': '  println(scientific(1.5, 0, 8, "fortran"))', 'gen_zero_digits': '  println(general(1.5, 0))', 'gen_neg_digits': '  int :: d = -2\n  println(general(1.5, d))', 'gen_fortran_stars': '  println(general(123456.0, 3, 6, "fortran"), general(0.001, 3, 5, "fortran"), general(1.0e150, 3, 12, "fortran"), general(1.0e-150, 3, 12, "fortran"))', 'specials': '  real :: z = 0.0\n  real :: inf = 1.0 / z\n  real :: nan = z / z\n  println(fixed(inf, 2), fixed(-inf, 2, 12), fixed(nan, 2, 2), scientific(inf, 3, 5), general(nan, 3, 4), general(-inf, 3, 3, "fortran"), text(inf), text(nan), right(inf, 4))', 'int_args': '  int :: n = 12345\n  int32 :: m = 67\n  println(fixed(n, 2), fixed(m, 1, 8), scientific(n, 2), general(m, 3), scientific(m, 2, 10, "fortran"))', 'real32_args': '  real32 :: r = 0.1\n  println(fixed(r, 9), scientific(r, 9), general(r, 9), text(r), right(r, 12))', 'in_loop': '  string :: line = ""\n  int :: i\n  do i = 1, 5\n    line = line + right(i * i, 4) + " " + fixed(i / 3.0, 2, 6) + "|"\n  end do\n  println(line)', 'table': '  int :: i\n  do i = 1, 4\n    println(left("row" + text(i), 8) + right(fixed(i * 1.5, 1), 6) + tf(mod(i, 2) == 0))\n  end do'}


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
        for defs, cases in ((RECORD_DEFS, RECORD_CASES), (RECORD_DEFS_2, RECORD_CASES_2), (RECORD_DEFS_3, RECORD_CASES_3),
                            (RECORD_DEFS_4, RECORD_CASES_4),
                            (RECORD_DEFS_5, RECORD_CASES_5),
                            (IFACE_DEFS, IFACE_CASES),
                            (SELECT_DEFS, SELECT_CASES),
                            (CASE_DEFS, CASE_CASES),
                            (STRING_DEFS, STRING_CASES), ("", FORMAT_CASES)):
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
        self.refused(prog("  string[2] :: s\n  println(1)"), "the declaration of 's'")
        self.refused(prog("  string :: s = \"h\u00e9\"\n  println(s)"), "outside ASCII")
        self.refused(prog("  string :: s = \"abc\"\n  println(s[1:3:2])"), "a part of a text")
        self.refused(prog("  string :: s = \"abc\"\n  s[1] = \"x\"\n  println(s)"), "part of a text")
        self.refused("interface f\n  function a(self) -> int\nend interface\nrecord r is f\n  function a(self) -> int\n    return 1\n  end function\nend record\nprogram t\n  f :: x = 3\nend program\n", "a value that is not a f")
        self.refused(SELECT_DEFS + "program t\n  figure :: f = circle()\n  select type f as p\n    case circle\n      p = circle()\n    case default\n  end select\nend program\n", "an assignment to 'p'")
        self.refused(SELECT_DEFS + "program t\n  select type describe(circle()) as p\n    case default\n  end select\nend program\n", "anything but an interface value")
        self.refused(prog("  const int :: k = 3\n  int :: g = 3\n  select case g\n    case k\n      println(1)\n  end select"), "not a literal")
        self.refused(prog("  real :: x = 1.5\n  select case x\n    case 1\n      println(1)\n  end select"), "a select case on anything but")
        self.refused("record p\n  string[2] :: s\nend record\nprogram t\nend program\n", "field 's' of record 'p'")
        self.refused("record p\n  string :: s = \"h\u00e9\"\nend record\nprogram t\nend program\n", "the default of field 's'")
        self.refused("record p\n  string :: s\nend record\nprogram t\n  p :: q\n  q.s = 5\nend program\n", "a type error")
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
