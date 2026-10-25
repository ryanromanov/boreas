import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas import run_source
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source

POINT = "record point\n  real :: x\n  real :: y\nend record\n"


def run(body, funcs="", defs=POINT):
    out = []
    run_source(f"{defs}\n{funcs}\nprogram t\n{body}\nend program t\n", out.append)
    return out


def fails(body, message, funcs="", defs=POINT):
    with unittest.TestCase().assertRaisesRegex(LangError, message):
        run(body, funcs, defs)


class BasicTests(unittest.TestCase):
    def test_declare_assign_and_read_fields(self):
        self.assertEqual(run("point :: p\np.x = 1.5\np.y = 2\nprint p.x, p.y"), ["1.5 2.0"])

    def test_positional_construction(self):
        self.assertEqual(run("point :: p = point(1.0, 2)\nprint p.x, p.y"), ["1.0 2.0"])

    def test_named_construction_in_any_order(self):
        self.assertEqual(run("point :: p = point(y = 2.0, x = 1.0)\nprint p"), ["point(x=1.0, y=2.0)"])

    def test_named_construction_leaves_the_others_at_their_defaults(self):
        self.assertEqual(run("point :: p = point(y = 5.0)\nprint p"), ["point(x=0.0, y=5.0)"])

    def test_positional_then_named(self):
        defs = "record box\n  integer :: a\n  integer :: b\n  integer :: c\nend record\n"
        self.assertEqual(run("box :: q = box(1, c = 3)\nq.b = 2\nprint q", defs=defs), ["box(a=1, b=2, c=3)"])

    def test_fields_start_at_their_default_values(self):
        defs = "record mix\n  integer :: i\n  real :: r\n  logical :: b\n  string :: s\n  point :: p\nend record\n"
        self.assertEqual(run("mix :: m\nprint m.i, m.r, m.b, m.p", defs=POINT + defs),
                         ["0 0.0 false point(x=0.0, y=0.0)"])

    def test_declared_record_can_be_printed_and_used_at_once(self):
        self.assertEqual(run("point :: p\nprint p"), ["point(x=0.0, y=0.0)"])
        self.assertEqual(run("point :: pts(2)\nprint pts(2).y"), ["0.0"])

    def test_assignment_copies(self):
        body = "point :: p = point(1.0, 2.0)\npoint :: q\nq = p\nq.x = 9.0\nprint p.x, q.x"
        self.assertEqual(run(body), ["1.0 9.0"])

    def test_initializer_copies(self):
        self.assertEqual(run("point :: p = point(1.0, 2.0)\npoint :: q = p\nq.y = 0.0\nprint p.y, q.y"), ["2.0 0.0"])

    def test_integer_widens_to_real_in_fields(self):
        self.assertEqual(run("point :: p = point(1, 2)\nprint p"), ["point(x=1.0, y=2.0)"])

    def test_field_type_mismatch(self):
        fails('point :: p\np.x = "hi"', "cannot assign string to real")

    def test_unknown_field(self):
        fails("point :: p\np.z = 1.0", "has no field 'z'")

    def test_field_of_a_non_record(self):
        fails("integer :: n = 1\nprint n.x", "'n' is not a record")

    def test_unknown_type(self):
        fails("blob :: b", "unknown type 'blob'")

    def test_constructor_errors(self):
        fails("point :: p = point(1.0)", "needs all 2 field")
        fails("point :: p = point(1.0, 2.0, 3.0)", "has 2 field\\(s\\), got 3")
        fails("point :: p = point(x = 1.0, x = 2.0)", "field 'x' is given twice")
        fails("point :: p = point(z = 1.0)", "has no field 'z'")
        fails("point :: p = point(x = 1.0, 2.0)", "positional arguments must come before named")

    def test_named_arguments_only_for_records(self):
        f = "function f(real :: a) -> real\n return a\nend function"
        fails("print f(a = 1.0)", "named arguments are only for building records", f)
        s = "subroutine s(real :: a)\nend subroutine"
        fails("call s(a = 1.0)", "named arguments are only for building records", s)

    def test_record_is_not_arithmetic_or_comparable(self):
        fails("point :: p = point(1.0, 2.0)\nprint p == p", "records cannot be used with '=='")
        fails("point :: p = point(1.0, 2.0)\nprint p + p", "records cannot be used with '\\+'")
        fails("point :: p = point(1.0, 2.0)\nprint -p", "records cannot be used with '-'")

    def test_record_cannot_be_a_select_subject(self):
        fails("point :: p = point(1.0, 2.0)\nselect case p\ncase 1\nprint 1\nend select", "got point")

    def test_field_of_a_computed_value(self):
        f = "function mk() -> point\n return point(1.0, 2.0)\nend function"
        self.assertEqual(run("print mk().y, point(3.0, 4.0).x", f), ["2.0 3.0"])
        fails("mk().x = 5.0", "cannot assign to a field of a computed value", f)


class NestedTests(unittest.TestCase):
    DEFS = POINT + "record segment\n  point :: a\n  point :: b\n  integer :: tags(3)\nend record\n"

    def test_nested_record_and_array_field(self):
        body = ("segment :: s\ns.a = point(1.0, 2.0)\ns.b.x = 5.0\ns.b.y = 6.0\ns.tags(2) = 7\n"
                "print s.a.x, s.b.y, s.tags")
        self.assertEqual(run(body, defs=self.DEFS), ["1.0 6.0 [0, 7, 0]"])

    def test_array_field_whole_assignment_and_construction(self):
        body = "segment :: s\ns.tags = [1, 2, 3]\nprint s.tags(3)"
        self.assertEqual(run(body, defs=self.DEFS), ["3"])
        fails("segment :: s\ns.tags = [1, 2]", "shape mismatch", defs=self.DEFS)
        body = "segment :: s = segment(tags = [4, 5, 6])\nprint s.tags"
        self.assertEqual(run(body, defs=self.DEFS), ["[4, 5, 6]"])
        fails("segment :: s = segment(tags = 1)", "is an array of shape \\(3\\)", defs=self.DEFS)

    def test_nested_assignment_copies(self):
        body = "segment :: s\npoint :: p = point(1.0, 1.0)\ns.a = p\ns.a.x = 9.0\nprint p.x, s.a.x"
        self.assertEqual(run(body, defs=self.DEFS), ["1.0 9.0"])

    def test_array_field_index_out_of_bounds(self):
        fails("segment :: s\ns.tags(4) = 1", "out of bounds", defs=self.DEFS)

    def test_array_of_records_elements_are_independent(self):
        body = ("point :: pts(3)\ninteger :: i\ndo i = 1, 3\n  pts(i).x = i * 1.0\n  pts(i).y = pts(i).x * 2.0\n"
                "end do\nprint pts(2), pts(3).y")
        self.assertEqual(run(body), ["point(x=2.0, y=4.0) 6.0"])

    def test_array_initializer_with_record_values(self):
        body = "point :: pts(2) = [point(1.0, 2.0), point(3.0, 4.0)]\npts(1).x = 0.0\nprint pts(1).x, pts(2).x"
        self.assertEqual(run(body), ["0.0 3.0"])

    def test_array_of_records_whole_assignment_copies(self):
        body = "point :: a(2)\npoint :: b(2)\na(1).x = 1.0\nb = a\na(1).x = 5.0\nprint b(1).x"
        self.assertEqual(run(body), ["1.0"])

    def test_element_assignment_copies(self):
        body = "point :: pts(2)\npoint :: p = point(1.0, 2.0)\npts(1) = p\npts(1).x = 9.0\nprint p.x, pts(1).x"
        self.assertEqual(run(body), ["1.0 9.0"])

    def test_array_field_of_records(self):
        defs = POINT + "record line\n  point :: ends(2)\nend record\n"
        self.assertEqual(run("line :: l\nl.ends(2).y = 4.0\nprint l.ends(2).y", defs=defs), ["4.0"])

    def test_slice_of_records_needs_one_element(self):
        fails("point :: a(2)\nprint a(1:2).x", "pick one element")

    def test_allocatable_records_are_rejected(self):
        fails("allocatable point :: a(:)", "allocatable arrays of records are not supported")


class DefinitionTests(unittest.TestCase):
    def test_field_defaults_are_rejected(self):
        fails("print 1", "no default values", defs="record q\n  real :: z = 1.0\nend record\n")

    def test_record_cannot_contain_itself(self):
        fails("print 1", "contains itself: a -> a", defs="record a\n  a :: inner\nend record\n")
        fails("print 1", "contains itself: a -> b -> a",
              defs="record a\n  b :: i\nend record\nrecord b\n  a :: j\nend record\n")

    def test_unknown_field_type(self):
        fails("print 1", "unknown type 'nope'", defs="record r\n  nope :: z\nend record\n")

    def test_duplicate_fields(self):
        fails("print 1", "two fields called 'x'", defs="record r\n  real :: x\n  real :: x\nend record\n")

    def test_field_array_size_must_be_literal(self):
        fails("print 1", "literal size", defs="record r\n  real :: v(n)\nend record\n")

    def test_name_clashes(self):
        fails("print 1", "same name as a procedure", "function point() -> integer\n return 1\nend function")
        fails("print 1", "cannot be the name of a record", defs="record real\n  integer :: n\nend record\n")
        fails("print 1", "cannot be the name of a record", defs="record if\n  integer :: n\nend record\n")

    def test_records_defined_twice(self):
        fails("print 1", "defined more than once", defs=POINT + POINT)

    def test_record_inside_a_procedure_is_rejected(self):
        f = "subroutine s()\n record r\n  integer :: n\n end record\nend subroutine"
        fails("print 1", "defined at the top level or inside a module", f)

    def test_field_lines_are_plain_declarations(self):
        fails("print 1", "plain declarations", defs="record r\n  const integer :: n\nend record\n")
        fails("print 1", "declare one variable per line", defs="record r\n  real :: x, y\nend record\n")


class ProcedureTests(unittest.TestCase):
    MOVE = "subroutine move(inout point :: p, real :: dx)\n  update p.x = p.x + dx\nend subroutine"
    MAKE = "subroutine make(out point :: p)\n  update p.x = 1.0\n  update p.y = 2.0\nend subroutine"

    def test_inout_record(self):
        self.assertEqual(run("point :: p = point(1.0, 2.0)\ncall move(p, 2.0)\nprint p", self.MOVE),
                         ["point(x=3.0, y=2.0)"])

    def test_out_record_must_be_fully_assigned(self):
        self.assertEqual(run("point :: p\ncall make(p)\nprint p", self.MAKE), ["point(x=1.0, y=2.0)"])
        half = "subroutine make(out point :: p)\n  update p.x = 1.0\nend subroutine"
        fails("point :: p\ncall make(p)", "not fully assigned in make: 'p.y' has no value", half)

    def test_out_record_starts_with_no_values(self):
        peek = "subroutine peek(out point :: p)\n  update p.y = p.x\nend subroutine"
        fails("point :: p = point(1.0, 2.0)\ncall peek(p)", "field 'p.x' is used before it is assigned", peek)

    def test_in_record_is_copied_and_read_only(self):
        bad = "subroutine bad(point :: p)\n  p.x = 1.0\nend subroutine"
        fails("point :: p = point(0.0, 0.0)\ncall bad(p)", "'p.x' is part of an 'in' parameter", bad)

    def test_missing_update_names_the_field(self):
        bad = "subroutine bad(inout point :: p)\n  p.x = 1.0\nend subroutine"
        fails("print 1", "write 'update p.x = ...'", bad)

    def test_update_needs_an_out_parameter(self):
        fails("point :: p\nupdate p.x = 1.0", "'update' is for out and inout parameters")

    def test_update_works_through_nested_fields_and_elements(self):
        defs = POINT + "record line\n  point :: ends(2)\nend record\n"
        sub = "subroutine f(inout line :: l)\n  update l.ends(2).y = 9.0\nend subroutine"
        self.assertEqual(run("line :: l\nl.ends(1) = point(0.0, 0.0)\nl.ends(2) = point(0.0, 0.0)\n"
                             "call f(l)\nprint l.ends(2).y", sub, defs), ["9.0"])

    def test_function_returns_a_copy(self):
        f = "function mk(real :: a) -> point\n  point :: r = point(a, a)\n  return r\nend function"
        self.assertEqual(run("print mk(2.0).y, mk(3.0)", f), ["2.0 point(x=3.0, y=3.0)"])

    def test_record_argument_type_is_checked(self):
        defs = POINT + "record other\n  real :: x\nend record\n"
        f = "function fx(point :: p) -> real\n  return p.x\nend function"
        fails("other :: o = other(1.0)\nprint fx(o)", "cannot assign other to point", f, defs)

    def test_same_named_records_from_different_modules_are_different_types(self):
        src = ("module a\n public record point\n  real :: x\n end record\n"
               " public function make() -> point\n  return point(1.0)\n end function\nend module\n"
               "module b\n use a: make\n public function take(point :: p) -> real\n  return p.x\n end function\n"
               " record point\n  real :: x\n end record\nend module\n"
               "program t\n use a: make\n use b: take\n print take(make())\nend program")
        with self.assertRaisesRegex(LangError, "two records share that name"):
            run_source(src)

    def test_distinct_fields_may_be_passed_together(self):
        swap = ("subroutine swap(inout real :: a, inout real :: b)\n  real :: t\n  t = a\n"
                "  update a = b\n  update b = t\nend subroutine")
        self.assertEqual(run("point :: p = point(1.0, 2.0)\ncall swap(p.x, p.y)\nprint p", swap),
                         ["point(x=2.0, y=1.0)"])

    def test_aliasing_is_rejected(self):
        swap = "subroutine swap(inout real :: a, inout real :: b)\nend subroutine"
        both = "subroutine both(inout point :: a, inout real :: b)\nend subroutine"
        fails("point :: p = point(1.0, 2.0)\ncall swap(p.x, p.x)", "alias", swap)
        fails("point :: p = point(1.0, 2.0)\ncall both(p, p.x)", "alias", both)

    def test_distinct_array_elements_do_not_alias(self):
        swap = "subroutine swap(inout real :: a, inout real :: b)\nend subroutine"
        body = "point :: a(2)\na(1) = point(1.0, 1.0)\na(2) = point(2.0, 2.0)\ncall swap(a(1).x, a(2).x)"
        run(body, swap)

    def test_const_record(self):
        body = "const point :: o = point(0.0, 0.0)\nprint o.x"
        self.assertEqual(run(body), ["0.0"])
        fails("const point :: o = point(0.0, 0.0)\no.x = 1.0", "'o.x' is part of a constant")
        fails("const point :: o = point(0.0, 0.0)\no = point(1.0, 1.0)", "'o' is a constant")
        move = "subroutine move(inout point :: p)\n  update p.x = 1.0\nend subroutine"
        fails("const point :: o = point(0.0, 0.0)\ncall move(o)", "part of a constant|is a constant", move)


class ModuleTests(unittest.TestCase):
    MOD = """module geo
  public record point
    real :: x
    real :: y
  end record
  record hidden
    integer :: n
  end record
  public const point :: origin = point(0.0, 0.0)
  public function mid(point :: a, point :: b) -> point
    return point((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
  end function
  public function secret() -> integer
    hidden :: h = hidden(7)
    return h.n
  end function
end module
"""

    def test_import_a_record_and_use_it(self):
        body = ("use geo: point, mid, origin\npoint :: a = point(0.0, 0.0)\npoint :: b = point(2.0, 4.0)\n"
                "print mid(a, b), origin")
        self.assertEqual(run(body, self.MOD, defs=""), ["point(x=1.0, y=2.0) point(x=0.0, y=0.0)"])

    def test_private_record_cannot_be_imported_but_can_be_used_inside(self):
        fails("use geo: hidden\nprint 1", "'hidden' is private to module 'geo'", self.MOD, "")
        self.assertEqual(run("use geo: secret\nprint secret()", self.MOD, defs=""), ["7"])

    def test_record_needs_an_import(self):
        fails("point :: a", "unknown type 'point'", self.MOD, "")

    def test_imported_const_record_is_read_only(self):
        fails("use geo: origin\norigin.x = 1.0", "part of a constant", self.MOD, "")

    def test_two_imports_with_one_name_clash(self):
        more = self.MOD + "module other\n public record point\n  integer :: k\n end record\nend module\n"
        fails("use geo: point\nuse other: point\nprint 1", "already defined here", more, "")

    def test_modules_cannot_hold_record_variables(self):
        with self.assertRaisesRegex(LangError, "declare this with 'const'"):
            run_source("module m\n point :: p\nend module\nprogram t\n print 1\nend program")

    def test_module_record_fields_use_records_it_imports(self):
        src = (self.MOD + "module shapes\n use geo: point\n public record seg\n  point :: a\n  point :: b\n end record\n"
               "end module\nprogram t\n use geo: point\n use shapes: seg\n seg :: s\n s.a = point(1.0, 2.0)\n"
               " s.b = point(3.0, 4.0)\n print s.b.y\nend program")
        out = []
        run_source(src, out.append)
        self.assertEqual(out, ["4.0"])


class PrinterTests(unittest.TestCase):
    SRC = (POINT + "record seg\n  point :: a\n  integer :: tags(2)\nend record\n"
           "module m\n  public record q\n    real :: z\n  end record\nend module\n"
           "program t\n  point :: p = point(1.0, y = 2.0)\n  seg :: s\n  p.x = 3.0\n  s.a.y = p.x\n  s.tags(2) = 1\n"
           "  print p.x + s.tags(2), p\nend program\n")

    def test_round_trip_is_stable(self):
        text = to_source(parse(self.SRC))
        self.assertIn("record point\n  real :: x\n  real :: y\nend record", text)
        self.assertIn("point :: p = point(1.0, y = 2.0)", text)
        self.assertIn("s.a.y = p.x", text)
        self.assertIn("  public record q\n    real :: z\n  end record", text)
        self.assertEqual(to_source(parse(text)), text)

    def test_printed_program_runs_the_same(self):
        out1, out2 = [], []
        run_source(self.SRC, out1.append)
        run_source(to_source(parse(self.SRC)), out2.append)
        self.assertEqual(out1, out2)
        self.assertEqual(out1, ["4.0 point(x=3.0, y=2.0)"])

    def test_example_runs(self):
        path = os.path.join(os.path.dirname(__file__), "..", "examples", "records.bor")
        out = []
        with open(path) as f:
            run_source(f.read(), out.append)
        self.assertEqual(out[-1], "length: 10.0")


if __name__ == "__main__":
    unittest.main()
