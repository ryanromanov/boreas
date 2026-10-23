import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fortis import run_source
from fortis.lexer import LangError


def run(body, funcs=""):
    out = []
    run_source(f"{funcs}\nprogram t\n{body}\nend program t\n", out.append)
    return out


class Tests(unittest.TestCase):
    def test_arith_and_int_division(self):
        self.assertEqual(run("print 7 / 2, -7 / 2, 2 ** 3 ** 2, 7.0 / 2"), ["3 -3 512 3.5"])

    def test_case_insensitive(self):
        self.assertEqual(run("INTEGER :: X = 4\nPrint x"), ["4"])

    def test_whole_array_ops(self):
        self.assertEqual(run("integer :: a(3) = [1,2,3]\nprint a * a + 1"), ["[2, 5, 10]"])

    def test_function(self):
        f = "function add(integer :: a, integer :: b) -> integer\n return a + b\nend function"
        self.assertEqual(run("print add(2, 3)", f), ["5"])

    def test_undeclared_variable(self):
        with self.assertRaisesRegex(LangError, "not declared"):
            run("x = 1")

    def test_no_implicit_real_to_integer(self):
        with self.assertRaisesRegex(LangError, "cannot assign real to integer"):
            run("integer :: n\nn = 2.5")

    def test_bounds_checked(self):
        with self.assertRaisesRegex(LangError, "out of bounds"):
            run("integer :: a(3)\na(4) = 1")

    def test_non_logical_condition(self):
        with self.assertRaisesRegex(LangError, "must be logical"):
            run("integer :: n = 1\nif n then\nprint 1\nend if")


if __name__ == "__main__":
    unittest.main()
