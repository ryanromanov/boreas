import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, fixture, read, run_bor, run_gfortran, todos
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output

STORE = """module store
  public allocatable integer[] :: data
  public subroutine setup(integer :: k)
    integer :: i
    allocate @data[k]
    do i = 1, k
      @data[i] = i * i
    end do
  end subroutine
  public function total() -> integer
    return sum(data)
  end function
  public subroutine teardown()
    deallocate @data
  end subroutine
end module
"""


def run(src):
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        Interp(parse(src)).run()
    return out.getvalue().split()


def program(body):
    return STORE + "program p\n  use store: setup, total, teardown, data\n" + body + "\nend program p\n"


class AllocatableModuleVariableTests(unittest.TestCase):
    def test_allocated_in_one_procedure_and_used_in_another(self):
        self.assertEqual(run(program("call setup(4)\nprintln(total())\n@data[2] = 100\nprintln(total(), size(data))")),
                         ["30", "126", "4"])

    def test_the_program_can_allocate_an_imported_array(self):
        self.assertEqual(run(program("allocate @data[2]\nprintln(data)\ndeallocate @data\nprintln(allocated(data))")),
                         ["[0,", "0]", "false"])

    def test_allocate_without_update_is_an_error(self):
        with self.assertRaisesRegex(LangError, "module variable.*write 'allocate @data\\[...\\]'"):
            parse(program("allocate data[3]"))
        with self.assertRaisesRegex(LangError, "write 'deallocate @data'"):
            parse(program("deallocate data"))

    def test_update_on_a_local_array_is_an_error(self):
        with self.assertRaisesRegex(LangError, "'@' is for module variables, but 'a' is a local array"):
            parse("program p\nallocatable integer[] :: a\nallocate @a[3]\nend program p")

    def test_a_function_cannot_allocate_a_module_variable(self):
        src = STORE.replace("return sum(data)", "allocate @data[1]\n    return 0")
        with self.assertRaisesRegex(LangError, "a function cannot allocate the module variable 'data'"):
            parse(src + "program p\nend program p")

    def test_extent_count_and_allocatable_checks(self):
        with self.assertRaisesRegex(LangError, "1 dimension.*2 extent"):     # an import is only checked when it runs
            run(program("allocate @data[2, 2]"))
        own = STORE.replace("deallocate @data", "allocate @data[1, 1]")
        with self.assertRaisesRegex(LangError, "1 dimension.*2 extent"):
            parse(own + "program p\nend program p")
        src = ("module m\n  public integer :: n\n  public subroutine s()\n    allocate @n[2]\n  end subroutine\n"
               "end module\nprogram p\nend program p")
        with self.assertRaisesRegex(LangError, "not allocatable"):
            parse(src)
        with self.assertRaisesRegex(LangError, "not an allocatable array"):          # an import: found when it runs
            run("module m\n  public integer :: n\nend module\nprogram p\n  use m: n\n  allocate @n[2]\nend program p")

    def test_use_before_allocate_and_double_allocate_are_errors(self):
        with self.assertRaisesRegex(LangError, "not allocated"):
            run(program("println(total())"))
        with self.assertRaisesRegex(LangError, "already allocated"):
            run(program("call setup(2)\ncall setup(2)"))
        with self.assertRaisesRegex(LangError, "not allocated"):
            run(program("deallocate @data"))

    def test_prints_back_as_source(self):
        text = to_source(parse(program("allocate @data[2]")))
        self.assertIn("public allocatable integer[] :: data", text)
        self.assertIn("allocate @data[2]", text)
        self.assertIn("deallocate @data", text)
        parse(text)


class ConvertTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = fixture("allocmod.f90")
        text, findings = convert(read(path), "allocmod.f90")
        self.assertEqual(todos(findings), [])
        if GFORTRAN:
            self.assertTrue(same_output("\n".join(run_bor(text)), run_gfortran(path)))

    def test_allocation_gets_update_and_a_function_becomes_a_subroutine(self):
        text, _ = convert(read(fixture("allocmod.f90")), "allocmod.f90")
        self.assertIn("public allocatable real32[,] :: cells", text)
        self.assertIn("allocate @cells[nx, ny]", text)
        self.assertIn("deallocate @cells", text)
        self.assertIn("allocate @cells[1, 1]", text)
        self.assertIn("allocate tmp[3]", text)               # a local array is unchanged
        self.assertIn("use grid: make, release, cells, ready", text)

    def test_a_module_procedure_sees_an_array_from_another_module(self):
        src = ("module a\n  integer, allocatable :: v(:)\nend module\n"
               "module b\n  use a\ncontains\n  subroutine s()\n    allocate(v(2))\n    v = 5\n  end subroutine\nend module\n"
               "program p\n  use a\n  use b\n  call s()\n  print *, v\nend program\n")
        text, findings = convert(src)
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["[5, 5]"])


if __name__ == "__main__":
    unittest.main()
