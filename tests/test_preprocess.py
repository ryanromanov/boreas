import os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import GFORTRAN, run_bor, todos
from boreas.fortran import convert
from boreas.parser import parse
from boreas.preprocess import preprocess

ROOT = os.path.join(os.path.dirname(__file__), "..")


def kept(src, **defines):
    text, findings = preprocess(src, defines)
    return [l for l in text.split("\n") if l.strip()], [m for _, k, m in findings if k == "todo"]


class ConditionTests(unittest.TestCase):
    def test_ifdef_and_else(self):
        src = "#ifdef FAST\nA\n#else\nB\n#endif\n"
        self.assertEqual(kept(src)[0], ["B"])
        self.assertEqual(kept(src, FAST="1")[0], ["A"])

    def test_ifndef(self):
        src = "#ifndef FAST\nA\n#endif\nC\n"
        self.assertEqual(kept(src)[0], ["A", "C"])
        self.assertEqual(kept(src, FAST="1")[0], ["C"])

    def test_if_with_defined_and_operators(self):
        src = "#if defined(A) && !defined(B)\nx\n#elif defined B || N > 2\ny\n#else\nz\n#endif\n"
        self.assertEqual(kept(src, A="1")[0], ["x"])
        self.assertEqual(kept(src, A="1", B="1")[0], ["y"])
        self.assertEqual(kept(src, N="3")[0], ["y"])
        self.assertEqual(kept(src)[0], ["z"])

    def test_values_and_arithmetic(self):
        src = "#if LEVEL >= 2 && (LEVEL * 2) % 4 == 0\nhigh\n#else\nlow\n#endif\n"
        self.assertEqual(kept(src, LEVEL="2")[0], ["high"])
        self.assertEqual(kept(src, LEVEL="1")[0], ["low"])

    def test_nested_blocks(self):
        src = "#ifdef A\n#ifdef B\nab\n#else\na\n#endif\n#else\nnone\n#endif\n"
        self.assertEqual(kept(src, A="1", B="1")[0], ["ab"])
        self.assertEqual(kept(src, A="1")[0], ["a"])
        self.assertEqual(kept(src)[0], ["none"])

    def test_only_the_first_true_branch_is_kept(self):
        src = "#if 1\none\n#elif 1\ntwo\n#else\nthree\n#endif\n"
        self.assertEqual(kept(src)[0], ["one"])

    def test_line_numbers_stay_the_same(self):
        text, _ = preprocess("a\n#ifdef X\nb\n#endif\nc\n", {})
        self.assertEqual(text.split("\n"), ["a", "", "", "", "c", ""])

    def test_a_file_without_directives_is_untouched(self):
        self.assertEqual(preprocess("a\nb\n", {}), ("a\nb\n", []))


class MacroTests(unittest.TestCase):
    def test_define_and_undef(self):
        src = "#define MODE 2\n#if MODE == 2\nyes\n#endif\n#undef MODE\n#ifdef MODE\nno\n#endif\n"
        self.assertEqual(kept(src)[0], ["yes"])

    def test_values_replace_names_outside_strings_and_comments(self):
        text, _ = preprocess("#define N 10\nx = N + 1 ! N here\ns = 'N'\n", {})
        self.assertEqual(text.split("\n")[1:3], ["x = 10 + 1 ! N here", "s = 'N'"])

    def test_function_like_macro_is_a_todo(self):
        _, found = kept("#define SQ(x) ((x)*(x))\n")
        self.assertTrue(any("function-like macro 'SQ'" in m for m in found))


class ProblemTests(unittest.TestCase):
    def test_unevaluable_condition_keeps_its_first_branch_with_a_todo(self):
        lines, found = kept("#if FOO(1)\nfirst\n#else\nsecond\n#endif\n")
        self.assertEqual(lines, ["first"])
        self.assertTrue(any("cannot evaluate" in m for m in found), found)

    def test_unclosed_if_and_stray_endif(self):
        self.assertTrue(any("never closed" in m for m in kept("#ifdef A\nx\n")[1]))
        self.assertTrue(any("without an #if" in m for m in kept("#endif\n")[1]))

    def test_error_directive_in_a_live_branch(self):
        self.assertTrue(any("#error: no way" in m for m in kept("#error no way\n")[1]))
        self.assertEqual(kept("#ifdef X\n#error no way\n#endif\n")[1], [])


class IncludeTests(unittest.TestCase):
    def test_include_from_the_same_directory(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "defs.inc"), "w") as f:
                f.write("#define FAST 1\nfrom_include\n")
            text, found = preprocess('#include "defs.inc"\n#ifdef FAST\nafter\n#endif\n', {}, d)
        self.assertEqual([l for l in text.split("\n") if l.strip()], ["from_include", "after"])
        self.assertEqual([m for _, k, m in found if k == "todo"], [])

    def test_missing_or_system_include_is_a_todo(self):
        with tempfile.TemporaryDirectory() as d:
            _, found = preprocess('#include "nope.inc"\n#include <stdio.h>\n', {}, d)
        self.assertEqual(len([1 for _, k, _ in found if k == "todo"]), 2)


PROGRAM = """program t
  implicit none
  integer :: n
#ifdef BIG
  n = 1000
#else
  n = 1
#endif
  print *, n
end program t
"""


class ConvertTests(unittest.TestCase):
    def test_convert_picks_the_branch_the_macros_pick(self):
        text, f = convert(PROGRAM, "t.f90")
        self.assertEqual(todos(f), [])
        self.assertEqual(run_bor(text), ["1"])
        self.assertTrue(any("no macros" in m or "defined: none" in m for _, _, m in f))
        text, f = convert(PROGRAM, "t.f90", {"BIG": "1"})
        self.assertEqual(run_bor(text), ["1000"])

    def test_a_directive_problem_is_a_todo_in_the_output(self):
        text, f = convert("#if FOO(1)\nprogram t\nend program t\n#endif\n", "t.f90")
        self.assertTrue(todos(f))
        self.assertIn("TODO(convert): cannot evaluate", text)

    def test_fixed_form_directives(self):
        src = "      PROGRAM T\n#ifdef BIG\n      N = 1000\n#else\n      N = 1\n#endif\n      PRINT *, N\n      END\n"
        text, f = convert(src, "t.f")
        self.assertEqual(todos(f), [])
        self.assertEqual(run_bor(text), ["1"])

    def test_the_command_line_option(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.f90")
            with open(path, "w") as f:
                f.write(PROGRAM)
            out = subprocess.run([sys.executable, "-m", "boreas", "convert", "--define", "BIG", path],
                                 capture_output=True, text=True, cwd=ROOT).stdout
        self.assertEqual(run_bor(out), ["1000"])

    @unittest.skipUnless(GFORTRAN, "gfortran not installed")
    def test_same_answer_as_gfortran(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.f90")
            with open(path, "w") as f:
                f.write(PROGRAM)
            for flags, defines in (([], {}), (["-DBIG"], {"BIG": "1"})):
                exe = os.path.join(d, "a.out")
                subprocess.run([GFORTRAN, "-cpp", *flags, path, "-o", exe], check=True, capture_output=True, cwd=d)
                want = subprocess.run([exe], capture_output=True, text=True).stdout.split()
                text, _ = convert(PROGRAM, "t.f90", defines)
                self.assertEqual(run_bor(text), want)


if __name__ == "__main__":
    unittest.main()
