import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.verify import same_output

INTEGRATE = """subroutine integrate(f, a, b, result)
  external f
  double precision f
  double precision a, b, result
  result = (b - a) * f((a + b) / 2.0d0)
end subroutine

double precision function square(x)
  double precision x
  square = x * x
end function

program t
  external square
  double precision r
  call integrate(square, 0.0d0, 2.0d0, r)
  print *, r
end program t
"""

APPLY = """subroutine apply(g, n, x)
  external g
  integer n, i
  double precision x(n)
  do i = 1, n
    call g(x(i))
  end do
end subroutine

subroutine scale(x)
  double precision x
  x = x * 2
end subroutine

program t
  external scale
  double precision v(3)
  v = [1.0d0, 2.0d0, 3.0d0]
  call apply(scale, 3, v)
  print *, v
end program t
"""

PASS_ON = """subroutine outer(f, r)
  external f
  double precision f, r
  call inner(f, r)
end subroutine

subroutine inner(h, r)
  external h
  double precision h, r
  r = h(3.0d0) + 1.0d0
end subroutine

double precision function triple(x)
  double precision x
  triple = 3.0d0 * x
end function

program t
  external triple
  double precision r
  call outer(triple, r)
  print *, r
end program t
"""

TWO_SHAPES = """subroutine run(f, r)
  external f
  double precision f, r
  r = f(1.0d0)
end subroutine

double precision function a1(x)
  double precision x
  a1 = x
end function

double precision function a2(x, y)
  double precision x, y
  a2 = x + y
end function

program t
  external a1, a2
  double precision r
  call run(a1, r)
  call run(a2, r)
end program t
"""

NO_CALLER = """subroutine run(f, r)
  external f
  double precision f, r
  r = f(1.0d0)
end subroutine
"""

WRONG_KIND = """subroutine run(f, r)
  external f
  double precision f, r
  r = f(1.0d0)
end subroutine

subroutine bump(x)
  double precision x
  x = x + 1
end subroutine

program t
  external bump
  double precision r
  call run(bump, r)
end program t
"""


class ExternalDummyTests(unittest.TestCase):
    def check(self, src):
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_a_function_argument_takes_the_signature_of_the_function_passed(self):
        text = self.check(INTEGRATE)
        self.assertIn("function(real :: x) -> real :: f", text)

    def test_a_subroutine_argument_takes_the_signature_of_the_subroutine_passed(self):
        text = self.check(APPLY)
        self.assertIn("subroutine(inout real :: x) :: g", text)

    def test_an_argument_passed_on_settles_through_the_chain(self):
        text = self.check(PASS_ON)
        self.assertEqual(text.count("function(real :: x) -> real"), 2)

    def test_callers_that_disagree_are_a_todo(self):
        _, findings = converted(TWO_SHAPES)
        self.assertTrue(any("different signatures" in m[2] for m in todos(findings)), todos(findings))

    def test_no_caller_is_a_todo(self):
        _, findings = converted(NO_CALLER)
        self.assertTrue(any("no call passes a procedure" in m[2] for m in todos(findings)), todos(findings))

    def test_a_function_used_as_a_value_but_passed_a_subroutine_is_a_todo(self):
        _, findings = converted(WRONG_KIND)
        self.assertTrue(any("used as a function but is passed" in m[2] for m in todos(findings)), todos(findings))


if __name__ == "__main__":
    unittest.main()
