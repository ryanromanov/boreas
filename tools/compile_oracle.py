"""Check the compiler against the interpreter on a whole collection of programs.

    python3 tools/compile_oracle.py [DIRECTORY]       (default: a collection made by running the test suite)

With no directory it runs the test suite with BOREAS_COMPILE_CORPUS set, so that every program the tests run is saved,
then tries to compile each one. A program the compiler refuses is counted by the reason; one it compiles is run and
its output, error text and exit code are compared with the interpreter's. Prints how many programs compiled and every
difference (there should be none). Takes a few minutes."""
import collections, os, re, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")


def collect(directory):
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT,
                   env={**os.environ, "BOREAS_COMPILE_CORPUS": directory}, capture_output=True)


def run(args, **kw):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=40, **kw)


def main(directory=None):
    tmp = tempfile.mkdtemp()
    if directory is None:
        directory = os.path.join(tmp, "corpus")
        collect(directory)
    files = sorted(f for f in os.listdir(directory) if f.endswith(".bor"))
    refused, compiled, different, skipped = collections.Counter(), 0, [], 0
    for name in files:
        path = os.path.join(directory, name)
        exe = os.path.join(tmp, "prog")
        built = run([sys.executable, "-m", "boreas", "build", path, "-o", exe])
        if built.returncode != 0:
            m = re.search(r"yet: (.*?)( \(line \d+\))?$", built.stderr.strip().split("\n")[-1])
            refused[re.sub(r"'[^']*'", "'..'", m.group(1))[:70] if m else built.stderr.strip()[:80]] += 1
            continue
        compiled += 1
        try:
            want = run([sys.executable, "-m", "boreas", path])
        except subprocess.TimeoutExpired:
            skipped += 1                                   # the interpreter itself did not finish
            continue
        try:
            got = subprocess.run([exe], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        except subprocess.TimeoutExpired:
            different.append((name, "the compiled program did not finish"))
            continue
        if (got.stdout, got.stderr, got.returncode) != (want.stdout, want.stderr, want.returncode):
            different.append((name, f"interpreter {want.returncode} {want.stderr.strip()[:60]!r}, compiled {got.returncode} {got.stderr.strip()[:60]!r}"))
    print(f"{len(files)} programs: {compiled} compiled, {sum(refused.values())} refused")
    print(f"{len(different)} differences" + (f" ({skipped} skipped: the interpreter did not finish)" if skipped else ""))
    for name, why in different[:30]:
        print("  ", name, why)
    print("most common reasons for refusing:")
    for why, n in refused.most_common(int(os.environ.get("ORACLE_TOP", "15"))):
        print(f"  {n:5} {why}")
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
