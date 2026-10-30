"""The CotEditor/Graupel syntax highlights every keyword, type and built-in the language knows,
so that adding one to the language without adding it to the editor syntax fails here."""
import json, os, re, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.interp import BUILTINS, ELEMENTAL
from boreas.parser import KEYWORDS, TYPES

HIGHLIGHTS = os.path.join(os.path.dirname(__file__), "..", "editors", "coteditor", "Boreas.cotsyntax",
                          "Regex", "Highlights.json")


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


if __name__ == "__main__":
    unittest.main()
