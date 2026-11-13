import unittest

from boreas.fortran import convert
from boreas.parser import parse

HEAD = ("subroutine s(a, c, n)\n  integer, intent(in) :: n\n  real, intent(in) :: a(n, n)\n"
        "  real, intent(out) :: c(n, n)\n  integer :: i, j\n")
MAIN = "\nprogram p\nend program p\n"


def conv(body):
    text, findings = convert(HEAD + body + "end subroutine\n", "s.f90")
    return text, [m for _, k, m in findings if k == "todo"]


class DoConcurrentTests(unittest.TestCase):
    def test_two_indices_become_nested_loops_with_a_note(self):
        text, todos = conv("  do concurrent (i = 1:n, j = 1:n)\n    c(i, j) = a(i, j)\n  end do\n")
        self.assertEqual(todos, [])
        self.assertIn("! was do concurrent", text)
        self.assertIn("  do i = 1, n\n    do j = 1, n\n      @c[i, j] = a[i, j]\n    end do\n  end do", text)
        parse(text + MAIN)

    def test_stride(self):
        text, _ = conv("  do concurrent (i = 1:n:2)\n    c(i, 1) = 0.0\n  end do\n")
        self.assertIn("do i = 1, n, 2", text)

    def test_mask_becomes_an_if(self):
        text, _ = conv("  do concurrent (i = 1:n, a(i, 1) > 0.0)\n    c(i, 1) = a(i, 1)\n  end do\n")
        self.assertIn("do i = 1, n\n    if a[i, 1] > 0.0 then\n      @c[i, 1] = a[i, 1]", text)

    def test_type_spec_in_the_header(self):
        text, todos = conv("  do concurrent (integer :: k = 1:n)\n    c(k, 1) = 0.0\n  end do\n")
        self.assertEqual(todos, [])
        self.assertIn("do k = 1, n", text)

    def test_cycle_in_the_body_stays_in_the_innermost_loop(self):
        text, _ = conv("  do concurrent (i = 1:n, j = 1:n)\n    if (a(i, j) < 0.0) cycle\n    c(i, j) = a(i, j)\n  end do\n")
        parse(text + MAIN)
        self.assertIn("cycle", text)

    def test_locality_clause_is_a_todo(self):
        text, todos = conv("  do concurrent (i = 1:n) local(j)\n    c(i, 1) = 1.0\n  end do\n")
        self.assertTrue(any("'local' clause" in m for m in todos), todos)


if __name__ == "__main__":
    unittest.main()
