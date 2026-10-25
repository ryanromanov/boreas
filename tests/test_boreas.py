import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas import run_source
from boreas.lexer import LangError


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


SWAP = """subroutine swap(inout integer :: a, inout integer :: b)
  integer :: t
  t = a
  update a = b
  update b = t
end subroutine"""

AREA = """subroutine circle(in real :: r, out real :: area)
  update area = 3.0 * r * r
end subroutine"""


class SubroutineTests(unittest.TestCase):
    def test_inout(self):
        self.assertEqual(run("integer :: x = 1\ninteger :: y = 2\ncall swap(x, y)\nprint x, y", SWAP), ["2 1"])

    def test_out_and_in(self):
        self.assertEqual(run("real :: a\ncall circle(2.0, a)\nprint a", AREA), ["12.0"])

    def test_in_widens_to_real(self):
        self.assertEqual(run("real :: a\ncall circle(2, a)\nprint a", AREA), ["12.0"])

    def test_array_element_and_whole_array(self):
        sub = """subroutine bump(inout integer :: v)
  update v = v + 1
end subroutine"""
        self.assertEqual(run("integer :: a(3) = [1, 2, 3]\ncall bump(a(2))\nprint a", sub), ["[1, 3, 3]"])

    def test_whole_array_inout(self):
        sub = """subroutine double(inout integer :: xs)
  update xs = xs * 2
end subroutine"""
        self.assertEqual(run("integer :: a(3) = [1, 2, 3]\ncall double(a)\nprint a", sub), ["[2, 4, 6]"])

    def test_no_arguments(self):
        sub = "subroutine hi()\n print \"hi\"\nend subroutine"
        self.assertEqual(run("call hi()\ncall hi", sub), ["hi", "hi"])

    def test_cannot_assign_in_parameter(self):
        sub = "subroutine bad(in integer :: n)\n n = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "'in' parameter"):
            run("integer :: x = 0\ncall bad(x)", sub)

    def test_default_mode_is_in(self):
        sub = "subroutine bad(integer :: n)\n n = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "'in' parameter"):
            run("integer :: x = 0\ncall bad(x)", sub)

    def test_out_must_be_assigned(self):
        sub = "subroutine lazy(out integer :: n)\nend subroutine"
        with self.assertRaisesRegex(LangError, "never assigned"):
            run("integer :: x\ncall lazy(x)", sub)

    def test_out_cannot_be_read_first(self):
        sub = "subroutine peek(out integer :: n)\n update n = n + 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "before it is assigned"):
            run("integer :: x\ncall peek(x)", sub)

    def test_out_argument_must_be_variable(self):
        with self.assertRaisesRegex(LangError, "variable or array element"):
            run("call swap(1, 2)", SWAP)

    def test_aliasing_rejected(self):
        with self.assertRaisesRegex(LangError, "alias"):
            run("integer :: x = 1\ncall swap(x, x)", SWAP)

    def test_aliasing_with_in_argument_rejected(self):
        sub = "subroutine add(in integer :: a, inout integer :: b)\n update b = b + a\nend subroutine"
        with self.assertRaisesRegex(LangError, "alias"):
            run("integer :: x = 1\ncall add(x, x)", sub)

    def test_distinct_elements_do_not_alias(self):
        self.assertEqual(run("integer :: a(2) = [1, 2]\ncall swap(a(1), a(2))\nprint a", SWAP), ["[2, 1]"])

    def test_type_mismatch_on_out(self):
        with self.assertRaisesRegex(LangError, "but 'b' is integer"):
            run("real :: a = 1.0\ninteger :: b = 1\ncall circle(1.0, b)", AREA)

    def test_wrong_argument_count(self):
        with self.assertRaisesRegex(LangError, "expects 2 arguments"):
            run("integer :: x = 1\ncall swap(x)", SWAP)

    def test_function_cannot_have_out(self):
        f = "function f(out integer :: n) -> integer\n return 1\nend function"
        with self.assertRaisesRegex(LangError, "only subroutines"):
            run("print 1", f)

    def test_subroutine_in_expression_rejected(self):
        with self.assertRaisesRegex(LangError, "is a subroutine"):
            run("integer :: x = 1\ninteger :: y = 2\nprint swap(x, y)", SWAP)

    def test_call_on_function_rejected(self):
        f = "function one() -> integer\n return 1\nend function"
        with self.assertRaisesRegex(LangError, "is a function"):
            run("call one()", f)

    def test_subroutine_cannot_return_value(self):
        sub = "subroutine s()\n return 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "cannot return a value"):
            run("call s()", sub)

    def test_early_return(self):
        sub = "subroutine s(out integer :: n)\n update n = 1\n return\n update n = 2\nend subroutine"
        self.assertEqual(run("integer :: x\ncall s(x)\nprint x", sub), ["1"])

    def test_explicit_in_on_function(self):
        f = "function twice(in integer :: n) -> integer\n return n * 2\nend function"
        self.assertEqual(run("print twice(4)", f), ["8"])

    def test_old_intent_syntax_rejected(self):
        sub = "subroutine s(integer, intent(out) :: n)\nend subroutine"
        with self.assertRaises(LangError):
            run("print 1", sub)

    def test_declared_variables_start_at_their_default_value(self):
        self.assertEqual(run("integer :: i\nreal :: r\nlogical :: b\nstring :: s\nprint i, r, b\nprint \"[\" + s + \"]\""),
                         ["0 0.0 false", "[]"])

    def test_default_values_can_be_used_in_arithmetic(self):
        self.assertEqual(run("integer :: total\ninteger :: k\ndo k = 1, 3\n  total = total + k\nend do\nprint total"),
                         ["6"])

    def test_out_parameter_still_starts_with_no_value(self):
        sub = "subroutine peek(out integer :: n)\n integer :: m\n m = n\n update n = m\nend subroutine"
        with self.assertRaisesRegex(LangError, "before it is assigned"):
            run("integer :: x\ncall peek(x)", sub)


M = "integer :: m(2, 3) = reshape([1, 2, 3, 4, 5, 6], [2, 3])\n"   # columns: [1,2] [3,4] [5,6]


class MultiDimTests(unittest.TestCase):
    def test_column_major_reshape(self):
        self.assertEqual(run(M + "print m"), ["[[1, 3, 5], [2, 4, 6]]"])

    def test_element_and_shape(self):
        self.assertEqual(run(M + "print m(2, 3), m(1, 2), shape(m), size(m), size(m, 2)"),
                         ["6 3 [2, 3] 6 3"])

    def test_row_and_column_sections(self):
        self.assertEqual(run(M + "print m(1, :), m(:, 2)"), ["[1, 3, 5] [3, 4]"])

    def test_block_section(self):
        self.assertEqual(run(M + "print m(1:2, 2:3)"), ["[[3, 5], [4, 6]]"])

    def test_element_assignment(self):
        self.assertEqual(run(M + "m(2, 2) = 40\nprint m"), ["[[1, 3, 5], [2, 40, 6]]"])

    def test_section_assignment_scalar_and_array(self):
        out = run(M + "m(1, :) = 0\nm(:, 3) = [9, 8]\nprint m")
        self.assertEqual(out, ["[[0, 0, 9], [2, 4, 8]]"])

    def test_whole_array_arithmetic(self):
        self.assertEqual(run(M + "print m * 2 - m"), ["[[1, 3, 5], [2, 4, 6]]"])

    def test_transpose_and_matmul(self):
        out = run(M + "print transpose(m)\nprint matmul(m, transpose(m))\nprint matmul(m, [1, 0, 1])")
        self.assertEqual(out, ["[[1, 2], [3, 4], [5, 6]]", "[[35, 44], [44, 56]]", "[6, 8]"])

    def test_reductions_and_elemental(self):
        out = run(M + "print sum(m), maxval(m), minval(m), any(m > 5), all(m > 5)\nprint sqrt(real(m(:, 1)) * 4.0)")
        self.assertEqual(out, ["21 6 1 true false", "[2.0, 2.8284271247461903]"])

    def test_dot_product(self):
        self.assertEqual(run("print dot_product([1, 2, 3], [4, 5, 6])"), ["32"])

    def test_zero_initialised_and_scalar_fill(self):
        self.assertEqual(run("real :: g(2, 2)\nreal :: h(2) = 1.5\nprint g, h"), ["[[0.0, 0.0], [0.0, 0.0]] [1.5, 1.5]"])

    def test_subscript_count_checked(self):
        with self.assertRaisesRegex(LangError, "2 dimension\\(s\\) but 1"):
            run(M + "print m(1)")

    def test_bounds_name_the_dimension(self):
        with self.assertRaisesRegex(LangError, "index 4 out of bounds 1..3 in dimension 2"):
            run(M + "print m(1, 4)")

    def test_shape_mismatch_in_arithmetic(self):
        with self.assertRaisesRegex(LangError, "shape mismatch"):
            run(M + "print m + transpose(m)")

    def test_shape_mismatch_in_assignment(self):
        with self.assertRaisesRegex(LangError, "shape mismatch"):
            run(M + "m(1, :) = [1, 2]")

    def test_bad_initializer_shape(self):
        with self.assertRaisesRegex(LangError, "initializer has shape"):
            run("integer :: m(2, 2) = [1, 2, 3, 4]")

    def test_reshape_checks_element_count(self):
        with self.assertRaisesRegex(LangError, "cannot fill"):
            run("print reshape([1, 2, 3], [2, 2])")

    def test_type_checked_on_section_write(self):
        with self.assertRaisesRegex(LangError, "cannot assign real to integer"):
            run(M + "m(1, :) = 1.5")

    def test_matmul_inner_dimension_checked(self):
        with self.assertRaisesRegex(LangError, "inner dimensions"):
            run(M + "print matmul(m, m)")

    def test_pass_matrix_and_section_to_subroutine(self):
        sub = """subroutine zero_row(inout integer :: r)
  update r = 0
end subroutine
subroutine total(integer :: a, out integer :: t)
  update t = sum(a)
end subroutine"""
        out = run(M + "integer :: t\ncall zero_row(m(2, :))\ncall total(m, t)\nprint m, t", sub)
        self.assertEqual(out, ["[[1, 3, 5], [0, 0, 0]] 9"])

    def test_in_parameter_matrix_is_read_only(self):
        sub = "subroutine bad(integer :: a)\n a(1, 1) = 0\nend subroutine"
        with self.assertRaisesRegex(LangError, "'in' parameter"):
            run(M + "call bad(m)", sub)

    def test_overlapping_sections_alias(self):
        sub = "subroutine two(inout integer :: a, inout integer :: b)\nend subroutine"
        with self.assertRaisesRegex(LangError, "alias"):
            run(M + "call two(m(1, :), m(:, 1))", sub)

    def test_disjoint_sections_do_not_alias(self):
        sub = "subroutine two(inout integer :: a, inout integer :: b)\n update a = 7\n update b = 8\nend subroutine"
        out = run(M + "call two(m(1, :), m(2, :))\nprint m", sub)
        self.assertEqual(out, ["[[7, 7, 7], [8, 8, 8]]"])

    def test_nested_loops(self):
        body = "integer :: i\ninteger :: j\nreal :: g(2, 2)\ndo j = 1, 2\n do i = 1, 2\n  g(i, j) = i * 10 + j\n end do\nend do\nprint g"
        self.assertEqual(run(body), ["[[11.0, 12.0], [21.0, 22.0]]"])


class DoVariableTests(unittest.TestCase):
    def test_variable_ends_one_step_past_range(self):
        self.assertEqual(run("integer :: i\ndo i = 1, 3\nend do\nprint i"), ["4"])

    def test_zero_trip_loop_leaves_lower_bound(self):
        self.assertEqual(run("integer :: i\ndo i = 5, 1\nend do\nprint i"), ["5"])

    def test_exit_keeps_current_value(self):
        self.assertEqual(run("integer :: i\ndo i = 1, 10\n if i == 4 then\n  exit\n end if\nend do\nprint i"), ["4"])

    def test_negative_step(self):
        self.assertEqual(run("integer :: i\ndo i = 6, 1, -2\nend do\nprint i"), ["0"])


class ConstTests(unittest.TestCase):
    def test_constants_work_in_expressions_and_dimensions(self):
        out = run("const integer :: n = 3\nconst real :: pi = 3.0\ninteger :: a(n)\nprint size(a), pi * 2.0")
        self.assertEqual(out, ["3 6.0"])

    def test_constant_may_use_an_earlier_constant(self):
        self.assertEqual(run("const integer :: a = 2\nconst integer :: b = a * 5\nprint b"), ["10"])

    def test_several_constants_in_one_declaration(self):
        self.assertEqual(run("const integer :: a = 1\nconst integer :: b = 2\nprint a + b"), ["3"])

    def test_constant_array(self):
        out = run("const integer :: p(3) = [2, 3, 5]\nprint p, sum(p), p(2)")
        self.assertEqual(out, ["[2, 3, 5] 10 3"])

    def test_cannot_assign_a_constant(self):
        with self.assertRaisesRegex(LangError, "'n' is a constant"):
            run("const integer :: n = 3\nn = 4")

    def test_cannot_assign_an_element_or_whole_constant_array(self):
        for stmt in ("p(1) = 0", "p = 0", "p(1:2) = [0, 0]"):
            with self.subTest(stmt=stmt):
                with self.assertRaisesRegex(LangError, "constant"):
                    run("const integer :: p(3) = [2, 3, 5]\n" + stmt)

    def test_cannot_use_a_constant_as_a_do_variable(self):
        with self.assertRaisesRegex(LangError, "constant"):
            run("const integer :: i = 1\ndo i = 1, 2\nend do")

    def test_cannot_pass_a_constant_as_out_or_inout(self):
        sub = "subroutine bump(inout integer :: v)\n update v = v + 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "constant and cannot be passed as inout"):
            run("const integer :: n = 1\ncall bump(n)", sub)

    def test_a_constant_can_be_passed_as_in(self):
        f = "function twice(integer :: v) -> integer\n return v * 2\nend function"
        self.assertEqual(run("const integer :: n = 4\nprint twice(n)", f), ["8"])

    def test_constants_need_a_value(self):
        with self.assertRaisesRegex(LangError, "needs a value"):
            run("const integer :: n")

    def test_constant_value_is_type_checked(self):
        with self.assertRaisesRegex(LangError, "cannot assign real to integer"):
            run("const integer :: n = 2.5")

    def test_local_constants_in_procedures(self):
        f = "function area(real :: r) -> real\n const real :: pi = 3.0\n return pi * r * r\nend function"
        self.assertEqual(run("print area(2.0)", f), ["12.0"])

    def test_printer_keeps_const(self):
        from boreas.printer import to_source
        from boreas.parser import parse
        src = "program p\n  const integer :: a = 1\n  const integer :: b = 2\n  const real :: c = 1.5\n  integer :: d = 3\nend program p\n"
        self.assertEqual(to_source(parse(src)), src)


class UpdateTests(unittest.TestCase):
    def test_write_to_out_needs_update(self):
        sub = "subroutine f(out integer :: n)\n n = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "write 'update n = ...'"):
            run("integer :: x\ncall f(x)", sub)

    def test_write_to_inout_array_element_needs_update(self):
        sub = "subroutine f(inout integer :: a)\n a(1) = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "write 'update a = ...'"):
            run("integer :: x(2) = [0, 0]\ncall f(x)", sub)

    def test_update_array_element(self):
        sub = "subroutine f(inout integer :: a)\n update a(2) = 9\nend subroutine"
        self.assertEqual(run("integer :: x(2) = [0, 0]\ncall f(x)\nprint x", sub), ["[0, 9]"])

    def test_update_on_local_rejected(self):
        with self.assertRaisesRegex(LangError, "local variable"):
            run("integer :: x = 0\nupdate x = 1")

    def test_update_on_in_parameter_rejected(self):
        sub = "subroutine f(integer :: n)\n update n = 1\nend subroutine"
        with self.assertRaisesRegex(LangError, "'n' is an 'in' parameter"):
            run("call f(1)", sub)

    def test_update_inside_nested_blocks(self):
        sub = ("subroutine f(inout integer :: t)\n integer :: i\n do i = 1, 3\n"
               "  if i > 1 then\n   update t = t + i\n  end if\n end do\nend subroutine")
        self.assertEqual(run("integer :: s = 0\ncall f(s)\nprint s", sub), ["5"])

    def test_missing_update_is_found_without_running_it(self):
        sub = "subroutine f(out integer :: n)\n if false then\n  n = 1\n end if\nend subroutine"
        with self.assertRaisesRegex(LangError, "write 'update n = ...'"):
            run("print 1", sub)

    def test_loop_variable_cannot_be_a_parameter(self):
        sub = "subroutine f(inout integer :: i)\n do i = 1, 2\n end do\nend subroutine"
        with self.assertRaisesRegex(LangError, "loop variable"):
            run("print 1", sub)


GEO = """module geometry
  public const real :: pi = 3.0
  const real :: secret = 7.0
  public function area(real :: r) -> real
    return pi * r ** 2
  end function
  public function bigger(real :: r) -> real
    return area(r) + helper(r)
  end function
  function helper(real :: r) -> real
    return r + secret
  end function
end module"""


class ModuleTests(unittest.TestCase):
    def test_import_function_and_constant(self):
        self.assertEqual(run("use geometry: area, pi\nprint area(2.0), pi", GEO), ["12.0 3.0"])

    def test_module_function_uses_private_names_internally(self):
        self.assertEqual(run("use geometry: bigger\nprint bigger(1.0)", GEO), ["11.0"])

    def test_private_name_cannot_be_imported(self):
        with self.assertRaisesRegex(LangError, "'helper' is private to module 'geometry'"):
            run("use geometry: helper\nprint 1", GEO)

    def test_private_constant_cannot_be_imported(self):
        with self.assertRaisesRegex(LangError, "'secret' is private"):
            run("use geometry: secret\nprint 1", GEO)

    def test_unknown_name_and_unknown_module(self):
        with self.assertRaisesRegex(LangError, "module 'geometry' has no 'nope'"):
            run("use geometry: nope\nprint 1", GEO)
        with self.assertRaisesRegex(LangError, "no module named 'ghost'"):
            run("use ghost: x\nprint 1")

    def test_nothing_is_visible_without_an_import(self):
        with self.assertRaisesRegex(LangError, "unknown function or array 'area'"):
            run("print area(1.0)", GEO)

    def test_imported_constant_is_read_only(self):
        with self.assertRaisesRegex(LangError, "constant and cannot be assigned"):
            run("use geometry: pi\npi = 4.0", GEO)

    def test_name_clash_with_import(self):
        with self.assertRaisesRegex(LangError, "'pi' already declared"):
            run("use geometry: pi\nreal :: pi = 1.0", GEO)

    def test_modules_can_import_from_modules(self):
        more = GEO + """
module more
  use geometry: area
  public function twice(real :: r) -> real
    return 2.0 * area(r)
  end function
end module"""
        self.assertEqual(run("use more: twice\nprint twice(1.0)", more), ["6.0"])

    def test_import_cycle_rejected(self):
        src = ("module a\n use b: g\n public function f() -> integer\n  return 1\n end function\nend module\n"
               "module b\n use a: f\n public function g() -> integer\n  return 2\n end function\nend module")
        with self.assertRaisesRegex(LangError, "cycle"):
            run("print 1", src)

    def test_constants_can_build_on_imports(self):
        src = GEO + "\nmodule more\n use geometry: pi\n public const real :: tau = 2.0 * pi\nend module"
        self.assertEqual(run("use more: tau\nprint tau", src), ["6.0"])

    def test_module_holds_only_constants_and_procedures(self):
        with self.assertRaisesRegex(LangError, "declare this with 'const'"):
            run("print 1", "module m\n integer :: counter = 0\nend module")

    def test_private_keyword_rejected(self):
        with self.assertRaisesRegex(LangError, "private unless marked 'public'"):
            run("print 1", "module m\n private function f() -> integer\n  return 1\n end function\nend module")

    def test_public_outside_module_rejected(self):
        with self.assertRaisesRegex(LangError, "only for the contents of a module"):
            run("print 1", "public function f() -> integer\n return 1\nend function")

    def test_duplicate_names_in_module(self):
        src = "module m\n const integer :: f = 1\n function f() -> integer\n  return 1\n end function\nend module"
        with self.assertRaisesRegex(LangError, "'f' is defined more than once in module 'm'"):
            run("print 1", src)

    def test_update_works_in_module_subroutines(self):
        src = "module m\n public subroutine put(out integer :: n)\n  update n = 5\n end subroutine\nend module"
        self.assertEqual(run("use m: put\ninteger :: x\ncall put(x)\nprint x", src), ["5"])

    def test_procedure_can_import_for_itself(self):
        src = GEO + "\nfunction f(real :: r) -> real\n use geometry: area\n return area(r)\nend function"
        self.assertEqual(run("print f(1.0)", src), ["3.0"])
        with self.assertRaisesRegex(LangError, "unknown function or array 'area'"):
            run("use geometry: area\nprint f(1.0)", src.replace("use geometry: area\n return", " return"))

    def test_printer_round_trips_modules(self):
        from boreas.parser import parse
        from boreas.printer import to_source
        text = to_source(parse(GEO + "\nprogram p\n use geometry: area\n print area(1.0)\nend program"))
        self.assertIn("  public function area(real :: r) -> real", text)
        self.assertEqual(to_source(parse(text)), text)


class OneDeclarationPerLineTests(unittest.TestCase):
    def test_several_names_on_one_line_are_rejected(self):
        with self.assertRaisesRegex(LangError, "declare one variable per line"):
            run("integer :: x, y")

    def test_rejected_with_initializers_const_and_arrays(self):
        for src in ("integer :: x = 1, y = 2", "const real :: a = 1.0, b = 2.0", "real :: v(3), w(2)",
                    "allocatable real :: v(:), w(:)"):
            with self.subTest(src=src):
                with self.assertRaisesRegex(LangError, "declare one variable per line"):
                    run(src)

    def test_printer_puts_each_declaration_on_its_own_line(self):
        from boreas.parser import parse
        from boreas.printer import to_source
        text = to_source(parse("program p\ninteger :: a\ninteger :: b = 2\nreal :: c\nend program p"))
        self.assertIn("  integer :: a\n  integer :: b = 2\n  real :: c\n", text)


if __name__ == "__main__":
    unittest.main()
