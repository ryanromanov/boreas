import sys
from .lexer import LangError
from .parser import parse
from .interp import Interp


def run_source(src, out=print):
    Interp(parse(src), out).run()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: boreas FILE.bor")
    try:
        run_source(open(sys.argv[1]).read())
    except LangError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
