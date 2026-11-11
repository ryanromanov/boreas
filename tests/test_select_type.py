import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from test_fortran import run_bor
from test_interfaces import DEFS
from boreas.cli import check_errors
from boreas.lexer import LangError
from boreas.parser import parse
from boreas.printer import to_source


def program(body, defs=DEFS):
    return defs + "\nprogram t\n" + body + "\nend program\n"


def run(body, defs=DEFS):
    return run_bor(program(body, defs))


SEL = """  select type f as p
    case circle
      println("circle", p.r)
    case rect
      println("rect", p.w, p.h)
    case default
      println("other")
  end select"""


class SelectTypeTests(unittest.TestCase):
    def test_the_case_for_the_record_the_value_holds_runs(self):
        self.assertEqual(run("  figure :: f = circle(r = 2.0)\n" + SEL), ["circle 2.0"])
        self.assertEqual(run("  figure :: f = rect(w = 3.0, h = 4.0)\n" + SEL), ["rect 3.0 4.0"])

    def test_default_runs_for_any_other_record(self):
        defs = DEFS + "\nrecord tri is figure\n  function name(self) -> string\n    return \"tri\"\n  end function\n  function area(self) -> real\n    return 1.0\n  end function\nend record\n"
        self.assertEqual(run("  figure :: f = tri()\n" + SEL, defs), ["other"])

    def test_an_empty_default_is_fine(self):
        body = "  figure :: f = circle()\n  select type f as p\n    case rect\n      println(\"rect\")\n    case default\n  end select\n  println(\"done\")"
        self.assertEqual(run(body), ["done"])

    def test_p_is_a_copy(self):
        body = ("  figure :: f = circle(r = 2.0)\n  select type f as p\n    case circle\n      p.r = 9.0\n"
                "      call p.grow(2.0)\n      println(p.r)\n    case default\n  end select\n  println(f.area())")
        self.assertEqual(run(body), ["18.0", "12.0"])

    def test_write_the_copy_back_to_change_the_original(self):
        body = ("  figure :: f = circle(r = 2.0)\n  select type f as p\n    case circle\n      p.r = 1.0\n      f = p\n    case default\n  end select\n"
                "  println(f.area())")
        self.assertEqual(run(body), ["3.0"])

    def test_it_works_on_array_elements_and_parameters(self):
        defs = DEFS + "\nsubroutine kind_of(figure :: g)\n  select type g as q\n    case circle\n      println(\"round\")\n    case default\n      println(\"other\")\n  end select\nend subroutine\n"
        body = "  figure[2] :: all\n  all[1] = circle()\n  all[2] = rect()\n  call kind_of(all[1])\n  call kind_of(all[2])"
        self.assertEqual(run(body, defs), ["round", "other"])

    def test_outer_variables_can_be_changed_inside_a_case(self):
        body = ("  figure :: f = rect(w = 2.0, h = 5.0)\n  real :: x = 0.0\n  select type f as p\n    case rect\n      x = p.w * p.h\n    case default\n  end select\n  println(x)")
        self.assertEqual(run(body), ["10.0"])

    def test_break_and_exit_and_cycle(self):
        body = ("  figure :: f = circle()\n  integer :: i\n  do i = 1, 3\n    select type f as p\n      case circle\n        if i == 2 then\n          cycle\n        end if\n"
                "        if i == 3 then\n          exit\n        end if\n        println(\"circle\", i)\n      case default\n    end select\n  end do\n"
                "  select type f as p\n    case circle\n      println(\"a\")\n      break\n      println(\"b\")\n    case default\n  end select")
        self.assertEqual(run(body), ["circle 1", "a"])

    def test_p_in_the_default_case_is_the_interface(self):
        body = ("  figure :: f = rect(w = 2.0, h = 3.0)\n  select type f as p\n    case circle\n      println(\"circle\")\n    case default\n      println(p.area())\n  end select")
        self.assertEqual(run(body), ["6.0"])
        with self.assertRaises(LangError) as cm:
            run("  figure :: f = rect()\n  select type f as p\n    case circle\n      println(1)\n    case default\n      println(p.w)\n  end select")
        self.assertIn("not available through figure", str(cm.exception))

    def test_a_plain_record_can_be_the_subject(self):
        self.assertEqual(run("  circle :: c = circle(r = 3.0)\n  select type c as p\n    case circle\n      println(p.r)\n    case default\n  end select"), ["3.0"])

    def test_the_cases_match_the_exact_record(self):
        # a record that is not listed does not match just because it fits the interface
        body = "  figure :: f = rect()\n  select type f as p\n    case circle\n      println(\"circle\")\n    case default\n      println(\"default\")\n  end select"
        self.assertEqual(run(body), ["default"])


class SelectTypeErrorTests(unittest.TestCase):
    def test_a_default_is_required(self):
        with self.assertRaises(LangError) as cm:
            parse(program("  figure :: f = circle()\n  select type f as p\n    case circle\n      println(1)\n  end select"))
        self.assertIn("needs a 'case default'", str(cm.exception))

    def test_duplicate_cases_are_errors(self):
        for body in ("    case circle\n    case circle\n    case default\n", "    case default\n    case default\n"):
            with self.assertRaises(LangError) as cm:
                parse(program("  figure :: f = circle()\n  select type f as p\n" + body + "  end select"))
            self.assertIn("appears twice", str(cm.exception))

    def test_the_variable_name_is_required(self):
        with self.assertRaises(LangError) as cm:
            parse(program("  figure :: f = circle()\n  select type f\n    case default\n  end select"))
        self.assertIn("select type x as p", str(cm.exception))

    def test_an_unassigned_value_and_a_non_record(self):
        with self.assertRaises(LangError) as cm:
            run("  figure[2] :: all\n  select type all[1] as p\n    case default\n  end select")
        self.assertIn("used before it is assigned", str(cm.exception))
        with self.assertRaises(LangError) as cm:
            run("  real :: x = 1.0\n  select type x as p\n    case default\n  end select")
        self.assertIn("needs a record or interface value", str(cm.exception))

    def test_check_reports_an_unknown_record_and_p_outside_the_select(self):
        src = program("  figure :: f = circle()\n  select type f as p\n    case blob\n      println(1)\n    case default\n  end select\n  println(p)")
        messages = [e[2] for e in check_errors(src)]
        self.assertEqual(messages, ["no record called 'blob'", "'p' is not declared"])

    def test_check_is_clean_for_a_good_select(self):
        self.assertEqual(check_errors(program("  figure :: f = circle()\n" + SEL)), [])


class ShapesExampleTests(unittest.TestCase):
    def test_the_shapes_example_matches_the_fortran_program(self):
        # the Fortran version (checked with gfortran) prints these areas, this total and this width
        path = os.path.join(os.path.dirname(__file__), "..", "examples", "shapes.bor")
        with open(path) as f:
            out = run_bor(f.read())
        self.assertEqual(out[:5], ["circle area = 12.566", "rect area = 12.000", "unit area = 3.142",
                                   "total = 27.708", "width of the rect: 3.0"])
        self.assertEqual(out[5], "circle area = 50.265")
        self.assertEqual(len(out), 6)


class SelectTypePrintTests(unittest.TestCase):
    def test_prints_back_the_same(self):
        src = program("  figure :: f = circle()\n" + SEL)
        text = to_source(parse(src), [])
        self.assertIn("select type f as p", text)
        self.assertIn("case default", text)
        self.assertEqual(to_source(parse(text), []), text)
        self.assertEqual(run_bor(text), run_bor(src))


if __name__ == "__main__":
    unittest.main()
