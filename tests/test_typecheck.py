"""The static type checker: the type it works out for expressions, and a check of the checker against the interpreter."""
import glob, json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from boreas.interp import Interp
from boreas.parser import parse, Print
from boreas.tree import statements_in
from boreas.typecheck import infer

ROOT = os.path.join(os.path.dirname(__file__), "..")

DEFS = """record point
  real :: x = 0.0
  real[3] :: v = 0.0
  function norm(self) -> real
    return self.x
  end function
end record
function twice(real :: x) -> real
  return x * 2.0
end function
function pair(integer :: a) -> (integer :: q, integer :: r)
  return a, a
end function
"""
DECLS = """  integer :: i = 1
  int64 :: big = 5
  int8 :: small = 1
  real :: x = 1.0
  real32 :: s = 1.0
  complex :: z = cmplx(1.0, 2.0)
  logical :: ok = true
  string :: t = "a"
  integer[3] :: a = [1, 2, 3]
  real[2, 2] :: m = [1.0, 2.0; 3.0, 4.0]
  real[3] :: r = [1.0, 2.0, 3.0]
  string[2] :: w = ["a", "b"]
  point :: p
  point[2] :: ps
  real? :: opt = null
"""


def type_of(expr):
    """The static type, as text, of `expr` written in a program with the DECLS above."""
    unit = parse(DEFS + "program t\n" + DECLS + "  println(" + expr + ")\nend program\n")
    interp = Interp(unit, lambda s: None)
    checker = infer(unit, interp)
    last = [s for s in statements_in(unit.program.body) if isinstance(s, Print)][-1]
    return str(checker.types[id(last.args[0])])


CASES = [
    # literals and variables
    ("1", "integer"), ("3000000000", "int64"), ("2.5", "real"), ('"a"', "string"), ("true", "logical"),
    ("i", "integer"), ("big", "int64"), ("small", "int8"), ("s", "real32"), ("z", "complex"), ("pi", "real"),
    ("a", "integer[]"), ("m", "real[,]"), ("p", "point"), ("opt", "real?"),
    # arithmetic: the wider kind wins, a real beats an integer, a single stays single next to a literal
    ("i + 1", "integer"), ("i + big", "int64"), ("i + small", "integer"), ("i + x", "real"), ("i / 2", "integer"),
    ("i + 2.5", "real"), ("s * 2.0", "real32"), ("2.0 * s", "real32"), ("s * x", "real"), ("s + i", "real32"),
    ("z * 2", "complex"), ("-i", "integer"), ("not ok", "logical"), ("i ** 2", "integer"), ("x ** 2", "real"),
    ('t + "b"', "string"), ("i +% 1", "integer"),
    # comparisons and logic
    ("i < 2", "logical"), ("a > 1", "logical[]"), ("m == m", "logical[,]"), ("ok and i > 1", "logical"),
    # arrays: elementwise, subscripts, slices
    ("a * 2", "integer[]"), ("a + r", "real[]"), ("m * 2.0", "real[,]"), ("a[1]", "integer"), ("m[1, 2]", "real"),
    ("m[1, :]", "real[]"), ("m[:, :]", "real[,]"), ("a[1:2]", "integer[]"), ("t[1:2]", "string"), ("t[1]", "string"),
    ("w[1]", "string"), ("[1, 2, 3]", "integer[]"), ("[1.0, 2.0]", "real[]"), ("[1, 2; 3, 4]", "integer[,]"),
    ("[(i * 2 do i = 1, 3)]", "integer[]"),
    # records
    ("p.x", "real"), ("p.v", "real[]"), ("p.v[2]", "real"), ("p.norm()", "real"), ("ps[1].x", "real"),
    ("point(x = 1.0)", "point"),
    # built-in functions
    ("sqrt(x)", "real"), ("sqrt(s)", "real32"), ("sqrt(i)", "real"), ("sqrt(r)", "real[]"), ("abs(i)", "integer"),
    ("abs(z)", "real"), ("abs(a)", "integer[]"), ("mod(i, 2)", "integer"), ("real(i)", "real"), ("int(x)", "integer"),
    ("int64(i)", "int64"), ("real32(x)", "real32"), ("max(i, 2)", "integer"), ("max(a, 2)", "integer[]"),
    ("max(i, x)", "unknown"), ("size(a)", "integer"), ("shape(m)", "integer[]"), ("len(t)", "integer"),
    ("sum(a)", "integer"), ("sum(r)", "real"), ("sum(m, 1)", "real[]"), ("maxval(r)", "real"), ("any(a > 1)", "logical"),
    ("any(m > 1.0, 1)", "logical[]"), ("count(a > 1)", "integer"), ("maxloc(a)", "integer"), ("maxloc(m)", "integer[]"),
    ("matmul(m, m)", "real[,]"), ("matmul(m, r[1:2])", "real[]"), ("dot_product(a, a)", "integer"),
    ("transpose(m)", "real[,]"), ("flatten(m)", "real[]"), ("norm2(r)", "real"), ("upper(t)", "string"),
    ("split(t, \" \")", "string[]"), ("contains(t, \"a\")", "logical"), ("iand(i, 3)", "integer"),
    ("btest(i, 0)", "logical"), ("random()", "real"), ("to_integer(t)", "integer?"), ("huge(1)", "integer"),
    # user functions, also applied to an array
    ("twice(x)", "real"), ("twice(r)", "real[]"), ("twice(i)", "real"),
    # optional values
    ("opt ?? 0.5", "real"), ("opt ?? s", "unknown"),
    # decided by the value, so not typed yet
    ("floor(x)", "unknown"), ("nint(x)", "unknown"), ("ceiling(r)", "unknown"),
]


class TypeOfTests(unittest.TestCase):
    def test_expression_types(self):
        wrong = []
        for expr, want in CASES:
            try:
                got = type_of(expr)
            except Exception as err:
                got = f"{type(err).__name__}: {err}"
            if got != want:
                wrong.append(f"{expr}: got {got}, want {want}")
        self.assertEqual(wrong, [])

    def test_unknown_is_never_an_error(self):
        self.assertEqual(type_of("nonsense_name"), "unknown")

    def test_types_that_depend_on_values_are_listed(self):
        unit = parse(DEFS + "program t\n" + DECLS + "  println(floor(x), nint(x), opt ?? s)\nend program\n")
        checker = infer(unit, Interp(unit, lambda s: None))
        what = " ".join(w for _, w in checker.value_dependent)
        self.assertIn("floor()", what)
        self.assertIn("nint()", what)
        self.assertIn("??", what)


class OracleTests(unittest.TestCase):
    """Run programs with BOREAS_TYPE_ORACLE: every value the interpreter computes must fit the static type."""

    def run_oracle(self, paths):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "oracle.jsonl")
            for path in paths:
                subprocess.run([sys.executable, "-m", "boreas", path], cwd=ROOT, stdin=subprocess.DEVNULL,
                               capture_output=True, env={**os.environ, "BOREAS_TYPE_ORACLE": log}, timeout=120)
            rows = [json.loads(line) for line in open(log)] if os.path.exists(log) else []
        return rows

    def test_the_examples_agree_with_the_interpreter(self):
        paths = sorted(glob.glob(os.path.join(ROOT, "examples", "*.bor")))
        rows = self.run_oracle(paths)
        bad = [r for r in rows if "why" in r or "crash" in r]
        self.assertEqual(bad, [])
        counts = [r["counts"] for r in rows if "counts" in r]
        evaluated = sum(c["evaluated"] for c in counts)
        typed = sum(c["typed"] for c in counts)
        self.assertGreater(evaluated, 1000)
        self.assertGreater(typed / evaluated, 0.9)


if __name__ == "__main__":
    unittest.main()
