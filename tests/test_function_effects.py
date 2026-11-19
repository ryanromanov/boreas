"""A function cannot change a module variable, not even by passing it on or calling a subroutine that does."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError

MOD = '''module counter
  public int :: hits
  public set[string] :: seen
  public subroutine bump(inout int :: n)
    @n = n + 1
  end subroutine
  public subroutine reset(out int :: n)
    @n = 0
  end subroutine
  public subroutine touch()
    @hits = hits + 1
  end subroutine
  public subroutine twice()
    call touch()
    call touch()
  end subroutine
  public subroutine note(string :: w)
    call add(@seen, w)
  end subroutine
  public subroutine quiet(inout int :: n)
    @n = n * 2
  end subroutine
%s
end module
program t
  use counter: hits, seen, bump, touch, twice, note, quiet, %s
  println(%s)
end program
'''


def run(body, name, call):
    return run_bor(MOD % (body, name, call))


class EffectTests(unittest.TestCase):
    def refused(self, body, message):
        with self.assertRaises(LangError) as cm:
            run(body, "f", "f()")
        self.assertIn(message, str(cm.exception))

    def test_passing_a_module_variable_to_inout_or_out(self):
        self.refused("  public function f() -> int\n    call bump(@hits)\n    return hits\n  end function",
                     "a function cannot change the module variable 'hits', which is passed to the inout parameter of 'bump'")
        self.refused("  public function f() -> int\n    call reset(@hits)\n    return hits\n  end function",
                     "passed to the out parameter of 'reset'")

    def test_calling_a_subroutine_that_changes_one(self):
        self.refused("  public function f() -> int\n    call touch()\n    return hits\n  end function",
                     "a function cannot call 'touch', which changes the module variable 'hits'")
        self.refused("  public function f() -> int\n    call note(\"a\")\n    return 1\n  end function",
                     "which changes the module variable 'seen'")

    def test_through_other_subroutines(self):
        self.refused("  public function f() -> int\n    call twice()\n    return hits\n  end function",
                     "a function cannot call 'twice', which changes the module variable 'hits'")

    def test_a_function_may_use_a_subroutine_on_its_own_variables(self):
        body = ("  public function f() -> int\n    int :: n = 3\n    call quiet(@n)\n    call bump(@n)\n"
                "    return n + hits\n  end function")
        self.assertEqual(run(body, "f", "f()"), ["7"])

    def test_a_subroutine_may_do_all_of_it(self):
        body = ("  public subroutine g()\n    call bump(@hits)\n    call twice()\n  end subroutine")
        src = (MOD % (body, "g", "hits")).replace("println(hits)", "call g()\n  println(hits)")
        self.assertEqual(run_bor(src), ["3"])

    def test_mutual_recursion_terminates(self):
        body = ("  public subroutine a(int :: n)\n    if n > 0 then\n      call b(n - 1)\n    end if\n  end subroutine\n"
                "  public subroutine b(int :: n)\n    call a(n)\n    call touch()\n  end subroutine\n"
                "  public function f() -> int\n    call a(1)\n    return 1\n  end function")
        self.refused(body, "which changes the module variable 'hits'")


if __name__ == "__main__":
    unittest.main()
