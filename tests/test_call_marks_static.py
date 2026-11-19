"""`boreas check` finds a missing `@` on a method call or a callback call without running the program."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from boreas.parser import parse
from boreas.names import undeclared_names
from boreas.interp import Interp

DEFS = """record counter
  int :: n = 0
  subroutine grow(inout self, int :: by)
    @self.n = self.n + by
  end subroutine
end record
interface grower
  subroutine grow(inout self, int :: by)
end interface
record tally is grower
  int :: n = 0
  subroutine grow(inout self, int :: by)
    @self.n = self.n + by
  end subroutine
end record
subroutine apply(subroutine(inout int) :: cb, inout int :: n)
  call cb(%s)
end subroutine
"""


def errors(body, mark="@n"):
    unit = parse((DEFS % mark) + "program t\n" + body + "end program\n")
    return [e[2] for e in undeclared_names(unit, Interp(unit, lambda s: None))]


class StaticMarkTests(unittest.TestCase):
    def test_a_record_method_is_checked_without_running(self):
        found = errors("  counter :: c\n  call c.grow(1)\n")
        self.assertTrue(any("'c' is changed by 'grow'" in e for e in found), found)

    def test_a_method_through_an_interface_is_checked_without_running(self):
        found = errors("  grower :: g = tally()\n  call g.grow(1)\n")
        self.assertTrue(any("'g' is changed by 'grow'" in e for e in found), found)

    def test_marked_calls_are_fine(self):
        self.assertEqual(errors("  grower :: g = tally()\n  call @g.grow(1)\n  counter :: c\n  call @c.grow(1)\n"), [])

    def test_a_callback_is_checked_without_running(self):
        found = errors("", mark="n")
        self.assertTrue(any("'n' is changed by 'cb'" in e for e in found), found)
        self.assertEqual(errors(""), [])


if __name__ == "__main__":
    unittest.main()
