"""`@` at the call site: an argument for an out or inout parameter carries it, and no other does."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from test_fortran import run_bor
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source
from boreas.callmarks import fill_marks
from boreas.names import undeclared_names
from boreas.interp import Interp
from add_call_marks import migrate

SUBS = '''subroutine swap(inout int :: a, inout int :: b)
  int :: t
  t = a
  @a = b
  @b = t
end subroutine
subroutine fill(out int :: a, int :: v)
  @a = v
end subroutine
record counter
  int :: n = 0
  subroutine grow(inout self, int :: by)
    @self.n = self.n + by
  end subroutine
  subroutine show(self)
    println(self.n)
  end subroutine
end record
'''


def run(body):
    return run_bor(SUBS + "program t\n" + body + "end program\n")


def fails(case, body, message):
    with case.assertRaises(LangError) as cm:
        run(body)
    case.assertIn(message, str(cm.exception))


class CallMarkTests(unittest.TestCase):
    def test_marked_arguments_work(self):
        self.assertEqual(run("  int :: x = 1\n  int :: y = 2\n  call swap(@x, @y)\n  println(x, y)\n"), ["2 1"])
        self.assertEqual(run("  int :: x\n  call fill(@x, 7)\n  println(x)\n"), ["7"])

    def test_an_unmarked_changed_argument_is_an_error(self):
        fails(self, "  int :: x = 1\n  int :: y = 2\n  call swap(@x, y)\n",
              "'y' is changed by 'swap' (its inout parameter 'b'), so mark it: @y")
        fails(self, "  int :: x\n  call fill(x, 7)\n", "'x' is changed by 'fill' (its out parameter 'a'), so mark it: @x")

    def test_a_mark_on_an_in_parameter_is_an_error(self):
        fails(self, "  int :: x\n  int :: v = 1\n  call fill(@x, @v)\n",
              "'@v' marks an argument the call changes, but 'v' of 'fill' is an 'in' parameter: remove the '@'")

    def test_elements_fields_and_entries(self):
        body = ("  int[3] :: a = [1, 2, 3]\n  call swap(@a[1], @a[3])\n  println(a)\n")
        self.assertEqual(run(body), ["[3, 2, 1]"])

    def test_a_records_function_marks_the_record(self):
        self.assertEqual(run("  counter :: c\n  call @c.grow(2)\n  call c.show()\n"), ["2"])
        fails(self, "  counter :: c\n  call c.grow(2)\n", "'c' is changed by 'grow'")
        fails(self, "  counter :: c\n  call @c.show()\n", "marks an argument the call changes")

    def test_add_and_remove_mark_the_collection(self):
        self.assertEqual(run('  set[string] :: s\n  call add(@s, "a")\n  call remove(@s, "a")\n  println(size(s))\n'), ["0"])
        fails(self, '  set[string] :: s\n  call add(s, "a")\n', "add changes its first argument, so mark it: call add(@s, ...)")
        fails(self, '  set[string] :: s\n  call add(@s, @"a")\n', "'@' marks what the call changes")

    def test_a_callback_is_checked_by_its_signature(self):
        src = ("subroutine run(subroutine(inout int) :: f, inout int :: n)\n  call f(@n)\nend subroutine\n"
               "subroutine inc(inout int :: k)\n  @k = k + 1\nend subroutine\n"
               "program t\n  int :: n = 1\n  call run(inc, @n)\n  println(n)\nend program\n")
        self.assertEqual(run_bor(src), ["2"])

    def test_a_mark_outside_a_call_statement(self):
        fails(self, "  int :: x = 1\n  println(abs(@x))\n", "'@' marks an argument of a call statement")

    def test_check_finds_them_without_running(self):
        text = SUBS + "program t\n  int :: x\n  call fill(x, 7)\nend program\n"
        unit = parse(text)
        errors = undeclared_names(unit, Interp(unit, lambda s: None))
        self.assertTrue(any("'x' is changed by 'fill'" in e[2] for e in errors))

    def test_the_printer_keeps_the_marks(self):
        src = SUBS + 'program t\n  int :: x\n  counter :: c\n  call @c.grow(1)\n  call fill(@x, 7)\nend program\n'
        again = to_source(parse(src))
        self.assertIn("call @c.grow(1)", again)
        self.assertIn("call fill(@x, 7)", again)
        self.assertEqual(to_source(parse(again)), again)


class MigrationTests(unittest.TestCase):
    def test_older_sources_get_their_marks(self):
        old = SUBS + "program t\n  int :: x = 1\n  int :: y = 2\n  counter :: c\n  call swap(x, y)\n  call c.grow(2)\n  call fill(x, 7)\nend program\n"
        new, changes = migrate(old)
        self.assertIn("call swap(@x, @y)", new)
        self.assertIn("call @c.grow(2)", new)
        self.assertIn("call fill(@x, 7)", new)
        self.assertEqual(len(changes), 3)
        self.assertEqual(run_bor(new), [])

    def test_fill_marks_sets_the_ast(self):
        unit = parse(SUBS + "program t\n  int :: x\n  call fill(x, 7)\nend program\n")
        self.assertEqual(len(fill_marks(unit)), 1)
        self.assertEqual(unit.program.body[-1].marks, [True, False])


    def test_the_migration_tool_handles_a_call_over_several_lines(self):
        src = (SUBS + "program t\n  int :: x\n  int :: y\n  call swap(x, &\n       y)\nend program\n")
        new, changes = migrate(src)
        self.assertIn("call swap(@x, &\n       @y)", new)
        self.assertEqual(len(changes), 1)

    def test_a_call_of_a_records_function_can_be_an_argument_of_a_call_that_changes_something(self):
        src = ("record acc\n  int :: n = 0\n  function plus(self, int :: k) -> int\n    return self.n + k\n  end function\n"
               "  subroutine add(inout self, int :: by)\n    @self.n = self.n + by\n  end subroutine\nend record\n"
               "program t\n  acc :: a\n  acc :: b\n  call @b.add(a.plus(1))\n  println(b.n)\nend program\n")
        self.assertEqual(run_bor(src), ["1"])


if __name__ == "__main__":
    unittest.main()
