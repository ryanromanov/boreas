"""The CotEditor/Graupel syntax highlights every keyword, type and built-in the language knows,
so that adding one to the language without adding it to the editor syntax fails here."""
import json, os, re, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.interp import BUILTINS, ELEMENTAL
from boreas.parser import KEYWORDS, TYPES

HIGHLIGHTS = os.path.join(os.path.dirname(__file__), "..", "editors", "coteditor", "Boreas.cotsyntax",
                          "Regex", "Highlights.json")
EDIT = os.path.join(os.path.dirname(__file__), "..", "editors", "coteditor", "Boreas.cotsyntax", "Edit.json")


def highlighted(*categories):
    """The words matched by the highlight rules of the categories."""
    with open(HIGHLIGHTS) as f:
        rules = json.load(f)
    words = set()
    for category in categories:
        for rule in rules[category]:
            words |= set(re.findall(r"[a-z_][a-z0-9_]*", rule["begin"]))
    return words


class EditorSyntaxTests(unittest.TestCase):
    def test_keywords(self):
        # `in`/`out`/`inout` are highlighted as types and `true`/`false`/`null` as values
        missing = KEYWORDS - highlighted("keywords", "types", "values")
        self.assertFalse(missing, f"add to Highlights.json keywords: {sorted(missing)}")

    def test_types(self):
        missing = TYPES - highlighted("types")
        self.assertFalse(missing, f"add to Highlights.json types: {sorted(missing)}")

    def test_builtins(self):
        missing = (set(BUILTINS) | set(ELEMENTAL)) - highlighted("commands")
        self.assertFalse(missing, f"add to Highlights.json commands: {sorted(missing)}")

    def test_builtin_rule_matches_calls(self):
        with open(HIGHLIGHTS) as f:
            (rule,) = json.load(f)["commands"]
        pattern = re.compile(rule["begin"], re.I)
        self.assertTrue(pattern.search("x = try_open(path)"))
        self.assertFalse(pattern.search("x = trimmed(y)"))

    def test_rules_in_graupel_order(self):
        # Graupel's bundled-syntax test wants each category sorted by its begin string
        with open(HIGHLIGHTS) as f:
            rules = json.load(f)
        for category, items in rules.items():
            begins = [(r["begin"], r.get("end") or "") for r in items]
            self.assertEqual(begins, sorted(begins), f"sort the {category} rules in Highlights.json")



def indents_after(line):
    """Whether Graupel indents the line after `line`, by the block delimiters in Edit.json.
    A `regularExpression` rule is matched against the whole line; a plain one must end it."""
    with open(EDIT) as f:
        rules = json.load(f)["indentation"]["blockDelimiters"]
    for rule in rules:
        flags = re.I if rule.get("ignoreCase") else 0
        if rule.get("regularExpression"):
            if re.search(rule["begin"], line, flags):
                return True
        elif re.search(r"(?<![A-Za-z0-9_])" + re.escape(rule["begin"]) + r"\Z", line, flags):
            return True
    return False


class EditorIndentTests(unittest.TestCase):
    def test_default_width_matches_the_converter(self):
        # Graupel uses this width for new Boreas files; it has to be the one `boreas convert` writes
        from boreas.fortran import convert
        with open(EDIT) as f:
            indentation = json.load(f)["indentation"]
        source, _ = convert("program p\n  implicit none\n  integer :: i\n  do i = 1, 3\n"
                            "    if (i > 1) then\n      print *, i\n    end if\n  end do\nend program\n")
        indents = sorted({len(l) - len(l.lstrip(" ")) for l in source.splitlines() if l.startswith(" ")})
        self.assertTrue(indents, source)
        self.assertEqual(indents[0], indentation["width"], source)
        self.assertTrue(indentation["expandsTab"])
        self.assertFalse(any(l.startswith("\t") for l in source.splitlines()))

    def test_blocks_indent(self):
        for line in ["program demo", "module geometry", "record point", "public record point",
                     "function square(real :: x) -> real", "public function f() -> int",
                     "subroutine swap(inout real :: a)", "do i = 1, n", "do while (x < 3)",
                     "outer: do i = 1, 10", "DO I = 1, N", "select case (x)", "case 1, 3", "case default",
                     "if x then", "if x then  ! note", "else", "else if y then", "ELSE"]:
            self.assertTrue(indents_after(line), line)

    def test_one_line_forms_and_ends_do_not(self):
        for line in ['case 1, 3 -> "small"', "case default -> 2", "end do", "end function", "end if",
                     "end select", "! do this", 'print "and then"', "x = 1 ! then", "call do_it()",
                     "done = true"]:
            self.assertFalse(indents_after(line), line)


if __name__ == "__main__":
    unittest.main()
