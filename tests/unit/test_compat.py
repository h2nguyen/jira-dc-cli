"""What the CLI says about the Jira version it meets: it is written for Jira Data Center 11.3.x."""
import unittest

from jira_dc_cli.domain.compat import OK, TARGET, WARN, assess, parse_version
from tests.support.cli import run_cli


class ParseVersionTest(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(parse_version("11.3.12"), (11, 3, 12))
        self.assertEqual(parse_version("9.12"), (9, 12, 0))
        self.assertEqual(parse_version(" 10 "), (10, 0, 0))
        self.assertEqual(parse_version("11.3.1-SNAPSHOT"), (11, 3, 1))

    def test_no_version(self):
        for text in ("", "unknown", None):
            self.assertIsNone(parse_version(text))

    def test_a_number_that_no_release_has_is_no_version(self):
        for text in ("9" * 5000, "1" * 10 + ".3", "²"):
            self.assertIsNone(parse_version(text), text[:12])
        self.assertEqual(parse_version("11." + "3" * 5000), (11, 0, 0))
        self.assertEqual(assess("9" * 5000).status, WARN)

    def test_what_the_server_calls_its_version_is_cut_and_cleaned_before_it_is_printed(self):
        long = assess("11.3.1" + "x" * 5000, "S" * 5000)
        self.assertEqual(long.status, OK)
        self.assertLess(len(long.detail), 300)
        self.assertLess(len(assess("9" * 5000).detail), 300)
        dirty = assess("\x1b[2J11.3.1\nsecond line", "Server\x07")
        self.assertNotIn("\x1b", dirty.detail)
        self.assertNotIn("\n", dirty.detail)


class AssessTest(unittest.TestCase):
    def test_the_release_line_it_is_written_for(self):
        self.assertEqual(TARGET, "11.3.x")
        for version in ("11.3.0", "11.3.12"):
            result = assess(version, "Server")
            self.assertEqual(result.status, OK, version)
            self.assertIn("11.3.x release line", result.detail)

    def test_the_same_major_release_is_fine(self):
        for version, side in (("11.1.0", "older"), ("11.4.2", "newer")):
            result = assess(version, "Server")
            self.assertEqual(result.status, OK)
            self.assertIn(side, result.detail)

    def test_older_and_newer_majors_warn(self):
        older = assess("9.12.0", "Server")
        self.assertEqual(older.status, WARN)
        self.assertIn("older", older.detail)
        self.assertIn("11.3.x", older.hint)
        self.assertEqual(assess("12.0.0").status, WARN)
        self.assertIn("much older", assess("8.20.1").detail)

    def test_jira_cloud_is_not_what_it_is_written_for(self):
        result = assess("1001.0.0", "Cloud")
        self.assertEqual(result.status, WARN)
        self.assertIn("Cloud", result.detail)

    def test_an_unreadable_version_warns(self):
        self.assertEqual(assess("", "Server").status, WARN)
        self.assertEqual(assess("abc").status, WARN)


class WhereTheReleaseLineIsStated(unittest.TestCase):
    def test_the_version_command_names_it_in_json(self):
        data = run_cli("version", "--json").json()
        self.assertEqual(data["jiraDataCenter"], TARGET)

    def test_the_documents_state_it(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for relative in ("README.md", "docs/reference/compatibility.md", "skills/jira-dc-cli/SKILL.md"):
            with self.subTest(document=relative):
                self.assertIn("11.3", (root / relative).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
