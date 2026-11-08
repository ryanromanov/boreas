"""`boreas check` and `boreas lsp` report names that are used but never declared."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.cli import check_errors
from boreas.lsp import diagnostics


def program(*lines):
    return "program t\n" + "".join(f"  {line}\n" for line in lines) + "end program\n"


def found(src):
    return [(line, col, msg) for line, col, msg, _ in check_errors(src)]


class UndeclaredTests(unittest.TestCase):
    def test_reports_every_undeclared_name_in_source_order(self):
        src = program("integer :: x", "x = y + 1", "z = x", "println(y)")
        self.assertEqual(found(src), [(3, 7, "'y' is not declared"), (4, 3, "'z' is not declared"),
                                      (5, 11, "'y' is not declared")])

    def test_each_kind_of_use(self):
        for line, name in [("do i = 1, 3\n  end do", "i"), 
                           ("if b then\n  end if", "b"), ("do while (b)\n  end do", "b"),
                           ("stop n", "n"), ("print(a(1))", "a"), ("print(p.x)", "p"), ("print([n])", "n"),
                           ("write(f, 1)", "f"), ("close(f)", "f"), ("print(-n)", "n"), ("println(allocated(a))", "a")]:
            with self.subTest(line):
                ((_, _, msg, length),) = check_errors(program(line))
                self.assertIn(f"'{name}'", msg)
                self.assertEqual(length, len(name))

    def test_select_subject_case_value_and_arm(self):
        src = program("integer :: x = 1", "integer :: r", "select case x", "case k", "  println(1)", "end select",
                      "r = select case x", "  case m -> n", "  case default -> 0", "end select")
        names = [msg.split("'")[1] for _, _, msg in found(src)]
        self.assertEqual(names, ["k", "m", "n"])

    def test_unknown_function_and_subroutine(self):
        self.assertEqual(found(program("println(nope(1))", "call gone()")),
                         [(2, 11, "unknown function or array 'nope'"), (3, 8, "unknown subroutine 'gone'")])

    def test_names_inside_procedures_and_modules(self):
        src = ("function f() -> integer\n  return a\nend function\n"
               "module m\n  subroutine s()\n    b = 1\n  end subroutine\nend module\n" + program())
        self.assertEqual(found(src), [(2, 10, "'a' is not declared"), (6, 5, "'b' is not declared")])

    def test_module_initializer_has_its_line(self):
        src = "module m\n  const integer :: a = b\nend module\n" + program()
        ((line, _, msg, _),) = check_errors(src)
        self.assertEqual(line, 2)
        self.assertIn("'b' is not declared", msg)


class KnownNameTests(unittest.TestCase):
    def assert_clean(self, src):
        self.assertEqual(check_errors(src), [])

    def test_locals_and_loop_variables(self):
        self.assert_clean(program("integer :: i", "do i = 1, 3", "  println(i)", "end do"))

    def test_parameters_and_optional_parameters(self):
        self.assert_clean("function f(integer :: a, integer? :: b) -> integer\n"
                          "  if present(b) then\n    return a + b\n  end if\n  return a\nend function\n" + program())

    def test_procedures_and_builtins(self):
        self.assert_clean("subroutine s()\nend subroutine\nfunction f() -> integer\n  return 1\nend function\n"
                          + program("call s()", "println(f())", "println(abs(-1))"))

    def test_module_names_and_imports(self):
        self.assert_clean("module m\n  public const integer :: a = 1\n  integer :: hidden = 2\n"
                          "  public function g() -> integer\n    return hidden + a\n  end function\nend module\n"
                          + "program t\n  use m: a, g\n  println(a + g())\nend program\n")

    def test_private_module_name_is_not_visible_elsewhere(self):
        src = ("module m\n  public const integer :: a = 1\n  const integer :: secret = 2\nend module\n"
               "program t\n  use m: a\n  println(secret)\nend program\n")
        self.assertEqual(found(src), [(7, 11, "'secret' is not declared")])

    def test_record_fields_and_named_arguments(self):
        self.assert_clean("record r\n  integer :: x\nend record\n"
                          + program("r :: p", "p = r(x = 1)", "println(p.x)"))

    def test_named_argument_value_is_checked(self):
        src = "record r\n  integer :: x\nend record\n" + program("r :: p", "p = r(x = v)")
        self.assertEqual(found(src), [(6, 13, "'v' is not declared")])

    def test_declaration_after_use_or_in_a_branch_counts(self):
        self.assert_clean(program("logical :: c = true", "if c then", "  integer :: x", "end if", "x = 1"))
        self.assert_clean(program("x = 1", "integer :: x"))

    def test_names_are_case_insensitive(self):
        self.assert_clean(program("integer :: Total", "TOTAL = 1"))

    def test_names_in_comments_and_strings_are_ignored(self):
        self.assert_clean(program('println("y") ! y'))


class ParseErrorTests(unittest.TestCase):
    def test_parse_error_comes_alone(self):
        ((_, _, msg, length),) = check_errors(program("x = y", "println(1 +)"))
        self.assertIn("expected an expression", msg)
        self.assertIsNone(length)


class DiagnosticsTests(unittest.TestCase):
    def test_one_diagnostic_per_name_covering_only_the_name(self):
        first, second = diagnostics(program("integer :: x", "x = yy + zzz"))
        self.assertEqual(first["range"], {"start": {"line": 2, "character": 6}, "end": {"line": 2, "character": 8}})
        self.assertEqual(second["range"], {"start": {"line": 2, "character": 11}, "end": {"line": 2, "character": 14}})
        self.assertEqual(first["message"], "'yy' is not declared")


if __name__ == "__main__":
    unittest.main()
