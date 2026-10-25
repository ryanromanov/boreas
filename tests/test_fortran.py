import os, re, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.fortran import convert, parse_fortran
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source, without_comments
from boreas.verify import words, same_output

HERE = os.path.dirname(__file__)
GFORTRAN = shutil.which("gfortran")


def fixture(name):
    return os.path.join(HERE, "fortran", name)


def read(path):
    with open(path) as f:
        return f.read()


def run_bor(text):
    out = []
    Interp(parse(text), out.append).run()
    return out


def run_gfortran(path):
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "a.out")
        subprocess.run([GFORTRAN, path, "-o", exe], check=True, capture_output=True)
        return subprocess.run([exe], check=True, capture_output=True, text=True).stdout


def assert_round_trips(src):
    """Printing the converted tree and parsing it back must give the same tree (compared as
    text), with comments stripped first so that only structure is compared."""
    unit, _, _ = parse_fortran(src)
    printed = to_source(without_comments(unit))
    reparsed = to_source(parse(printed))
    assert reparsed == printed, f"printer is not structure-preserving:\n{printed}\n---\n{reparsed}"


def conv(body, decls="", name="t"):
    """Convert a Fortran program body to Boreas source."""
    src = f"program {name}\n  implicit none\n{decls}\n{body}\nend program {name}\n"
    assert_round_trips(src)
    text, findings = convert(src)
    return text, findings


def todos(findings):
    return [f for f in findings if f[1] == "todo"]


class FixtureTests(unittest.TestCase):
    """Whole programs: convert, check the Boreas is well-formed, run it, compare with gfortran."""

    def check(self, name):
        path = name if os.path.isabs(name) else fixture(name)
        if GFORTRAN:
            subprocess.run([GFORTRAN, "-fsyntax-only", "-std=f2018", path], check=True, capture_output=True)
        text, findings = convert(read(path), name)
        self.assertEqual(todos(findings), [])
        assert_round_trips(read(path))
        parse(text)                                  # the file as written is valid Boreas
        got = run_bor(text)
        if GFORTRAN:
            want = run_gfortran(path)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        return got

    def test_basic(self):
        got = self.check("basic.f90")
        self.assertEqual(got[0], "squares: [1, 4, 9, 16, 25]")
        self.assertEqual(got[-1], "done")

    def test_implicit_typing_and_inferred_intents(self):
        got = self.check("implicit.f90")
        self.assertEqual(got[0], "25.0")
        self.assertIn("medium", got)
        self.assertEqual(got[-1], "120 true true")

    def test_every_example_program_converts_and_matches_gfortran(self):
        examples = os.path.join(HERE, "..", "examples")
        paths = sorted(os.path.join(examples, f) for f in os.listdir(examples) if f.endswith(".f90"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(example=os.path.basename(path)):
                self.check(os.path.abspath(path))

    def test_conditionally_set_argument_is_inout(self):
        got = self.check("search.f90")
        self.assertEqual(got, ["found at 3", "still 3"])
        text, _ = convert(read(fixture("search.f90")))
        self.assertIn("inout integer :: pos", text)

    def test_writes_to_out_arguments_are_marked_update(self):
        src = ("subroutine fill(n, t)\n  integer, intent(in) :: n\n  integer, intent(out) :: t\n"
               "  integer :: i\n  t = 0\n  do i = 1, n\n    t = t + i\n  end do\nend subroutine\n"
               "program p\n  integer :: k\n  call fill(4, k)\n  print *, k\nend program\n")
        text, _ = convert(src)
        self.assertIn("  update t = 0", text)
        self.assertIn("    update t = t + i", text)
        self.assertNotIn("update i", text)
        self.assertEqual(run_bor(text), ["10"])

    def test_loop_over_a_dummy_argument_is_flagged(self):
        src = ("subroutine f(i)\n  integer, intent(inout) :: i\n  do i = 1, 2\n  end do\nend subroutine\n"
               "program p\n  integer :: k\n  call f(k)\nend program\n")
        _, findings = convert(src)
        self.assertTrue(any("loop variable" in f[2] for f in todos(findings)))

    def test_variables_named_like_module_words_are_renamed(self):
        src = "program p\n  integer :: use, public\n  use = 1\n  public = 2\n  print *, use + public\nend program\n"
        text, _ = convert(src)
        self.assertEqual(run_bor(text), ["3"])

    def test_variable_named_update_is_renamed(self):
        src = "program p\n  integer :: update\n  update = 3\n  print *, update\nend program\n"
        text, _ = convert(src)
        self.assertEqual(run_bor(text), ["3"])

    def test_modules(self):
        got = self.check("modules.f90")
        self.assertEqual(got[-1].split()[0], "55")
        text, _ = convert(read(fixture("modules.f90")))
        self.assertIn("module geometry", text)
        self.assertIn("  public const real :: pi = 3.14159", text)
        self.assertIn("  const real :: unit_scale = 2.0", text)              # private by default in Boreas
        self.assertIn("  public function area(real :: r) -> real", text)
        self.assertNotIn("public function scale_of", text)
        self.assertIn("use geometry: area, ratio, tally, pi", text)           # `use geometry` became explicit
        self.assertIn("use counting: triangle, limit, circle_sum", text)
        self.assertIn("  use geometry: pi", text)                             # `use geometry, only: pi` in a module

    def test_unsupported_constructs_become_todos(self):
        text, findings = convert(read(fixture("unsupported.f90")), "unsupported.f90")
        self.assertEqual(len(todos(findings)), 7)
        self.assertEqual(text.count("TODO(convert)"), 7 + 1)      # + the summary line in the header
        self.assertEqual(run_bor(text), ["total: 6", "end"])      # the translatable parts still run
        for line in ("associate (t => total)", "allocate(buf(3))", "p%x = 1.0"):
            self.assertIn(line, text)                               # the original line is quoted


class TranslationTests(unittest.TestCase):
    def test_relational_dot_operators_and_the_real_literal_trap(self):
        text, _ = conv("print *, 1.eq.1, 2.gt.1, 3.ne.3", "")
        self.assertIn("print 1 == 1, 2 > 1, 3 /= 3", text)

    def test_parameter_becomes_const(self):
        text, findings = conv("integer, parameter :: n = 4, sq(2) = (/ 1, 4 /)\n  real, parameter :: half = 0.5\n"
                              "  integer :: k\n  k = n\n  print *, k, sq, half", "")
        self.assertIn("const integer :: n = 4, sq(2) = [1, 4]", text)
        self.assertIn("const real :: half = 0.5", text)
        self.assertFalse(any("ordinary variables" in f[2] for f in findings))
        self.assertEqual(run_bor(text), ["4 [1, 4] 0.5"])

    def test_assigning_a_parameter_fails_loudly_in_boreas(self):
        text, _ = conv("integer, parameter :: n = 4\n  print *, n", "")
        with self.assertRaisesRegex(LangError, "constant"):
            run_bor(text.replace("print n", "n = 5"))

    def test_a_fortran_variable_called_const_is_renamed(self):
        text, _ = conv("integer :: const\n  const = 2\n  print *, const", "")
        self.assertIn("const_ = 2", text)
        self.assertEqual(run_bor(text), ["2"])

    def test_d_exponents_and_kinds(self):
        text, _ = conv("print *, 1.5d0, 2.0e3, 3.0_8, 4_8", "")
        self.assertIn("print 1.5, 2000.0, 3.0, 4", text)

    def test_continuation_comments_and_semicolons(self):
        text, _ = conv("integer :: a, b\n  a = 1; b = &\n    & 2   ! trailing\n  print *, a, &\n     b", "")
        self.assertIn("a = 1\n  b = 2", text)
        self.assertIn("print a, b", text)
        self.assertNotIn("trailing", text)

    def test_strings_with_quotes_and_bangs(self):
        text, _ = conv("""print *, 'it''s ! fine'""", "")
        self.assertIn('print "it\'s ! fine"', text)

    def test_string_with_double_quote_is_a_todo(self):
        text, findings = conv("""print *, 'say "hi"'""", "")
        self.assertEqual(len(todos(findings)), 1)

    def test_precedence_follows_boreas_not_fortran(self):
        text, _ = conv("real :: a, b, c\n  a = 1.0; b = 2.0\n  c = -a ** 2 + (a - (b - a)) * 3.0 - a / (b * a)", "")
        self.assertIn("c = -(a ** 2) + (a - (b - a)) * 3.0 - a / (b * a)".replace("-(a ** 2)", "-a ** 2"), text)

    def test_negation_covers_the_whole_first_term(self):
        text, _ = conv("real :: a, b\n  a = 1.0; b = 2.0\n  a = -a * b", "")
        self.assertIn("a = -(a * b)", text)

    def test_eqv_and_not_get_parentheses(self):
        text, _ = conv("logical :: p, q\n  p = .true.; q = .false.\n  print *, p .eqv. q, .not. p .neqv. q, p .or. q .eqv. p", "")
        self.assertIn("print p == q, (not p) /= q, (p or q) == p", text)

    def test_keyword_names_are_renamed(self):
        text, findings = conv("integer :: in, out, string\n  in = 1; out = 2; string = in + out\n  print *, string", "")
        self.assertIn("in_ = 1", text)
        self.assertIn("string_ = in_ + out_", text)
        self.assertTrue(any("renamed" in f[2] for f in findings))
        self.assertEqual(run_bor(text), ["3"])

    def test_bare_print_and_write(self):
        text, _ = conv("print *\n  write(*,*) 'a', 1\n  write(*,'(a)') 'b'", "")
        self.assertIn('print ""', text)
        self.assertIn('print "a", 1', text)
        self.assertIn('print "b"', text)

    def test_write_to_a_file_unit_is_a_todo(self):
        _, findings = conv("write(10, *) 1", "")
        self.assertEqual(len(todos(findings)), 1)

    def test_exit_and_cycle_and_infinite_do(self):
        text, _ = conv("integer :: i\n  i = 0\n  do\n    i = i + 1\n    if (i < 3) cycle\n    exit\n  end do\n  print *, i", "")
        self.assertEqual(run_bor(text), ["3"])

    def test_fused_end_keywords_and_elseif(self):
        text, _ = conv("integer :: i\n  i = 2\n  if (i == 1) then\n    print *, 'one'\n  elseif (i == 2) then\n    print *, 'two'\n  endif\n  do i = 1, 2\n  enddo", "")
        self.assertEqual(run_bor(text), ["two"])

    def test_labelled_do_is_a_todo_not_a_crash(self):
        text, findings = conv("integer :: i\n  do 10 i = 1, 3\n    print *, i\n10 continue", "")
        self.assertGreaterEqual(len(todos(findings)), 1)
        parse(text)                                            # still valid Boreas

    def test_trailing_stop_is_dropped_but_others_are_counted(self):
        text, findings = conv("print *, 1\n  stop", "")
        self.assertEqual(todos(findings), [])
        self.assertNotIn("stop", text)
        text, findings = conv("integer :: i\n  i = 1\n  if (i == 1) stop\n  print *, 2", "")
        self.assertIn("`stop`", text)
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("1 construct(s)", text)                      # the header count includes it
        src = "subroutine quit()\n  stop 1\nend subroutine\nprogram p\n  call quit()\nend program\n"
        self.assertEqual(len(todos(convert(src)[1])), 1)           # a stop in a procedure is never trailing

    def test_host_variable_used_by_an_internal_procedure_is_flagged(self):
        src = ("program host\n  implicit none\n  integer :: total\n  total = 0\n  call add(5)\n  print *, total\n"
               "contains\n  subroutine add(k)\n    integer, intent(in) :: k\n    total = total + k\n"
               "  end subroutine add\nend program host\n")
        text, findings = convert(src)
        self.assertTrue(any("enclosing program" in f[2] for f in todos(findings)))
        self.assertNotIn("real :: total", text)                    # not silently turned into a local
        self.assertNotIn("integer :: total\n  total = total", text)
        with self.assertRaises(LangError):
            run_bor(text)                                          # fails loudly instead of printing 0

    def test_internal_procedure_inherits_implicit_none(self):
        src = ("program p\n  implicit none\n  call s()\ncontains\n  subroutine s()\n    q = 1.0\n  end subroutine s\n"
               "end program p\n")
        _, findings = convert(src)
        self.assertTrue(any("not declared" in f[2] for f in findings))

    def test_argument_assigned_unconditionally_is_out(self):
        src = "subroutine s(v)\n  integer v\n  v = 1\nend subroutine\nprogram p\n  integer :: k\n  call s(k)\nend program\n"
        self.assertIn("out integer :: v", convert(src)[0])

    def test_argument_assigned_in_a_loop_or_read_is_inout(self):
        src = ("subroutine a(v, n)\n  integer v, n, i\n  do i = 1, n\n    v = i\n  end do\nend subroutine\n"
               "subroutine b(v)\n  integer v\n  v = v + 1\nend subroutine\n"
               "program p\n  integer :: k\n  k = 0\n  call a(k, 3)\n  call b(k)\nend program\n")
        text = convert(src)[0]
        self.assertIn("subroutine a(inout integer :: v", text)
        self.assertIn("subroutine b(inout integer :: v", text)

    def test_intent_propagates_through_calls(self):
        src = ("subroutine outer(x)\n  real x\n  call inner(x)\nend subroutine\n"
               "subroutine inner(y)\n  real y\n  y = 2.0\nend subroutine\n"
               "program p\n  real :: z\n  call outer(z)\n  print *, z\nend program\n")
        text, _ = convert(src)
        self.assertIn("subroutine outer(out real :: x)", text)
        self.assertEqual(run_bor(text), ["2.0"])

    def test_save_and_character_length_are_noted(self):
        src = ("subroutine s()\n  integer, save :: n\n  character(len=3) :: c\n  n = 1\n  c = 'abcdef'\nend subroutine\n"
               "program p\n  call s()\nend program\n")
        _, findings = convert(src)
        self.assertTrue(any("'save'" in f[2] for f in findings))
        self.assertTrue(any("character lengths" in f[2] for f in findings))
        src = "subroutine s()\n  integer n\n  save n\n  n = 1\nend subroutine\nprogram p\nend program\n"
        self.assertTrue(any("'save'" in f[2] for f in convert(src)[1]))

    def test_single_precision_real_is_noted(self):
        _, findings = conv("real :: x\n  x = 1.0", "")
        self.assertTrue(any("single precision" in f[2] for f in findings))

    def test_unknown_procedures_are_todos_in_the_output(self):
        text, findings = conv("print *, nint(2.6)\n  call mystery(1)", "")
        names = [f[2] for f in todos(findings)]
        self.assertTrue(any("nint" in m for m in names) and any("mystery" in m for m in names))
        self.assertIn("TODO(convert): function 'nint'", text)
        self.assertIn("2 construct(s)", text)

    def test_dble_and_kind_arguments(self):
        text, _ = conv("print *, dble(3), real(2, kind=8), int(2.7d0)", "")
        self.assertIn("print real(3), real(2), int(2.7)", text)

    def test_array_sections_and_constructors(self):
        text, _ = conv("integer :: v(5)\n  v = [1, 2, 3, 4, 5]\n  v(2:3) = 0\n  print *, v, v(:2), sum(v)", "")
        self.assertEqual(run_bor(text), ["[1, 0, 0, 4, 5] [1, 0] 10"])

    def test_two_dimensional_declarations(self):
        text, _ = conv("real :: m(2, 3)\n  m = 1.5\n  print *, size(m), shape(m)", "")
        self.assertEqual(run_bor(text), ["6 [2, 3]"])

    def test_lower_bounds_other_than_one_are_a_todo(self):
        _, findings = conv("integer :: v(0:4)\n  v(0) = 1", "")
        self.assertGreaterEqual(len(todos(findings)), 1)

    def test_function_name_is_the_result_variable(self):
        src = "function twice(x)\n  implicit none\n  real, intent(in) :: x\n  real :: twice\n  twice = x * 2.0\nend function\n" \
              "program p\n  implicit none\n  real :: twice\n  print *, twice(2.0)\nend program\n"
        text, findings = convert(src)
        self.assertIn("twice_result", text)
        self.assertEqual(run_bor(text), ["4.0"])

    def test_function_that_modifies_an_argument_is_flagged(self):
        src = "integer function bad(n)\n  integer, intent(inout) :: n\n  n = n + 1\n  bad = n\nend function\n" \
              "program p\n  integer :: k\n  k = 1\n  print *, bad(k)\nend program\n"
        text, findings = convert(src)
        self.assertTrue(any("modifies argument" in f[2] for f in todos(findings)))

    def test_implicit_save_is_flagged(self):
        src = "subroutine counter(n)\n  integer, intent(out) :: n\n  integer :: calls = 0\n  calls = calls + 1\n  n = calls\nend subroutine\n" \
              "program p\n  integer :: k\n  call counter(k)\n  print *, k\nend program\n"
        _, findings = convert(src)
        self.assertTrue(any("SAVE" in f[2] for f in findings))

    def test_module_variable_is_a_todo_and_its_uses_are_skipped(self):
        src = ("module m\n  implicit none\n  integer :: counter = 0\n  integer, parameter :: k = 3\ncontains\n"
               "  subroutine bump()\n    counter = counter + 1\n  end subroutine\n"
               "  function triple(x)\n    integer, intent(in) :: x\n    integer :: triple\n    triple = x * k\n  end function\n"
               "end module m\n"
               "program p\n  use m\n  implicit none\n  call bump()\n  print *, triple(2)\nend program\n")
        text, findings = convert(src)
        self.assertTrue(any("module variable 'counter'" in f[2] for f in todos(findings)))
        self.assertIn("TODO(convert): uses 'counter'", text)
        self.assertEqual(run_bor(text), ["6"])

    def test_unknown_module_and_private_names_are_todos(self):
        src = ("module m\n  implicit none\n  private\n  public :: f\ncontains\n"
               "  function f()\n    integer :: f\n    f = 1\n  end function\n"
               "  function g()\n    integer :: g\n    g = 2\n  end function\nend module\n"
               "program p\n  use other\n  use m, only: f, g\n  implicit none\n  print *, f()\nend program\n")
        text, findings = convert(src)
        msgs = " | ".join(f[2] for f in todos(findings))
        self.assertIn("module 'other' is not defined earlier in this file", msgs)
        self.assertIn("module 'm' has no public 'g'", msgs)
        self.assertEqual(run_bor(text), ["1"])

    def test_names_a_module_reexports_are_imported_from_their_source(self):
        src = ("module a\n  implicit none\n  integer, parameter :: base = 10\nend module a\n"
               "module b\n  use a\n  implicit none\ncontains\n  function bump(x)\n    integer, intent(in) :: x\n"
               "    integer :: bump\n    bump = x + base\n  end function\nend module b\n"
               "program p\n  use b\n  implicit none\n  print *, bump(1), base\nend program\n")
        text, _ = convert(src)
        self.assertIn("use a: base", text)
        self.assertEqual(run_bor(text), ["11 10"])

    def test_module_converts_without_a_program(self):
        text, findings = convert("module m\n  implicit none\ncontains\n  subroutine s()\n  end subroutine\nend module m\n"
                                 "program p\n  print *, 1\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["1"])

    def test_unterminated_block_is_an_error(self):
        with self.assertRaises(LangError):
            convert("program p\n  if (.true.) then\n  print *, 1\nend program\n")


class CommandLineTests(unittest.TestCase):
    def boreas(self, *args):
        return subprocess.run([sys.executable, "-m", "boreas", *args], capture_output=True, text=True,
                              cwd=os.path.join(HERE, ".."))

    def test_convert_to_stdout_and_report_on_stderr(self):
        r = self.boreas("convert", fixture("unsupported.f90"))
        self.assertEqual(r.returncode, 0)
        self.assertIn("program mixed", r.stdout)
        self.assertIn("7 TODO(s)", r.stderr)

    def test_convert_to_file_then_run_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "basic.bor")
            self.assertEqual(self.boreas("convert", fixture("basic.f90"), "-o", out).returncode, 0)
            r = self.boreas(out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("total: 55", r.stdout)

    def test_fixed_form_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.f")
            with open(path, "w") as f:
                f.write("      PROGRAM X\n      END\n")
            r = self.boreas("convert", path)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("fixed-form", r.stderr)


if __name__ == "__main__":
    unittest.main()
