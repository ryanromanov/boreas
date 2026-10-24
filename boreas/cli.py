import os
import sys
from .lexer import LangError
from .parser import parse
from .interp import Interp

USAGE = """usage:
  boreas FILE.bor                    run a program
  boreas convert FILE.f90 [-o OUT]   convert free-form Fortran to Boreas source"""


def run_source(src, out=print):
    Interp(parse(src), out).run()


def convert_command(args):
    from .fortran import convert, FIXED_FORM_EXTENSIONS
    path, out_path = None, None
    it = iter(args)
    for a in it:
        if a == "-o":
            out_path = next(it, None)
            if out_path is None:
                sys.exit("error: -o needs a file name")
        elif path is None:
            path = a
        else:
            sys.exit(USAGE)
    if path is None:
        sys.exit(USAGE)
    if path.lower().endswith(FIXED_FORM_EXTENSIONS):
        sys.exit(f"error: {path} looks like fixed-form Fortran, which is not supported yet "
                 f"(free-form .f90 and later only)")
    try:
        with open(path) as f:
            src = f.read()
    except OSError as e:
        sys.exit(f"error: {e}")
    text, findings = convert(src, os.path.basename(path))
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)

    todos = [f for f in findings if f[1] == "todo"]
    notes = [f for f in findings if f[1] == "note"]
    report = sys.stderr
    for line, kind, msg in sorted(findings, key=lambda f: (f[0], f[1] != "todo")):
        where = f"{path}:{line}" if line else path
        print(f"{where}: {'TODO' if kind == 'todo' else 'note'}: {msg}", file=report)
    print(f"converted {path}: {len(todos)} TODO(s), {len(notes)} note(s)"
          + (f" -> {out_path}" if out_path else ""), file=report)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(USAGE)
    try:
        if args[0] == "convert":
            convert_command(args[1:])
        elif len(args) == 1:
            run_source(open(args[0]).read())
        else:
            sys.exit(USAGE)
    except LangError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
