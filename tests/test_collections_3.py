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


if __name__ == "__main__":
    unittest.main()
