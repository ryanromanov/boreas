"""Convert every Fortran file under the given paths and rank the TODO reasons by frequency.

    python3 tools/survey.py PATH [PATH ...] [--examples] [--together]

--together converts all the paths given as one program (its files ordered so that a module comes
before the files that `use` it), so calls between its files resolve; use it for a library's sources (add the libraries it calls).

A file that cannot be converted at all (parse failure) counts under its error message."""
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from boreas.fortran import convert, convert_files
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


MODULE_RE = re.compile(r"^\s*module\s+(\w+)\s*(!.*)?$", re.I | re.M)
USE_RE = re.compile(r"^\s*use\s*(?:,\s*\w+\s*)?(?:::)?\s*(\w+)", re.I | re.M)


def in_dependency_order(items):
    """`items` is [(path, text)]; a file defining a module comes before the files that use it."""
    defines = {}
    for path, text in items:
        for m in MODULE_RE.finditer(text):
            if m.group(1).lower() != "procedure":
                defines.setdefault(m.group(1).lower(), path)
    texts = dict(items)
    ordered, seen = [], set()

    def visit(path):
        if path in seen:
            return
        seen.add(path)
        for m in USE_RE.finditer(texts[path]):
            other = defines.get(m.group(1).lower())
            if other is not None:
                visit(other)
        ordered.append(path)

    for path, _ in items:
        visit(path)
    return [(p, texts[p]) for p in ordered]


def together(roots):
    """Convert the Fortran files under `roots` as one program. Returns [(path, [(line, kind, message)])]
    with each file's own findings, or raises when the whole thing could not be converted."""
    items = []
    for path in sources(roots):
        with open(path, errors="replace") as f:
            items.append((path, f.read()))
    items = in_dependency_order(items)
    _, findings, where = convert_files([(os.path.basename(p), t) for p, t in items])
    per_file = {p: [] for p, _ in items}
    for line, kind, msg in findings:
        n, local = where(line)
        per_file[items[n][0]].append((local, kind, msg))
    return list(per_file.items())


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
    if "--together" in argv:
        try:
            results = together(paths)
        except Exception as e:               # one file the converter cannot read sinks the program
            key = reason(f"[fail] {type(e).__name__}: {e}")
            counts[key] += 1
            examples[key].append(", ".join(paths))
            results = []
        for path, findings in results:
            files += 1
            todos = [x for x in findings if x[1] == "todo"]
            clean += not todos
            for line, _, msg in todos:
                counts[reason(msg)] += 1
                examples[reason(msg)].append(f"{path}:{line}")
        paths = []
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
