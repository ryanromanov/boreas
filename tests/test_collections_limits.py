"""examples/collections_limits.bor quotes the errors Boreas gives for what collections cannot do.
Each snippet here is run, and the message it gives must be the one the example file quotes."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "collections_limits.bor")

M = "  map[string, integer] :: m\n"
A_B = "  map[string, integer] :: a\n  map[string, integer] :: b\n"
CASES = [
    ("  const map[string, integer] :: m\n", "a map cannot be 'const' or 'allocatable': it starts empty and grows by itself"),
    ("  map[string, integer] :: ms[3]\n", "an array of maps is not supported: a map is declared on its own"),
    ("  map[string, integer]? :: m\n", "a map cannot be optional: it is always there, and may be empty"),
    ("  map[real, integer] :: m\n", "a map's key must be integer or string, not real"),
    ("  map[string, map[string, integer]] :: m\n", "a map's value cannot be another map; use a set or an array as the value"),
    ("  map[string, integer?] :: m\n", "a map's value cannot be optional: get(m, key) already gives null for a missing key"),
    ("  set[set[string]] :: s\n", "a set's member must be a single value: arrays, optional values and other collections are not supported here"),
    ("  map[string, integer[3]] :: m\n", "a map's array value is written without sizes, as in 'integer[]' or 'real[,]'"),
    (A_B + "  logical :: same\n  same = a < b\n", "a map cannot be used with '<'; maps and sets can be compared with == and /=, not ordered"),
    ("  map[string, integer] :: a\n  map[string, real] :: b\n  logical :: same\n  same = a == b\n",
     "cannot compare a map with a map: their types differ"),
    (A_B + "  b = a + a\n", "a map cannot be used with '+'"),
    ("  set[string] :: s\n  println(s[\"x\"])\n", "'s' is a set, which has no entries to index; test a member with has(s, x)"),
    (M + "  call add(m, \"x\")\n", "add needs a set, but 'm' is a map; a map gets entries with m[key] = value"),
    ("  integer[3] :: a = [1, 2, 3]\n  integer :: x\n  x = a[1][2]\n",
     "'a' is not a map, so it takes one subscript list: write a[i, j] for a matrix"),
    (M + "  integer :: x\n  m[\"a\"] = 1\n  x = m[\"a\"][2]\n",
     "an entry of 'm' is one integer, not an array, so it takes no second subscript"),
    ("  map[string, set[string]] :: g\n  println(values(g))\n",
     "values() gives an array of single values, but this map's values are sets; loop with do k in keys(m), or read one entry at a time"),
    (M + "  println(m[\"x\"])\n", "the map 'm' has no key \"x\" (test with has(m, key), or use get(m, key) ?? default)"),
    (M + "  call remove(m, \"x\")\n", "cannot remove \"x\": 'm' has no such key (test with has first)"),
    (M + "  m[1] = 2\n", "the map 'm' has string keys, got integer"),
    (M + "  m[\"a\"] = \"x\"\n", "a value of 'm': cannot assign string to integer"),
    ("  map[string, set[string]] :: g\n  call add(g[\"a\"], \"x\")\n",
     "the map 'g' has no key \"a\" (test with has(g, key), or use get(g, key) ?? default)"),
    ("  integer :: i\n  integer :: j\n  do i, j in [1, 2]\n    println(i)\n  end do\n",
     "an array loop takes one variable, like 'do x in a' (for the position as well, use do i = 1, size(a))"),
    (M + "  string :: k\n  do k in m\n    println(k)\n  end do\n",
     "a map loop takes two variables, like 'do key, value in m' (to loop over the keys alone, use a set or keys(m))"),
]


class LimitsExampleTests(unittest.TestCase):
    def test_every_quoted_message_is_the_real_one(self):
        text = open(EXAMPLE).read()
        for body, message in CASES:
            with self.subTest(message):
                with self.assertRaises(LangError) as cm:
                    run_bor("program t\n" + body + "end program\n")
                self.assertIn(message, str(cm.exception))
                self.assertIn("!  error: " + message, text)

    def test_a_record_field_has_no_default(self):
        src = "record r\n  map[string, integer] :: stock = other\nend record\nprogram t\nend program\n"
        with self.assertRaises(LangError) as cm:
            run_bor(src)
        self.assertIn("a map field starts empty, so it has no default value", str(cm.exception))
        self.assertIn("!  error: a map field starts empty, so it has no default value", open(EXAMPLE).read())

    def test_the_program_at_the_bottom_runs(self):
        out = run_bor(open(EXAMPLE).read())
        self.assertEqual(out, ["true", "{fruit: {apple, pear}}", "{a: [10, 99]} 10", "0"])


if __name__ == "__main__":
    unittest.main()
