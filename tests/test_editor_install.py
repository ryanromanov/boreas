"""editors/graupel/install.sh copies the scripts into Graupel's scripts folder."""
import os
import subprocess
import tempfile
import unittest

EDITOR = os.path.join(os.path.dirname(__file__), "..", "editors", "graupel")


class InstallTests(unittest.TestCase):
    def install(self, has_folder):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        scripts = os.path.join(home.name, "Library", "Application Scripts", "com.ryanromanov.Graupel")
        if has_folder:
            os.makedirs(scripts)
        result = subprocess.run(["sh", os.path.join(EDITOR, "install.sh")], capture_output=True, text=True,
                                env={**os.environ, "HOME": home.name}, check=True)
        return os.path.join(scripts, "Boreas"), result.stdout

    def test_copies_every_script(self):
        folder, _ = self.install(True)
        self.assertEqual(sorted(os.listdir(folder)), sorted(os.listdir(os.path.join(EDITOR, "Scripts"))))

    def test_explains_when_the_scripts_folder_is_missing(self):
        folder, output = self.install(False)
        self.assertIn("scripts folder not found", output)
        self.assertFalse(os.path.exists(folder))

    def test_an_old_run_script_is_removed(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        folder = os.path.join(home.name, "Library", "Application Scripts", "com.ryanromanov.Graupel", "Boreas")
        os.makedirs(folder)
        old = os.path.join(folder, "Run.@r.sh")
        open(old, "w").close()
        subprocess.run(["sh", os.path.join(EDITOR, "install.sh")], capture_output=True, text=True,
                       env={**os.environ, "HOME": home.name}, check=True)
        self.assertFalse(os.path.exists(old))
        self.assertNotIn("Run.@r.sh", os.listdir(os.path.join(EDITOR, "Scripts")))


if __name__ == "__main__":
    unittest.main()
