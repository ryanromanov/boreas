import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.verify import same_output

SHAPES = """module shapes
  implicit none
  private
  public :: shape, circle, rect

  type, abstract :: shape
    character(len=16) :: name = "shape"
  contains
    procedure(area_i), deferred :: area
    procedure :: describe
  end type

  abstract interface
    function area_i(self) result(a)
      import :: shape
      class(shape), intent(in) :: self
      real :: a
    end function
  end interface

  type, extends(shape) :: circle
    real :: r = 1.0
  contains
    procedure :: area => circle_area
  end type

  type, extends(shape) :: rect
    real :: w = 1.0, h = 1.0
  contains
    procedure :: area => rect_area
  end type

contains

  function circle_area(self) result(a)
    class(circle), intent(in) :: self
    real :: a
    a = 3.14159 * self%r**2
  end function

  function rect_area(self) result(a)
    class(rect), intent(in) :: self
    real :: a
    a = self%w * self%h
  end function

  subroutine describe(self)
    class(shape), intent(in) :: self
    print '(a, f8.3)', trim(self%name) // " area =", self%area()
  end subroutine
end module
"""

ANIMALS = """module animals
  implicit none

  type :: animal
    character(len=8) :: name = "animal"
  contains
    procedure :: speak
    procedure :: intro
  end type

  type, extends(animal) :: dog
    integer :: tricks = 2
  contains
    procedure :: speak => dog_speak
  end type

  type, extends(dog) :: puppy
  contains
    procedure :: speak => puppy_speak
  end type

contains

  subroutine speak(self)
    class(animal), intent(in) :: self
    print *, trim(self%name), " makes a sound"
  end subroutine

  subroutine dog_speak(self)
    class(dog), intent(in) :: self
    print *, trim(self%name), " barks", self%tricks
  end subroutine

  subroutine puppy_speak(self)
    class(puppy), intent(in) :: self
    print *, trim(self%name), " yips"
  end subroutine

  subroutine intro(self)
    class(animal), intent(in) :: self
    print *, "I am ", trim(self%name)
    call self%speak()
  end subroutine
end module
"""


class OOConversionTests(unittest.TestCase):
    def check(self, src, expect_in_text=()):
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        for piece in expect_in_text:
            self.assertIn(piece, text)
        return text

    def test_abstract_type_deferred_and_shared_procedures(self):
        text = self.check(SHAPES + """program demo
  use shapes
  implicit none
  type(circle) :: c
  type(rect) :: r
  c = circle(name="circle", r=2.0)
  r = rect(name="rect", w=3.0, h=4.0)
  call c%describe()
  call r%describe()
  call show(c)
  call show(r)
contains
  subroutine show(s)
    class(shape), intent(in) :: s
    print '(a, f8.3)', "area:", s%area()
  end subroutine
end program
""", ["interface shape_", "record circle is shape_", "subroutine show(shape_ :: s)", "call c.describe()"])
        self.assertEqual(text.count("subroutine describe(self)"), 3)         # the interface lists it; each record has a copy
        self.assertNotIn("circle_area", text)                                 # the implementations moved into the records

    def test_a_normal_type_that_is_extended_and_used_polymorphically(self):
        text = self.check(ANIMALS + """program demo
  use animals
  implicit none
  type(animal) :: a
  type(dog) :: d
  type(puppy) :: p
  d%name = "rex"
  p%name = "bit"
  call meet(a)
  call meet(d)
  call meet(p)
contains
  subroutine meet(x)
    class(animal), intent(in) :: x
    call x%intro()
  end subroutine
end program
""", ["interface animal_any", "record dog is animal_any", "record puppy is animal_any", "subroutine meet(animal_any :: x)"])
        self.assertIn("record animal is animal_any", text)                    # the record keeps the plain name

    def test_an_inherited_procedure_uses_the_childs_override(self):
        # intro is written once for animal; the copy in puppy calls puppy's speak
        self.check(ANIMALS + """program demo
  use animals
  implicit none
  type(puppy) :: p
  p%name = "bit"
  call p%intro()
  call p%speak()
end program
""")

    def test_a_record_variable_can_go_to_an_inout_polymorphic_dummy(self):
        self.check("""module counters
  implicit none
  type :: counter
    integer :: n = 0
  contains
    procedure :: bump
  end type
  type, extends(counter) :: big_counter
    integer :: step = 10
  contains
    procedure :: bump => big_bump
  end type
contains
  subroutine bump(self)
    class(counter), intent(inout) :: self
    self%n = self%n + 1
  end subroutine
  subroutine big_bump(self)
    class(big_counter), intent(inout) :: self
    self%n = self%n + self%step
  end subroutine
  subroutine twice(c)
    class(counter), intent(inout) :: c
    call c%bump()
    call c%bump()
  end subroutine
end module
program demo
  use counters
  implicit none
  type(counter) :: a
  type(big_counter) :: b
  call twice(a)
  call twice(b)
  print *, a%n, b%n
end program
""")

    def test_binding_calls_that_return_values_in_expressions(self):
        self.check(SHAPES + """program demo
  use shapes
  implicit none
  type(circle) :: c
  type(rect) :: r
  real :: total
  c = circle(name="c", r=1.0)
  r = rect(name="r", w=2.0, h=5.0)
  total = c%area() + r%area()
  print '(f8.3)', total
end program
""")


class OOLimitTests(unittest.TestCase):
    def test_an_inherited_procedure_from_another_module_is_a_todo(self):
        src = ("module base_m\n  implicit none\n  type :: base\n    integer :: v = 1\n  contains\n    procedure :: show\n"
               "  end type\ncontains\n  subroutine show(self)\n    class(base), intent(in) :: self\n    print *, self%v\n"
               "  end subroutine\nend module\nmodule child_m\n  use base_m\n  implicit none\n"
               "  type, extends(base) :: child\n    integer :: w = 2\n  end type\nend module\n"
               "program p\n  use child_m\n  implicit none\n  type(child) :: c\n  call c%show()\nend program\n")
        text, findings = converted(src)
        self.assertTrue(any("inherited from another module" in m for _, _, m in findings))

    def test_a_deferred_procedure_nobody_implements_is_a_todo(self):
        src = ("module m\n  implicit none\n  type, abstract :: a\n  contains\n    procedure(f), deferred :: go\n  end type\n"
               "  abstract interface\n    subroutine f(self)\n      import :: a\n      class(a), intent(in) :: self\n"
               "    end subroutine\n  end interface\n  type, extends(a) :: b\n    integer :: v = 1\n  end type\nend module\n"
               "program p\n  use m\n  implicit none\n  type(b) :: x\n  print *, x%v\nend program\n")
        text, findings = converted(src)
        self.assertTrue(any("never implemented" in m for _, _, m in findings))

    def test_a_non_abstract_type_named_like_a_builtin_is_a_todo(self):
        src = ("module m\n  implicit none\n  type :: shape\n    integer :: v = 1\n  end type\nend module\n"
               "program p\n  use m\n  implicit none\n  type(shape) :: x\n  print *, 1\nend program\n")
        text, findings = converted(src)
        self.assertTrue(any("named like a built-in function" in m for _, _, m in findings))


if __name__ == "__main__":
    unittest.main()
