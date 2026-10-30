"""`boreas lsp` completion: names that fit where the cursor is, even in half-typed code."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.completion import completions
from test_lsp import URI, open_doc, talk

SRC = """record point
  real :: x
  real :: y
end record

record segment
  point :: from
  point :: to
end record

function square(real :: side) -> real
  real :: area
  area = side * side
  return area
end function

program demo
  point :: p
  segment :: s
  integer :: total = 0
CURSOR
end program
"""


def labels(line_text, src=SRC):
    """The labels offered at the end of `line_text` written in place of CURSOR."""
    src = src.replace("CURSOR", line_text)
    row = src.split("\n").index(line_text)
    return [item["label"] for item in completions(src, row, len(line_text))]


class CompletionTests(unittest.TestCase):
    def test_prefix_matches_declared_names_types_builtins_and_keywords(self):
        self.assertEqual(labels("  t"), ["total", "tan", "tanh", "tiny", "transpose", "trim", "then", "true"])
        self.assertEqual(labels("  INT"), ["int16", "int32", "int64", "int8", "integer"])

    def test_sort_text_keeps_the_order(self):
        src = SRC.replace("CURSOR", "  t")
        items = completions(src, src.split("\n").index("  t"), 3)
        self.assertEqual(sorted(items, key=lambda i: i["sortText"]), items)

    def test_names_keep_their_spelling_and_match_any_case(self):
        self.assertEqual(labels("  SQ"), ["square", "sqrt"])

    def test_names_of_other_procedures_are_hidden(self):
        self.assertNotIn("area", labels("  a"))
        self.assertNotIn("side", labels("  s"))
        self.assertIn("side", labels("  s", SRC.replace("  area = side", "CURSOR\n  area = side")))

    def test_record_fields_after_a_dot(self):
        self.assertEqual(labels("  p."), ["x", "y"])
        self.assertEqual(labels("  s.to."), ["x", "y"])
        self.assertEqual(labels("  total = square(s.f"), ["from"])
        self.assertEqual(labels("  nothing."), [])
        self.assertEqual(labels("  total = 1."), [])

    def test_fields_are_not_offered_as_names(self):
        self.assertNotIn("from", labels("  f"))

    def test_nothing_in_comments_or_strings(self):
        self.assertEqual(labels("  ! t"), [])
        self.assertEqual(labels('  print "t'), [])
        self.assertIn("total", labels('  print "x", t'))

    def test_the_word_already_typed_is_not_offered(self):
        self.assertNotIn("total", labels("  total"))

    def test_works_on_code_that_does_not_parse(self):
        self.assertIn("total", labels("  if t"))
        self.assertEqual(labels("  x = (1 + p."), ["x", "y"])

    def test_detail_describes_the_name(self):
        src = SRC.replace("CURSOR", "  squ")
        (item,) = completions(src, src.split("\n").index("  squ"), 5)
        self.assertEqual(item, {"label": "square", "kind": 3, "detail": "function square(real :: side) -> real",
                                "sortText": "0000"})


class ServerTests(unittest.TestCase):
    def test_initialize_advertises_completion_after_a_dot(self):
        (r,), _ = talk({"id": 1, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["capabilities"]["completionProvider"], {"triggerCharacters": ["."]})

    def test_completion_request_uses_the_open_document(self):
        src = SRC.replace("CURSOR", "  p.")
        row = src.split("\n").index("  p.")
        replies, _ = talk(open_doc(src), {"id": 3, "method": "textDocument/completion", "params": {
            "textDocument": {"uri": URI}, "position": {"line": row, "character": 4}}})
        self.assertEqual(replies[1]["id"], 3)
        self.assertFalse(replies[1]["result"]["isIncomplete"])
        self.assertEqual([i["label"] for i in replies[1]["result"]["items"]], ["x", "y"])


if __name__ == "__main__":
    unittest.main()
