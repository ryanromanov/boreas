import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, run_bor, run_gfortran, todos
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import ProcType, parse
from boreas.verify import same_output

COUNT_IF = """function count_if(n, w, pred) result(c)
  implicit none
  integer, intent(in) :: n
  double precision, intent(in) :: w(n)
  interface
    logical function pred_t(x)
      double precision, intent(in) :: x
    end function
  end interface
  procedure(pred_t) :: pred
  integer :: c, i
  c = 0
  do i = 1, n
    if (pred(w(i))) c = c + 1
  end do
end function count_if

function positive(x) result(r)
  implicit none
  double precision, intent(in) :: x
  logical :: r
  r = x > 0.0d0
end function positive

program t
  implicit none
  integer :: count_if
  logical :: positive
  external positive
  double precision :: w(5) = [1.0d0, -2.0d0, 3.0d0, 0.0d0, 5.5d0]
  print *, count_if(5, w, positive)
end program t
"""

TWICE = """subroutine twice(n, cb)
  implicit none
  integer, intent(inout) :: n
  interface
    subroutine cb_t(k)
      integer k
    end subroutine
  end interface
  procedure(cb_t) :: cb
  call cb(n)
  call cb(n)
end subroutine twice

subroutine bump(k)
  implicit none
  integer, intent(inout) :: k
  k = k + 1
end subroutine bump

program t
  implicit none
  integer :: n = 0
  external bump
  call twice(n, bump)
  print *, n
end program t
"""

INTEGRATE = """function integrate(f, a, b) result(total)
  implicit none
  interface
    function f_t(x)
      double precision, intent(in) :: x
      double precision :: f_t
    end function
  end interface
  procedure(f_t) :: f
  double precision, intent(in) :: a, b
  double precision :: total, h
  integer :: i
  h = (b - a) / 100.0d0
  total = 0.0d0
  do i = 0, 99
    total = total + f(a + (i + 0.5d0) * h) * h
  end do
end function integrate

program t
  implicit none
  double precision :: integrate
  intrinsic :: dsin, dcos
  print '(F8.5)', integrate(dsin, 0.0d0, 1.5707963267948966d0)
  print '(F8.5)', integrate(dcos, 0.0d0, 1.5707963267948966d0)
end program t
"""


def converted(src, name="t.f90"):
    text, findings = convert(src, name)
    try:
        parse(text)
    except LangError as e:
        if "no program found" not in str(e):       # a library has no program
            raise
    return text, findings


def fortran_output(src):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.f90")
        with open(path, "w") as f:
            f.write(src)
        return run_gfortran(path)


class CallbackConversionTests(unittest.TestCase):
    def check(self, src):
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    def test_a_function_callback_from_an_interface_block(self):
        text = self.check(COUNT_IF)
        self.assertIn("function(real :: x) -> logical :: pred", text)
        self.assertNotIn("interface", text.replace("interface block dropped", ""))

    def test_a_subroutine_callback_takes_its_intents_from_the_procedure_passed(self):
        text = self.check(TWICE)
        self.assertIn("subroutine(inout integer :: k) :: cb", text)

    def test_an_intrinsic_passed_to_a_callback_is_wrapped(self):
        text = self.check(INTEGRATE)
        self.assertIn("function sin_fn(real :: x) -> real", text)
        self.assertIn("integrate(sin_fn,", text)
        self.assertIn("integrate(cos_fn,", text)

    def test_a_callback_passed_on_keeps_its_signature(self):
        src = TWICE.replace("\nprogram t\n", """\nsubroutine outer(n, cb)
  implicit none
  integer, intent(inout) :: n
  interface
    subroutine cb_t(k)
      integer k
    end subroutine
  end interface
  procedure(cb_t) :: cb
  call twice(n, cb)
end subroutine outer

program t
""").replace("call twice(n, bump)", "call outer(n, bump)")
        self.check(src)

    def test_an_abstract_interface_in_a_module(self):
        src = """module m
  implicit none
  abstract interface
    function op_t(x) result(r)
      integer, intent(in) :: x
      integer :: r
    end function
  end interface
contains
  function apply(f, x) result(r)
    procedure(op_t) :: f
    integer, intent(in) :: x
    integer :: r
    r = f(x) + f(x + 1)
  end function
  function dbl(x) result(r)
    integer, intent(in) :: x
    integer :: r
    r = 2 * x
  end function
end module m

program t
  use m
  print *, apply(dbl, 5)
end program t
"""
        self.check(src)


class UnsupportedCaseTests(unittest.TestCase):
    def test_a_generic_interface_is_a_todo(self):
        src = """module m
  interface swap
    module procedure swap_int
  end interface
contains
  subroutine swap_int(a, b)
    integer, intent(inout) :: a, b
    integer :: t
    t = a
    a = b
    b = t
  end subroutine
end module
"""
        _, findings = converted(src)
        self.assertTrue(any("generic interface 'swap'" in m for _, _, m in findings), findings)

    def test_a_procedure_pointer_is_a_todo(self):
        src = """module m
  abstract interface
    subroutine cb_t(k)
      integer, intent(in) :: k
    end subroutine
  end interface
  procedure(cb_t), pointer :: active => null()
end module
"""
        _, findings = converted(src)
        self.assertTrue(any("procedure variable" in m for m in [f[2] for f in findings]), findings)

    def test_an_intrinsic_passed_to_an_unknown_callee_is_a_todo(self):
        src = "program t\n  intrinsic :: sin\n  call run(sin)\nend program t\n"
        _, findings = converted(src)
        self.assertTrue(any("cannot be wrapped" in m for _, _, m in findings), findings)

    def test_an_interface_block_alone_is_a_note_not_a_todo(self):
        src = """subroutine s()
  interface
    subroutine other(x)
      real x
    end subroutine
  end interface
  call other(1.0)
end subroutine s
"""
        _, findings = converted(src)
        self.assertTrue(any(k == "note" and "interface block dropped" in m for _, k, m in findings))

    def test_the_signature_is_a_proc_type(self):
        text, _ = converted(COUNT_IF)
        from boreas.parser import parse as p
        unit = p(text)
        self.assertIsInstance(unit.functions["count_if"].params[2][0], ProcType)


if __name__ == "__main__":
    unittest.main()
