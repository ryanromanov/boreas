import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output

FREQ = """function frequencies(integer :: n) -> integer[]
  allocatable integer :: out(:)
  allocate out(n)
  integer :: i
  do i = 1, n
    out(i) = i - 1
  end do
  return out
end function
"""


def program(funcs, body):
    return funcs + "\nprogram t\n" + body + "\nend program\n"


class ArrayResultTests(unittest.TestCase):
    def test_assign_to_an_allocatable_and_use_in_expressions(self):
        src = program(FREQ, "  allocatable integer :: f(:)\n  f = frequencies(5)\n  println(f)\n"
                            "  println(sum(frequencies(4)), size(frequencies(0)))")
        self.assertEqual(run_bor(src), ["[0, 1, 2, 3, 4]", "6 0"])

    def test_assign_to_a_fixed_array_and_compute_with_it(self):
        src = program(FREQ, "  integer :: v(3)\n  v = frequencies(3)\n  println(v, frequencies(3) + 1)")
        self.assertEqual(run_bor(src), ["[0, 1, 2] [1, 2, 3]"])

    def test_two_dimensions(self):
        src = program("""function grid(integer :: n) -> real[,]
  allocatable real :: g(:, :)
  allocate g(n, 2)
  g(1, 1) = 1.5
  return g
end function""", "  println(grid(2))")
        self.assertEqual(run_bor(src), ["[[1.5, 0.0], [0.0, 0.0]]"])

    def test_elements_widen_to_the_element_type(self):
        src = program("function f() -> real[]\n  return [1, 2, 3]\nend function", "  println(f())")
        self.assertEqual(run_bor(src), ["[1.0, 2.0, 3.0]"])

    def test_strings_and_records(self):
        src = """record pt
  integer :: x
end record
function names() -> string[]
  return ["a", "bc"]
end function
function pts() -> pt[]
  pt :: a(2)
  a(2).x = 7
  return a
end function
program t
  pt :: p(2)
  p = pts()
  println(names(), size(names()), p(2).x)
end program
"""
        self.assertEqual(run_bor(src), ["[a, bc] 2 7"])

    def test_wrong_shape_of_result_is_an_error(self):
        for ret, value, message in [("integer[]", "5", "must return a 1-dimensional integer array, not integer"),
                                    ("integer[,]", "[1, 2]", "not a 1-dimensional array"),
                                    ("real", "[1.0, 2.0]", "cannot assign array to real")]:
            with self.subTest(ret=ret):
                src = program(f"function f() -> {ret}\n  return {value}\nend function", "  println(f())")
                with self.assertRaises(LangError) as cm:
                    run_bor(src)
                self.assertIn(message, str(cm.exception))

    def test_returning_an_unallocated_array_is_an_error(self):
        src = program("function f() -> integer[]\n  allocatable integer :: a(:)\n  return a\nend function",
                      "  println(f())")
        with self.assertRaises(LangError) as cm:
            run_bor(src)
        self.assertIn("not allocated", str(cm.exception))

    def test_an_array_result_cannot_go_into_a_scalar(self):
        src = program(FREQ, "  integer :: x = frequencies(3)")
        with self.assertRaises(LangError):
            run_bor(src)

    def test_a_procedure_signature_cannot_return_an_array(self):
        src = "function f(function(real) -> real[] :: g) -> real\n  return 1.0\nend function\nprogram t\nend program"
        with self.assertRaises(LangError) as cm:
            parse(src)
        self.assertIn("cannot return an array", str(cm.exception))

    def test_prints_back_the_same(self):
        src = program(FREQ, "  println(frequencies(2))")
        self.assertIn("-> integer[]", to_source(parse(src), []))


class ArrayResultConversionTests(unittest.TestCase):
    def check(self, src):
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return text

    SHAPES = """module m
  implicit none
contains
  function freq(n) result(r)
    integer, intent(in) :: n
    integer, dimension(n) :: r
    integer :: i
    do i = 1, n
      r(i) = i - 1
    end do
  end function
  function doubled(x) result(y)
    real, intent(in) :: x(:)
    real :: y(size(x))
    y = 2.0 * x
  end function
  function grow(x, k) result(r)
    real, intent(in) :: x(:)
    integer, intent(in) :: k
    real, allocatable :: r(:)
    allocate (r(size(x) + k))
    r = 0.0
    r(1:size(x)) = x
  end function
  function ident(n) result(a)
    integer, intent(in) :: n
    real :: a(n, n)
    integer :: i
    a = 0.0
    do i = 1, n
      a(i, i) = 1.0
    end do
  end function
end module
program t
  use m
  implicit none
  real :: v(3) = [1.0, 2.0, 3.0]
  print *, freq(4)
  print *, doubled(v)
  print *, grow(v, 2)
  print *, sum(ident(3))
end program
"""

    def test_the_three_fortran_shapes(self):
        text = self.check(self.SHAPES)
        self.assertIn("function freq(integer :: n) -> integer[]", text)
        self.assertIn("function doubled(real32 :: x) -> real32[]", text)
        self.assertIn("function ident(integer :: n) -> real32[,]", text)
        self.assertIn("allocate r", text)

    def test_a_function_that_changes_module_variables_and_returns_an_array_is_a_todo(self):
        src = """module m
  implicit none
  integer :: count = 0
contains
  function f(n) result(r)
    integer, intent(in) :: n
    integer :: r(n)
    count = count + 1
    r = 1
  end function
end module
program t
  use m
  print *, f(2)
end program
"""
        _, findings = converted(src)
        self.assertTrue(any("returns an array" in f[2] for f in todos(findings)))
        text, _ = converted(src)
        self.assertNotIn("count = count", text)


if __name__ == "__main__":
    unittest.main()
