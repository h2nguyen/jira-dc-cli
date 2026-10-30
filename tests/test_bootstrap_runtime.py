"""What `jira doctor` learns about the program itself: the launcher on the PATH, how this run started, the installer."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from jira_dc_cli.bootstrap import runtime_facts
from jira_dc_cli.version import __version__


class RuntimeFactsTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)

    def make_launcher(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        launcher = folder / ("jira.cmd" if os.name == "nt" else "jira")
        launcher.write_text("echo jira\n", encoding="utf-8")
        launcher.chmod(0o755)
        return launcher

    def test_the_launcher_is_found_on_the_path_given(self):
        launcher = self.make_launcher(self.root / "bin")
        facts = runtime_facts({"PATH": str(self.root / "bin")}, self.root)
        self.assertEqual(Path(facts["launcher"]).resolve(), launcher.resolve())

    def test_no_launcher_on_the_path_is_an_empty_text(self):
        facts = runtime_facts({"PATH": str(self.root / "nowhere")}, self.root)
        self.assertEqual(facts["launcher"], "")

    def test_the_installer_is_named_only_in_a_source_checkout(self):
        (self.root / "install.py").write_text("# installer\n", encoding="utf-8")
        # an installation holds a copy of install.py too, but that copy refuses to install over itself
        self.assertEqual(runtime_facts({"PATH": ""}, self.root)["install_script"], "")
        (self.root / "src" / "jira_dc_cli").mkdir(parents=True)
        self.assertEqual(Path(runtime_facts({"PATH": ""}, self.root)["install_script"]), self.root / "install.py")
        self.assertEqual(runtime_facts({"PATH": ""}, self.root / "elsewhere")["install_script"], "")

    def test_the_rest(self):
        facts = runtime_facts({"PATH": ""}, self.root)
        self.assertEqual((facts["version"], facts["python"]), (__version__, sys.version.split()[0]))
        self.assertEqual(facts["program"], sys.argv[0])


if __name__ == "__main__":
    unittest.main()
