import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.interp import Interp
from boreas.parser import parse


def run(body, decls=""):
    out = []
    Interp(parse(f"program t\n{decls}\n  print {body}\nend program t\n"), out.append).run()
    return out[0].rstrip("\n")


class TextTests(unittest.TestCase):
    def test_numbers_and_logicals_read_as_print_shows_them(self):
        self.assertEqual(run('"n = " + text(7) + ", x = " + text(2.5) + ", done = " + text(true)'),
                         "n = 7, x = 2.5, done = true")

    def test_strings_come_back_unchanged(self):
        self.assertEqual(run('"[" + text("abc") + "]"'), "[abc]")

    def test_arrays_and_negative_numbers(self):
        self.assertEqual(run("text([1, 2, 3]) + text(-4)"), "[1, 2, 3]-4")

    def test_matches_what_print_writes(self):
        decls = "  real32 :: a = 0.1\n  real :: b = 1.0 / 3.0\n  integer :: n = 12"
        for name in ("a", "b", "n"):
            with self.subTest(name=name):
                self.assertEqual(run(f"text({name})", decls), run(name, decls))

    def test_needs_one_value(self):
        with self.assertRaisesRegex(Exception, "needs one value"):
            run("text(1, 2)")


if __name__ == "__main__":
    unittest.main()
