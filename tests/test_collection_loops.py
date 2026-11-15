"""`do key, value in m` over a map and `do x in s` over a set."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

DECLS = ("  map[string, integer] :: age\n  set[string] :: seen\n"
         "  string :: name\n  integer :: n\n  integer :: total\n")


def show(body, decls=DECLS):
    return run_bor("program t\n" + decls + body + "\nend program\n")


def fails(test, body, message, decls=DECLS):
    with test.assertRaises(LangError) as cm:
        show(body, decls)
    test.assertIn(message, str(cm.exception))


class LoopTests(unittest.TestCase):
    def test_map_in_insertion_order(self):
        out = show('  age["bob"] = 40\n  age["ann"] = 31\n  do name, n in age\n    println(name, n)\n  end do')
        self.assertEqual(out, ["bob 40", "ann 31"])

    def test_set_in_insertion_order(self):
        out = show('  call add(seen, "y")\n  call add(seen, "x")\n  do name in seen\n    println(name)\n  end do')
        self.assertEqual(out, ["y", "x"])

    def test_empty_collection_runs_zero_times(self):
        out = show('  do name, n in age\n    println(name)\n  end do\n  println("done")')
        self.assertEqual(out, ["done"])

    def test_sum_the_values(self):
        out = show('  age["a"] = 1\n  age["b"] = 2\n  total = 0\n  do name, n in age\n    total = total + n\n  end do\n  println(total)')
        self.assertEqual(out, ["3"])

    def test_body_can_change_the_collection(self):
        # the loop walks a snapshot, so these additions and removals do not disturb it
        out = show('  call add(seen, "x")\n  call add(seen, "y")\n  do name in seen\n    call add(seen, name + "!")\n'
                   '    call remove(seen, name)\n  end do\n  println(size(seen))')
        self.assertEqual(out, ["2"])

    def test_body_can_update_the_map(self):
        out = show('  age["a"] = 1\n  age["b"] = 2\n  do name, n in age\n    age[name] = n * 10\n  end do\n  println(age)')
        self.assertEqual(out, ["{a: 10, b: 20}"])

    def test_exit_and_cycle(self):
        out = show('  age["a"] = 1\n  age["b"] = 2\n  age["c"] = 3\n  do name, n in age\n'
                   '    if n == 1 then\n      cycle\n    end if\n    if n == 3 then\n      exit\n    end if\n    println(name)\n  end do')
        self.assertEqual(out, ["b"])

    def test_labels(self):
        out = show('  age["a"] = 1\n  age["b"] = 2\n  call add(seen, "x")\n  call add(seen, "y")\n'
                   '  outer: do name, n in age\n    do name in seen\n      println(n, name)\n      exit outer\n    end do\n  end do outer')
        self.assertEqual(out, ["1 x"])

    def test_nested_with_a_counting_loop(self):
        out = show('  age["a"] = 2\n  do name, n in age\n    do total = 1, n\n      println(name, total)\n    end do\n  end do')
        self.assertEqual(out, ["a 1", "a 2"])

    def test_a_field(self):
        src = ("record shelf\n  map[string, integer] :: stock\nend record\n"
               "program t\n  shelf :: s\n  string :: k\n  integer :: v\n  s.stock[\"pen\"] = 3\n"
               "  do k, v in s.stock\n    println(k, v)\n  end do\nend program\n")
        self.assertEqual(run_bor(src), ["pen 3"])

    def test_a_parameter_and_a_result(self):
        src = ("function total(map[string, integer] :: m) -> integer\n  string :: k\n  integer :: v\n  integer :: t\n"
               "  t = 0\n  do k, v in m\n    t = t + v\n  end do\n  return t\nend function\n"
               "program t\n  map[string, integer] :: m\n  m[\"a\"] = 4\n  m[\"b\"] = 5\n  println(total(m))\nend program\n")
        self.assertEqual(run_bor(src), ["9"])


class ArrayLoopTests(unittest.TestCase):
    def test_a_literal(self):
        out = show('  do n in [3, 1, 2]\n    println(n)\n  end do')
        self.assertEqual(out, ["3", "1", "2"])

    def test_keys_and_values(self):
        out = show('  age["a"] = 1\n  age["b"] = 2\n  do name in keys(age)\n    println(name)\n  end do\n'
                   '  total = 0\n  do n in values(age)\n    total = total + n\n  end do\n  println(total)')
        self.assertEqual(out, ["a", "b", "3"])

    def test_a_matrix_is_column_major(self):
        out = show('  integer[2, 2] :: g = reshape([1, 2, 3, 4], [2, 2])\n  do n in g\n    println(n)\n  end do')
        self.assertEqual(out, ["1", "2", "3", "4"])

    def test_an_empty_array(self):
        out = show('  allocatable integer[] :: e\n  allocate e[0]\n  do n in e\n    println(n)\n  end do\n  println("done")')
        self.assertEqual(out, ["done"])

    def test_the_loop_walks_a_snapshot(self):
        out = show('  integer[3] :: a = [1, 2, 3]\n  do n in a\n    a[3] = 0\n    println(n)\n  end do')
        self.assertEqual(out, ["1", "2", "3"])

    def test_a_string_array_and_exit(self):
        out = show('  string[3] :: w = ["a", "b", "c"]\n  do name in w\n    if name == "b" then\n      exit\n    end if\n    println(name)\n  end do')
        self.assertEqual(out, ["a"])


class ErrorTests(unittest.TestCase):
    def test_a_map_needs_two_variables(self):
        fails(self, '  do name in age\n    println(name)\n  end do', "a map loop takes two variables")

    def test_a_set_needs_one_variable(self):
        fails(self, '  do name, n in seen\n    println(name)\n  end do', "a set loop takes one variable")

    def test_an_array_needs_one_variable(self):
        fails(self, '  do name, n in [1, 2, 3]\n    println(n)\n  end do', "an array loop takes one variable")

    def test_a_scalar_is_not_looped(self):
        fails(self, '  do n in total\n    println(n)\n  end do', "loops over a map, a set or an array")

    def test_a_wrong_variable_type(self):
        fails(self, '  age["a"] = 1\n  do n, name in age\n    println(n)\n  end do',
              "loop variable 'n' takes the key of the map, but cannot assign string to integer")

    def test_a_loop_variable_must_be_declared(self):
        src = "program t\n  map[string, integer] :: m\n  do k, v in m\n    println(k)\n  end do\nend program\n"
        errors = check_errors(src)
        self.assertTrue(any("'k' is not declared" in e[2] for e in errors), errors)
        self.assertTrue(any("'v' is not declared" in e[2] for e in errors), errors)

    def test_a_parameter_cannot_be_a_loop_variable(self):
        src = ("subroutine s(in set[string] :: x, in string :: k)\n  do k in x\n    println(k)\n  end do\nend subroutine\n"
               "program t\nend program\n")
        with self.assertRaises(LangError) as cm:
            parse(src)
        self.assertIn("cannot be a parameter", str(cm.exception))

    def test_the_ordinary_loop_still_works(self):
        out = show('  do n = 1, 3\n    total = n\n  end do\n  println(total)')
        self.assertEqual(out, ["3"])


class ToolTests(unittest.TestCase):
    SRC = ("program t\n  map[string, integer] :: m\n  string :: k\n  integer :: v\n"
           "  outer: do k, v in m\n    println(k)\n  end do outer\nend program\n")

    def test_prints_back(self):
        again = to_source(parse(self.SRC))
        self.assertIn("outer: do k, v in m", again)
        self.assertEqual(to_source(parse(again)), again)

    def test_check_is_clean(self):
        self.assertEqual(check_errors(self.SRC), [])


if __name__ == "__main__":
    unittest.main()
