import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor, todos
from boreas.completion import completions
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.navigation import hover
from boreas.parser import parse
from boreas.printer import to_source
from boreas.verify import same_output


class NewSyntaxTests(unittest.TestCase):
    def test_elements_sections_and_assignment(self):
        out = run_bor("""program t
  real[2, 3] :: m = reshape([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [2, 3])
  m[2, :] = 0.0
  println(m[1, 2], m[1, :], m[:, 3], m[1:2, 2:3])
end program""")
        self.assertEqual(out, ["3.0 [1.0, 3.0, 5.0] [5.0, 0.0] [[3.0, 5.0], [0.0, 0.0]]"])

    def test_strings_are_indexed_with_brackets_too(self):
        out = run_bor('program t\n  string :: s = "hello"\n  s[1:1] = "j"\n  println(s, s[2:3])\nend program')
        self.assertEqual(out, ["jello el"])

    def test_allocatable_and_allocate(self):
        out = run_bor("""program t
  allocatable integer[,] :: g
  allocatable real[] :: v
  allocate g[2, 2]
  g[1, 2] = 5
  allocate v[3]
  println(g, size(v))
end program""")
        self.assertEqual(out, ["[[0, 5], [0, 0]] 3"])

    def test_record_array_fields(self):
        out = run_bor("""record team
  integer[3] :: tags
end record
program t
  team :: tm
  tm.tags[2] = 7
  println(tm.tags, tm.tags[2])
end program""")
        self.assertEqual(out, ["[0, 7, 0] 7"])

    def test_array_parameters_state_their_rank(self):
        funcs = """function trace(real[,] :: m) -> real
  return m[1, 1] + m[2, 2]
end function
function total(real[] :: v) -> real
  return sum(v)
end function
subroutine zero(inout integer[] :: v)
  @v = 0
end subroutine
"""
        out = run_bor(funcs + """program t
  real[2, 2] :: a = reshape([1.0, 2.0, 3.0, 4.0], [2, 2])
  integer[2] :: z = [5, 6]
  call zero(z)
  println(trace(a), total(a[:, 1]), z)
end program""")
        self.assertEqual(out, ["5.0 3.0 [0, 0]"])

    def test_a_wrong_rank_or_a_scalar_is_an_error(self):
        funcs = "function f(real[,] :: m) -> real\n  return 1.0\nend function\n"
        for arg, message in [("[1.0, 2.0]", "2-dimensional real array, not a 1-dimensional array"),
                             ("2.0", "2-dimensional real array, not a real")]:
            with self.subTest(arg=arg):
                with self.assertRaises(LangError) as cm:
                    run_bor(funcs + f"program t\n  println(f({arg}))\nend program")
                self.assertIn(message, str(cm.exception))

    def test_a_scalar_parameter_stays_a_number(self):
        out = run_bor("function half(real :: x) -> real\n  return x / 2.0\nend function\n"
                      "program t\n  println(half(3.0))\nend program")
        self.assertEqual(out, ["1.5"])

    def test_callback_signatures_can_take_arrays(self):
        out = run_bor("""function apply(function(real[]) -> real :: f, real[] :: v) -> real
  return f(v)
end function
function total(real[] :: v) -> real
  return sum(v)
end function
program t
  println(apply(total, [1.0, 2.0, 3.0]))
end program""")
        self.assertEqual(out, ["6.0"])

    def test_brackets_never_call_a_function(self):
        with self.assertRaises(LangError) as cm:
            run_bor("program t\n  println(sqrt[4.0])\nend program")
        self.assertIn("call a function with parentheses", str(cm.exception))

    def test_declaration_errors(self):
        for body, message in [("real[] :: v", "needs a size"),
                              ("allocatable real[3] :: v", "no sizes"),
                              ("allocatable real :: v", "needs its rank")]:
            with self.subTest(body=body):
                with self.assertRaises(LangError) as cm:
                    parse(f"program t\n  {body}\nend program")
                self.assertIn(message, str(cm.exception))

    def test_prints_back_in_brackets(self):
        src = """record team
  integer[3] :: tags
end record

program t
  real[2, 2] :: a
  allocatable integer[,] :: g
  team :: tm
  allocate g[2, 2]
  a[1, 2] = tm.tags[1] + g[1, 1]
end program t
"""
        self.assertEqual(to_source(parse(src)), src)


class OldSyntaxIsAnErrorTests(unittest.TestCase):
    def fails(self, src, message):
        with self.assertRaises(LangError) as cm:
            run_bor(src)
        self.assertIn(message, str(cm.exception))

    def test_declarations_and_allocate(self):
        for body, hint in [("real :: v(3)", "write 'real[size] :: v'"),
                           ("allocatable integer :: g(:)", "write 'allocatable integer[] :: g'"),
                           ("allocatable integer :: g", "needs its rank"),
                           ("allocatable integer[] :: g\n  allocate g(2)", "write 'allocate g[...]'")]:
            with self.subTest(body=body):
                self.fails(f"program t\n  {body}\nend program", hint)

    def test_indexing_with_parentheses(self):
        self.fails("program t\n  real[2] :: a = [1.0, 2.0]\n  println(a(2))\nend program",
                   "index with brackets: write a[...], not a(...)")
        self.fails('program t\n  string :: s = "hello"\n  println(s(2:3))\nend program', "write s[...]")
        self.fails("record team\n  integer[3] :: tags\nend record\nprogram t\n  team :: tm\n  println(tm.tags(2))\nend program",
                   "write tags[...]")

    def test_check_finds_it_without_running(self):
        found = check_errors("program t\n  real[2] :: a = [1.0, 2.0]\n  if false then\n    println(a(2))\n  end if\nend program")
        self.assertEqual([(line, msg) for line, _, msg, _ in found],
                         [(4, "index with brackets: write a[...], not a(...)")])
        found = check_errors("function f(real[] :: v) -> real\n  return v(1)\nend function\nprogram t\nend program")
        self.assertEqual([msg for _, _, msg, _ in found], ["index with brackets: write v[...], not v(...)"])

    def test_calls_still_use_parentheses(self):
        self.assertEqual(run_bor("program t\n  println(sqrt(4.0), max(1, 2))\nend program"), ["2.0 2"])


class ConversionTests(unittest.TestCase):
    SRC = """module m
  implicit none
  integer :: table(3) = [1, 2, 3]
  type pair
    integer :: v(2)
  end type
contains
  function trace(a) result(t)
    real, intent(in) :: a(:, :)
    real :: t
    integer :: i
    t = 0.0
    do i = 1, size(a, 1)
      t = t + a(i, i)
    end do
  end function
  function first(v) result(x)
    real, intent(in) :: v(:)
    real :: x
    x = v(1)
  end function
end module
program p
  use m
  implicit none
  real :: a(2, 2)
  type(pair) :: q
  character(len=5) :: s
  a = reshape([1.0, 2.0, 3.0, 4.0], [2, 2])
  q%v(2) = table(3)
  s = 'hello'
  print *, trace(a), first(a(:, 2)), q%v(2), s(2:3), sqrt(a(1, 1))
end program
"""

    def test_arrays_use_brackets_and_calls_keep_parentheses(self):
        text, findings = converted(self.SRC)
        self.assertEqual(todos(findings), [])
        for expected in ("function trace(real32[,] :: a) -> real32", "function first(real32[] :: v) -> real32",
                         "integer[3] :: table", "integer[2] :: v", "t = t + a[i, i]", "x = v[1]", "q.v[2] = table[3]",
                         "s[2:3]", "sqrt(a[1, 1])", "first(a[:, 2])", "trace(a)"):
            self.assertIn(expected, text)
        got = run_bor(text)
        if GFORTRAN:
            self.assertTrue(same_output("\n".join(got), fortran_output(self.SRC)))


class EditorTests(unittest.TestCase):
    SRC = """record team
  integer[3] :: tags
end record
function trace(real[,] :: m) -> real
  return m[1, 1]
end function
program t
  allocatable integer[,] :: grid
  real[2] :: v
  team :: tm
  println(tm.tags[1])
end program
"""

    def hover_on(self, name):
        lines = self.SRC.splitlines()
        row = next(i for i, l in enumerate(lines) if f":: {name}" in l)
        return hover(self.SRC, row, lines[row].index(f":: {name}") + 3)["contents"]["value"]

    def test_hover_shows_the_declaration(self):
        self.assertIn("allocatable integer[,] :: grid", self.hover_on("grid"))
        self.assertIn("real[2] :: v", self.hover_on("v"))
        self.assertIn("real[,] :: m", self.hover_on("m"))

    def test_fields_complete_after_an_indexed_record_and_a_dot(self):
        src = self.SRC.replace("println(tm.tags[1])", "println(tm.")
        row = next(i for i, l in enumerate(src.splitlines()) if "println(tm." in l)
        labels = [c["label"] for c in completions(src, row, len("  println(tm."))]
        self.assertEqual(labels, ["tags"])


if __name__ == "__main__":
    unittest.main()
