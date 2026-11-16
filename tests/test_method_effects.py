"""A function cannot change a module variable through a record's subroutine either."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError

MOD = '''module box
  public integer :: hits
  public record tally
    integer :: n = 0
    subroutine grow(inout self, integer :: by)
      @self.n = self.n + by
    end subroutine
    subroutine count(inout self)
      @hits = hits + 1
    end subroutine
    subroutine show(self)
      println(self.n)
    end subroutine
  end record
  public tally :: total
%s
end module
program t
  use box: hits, tally, total, f
  println(f())
end program
'''


def run(body):
    return run_bor(MOD % body)


class MethodEffectTests(unittest.TestCase):
    def refused(self, body, message):
        with self.assertRaises(LangError) as cm:
            run(body)
        self.assertIn(message, str(cm.exception))

    def test_a_function_cannot_grow_a_module_record(self):
        self.refused("  public function f() -> integer\n    call @total.grow(1)\n    return 1\n  end function",
                     "cannot change the module variable 'total'")

    def test_a_function_cannot_call_a_method_that_changes_a_module_variable(self):
        self.refused("  public function f() -> integer\n    tally :: t\n    call @t.count()\n    return 1\n  end function",
                     "cannot call 'count', which changes the module variable 'hits'")

    def test_a_function_can_use_a_local_record(self):
        self.assertEqual(run("  public function f() -> integer\n    tally :: t\n    call @t.grow(2)\n    call t.show()\n    return t.n\n  end function"),
                         ["2", "2"])

    def test_a_subroutine_can_grow_a_module_record(self):
        src = MOD.replace("  println(f())", "  call @total.grow(3)\n  call total.show()") % "  public function f() -> integer\n    return 1\n  end function"
        self.assertEqual(run_bor(src), ["3"])


if __name__ == "__main__":
    unittest.main()
