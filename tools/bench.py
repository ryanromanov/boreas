"""Time the compiler against the interpreter and gfortran on the programs in tools/bench/.

    python3 tools/bench.py [NAME ...]        (names without the extension; default: all)

For each NAME it builds NAME.bor (checked, and --release) and NAME.f90 (gfortran -O2, when gfortran is there), runs
them and the interpreter, and prints the times and the output of each. The compiled and interpreted output must be
identical; gfortran may differ in the last digits where it fuses a multiply and an add."""
import os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def timed(cmd, **kw):
    start = time.time()
    done = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return time.time() - start, done.stdout.strip()


def main(names):
    names = names or sorted(f[:-4] for f in os.listdir(os.path.join(HERE, "bench")) if f.endswith(".bor"))
    out = tempfile.mkdtemp()
    for name in names:
        bor, f90 = (os.path.join(HERE, "bench", name + ext) for ext in (".bor", ".f90"))
        rows = []
        for label, flags in (("compiled", []), ("compiled --release", ["--release"])):
            exe = os.path.join(out, name + ("_r" if flags else ""))
            built = subprocess.run([sys.executable, "-m", "boreas", "build", bor, "-o", exe, *flags], cwd=ROOT,
                                   capture_output=True, text=True)
            if built.returncode:
                print(name, label, built.stderr.strip())
                continue
            rows.append((label, *timed([exe])))
        if os.path.exists(f90) and subprocess.run(["which", "gfortran"], capture_output=True).returncode == 0:
            exe = os.path.join(out, name + "_f")
            subprocess.run(["gfortran", "-O2", "-o", exe, f90], capture_output=True)
            rows.append(("gfortran -O2", *timed([exe])))
        rows.append(("interpreter", *timed([sys.executable, "-m", "boreas", bor], cwd=ROOT)))
        print(f"== {name}")
        interp = rows[-1][1]
        for label, secs, text in rows:
            print(f"  {label:20} {secs:8.3f} s   {text[:70]}")
        if len(rows) > 1 and rows[0][2] != rows[-1][2]:
            print("  !! the compiled output differs from the interpreter's")
        print(f"  compiled is {rows[-1][1] / max(rows[0][1], 1e-6):.0f}x faster than the interpreter")


if __name__ == "__main__":
    main(sys.argv[1:])
