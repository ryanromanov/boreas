"""Collections, slice 3 (D69): a map entry as an out/inout argument, module variables, `do x in array`,
nested collections."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

HELPERS = """
subroutine bump(inout integer :: n)
  @n = n + 1
end subroutine

subroutine fresh(out integer :: n)
  @n = 7
end subroutine

subroutine two(inout integer :: a, inout integer :: b)
  @a = a + b
end subroutine

subroutine word(inout string :: s)
  @s = s + "!"
end subroutine

record shelf
  map[string, integer] :: stock
end record
"""


def run(body, decls="  map[string, integer] :: m\n", defs=HELPERS):
    return run_bor(defs + "program t\n" + decls + body + "\nend program\n")


def fails(test, body, message, decls="  map[string, integer] :: m\n", defs=HELPERS):
    with test.assertRaises(LangError) as cm:
        run(body, decls, defs)
    test.assertIn(message, str(cm.exception))


class EntryArgumentTests(unittest.TestCase):
    def test_inout_changes_the_entry(self):
        self.assertEqual(run('  m["a"] = 1\n  call bump(m["a"])\n  println(m)'), ["{a: 2}"])

    def test_out_creates_the_entry(self):
        self.assertEqual(run('  call fresh(m["z"])\n  println(m)'), ["{z: 7}"])

    def test_out_replaces_an_entry(self):
        self.assertEqual(run('  m["z"] = 1\n  call fresh(m["z"])\n  println(m)'), ["{z: 7}"])

    def test_two_different_entries(self):
        out = run('  m["a"] = 1\n  m["b"] = 5\n  call two(m["a"], m["b"])\n  println(m)')
        self.assertEqual(out, ["{a: 6, b: 5}"])

    def test_a_computed_key(self):
        self.assertEqual(run('  m["ab"] = 1\n  call bump(m["a" + "b"])\n  println(m)'), ["{ab: 2}"])

    def test_a_field_entry(self):
        out = run('  shelf :: s\n  s.stock["pen"] = 3\n  call bump(s.stock["pen"])\n  println(s.stock)', "")
        self.assertEqual(out, ["{pen: 4}"])

    def test_the_entry_of_a_parameter_map(self):
        defs = HELPERS + "subroutine up(inout map[string, integer] :: q)\n  call bump(q[\"a\"])\nend subroutine\n"
        out = run('  m["a"] = 1\n  call up(m)\n  println(m)', defs=defs)
        self.assertEqual(out, ["{a: 2}"])

    def test_a_string_entry(self):
        out = run('  m["a"] = "hi"\n  call word(m["a"])\n  println(m)', "  map[string, string] :: m\n")
        self.assertEqual(out, ["{a: hi!}"])

    def test_the_loop_variable_still_works_alongside(self):
        out = run('  m["a"] = 1\n  m["b"] = 2\n  string :: k\n  integer :: v\n  do k, v in m\n    call bump(m[k])\n  end do\n  println(m)')
        self.assertEqual(out, ["{a: 2, b: 3}"])

    def test_missing_key_for_inout(self):
        fails(self, '  call bump(m["q"])', "has no key")

    def test_wrong_type(self):
        fails(self, '  m["a"] = 1\n  call word(m["a"])', "argument 's' is string but an entry of 'm' is integer")

    def test_an_in_parameter_map_is_refused(self):
        defs = HELPERS + "subroutine peek(in map[string, integer] :: q)\n  call bump(q[\"a\"])\nend subroutine\n"
        fails(self, '  m["a"] = 1\n  call peek(m)', "is an 'in' parameter and cannot be passed as inout", defs=defs)

    def test_the_same_entry_twice_is_aliasing(self):
        fails(self, '  m["a"] = 1\n  call two(m["a"], m["a"])', "must not alias")

    def test_the_whole_map_and_an_entry_alias(self):
        defs = HELPERS + "subroutine mix(inout map[string, integer] :: q, inout integer :: n)\n  @n = 0\nend subroutine\n"
        fails(self, '  m["a"] = 1\n  call mix(m, m["a"])', "must not alias", defs=defs)

    def test_a_set_member_is_not_an_entry(self):
        fails(self, '  call add(s, "x")\n  call bump(s["x"])', "set", "  set[string] :: s\n")


REGISTRY = """
module registry
  public map[string, integer] :: hits
  public set[string] :: seen
  map[string, integer] :: hidden

  public subroutine record(in string :: who)
    @hits[who] = (get(hits, who) ?? 0) + 1
    call add(seen, who)
    @hidden[who] = 1
  end subroutine

  public function total() -> integer
    string :: k
    integer :: v
    integer :: t = 0
    do k, v in hits
      t = t + v
    end do
    return t + size(hidden)
  end function
end module
"""


def with_registry(body, decls="", uses="hits, seen, record, total"):
    return run_bor(REGISTRY + "program t\n  use registry: " + uses + "\n" + decls + body + "\nend program\n")


class ModuleVariableTests(unittest.TestCase):
    def test_starts_empty_and_is_shared(self):
        out = with_registry('  println(size(hits), size(seen))\n  call record("a")\n  call record("a")\n  call record("b")\n'
                            '  println(hits, seen, total())')
        self.assertEqual(out, ["0 0", "{a: 2, b: 1} {a, b} 5"])

    def test_the_importer_can_write_it(self):
        out = with_registry('  @hits["z"] = 9\n  call add(seen, "q")\n  call remove(seen, "q")\n  println(hits, size(seen))')
        self.assertEqual(out, ["{z: 9} 0"])

    def test_the_importer_can_loop_over_it(self):
        out = with_registry('  string :: k\n  integer :: v\n  call record("a")\n  do k, v in hits\n    println(k, v)\n  end do')
        self.assertEqual(out, ["a 1"])

    def test_a_private_one_cannot_be_imported(self):
        with self.assertRaises(LangError):
            with_registry('  println(hidden)', uses="hits, record, hidden")

    def test_an_importer_write_needs_the_mark(self):
        with self.assertRaises(LangError) as cm:
            with_registry('  hits["z"] = 9')
        self.assertIn("('hits')", str(cm.exception))

    def test_a_function_cannot_change_one(self):
        for stmt in ('@hits["k"] = 1', 'call add(seen, "x")', 'call remove(seen, "x")'):
            with self.subTest(stmt):
                src = ("module reg\n  public map[string, integer] :: hits\n  public set[string] :: seen\n"
                       "  public function f() -> integer\n    " + stmt + "\n    return 1\n  end function\nend module\n"
                       "program t\n  use reg: f\n  integer :: n\n  n = f()\nend program\n")
                with self.assertRaises(LangError) as cm:
                    run_bor(src)
                self.assertIn("a function cannot change the module variable", str(cm.exception))

    def test_a_function_may_change_a_local_one(self):
        src = ("module reg\n  public function f() -> integer\n    set[string] :: s\n    call add(s, \"x\")\n"
               "    return size(s)\n  end function\nend module\nprogram t\n  use reg: f\n  println(f())\nend program\n")
        self.assertEqual(run_bor(src), ["1"])

    def test_a_function_may_read_one(self):
        out = with_registry('  call record("a")\n  println(total())')
        self.assertEqual(out, ["2"])

    def test_check_is_clean(self):
        src = REGISTRY + "program t\n  use registry: hits, seen, record\n  call record(\"a\")\n  @hits[\"z\"] = 1\n  println(seen)\nend program\n"
        self.assertEqual(check_errors(src), [])

    def test_a_program_level_declaration_still_works(self):
        self.assertEqual(run('  m["a"] = 1\n  println(m)'), ["{a: 1}"])


if __name__ == "__main__":
    unittest.main()
