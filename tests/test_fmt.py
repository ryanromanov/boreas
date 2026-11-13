"""`boreas fmt` and LSP formatting: re-indent by block structure, changing nothing else."""
import ast, glob, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.formatter import format_source, formatting_edits
from boreas.lexer import LangError, lex
from boreas.parser import parse
from test_lsp import URI, open_doc, talk

ROOT = os.path.join(os.path.dirname(__file__), "..")


def fmt(src, **kw):
    return format_source(src, **kw)


class Indentation(unittest.TestCase):

    def test_blocks(self):
        src = """module geometry
public function area(real :: w, real :: h) -> real
return w * h
end function
end module

record point
real :: x
function norm(self) -> real
return sqrt(self.x ** 2)
end function
end record

interface shape
function area(self) -> real
subroutine grow(self, real :: k)
end interface

program t
integer :: i
if i > 0 then
println("pos")
else if i < 0 then
println("neg")
else
println("zero")
end if
outer: do i = 1, 3
do while false
exit outer
end do
end do outer
end program
"""
        self.assertEqual(fmt(src), """module geometry
  public function area(real :: w, real :: h) -> real
    return w * h
  end function
end module

record point
  real :: x
  function norm(self) -> real
    return sqrt(self.x ** 2)
  end function
end record

interface shape
  function area(self) -> real
  subroutine grow(self, real :: k)
end interface

program t
  integer :: i
  if i > 0 then
    println("pos")
  else if i < 0 then
    println("neg")
  else
    println("zero")
  end if
  outer: do i = 1, 3
    do while false
      exit outer
    end do
  end do outer
end program
""")

    def test_select_case_lines_sit_one_level_in(self):
        src = """program t
select case 2
case 1
println(1)
case default
select type x as p
case rect
println(p.w)
case default
end select
end select
end program
"""
        self.assertEqual(fmt(src), """program t
  select case 2
    case 1
      println(1)
    case default
      select type x as p
        case rect
          println(p.w)
        case default
      end select
  end select
end program
""")

    def test_select_expression_arms(self):
        src = """program t
string :: s
s = select case 1
case 1 -> "one"
case default -> "other"
end select
println(s)
end program
"""
        self.assertEqual(fmt(src), """program t
  string :: s
  s = select case 1
    case 1 -> "one"
    case default -> "other"
  end select
  println(s)
end program
""")

    def test_select_expression_starting_after_another_ends(self):
        src = ("program t\nprintln(select case 1\ncase 1 -> 2\ncase default -> 3\n"
               "end select, select case 2\ncase 2 -> 4\ncase default -> 5\nend select)\nend program\n")
        self.assertEqual(fmt(src), ("program t\n  println(select case 1\n    case 1 -> 2\n    case default -> 3\n"
                                    "  end select, select case 2\n    case 2 -> 4\n    case default -> 5\n"
                                    "  end select)\nend program\n"))

    def test_generators_open_nothing(self):
        src = "program t\ninteger[] :: a = [(i do i = 1, 3)]\nprintln(a)\nend program\n"
        self.assertEqual(fmt(src), "program t\n  integer[] :: a = [(i do i = 1, 3)]\n  println(a)\nend program\n")

    def test_select_expressions_inside_other_statements(self):
        src = ("program t\ninteger :: i = 0\n"
               "if select case i\ncase 0 -> 1\ncase default -> 2\nend select == 1 then\nprintln(\"one\")\nend if\n"
               "do while select case i\ncase 0 -> true\ncase default -> false\nend select\ni = i + 1\nend do\n"
               "println(i, select case i\ncase 1 -> \"one\"\ncase default -> \"other\"\nend select)\nend program\n")
        parse(src)
        self.assertEqual(fmt(src), ("program t\n  integer :: i = 0\n"
                                    "  if select case i\n    case 0 -> 1\n    case default -> 2\n  end select == 1 then\n"
                                    "    println(\"one\")\n  end if\n"
                                    "  do while select case i\n    case 0 -> true\n    case default -> false\n  end select\n"
                                    "    i = i + 1\n  end do\n"
                                    "  println(i, select case i\n    case 1 -> \"one\"\n    case default -> \"other\"\n"
                                    "  end select)\nend program\n"))

    def test_procedure_types_are_not_definitions(self):
        src = "subroutine each(subroutine(real) :: show)\ncall show(1.0)\nend subroutine\n"
        self.assertEqual(fmt(src), "subroutine each(subroutine(real) :: show)\n  call show(1.0)\nend subroutine\n")

    def test_indent_width_and_tabs(self):
        src = "program t\nif true then\nprintln(1)\nend if\nend program\n"
        self.assertEqual(fmt(src, indent=4), "program t\n    if true then\n        println(1)\n    end if\nend program\n")
        self.assertEqual(fmt(src, tabs=True), "program t\n\tif true then\n\t\tprintln(1)\n\tend if\nend program\n")


class WhatStaysTheSame(unittest.TestCase):

    def test_comments_strings_case_and_spacing(self):
        src = ('program T\n'
               '! Says hi.\n'
               '      PrintLn("if x then   ! not a comment",   1)   ! trailing comment\n'
               'end program\n')
        self.assertEqual(fmt(src), ('program T\n'
                                    '  ! Says hi.\n'
                                    '  PrintLn("if x then   ! not a comment",   1)   ! trailing comment\n'
                                    'end program\n'))

    def test_keywords_in_strings_and_comments_open_nothing(self):
        src = 'program t\nprintln("do i = 1, 2")  ! if x then\nprintln(2)\nend program\n'
        self.assertEqual(fmt(src), 'program t\n  println("do i = 1, 2")  ! if x then\n  println(2)\nend program\n')

    def test_blank_lines_kept_and_trailing_whitespace_removed(self):
        src = "program t   \n\n\n  \t\nprintln(1)\t\nend program\n"
        self.assertEqual(fmt(src), "program t\n\n\n\n  println(1)\nend program\n")

    def test_continuation_lines_keep_their_offset(self):
        src = ("program t\n"
               "println(1, &\n"
               "        2)\n"
               "  println(3, &\n"
               "  4)\n"
               "end program\n")
        self.assertEqual(fmt(src), ("program t\n"
                                    "  println(1, &\n"
                                    "          2)\n"
                                    "  println(3, &\n"
                                    "    4)\n"
                                    "end program\n"))

    def test_line_endings_and_missing_final_newline(self):
        self.assertEqual(fmt("program t\r\nprintln(1)\r\nend program"), "program t\r\n  println(1)\r\nend program")

    def test_code_that_does_not_parse_still_formats(self):
        self.assertEqual(fmt("program t\nif x then\nprintln(\nend program\n"),
                         "program t\n  if x then\n    println(\n  end program\n")
        self.assertEqual(fmt("end if\nend if\nx = 1\n"), "end if\nend if\nx = 1\n")

    def test_unreadable_character_is_an_error(self):
        with self.assertRaises(LangError):
            fmt("program t\n  x = 1 @ 2\nend program\n")


def sources():
    """Every Boreas program in the repository that parses: examples and the programs in the tests."""
    found = []
    for path in sorted(glob.glob(os.path.join(ROOT, "examples", "*.bor"))):
        with open(path) as f:
            found.append((os.path.relpath(path, ROOT), f.read()))
    for path in sorted(glob.glob(os.path.join(ROOT, "tests", "*.py"))):
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "end " in node.value:
                try:
                    parse(node.value)
                except Exception:
                    continue
                found.append((f"{os.path.relpath(path, ROOT)}:{node.lineno}", node.value))
    return found


def apply(text, edits):
    """`text` with LSP edits applied, positions counted in UTF-16 the way an editor counts them."""
    units = text.encode("utf-16-le")
    starts = [0]
    for i in range(0, len(units), 2):
        if units[i:i + 2] == b"\n\x00":
            starts.append(i // 2 + 1)
    def offset(p):
        return 2 * (starts[p["line"]] + p["character"])
    for e in sorted(edits, key=lambda e: offset(e["range"]["start"]), reverse=True):
        units = (units[:offset(e["range"]["start"])] + e["newText"].encode("utf-16-le")
                 + units[offset(e["range"]["end"]):])
    return units.decode("utf-16-le")


class OnEveryProgram(unittest.TestCase):
    """Formatting never changes what a program means, and formatting twice changes nothing more."""

    def test_same_tokens_and_idempotent(self):
        programs = sources()
        self.assertGreater(len(programs), 50)
        for name, src in programs:
            with self.subTest(name):
                once = format_source(src)
                self.assertEqual([(t.kind, t.text, t.line) for t in lex(once)],
                                 [(t.kind, t.text, t.line) for t in lex(src)])
                self.assertEqual(format_source(once), once)
                for indent, tabs in ((4, False), (2, True)):
                    other = format_source(src, indent, tabs)
                    self.assertEqual([(t.kind, t.text) for t in lex(other)], [(t.kind, t.text) for t in lex(src)])

    def test_converter_output_is_already_formatted(self):
        from boreas.printer import to_source
        for name, src in sources():
            with self.subTest(name):
                printed = to_source(parse(src))
                self.assertEqual(format_source(printed), printed)

    def test_examples_are_already_formatted(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "examples", "*.bor"))):
            with self.subTest(path), open(path) as f:
                src = f.read()
                self.assertEqual(format_source(src), src)


class LanguageServer(unittest.TestCase):

    def test_advertised(self):
        replies, _ = talk({"id": 1, "method": "initialize", "params": {}})
        self.assertIs(replies[0]["result"]["capabilities"]["documentFormattingProvider"], True)

    def request(self, text, options):
        replies, _ = talk(open_doc(text), {"id": 2, "method": "textDocument/formatting",
                                           "params": {"textDocument": {"uri": URI}, "options": options}})
        return next(r for r in replies if r.get("id") == 2)

    def test_edits_touch_only_the_whitespace_that_changes(self):
        text = "program t\nprintln(\"é😀\")   \n  \t\n  end program"
        reply = self.request(text, {"tabSize": 3, "insertSpaces": True})
        self.assertEqual(reply["result"], [
            {"range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 0}}, "newText": "   "},
            {"range": {"start": {"line": 1, "character": 14}, "end": {"line": 1, "character": 17}}, "newText": ""},
            {"range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 3}}, "newText": ""},
            {"range": {"start": {"line": 3, "character": 0}, "end": {"line": 3, "character": 2}}, "newText": ""}])
        tabs = self.request(text, {"tabSize": 8, "insertSpaces": False})
        self.assertEqual(apply(text, tabs["result"]), "program t\n\tprintln(\"é😀\")\n\nend program")

    def test_already_formatted_gives_no_edits(self):
        self.assertEqual(self.request("program t\n  println(1)\nend program\n", {"tabSize": 2, "insertSpaces": True})["result"], [])

    def test_lines_already_right_get_no_edit(self):
        src = "program t\n  println(1)\nprintln(2)\nend program\n"
        self.assertEqual([e["range"]["start"]["line"] for e in formatting_edits(src, {})], [2])

    def test_edits_give_what_fmt_gives(self):
        for name, src in sources() + [("crlf", "program t\r\nprintln(1)  \r\n end program")]:
            for options, indent, tabs in (({}, 2, False), ({"tabSize": 4}, 4, False),
                                          ({"tabSize": 4, "insertSpaces": False}, 4, True)):
                with self.subTest(name, options=options):
                    edits = formatting_edits(src, options)
                    self.assertEqual(apply(src, edits), format_source(src, indent, tabs))

    def test_unreadable_text_is_an_error_not_an_edit(self):
        reply = self.request("program t\nx = 1 @ 2\nend program\n", {"tabSize": 2, "insertSpaces": True})
        self.assertIn("error", reply)


class Command(unittest.TestCase):

    def run_boreas(self, *args):
        return subprocess.run([sys.executable, "-m", "boreas", "fmt", *args], cwd=ROOT,
                              capture_output=True, text=True)

    def test_rewrites_files_and_check_reports_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.bor")
            with open(path, "w") as f:
                f.write("program t\nprintln(1)\nend program\n")
            check = self.run_boreas("--check", path)
            self.assertEqual(check.returncode, 1)
            self.assertIn("would be reformatted", check.stdout)
            with open(path) as f:
                self.assertEqual(f.read(), "program t\nprintln(1)\nend program\n")
            self.assertEqual(self.run_boreas("--indent", "4", path).returncode, 0)
            with open(path) as f:
                self.assertEqual(f.read(), "program t\n    println(1)\nend program\n")
            self.assertEqual(self.run_boreas("--check", "--indent", "4", path).returncode, 0)

    def test_usage_errors(self):
        self.assertNotEqual(self.run_boreas().returncode, 0)
        self.assertNotEqual(self.run_boreas("--indent", "0", "x.bor").returncode, 0)


if __name__ == "__main__":
    unittest.main()
