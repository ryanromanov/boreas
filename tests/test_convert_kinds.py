import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from test_fortran import FixtureTests, run_bor, todos
from boreas.fortran import convert


def conv(body, decls=""):
    return convert(f"program p\n  implicit none\n{decls}{body}end program\n")


def declared(typespec):
    """The Boreas type a Fortran declaration `typespec :: x` becomes."""
    text, findings = conv("", f"  {typespec} :: x\n")
    for line in text.splitlines():
        if line.strip().endswith(":: x"):
            return line.strip()[:-len(":: x")].strip()
    return None


class DeclarationKindTests(unittest.TestCase):
    def test_real_kinds(self):
        for spec, want in [("real", "real32"), ("real(4)", "real32"), ("real(kind=4)", "real32"),
                           ("real(8)", "real"), ("real(kind=8)", "real"), ("real*8", "real"), ("real*4", "real32"),
                           ("double precision", "real")]:
            with self.subTest(spec):
                self.assertEqual(declared(spec), want)

    def test_integer_kinds(self):
        for spec, want in [("integer", "integer"), ("integer(1)", "int8"), ("integer(2)", "int16"),
                           ("integer(4)", "integer"), ("integer(8)", "int64"), ("integer(kind=8)", "int64"),
                           ("integer*8", "int64"), ("integer*2", "int16")]:
            with self.subTest(spec):
                self.assertEqual(declared(spec), want)

    def test_complex_kinds_are_double(self):
        for spec in ("complex", "complex(4)", "complex(8)", "complex(kind=8)", "double complex", "complex*16"):
            with self.subTest(spec):
                self.assertEqual(declared(spec), "complex")

    def test_unsupported_kinds_are_todos(self):
        for spec in ("real(16)", "integer(16)", "real(kind=10)"):
            with self.subTest(spec):
                _, findings = conv("  x = 1\n", f"  {spec} :: x\n")
                self.assertTrue(todos(findings), spec)

    def test_default_complex_is_noted_as_single(self):
        _, findings = conv("", "  complex :: z\n")
        self.assertTrue(any("single precision" in f[2] for f in findings))

    def test_logical_and_character_kinds_are_ignored(self):
        self.assertEqual(declared("logical(4)"), "logical")
        self.assertEqual(declared("character(len=5)"), "string")

    def test_implicit_real_is_single(self):
        text, _ = convert("program p\n  y = 1.5\n  print *, y\nend program\n")
        self.assertIn("real32 :: y", text)


class KindNameTests(unittest.TestCase):
    MODULE = ("module k\n  implicit none\n  integer, parameter :: sp = kind(1.0)\n  integer, parameter :: dp = kind(1.0d0)\n"
              "  integer, parameter :: wp = dp\n  integer, parameter :: r15 = selected_real_kind(15, 307)\n"
              "  integer, parameter :: r6 = selected_real_kind(6)\n  integer, parameter :: i9 = selected_int_kind(9)\n"
              "  integer, parameter :: i18 = selected_int_kind(18)\n  integer, parameter :: i4 = selected_int_kind(4)\n"
              "  integer, parameter :: eight = 8\nend module\n")

    def kinds(self, decls):
        text, findings = convert(self.MODULE + f"program p\n  use k\n  implicit none\n{decls}end program\n")
        return text, findings

    def test_named_kinds(self):
        text, findings = self.kinds("  real(sp) :: a\n  real(dp) :: b\n  real(wp) :: c\n  real(r15) :: d\n  real(r6) :: e\n"
                                    "  integer(i9) :: f\n  integer(i18) :: g\n  integer(i4) :: h\n  real(kind=eight) :: i\n")
        self.assertEqual(todos(findings), [])
        for want in ("real32 :: a", "real :: b", "real :: c", "real :: d", "real32 :: e", "integer :: f",
                     "int64 :: g", "int16 :: h", "real :: i"):
            self.assertIn(want, text)

    def test_kind_constants_become_plain_integers(self):
        text, _ = self.kinds("")
        for want in ("public const integer :: sp = 4", "public const integer :: dp = 8", "public const integer :: wp = dp",
                     "public const integer :: r15 = 8", "public const integer :: i18 = 8", "public const integer :: i4 = 2"):
            self.assertIn(want, text)
        self.assertNotIn("selected", text)
        self.assertNotIn("kind(", text)

    def test_kind_names_are_not_imported_when_only_used_as_kinds(self):
        text, _ = self.kinds("  real(dp) :: b\n")
        self.assertNotIn("use k", text)

    def test_iso_fortran_env_names(self):
        text, findings = convert("program p\n  use, intrinsic :: iso_fortran_env, only: real64, int64, real32, int8\n"
                                 "  implicit none\n  real(real64) :: a\n  integer(int64) :: b\n  real(real32) :: c\n  integer(int8) :: d\n"
                                 "end program\n")
        self.assertEqual(todos(findings), [])
        for want in ("real :: a", "int64 :: b", "real32 :: c", "int8 :: d"):
            self.assertIn(want, text)

    def test_use_iso_fortran_env_without_attributes(self):
        text, findings = convert("program p\n  use iso_fortran_env\n  implicit none\n  real(real64) :: a\nend program\n")
        self.assertEqual(todos(findings), [])
        self.assertIn("real :: a", text)


class LiteralAndConversionTests(unittest.TestCase):
    def out(self, expr, decls="  real :: x\n  double precision :: d\n  complex(8) :: z\n  integer :: i\n"):
        text, findings = conv(f"  print *, {expr}\n", decls)
        for line in text.splitlines():
            if line.strip().startswith("print "):
                return line.strip()[6:]
        raise AssertionError(text)

    def test_single_literals_that_are_not_exact_are_real32(self):
        self.assertEqual(self.out("0.1, 0.5, 2.0, 3.14159"), "real32(0.1), 0.5, 2.0, real32(3.14159)")

    def test_double_literals_stay_plain(self):
        self.assertEqual(self.out("0.1d0, 1.0e-3_8, 0.1_8"), "0.1, 0.001, 0.1")

    def test_named_kind_literals(self):
        text, _ = convert("program p\n  implicit none\n  integer, parameter :: dp = kind(1.0d0)\n  integer, parameter :: sp = kind(1.0)\n"
                          "  print *, 0.1_dp, 0.1_sp\nend program\n")
        self.assertIn("print 0.1, real32(0.1)", text)

    def test_integer_literal_kinds(self):
        self.assertEqual(self.out("5_8, 5_4, 5_2, 5_1, 5"), "int64(5), 5, int16(5), int8(5), 5")

    def test_real_of_an_integer_or_real_is_single(self):
        self.assertEqual(self.out("real(i), real(d), real(x)"), "real32(i), real32(d), real32(x)")

    def test_real_of_a_complex_is_the_real_part(self):
        self.assertEqual(self.out("real(z), real(z * z), real(cmplx(1.0, 2.0))"), "real(z), real(z * z), real(cmplx(1.0, 2.0))")

    def test_real_with_a_kind(self):
        self.assertEqual(self.out("real(i, 8), real(i, kind=8), real(d, 4)"), "real64(i), real64(i), real32(d)")

    def test_dble_float_sngl(self):
        self.assertEqual(self.out("dble(i), float(i), sngl(d)"), "real64(i), real32(i), real32(d)")

    def test_int_with_a_kind(self):
        self.assertEqual(self.out("int(x), int(x, 8), int(x, kind=2), int(x, 1)"), "int(x), int64(x), int16(x), int8(x)")

    def test_epsilon_of_a_bare_single_literal(self):
        self.assertEqual(self.out("epsilon(1.0), epsilon(1.0d0), huge(1.0), huge(1)"),
                         "epsilon(real32(1.0)), epsilon(1.0), huge(real32(1.0)), huge(1)")

    def test_cmplx_kind_is_dropped(self):
        self.assertEqual(self.out("cmplx(1.0, 2.0, kind=8), cmplx(1.0, 2.0, 8)"), "cmplx(1.0, 2.0), cmplx(1.0, 2.0)")


class ConvertedProgramTests(unittest.TestCase):
    check = FixtureTests.check

    def test_kinds_fixture_matches_gfortran(self):
        got = self.check("kinds.f90")
        self.assertEqual(len(got), 8)

    def test_single_precision_accumulation_is_reproduced(self):
        text, findings = conv("  s = 0.0\n  do i = 1, 1000\n    s = s + 0.1\n  end do\n  print *, s\n",
                              "  real :: s\n  integer :: i\n")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["99.99905"])

    def test_double_precision_accumulation(self):
        text, _ = conv("  s = 0.0d0\n  do i = 1, 1000\n    s = s + 0.1d0\n  end do\n  print *, s\n",
                       "  double precision :: s\n  integer :: i\n")
        total = 0.0
        for _ in range(1000):
            total += 0.1
        self.assertEqual(run_bor(text), [repr(total)])


if __name__ == "__main__":
    unittest.main()
