import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.cli import run_source
from boreas.lexer import LangError


def run(body, extra=""):
    out = []
    run_source(f"{extra}program t\n{body}\nend program t\n", out.append)
    return out


def fails(test, body, message, extra=""):
    with test.assertRaisesRegex(LangError, message):
        run(body, extra)


P = "  allocatable string :: p(:)\n"


class SplitTests(unittest.TestCase):
    def test_split_cuts_at_exactly_the_separator(self):
        self.assertEqual(run(P + '  p = split("1.5,,3", ",")\n  println(size(p), p(1), p(3))\n  println(p(2) == "")'),
                         ["3 1.5 3", "true"])

    def test_split_on_a_blank_keeps_the_empty_pieces(self):
        self.assertEqual(run(P + '  p = split(" a  b ", " ")\n  println(size(p))'), ["5"])

    def test_the_separator_may_be_several_characters(self):
        self.assertEqual(run(P + '  p = split("a, b, c", ", ")\n  println(p)'), ["[a, b, c]"])

    def test_no_separator_gives_one_piece_and_an_empty_string_one_empty_piece(self):
        self.assertEqual(run(P + '  p = split("abc", ",")\n  println(size(p), p(1))'), ["1 abc"])
        self.assertEqual(run(P + '  p = split("", ",")\n  println(size(p))'), ["1"])

    def test_fields_treats_a_run_of_blanks_as_one_gap(self):
        self.assertEqual(run(P + '  p = fields("  1.5   2.0 3.7 ")\n  println(size(p), p)'), ["3 [1.5, 2.0, 3.7]"])
        self.assertEqual(run(P + '  p = fields("   ")\n  println(size(p))'), ["0"])

    def test_fields_with_extra_separators(self):
        self.assertEqual(run(P + '  p = fields("3, 4.5 ,,x;y", ",;")\n  println(size(p), p)'), ["4 [3, 4.5, x, y]"])
        self.assertEqual(run(P + '  p = fields("a,b c", "")\n  println(p)'), ["[a,b, c]"])
        fails(self, P + '  p = fields("a", 1)', "extra separators")

    def test_errors(self):
        fails(self, P + '  p = split("a", "")', "separator")
        fails(self, P + "  p = split(3, \",\")", "needs a string")
        fails(self, P + "  p = fields(3)", "needs a string")


class ToNumberTests(unittest.TestCase):
    def test_to_integer(self):
        self.assertEqual(run('  println(to_integer("42"), to_integer(" -7 "), to_integer("+3"))'), ["42 -7 3"])
        self.assertEqual(run('  println(to_integer("3.5") ?? -1, to_integer("") ?? -1, to_integer("x") ?? -1)'), ["-1 -1 -1"])

    def test_to_real(self):
        self.assertEqual(run('  println(to_real("2.5"), to_real("3"), to_real("1e3"), to_real("1d3"), to_real(" .5 "))'),
                         ["2.5 3.0 1000.0 1000.0 0.5"])
        self.assertEqual(run('  println(to_real("") ?? 0.0, to_real("abc") ?? 0.0, to_real("1.2.3") ?? 0.0)'), ["0.0 0.0 0.0"])

    def test_to_logical(self):
        self.assertEqual(run('  println(to_logical("T"), to_logical(".false."), to_logical(" true "), to_logical("F"))'),
                         ["true false true false"])
        self.assertEqual(run('  println(to_logical("yes") ?? false, to_logical("") == null)'), ["false true"])

    def test_the_result_is_null_when_the_text_is_not_a_number(self):
        self.assertEqual(run('  println(to_integer("x") == null, to_real("1") == null)'), ["true false"])

    def test_needs_a_string(self):
        fails(self, "  println(to_integer(3))", "needs a string")

    def test_a_row_of_numbers(self):
        body = P + '  p = fields("1.5 2.0 3.7")\n  println(to_real(p(1)) + to_real(p(2)) + to_real(p(3)))'
        self.assertEqual(run(body), ["7.2"])


class AllocateOnAssignmentTests(unittest.TestCase):
    D = "  allocatable integer :: a(:)\n"

    def test_assignment_allocates(self):
        self.assertEqual(run(self.D + "  a = [1, 2, 3]\n  println(size(a), a)"), ["3 [1, 2, 3]"])

    def test_assignment_resizes(self):
        self.assertEqual(run(self.D + "  a = [1, 2, 3]\n  a = [9]\n  println(size(a), a)"), ["1 [9]"])

    def test_elements_take_the_array_type(self):
        self.assertEqual(run("  allocatable real :: v(:)\n  v = [1, 2]\n  println(v)"), ["[1.0, 2.0]"])
        fails(self, self.D + "  a = [1.5, 2.5]", "integer")

    def test_a_scalar_still_needs_an_allocated_array(self):
        fails(self, self.D + "  a = 1", "not allocated")
        self.assertEqual(run(self.D + "  allocate a(2)\n  a = 7\n  println(a)"), ["[7, 7]"])

    def test_the_rank_must_match(self):
        fails(self, self.D + "  a = reshape([1, 2, 3, 4], [2, 2])", "1 dimension")

    def test_two_dimensions(self):
        body = "  allocatable integer :: m(:, :)\n  m = reshape([1, 2, 3, 4, 5, 6], [2, 3])\n  println(shape(m))"
        self.assertEqual(run(body), ["[2, 3]"])

    def test_the_copy_is_independent(self):
        body = self.D + "  integer :: b(2) = [1, 2]\n  a = b\n  b(1) = 9\n  println(a)"
        self.assertEqual(run(body), ["[1, 2]"])

    def test_a_module_variable_needs_update(self):
        mod = "module m\n  public allocatable integer :: data(:)\n  public subroutine load()\n    {}\n  end subroutine\nend module\n"
        prog = "use m: data, load\ncall load()\nprintln(data)"
        self.assertEqual(run(prog, mod.format("update data = [1, 2]")), ["[1, 2]"])
        fails(self, prog, "update", mod.format("data = [1, 2]"))
