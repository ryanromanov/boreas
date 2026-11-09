import json
import os
import re
import sys
from .lexer import LangError
from .parser import parse
from .interp import Interp
from .names import undeclared_names

USAGE = """usage:
  boreas FILE.bor                    run a program
  boreas check FILE.bor [--json]     parse and check without running; exit 1 on errors
  boreas lsp                         language server on stdin/stdout (errors as diagnostics)
  boreas convert [--define NAME[=VALUE]]... FILE.f90 [FILE...] [-o OUT]
                                     convert Fortran (free or fixed form; several files make one program);
                                     #if/#ifdef lines are evaluated with the macros you --define
  boreas convert --verify [--timeout SECONDS] [--input FILE] FILE.f90 [FILE...] [-o OUT]
                                     also run the original with gfortran and compare output"""


def run_source(src, out=print, args=(), stdin=None):
    """Run a program; returns its exit code."""
    unit = parse(src)
    warn = lambda text: print(text, file=sys.stderr)
    for line, message in unit.warnings:
        if message not in {m for _, m in unit.warnings[:unit.warnings.index((line, message))]}:
            warn(f"warning: line {line}: {message}")
    return Interp(unit, out, args, stdin, warn).run()


def check_errors(src):
    """Parse, link and look for undeclared names without running. Returns a list of
    (line, col, message, length). A parse or link error stops the check, so it comes alone, and
    its line and col are None when it does not carry them; its length is always None."""
    try:
        unit = parse(src)
        return undeclared_names(unit, Interp(unit, lambda *a: None))
    except LangError as e:
        m = re.match(r"line (\d+): (.*)", str(e), re.S)
        return [(int(m.group(1)) if m else None, e.col, m.group(2) if m else str(e), None)]


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
    errors = check_errors(src)
    if as_json:
        print(json.dumps({"file": path, "errors": [{"line": line, "col": col, "message": msg}
                                                    for line, col, msg, _ in errors]}))
    else:
        for line, col, msg, _ in errors:
            print(f"{path}:{line or 1}:{col or 1}: {msg}")
    sys.exit(1 if errors else 0)


def convert_command(args):
    from .fortran import convert_files
    paths, out_path, do_verify, timeout, input_path = [], None, False, None, None
    defines = {}
    it = iter(args)
    for a in it:
        if a == "--verify":
            do_verify = True
        elif a in ("--define", "-D"):
            spec = next(it, None)
            name, _, value = (spec or "").partition("=")
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                sys.exit("error: --define needs NAME or NAME=VALUE")
            defines[name] = value if "=" in spec else "1"
        elif a == "--timeout":
            try:
                timeout = float(next(it, None))
                if not timeout > 0:
                    raise ValueError
            except (TypeError, ValueError):
                sys.exit("error: --timeout needs a positive number of seconds")
        elif a == "--input":
            input_path = next(it, None)
            if input_path is None:
                sys.exit("error: --input needs a file name")
        elif a == "-o":
            out_path = next(it, None)
            if out_path is None:
                sys.exit("error: -o needs a file name")
        elif a.startswith("-") and len(a) > 1:
            sys.exit(USAGE)
        else:
            paths.append(a)
    if not paths or ((timeout is not None or input_path is not None) and not do_verify):
        sys.exit(USAGE)
    sources = []
    for path in paths:
        try:
            with open(path) as f:
                sources.append((os.path.basename(path), f.read(), os.path.dirname(os.path.abspath(path))))
        except OSError as e:
            sys.exit(f"error: {e}")
    text, findings, where = convert_files(sources, defines)
    path = paths[0] if len(paths) == 1 else ", ".join(paths)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)

    todos = [f for f in findings if f[1] == "todo"]
    notes = [f for f in findings if f[1] == "note"]
    report = sys.stderr
    for line, kind, msg in sorted(findings, key=lambda f: (f[0], f[1] != "todo")):
        which, local = where(line) if line else (0, 0)
        place = f"{paths[which]}:{local}" if line else path
        print(f"{place}: {'TODO' if kind == 'todo' else 'note'}: {msg}", file=report)
    print(f"converted {path}: {len(todos)} TODO(s), {len(notes)} note(s)"
          + (f" -> {out_path}" if out_path else ""), file=report)
    if do_verify:
        from .verify import verify, DEFAULT_TIMEOUT
        sys.stdout.flush()
        stdin_text = ""
        if input_path is not None:
            try:
                with open(input_path) as f:
                    stdin_text = f.read()
            except OSError as e:
                sys.exit(f"error: {e}")
        sys.exit(verify(paths[0] if len(paths) == 1 else paths, text, bool(todos), timeout or DEFAULT_TIMEOUT, stdin_text=stdin_text, defines=defines))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(USAGE)
    if hasattr(sys.stdout, "reconfigure") and not sys.stdout.isatty():    # a pipe: show each line at once
        sys.stdout.reconfigure(line_buffering=True)
    try:
        if args[0] == "check":
            check_command(args[1:])
        elif args[0] == "lsp" and len(args) == 1:
            from .lsp import serve
            serve()
        elif args[0] == "convert":
            convert_command(args[1:])
        elif args[0] != "" and not args[0].startswith("-"):      # boreas FILE [ARGUMENT ...]
            sys.exit(run_source(open(args[0]).read(), args=args))
        else:
            sys.exit(USAGE)
    except LangError as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:                    # Control-C or an editor's Stop button: leave quietly
        sys.exit(130)                            # the shell's code for SIGINT


if __name__ == "__main__":
    main()
