"""`boreas check` finds the misuse of functions with several results without running (D57)."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from boreas.cli import check_errors

DEFS = ("function divmod(integer :: a, integer :: b) -> (integer :: q, integer :: r)\n  return a / b, mod(a, b)\nend function\n"
        "function one(integer :: a) -> integer\n  return a\nend function\n")


def messages(body, defs=DEFS):
    return [(line, msg) for line, _, msg, _ in check_errors(defs + "program t\n  integer :: x\n  integer :: y\n"
                                                              + body + "\nend program\n")]


class MisuseTests(unittest.TestCase):
    LINE = DEFS.count("\n") + 4

    def test_a_correct_unpacking_is_fine(self):
        self.assertEqual(messages("  (x, y) = divmod(7, 2)\n  (x, _) = divmod(7, 2)"), [])

    def test_a_single_assignment(self):
        self.assertEqual(messages("  x = divmod(7, 2)"),
                         [(self.LINE, "'divmod' returns 2 results; unpack them: (a, b) = divmod(...)")])

    def test_inside_an_expression_or_a_print(self):
        found = messages("  x = divmod(7, 2) + 1\n  println(divmod(7, 2))")
        self.assertEqual([line for line, _ in found], [self.LINE, self.LINE + 1])

    def test_as_a_statement(self):
        self.assertEqual(len(messages("  divmod(7, 2)")), 1)

    def test_forwarding_a_return(self):
        defs = DEFS + "function twice(integer :: a) -> (integer :: q, integer :: r)\n  return divmod(a, 1)\nend function\n"
        self.assertTrue(any("returns 2 results" in m for _, m in messages("", defs)))

    def test_the_wrong_number_of_targets(self):
        self.assertEqual(messages("  integer :: z\n  (x, y, z) = divmod(7, 2)"),
                         [(self.LINE + 1, "'divmod' returns 2 results, but 3 targets are listed")])

    def test_unpacking_a_function_with_one_result(self):
        found = messages("  (x, y) = one(3)")
        self.assertEqual(len(found), 1)
        self.assertIn("returns one result", found[0][1])

    def test_the_arguments_of_an_unpacked_call_are_still_checked(self):
        found = messages("  (x, y) = divmod(nope, 2)")
        self.assertEqual([m for _, m in found], ["'nope' is not declared"])

    def test_an_array_that_shares_the_name_is_not_a_call(self):
        self.assertEqual(messages("  integer[3] :: divmod = [1, 2, 3]\n  x = divmod[1]"), [])

    def test_a_function_in_a_module(self):
        src = ("module m\n  public function pair(integer :: a) -> (integer :: p, integer :: q)\n    return a, a\n  end function\n"
               "end module\nprogram t\n  use m: pair\n  integer :: x\n  x = pair(1)\nend program\n")
        self.assertTrue(any("returns 2 results" in e[2] for e in check_errors(src)))


if __name__ == "__main__":
    unittest.main()
