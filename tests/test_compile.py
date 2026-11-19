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


FAILING = {
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
        self.refused(prog("  allocatable int[] :: a\n  println(1)"), "allocatable")
        self.refused(prog("  int[2, 2, 2, 2] :: a\n  println(1)"), "the declaration of 'a'")
        self.refused(prog("  int[3] :: a = [1, 2, 3]\n  println(a[1:2])"), "part of an array")
        self.refused(prog("  real[3] :: a = [1.0, 2.0, 3.0]\n  println(sum(a))"), "sum of reals")
        self.refused(prog("  real32 :: x = 1.0\n  println(x)"), "the declaration of 'x' (real32)")
        self.refused(prog("  string :: s = \"a\"\n  println(s)"), "the declaration of 's' (string)")
        self.refused("record p\n  real :: x = 0.0\nend record\nprogram t\nend program\n", "records")
        self.refused("module m\n  public int :: k = 1\nend module\nprogram t\nend program\n", "modules")
        self.refused(prog("  int :: n = 1\n  println(floor(2.5), n)"), "the function 'floor'")
        self.refused(prog("  const int :: i = 1\n  do i = 1, 2\n  end do"), "constant or parameter")
        self.refused("function f(int :: n) -> int\n  n = 2\n  return n\nend function\nprogram t\nend program\n", "constant or parameter")
        self.refused("subroutine s(out int :: k)\n  @k = 1\nend subroutine\nprogram t\nend program\n", "out parameter")

    def test_calls_that_the_interpreter_rejects_are_refused(self):
        defs = "subroutine s(inout real[] :: x, real[] :: y)\n  @x[1] = 9.0\nend subroutine\n"
        self.refused(prog("  real[2] :: a = 1.0\n  call s(@a, a)", defs), "passed as inout and as another argument")
        self.refused(prog("  const int :: n = 1\n  call t(@n)", "subroutine t(inout int :: k)\n  @k = 2\nend subroutine\n"),
                     "constant or parameter")
        self.refused("function g(int[] :: a) -> int[]\n  return a\nend function\nprogram t\nend program\n", "returns")
        self.refused(prog("  int[3] :: a = [1, 2, 3]\n  int :: t = 0\n  println(mod(a, 2), t)"), "mod() of an array")

    def test_a_program_with_a_type_error_is_refused(self):
        self.refused(prog("  int :: n = 2.5"), "type error")

    def test_the_line_is_named(self):
        self.refused(prog("  int :: n = 1\n  println(floor(2.5), n)"), "(line 3)")


if __name__ == "__main__":
    unittest.main()
