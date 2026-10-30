import contextlib, io, os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import survey


class SurveyTests(unittest.TestCase):
    def run_survey(self, files):
        with tempfile.TemporaryDirectory() as d:
            for name, text in files.items():
                with open(os.path.join(d, name), "w") as f:
                    f.write(text)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                survey.main([d])
        return out.getvalue()

    def test_counts_clean_files_and_ranks_reasons(self):
        out = self.run_survey({
            "ok.f90": "program p\n  print *, 1\nend program\n",
            "lib.f90": "subroutine s(a)\n  integer, intent(in) :: a\n  print *, a\nend subroutine\n",
            "bad.f90": "program p\n  integer :: a, b\n  equivalence (a, b)\nend program\n",
            "notes.txt": "not fortran",
        })
        self.assertIn("3 files, 2 converted with no TODOs", out)
        self.assertIn("'X' is not supported: equivalence (a, b)", out)


if __name__ == "__main__":
    unittest.main()
