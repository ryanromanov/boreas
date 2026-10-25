import json
import os
import re
import sys
from .lexer import LangError
from .parser import parse
from .interp import Interp

USAGE = """usage:
  boreas FILE.bor                    run a program
  boreas check FILE.bor [--json]     parse and check without running; exit 1 on errors
  boreas lsp                         language server on stdin/stdout (errors as diagnostics)
  boreas convert FILE.f90 [-o OUT]   convert free-form Fortran to Boreas source
  boreas convert --verify [--timeout SECONDS] FILE.f90 [-o OUT]
                                     also run the original with gfortran and compare output"""


def run_source(src, out=print):
    Interp(parse(src), out).run()


def check_source(src):
    """Parse and link without running. Returns None if clean, else (line, col, message);
    line and col are None when the error does not carry them."""
    try:
        Interp(parse(src), lambda *a: None)
    except LangError as e:
        m = re.match(r"line (\d+): (.*)", str(e), re.S)
        return (int(m.group(1)) if m else None, e.col, m.group(2) if m else str(e))
    return None


def check_command(args):
    as_json = "--json" in args
    files = [a for a in args if a != "--json"]
    if len(files) != 1 or files[0].startswith("-"):
        sys.exit(USAGE)
    path = files[0]
    try:
        with open(path) as f:
            src = f.read()
    except OSError as e:
        sys.exit(f"error: {e}")
    err = check_source(src)
    if as_json:
        print(json.dumps({"file": path, "errors": [] if err is None else
                          [{"line": err[0], "col": err[1], "message": err[2]}]}))
    elif err:
        line, col, msg = err
        print(f"{path}:{line or 1}:{col or 1}: {msg}")
    sys.exit(0 if err is None else 1)


def convert_command(args):
    from .fortran import convert, FIXED_FORM_EXTENSIONS
    path, out_path, do_verify, timeout = None, None, False, None
    it = iter(args)
    for a in it:
        if a == "--verify":
            do_verify = True
        elif a == "--timeout":
            try:
                timeout = float(next(it, None))
                if not timeout > 0:
                    raise ValueError
            except (TypeError, ValueError):
                sys.exit("error: --timeout needs a positive number of seconds")
        elif a == "-o":
            out_path = next(it, None)
            if out_path is None:
                sys.exit("error: -o needs a file name")
        elif a.startswith("-") and len(a) > 1:
            sys.exit(USAGE)
        elif path is None:
            path = a
        else:
            sys.exit(USAGE)
    if path is None or (timeout is not None and not do_verify):
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
    if do_verify:
        from .verify import verify, DEFAULT_TIMEOUT
        sys.stdout.flush()
        sys.exit(verify(path, text, bool(todos), timeout or DEFAULT_TIMEOUT))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(USAGE)
    try:
        if args[0] == "check":
            check_command(args[1:])
        elif args[0] == "lsp" and len(args) == 1:
            from .lsp import serve
            serve()
        elif args[0] == "convert":
            convert_command(args[1:])
        elif len(args) == 1:
            run_source(open(args[0]).read())
        else:
            sys.exit(USAGE)
    except LangError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
