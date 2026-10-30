"""install.py offers the folder that already holds the launcher of the sibling CLI (conflu): both launchers belong side by side."""
import os
import unittest
from pathlib import Path

from tests.test_installer import InstallerCase


class SiblingLauncherTest(InstallerCase):
    def folder_with_conflu(self) -> Path:
        shared = self.root / "shared-launchers"
        shared.mkdir()
        (shared / ("conflu.cmd" if os.name == "nt" else "conflu")).write_text("# the sibling launcher\n", encoding="utf-8")
        return shared

    def test_the_exact_command_for_that_folder_is_printed_when_the_chosen_one_is_not_on_the_path(self):
        shared = self.folder_with_conflu()
        done = self.install(env={"PATH": os.pathsep.join([str(shared), self.env.get("PATH", "")])})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("is not on the PATH of this shell", done.stdout)
        self.assertIn("the folder that holds the launcher of conflu", done.stdout)
        self.assertRegex(done.stdout, r'python install\.py --bin-dir ".*shared-launchers"')

    def test_nothing_is_offered_when_no_folder_on_the_path_holds_it(self):
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("launcher of conflu", done.stdout)

    def test_the_chosen_folder_is_not_offered_again(self):
        self.bin.mkdir()
        (self.bin / ("conflu.cmd" if os.name == "nt" else "conflu")).write_text("# the sibling launcher\n", encoding="utf-8")
        done = self.install(env={"PATH": os.pathsep.join([str(self.bin), self.env.get("PATH", "")])})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("launcher of conflu", done.stdout)
        self.assertNotIn("is not on the PATH", done.stdout)


if __name__ == "__main__":
    unittest.main()
