"""`boreas convert --verify`: run the original Fortran and the converted Boreas, compare output."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

DEFAULT_TIMEOUT = 10.0
TOLERANCE = 1e-9
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def words(text):
    """Normalise program output so Fortran list-directed output and Boreas output compare equal."""
    text = re.sub(r"[\[\],]", " ", text)
    return [{"T": "true", "F": "false"}.get(w, w) for w in text.split()]


def tokens_equal(x, y):
    try:
        fx, fy = float(x), float(y)
    except ValueError:
        return x == y
    return abs(fx - fy) <= TOLERANCE * max(1.0, abs(fx), abs(fy))


def first_difference(a, b):
    """Index of the first differing token (or the shorter length), or None if they match."""
    ta, tb = words(a), words(b)
    for i, (x, y) in enumerate(zip(ta, tb)):
        if not tokens_equal(x, y):
            return i
    return None if len(ta) == len(tb) else min(len(ta), len(tb))


def same_output(a, b):
    return first_difference(a, b) is None


def find_gfortran():
    return shutil.which(os.environ.get("FC") or "gfortran")


def locate(text, index):
    """(line number, token number within the line) of the index-th token, both 1-based."""
    seen = 0
    for n, line in enumerate(text.splitlines(), 1):
        count = len(words(line))
        if index < seen + count:
            return n, index - seen + 1
        seen += count
    return None


def describe_difference(orig, conv):
    """A short side-by-side description of the first differing token."""
    i = first_difference(orig, conv)
    to, tc = words(orig), words(conv)
    lines = [f"first difference at value {i + 1} (of {len(to)} original, {len(tc)} converted)"]
    for label, text, toks in (("original ", orig, to), ("converted", conv, tc)):
        shown = toks[i] if i < len(toks) else "<end of output>"
        at = locate(text, i) if i < len(toks) else None
        where = f" (line {at[0]}, value {at[1]} on that line)" if at else ""
        lines.append(f"  {label}: {shown}{where}")
    return "\n".join(lines)


class VerifyError(Exception):
    """Something other than a mismatch went wrong (status 2)."""


def run_limited(cmd, timeout, **kw):
    """Run with empty stdin and a timeout; returns CompletedProcess or None on timeout."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return None


def compile_original(gfortran, path, exe):
    for flags in (["-std=f2018"], []):
        r = subprocess.run([gfortran, *flags, path, "-o", exe], capture_output=True, text=True,
                           errors="replace")
        if r.returncode == 0:
            return
    raise VerifyError("could not compile the original with gfortran:\n" + r.stderr.rstrip())


def verify(path, boreas_text, had_todos, timeout=DEFAULT_TIMEOUT, report=None):
    """Returns the exit status: 0 verified, 1 mismatch or converted program failed, 2 could not verify."""
    def say(msg):
        print(msg, file=report or sys.stderr)

    gfortran = find_gfortran()
    if not gfortran:
        say("error: cannot verify: gfortran was not found (install it, or set FC to its path)")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "original")
        try:
            compile_original(gfortran, path, exe)
        except VerifyError as e:
            say(f"error: {e}")
            return 2
        orig = run_limited([exe], timeout)
        if orig is None:
            say(f"error: the original program did not finish within {timeout:g} s "
                f"(it may be waiting for input; verify runs it with empty stdin; see --timeout)")
            return 2
        if orig.returncode != 0:
            err = orig.stderr.strip().splitlines()
            say(f"note: the original exited with status {orig.returncode}"
                + (f": {err[-1]}" if err else ""))
            if "end of file" in orig.stderr.lower():
                say("note: it tried to read input, but verify gives programs empty stdin")

        bor = os.path.join(tmp, "converted.bor")
        with open(bor, "w") as f:
            f.write(boreas_text)
        env = dict(os.environ, PYTHONPATH=PACKAGE_PARENT + os.pathsep + os.environ.get("PYTHONPATH", ""))
        conv = run_limited([sys.executable, "-m", "boreas", bor], timeout, env=env, cwd=tmp)
        if conv is None:
            say(f"error: the converted program did not finish within {timeout:g} s")
            return 1
        if conv.returncode != 0:
            say("error: the converted program failed: "
                + (conv.stderr.strip() or f"exit status {conv.returncode}"))
            if had_todos:
                say("note: some constructs were not translated (TODO), so a failure is expected")
            return 1

    if same_output(orig.stdout, conv.stdout):
        say(f"verified: the converted program printed the same output as the original "
            f"({len(words(orig.stdout))} values compared)")
        return 0
    say("MISMATCH: the converted program printed different output from the original")
    say(describe_difference(orig.stdout, conv.stdout))
    if had_todos:
        say("note: some constructs were not translated (TODO), so a mismatch is expected")
    say("likely causes: Fortran reals are 32-bit by default but Boreas reals are doubles; "
        "Fortran integers are 32-bit and wrap on overflow, Python integers do not. "
        "(Spacing and commas in list-directed output are already ignored.)")
    return 1
