"""Check the static type checker against the interpreter on the whole test suite.

    python3 tools/type_oracle.py [how many lines of each list to show]

Runs the tests with BOREAS_TYPE_ORACLE set, so every expression the interpreter evaluates is compared with the type
the checker worked out for it, then prints: how many expressions had a static type, every difference between the two
(there should be none), and which expressions the checker still leaves UNKNOWN, most common first."""
import collections, json, os, subprocess, sys, tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main(show=25):
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "oracle.jsonl")
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT,
                       env={**os.environ, "BOREAS_TYPE_ORACLE": log}, capture_output=True)
        rows = [json.loads(line) for line in open(log)] if os.path.exists(log) else []
    counts = [r["counts"] for r in rows if "counts" in r]
    evaluated = sum(c["evaluated"] for c in counts)
    typed = sum(c["typed"] for c in counts)
    print(f"{evaluated} expressions evaluated, {typed} with a static type ({100 * typed / max(evaluated, 1):.1f}%)")
    for r in (r for r in rows if "crash" in r):
        print("CHECKER CRASH:", r["crash"])
    groups = collections.defaultdict(list)
    for r in (r for r in rows if "why" in r):
        groups[(r["why"], r["static"], r["runtime"], r["node"])].append(r["expr"])
    print(f"{sum(len(v) for v in groups.values())} differences in {len(groups)} groups")
    for key, exprs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:show]:
        print(f"  {len(exprs)}x {key[0]}: static {key[1]}, runtime {key[2]} ({key[3]}), e.g. {exprs[0][:60]}")
    unknown = collections.Counter()
    for r in rows:
        unknown.update(r.get("untyped", {}))
    print("most common expressions still UNKNOWN:")
    for key, n in unknown.most_common(show):
        print(f"  {n:6} {key}")
    return 1 if groups else 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 25))
