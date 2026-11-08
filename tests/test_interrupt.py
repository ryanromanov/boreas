import os, signal, subprocess, sys, tempfile, time, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")

LOOP = """program t
  println("ready")
  do while (true)
  end do
end program t
"""


class InterruptTests(unittest.TestCase):
    def test_sigint_exits_quietly_with_130_and_output_is_not_held_back(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "l.bor")
            with open(path, "w") as f:
                f.write(LOOP)
            env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
            p = subprocess.Popen([sys.executable, "-m", "boreas", path], cwd=ROOT, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(p.stdout.readline(), "ready\n")      # arrived while the program still runs
            p.send_signal(signal.SIGINT)
            _, err = p.communicate(timeout=20)
            self.assertEqual(p.returncode, 130)
            self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
