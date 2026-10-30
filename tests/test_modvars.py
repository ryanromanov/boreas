import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

RNG = ("module rng\n"
       "  public integer :: seed = 7\n"
       "  integer :: calls\n"
       "  public subroutine reseed(integer :: s)\n"
       "    update seed = s\n"
       "    update calls = 0\n"
       "  end subroutine\n"
       "  public subroutine next(out integer :: k)\n"
       "    update seed = mod(seed * 3, 11) + 1\n"
       "    update calls = calls + 1\n"
       "    update k = seed\n"
       "  end subroutine\n"
       "  public function count() -> integer\n"
       "    return calls\n"
       "  end function\n"
       "end module\n")


def run(body, extra=RNG, stdin=None):
    out = []
    Interp(parse(f"{extra}program t\n{body}\nend program\n"), out.append, stdin=stdin).run()
    return out


def fails(test, body, message, extra=RNG):
    with test.assertRaises(LangError) as cm:
        run(body, extra)
    test.assertIn(message, str(cm.exception))


class ModuleVariableTests(unittest.TestCase):
    def test_state_lives_between_calls(self):
        body = "  use rng: next, count\n  integer :: k\n  call next(k)\n  call next(k)\n  print k, count()"
        self.assertEqual(run(body), ["1 2"])

    def test_initial_value_and_reading_an_import(self):
        self.assertEqual(run("  use rng: seed\n  print seed"), ["7"])

    def test_an_import_sees_changes(self):
        body = "  use rng: seed, reseed\n  print seed\n  call reseed(40)\n  print seed"
        self.assertEqual(run(body), ["7", "40"])

    def test_the_program_may_update_an_imported_variable(self):
        self.assertEqual(run("  use rng: seed\n  update seed = seed + 1\n  print seed"), ["8"])

    def test_writing_needs_update(self):
        fails(self, "  use rng: seed\n  seed = 1", "must be used exactly")
        fails(self, "", "other code sees this write",
              "module m\n  integer :: n\n  subroutine s()\n    n = 1\n  end subroutine\nend module\n")

    def test_update_on_a_local_is_still_wrong(self):
        fails(self, "  integer :: k\n  update k = 1", "'update' is for out and inout parameters and module variables")

    def test_private_variable_cannot_be_imported(self):
        fails(self, "  use rng: calls\n  print calls", "private to module")

    def test_a_function_cannot_change_a_module_variable(self):
        fails(self, "", "a function cannot change the module variable",
              "module m\n  integer :: n\n  function f() -> integer\n    update n = 1\n    return n\n  end function\nend module\n")

    def test_arrays_and_records(self):
        src = ("module bank\n  record acct\n    integer :: total\n  end record\n"
               "  public integer :: slots(3)\n  public acct :: main\n"
               "  public subroutine put(integer :: i)\n    update slots(i) = i * 10\n"
               "    update main.total = main.total + i\n  end subroutine\nend module\n")
        self.assertEqual(run("  use bank: slots, main, put\n  call put(2)\n  call put(3)\n  print slots, main.total", src),
                         ["[0, 20, 30] 5"])

    def test_read_into_a_module_variable(self):
        src = "module m\n  public integer :: n\nend module\n"
        self.assertEqual(run("  use m: n\n  update read n\n  print n", src, "41\n"), ["41"])
        fails(self, "  use m: n\n  read n", "'update' must be used exactly", src)

    def test_a_local_cannot_reuse_the_name(self):
        fails(self, "  use rng: seed\n  integer :: seed", "already")

    def test_constants_stay_constant(self):
        src = "module m\n  public const integer :: k = 3\nend module\n"
        fails(self, "  use m: k\n  k = 4", "constant", src)
        fails(self, "  use m: k\n  update k = 4", "must be used exactly", src)

    def test_round_trip(self):
        src = to_source(parse(RNG + "program t\n  use rng: seed\n  update seed = 1\nend program\n"))
        self.assertEqual(to_source(parse(src)), src)
        self.assertIn("public integer :: seed = 7", src)


if __name__ == "__main__":
    unittest.main()
