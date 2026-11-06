import unittest

from boreas.fortran import convert
from boreas.parser import parse


def conv(src, name="s.f90"):
    text, findings = convert(src, name)
    return text, [m for _, k, m in findings if k == "todo"]


class LoopCounterTests(unittest.TestCase):
    def test_return_inside_the_loop_copies_the_counter(self):
        text, todos = conv("""subroutine s(a, n, info)
  integer, intent(in) :: n
  real, intent(in) :: a(n)
  integer, intent(out) :: info
  do info = 1, n
    if (a(info) < 0.0) return
  end do
  info = 0
end subroutine
""")
        self.assertEqual(todos, [])
        self.assertIn("do info_i = 1, n", text)
        self.assertIn("update info = info_i\n    return", text.replace("      ", "    "))
        self.assertNotIn("update info = max", text)       # `info = 0` follows, so no final value
        parse(text + "\nprogram p\nend program p\n")

    def test_final_value_is_set_when_it_can_be_seen(self):
        text, todos = conv("""subroutine s(n, info)
  integer, intent(in) :: n
  integer, intent(out) :: info
  do info = 1, n
  end do
end subroutine
""")
        self.assertEqual(todos, [])
        self.assertIn("update info = max(1, n + 1)", text)

    def test_counting_down_and_constant_bound(self):
        text, _ = conv("""subroutine s(n, info)
  integer, intent(in) :: n
  integer, intent(out) :: info
  do info = n, 1, -1
  end do
end subroutine
""")
        self.assertIn("update info = min(n, 0)", text)

    def test_exit_with_a_visible_final_value_is_a_todo(self):
        text, todos = conv("""subroutine s(n, info)
  integer, intent(in) :: n
  integer, intent(out) :: info
  do info = 1, n
    if (info > 3) exit
  end do
end subroutine
""")
        self.assertTrue(any("final value of 'info'" in m for m in todos), todos)
        self.assertIn("do info_i = 1, n", text)

    def test_two_loops_share_one_counter(self):
        text, _ = conv("""subroutine s(n, info)
  integer, intent(in) :: n
  integer, intent(out) :: info
  do info = 1, n
    if (info == 2) return
  end do
  do info = 1, n
    if (info == 3) return
  end do
  info = 0
end subroutine
""")
        self.assertEqual(text.count("integer :: info_i"), 1)


if __name__ == "__main__":
    unittest.main()
