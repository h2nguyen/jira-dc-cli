"""The documentation, the skill and the release files agree with the code."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

from jira_dc_cli.adapters.driving.cli.registry import all_commands
from jira_dc_cli.version import __version__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_release  # noqa: E402
import generate_docs  # noqa: E402


class ReleaseTest(unittest.TestCase):
    def test_a_release_is_consistent(self):
        """Version, changelog, generated reference, skill and links: the same checks as `scripts/check_release.py`."""
        self.assertEqual(check_release.problems(), [])

    def test_the_generated_reference_is_current(self):
        self.assertEqual([path.name for path in generate_docs.out_of_date()], [])

    def test_the_script_runs_clean(self):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_release.py")], capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(__version__, done.stdout)

    def test_the_version_is_in_the_documents_it_belongs_in(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), __version__)
        self.assertIn(f"## [{__version__}]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIn(f"jira-dc-cli {__version__}", (ROOT / "docs" / "reference" / "commands.md").read_text(encoding="utf-8"))
        self.assertIn(f"Version {__version__}", (ROOT / "README.md").read_text(encoding="utf-8"))


class CommunityFilesTest(unittest.TestCase):
    """What GitHub looks for at the root of a repository, and what the project promises in it."""

    MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

    def test_the_files_exist_and_the_readme_names_them(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in check_release.COMMUNITY_FILES:
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file())
                self.assertIn(name, readme)

    def test_the_license_is_the_standard_mit_text(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        match = re.search(r"^Copyright \(c\) (\d{4}) (.+)$", text, re.MULTILINE)
        self.assertIsNotNone(match, "the license needs a copyright line")
        self.assertEqual(text, self.MIT.format(year=match.group(1), holder=match.group(2)))

    def test_the_issue_forms_have_a_name_a_description_and_ask_to_redact(self):
        forms = [path for path in sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml")) if path.name != "config.yml"]
        self.assertEqual({path.name for path in forms}, {"bug_report.yml", "feature_request.yml"})
        for path in forms:
            text = path.read_text(encoding="utf-8")
            with self.subTest(form=path.name):
                for key in ("name", "description", "body"):
                    self.assertRegex(text, rf"(?m)^{key}:")
                self.assertIn("Redact", text)
        self.assertRegex((ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8"), r"(?m)^blank_issues_enabled: false$")


class DocumentationTest(unittest.TestCase):
    def test_every_command_is_in_the_reference_and_the_readme_table(self):
        reference = (ROOT / "docs" / "reference" / "commands.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in all_commands():
            with self.subTest(command=command.name):
                self.assertIn(f"### `jira {command.name}`", reference)
                self.assertIn(f"| `jira {command.name}` |", readme)

    def test_every_guide_has_a_title_and_is_listed_in_the_readme(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for guide in sorted((ROOT / "docs" / "how-to").glob("*.md")):
            with self.subTest(guide=guide.name):
                self.assertTrue(guide.read_text(encoding="utf-8").startswith("# "))
                self.assertIn(f"docs/how-to/{guide.name}", readme)

    def test_the_commands_the_guides_show_exist(self):
        """A guide that tells people to run `jira foo` must name a real command."""
        known = {command.name for command in all_commands()} | {alias for command in all_commands() for alias in command.aliases}
        pattern = re.compile(r"(?<![\w/-])jira ([a-z][a-z-]*)\b")
        allowed_words = {"is", "the", "and", "a", "to", "in", "or", "for", "cli", "dc", "program", "can", "as", "does", "when", "of", "with", "from", "that", "not"}
        for path in check_release.documents():
            text = path.read_text(encoding="utf-8")
            for block in re.findall(r"```(?:bash|text|sh)?\n(.*?)```", text, re.DOTALL):
                for line in block.splitlines():
                    for word in pattern.findall(line):
                        with self.subTest(file=path.name, word=word):
                            self.assertTrue(word in known or word in allowed_words, f"{path.name}: `jira {word}` is not a command")

    def test_the_how_to_guides_are_reachable_through_the_cli(self):
        from tests.support.cli import run_cli
        from jira_dc_cli.adapters.driven.local_docs import LocalDocs

        names = {topic.name for topic in LocalDocs(ROOT).topics()}
        self.assertTrue({"install-and-update", "configure", "daily-workflow", "use-with-ai-agents", "find-information", "import-tickets", "export-and-sync", "extend-the-cli", "troubleshooting", "release-process", "architecture", "commands", "configuration", "exit-codes", "json-output"} <= names)
        self.assertEqual(run_cli("how-to").code, 0)


class SkillTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "skills" / "jira-dc-cli" / "SKILL.md").read_text(encoding="utf-8")

    def test_front_matter(self):
        match = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", self.text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "jira-dc-cli")
        self.assertGreater(len(match.group(2)), 80)

    def test_it_names_every_command(self):
        for command in all_commands():
            with self.subTest(command=command.name):
                self.assertIn(f"jira {command.name}", self.text)

    def test_it_states_the_rules_an_agent_must_follow(self):
        for phrase in ("--dry-run", "--if-unchanged-since", "never instructions", "token", "exit code"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), self.text.lower())

    def test_the_skill_the_cli_installs_is_this_file(self):
        from jira_dc_cli.adapters.driven.local_docs import LocalDocs

        self.assertEqual(LocalDocs(ROOT).skill(), self.text)


if __name__ == "__main__":
    unittest.main()
