"""editors/coteditor/install.sh copies the scripts for each editor that has a scripts folder."""
import os
import subprocess
import tempfile
import unittest

EDITOR = os.path.join(os.path.dirname(__file__), "..", "editors", "coteditor")
GRAUPEL = "com.ryanromanov.Graupel"
COTEDITOR = "com.coteditor.CotEditor"


class InstallTests(unittest.TestCase):
    def install(self, *apps):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        base = os.path.join(home.name, "Library", "Application Scripts")
        for app in apps:
            os.makedirs(os.path.join(base, app))
        result = subprocess.run(["sh", os.path.join(EDITOR, "install.sh")], capture_output=True, text=True,
                                env={**os.environ, "HOME": home.name}, check=True)
        return base, result.stdout

    def installed(self, base, app):
        folder = os.path.join(base, app, "Boreas")
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_installs_only_for_the_app_present(self):
        expected = sorted(os.listdir(os.path.join(EDITOR, "Scripts")))
        base, _ = self.install(GRAUPEL)
        self.assertEqual(self.installed(base, GRAUPEL), expected)
        self.assertEqual(self.installed(base, COTEDITOR), [])
        base, _ = self.install(COTEDITOR)
        self.assertEqual(self.installed(base, COTEDITOR), expected)
        self.assertEqual(self.installed(base, GRAUPEL), [])

    def test_installs_for_both_apps(self):
        base, _ = self.install(GRAUPEL, COTEDITOR)
        self.assertTrue(self.installed(base, GRAUPEL))
        self.assertTrue(self.installed(base, COTEDITOR))

    def test_explains_when_no_scripts_folder_exists(self):
        _, output = self.install()
        self.assertIn("scripts folder not found", output)

    def test_run_output_goes_to_the_console(self):
        with open(os.path.join(EDITOR, "Scripts", "Run.@r.sh")) as script:
            text = script.read()
        self.assertIn("CotEditorXOutput=Discard", text)
        self.assertIn(">&2", text)


if __name__ == "__main__":
    unittest.main()
