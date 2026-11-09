import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from migrate_brackets import infer_ranks, migrate, migrate_program
from boreas.interp import Interp
from boreas.parser import parse


def lines(*ls):
    return "\n".join(ls) + "\n"


class MigrateTests(unittest.TestCase):
    def test_declarations(self):
        old = lines("real :: v(3) = [1.0, 2.0, 3.0]", "integer :: m(2, n + 1)", "const integer :: p(2) = [2, 3]",
                    "public integer :: slots(3)", "point :: pts(10)")
        new = lines("real[3] :: v = [1.0, 2.0, 3.0]", "integer[2, n + 1] :: m", "const integer[2] :: p = [2, 3]",
                    "public integer[3] :: slots", "point[10] :: pts")
        self.assertEqual(migrate(old, {"point"}), new)

    def test_allocatable_keeps_only_the_rank(self):
        old = lines("allocatable integer :: a(:)", "allocatable real :: m(:, :)", "public allocatable real :: c(:, :, :)")
        new = lines("allocatable integer[] :: a", "allocatable real[,] :: m", "public allocatable real[,,] :: c")
        self.assertEqual(migrate(old), new)

    def test_indexing_allocate_fields_and_strings(self):
        old = lines("allocate m(n, 2 * n)", "update allocate data(k)", "a(i, :) = b(1:2)", "t.tags(2) = pts(i).x(1)",
                    's(1:2) = "ab"', "println(a(3), s(2:3))")
        new = lines("allocate m[n, 2 * n]", "update allocate data[k]", "a[i, :] = b[1:2]", "t.tags[2] = pts[i].x[1]",
                    's[1:2] = "ab"', "println(a[3], s[2:3])")
        self.assertEqual(migrate(old), new)

    def test_calls_are_left_alone(self):
        old = lines("println(sqrt(x), max(a(1), 2), real(i), text(k))", "y = area(r) + f(3)", "call fill(a(1:2))",
                    "p = point(1.0, 2.0)", "if present(x) then", "x = out()")
        new = lines("println(sqrt(x), max(a[1], 2), real(i), text(k))", "y = area(r) + f(3)", "call fill(a[1:2])",
                    "p = point(1.0, 2.0)", "if present(x) then", "x = out()")
        self.assertEqual(migrate(old, {"area", "f", "point"}), new)

    def test_functions_defined_in_the_text_are_calls(self):
        old = lines("function area(real :: r) -> real", "  return r * r", "end function", "program t", "  println(area(2.0), b(1))",
                    "end program")
        self.assertIn("println(area(2.0), b[1])", migrate(old))

    def test_in_and_out_can_be_arrays_names(self):
        self.assertEqual(migrate("update out(i) = in(i)\n"), "update out[i] = in[i]\n")

    def test_parameter_ranks_come_from_how_the_function_uses_them(self):
        src = lines("function trace(real :: m) -> real", "  return m(1, 1)", "end function",
                    "function first(real :: v, string :: s) -> real", "  return v(1) + len(s(1:2))", "end function",
                    "program t", "end program t")
        self.assertEqual(infer_ranks(src), {("trace", "m"): "[,]", ("first", "v"): "[]"})
        new = migrate_program(src)
        self.assertIn("function trace(real[,] :: m) -> real", new)
        self.assertIn("function first(real[] :: v, string :: s) -> real", new)
        self.assertIn("return m[1, 1]", new)
        self.assertIn("len(s[1:2])", new)

    def test_parameter_ranks_the_program_shows_by_running(self):
        src = lines("function total(real :: v) -> real", "  return sum(v)", "end function",
                    "program t", "  real :: a(3) = [1.0, 2.0, 3.0]", "  println(total(a))", "end program t")
        new = migrate_program(src)
        self.assertIn("function total(real[] :: v) -> real", new)
        self.assertIn("real[3] :: a = [1.0, 2.0, 3.0]", new)

    def test_a_migrated_program_runs_the_same_with_no_warnings(self):
        old = lines("record team", "  integer :: tags(3)", "end record",
                    "subroutine bump(inout real :: m)", "  update m(1, :) = m(1, :) + 1.0", "end subroutine",
                    "program t", "  real :: a(2, 2) = 0.0", "  allocatable integer :: g(:)", "  team :: tm",
                    "  allocate g(2)", "  g(2) = 5", "  tm.tags(3) = 7", "  call bump(a)",
                    '  string :: s = "hello"', "  println(a, g, tm.tags, s(2:3), sqrt(4.0))", "end program t")
        new = migrate_program(old)
        before, after, warned = [], [], []
        Interp(parse(old), before.append).run()
        unit = parse(new)
        Interp(unit, after.append, warn=warned.append).run()
        self.assertEqual((before, warned, unit.warnings), (after, [], []))

    def test_migrating_twice_changes_nothing(self):
        once = migrate_program(lines("program t", "  real :: a(2) = [1.0, 2.0]", "  println(a(1), sqrt(a(2)))", "end program t"))
        self.assertEqual(migrate_program(once), once)

    def test_text_that_does_not_lex_is_returned_unchanged(self):
        self.assertEqual(migrate("a(1) = `\n"), "a(1) = `\n")


if __name__ == "__main__":
    unittest.main()
