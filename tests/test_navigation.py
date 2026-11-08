"""Go to definition and hover in `boreas lsp`."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.navigation import definition, hover
from test_lsp import URI, open_doc, talk

SRC = """record point
  real :: x
  real :: y
end record

module shapes
  public const real :: scale = 2.0
end module

function area(real :: side, function(real :: x) -> real :: f) -> real
  real :: total = 0.0
  total = f(side) * side  ! total in a comment
  return total
end function

program demo
  use shapes: scale
  point :: p
  real :: total
  p = point(x = 1.0, y = 2.0)
  total = area(p.x, sqrt) * scale
  println("total", total)
end program
"""
LINES = SRC.split("\n")


def at(row_text, word, nth=0):
    """The (line, character) of the nth `word` on the line whose text is `row_text`."""
    row = LINES.index(row_text)
    col = -1
    for _ in range(nth + 1):
        col = LINES[row].index(word, col + 1)
    return row, col


def target(row_text, word, nth=0):
    """Where definition() goes from that word, as (line, character)."""
    loc = definition(SRC, URI, *at(row_text, word, nth))
    return loc and (loc["range"]["start"]["line"], loc["range"]["start"]["character"])


class DefinitionTests(unittest.TestCase):
    def test_local_shadows_global(self):
        # `total` in the program is the program's own, not area's
        self.assertEqual(target("  println(\"total\", total)", "total", 1), at("  real :: total", "total"))

    def test_local_in_procedure(self):
        self.assertEqual(target("  return total", "total"), at("  real :: total = 0.0", "total"))

    def test_parameters_including_a_procedure_parameter(self):
        line = "  total = f(side) * side  ! total in a comment"
        header = "function area(real :: side, function(real :: x) -> real :: f) -> real"
        self.assertEqual(target(line, "side"), at(header, "side"))
        self.assertEqual(target(line, "f"), at(header, ":: f") and (at(header, ":: f")[0], at(header, ":: f")[1] + 3))

    def test_procedure_record_module_and_import(self):
        line = "  total = area(p.x, sqrt) * scale"
        self.assertEqual(target(line, "area"), at("function area(real :: side, function(real :: x) -> real :: f) -> real", "area"))
        self.assertEqual(target("  p = point(x = 1.0, y = 2.0)", "point"), at("record point", "point"))
        self.assertEqual(target(line, "scale"), at("  public const real :: scale = 2.0", "scale"))
        self.assertEqual(target("  use shapes: scale", "shapes"), at("module shapes", "shapes"))

    def test_record_field_after_a_dot(self):
        self.assertEqual(target("  total = area(p.x, sqrt) * scale", "x"), at("  real :: x", "x"))

    def test_nothing_for_builtins_comments_strings_and_unknown_names(self):
        self.assertIsNone(target("  total = area(p.x, sqrt) * scale", "sqrt"))
        self.assertIsNone(target("  total = f(side) * side  ! total in a comment", "total", 1))
        self.assertIsNone(target("  println(\"total\", total)", "total"))
        self.assertIsNone(definition("program t\n  x = 1\nend program\n", URI, 1, 2))


class HoverTests(unittest.TestCase):
    def text(self, row_text, word, nth=0):
        h = hover(SRC, *at(row_text, word, nth))
        return h and h["contents"]["value"]

    def test_variable_shows_its_declaration(self):
        self.assertEqual(self.text("  return total", "total"), "```boreas\nreal :: total = 0.0\n```")

    def test_parameter_and_procedure_parameter(self):
        line = "  total = f(side) * side  ! total in a comment"
        self.assertEqual(self.text(line, "side"), "```boreas\nreal :: side\n```")
        self.assertEqual(self.text(line, "f"), "```boreas\nfunction(real :: x) -> real :: f\n```")

    def test_procedure_record_and_field(self):
        line = "  total = area(p.x, sqrt) * scale"
        self.assertEqual(self.text(line, "area"),
                         "```boreas\nfunction area(real :: side, function(real :: x) -> real :: f) -> real\n```")
        self.assertEqual(self.text("  point :: p", "point"), "```boreas\nrecord point\n  real :: x\n  real :: y\nend record\n```")
        self.assertEqual(self.text(line, "x"), "```boreas\nreal :: x\n(field of point)\n```")

    def test_builtin(self):
        self.assertEqual(self.text("  total = area(p.x, sqrt) * scale", "sqrt"), "built-in function `sqrt`")

    def test_range_covers_the_word(self):
        row, col = at("  return total", "total")
        self.assertEqual(hover(SRC, row, col + 2)["range"],
                         {"start": {"line": row, "character": col}, "end": {"line": row, "character": col + 5}})

    def test_utf16_positions(self):
        src = 'program t\n  string :: s = "é😀"; integer :: n\n  n = 1\nend program\n'
        # `n` after a 3-UTF-16-unit string: the declaration's character counts UTF-16 units
        loc = definition(src, URI, 2, 2)
        line = src.split("\n")[1]
        self.assertEqual(loc["range"]["start"]["character"], len(line[:line.index(":: n") + 3].encode("utf-16-le")) // 2)


class ServerTests(unittest.TestCase):
    def test_capabilities_and_requests(self):
        (init,), _ = talk({"id": 1, "method": "initialize", "params": {}})
        caps = init["result"]["capabilities"]
        self.assertTrue(caps["definitionProvider"])
        self.assertTrue(caps["hoverProvider"])
        row, col = at("  return total", "total")
        replies, _ = talk(open_doc(SRC),
                          {"id": 2, "method": "textDocument/definition",
                           "params": {"textDocument": {"uri": URI}, "position": {"line": row, "character": col}}},
                          {"id": 3, "method": "textDocument/hover",
                           "params": {"textDocument": {"uri": URI}, "position": {"line": row, "character": col}}})
        by_id = {r["id"]: r for r in replies if "id" in r}
        self.assertEqual(by_id[2]["result"]["range"]["start"]["line"], LINES.index("  real :: total = 0.0"))
        self.assertIn("real :: total = 0.0", by_id[3]["result"]["contents"]["value"])


if __name__ == "__main__":
    unittest.main()
