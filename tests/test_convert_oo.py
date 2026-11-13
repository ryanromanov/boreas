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



SHAPES_WITH_GROW = SHAPES.replace("""    procedure :: area => circle_area
  end type""", """    procedure :: area => circle_area
    procedure :: grow
  end type""", 1).replace("""contains

  function circle_area""", """contains

  subroutine grow(self, f)
    class(circle), intent(inout) :: self
    real, intent(in) :: f
    self%r = self%r * f
  end subroutine

  function circle_area""", 1)

WRAPPER = """
module holders
  use shapes
  implicit none
  type :: shape_ref
    class(shape), allocatable :: s
  end type
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


class SelectTypeAndAllocateTests(unittest.TestCase):
    check = OOConversionTests.check

    def test_the_original_sample_with_a_wrapper_select_type_and_allocate(self):
        text = self.check(SHAPES + """
module holders
  use shapes
  implicit none
  public
  type :: shape_ref
    class(shape), allocatable :: s
  end type
end module

program demo
  use shapes
  use holders
  implicit none
  type(shape_ref) :: all(3)
  real :: total
  integer :: i

  allocate(all(1)%s, source=circle(name="circle", r=2.0))
  allocate(all(2)%s, source=rect(name="rect", w=3.0, h=4.0))
  allocate(all(3)%s, source=circle(name="unit", r=1.0))

  total = 0.0
  do i = 1, size(all)
    call all(i)%s%describe()
    total = total + all(i)%s%area()
  end do
  print '(a, f8.3)', "total =", total

  select type (p => all(2)%s)
  type is (rect)
    print '(a, f6.1)', "width of the rect:", p%w
  end select
end program
""", ["shape_[3] :: all", "all[1] = circle(", "select type all[2] as p", "case rect"])
        self.assertNotIn("shape_ref", text)                                    # the wrapper type is gone
        self.assertIn("case default", text)                                    # Fortran has none; Boreas needs one

    def test_a_case_that_changes_the_value_writes_it_back(self):
        self.check(SHAPES_WITH_GROW + """program demo
  use shapes
  implicit none
  class(shape), allocatable :: s
  allocate(s, source=circle(name="c", r=1.0))
  select type (p => s)
  type is (circle)
    call p%grow(3.0)
  end select
  call s%describe()
end program
""", ["call p.grow(3.0)", "s = p"])

    def test_select_type_without_an_associate_name(self):
        self.check(SHAPES + """program demo
  use shapes
  implicit none
  class(shape), allocatable :: s
  allocate(s, source=rect(name="r", w=2.0, h=3.0))
  select type (s)
  type is (circle)
    print *, "circle", s%r
  type is (rect)
    print *, "rect", s%w
  class default
    print *, "other"
  end select
end program
""", ["select type s as s_as"])

    def test_allocate_with_a_type_spec_uses_the_defaults(self):
        self.check(SHAPES + """program demo
  use shapes
  implicit none
  class(shape), allocatable :: s
  allocate(circle :: s)
  call s%describe()
end program
""", ["s = circle()"])

    def test_a_polymorphic_dummy_array_of_wrappers(self):
        self.check(SHAPES + """module holders
  use shapes
  implicit none
  public
  type :: shape_ref
    class(shape), allocatable :: s
  end type
contains
  subroutine total_area(items, total)
    type(shape_ref), intent(in) :: items(:)
    real, intent(out) :: total
    integer :: i
    total = 0.0
    do i = 1, size(items)
      total = total + items(i)%s%area()
    end do
  end subroutine
end module
program demo
  use shapes
  use holders
  implicit none
  type(shape_ref) :: all(2)
  real :: t
  allocate(all(1)%s, source=circle(name="a", r=1.0))
  allocate(all(2)%s, source=rect(name="b", w=2.0, h=2.0))
  call total_area(all, t)
  print '(f8.3)', t
end program
""", ["subroutine total_area(shape_[] :: items, out real32 :: total)"])


class SelectTypeLimitTests(unittest.TestCase):
    def convert(self, body):
        return converted(SHAPES + "program demo\n  use shapes\n  implicit none\n  class(shape), allocatable :: s\n"
                        "  allocate(circle :: s)\n" + body + "end program\n")

    def messages(self, findings):
        return " | ".join(m for _, kind, m in findings if kind == "todo")

    def test_intrinsic_type_guards_are_todos(self):
        _, findings = self.convert("  select type (s)\n  type is (integer)\n    print *, 1\n  end select\n")
        self.assertIn("only derived types are supported", self.messages(findings))

    def test_allocated_of_a_polymorphic_value_is_a_todo(self):
        _, findings = self.convert("  print *, allocated(s)\n")
        self.assertIn("allocated() of a class(T) value", self.messages(findings))

    def test_a_case_that_changes_the_value_and_exits_is_a_todo(self):
        src = SHAPES_WITH_GROW + """program demo
  use shapes
  implicit none
  class(shape), allocatable :: s
  integer :: i
  allocate(circle :: s)
  do i = 1, 2
    select type (p => s)
    type is (circle)
      call p%grow(2.0)
      exit
    end select
  end do
end program
"""
        _, findings = converted(src)
        self.assertIn("leaves early with exit, cycle or return", self.messages(findings))

    def test_allocate_source_of_something_that_is_not_polymorphic_is_a_todo(self):
        src = "program p\n  implicit none\n  integer, allocatable :: a(:)\n  allocate(a, source=[1, 2])\n  print *, 1\nend program\n"
        _, findings = converted(src)
        self.assertIn("'allocate' of something that is not a class(T) value", self.messages(findings))


class OOReviewTests(unittest.TestCase):
    """Cases a reviewer found: nothing may convert quietly into a program that is wrong."""
    check = OOConversionTests.check

    def messages(self, findings):
        return [m for _, kind, m in findings if kind == "todo"]

    def test_a_binding_body_is_checked_for_calls_to_nothing(self):
        src = ("module m\n  implicit none\n  type :: t\n    integer :: n = 1\n  contains\n    procedure :: go\n  end type\n"
               "contains\n  subroutine go(self)\n    class(t), intent(in) :: self\n    call nowhere(self%n)\n  end subroutine\n"
               "end module\nprogram p\n  use m\n  implicit none\n  type(t) :: x\n  call x%go()\nend program\n")
        _, findings = converted(src)
        gone = [m for m in self.messages(findings) if "'nowhere'" in m]
        self.assertEqual(len(gone), 1, self.messages(findings))

    def test_an_implementation_that_is_also_called_by_name_is_a_todo(self):
        src = ANIMALS + """program demo
  use animals
  implicit none
  type(animal) :: a
  call speak(a)
end program
"""
        _, findings = converted(src)
        self.assertTrue(any("is also used by name" in m for m in self.messages(findings)), self.messages(findings))

    def test_an_override_that_calls_the_parents_procedure_by_name_is_a_todo(self):
        src = ANIMALS.replace('print *, trim(self%name), " barks", self%tricks', 'call speak(self)') + """program demo
  use animals
  implicit none
  type(dog) :: d
  call d%speak()
end program
"""
        text, findings = converted(src)
        self.assertTrue(any("is also used by name" in m for m in self.messages(findings)), self.messages(findings))

    def test_no_write_back_into_an_in_parameter(self):
        text = self.check(SHAPES_WITH_GROW + """module users
  use shapes
  implicit none
contains
  subroutine inspect(s)
    class(shape), intent(in) :: s
    select type (s)
    type is (circle)
      call s%describe()
    class default
      print *, "other"
    end select
  end subroutine
end module
program demo
  use shapes
  use users
  implicit none
  type(circle) :: c
  c = circle(name="c", r=2.0)
  call inspect(c)
end program
""")
        self.assertNotIn("s = s_as", text)

    def test_write_back_into_an_inout_parameter_is_an_update(self):
        text = self.check(SHAPES_WITH_GROW + """module users
  use shapes
  implicit none
contains
  subroutine enlarge(s)
    class(shape), intent(inout) :: s
    select type (s)
    type is (circle)
      call s%grow(2.0)
    end select
  end subroutine
end module
program demo
  use shapes
  use users
  implicit none
  type(circle) :: c
  c = circle(name="c", r=2.0)
  call enlarge(c)
  call c%describe()
end program
""")
        self.assertIn("update s = s_as", text)

    def test_an_internal_procedure_keeps_a_local_that_hides_a_host_import(self):
        self.check("""module m
  implicit none
  integer, parameter :: n = 3
  integer, parameter :: k = 4
end module
program p
  use m, only: n, k
  implicit none
  call s()
contains
  subroutine s()
    integer :: n
    n = 7
    print *, n, k
  end subroutine
end program
""")

    def test_an_internal_procedure_imports_only_what_it_uses(self):
        text = self.check("""module m
  implicit none
  integer, parameter :: n = 3
  integer, parameter :: k = 4
end module
program p
  use m, only: n, k
  implicit none
  call s()
  print *, n
contains
  subroutine s()
    print *, k
  end subroutine
end program
""")
        self.assertIn("use m: k\n", text)

    def test_a_function_binding_that_became_a_subroutine_is_a_todo(self):
        src = ("module m\n  implicit none\n  type :: counter\n    integer :: n = 0\n  contains\n    procedure :: next\n  end type\n"
               "contains\n  function next(self) result(r)\n    class(counter), intent(inout) :: self\n    integer :: r\n"
               "    self%n = self%n + 1\n    r = self%n\n  end function\nend module\n"
               "program p\n  use m\n  implicit none\n  type(counter) :: c\n  print *, c%next()\nend program\n")
        _, findings = converted(src)
        self.assertTrue(any("it became a subroutine" in m for m in self.messages(findings)), self.messages(findings))


class OOLimitTests(unittest.TestCase):
    def parts(self, helper="", use_in_show=""):
        return ("module base_m\n  implicit none\n  private\n  public :: base\n" + helper +
                "  type :: base\n    integer :: v = 1\n  contains\n    procedure :: show => base_show\n  end type\n"
                "contains\n  subroutine base_show(self)\n    class(base), intent(in) :: self\n" + use_in_show +
                "    print *, self%v\n  end subroutine\nend module\n"
                "module child_m\n  use base_m\n  implicit none\n"
                "  type, extends(base) :: child\n    integer :: w = 2\n  end type\nend module\n"
                "program p\n  use child_m\n  implicit none\n  type(child) :: c\n  call c%show()\nend program\n")

    def test_an_inherited_procedure_from_another_module_is_copied(self):
        text = OOConversionTests.check(self, self.parts())
        self.assertIn("record child", text)

    def test_an_inherited_procedure_may_use_the_parents_public_names(self):
        src = self.parts("  integer, parameter, public :: scale = 10\n", "    print *, scale * self%v\n")
        text = OOConversionTests.check(self, src)
        self.assertIn("use base_m: scale", text.split("module child_m")[1])

    def test_an_inherited_procedure_that_uses_a_private_name_is_a_todo(self):
        src = self.parts("  integer, parameter :: width = 8\n", "    print *, width\n")
        _, findings = converted(src)
        said = " | ".join(m for _, kind, m in findings if kind == "todo")
        self.assertIn("uses 'width', private to module 'base_m'", said)

    def test_an_inherited_procedure_that_writes_a_module_variable_of_the_parent(self):
        src = self.parts("  integer, public :: calls = 0\n", "    calls = calls + 1\n").replace(
            "  call c%show()\n", "  call c%show()\n  call c%show()\n  print *, calls\n").replace(
            "use child_m\n  implicit none\n  type(child)", "use child_m\n  use base_m, only: calls\n  implicit none\n  type(child)")
        OOConversionTests.check(self, src)               # both copies update the one shared `calls`

    def test_an_inherited_procedure_whose_name_clashes_is_a_todo(self):
        src = self.parts("  integer, parameter, public :: scale = 10\n", "    print *, scale * self%v\n") \
            .replace("module child_m\n  use base_m\n", "module child_m\n  use base_m, only: base\n") \
            .replace("  type, extends(base) :: child", "  integer, parameter :: scale = 3\n  type, extends(base) :: child")
        _, findings = converted(src)
        said = " | ".join(m for _, kind, m in findings if kind == "todo")
        self.assertIn("a name this module already has for something else", said)

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


MEET = """program demo
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
    select type (x)
BODY    end select
  end subroutine
end program
"""


class ClassIsGuardTests(unittest.TestCase):
    """`class is (T)` matches T and everything that extends it."""
    check = OOConversionTests.check

    def test_a_guard_that_only_calls_functions_is_a_case_on_the_interface(self):
        text = self.check(ANIMALS + MEET.replace("BODY", """    type is (puppy)
      print *, "a puppy"
    class is (animal)
      call x%speak()
    class default
      print *, "other"
"""), ["case animal_any", "case puppy"])
        self.assertNotIn("case dog", text)

    def test_a_guard_that_reads_fields_is_written_out_per_type(self):
        text = self.check(ANIMALS + MEET.replace("BODY", """    type is (puppy)
      print *, "a puppy"
    class is (dog)
      print *, "a dog with", x%tricks
    class default
      print *, "other"
"""), ["case dog", "case puppy"])
        self.assertNotIn("case dog_any", text)
        self.assertEqual(text.count("with"), 1)                     # puppy has its own guard, so only dog gets the body

    def test_an_interface_that_an_unrelated_type_also_fits_is_written_out(self):
        # a plain `animal` has every function `dog` lists, so `case dog_any` would wrongly match it
        self.check(ANIMALS + MEET.replace("BODY", """    class is (dog)
      call x%speak()
    class default
      print *, "other"
"""), ["case dog", "case puppy"])

    def test_the_more_specific_guard_wins(self):
        self.check(ANIMALS + MEET.replace("BODY", """    class is (animal)
      print *, "an animal"
    class is (dog)
      print *, "a dog", x%tricks
    class default
      print *, "other"
"""))

    def test_a_guard_that_changes_the_value_writes_it_back(self):
        src = SHAPES_WITH_GROW + """module users
  use shapes
  implicit none
contains
  subroutine enlarge(s)
    class(shape), intent(inout) :: s
    select type (s)
    class is (circle)
      call s%grow(2.0)
    end select
  end subroutine
end module
program demo
  use shapes
  use users
  implicit none
  type(circle) :: c
  c = circle(name="c", r=2.0)
  call enlarge(c)
  call c%describe()
end program
"""
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            self.assertTrue(same_output("\n".join(got), fortran_output(src)), got)


KENNEL = """module kennel
  implicit none
  type :: animal
    integer :: legs = 4
  contains
    procedure :: speak
  end type
  type, extends(animal) :: dog
  contains
    procedure :: speak => dog_speak
    procedure :: fetch
  end type
  type, extends(dog) :: puppy
  end type
contains
  subroutine speak(self)
    class(animal), intent(in) :: self
    print *, "animal speaks"
  end subroutine
  subroutine dog_speak(self)
    class(dog), intent(in) :: self
    print *, "dog speaks"
  end subroutine
  subroutine fetch(self)
    class(dog), intent(in) :: self
    print *, "fetching"
  end subroutine
  subroutine walk(d)
    class(dog), intent(in) :: d
    call d%speak()
  end subroutine
end module
"""


class ClassIsReviewTests(unittest.TestCase):
    check = OOConversionTests.check

    def test_the_more_specific_interface_guard_is_tried_first(self):
        text = self.check(KENNEL + """program demo
  use kennel
  implicit none
  type(animal) :: a
  type(dog) :: d
  type(puppy) :: p
  call meet(a)
  call meet(d)
  call meet(p)
  call walk(p)
contains
  subroutine meet(x)
    class(animal), intent(in) :: x
    select type (x)
    class is (animal)
      call x%speak()
    class is (dog)
      call x%fetch()
    end select
  end subroutine
end program
""", ["case dog_any", "case animal_any"])
        self.assertLess(text.index("case dog_any"), text.index("case animal_any"))

    def test_a_guard_inside_a_guard_is_settled_too(self):
        self.check(ANIMALS + MEET.replace("BODY", """    class is (dog)
      select type (x)
      class is (puppy)
        print *, "puppy", x%tricks
      class default
        print *, "dog", x%tricks
      end select
    class default
      print *, "other"
"""))

    def test_a_type_the_code_cannot_see_is_flagged_not_dropped(self):
        src = """module base
  implicit none
  type :: animal
    integer :: legs = 4
  contains
    procedure :: speak
  end type
contains
  subroutine speak(self)
    class(animal), intent(in) :: self
    print *, "animal"
  end subroutine
end module
module kinds
  use base
  implicit none
  type, extends(animal) :: bird
  contains
    procedure :: speak => bird_speak
  end type
contains
  subroutine bird_speak(self)
    class(bird), intent(in) :: self
    print *, "bird"
  end subroutine
end module
module users
  use base
  implicit none
contains
  subroutine count_legs(x)
    class(animal), intent(in) :: x
    select type (x)
    class is (animal)
      print *, x%legs
    end select
  end subroutine
end module
program demo
  use base
  use kinds
  use users
  implicit none
  type(bird) :: b
  call count_legs(b)
end program
"""
        text, findings = converted(src)
        said = [m for _, kind, m in findings if kind == "todo"]
        self.assertEqual(len(said), 1, said)
        self.assertIn("also matches bird", said[0])
        self.assertIn("TODO(convert): 'class is (animal)' also matches bird", text)
        run_bor(text)                                  # what is left still runs


COUNTER = """module counters
  implicit none
  type :: counter
    integer :: n = 0
  contains
    procedure, pass(c) :: add
    procedure, pass(c) :: show
  end type
  type, extends(counter) :: double_counter
  contains
    procedure, pass(c) :: add => double_add
  end type
contains
  subroutine add(amount, c)
    integer, intent(in) :: amount
    class(counter), intent(inout) :: c
    c%n = c%n + amount
  end subroutine
  subroutine double_add(amount, c)
    integer, intent(in) :: amount
    class(double_counter), intent(inout) :: c
    c%n = c%n + 2 * amount
  end subroutine
  function show(label, c) result(r)
    character(len=*), intent(in) :: label
    class(counter), intent(in) :: c
    integer :: r
    print *, label, c%n
    r = c%n
  end function
end module
"""


class PassArgTests(unittest.TestCase):
    check = OOConversionTests.check

    def test_the_object_goes_first_as_self_and_calls_are_unchanged(self):
        text = self.check(COUNTER + """program demo
  use counters
  implicit none
  type(counter) :: x
  type(double_counter) :: y
  integer :: k
  call x%add(5)
  call x%add(2)
  call y%add(5)
  k = x%show("x is")
  k = y%show("y is")
  print *, k
end program
""", ["subroutine add(inout self, integer :: amount)", "call x.add(5)"])
        self.assertNotIn("subroutine add(integer", text)

    def test_polymorphic_calls_dispatch_through_the_override(self):
        self.check(COUNTER + """program demo
  use counters
  implicit none
  type(double_counter) :: y
  call bump(y)
  print *, y%n
contains
  subroutine bump(z)
    class(counter), intent(inout) :: z
    call z%add(4)
  end subroutine
end program
""")

    def test_a_pass_argument_the_procedure_does_not_have_is_a_todo(self):
        src = COUNTER.replace("procedure, pass(c) :: show", "procedure, pass(nobody) :: show") + """program demo
  use counters
  implicit none
  type(counter) :: x
  call x%add(1)
  print *, x%n
end program
"""
        _, findings = converted(src)
        said = " | ".join(m for _, kind, m in findings if kind == "todo")
        self.assertIn("says pass(nobody), but 'show' has no argument of that name", said)


SCENE_TYPES = """  type :: slot
    class(shape), allocatable :: s
  end type
  type :: scene
    type(slot) :: items(2)
    integer :: count = 0
  end type
  type :: pet_owner
    integer :: age = 30
    class(shape), allocatable :: favourite
    character(len=8) :: name = "pat"
  end type
"""


class InterfaceFieldConversionTests(unittest.TestCase):
    """A type that holds a class(T) value (directly or through a wrapper) next to other components."""
    check = OOConversionTests.check

    def module(self):
        base = SHAPES[:SHAPES.rindex("end module")]
        base = base.replace("public :: shape, circle, rect", "public :: shape, circle, rect, slot, scene, pet_owner")
        k = base.index("\ncontains")
        return base[:k] + "\n" + SCENE_TYPES + base[k:] + "end module\n"

    def test_an_array_of_a_wrapper_inside_another_type(self):
        text = self.check(self.module() + """program demo
  use shapes
  implicit none
  type(scene) :: sc
  integer :: i
  allocate(sc%items(1)%s, source=circle(name="c", r=1.0))
  allocate(sc%items(2)%s, source=rect(name="r", w=2.0, h=3.0))
  sc%count = 2
  do i = 1, sc%count
    call sc%items(i)%s%describe()
  end do
end program
""", ["shape_[2] :: items", "sc.items[1] = circle(", "call sc.items[i].describe()"])
        self.assertNotIn("slot", text)

    def test_a_polymorphic_component_next_to_other_components(self):
        text = self.check(self.module() + """program demo
  use shapes
  implicit none
  type(pet_owner) :: o
  o%age = 41
  allocate(o%favourite, source=circle(name="round", r=2.0))
  call o%favourite%describe()
  print *, o%age, o%name
end program
""", ["shape_ :: favourite", "integer :: age = 30"])
        record = text[text.index("record pet_owner"):]
        self.assertLess(record.index("integer :: age"), record.index("shape_ :: favourite"))   # declaration order kept
        self.assertLess(record.index("shape_ :: favourite"), record.index("string :: name"))

    def test_select_type_on_a_component(self):
        self.check(self.module() + """program demo
  use shapes
  implicit none
  type(scene) :: sc
  allocate(sc%items(1)%s, source=circle(name="c", r=2.0))
  select type (p => sc%items(1)%s)
  type is (circle)
    print *, "radius", p%r
  class default
    print *, "other"
  end select
end program
""")

    def test_a_normal_type_used_only_as_a_component(self):
        self.check(ANIMALS.replace("end module", "") .replace("contains\n\n  subroutine speak", "  type :: keeper\n    class(animal), allocatable :: pet\n    integer :: n = 1\n  end type\ncontains\n\n  subroutine speak", 1) + "end module\n" + """program demo
  use animals
  implicit none
  type(keeper) :: k
  allocate(k%pet, source=dog(name="rex", tricks=3))
  call k%pet%speak()
  print *, k%n
end program
""")

    def test_allocated_of_a_component_is_a_todo(self):
        src = self.module() + """program demo
  use shapes
  implicit none
  type(pet_owner) :: o
  print *, allocated(o%favourite)
end program
"""
        _, findings = converted(src)
        said = " | ".join(m for _, kind, m in findings if kind == "todo")
        self.assertIn("allocated() of a class(T) value", said)
