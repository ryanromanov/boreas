"""Maps and sets: map[string, integer], set[string] (D66). Local variables only so far."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

DECLS = "  map[string, integer] :: age\n  set[string] :: seen\n"


def show(body, decls=DECLS):
    return run_bor("program t\n" + decls + body + "\nend program\n")


def fails(test, body, message, decls=DECLS):
    with test.assertRaises(LangError) as cm:
        show(body, decls)
    test.assertIn(message, str(cm.exception))


class MapTests(unittest.TestCase):
    def test_insert_update_and_read(self):
        out = show('  age["ann"] = 31\n  age["bob"] = 40\n  age["ann"] = 32\n  println(age["ann"], age["bob"], size(age))')
        self.assertEqual(out, ["32 40 2"])

    def test_has_and_get(self):
        out = show('  age["ann"] = 31\n  println(has(age, "ann"), has(age, "cy"))\n'
                   '  println(get(age, "ann") ?? 0, get(age, "cy") ?? -1)')
        self.assertEqual(out, ["true false", "31 -1"])

    def test_keys_and_values_keep_the_order_they_were_added_in(self):
        out = show('  age["zed"] = 1\n  age["amy"] = 2\n  age["bob"] = 3\n  age["zed"] = 9\n'
                   '  println(keys(age))\n  println(values(age))')
        self.assertEqual(out, ["[zed, amy, bob]", "[9, 2, 3]"])

    def test_printing(self):
        self.assertEqual(show('  age["ann"] = 31\n  age["bob"] = 40\n  println(age)'), ["{ann: 31, bob: 40}"])
        self.assertEqual(show("  println(age, seen)"), ["{} {}"])

    def test_remove(self):
        out = show('  age["ann"] = 1\n  age["bob"] = 2\n  call remove(age, "ann")\n  println(age, size(age))')
        self.assertEqual(out, ["{bob: 2} 1"])

    def test_integer_keys_and_other_value_types(self):
        out = show('  ok[7] = true\n  ok[2] = false\n  println(ok, ok[7])', "  map[integer, logical] :: ok\n")
        self.assertEqual(out, ["{7: true, 2: false} true"])

    def test_an_integer_goes_into_a_real_value(self):
        self.assertEqual(show('  w["x"] = 1\n  println(w, w["x"] + 0.5)', "  map[string, real] :: w\n"),
                         ["{x: 1.0} 1.5"])

    def test_a_loop_over_the_keys(self):
        out = show('  age["x"] = 1\n  age["y"] = 2\n  allocatable string[] :: ks\n  ks = keys(age)\n  integer :: i\n'
                   '  integer :: total = 0\n  do i = 1, size(ks)\n    total = total + age[ks[i]]\n  end do\n  println(total)')
        self.assertEqual(out, ["3"])

    def test_counting_words(self):
        out = show('  allocatable string[] :: words\n  words = split("a b a c a b", " ")\n  integer :: i\n'
                   '  do i = 1, size(words)\n    age[words[i]] = (get(age, words[i]) ?? 0) + 1\n  end do\n  println(age)')
        self.assertEqual(out, ["{a: 3, b: 2, c: 1}"])

    def test_empty_keys_and_values(self):
        self.assertEqual(show("  println(keys(age), values(age), size(keys(age)))"), ["[] [] 0"])


class SetTests(unittest.TestCase):
    def test_add_has_size(self):
        out = show('  call add(seen, "x")\n  call add(seen, "y")\n  call add(seen, "x")\n'
                   '  println(seen, size(seen), has(seen, "y"), has(seen, "q"))')
        self.assertEqual(out, ["{x, y} 2 true false"])

    def test_keys_gives_the_members(self):
        self.assertEqual(show('  call add(seen, "b")\n  call add(seen, "a")\n  println(keys(seen))'), ["[b, a]"])

    def test_remove(self):
        self.assertEqual(show('  call add(seen, "x")\n  call remove(seen, "x")\n  println(seen)'), ["{}"])

    def test_integer_members(self):
        self.assertEqual(show("  call add(s, 3)\n  call add(s, 1)\n  println(s, has(s, 3))", "  set[integer] :: s\n"),
                         ["{3, 1} true"])


class CopyTests(unittest.TestCase):
    def test_an_initializer_copies(self):
        out = show('  age["a"] = 1\n  map[string, integer] :: b = age\n  b["z"] = 2\n  println(has(age, "z"), has(b, "z"))')
        self.assertEqual(out, ["false true"])

    def test_assignment_copies(self):
        out = show('  map[string, integer] :: b\n  age["a"] = 1\n  b = age\n  b["z"] = 2\n  age["y"] = 3\n  println(age)\n  println(b)')
        self.assertEqual(out, ["{a: 1, y: 3}", "{a: 1, z: 2}"])

    def test_a_set_assignment_copies(self):
        out = show('  set[string] :: t\n  call add(seen, "a")\n  t = seen\n  call add(t, "b")\n  println(seen, t)')
        self.assertEqual(out, ["{a} {a, b}"])

    def test_keys_is_a_new_array(self):
        out = show('  age["a"] = 1\n  allocatable string[] :: ks\n  ks = keys(age)\n  ks[1] = "changed"\n  println(keys(age))')
        self.assertEqual(out, ["[a]"])

    def test_the_types_must_match(self):
        fails(self, "  map[string, real] :: b = age", "expected a map[string, real], got a map[string, integer]")
        fails(self, "  age = seen", "expected a map[string, integer], got a set[string]")


class ErrorTests(unittest.TestCase):
    def test_a_missing_key(self):
        fails(self, '  println(age["zz"])', "the map 'age' has no key \"zz\"")
        fails(self, '  println(age["zz"])', "get(age, key)")

    def test_the_wrong_key_type(self):
        fails(self, "  println(age[1])", "the map 'age' has string keys, got integer")
        fails(self, "  age[3] = 1", "has string keys")
        fails(self, "  call add(seen, 3)", "the set 'seen' has string members, got integer")
        fails(self, "  println(has(seen, 3))", "has string members")

    def test_the_wrong_value_type(self):
        fails(self, '  age["x"] = "text"', "cannot assign string to integer")

    def test_removing_what_is_not_there(self):
        fails(self, '  call remove(age, "q")', "'age' has no such key")
        fails(self, '  call remove(seen, "q")', "'seen' has no such member")

    def test_the_wrong_kind_of_collection(self):
        fails(self, '  call add(age, "x")', "a map gets entries with m[key] = value")
        fails(self, '  seen["x"] = 1', "a set, which has no entries to index")
        fails(self, '  println(get(seen, "x"))', "get() needs a map")
        fails(self, "  println(values(seen))", "values() needs a map")
        fails(self, "  println(has(3, 1))", "has() needs a map or a set")

    def test_the_wrong_number_of_arguments(self):
        fails(self, "  call add(seen)", "add expects 2 arguments")

    def test_parentheses_are_for_functions(self):
        fails(self, '  println(age("ann"))', "look up one key with age[key]")

    def test_no_arithmetic_or_comparison(self):
        fails(self, "  println(age + 1)", "a map cannot be used with '+'")
        fails(self, "  map[string, integer] :: b\n  println(age == b)", "cannot be compared")
        fails(self, "  println(-age)", "cannot be used with '-'")

    def test_no_dimension_for_size(self):
        fails(self, "  println(size(age, 1))", "no dimensions")

    def test_a_collection_is_refused_where_a_number_is_wanted(self):
        src = "function f(integer :: n) -> integer\n  return n\nend function\n"
        with self.assertRaises(LangError):
            run_bor(src + "program t\n  set[integer] :: s\n  println(f(s))\nend program\n")


class EntryArgumentTests(unittest.TestCase):
    SWAP = ("subroutine swap(inout integer :: a, inout integer :: b)\n  integer :: t\n  t = a\n  @a = b\n  @b = t\nend subroutine\n"
            "function divmod(integer :: a, integer :: b) -> (integer :: q, integer :: r)\n  return a / b, mod(a, b)\nend function\n")

    def run_it(self, body):
        return run_bor(self.SWAP + "program t\n" + DECLS + '  age["a"] = 1\n  age["b"] = 2\n' + body + "\nend program\n")

    def test_a_map_entry_cannot_be_an_inout_or_out_argument_yet(self):
        for body in ('call swap(age["a"], age["b"])', 'integer :: n\n  call swap(n, age["a"])',
                     'call swap(age["a"], age["a"])'):
            with self.subTest(body):
                with self.assertRaisesRegex(LangError, "an entry of a map cannot be passed as an inout argument"):
                    self.run_it(body)

    def test_an_entry_can_be_read_as_an_in_argument_and_unpacked_into(self):
        out = self.run_it('(age["q"], age["r"]) = divmod(7, 2)\n  println(age)')
        self.assertEqual(out, ["{a: 1, b: 2, q: 3, r: 1}"])

    def test_the_same_entry_twice_in_an_unpacking_is_refused(self):
        with self.assertRaisesRegex(LangError, "assigned twice"):
            self.run_it('(age["a"], age["a"]) = divmod(7, 2)')


class ParseTests(unittest.TestCase):
    def refused(self, src, message):
        with self.assertRaises(LangError) as cm:
            parse(src)
        self.assertIn(message, str(cm.exception))

    def test_a_module_variable_is_not_supported_yet(self):
        for src in ["module m\n  public map[string, integer] :: age\nend module\nprogram t\nend program\n",
                    "module m\n  map[string, integer] :: age\nend module\nprogram t\nend program\n"]:
            with self.subTest(src.splitlines()[1]):
                self.refused(src, "cannot be a module variable yet")

    def test_parameters_results_and_fields_parse(self):
        parse("function f(map[string, integer] :: m, set[integer] :: s) -> map[string, integer]\n  return m\nend function\n"
              "record r\n  set[string] :: tags\nend record\nprogram t\nend program\n")

    def test_a_field_has_no_default_and_no_optional(self):
        self.refused("record r\n  map[string, integer] :: m = x\nend record\nprogram t\nend program\n", "starts empty")
        self.refused("function f(map[string, integer]? :: m) -> integer\n  return 1\nend function\nprogram t\nend program\n",
                     "optional map is not supported")

    def test_keys_must_be_integer_or_string(self):
        self.refused("program t\n  map[real, integer] :: m\nend program\n", "must be integer or string, not real")
        self.refused("program t\n  set[logical] :: m\nend program\n", "must be integer or string, not logical")

    def test_values_must_be_single_values(self):
        for typ in ("integer[]", "integer?", "map[string, integer]", "set[integer]"):
            with self.subTest(typ):
                self.refused(f"program t\n  map[string, {typ}] :: m\nend program\n", "must be a single value")

    def test_no_const_allocatable_or_arrays(self):
        self.refused("program t\n  const map[string, integer] :: m\nend program\n", "cannot be 'const' or 'allocatable'")
        self.refused("program t\n  allocatable set[string] :: m\nend program\n", "cannot be 'const' or 'allocatable'")
        self.refused("program t\n  map[string, integer] :: m[3]\nend program\n", "an array of maps is not supported")

    def test_map_and_set_are_still_ordinary_names(self):
        self.assertEqual(show("  integer :: map = 3\n  integer :: set = 4\n  println(map + set)", ""), ["7"])

    def test_an_array_of_a_record_called_map_still_works(self):
        src = "record map\n  integer :: a = 1\nend record\nprogram t\n  map[3] :: x\n  println(size(x))\nend program\n"
        self.assertEqual(run_bor(src), ["3"])

    def test_inside_functions_and_record_functions(self):
        src = ("function count_distinct(integer[] :: v) -> integer\n  set[integer] :: s\n  integer :: i\n"
               "  do i = 1, size(v)\n    call add(s, v[i])\n  end do\n  return size(s)\nend function\n"
               "program t\n  println(count_distinct([3, 1, 3, 2, 1]))\nend program\n")
        self.assertEqual(run_bor(src), ["3"])


class ToolTests(unittest.TestCase):
    SRC = ("program t\n  map[string, integer] :: age\n  set[integer] :: s\n  age[\"a\"] = 1\n  call add(s, 3)\n"
           "  println(age, s, keys(age), values(age), has(s, 3), get(age, \"a\") ?? 0)\nend program\n")

    def test_printing_and_parsing_round_trip(self):
        once = to_source(parse(self.SRC))
        self.assertEqual(to_source(parse(once)), once)
        self.assertIn("map[string, integer] :: age", once)
        self.assertIn("set[integer] :: s", once)

    def test_check_accepts_the_built_ins(self):
        self.assertEqual(check_errors(self.SRC), [])

    def test_check_asks_for_brackets(self):
        src = self.SRC.replace('age["a"] = 1', 'age("a") = 1')
        self.assertTrue(any("index with brackets" in e[2] for e in check_errors(src)))

    def test_check_still_finds_unknown_subroutines(self):
        src = self.SRC.replace("call add(s, 3)", "call push(s, 3)")
        self.assertTrue(any("unknown subroutine 'push'" in e[2] for e in check_errors(src)))

    def test_a_user_function_named_get_wins(self):
        src = ("function get(integer :: a) -> integer\n  return a + 100\nend function\n"
               "program t\n  println(get(1))\nend program\n")
        self.assertEqual(run_bor(src), ["101"])

    def test_a_user_subroutine_named_add_wins(self):
        src = ("subroutine add(inout integer :: n)\n  @n = n + 1\nend subroutine\n"
               "program t\n  integer :: x = 1\n  call add(x)\n  println(x)\nend program\n")
        self.assertEqual(run_bor(src), ["2"])


BOOK = """record book
  integer :: pages = 1
  map[string, integer] :: stock
  set[string] :: tags
end record
"""


def run_with(defs, body):
    return run_bor(defs + "program t\n  map[string, integer] :: m\n" + body + "\nend program\n")


class ParameterTests(unittest.TestCase):
    DEFS = ("function sizeof(map[string, integer] :: m) -> integer\n  return size(m)\nend function\n"
            "subroutine put(inout map[string, integer] :: m, string :: k, integer :: v)\n  @m[k] = v\nend subroutine\n"
            "subroutine fresh(out set[integer] :: s)\n  call add(s, 5)\nend subroutine\n"
            "subroutine drop(inout map[string, integer] :: m, string :: k)\n  call remove(m, k)\nend subroutine\n")

    def test_an_in_parameter_is_read_only_but_not_copied(self):
        self.assertEqual(run_with(self.DEFS, '  m["a"] = 1\n  println(sizeof(m))'), ["1"])

    def test_an_inout_parameter_changes_the_callers_map(self):
        self.assertEqual(run_with(self.DEFS, '  call put(m, "k", 5)\n  call put(m, "j", 6)\n  println(m)'), ["{k: 5, j: 6}"])

    def test_call_add_and_remove_work_on_an_inout_parameter(self):
        self.assertEqual(run_with(self.DEFS, '  m["a"] = 1\n  m["b"] = 2\n  call drop(m, "a")\n  println(m)'), ["{b: 2}"])

    def test_an_out_parameter_starts_empty(self):
        out = run_with(self.DEFS, "  set[integer] :: s\n  call add(s, 1)\n  call fresh(s)\n  println(s)")
        self.assertEqual(out, ["{5}"])

    def test_the_arguments_may_not_overlap(self):
        defs = self.DEFS + "subroutine both(inout map[string, integer] :: a, inout map[string, integer] :: b)\nend subroutine\n"
        with self.assertRaisesRegex(LangError, "must not alias"):
            run_with(defs, "  call both(m, m)")

    def test_an_in_parameter_cannot_be_changed(self):
        for body, text in [('m["x"] = 1', "'m' is an 'in' parameter and cannot be assigned"),
                           ('call add(s, 1)', "'s' is an 'in' parameter and cannot be changed"),
                           ('call remove(m, "x")', "cannot be changed")]:
            defs = f"function f(map[string, integer] :: m, set[integer] :: s) -> integer\n  {body}\n  return 1\nend function\n"
            with self.subTest(body):
                with self.assertRaisesRegex(LangError, text):
                    run_with(defs, "  set[integer] :: q\n  println(f(m, q))")

    def test_an_inout_write_needs_its_mark(self):
        defs = "subroutine nomark(inout map[string, integer] :: m)\n  m[\"k\"] = 5\nend subroutine\n"
        with self.assertRaisesRegex(LangError, "so other code sees this write"):
            run_with(defs, "  call nomark(m)")

    def test_an_in_parameter_cannot_be_passed_on_as_inout(self):
        defs = (self.DEFS + "function outer(map[string, integer] :: m) -> integer\n  call put(m, \"k\", 1)\n  return 1\nend function\n")
        with self.assertRaisesRegex(LangError, "cannot be passed as inout"):
            run_with(defs, "  println(outer(m))")

    def test_the_wrong_map_type_is_refused(self):
        with self.assertRaisesRegex(LangError, "expected a map\\[string, integer\\], got a map\\[integer, integer\\]"):
            run_with(self.DEFS, "  map[integer, integer] :: w\n  println(sizeof(w))")

    def test_a_map_entry_can_be_passed_as_an_in_argument(self):
        defs = "function twice(integer :: n) -> integer\n  return n * 2\nend function\n"
        self.assertEqual(run_with(defs, '  m["a"] = 4\n  println(twice(m["a"]))'), ["8"])


class ResultTests(unittest.TestCase):
    DEFS = ("function make() -> map[string, integer]\n  map[string, integer] :: m\n  m[\"a\"] = 1\n  m[\"b\"] = 2\n  return m\nend function\n"
            "function members(integer[] :: v) -> set[integer]\n  set[integer] :: s\n  integer :: i\n"
            "  do i = 1, size(v)\n    call add(s, v[i])\n  end do\n  return s\nend function\n"
            "function pair() -> (map[string, integer], integer)\n  map[string, integer] :: m\n  m[\"a\"] = 1\n  return m, 7\nend function\n"
            "function passthrough(map[string, integer] :: m) -> map[string, integer]\n  return m\nend function\n")

    def test_a_function_can_return_a_map_or_a_set(self):
        self.assertEqual(run_with(self.DEFS, "  m = make()\n  println(m)\n  println(members([3, 1, 3]))"),
                         ["{a: 1, b: 2}", "{3, 1}"])

    def test_the_result_is_a_copy(self):
        out = run_with(self.DEFS, '  m["z"] = 1\n  map[string, integer] :: n = passthrough(m)\n  n["y"] = 2\n  println(m)\n  println(n)')
        self.assertEqual(out, ["{z: 1}", "{z: 1, y: 2}"])

    def test_a_result_list_can_hold_a_map(self):
        self.assertEqual(run_with(self.DEFS, "  integer :: n\n  (m, n) = pair()\n  println(m, n)"), ["{a: 1} 7"])

    def test_a_result_can_be_used_without_a_variable(self):
        self.assertEqual(run_with(self.DEFS, '  println(has(make(), "a"), size(make()), keys(make()))'), ["true 2 [a, b]"])

    def test_the_result_must_be_the_declared_type(self):
        defs = "function bad() -> map[string, integer]\n  set[string] :: s\n  return s\nend function\n"
        with self.assertRaisesRegex(LangError, "expected a map\\[string, integer\\], got a set\\[string\\]"):
            run_with(defs, "  m = bad()")

    def test_it_cannot_be_applied_to_each_element_of_an_array(self):
        defs = "function one(integer :: n) -> set[integer]\n  set[integer] :: s\n  call add(s, n)\n  return s\nend function\n"
        with self.assertRaisesRegex(LangError, "cannot be applied to each element"):
            run_with(defs, "  println(one([1, 2]))")


class CallbackTests(unittest.TestCase):
    DEFS = ("function sizeof(map[string, integer] :: m) -> integer\n  return size(m)\nend function\n"
            "function apply(function(map[string, integer]) -> integer :: f, map[string, integer] :: m) -> integer\n  return f(m)\nend function\n"
            "function build() -> map[string, integer]\n  map[string, integer] :: m\n  m[\"a\"] = 1\n  return m\nend function\n"
            "function use_builder(function() -> map[string, integer] :: g) -> integer\n  map[string, integer] :: m\n  m = g()\n  return size(m)\nend function\n")

    def test_a_callback_can_take_a_map(self):
        self.assertEqual(run_with(self.DEFS, '  m["a"] = 1\n  m["b"] = 2\n  println(apply(sizeof, m))'), ["2"])

    def test_a_callback_can_return_a_map(self):
        self.assertEqual(run_with(self.DEFS, "  println(use_builder(build))"), ["1"])

    def test_the_signature_must_match(self):
        defs = self.DEFS + "function other(set[string] :: s) -> integer\n  return 1\nend function\n"
        with self.assertRaisesRegex(LangError, "is function"):
            run_with(defs, "  println(apply(other, m))")


class FieldTests(unittest.TestCase):
    def test_a_field_starts_empty_and_is_used_with_brackets(self):
        out = run_with(BOOK, '  book :: b\n  b.stock["pen"] = 3\n  call add(b.tags, "office")\n'
                             '  println(b.stock["pen"], has(b.tags, "office"), size(b.stock))\n  println(b)')
        self.assertEqual(out, ["3 true 1", "book(pages=1, stock={pen: 3}, tags={office})"])

    def test_copying_a_record_copies_its_collections(self):
        out = run_with(BOOK, '  book :: b\n  b.stock["a"] = 1\n  book :: c = b\n  c.stock["z"] = 2\n  call add(c.tags, "t")\n'
                             '  println(size(b.stock), size(c.stock), size(b.tags), size(c.tags))')
        self.assertEqual(out, ["1 2 0 1"])

    def test_assigning_a_whole_field_copies(self):
        out = run_with(BOOK, '  book :: b\n  m["a"] = 1\n  b.stock = m\n  m["z"] = 2\n  println(size(b.stock), size(m))')
        self.assertEqual(out, ["1 2"])

    def test_the_constructor_copies_and_a_collection_field_may_be_left_out(self):
        out = run_with(BOOK, '  m["a"] = 1\n  book :: b = book(2, m)\n  m["b"] = 2\n  book :: c = book(3)\n'
                             '  book :: d = book(pages = 4)\n  println(b)\n  println(size(m), c.pages, d.pages, size(c.stock))')
        self.assertEqual(out, ["book(pages=2, stock={a: 1}, tags={})", "2 3 4 0"])

    def test_a_record_in_an_array_has_its_own_collections(self):
        out = run_with(BOOK, '  book[2] :: shelf\n  shelf[1].stock["a"] = 1\n  println(size(shelf[1].stock), size(shelf[2].stock))')
        self.assertEqual(out, ["1 0"])

    def test_an_inout_record_parameter_can_change_its_collections(self):
        defs = BOOK + "subroutine stock_up(inout book :: b)\n  @b.stock[\"x\"] = 9\n  call add(b.tags, \"t\")\nend subroutine\n"
        out = run_with(defs, "  book :: b\n  call stock_up(b)\n  println(b)")
        self.assertEqual(out, ["book(pages=1, stock={x: 9}, tags={t})"])

    def test_an_out_record_starts_with_empty_collections(self):
        defs = BOOK + "subroutine fresh(out book :: b)\n  @b.pages = 5\nend subroutine\n"
        out = run_with(defs, '  book :: b\n  b.stock["old"] = 1\n  call fresh(b)\n  println(b)')
        self.assertEqual(out, ["book(pages=5, stock={}, tags={})"])

    def test_a_field_can_be_passed_to_a_procedure(self):
        defs = (BOOK + "function sizeof(map[string, integer] :: m) -> integer\n  return size(m)\nend function\n"
                "subroutine put(inout map[string, integer] :: m, string :: k, integer :: v)\n  @m[k] = v\nend subroutine\n")
        out = run_with(defs, '  book :: b\n  call put(b.stock, "q", 1)\n  println(sizeof(b.stock), b.stock)')
        self.assertEqual(out, ["1 {q: 1}"])

    def test_an_in_record_parameter_cannot_change_a_field(self):
        defs = BOOK + "function f(book :: b) -> integer\n  b.stock[\"x\"] = 1\n  return 1\nend function\n"
        with self.assertRaisesRegex(LangError, "in' parameter"):
            run_with(defs, "  book :: b\n  println(f(b))")

    def test_a_field_of_the_wrong_type_is_refused(self):
        with self.assertRaisesRegex(LangError, "expected a map\\[string, integer\\], got a set\\[string\\]"):
            run_with(BOOK, "  set[string] :: s\n  book :: b\n  b.stock = s")

    def test_a_set_field_is_not_indexed(self):
        with self.assertRaisesRegex(LangError, "is a set, which has no entries to index"):
            run_with(BOOK, '  book :: b\n  b.tags["x"] = 1')

    def test_a_record_function_can_use_its_collection(self):
        src = ("record counter\n  map[string, integer] :: n\n\n  function total(self) -> integer\n    integer :: t = 0\n"
               "    allocatable string[] :: ks\n    integer :: i\n    ks = keys(self.n)\n    do i = 1, size(ks)\n"
               "      t = t + self.n[ks[i]]\n    end do\n    return t\n  end function\n\n"
               "  subroutine bump(inout self, string :: k)\n    @self.n[k] = (get(self.n, k) ?? 0) + 1\n  end subroutine\nend record\n")
        out = run_bor(src + 'program t\n  counter :: c\n  call c.bump("a")\n  call c.bump("a")\n  call c.bump("b")\n  println(c.total(), c.n)\nend program\n')
        self.assertEqual(out, ["3 {a: 2, b: 1}"])


class CheckTests(unittest.TestCase):
    SRC = (BOOK + "function f(map[string, integer] :: m, book :: b) -> integer\n  return m[\"a\"] + size(b.stock) + b.stock[\"q\"]\nend function\n"
           "program t\n  book :: b\n  call add(b.tags, \"x\")\n  println(b)\nend program\n")

    def test_check_accepts_them(self):
        self.assertEqual(check_errors(self.SRC), [])

    def test_check_asks_for_brackets_on_a_parameter(self):
        src = self.SRC.replace('m["a"]', 'm("a")')
        self.assertTrue(any("index with brackets" in e[2] for e in check_errors(src)))

    def test_printing_and_parsing_round_trip(self):
        once = to_source(parse(self.SRC))
        self.assertEqual(to_source(parse(once)), once)
        self.assertIn("map[string, integer] :: stock", once)
        self.assertIn("function f(map[string, integer] :: m, book :: b) -> integer", once)


class EditorTests(unittest.TestCase):
    """The language server reads declarations as text: hover, go to definition and completion work on maps."""
    SRC = 'program t\n  map[string, integer] :: age\n  set[string] :: seen\n  age["a"] = 1\n  println(age)\nend program\n'

    def ask(self, method, line, character):
        from test_lsp import talk, open_doc, URI
        replies, _ = talk({"id": 1, "method": "initialize", "params": {}}, open_doc(self.SRC),
                          {"id": 2, "method": "textDocument/" + method,
                           "params": {"textDocument": {"uri": URI}, "position": {"line": line, "character": character}}})
        (reply,) = [r for r in replies if r.get("id") == 2]
        self.assertNotIn("error", reply)
        return reply["result"]

    def test_hover_shows_the_type(self):
        self.assertIn("map[string, integer] :: age", self.ask("hover", 4, 11)["contents"]["value"])

    def test_definition(self):
        self.assertEqual(self.ask("definition", 4, 11)["range"]["start"]["line"], 1)

    def test_completion_lists_them_with_their_types(self):
        items = {i["label"]: i.get("detail") for i in self.ask("completion", 3, 2)["items"]}
        self.assertEqual((items["age"], items["seen"]), ("map[string, integer]", "set[string]"))

    def test_the_server_reports_no_errors(self):
        from test_lsp import talk, open_doc
        replies, _ = talk({"id": 1, "method": "initialize", "params": {}}, open_doc(self.SRC))
        diags = [r for r in replies if r.get("method") == "textDocument/publishDiagnostics"]
        self.assertEqual(diags[0]["params"]["diagnostics"], [])


if __name__ == "__main__":
    unittest.main()
