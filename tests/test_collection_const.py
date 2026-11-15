"""const maps and sets: read, loop, copy and compare; never changed."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from boreas.lexer import LangError

SUBS = '''subroutine grow(inout map[string, integer] :: m)
  @m["z"] = 1
end subroutine
function total(map[string, integer] :: m) -> integer
  integer :: t = 0
  integer :: v
  string :: k
  do k, v in m
    t = t + v
  end do
  return t
end function
'''


def run(body, before=""):
    return run_bor(before + SUBS + "program t\n" + body + "end program\n")


def fails(case, body, message, before=""):
    with case.assertRaises(LangError) as cm:
        run(body, before)
    case.assertIn(message, str(cm.exception))


class ConstTests(unittest.TestCase):
    def test_a_const_collection_can_be_read(self):
        out = run('  const map[string, integer] :: m = {"a": 1, "b": 2}\n  integer :: v\n  string :: k\n'
                  '  println(m, m["a"], size(m), has(m, "a"), total(m), m == {"b": 2, "a": 1})\n'
                  '  do k, v in m\n    println(k, v)\n  end do\n')
        self.assertEqual(out, ["{a: 1, b: 2} 1 2 true 3 true", "a 1", "b 2"])

    def test_a_copy_of_one_can_be_changed(self):
        self.assertEqual(run('  const set[string] :: s = {"a"}\n  set[string] :: t = s\n  call add(t, "b")\n  println(s, t)\n'),
                         ["{a} {a, b}"])

    def test_it_can_start_from_another_collection(self):
        self.assertEqual(run('  set[string] :: s = {"a"}\n  const set[string] :: t = s\n  call add(s, "b")\n  println(s, t)\n'),
                         ["{a, b} {a}"])

    def test_it_cannot_be_changed(self):
        base = '  const map[string, integer] :: m = {"a": 1}\n  const set[string] :: s = {"a"}\n'
        fails(self, base + '  m["a"] = 2\n', "'m' is a constant and cannot be assigned")
        fails(self, base + '  m = {}\n', "'m' is a constant and cannot be assigned")
        fails(self, base + '  call add(s, "b")\n', "'s' is a constant and cannot be changed")
        fails(self, base + '  call remove(s, "a")\n', "'s' is a constant and cannot be changed")
        fails(self, base + '  call grow(m)\n', "'m' is a constant and cannot be passed as inout")

    def test_it_needs_a_value_and_cannot_be_allocatable(self):
        fails(self, '  const map[string, integer] :: m\n', "constant 'm' needs a value")
        fails(self, '  allocatable set[string] :: s\n', "a set cannot be 'allocatable'")

    def test_a_module_can_share_one(self):
        mod = ('module cfg\n  public const map[string, integer] :: limits = {"a": 1, "b": 2}\n'
               '  public const set[string] :: names = {"x", "y"}\nend module\n')
        body = '  use cfg: limits, names\n  println(limits, names, limits["b"])\n'
        self.assertEqual(run(body, mod), ["{a: 1, b: 2} {x, y} 2"])
        fails(self, '  use cfg: limits\n  call grow(limits)\n', "'limits' is a constant", mod)


if __name__ == "__main__":
    unittest.main()
