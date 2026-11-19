"""Renames the type `integer` to `int` in Boreas source written before `int` became the 64-bit default (and `int32` the
32-bit one). `python3 tools/rename_integer.py [--int32] FILE...` rewrites the files in place; `--int32` writes `int32`
instead, for a program that relied on 32-bit arithmetic (a hash or random-number generator that wraps with `*%`, say).
`rename(text, new)` does one string and returns (new text, how many words it changed)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.lexer import lex


def rename(text, new="int"):
    lines = text.split("\n")
    count = 0
    for t in reversed(lex(text)):
        if t.kind == "ident" and t.text == "integer":
            row, col = t.line - 1, t.col - 1
            if lines[row][col:col + 7].lower() == "integer":
                lines[row] = lines[row][:col] + new + lines[row][col + 7:]
                count += 1
    return "\n".join(lines), count


def main(args):
    new = "int"
    if args and args[0] == "--int32":
        new, args = "int32", args[1:]
    for path in args:
        text = open(path).read()
        try:
            out, count = rename(text, new)
        except Exception as err:
            print(f"{path}: {err}", file=sys.stderr)
            continue
        if count:
            open(path, "w").write(out)
            print(f"{path}: {count} word(s) renamed")


if __name__ == "__main__":
    main(sys.argv[1:])
