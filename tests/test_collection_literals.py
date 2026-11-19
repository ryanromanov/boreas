"""Map and set literals: {k: v}, {a, b} and {}; they take the type of what they are stored in."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def out(body, before=""):
    return run_bor(before + "program t\n" + body + "end program\n")


def fails(case, body, message, before=""):
    with case.assertRaises(LangError) as cm:
        out(body, before)
    case.assertIn(message, str(cm.exception))


class LiteralTests(unittest.TestCase):
    def test_map_set_and_empty(self):
        self.assertEqual(out('  map[string, int] :: a\n  set[string] :: s\n  a = {"x": 1, "y": 2}\n  s = {"p", "q"}\n'
                             '  println(a, s)\n  a = {}\n  s = {}\n  println(size(a), size(s))\n'),
                         ["{x: 1, y: 2} {p, q}", "0 0"])

    def test_a_declaration_can_start_from_one(self):
        self.assertEqual(out('  map[string, real] :: r = {"pi": 3}\n  println(r)\n'), ["{pi: 3.0}"])

    def test_values_are_converted_like_an_assignment(self):
        fails(self, '  map[string, int] :: a\n  a = {"x": "no"}\n', "cannot assign string to int")
        fails(self, '  map[string, int] :: a\n  a = {1: 1}\n', "has string keys, got int")
        fails(self, '  set[string] :: s\n  s = {1}\n', "has string members, got int")

    def test_nested_set_and_array_values(self):
        self.assertEqual(out('  map[string, set[string]] :: g\n  map[string, int[]] :: l\n'
                             '  g = {"fruit": {"apple", "pear"}, "none": {}}\n  l = {"k": [1, 2, 3]}\n  println(g)\n  println(l)\n'
                             '  g["x"] = {"a"}\n  println(size(g))\n'),
                         ["{fruit: {apple, pear}, none: {}}", "{k: [1, 2, 3]}", "3"])

    def test_arguments_results_and_comparisons(self):
        before = ("module m\n  public subroutine show(in map[string, real] :: x)\n    println(x)\n  end subroutine\n"
                  "  public function mk() -> set[string]\n    return {\"p\", \"q\"}\n  end function\nend module\n")
        src = before + ('program t\n  use m: show, mk\n  map[string, int] :: a\n  set[string] :: s\n  s = mk()\n'
                        '  call show({"z": 1})\n  println(s, a == {}, s == {"q", "p"}, s /= {"p"})\nend program\n')
        self.assertEqual(run_bor(src), ["{z: 1.0}", "{p, q} true true true"])

    def test_a_literal_in_a_loop_or_on_its_own(self):
        self.assertEqual(out('  string :: k\n  do k in {"u", "v"}\n    println(k)\n  end do\n  println({"a": 1})\n'),
                         ["u", "v", "{a: 1}"])

    def test_the_wrong_kind_of_literal(self):
        fails(self, '  map[string, int] :: a\n  a = {"p"}\n', "got a set literal; a map is written {key: value}")
        fails(self, '  set[string] :: s\n  s = {"p": 1}\n', "got a map literal; a set is written {a, b}")

    def test_a_key_listed_twice_and_bad_keys(self):
        fails(self, '  map[string, int] :: a\n  a = {"x": 1, "x": 2}\n', '"x" appears twice in this literal')
        fails(self, '  set[string] :: s\n  s = {"p", "p"}\n', '"p" appears twice in this literal')
        fails(self, '  set[string] :: s\n  s = {[1, 2]}\n', "must be a single integer or string")

    def test_mixing_the_two_forms_is_a_parse_error(self):
        fails(self, '  map[string, int] :: a\n  a = {"x": 1, "y"}\n', "expected ':'")
        fails(self, '  set[string] :: s\n  s = {"x", "y": 1}\n', "a set literal lists members only")

    def test_the_formatter_keeps_them(self):
        src = 'program t\n  map[string, int] :: a = {"x": 1, "y": 2}\n  set[string] :: s = {"p", "q"}\n  a = {}\nend program\n'
        self.assertEqual(parse(to_source(parse(src))), parse(src))


if __name__ == "__main__":
    unittest.main()
