"""pi, dim on the reductions, the bit functions and the Bessel functions (second review)."""
import math, os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_callback_convert import converted, fortran_output
from test_fortran import GFORTRAN, run_bor
from boreas.lexer import LangError
from boreas.verify import same_output
from boreas.cli import check_errors


def show(body):
    return run_bor("program t\n" + body + "\nend program\n")


def fails(case, body, message):
    with case.assertRaises(LangError) as cm:
        show(body)
    case.assertIn(message, str(cm.exception))


M = "  integer[2, 3] :: m = reshape([1, 2, 3, 4, 5, 6], [2, 3])\n"


class PiTests(unittest.TestCase):
    def test_pi_is_there(self):
        self.assertEqual(show("  println(pi, sin(pi / 2))"), [f"{math.pi} 1.0"])

    def test_a_program_can_declare_its_own(self):
        self.assertEqual(show("  const real :: pi = 3.0\n  println(pi)"), ["3.0"])
        self.assertEqual(show("  real :: pi = 3.5\n  pi = 4.0\n  println(pi)"), ["4.0"])

    def test_it_cannot_be_assigned(self):
        fails(self, "  pi = 3.0", "constant")

    def test_check_knows_it(self):
        self.assertEqual(check_errors("program t\n  println(pi)\nend program\n"), [])


class DimTests(unittest.TestCase):
    def test_sum_product_maxval_minval(self):
        self.assertEqual(show(M + "  println(sum(m, 1), sum(m, 2), sum(m))"), ["[3, 7, 11] [9, 12] 21"])
        self.assertEqual(show(M + "  println(maxval(m, 1), minval(m, 2), product(m, 2))"), ["[2, 4, 6] [1, 2] [15, 48]"])

    def test_any_all_count(self):
        body = "  logical[2, 2] :: m = reshape([true, false, true, true], [2, 2])\n  println(any(m, 1), all(m, 2), count(m, 1), count(m))"
        self.assertEqual(show(body), ["[true, true] [true, false] [1, 2] 3"])

    def test_a_vector_gives_one_number(self):
        self.assertEqual(show("  println(sum([1, 2, 3], 1), maxval([1, 5, 2], 1))"), ["6 5"])

    def test_dim_must_fit_the_rank(self):
        for dim in (0, 3):
            fails(self, M + f"  println(sum(m, {dim}))", "dimension must be 1..2")


class BitTests(unittest.TestCase):
    def test_and_or_xor_not(self):
        self.assertEqual(show("  println(iand(12, 10), ior(12, 10), ieor(12, 10), bitnot(5), bitnot(0))"), ["8 14 6 -6 -1"])

    def test_shifts(self):
        self.assertEqual(show("  println(ishft(1, 4), ishft(16, -2), ishft(-1, -28), ishft(1, 31))"), ["16 4 15 -2147483648"])
        self.assertEqual(show("  println(shiftl(1, 3), shiftr(-8, 1), shifta(-8, 1))"), ["8 2147483644 -4"])

    def test_single_bits_and_counts(self):
        self.assertEqual(show("  println(btest(5, 0), btest(5, 1), ibset(0, 3), ibclr(15, 0), ibits(173, 2, 3))"),
                         ["true false 8 14 3"])
        self.assertEqual(show("  println(popcnt(255), popcnt(-1), leadz(1), trailz(8), trailz(0))"), ["8 32 31 3 32"])

    def test_widths(self):
        self.assertEqual(show("  println(bit_size(1), bit_size(int64(1)), bit_size(int8(1)))"), ["32 64 8"])
        self.assertEqual(show("  println(ishft(int8(64), 1), bitnot(int8(0)), iand(int8(-1), int8(15)))"), ["-128 -1 15"])
        self.assertEqual(show("  println(shiftl(int64(1), 40), popcnt(int64(-1)))"), ["1099511627776 64"])

    def test_arrays_work_elementwise(self):
        self.assertEqual(show("  println(iand([1, 2, 3], 1), popcnt([1, 3, 7]))"), ["[1, 0, 1] [1, 2, 3]"])

    def test_mistakes(self):
        fails(self, "  println(iand(1.0, 2))", "needs whole numbers")
        fails(self, "  println(iand(1))", "takes 2 arguments, got 1")
        fails(self, "  println(btest(1, 40))", "bit position")
        fails(self, "  println(ishft(1, 33))", "the shift must be")
        fails(self, "  println(popcnt(true))", "needs numbers")


class BesselTests(unittest.TestCase):
    def test_values(self):
        out = show("  println(bessel_j0(1.0), bessel_j1(1.0), bessel_y0(1.0), bessel_y1(1.0), bessel_jn(2, 1.0), bessel_yn(2, 1.0))")
        got = [float(x) for x in out[0].split()]
        want = [0.7651976865579666, 0.4400505857449335, 0.08825696421567697, -0.7812128213002887,
                0.11490348493190049, -1.6506826068162543]
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, places=12)

    def test_the_second_kind_at_zero(self):
        fails(self, "  println(bessel_y0(0.0))", "has no real answer")
        self.assertEqual(run_bor("use ieee\nprogram t\n  println(bessel_y0(0.0))\nend program\n"), ["-Infinity"])

    def test_the_order_is_a_whole_number(self):
        fails(self, "  println(bessel_jn(1.5, 1.0))", "whole-number order")


@unittest.skipUnless(GFORTRAN, "needs gfortran")
class ConversionTests(unittest.TestCase):
    SRC = '''program t
  implicit none
  integer :: a(2,3), i, j
  a = reshape([1,2,3,4,5,6],[2,3])
  i = 12
  j = 10
  print *, iand(i, j), ior(i, j), ieor(i, j), not(5), ishft(1, 4), ishft(16, -2), btest(5, 0), popcnt(255)
  print *, ibset(0, 3), ibclr(15, 0), ibits(173, 2, 3), leadz(1), trailz(8), bit_size(i), shiftl(1, 3)
  print *, shiftr(-8, 1), shifta(-8, 1), popcnt(-1), ishft(-1, -28)
  print *, sum(a, dim=1), sum(a, 2), maxval(a, dim=2), minval(a, 1), product(a, dim=1), count(a > 2, dim=1)
  print *, any(a > 5, dim=1), all(a > 0, 2)
  print *, bessel_j0(1.0d0), bessel_j1(2.0d0), bessel_y0(3.0d0), bessel_y1(0.5d0), bessel_jn(2, 1.0d0), bessel_yn(2, 1.0d0)
end program
'''

    def test_it_converts_and_matches_gfortran(self):
        text, findings = converted(self.SRC)
        self.assertEqual([f for f in findings if f[1] == "todo"], [])
        got = run_bor(text)
        want = fortran_output(self.SRC)
        self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")


if __name__ == "__main__":
    unittest.main()
