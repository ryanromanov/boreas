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


NESTED = """
subroutine double(inout integer[] :: v)
  integer :: i
  do i = 1, size(v)
    @v[i] = v[i] * 2
  end do
end subroutine

subroutine start(out set[string] :: s)
  call add(s, "first")
end subroutine

subroutine tag(inout set[string] :: s, in string :: x)
  call add(s, x)
end subroutine

function members(map[string, set[string]] :: g, string :: k) -> integer
  return size(g[k])
end function

function build() -> map[string, set[string]]
  map[string, set[string]] :: g
  set[string] :: s
  call add(s, "x")
  g["a"] = s
  return g
end function

record graph
  map[string, set[string]] :: edges
  map[string, integer[]] :: rows
end record
"""


def nested(body, decls="  map[string, set[string]] :: g\n  set[string] :: s\n  string :: w\n"):
    return run_bor(NESTED + "program t\n" + decls + body + "\nend program\n")


def nested_fails(test, body, message, decls="  map[string, set[string]] :: g\n  set[string] :: s\n  string :: w\n"):
    with test.assertRaises(LangError) as cm:
        nested(body, decls)
    test.assertIn(message, str(cm.exception))


class NestedSetTests(unittest.TestCase):
    def test_store_and_add(self):
        out = nested('  g["a"] = s\n  call add(g["a"], "x")\n  call add(g["a"], "y")\n  println(g, size(g["a"]), has(g["a"], "y"))')
        self.assertEqual(out, ["{a: {x, y}} 2 true"])

    def test_remove(self):
        out = nested('  call add(s, "x")\n  call add(s, "y")\n  g["a"] = s\n  call remove(g["a"], "x")\n  println(g)')
        self.assertEqual(out, ["{a: {y}}"])

    def test_storing_copies(self):
        out = nested('  g["a"] = s\n  call add(s, "later")\n  println(g, size(s))')
        self.assertEqual(out, ["{a: {}} 1"])

    def test_one_entry_stored_into_another_is_a_copy(self):
        out = nested('  g["a"] = s\n  call add(g["a"], "x")\n  g["b"] = g["a"]\n  call add(g["b"], "y")\n  println(g)')
        self.assertEqual(out, ["{a: {x}, b: {x, y}}"])

    def test_reading_into_a_variable_copies(self):
        out = nested('  g["a"] = s\n  s = g["a"]\n  call add(s, "x")\n  println(g, s)')
        self.assertEqual(out, ["{a: {}} {x}"])

    def test_assigning_a_map_copies_the_inner_sets(self):
        out = nested('  map[string, set[string]] :: h\n  g["a"] = s\n  call add(g["a"], "x")\n  h = g\n  call add(h["a"], "y")\n  println(g, h)')
        self.assertEqual(out, ["{a: {x}} {a: {x, y}}"])

    def test_loop_over_an_entry(self):
        out = nested('  call add(s, "p")\n  call add(s, "q")\n  g["a"] = s\n  do w in g["a"]\n    println(w)\n  end do')
        self.assertEqual(out, ["p", "q"])

    def test_loop_over_the_whole_map(self):
        out = nested('  string :: k\n  set[string] :: m\n  call add(s, "p")\n  g["a"] = s\n  do k, m in g\n    println(k, m)\n  end do')
        self.assertEqual(out, ["a {p}"])

    def test_get_and_default(self):
        out = nested('  g["a"] = s\n  call add(s, "z")\n  println(get(g, "a") ?? s, get(g, "q") ?? s)')
        self.assertEqual(out, ["{} {z}"])

    def test_missing_outer_key_is_an_error(self):
        nested_fails(self, '  call add(g["nope"], "x")', 'the map \'g\' has no key "nope"')

    def test_missing_outer_key_on_remove(self):
        nested_fails(self, '  call remove(g["nope"], "x")', "has no key")

    def test_reading_a_missing_one(self):
        nested_fails(self, '  println(size(g["nope"]))', "has no key")

    def test_wrong_member_type(self):
        nested_fails(self, '  g["a"] = s\n  call add(g["a"], 3)', "members")

    def test_wrong_value_type(self):
        nested_fails(self, '  set[integer] :: nums\n  g["a"] = nums', "expected a set[string], got a set[integer]")

    def test_a_plain_value_is_refused(self):
        nested_fails(self, '  g["a"] = 5', "expected a set[string]")

    def test_parameter_entry_out_and_inout(self):
        out = nested('  g["a"] = s\n  call tag(g["a"], "x")\n  call start(g["b"])\n  println(g)')
        self.assertEqual(out, ["{a: {x}, b: {first}}"])

    def test_as_a_parameter_and_result(self):
        out = nested('  g = build()\n  println(g, members(g, "a"))')
        self.assertEqual(out, ["{a: {x}} 1"])

    def test_a_record_field(self):
        out = nested('  graph :: net\n  net.edges["a"] = s\n  call add(net.edges["a"], "b")\n  println(net.edges)', "  set[string] :: s\n")
        self.assertEqual(out, ["{a: {b}}"])

    def test_copying_a_record_copies_inner_sets(self):
        out = nested('  graph :: one\n  graph :: two\n  one.edges["a"] = s\n  two = one\n  call add(two.edges["a"], "z")\n  println(one.edges, two.edges)',
                     "  set[string] :: s\n")
        self.assertEqual(out, ["{a: {}} {a: {z}}"])

    def test_values_needs_single_values(self):
        nested_fails(self, '  println(values(g))', "values() gives an array of single values")

    def test_an_in_parameter_entry_cannot_change(self):
        src = (NESTED + "subroutine peek(in map[string, set[string]] :: q)\n  call add(q[\"a\"], \"x\")\nend subroutine\n"
               "program t\n  map[string, set[string]] :: g\n  set[string] :: s\n  g[\"a\"] = s\n  call peek(g)\nend program\n")
        with self.assertRaises(LangError) as cm:
            run_bor(src)
        self.assertIn("cannot be changed", str(cm.exception))

    def test_a_function_cannot_change_a_module_entry(self):
        src = ("module reg\n  public map[string, set[string]] :: g\n  public function f() -> integer\n"
               "    call add(g[\"a\"], \"x\")\n    return 1\n  end function\nend module\n"
               "program t\n  use reg: f\n  integer :: n\n  n = f()\nend program\n")
        with self.assertRaises(LangError) as cm:
            run_bor(src)
        self.assertIn("a function cannot change the module variable", str(cm.exception))


class NestedArrayTests(unittest.TestCase):
    DECLS = "  map[string, integer[]] :: m\n  integer :: n\n"

    def test_store_and_read(self):
        out = nested('  m["a"] = [1, 2, 3]\n  println(m, size(m["a"]), sum(m["a"]))', self.DECLS)
        self.assertEqual(out, ["{a: [1, 2, 3]} 3 6"])

    def test_loop(self):
        out = nested('  m["a"] = [4, 5]\n  do n in m["a"]\n    println(n)\n  end do', self.DECLS)
        self.assertEqual(out, ["4", "5"])

    def test_inout_an_entry(self):
        out = nested('  m["a"] = [1, 2, 3]\n  call double(m["a"])\n  println(m)', self.DECLS)
        self.assertEqual(out, ["{a: [2, 4, 6]}"])

    def test_replacing_an_entry_changes_its_length(self):
        out = nested('  m["a"] = [1]\n  m["a"] = [1, 2]\n  println(m)', self.DECLS)
        self.assertEqual(out, ["{a: [1, 2]}"])

    def test_storing_copies(self):
        out = nested('  integer[2] :: a = [1, 2]\n  m["k"] = a\n  a[1] = 9\n  println(m, a)', self.DECLS)
        self.assertEqual(out, ["{k: [1, 2]} [9, 2]"])

    def test_reading_copies(self):
        out = nested('  integer[2] :: a\n  m["k"] = [1, 2]\n  a = m["k"]\n  a[1] = 9\n  println(m, a)', self.DECLS)
        self.assertEqual(out, ["{k: [1, 2]} [9, 2]"])

    def test_assigning_the_map_copies_the_arrays(self):
        out = nested('  map[string, integer[]] :: h\n  m["k"] = [1, 2]\n  h = m\n  call double(h["k"])\n  println(m, h)', self.DECLS)
        self.assertEqual(out, ["{k: [1, 2]} {k: [2, 4]}"])

    def test_real_values_widen(self):
        out = nested('  m["a"] = [1, 2]\n  println(m)', "  map[string, real[]] :: m\n")
        self.assertEqual(out, ["{a: [1.0, 2.0]}"])

    def test_wrong_element_type(self):
        nested_fails(self, '  m["a"] = ["x"]', "cannot assign string to integer", self.DECLS)

    def test_wrong_rank(self):
        nested_fails(self, '  m["a"] = reshape([1, 2, 3, 4], [2, 2])', "expected a 1-dimensional integer array", self.DECLS)

    def test_a_scalar_is_refused(self):
        nested_fails(self, '  m["a"] = 5', "expected a 1-dimensional integer array", self.DECLS)

    def test_missing_key(self):
        nested_fails(self, '  call double(m["q"])', "has no key", self.DECLS)

    def test_a_matrix_value(self):
        out = nested('  m["a"] = reshape([1, 2, 3, 4], [2, 2])\n  println(m)', "  map[string, integer[,]] :: m\n")
        self.assertEqual(out, ["{a: [[1, 3], [2, 4]]}"])

    def test_values_needs_single_values(self):
        nested_fails(self, '  println(values(m))', "values() gives an array of single values", self.DECLS)

    def test_a_record_field(self):
        out = nested('  graph :: net\n  net.rows["a"] = [1, 2]\n  call double(net.rows["a"])\n  println(net.rows)', "")
        self.assertEqual(out, ["{a: [2, 4]}"])


class NestedParseTests(unittest.TestCase):
    def refused(self, decl, message):
        with self.assertRaises(LangError) as cm:
            parse("program t\n  " + decl + "\nend program\n")
        self.assertIn(message, str(cm.exception))

    def test_a_map_of_maps_is_refused(self):
        self.refused("map[string, map[string, integer]] :: m", "cannot be another map")

    def test_an_optional_value_is_refused(self):
        self.refused("map[string, integer?] :: m", "cannot be optional")

    def test_a_set_of_sets_is_refused(self):
        self.refused("set[set[string]] :: s", "must be a single value")

    def test_an_array_member_is_refused(self):
        self.refused("set[integer[]] :: s", "must be a single value")

    def test_array_keys_are_refused(self):
        self.refused("map[integer[], integer] :: m", "must be a single value")

    def test_sized_array_values_are_refused(self):
        self.refused("map[string, integer[3]] :: m", "without sizes")

    def test_prints_back(self):
        src = "program t\n  map[string, set[string]] :: g\n  map[string, real[,]] :: m\nend program\n"
        again = to_source(parse(src))
        self.assertIn("map[string, set[string]] :: g", again)
        self.assertIn("map[string, real[,]] :: m", again)
        self.assertEqual(to_source(parse(again)), again)

    def test_check_is_clean(self):
        src = NESTED + "program t\n  map[string, set[string]] :: g\n  set[string] :: s\n  g[\"a\"] = s\n  call add(g[\"a\"], \"x\")\n  println(g)\nend program\n"
        self.assertEqual(check_errors(src), [])


class NestedEditorAndOutTests(unittest.TestCase):
    def test_an_out_array_entry_is_caller_owned_storage(self):
        defs = "subroutine squares(out integer[] :: v)\n  integer :: i\n  do i = 1, size(v)\n    @v[i] = i * i\n  end do\nend subroutine\n"
        out = run('  m["a"] = [0, 0, 0]\n  call squares(m["a"])\n  println(m)', "  map[string, integer[]] :: m\n", defs)
        self.assertEqual(out, ["{a: [1, 4, 9]}"])

    def test_an_out_array_entry_must_exist(self):
        defs = "subroutine squares(out integer[] :: v)\n  @v[1] = 1\nend subroutine\n"
        with self.assertRaises(LangError) as cm:
            run('  call squares(m["zz"])', "  map[string, integer[]] :: m\n", defs)
        self.assertIn("has no key", str(cm.exception))

    def test_hover_on_nested_declarations(self):
        from boreas import navigation
        src = ("module reg\n  public map[string, set[string]] :: g\n  public map[string, real[,]] :: grid\nend module\n"
               "program t\n  use reg: g, grid\n  map[string, set[string]] :: local\n  println(size(g), size(grid), size(local))\nend program\n")
        lines = src.split("\n")
        for row, anchor, want in [(1, ":: g", "map[string, set[string]] :: g"), (2, ":: grid", "map[string, real[,]] :: grid"),
                                  (6, ":: local", "map[string, set[string]] :: local"), (7, "size(g", "map[string, set[string]] :: g")]:
            col = lines[row].index(anchor) + len(anchor) - 1
            hover = navigation.hover(src, row, col)
            self.assertIsNotNone(hover, anchor)
            self.assertIn(want, hover["contents"]["value"])


if __name__ == "__main__":
    unittest.main()
