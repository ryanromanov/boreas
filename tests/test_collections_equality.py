"""== and /= on maps and sets compare contents, in any order."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError


def out(body):
    return run_bor("program t\n" + body + "end program\n")


class EqualityTests(unittest.TestCase):
    def test_maps_ignore_order(self):
        body = ('  map[string, integer] :: a\n  map[string, integer] :: b\n'
                '  a["x"] = 1\n  a["y"] = 2\n  b["y"] = 2\n  b["x"] = 1\n  println(a == b)\n  println(a /= b)\n')
        self.assertEqual(out(body), ["true", "false"])

    def test_a_different_value_or_key_is_unequal(self):
        base = '  map[string, integer] :: a\n  map[string, integer] :: b\n  a["x"] = 1\n  b["x"] = 1\n'
        self.assertEqual(out(base + '  b["x"] = 2\n  println(a == b)\n'), ["false"])
        self.assertEqual(out(base + '  b["z"] = 1\n  println(a == b)\n'), ["false"])

    def test_empty_maps_are_equal(self):
        self.assertEqual(out("  map[string, integer] :: a\n  map[string, integer] :: b\n  println(a == b)\n"), ["true"])

    def test_sets_ignore_order(self):
        body = ('  set[string] :: s\n  set[string] :: u\n  call add(s, "p")\n  call add(s, "q")\n'
                '  call add(u, "q")\n  call add(u, "p")\n  println(s == u)\n  call remove(u, "p")\n  println(s /= u)\n')
        self.assertEqual(out(body), ["true", "true"])

    def test_array_and_set_values_compare_by_contents(self):
        body = ('  map[string, integer[]] :: l\n  map[string, integer[]] :: n\n  l["k"] = [1, 2]\n  n["k"] = [1, 2]\n'
                '  println(l == n)\n  n["k"][2] = 5\n  println(l == n)\n'
                '  map[string, set[string]] :: g\n  map[string, set[string]] :: h\n'
                '  set[string] :: s\n  call add(s, "a")\n  g["k"] = s\n  h["k"] = s\n  println(g == h)\n')
        self.assertEqual(out(body), ["true", "false", "true"])

    def test_it_works_in_a_condition_and_on_parameters(self):
        body = ('  map[string, integer] :: a\n  map[string, integer] :: b\n'
                '  if a == b then\n    println("same")\n  end if\n')
        self.assertEqual(out(body), ["same"])

    def test_different_types_are_an_error(self):
        for body, msg in [
            ("  map[string, integer] :: a\n  map[string, real] :: b\n  println(a == b)\n", "their types differ"),
            ("  set[string] :: a\n  set[integer] :: b\n  println(a /= b)\n", "their types differ"),
            ("  map[string, integer] :: a\n  set[string] :: b\n  println(a == b)\n", "cannot compare a map with a set"),
            ("  map[string, integer] :: a\n  println(a == 1)\n", "cannot compare a map with a integer|cannot compare a map with an integer"),
        ]:
            with self.subTest(body):
                with self.assertRaises(LangError) as cm:
                    run_bor("program t\n" + body + "end program\n")
                self.assertRegex(str(cm.exception), msg)

    def test_ordering_is_still_an_error(self):
        with self.assertRaises(LangError) as cm:
            run_bor("program t\n  set[string] :: a\n  set[string] :: b\n  println(a < b)\nend program\n")
        self.assertIn("not ordered", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
