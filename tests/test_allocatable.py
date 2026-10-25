import os, subprocess, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_boreas import run
from test_fortran import (GFORTRAN, assert_round_trips, conv, fixture, read, run_bor, run_gfortran,
                          same_output, todos)
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse, Decl, Allocate, Deallocate
from boreas.printer import to_source


class ParseTests(unittest.TestCase):
    def test_declaration_ranks(self):
        unit = parse("program t\nallocatable integer :: a(:)\nallocatable real :: m(:, :), v(:)\nend program t")
        a, m, v = unit.program.body[0], *unit.program.body[1]
        self.assertTrue(a.allocatable and len(a.dims) == 1)
        self.assertTrue(m.allocatable and len(m.dims) == 2)
        self.assertEqual(len(v.dims), 1)

    def test_statements_parse(self):
        unit = parse("program t\nallocatable integer :: a(:, :)\nallocate a(2, 3)\ndeallocate a\nend program t")
        _, al, de = unit.program.body
        self.assertEqual((al.name, len(al.dims)), ("a", 2))
        self.assertIsInstance(de, Deallocate)

    def test_plain_colon_dims_stay_errors(self):
        with self.assertRaises(LangError):
            parse("program t\ninteger :: a(:)\nend program t")

    def test_allocatable_declaration_errors(self):
        for body, msg in [("allocatable integer :: a(3)", "':' for each dimension"),
                          ("allocatable integer :: a", "needs one ':'"),
                          ("allocatable integer :: a(:) = [1]", "initializer"),
                          ("const allocatable integer :: a(:)", "cannot be 'const'"),
                          ("allocatable const integer :: a(:)", "cannot be 'const'"),
                          ("allocatable integer :: a(1:)", "':' for each dimension")]:
            with self.subTest(body=body), self.assertRaisesRegex(LangError, msg):
                parse(f"program t\n{body}\nend program t")

    def test_module_cannot_hold_allocatables(self):
        with self.assertRaisesRegex(LangError, "module cannot hold allocatable"):
            parse("module m\nallocatable integer :: a(:)\nend module\nprogram t\nend program t")

    def test_allocate_target_errors_at_parse_time(self):
        cases = [
            ("integer :: a(3)\nallocate a(3)", "not allocatable"),
            ("integer :: x\nallocate x(3)", "not allocatable"),
            ("const integer :: c(2) = 1\nallocate c(2)", "a constant"),
            ("allocate q(3)", "not declared"),
            ("allocatable integer :: a(:, :)\nallocate a(3)", "2 dimension"),
            ("integer :: a(3)\ndeallocate a", "not allocatable"),
        ]
        for body, msg in cases:
            with self.subTest(body=body), self.assertRaisesRegex(LangError, msg):
                parse(f"program t\n{body}\nend program t")

    def test_parameter_cannot_be_allocated(self):
        for intent in ("", "inout "):
            with self.subTest(intent=intent), self.assertRaisesRegex(LangError, "is a parameter"):
                parse(f"subroutine s({intent}integer :: v)\nallocate v(3)\nend subroutine\nprogram t\nend program t")
        with self.assertRaisesRegex(LangError, "is a parameter"):
            parse("subroutine s(integer :: v)\ndeallocate v\nend subroutine\nprogram t\nend program t")

    def test_checks_descend_into_blocks(self):
        with self.assertRaisesRegex(LangError, "not allocatable"):
            parse("program t\ninteger :: a(2)\nif true then\ndo while false\nallocate a(2)\nend do\nend if\nend program t")

    def test_names_are_not_reserved_in_expressions(self):
        self.assertEqual(run("integer :: allocate = 3\nprint allocate + 1"), ["4"])


class RunTests(unittest.TestCase):
    def test_allocate_use_deallocate(self):
        out = run("""allocatable integer :: a(:)
print allocated(a)
allocate a(3)
print allocated(a), size(a), a
a(2) = 5
a = a + 1
print a
deallocate a
print allocated(a)""")
        self.assertEqual(out, ["false", "true 3 [0, 0, 0]", "[1, 6, 1]", "false"])

    def test_defaults_per_type(self):
        out = run("""allocatable real :: r(:)
allocatable logical :: b(:)
allocatable string :: s(:)
allocate r(1)
allocate b(1)
allocate s(1)
print r, b, '[' + s(1) + ']'""".replace("'", '"'))
        self.assertEqual(out, ["[0.0] [false] []"])

    def test_extents_are_expressions(self):
        out = run("integer :: n = 2\nallocatable integer :: a(:)\nallocate a(n * 3 - 1)\nprint size(a)")
        self.assertEqual(out, ["5"])

    def test_zero_size(self):
        out = run("allocatable integer :: a(:)\nallocate a(0)\nprint allocated(a), size(a), sum(a)\ndeallocate a")
        self.assertEqual(out, ["true 0 0"])

    def test_two_dimensional_and_shape(self):
        out = run("""allocatable integer :: m(:, :)
integer :: i, j
allocate m(2, 3)
do j = 1, 3
  do i = 1, 2
    m(i, j) = 10 * i + j
  end do
end do
print m
print shape(m), size(m, 1), size(m, 2), m(2, 3)
print transpose(m)""")
        self.assertEqual(out, ["[[11, 12, 13], [21, 22, 23]]", "[2, 3] 2 3 23",
                               "[[11, 21], [12, 22], [13, 23]]"])

    def test_slices_of_allocated_arrays(self):
        out = run("""allocatable integer :: a(:)
allocatable integer :: m(:, :)
allocate a(5)
a(2:4) = [7, 8, 9]
print a(2:), a(:2), a(3)
allocate m(2, 2)
m(:, 1) = [1, 2]
m(1, :) = 5
print m""")
        self.assertEqual(out, ["[7, 8, 9, 0] [0, 7] 8", "[[5, 5], [2, 0]]"])

    def test_deallocate_and_reallocate_different_size(self):
        out = run("""allocatable integer :: a(:)
allocate a(2)
a = 9
deallocate a
allocate a(4)
print a""")
        self.assertEqual(out, ["[0, 0, 0, 0]"])

    def test_allocated_in_a_condition_and_loop(self):
        out = run("""allocatable integer :: a(:)
integer :: i
do i = 1, 3
  if not allocated(a) then
    allocate a(i)
  end if
end do
print size(a)""")
        self.assertEqual(out, ["1"])

    def test_pass_to_procedures(self):
        funcs = """subroutine fill(inout integer :: v)
  integer :: i
  do i = 1, size(v)
    update v(i) = i * i
  end do
end subroutine
function total(integer :: v) -> integer
  return sum(v)
end function"""
        out = run("allocatable integer :: a(:)\nallocate a(4)\ncall fill(a)\nprint a, total(a), total(a(2:3))", funcs)
        self.assertEqual(out, ["[1, 4, 9, 16] 30 13"])

    def test_locals_in_procedures_are_per_call(self):
        funcs = """function build(integer :: n) -> integer
  allocatable integer :: t(:)
  allocate t(n)
  t = n
  return sum(t)
end function"""
        self.assertEqual(run("print build(3), build(4)", funcs), ["9 16"])

    def test_leftover_allocation_is_freed_silently(self):
        funcs = "subroutine s()\n  allocatable integer :: t(:)\n  allocate t(2)\nend subroutine"
        self.assertEqual(run("call s()\ncall s()\nprint 1", funcs), ["1"])

    def test_allocated_inside_a_procedure_sees_its_own_locals(self):
        funcs = """function f() -> logical
  allocatable integer :: t(:)
  allocate t(1)
  return allocated(t)
end function"""
        self.assertEqual(run("allocatable integer :: t(:)\nprint f(), allocated(t)", funcs), ["true false"])

    def test_user_function_named_allocated_wins(self):
        funcs = "function allocated(integer :: x) -> integer\n  return x + 1\nend function"
        self.assertEqual(run("print allocated(1)", funcs), ["2"])


class RuntimeErrorTests(unittest.TestCase):
    DECL = "allocatable integer :: a(:)\n"

    def fails(self, body, msg, funcs=""):
        with self.assertRaisesRegex(LangError, msg):
            run(self.DECL + body, funcs)

    def test_every_use_of_an_unallocated_array(self):
        funcs = ("subroutine bump(inout integer :: v)\n  update v(1) = 1\nend subroutine\n"
                 "function first(integer :: v) -> integer\n  return v(1)\nend function")
        cases = [
            "print a", "print a(1)", "print a(1:2)", "a(1) = 1", "a(1:2) = 1", "a = 1", "a = [1, 2, 3]",
            "integer :: b(3)\nb = a", "print size(a)", "print shape(a)", "print sum(a)", "print a + 1",
            "call bump(a)", "print first(a)", "integer :: n\nn = size(a, 1)",
        ]
        for body in cases:
            with self.subTest(body=body):
                self.fails(body, "'a' is not allocated", funcs)

    def test_allocate_twice(self):
        self.fails("allocate a(2)\nallocate a(3)", "already allocated")

    def test_deallocate_unallocated(self):
        self.fails("deallocate a", "not allocated")
        self.fails("allocate a(1)\ndeallocate a\ndeallocate a", "not allocated")

    def test_use_after_deallocate(self):
        self.fails("allocate a(1)\ndeallocate a\nprint a(1)", "not allocated")

    def test_negative_and_non_integer_extents(self):
        self.fails("allocate a(-1)", "non-negative integers")
        self.fails("allocate a(2.5)", "non-negative integers")
        self.fails("allocate a(true)", "non-negative integers")

    def test_extent_must_be_assigned(self):
        self.fails("integer :: n\nallocate a(n)", "used before it is assigned")

    def test_bounds_after_allocation(self):
        self.fails("allocate a(2)\nprint a(3)", "out of bounds")

    def test_shape_mismatch_on_whole_assignment(self):
        self.fails("allocate a(2)\na = [1, 2, 3]", "shape mismatch")

    def test_allocated_needs_an_allocatable_name(self):
        self.fails("integer :: b(2)\nprint allocated(b)", "not an allocatable")
        self.fails("print allocated(a(1))", "needs the name")
        self.fails("print allocated(3)", "needs the name")
        self.fails("print allocated(zzz)", "not declared")
        self.fails("print allocated()", "needs the name")

    def test_unallocated_does_not_leak_through_arguments_of_allocated(self):
        self.fails("print allocated(a), a", "not allocated")

    def test_callee_cannot_allocate_callers_array(self):
        # the callee's parameter is not allocatable, so this is rejected when the file is parsed
        with self.assertRaisesRegex(LangError, "is a parameter"):
            run(self.DECL + "call s(a)", "subroutine s(inout integer :: v)\n  allocate v(3)\nend subroutine")

    def test_update_rules_still_apply(self):
        funcs = "subroutine s(inout integer :: v)\n  v(1) = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "update"):
            run(self.DECL + "allocate a(2)\ncall s(a)", funcs)
        with self.assertRaisesRegex(LangError, "'update' is for out and inout"):
            parse("program t\nallocatable integer :: a(:)\nallocate a(2)\nupdate a(1) = 1\nend program t")

    def test_in_parameter_receiving_allocatable_is_read_only(self):
        funcs = "subroutine s(integer :: v)\n  v(1) = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "'in' parameter"):
            run(self.DECL + "allocate a(2)\ncall s(a)", funcs)


class PrinterTests(unittest.TestCase):
    SRC = """subroutine fill(inout integer :: v)
  update v = 1
end subroutine

program t
  allocatable integer :: a(:), b(:, :)
  allocatable real :: r(:)
  integer :: n
  n = 3
  allocate a(n + 1)
  allocate b(n, 2 * n)
  allocate r(0)
  if allocated(a) then
    deallocate a
  end if
  print allocated(b)
end program t
"""

    def test_round_trip_is_stable(self):
        once = to_source(parse(self.SRC))
        self.assertEqual(to_source(parse(once)), once)
        self.assertIn("  allocatable integer :: a(:), b(:, :)\n", once)
        self.assertIn("  allocatable real :: r(:)\n", once)
        self.assertIn("  integer :: n\n", once)           # plain declarations are not merged with these
        self.assertIn("  allocate b(n, 2 * n)\n", once)
        self.assertIn("    deallocate a\n", once)

    def test_printed_program_runs(self):
        out = []
        from boreas.interp import Interp
        Interp(parse(to_source(parse(self.SRC.replace("update v = 1", "update v(1) = 1")))), out.append).run()
        self.assertEqual(out, ["true"])


class ConvertTests(unittest.TestCase):
    def test_declaration_forms(self):
        text, findings = conv("", "  real, allocatable :: a(:)\n  integer, allocatable, dimension(:,:) :: m\n"
                                  "  real, allocatable :: x(:), y(:, :, :)")
        self.assertEqual(todos(findings), [])
        self.assertIn("allocatable real :: a(:)", text)
        self.assertIn("allocatable integer :: m(:, :)", text)
        self.assertIn("x(:)", text)
        self.assertIn("y(:, :, :)", text)

    def test_allocate_forms(self):
        text, findings = conv("  n = 3\n  allocate(a(n))\n  allocate(m(n, 1:n + 1))\n  allocate(a(1:n))",
                              "  integer :: n\n  integer, allocatable :: a(:), m(:, :)")
        self.assertEqual(todos(findings), [])
        self.assertIn("allocate a(n)\n", text)
        self.assertIn("allocate m(n, n + 1)\n", text)
        self.assertEqual(text.count("allocate a(n)"), 2)

    def test_several_objects_become_several_statements(self):
        text, findings = conv("  allocate(a(2), m(2, 3))\n  deallocate(a, m)",
                              "  integer, allocatable :: a(:), m(:, :)")
        self.assertEqual(todos(findings), [])
        for line in ("allocate a(2)\n", "allocate m(2, 3)\n", "deallocate a\n", "deallocate m\n"):
            self.assertIn("  " + line, text)

    def test_allocated_intrinsic(self):
        text, findings = conv("  allocate(a(2))\n  print *, allocated(a)", "  integer, allocatable :: a(:)")
        self.assertEqual(todos(findings), [])
        self.assertIn("print allocated(a)", text)

    def test_unsupported_allocate_options_become_todos(self):
        for stmt in ("allocate(a(2), stat=ierr)", "allocate(a(2), errmsg=msg)", "allocate(a, source=b)",
                     "allocate(a, mold=b)", "allocate(a(0:2))", "allocate(a(2), b(0:3))",
                     "deallocate(a, stat=ierr)"):
            with self.subTest(stmt=stmt):
                text, findings = conv(f"  {stmt}",
                                      "  integer :: ierr\n  character(len=20) :: msg\n"
                                      "  integer, allocatable :: a(:), b(:)")
                self.assertEqual(len(todos(findings)), 1)
                self.assertIn("TODO(convert)", text)
                self.assertIn(stmt, text)
                parse(text)

    def test_allocate_of_a_non_allocatable_is_a_todo(self):
        text, findings = conv("  allocate(a(2))", "  integer :: n\n  integer, pointer :: a(:)")
        self.assertTrue(todos(findings))
        self.assertIn("TODO(convert)", text)
        self.assertNotIn("allocate a(2)", text)

    def test_allocatable_dummy_argument_is_a_todo_with_a_reason(self):
        src = """subroutine grow(a)
  implicit none
  real, allocatable, intent(inout) :: a(:)
  integer :: n
  n = size(a)
  a(1) = 2.0
end subroutine grow

program p
  implicit none
  real, allocatable :: x(:)
  allocate(x(2))
  call grow(x)
end program p
"""
        text, findings = convert(src)
        reasons = [f[2] for f in todos(findings)]
        self.assertTrue(any("allocatable dummy argument 'a'" in r and "cannot be allocatable" in r for r in reasons))
        self.assertIn("TODO(convert): uses 'a'", text)      # the body that touches it is not translated
        self.assertNotIn("update a(1)", text)

    def test_allocatable_module_variable_is_a_todo_and_uses_follow(self):
        src = """module store
  implicit none
  real, allocatable :: buf(:)
contains
  subroutine put(x)
    real, intent(in) :: x
    buf(1) = x
  end subroutine put
end module store
"""
        text, findings = convert(src + "program p\n  use store\n  call put(1.0)\nend program p\n")
        self.assertTrue(any("allocatable module variable 'buf'" in f[2] for f in todos(findings)))
        self.assertIn("TODO(convert): uses 'buf'", text)

    def test_allocatable_function_result_is_a_todo(self):
        src = """function make(n)
  implicit none
  integer, intent(in) :: n
  integer, allocatable :: make(:)
  allocate(make(n))
  make = 1
end function make
program p
  print *, 1
end program p
"""
        _, findings = convert(src)
        self.assertTrue(any("returns an array" in f[2] for f in todos(findings)))

    def test_allocatable_scalars_components_and_pointers_stay_todos(self):
        for decl in ("integer, allocatable :: s", "integer, pointer :: p(:)", "integer, allocatable :: w(3)"):
            with self.subTest(decl=decl):
                text, findings = conv("  print *, 1", "  " + decl)
                self.assertEqual(len(todos(findings)), 1)
                self.assertIn("TODO(convert)", text)

    def test_skipped_allocatable_uses_become_todos(self):
        text, findings = conv("  s = 1\n  print *, s", "  integer, allocatable :: s")
        self.assertGreaterEqual(len(todos(findings)), 2)

    def test_auto_allocation_on_assignment_is_noted(self):
        _, findings = conv("  b = [1, 2, 3]\n  a = b", "  integer, allocatable :: a(:), b(:)")
        notes = [f[2] for f in findings if f[1] == "note" and "automatic allocation" in f[2]]
        self.assertEqual(len(notes), 2)
        self.assertIn("assignment to allocatable 'a' relied on automatic allocation in Fortran; "
                      "Boreas requires an explicit allocate", notes)

    def test_no_note_after_an_allocate(self):
        _, findings = conv("  allocate(a(3))\n  a = 1", "  integer, allocatable :: a(:)")
        self.assertFalse([f for f in findings if "automatic allocation" in f[2]])

    def test_note_returns_after_deallocate_and_for_elements_not_at_all(self):
        _, findings = conv("  allocate(a(3))\n  a(1) = 1\n  deallocate(a)\n  a = 2",
                           "  integer, allocatable :: a(:)")
        self.assertEqual(len([f for f in findings if "automatic allocation" in f[2]]), 1)

    def test_keywords_are_renamed(self):
        text, findings = conv("  allocate = 1\n  print *, allocate", "  integer :: allocate")
        self.assertIn("allocate_", text)
        parse(text)

    def test_allocated_is_not_renamed_and_is_known(self):
        text, findings = conv("  allocate(a(1))\n  print *, allocated(a)", "  integer, allocatable :: a(:)")
        self.assertEqual(todos(findings), [])
        self.assertNotIn("allocated_", text)

    def test_intent_inference_reads_allocate_extents(self):
        src = """subroutine s(n)
  implicit none
  integer :: n
  integer, allocatable :: a(:)
  allocate(a(n))
  n = 0
end subroutine s
program p
  integer :: k
  k = 3
  call s(k)
end program p
"""
        text, _ = convert(src)
        self.assertIn("inout integer :: n", text)           # n is read (by allocate) before it is written


class FixtureTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = fixture("allocatable.f90")
        if GFORTRAN:
            subprocess.run([GFORTRAN, "-fsyntax-only", "-std=f2018", path], check=True, capture_output=True)
        text, findings = convert(read(path), "allocatable.f90")
        self.assertEqual(todos(findings), [])
        assert_round_trips(read(path))
        parse(text)
        got = run_bor(text)
        if GFORTRAN:
            want = run_gfortran(path)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        self.assertEqual(got[0], "false")
        self.assertEqual(got[1], "true 5")
        self.assertEqual(got[-1], "1 0")


if __name__ == "__main__":
    unittest.main()
