import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.lexer import LangError
from boreas.verify import same_output


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


X = "  integer[3] :: x = [1, 2, 3]\n"


class SpliceTests(unittest.TestCase):
    def test_a_one_dimensional_array_is_spliced_in(self):
        self.assertEqual(show(X + "  println([x, 4], [0, x], [x, 9, x], [x, x])"),
                         ["[1, 2, 3, 4] [0, 1, 2, 3] [1, 2, 3, 9, 1, 2, 3] [1, 2, 3, 1, 2, 3]"])

    def test_an_empty_array_adds_nothing(self):
        self.assertEqual(show(X + "  allocatable integer[] :: e\n  allocate e[0]\n  println([e, x], [x, e], size([e, e]))"),
                         ["[1, 2, 3] [1, 2, 3] 0"])

    def test_sections_and_function_results_splice(self):
        self.assertEqual(show(X + "  println([x[2:3], 0], [reshape([7, 8], [2]), x[1]])"), ["[2, 3, 0] [7, 8, 1]"])

    def test_the_result_is_a_new_array(self):
        self.assertEqual(show(X + "  integer[4] :: y\n  y = [x, 4]\n  y[1] = 99\n  println(x, y)"),
                         ["[1, 2, 3] [99, 2, 3, 4]"])

    def test_a_matrix_is_an_error(self):
        with self.assertRaises(LangError) as cm:
            show("  integer[2, 2] :: m = reshape([1, 2, 3, 4], [2, 2])\n  println([m, 5])")
        self.assertIn("one-dimensional arrays", str(cm.exception))


class SpliceConversionTests(unittest.TestCase):
    def test_fortran_constructors_with_arrays(self):
        src = ("program t\n  integer :: x(3), y(4), z(7)\n  x = [1, 2, 3]\n  y = [x, 4]\n  z = [x, 9, x]\n"
               "  print *, y\n  print *, z\n  print *, size([x, x])\nend program\n")
        text, findings = converted(src)
        self.assertEqual(todos(findings), [])
        got = run_bor(text)
        if GFORTRAN:
            want = fortran_output(src)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")


if __name__ == "__main__":
    unittest.main()
