"""Labeled loops: `outer: do ... end do outer`, `exit outer`, `cycle outer`."""
import os, subprocess, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.fortran import convert
from boreas.interp import Interp
from boreas.lexer import LangError
from boreas.parser import parse, Do, DoWhile, Exit, Cycle
from boreas.printer import to_source
from test_fortran import GFORTRAN, fixture, read, run_bor, run_gfortran, same_output, todos, \
    assert_round_trips, conv


def prog(body, decls="integer :: i\ninteger :: j\ninteger :: k\ninteger :: n"):
    return f"program t\n  {decls}\n{body}\nend program t\n"


def run(body, decls="integer :: i\ninteger :: j\ninteger :: k\ninteger :: n"):
    return run_bor(prog(body, decls))


def fails(body, msg, decls="integer :: i\ninteger :: j\ninteger :: k\ninteger :: n"):
    with unittest.TestCase().assertRaises(LangError) as cm:
        parse(prog(body, decls))
    assert msg in str(cm.exception), str(cm.exception)
    return str(cm.exception)


NEST = """
  outer: do i = 1, 3
    do j = 1, 3
      if j == 2 then
        cycle outer
      end if
      print i, j
    end do
  end do outer
"""


class ParseTests(unittest.TestCase):
    def test_nodes_carry_labels(self):
        body = parse(prog("""
  outer: do i = 1, 3
    exit outer
  end do outer
  scan: do while true
    cycle scan
  end do scan
  do i = 1, 2
  end do""")).program.body
        d, w, plain = body[-3:]
        self.assertEqual((d.label, w.label, plain.label), ("outer", "scan", None))
        self.assertIsInstance(d.body[0], Exit)
        self.assertEqual(d.body[0].label, "outer")
        self.assertIsInstance(w.body[0], Cycle)
        self.assertEqual(w.body[0].label, "scan")

    def test_plain_exit_has_no_label(self):
        d = parse(prog("do i = 1, 2\n exit\n cycle\n end do")).program.body[-1]
        self.assertEqual([s.label for s in d.body], [None, None])

    def test_labels_are_case_insensitive(self):
        self.assertEqual(run("""
  Outer: do i = 1, 3
    do j = 1, 3
      if j == 2 then
        EXIT OUTER
      end if
    end do
  end do OUTER
  print i"""), ["1"])

    def test_label_may_equal_a_variable(self):
        self.assertEqual(run("""
  k: do k = 1, 3
    if k == 2 then
      exit k
    end if
  end do k
  print k""", "integer :: k"), ["2"])

    def test_label_may_equal_a_procedure(self):
        src = """function f(integer :: x) -> integer
  return x
end function
program t
  integer :: i
  f: do i = 1, 2
    exit f
  end do f
  print f(i)
end program t
"""
        self.assertEqual(run_bor(src), ["1"])

    def test_slices_and_use_still_parse(self):
        src = """module m
  public const integer :: pi2 = 6
end module m
program t
  use m: pi2
  integer :: a(4)
  integer :: s
  a = [1, 2, 3, 4]
  lo: do s = 1, 1
    print a(2:3), pi2
  end do lo
end program t
"""
        self.assertEqual(run_bor(src), ["[2, 3] 6"])

    def test_reused_in_siblings(self):
        self.assertEqual(run("""
  a: do i = 1, 2
    exit a
  end do a
  a: do j = 5, 6
    exit a
  end do a
  print i, j"""), ["1 5"])

    def test_loop_in_if_and_else(self):
        self.assertEqual(run("""
  if true then
    a: do i = 1, 3
      exit a
    end do a
  else
    a: do i = 1, 3
      exit a
    end do a
  end if
  print i"""), ["1"])


class ErrorTests(unittest.TestCase):
    def test_missing_closing_label(self):
        e = fails("outer: do i = 1, 2\nend do", "must close with 'end do outer'")
        self.assertIn("line 7", e)

    def test_missing_closing_label_while(self):
        fails("w: do while false\nend do", "must close with 'end do w'")

    def test_wrong_closing_label(self):
        fails("a: do i = 1, 2\nend do b", "must close with 'end do a'")

    def test_unlabeled_loop_with_closing_label(self):
        fails("do i = 1, 2\nend do outer", "no label")
        fails("do while false\nend do outer", "no label")

    def test_inner_closing_label_on_outer(self):
        fails("a: do i = 1, 2\n b: do j = 1, 2\n end do a\n end do b", "must close with 'end do b'")

    def test_unknown_label(self):
        e = fails("do i = 1, 2\n exit nope\n end do", "'exit nope' does not name an enclosing loop")
        self.assertIn("line 7", e)
        fails("do i = 1, 2\n cycle nope\n end do", "'cycle nope' does not name an enclosing loop")

    def test_label_outside_any_loop(self):
        fails("exit outer", "not inside a labeled loop")

    def test_error_lists_enclosing_labels(self):
        fails("a: do i = 1, 2\n exit b\n end do a", "enclosing loops are 'a'")

    def test_sibling_label_is_not_enclosing(self):
        fails("""
  a: do i = 1, 2
  end do a
  do j = 1, 2
    exit a
  end do""", "does not name an enclosing loop")

    def test_inner_loop_label_is_not_visible_outside(self):
        fails("""
  do i = 1, 2
    b: do j = 1, 2
    end do b
    cycle b
  end do""", "does not name an enclosing loop")

    def test_duplicate_nested_label(self):
        fails("a: do i = 1, 2\n a: do j = 1, 2\n end do a\n end do a", "already used by an enclosing loop")
        fails("a: do i = 1, 2\n if true then\n a: do while false\n end do a\n end if\n end do a",
              "already used by an enclosing loop")

    def test_label_does_not_leak_into_procedures(self):
        with self.assertRaises(LangError):
            parse("""subroutine s()
  exit outer
end subroutine
program t
  outer: do while false
    call s()
  end do outer
end program t
""")

    def test_label_must_precede_do(self):
        with self.assertRaises(LangError):
            parse(prog("a: if true then\nend if"))

    def test_end_and_else_are_not_labels(self):
        fails("end: do i = 1, 2\nend do end", "cannot be used as a loop label")
        fails("else: do i = 1, 2\nend do else", "cannot be used as a loop label")

    def test_exit_with_extra_tokens(self):
        with self.assertRaises(LangError):
            parse(prog("a: do i = 1, 2\n exit a b\n end do a"))


class RunTests(unittest.TestCase):
    def test_cycle_outer(self):
        self.assertEqual(run(NEST), ["1 1", "2 1", "3 1"])

    def test_exit_outer(self):
        self.assertEqual(run("""
  outer: do i = 1, 5
    do j = 1, 5
      if i * j == 6 then
        exit outer
      end if
    end do
  end do outer
  print i, j"""), ["2 3"])

    def test_exit_outer_keeps_outer_variable_like_plain_exit(self):
        # a plain exit leaves the variable at the iteration it stopped on; so does exit outer
        self.assertEqual(run("do i = 1, 5\n if i == 3 then\n exit\n end if\n end do\n print i"), ["3"])
        self.assertEqual(run("o: do i = 1, 5\n do j = 1, 2\n if i == 3 then\n exit o\n end if\n end do\n"
                             " end do o\n print i, j"), ["3 1"])

    def test_normal_completion_variable_one_step_past(self):
        self.assertEqual(run("o: do i = 1, 3\n do j = 1, 2\n end do\n end do o\n print i, j"), ["4 3"])

    def test_cycle_outer_advances_outer_variable(self):
        self.assertEqual(run("""
  n = 0
  o: do i = 1, 4
    do j = 1, 4
      n = n + 1
      cycle o
    end do
    n = n + 1000
  end do o
  print n, i"""), ["4 5"])

    def test_cycle_own_label_and_exit_own_label(self):
        self.assertEqual(run("""
  n = 0
  a: do i = 1, 5
    if i == 2 then
      cycle a
    end if
    if i == 4 then
      exit a
    end if
    n = n + i
  end do a
  print n, i"""), ["4 4"])

    def test_inner_unlabeled_exit_only_leaves_inner(self):
        self.assertEqual(run("""
  n = 0
  a: do i = 1, 3
    do j = 1, 3
      exit
    end do
    n = n + 1
  end do a
  print n"""), ["3"])

    def test_do_while_labels(self):
        self.assertEqual(run("""
  n = 0
  scan: do while n < 100
    n = n + 7
    do k = 1, 3
      if n > 20 then
        exit scan
      end if
      if mod(n, 2) == 0 then
        cycle scan
      end if
    end do
    print n
  end do scan
  print n"""), ["7", "21"])

    def test_exit_do_while_from_for_loop_and_back(self):
        self.assertEqual(run("""
  n = 0
  w: do while true
    n = n + 1
    for_: do i = 1, 3
      if n == 3 then
        exit w
      end if
      cycle w
    end do for_
  end do w
  print n"""), ["3"])

    def test_three_deep(self):
        self.assertEqual(run("""
  n = 0
  a: do i = 1, 4
    b: do j = 1, 4
      c: do k = 1, 4
        if k == 3 then
          cycle b
        end if
        if j == 3 then
          exit b
        end if
        if i == 3 then
          cycle a
        end if
        if i == 4 then
          exit a
        end if
        n = n + 100 * i + 10 * j + k
      end do c
    end do b
    print i, n
  end do a
  print i, j, k, n"""), ["1 466", "2 1332", "4 1 1 1332"])

    def test_exit_through_if_blocks(self):
        self.assertEqual(run("""
  a: do i = 1, 3
    if i > 1 then
      if i > 1 then
        exit a
      end if
    end if
  end do a
  print i"""), ["2"])

    def test_return_inside_labeled_loop(self):
        src = """function first(integer :: t) -> integer
  integer :: i
  integer :: j
  o: do i = 1, 5
    do j = 1, 5
      if i * j == t then
        return i * 10 + j
      end if
    end do
  end do o
  return 0
end function
program t
  print first(12), first(99)
end program t
"""
        self.assertEqual(run_bor(src), ["34 0"])

    def test_labels_in_called_procedure_are_independent(self):
        src = """subroutine inner(out integer :: r)
  integer :: i
  a: do i = 1, 10
    if i == 3 then
      update r = i
      exit a
    end if
  end do a
end subroutine
program t
  integer :: i
  integer :: r
  integer :: n
  n = 0
  a: do i = 1, 3
    call inner(r)
    n = n + r
    if i == 2 then
      exit a
    end if
  end do a
  print n, i
end program t
"""
        self.assertEqual(run_bor(src), ["6 2"])


class UpdateTests(unittest.TestCase):
    SUB = """subroutine tally(inout integer :: total, integer :: n)
  integer :: i
  integer :: j
  o: do i = 1, n
    do j = 1, n
      if j > i then
        cycle o
      end if
      if i == 4 then
        exit o
      end if
      update total = total + 1
    end do
  end do o
end subroutine
program t
  integer :: total
  total = 0
  call tally(total, 6)
  print total
end program t
"""

    def test_update_inside_labeled_loops(self):
        self.assertEqual(run_bor(self.SUB), ["6"])

    def test_missing_update_still_caught_in_labeled_loop(self):
        with self.assertRaises(LangError) as cm:
            parse(self.SUB.replace("update total = total + 1", "total = total + 1"))
        self.assertIn("update total", str(cm.exception))

    def test_update_on_loop_local_still_caught(self):
        with self.assertRaises(LangError):
            parse(self.SUB.replace("update total = total + 1", "update i = 3"))

    def test_loop_variable_cannot_be_a_parameter(self):
        with self.assertRaises(LangError):
            parse(self.SUB.replace("integer :: i\ninteger :: j", "integer :: j").replace("o: do i = 1, n", "o: do n = 1, 3"))


class PrinterTests(unittest.TestCase):
    def test_prints_labels(self):
        text = to_source(parse(prog(NEST)))
        self.assertIn("  outer: do i = 1, 3\n", text)
        self.assertIn("      cycle outer\n", text)
        self.assertIn("  end do outer\n", text)

    def test_round_trip_stable(self):
        src = prog("""
  a: do i = 1, 3
    b: do while n < 3
      n = n + 1
      if n == 2 then
        cycle a
      end if
      exit b
    end do b
    do j = 1, 2
      exit a
    end do
  end do a
  a: do k = 1, 2, 1
    cycle
  end do a""")
        once = to_source(parse(src))
        twice = to_source(parse(once))
        self.assertEqual(once, twice)
        self.assertEqual(once.count("end do a"), 2)

    def test_round_trip_runs_the_same(self):
        self.assertEqual(run_bor(to_source(parse(prog(NEST)))), run(NEST))

    def test_without_comments_keeps_labels(self):
        from boreas.printer import without_comments
        u = without_comments(parse(prog(NEST)))
        self.assertEqual(u.program.body[-1].label, "outer")
        self.assertIn("end do outer", to_source(u))


class ConverterTests(unittest.TestCase):
    def test_fixture_matches_gfortran(self):
        path = fixture("labels.f90")
        if GFORTRAN:
            subprocess.run([GFORTRAN, "-fsyntax-only", "-std=f2018", path], check=True, capture_output=True)
        text, findings = convert(read(path), "labels.f90")
        self.assertEqual(todos(findings), [])
        assert_round_trips(read(path))
        parse(text)
        got = run_bor(text)
        if GFORTRAN:
            want = run_gfortran(path)
            self.assertTrue(same_output("\n".join(got), want), f"\nBoreas:\n{chr(10).join(got)}\n\ngfortran:\n{want}")
        self.assertEqual(got[0], "3 4 3")
        self.assertEqual(got[-1], "2")
        self.assertIn("  outer: do i = 1, 5\n", text)
        self.assertIn("      c: do k = 1, 4\n", text)
        self.assertIn("  scan: do while n < 100\n", text)

    def test_keyword_label_is_renamed_consistently(self):
        text, findings = conv("""
  in: do i = 1, 3
    if (i == 2) exit in
    do j = 1, 2
      cycle in
    end do
  end do in""", "  integer :: i\ninteger :: j")
        self.assertEqual(todos(findings), [])
        self.assertIn("in_: do i = 1, 3", text)
        self.assertIn("exit in_", text)
        self.assertIn("cycle in_", text)
        self.assertIn("end do in_", text)
        self.assertTrue(any("'in' is a Boreas keyword" in f[2] for f in findings))
        parse(text)

    def test_name_dropped_note_is_gone_for_loops_only(self):
        _, findings = conv("  a: do i = 1, 2\n  end do a\n  b: if (i > 0) then\n  end if b", "  integer :: i")
        notes = [f[2] for f in findings if f[1] == "note"]
        self.assertFalse(any("'a'" in n for n in notes))
        self.assertTrue(any("construct name 'b' dropped" in n for n in notes))

    def test_lenient_unnamed_end_do(self):
        text, findings = conv("  a: do i = 1, 2\n    exit a\n  end do", "  integer :: i")
        self.assertEqual(todos(findings), [])
        self.assertIn("end do a", text)
        parse(text)

    def test_lenient_end_do_while(self):
        text, _ = conv("  w: do while (i < 3)\n    i = i + 1\n    cycle w\n  end do", "  integer :: i")
        self.assertIn("w: do while i < 3", text)
        self.assertIn("end do w", text)
        parse(text)

    def test_infinite_labeled_loop(self):
        text, findings = conv("  n = 0\n  l: do\n    n = n + 1\n    if (n > 2) exit l\n  end do l\n  print *, n",
                              "  integer :: n")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["3"])

    def test_exit_of_non_loop_construct_is_todo(self):
        text, findings = conv("""
  c: if (i > 0) then
    exit c
  end if c""", "  integer :: i")
        self.assertEqual(len(todos(findings)), 1)
        self.assertIn("'exit c': 'c' is not an enclosing do loop", todos(findings)[0][2])
        self.assertIn("TODO(convert)", text)

    def test_exit_unknown_or_sibling_name_is_todo(self):
        _, findings = conv("""
  a: do i = 1, 2
  end do a
  do i = 1, 2
    cycle a
  end do""", "  integer :: i")
        self.assertEqual(len(todos(findings)), 1)

    def test_shadowing_sibling_names_and_variable_namesake(self):
        text, findings = conv("""
  a: do i = 1, 2
    exit a
  end do a
  a2: do i = 1, 2
    a: do j = 1, 2
      cycle a2
    end do a
  end do a2""", "  integer :: i\ninteger :: j")
        self.assertEqual(todos(findings), [])
        parse(text)

    def test_name_equal_to_variable(self):
        text, findings = conv("  i: do i = 1, 3\n    if (i == 2) exit i\n  end do i\n  print *, i", "  integer :: i")
        self.assertEqual(todos(findings), [])
        self.assertEqual(run_bor(text), ["2"])


if __name__ == "__main__":
    unittest.main()
