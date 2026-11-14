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
        fails(self, "  map[string, real] :: b = age", "so it cannot hold a string to integer map")
        fails(self, "  age = seen", "so it cannot hold a set of string")


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

    def test_only_local_variables_so_far(self):
        for src in ["function f(map[string, integer] :: m) -> integer\n  return 1\nend function\nprogram t\nend program\n",
                    "function f() -> set[integer]\n  return 1\nend function\nprogram t\nend program\n",
                    "record r\n  set[string] :: s\nend record\nprogram t\nend program\n",
                    "module m\n  public map[string, integer] :: age\nend module\nprogram t\nend program\n",
                    "module m\n  map[string, integer] :: age\nend module\nprogram t\nend program\n"]:
            with self.subTest(src.splitlines()[0]):
                self.refused(src, "can only be a local variable so far")

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
