"""`jira help --brief`: every command with its arguments and main options in one short text, and the compact reference of the skill."""
import re
import sys
import unittest
from pathlib import Path

from jira_dc_cli.adapters.driving.cli.help import BRIEF_BUDGET, render_brief, render_compact_reference
from jira_dc_cli.adapters.driving.cli.registry import all_commands
from tests.support.cli import run_cli

ROOT = Path(__file__).resolve().parents[2]
NAMES = [command.name for command in all_commands()]


class BriefTest(unittest.TestCase):
    def test_one_call_prints_every_command_in_at_most_four_kilobytes(self):
        result = run_cli("help", "--brief")
        self.assertEqual(result.code, 0, result.err)
        self.assertLessEqual(len(result.out) - 1, BRIEF_BUDGET)  # the print adds a line end
        self.assertEqual(BRIEF_BUDGET, 4096)
        for name in NAMES:
            with self.subTest(command=name):
                self.assertRegex(result.out, rf"(?m)^  {re.escape(name)}\*?(?: |$)")

    def test_a_star_marks_the_commands_that_change_data_and_the_text_explains_it(self):
        out = run_cli("help", "--brief").out
        self.assertRegex(out, r"(?m)^  update\* ")
        self.assertRegex(out, r"(?m)^  transition\* ")
        self.assertRegex(out, r"(?m)^  issue ")
        self.assertIn("* changes data: use --dry-run first where the command has it", out)

    def test_an_option_that_must_be_given_stands_outside_the_brackets_and_is_never_cut(self):
        out = run_cli("help", "--brief").out
        self.assertRegex(out, r"(?m)^  link\* ISSUE_KEY --type --to \[")
        small = render_brief(all_commands(), budget=1)  # everything that can be cut is cut
        self.assertRegex(small, r"(?m)^  link\* ISSUE_KEY --type --to(?: \[\.\.\.\])?$")

    def test_the_groups_are_named_and_help_for_one_command_is_pointed_to(self):
        out = run_cli("help", "--brief").out
        for group in ("Read:", "Work on an issue:", "Setup and information:"):
            self.assertIn(group, out)
        self.assertIn("jira help COMMAND", out)

    def test_without_a_budget_nothing_is_cut(self):
        full = render_brief(all_commands(), budget=None)
        self.assertIn("--if-unchanged-since", full)
        self.assertNotIn(" ...]", full)
        self.assertNotIn("[...]", full)
        self.assertGreaterEqual(len(full), len(render_brief(all_commands())))

    def test_the_options_are_cut_when_the_text_would_be_too_long_and_the_cut_is_shown(self):
        full = render_brief(all_commands(), budget=None)
        cut = render_brief(all_commands(), budget=len(full) - 200)
        self.assertLess(len(cut), len(full))
        self.assertRegex(cut, r"( \.\.\.\]|\[\.\.\.\])")

    def test_a_smaller_budget_cuts_more_but_keeps_every_command(self):
        full = render_brief(all_commands(), budget=None)
        small = render_brief(all_commands(), budget=1500)
        for name in NAMES:
            self.assertRegex(small, rf"(?m)^  {re.escape(name)}\*?(?: |$)")
        self.assertLess(len(small), len(full))

    def test_names_keep_only_those_commands(self):
        text = render_brief(all_commands(), budget=None, names=("comment", "api"))
        self.assertIn("  comment* ", text)
        self.assertIn("  api* ", text)
        self.assertNotIn("  issue ", text)

    def test_a_command_name_with_brief_is_a_usage_error(self):
        result = run_cli("help", "issue", "--brief")
        self.assertEqual(result.code, 2)
        self.assertIn("jira help COMMAND", result.err)

    def test_help_without_brief_is_unchanged(self):
        self.assertEqual(run_cli("help").out, run_cli().out)


class CompactReferenceTest(unittest.TestCase):
    def test_all_options_and_one_example_per_verb(self):
        text = render_compact_reference(all_commands(), ("comment", "api"))
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("comment* ISSUE_KEY ["))
        self.assertIn("--list", lines[0])
        self.assertTrue(lines[1].startswith("    jira comment "))
        self.assertTrue(any(line.startswith("api* METHOD PATH [") for line in lines))
        self.assertNotIn("...", lines[0])

    def test_the_skill_carries_it_for_the_verbs_agents_looked_up_most(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import generate_docs

        skill = (ROOT / "skills" / "jira-dc-cli" / "SKILL.md").read_text(encoding="utf-8")
        for name in generate_docs.SKILL_REFERENCE:
            self.assertRegex(skill, rf"(?m)^{re.escape(name)}\*? ")
        self.assertEqual(generate_docs.SKILL_REFERENCE, ("comment", "api", "create", "update", "pull", "diff"))


if __name__ == "__main__":
    unittest.main()
