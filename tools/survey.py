"""Convert every Fortran file under the given paths and rank the TODO reasons by frequency.

    python3 tools/survey.py PATH [PATH ...] [--examples]

A file that cannot be converted at all (parse failure) counts under its error message."""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.fortran import convert
from boreas.lexer import LangError
from boreas.parser import parse

EXTENSIONS = (".f90", ".f95", ".f03", ".f08", ".f", ".for", ".f77")


def sources(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, _, names in os.walk(p):
            for n in sorted(names):
                if n.lower().endswith(EXTENSIONS):
                    yield os.path.join(root, n)


def reason(message):
    """Collapse names and numbers so similar messages count together."""
    return re.sub(r"\d+", "N", re.sub(r"'[^']*'", "'X'", message))[:100]


def main(argv):
    show = "--examples" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        sys.exit(__doc__)
    counts, examples = Counter(), defaultdict(list)
    files = clean = 0
    for path in sources(paths):
        files += 1
        try:
            with open(path, errors="replace") as f:
                text, findings = convert(f.read(), os.path.basename(path))
            try:
                parse(text)                  # the output must itself be valid Boreas
            except LangError as e:
                if "no program found" not in str(e):   # a library file has no program: fine
                    raise
        except Exception as e:               # LangError, Unsupported, anything else
            counts[reason(f"[fail] {type(e).__name__}: {e}")] += 1
            examples[reason(f"[fail] {type(e).__name__}: {e}")].append(path)
            continue
        todos = [x for x in findings if x[1] == "todo"]
        clean += not todos
        for line, _, msg in todos:
            counts[reason(msg)] += 1
            examples[reason(msg)].append(f"{path}:{line}")
    print(f"{files} files, {clean} converted with no TODOs")
    for msg, n in counts.most_common():
        print(f"{n:5}  {msg}")
        if show:
            for e in examples[msg][:3]:
                print(f"         {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
