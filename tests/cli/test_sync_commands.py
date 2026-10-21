"""`jira diff`, `jira pull` and `jira push` on the command line: what is printed, the JSON, the notes and the exit codes."""
import dataclasses
import re
import unittest

from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway, MemoryFiles

DIR = "mirror"
CLEAN = "h2. Goal\n\nSome *text* here."


def gateway():
    fake = FakeGateway()
    fake.page_cap = 100
    for key in list(fake.issues):
        fake.issues[key] = dataclasses.replace(fake.issues[key], description=CLEAN)

    def answer(jql):
        issues = sorted(fake.issues.values(), key=lambda issue: int(issue.key.split("-")[1]))
        found = re.search(r"key in \(([^)]*)\)", jql)
        wanted = {item.strip() for item in found.group(1).split(",")} if found else None
        return [issue for issue in issues if (issue.key in wanted if wanted else issue.key.startswith("PROJ-"))]

    fake.search_handler = answer
    return fake


class Folder(unittest.TestCase):
    def setUp(self):
        self.gateway = gateway()
        self.files = MemoryFiles()

    def cli(self, *argv):
        return run_cli(*argv, gateway=self.gateway, files=self.files)

    def name_of(self, key):
        return next(path for path in self.files.files if path.startswith(f"{DIR}/{key}-"))

    def edit(self, key, old, new):
        path = self.name_of(key)
        self.files.files[path] = self.files.files[path].replace(old, new, 1)


class PullCommandTest(Folder):
    def test_the_first_pull_creates_the_files_and_says_so(self):
        result = self.cli("pull", DIR)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out.splitlines()[0].split()[:3], ["created", "PROJ-1", "PROJ-1-export-the-report.md"])
        self.assertEqual(result.out.splitlines()[-1], "Done: created=6 updated=0 adopted=0 unchanged=0 skipped=0 conflict=0")

    def test_json(self):
        data = self.cli("pull", DIR, "--json").json()
        self.assertEqual(sorted(data), ["counts", "dryRun", "leftOut", "onlyLocal", "outcomes", "scope", "skipped"])
        self.assertEqual((data["scope"], data["dryRun"], data["counts"]["created"]), ("project PROJ", False, 6))
        self.assertEqual(sorted(data["outcomes"][0]), ["action", "fields", "file", "key", "lossy", "message"])
        again = self.cli("pull", DIR, "--json").json()
        self.assertEqual((again["counts"]["unchanged"], again["outcomes"]), (6, []))  # what is unchanged is not listed

    def test_a_dry_run_says_would_and_writes_nothing(self):
        result = self.cli("pull", DIR, "--dry-run")
        self.assertTrue(result.out.startswith("would create"))
        self.assertTrue(result.out.rstrip().endswith("(dry run, nothing was written)"))
        self.assertEqual(self.files.files, {})

    def test_the_issues_named_are_the_only_ones(self):
        self.cli("pull", DIR, "--issue", "PROJ-1,PROJ-2")
        self.assertEqual(sorted(path.split("/")[1][:6] for path in self.files.files), ["PROJ-1", "PROJ-2"])

    def test_no_new_update_and_force_reach_the_use_case(self):
        self.assertEqual(self.cli("pull", DIR, "--no-new").out.splitlines()[-1], "Done: created=0 updated=0 adopted=0 unchanged=0 skipped=6 conflict=0")
        self.assertEqual(self.files.files, {})
        self.cli("pull", DIR)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.gateway.issues["PROJ-2"] = dataclasses.replace(self.gateway.issues["PROJ-2"], summary="Changed there")
        self.assertEqual(self.cli("pull", DIR, "--update").code, 3)
        result = self.cli("pull", DIR, "--update", "--force")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("summary: Changed there", self.files.files[self.name_of("PROJ-2")])

    def test_a_conflict_prints_what_happened_and_ends_with_exit_code_3(self):
        self.cli("pull", DIR)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.gateway.issues["PROJ-2"] = dataclasses.replace(self.gateway.issues["PROJ-2"], summary="Changed there")
        result = self.cli("pull", DIR, "--update")
        self.assertEqual(result.code, 3)
        self.assertIn("conflict      PROJ-2", result.out)
        self.assertIn("(summary): Jira changed it and the file was edited too: nothing was overwritten", result.out)
        self.assertIn("changed in Jira after the last sync", result.err)
        as_json = self.cli("pull", DIR, "--update", "--json")
        self.assertEqual(as_json.code, 3)
        self.assertEqual(as_json.error()["code"], "CONFLICT")
        self.assertEqual(as_json.error()["details"], {"keys": ["PROJ-2"]})
        self.assertEqual(as_json.json()["counts"]["conflict"], 1)

    def test_a_note_names_the_files_that_jira_does_not_know(self):
        self.cli("pull", DIR)
        self.files.files[f"{DIR}/PROJ-77-gone.md"] = "---\nissue_key: PROJ-77\nsummary: x\n---\n"
        self.assertIn("In the folder but not in Jira: PROJ-77. Nothing was deleted.", self.cli("pull", DIR).err)

    def test_what_markdown_cannot_show_is_said_in_the_line(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], description="||a||b||\n|c|d|")
        self.assertIn("[Markdown cannot show: table]", self.cli("pull", DIR).out)

    def test_the_fields_that_were_left_out_are_named_on_standard_error_and_in_the_json(self):
        self.cli("pull", DIR)
        self.files.files[self.name_of("PROJ-2")] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        pulled = self.cli("pull", DIR, "--json")
        self.assertEqual(pulled.json()["skipped"], {"PROJ-2": {"description": "the text holds a relative link, which Jira cannot show the same way, so the description is not compared"}})
        self.assertIn("Not compared (`--json` says why): description of PROJ-2.", pulled.err)
        self.assertEqual(self.cli("pull", DIR).out.splitlines()[-1], "Done: created=0 updated=0 adopted=0 unchanged=6 skipped=0 conflict=0")
        diff = self.cli("diff", DIR)
        self.assertIn("Not compared (`--json` says why): description of PROJ-2.", diff.err)
        self.assertEqual(diff.code, 0)

    def test_files_that_cannot_be_read_are_in_the_answer(self):
        self.cli("pull", DIR)
        self.files.files[f"{DIR}/copy.md"] = self.files.files[self.name_of("PROJ-2")]
        out = self.cli("pull", DIR).out
        self.assertIn("skipped", out)
        self.assertIn("2 files hold this key", out)

    def test_wrong_calls_are_usage_errors(self):
        for argv in (("pull",), ("pull", DIR, "--issue", "PROJ"), ("pull", DIR, "--nope")):
            with self.subTest(argv=argv):
                self.assertNotEqual(self.cli(*argv).code, 0)


class DiffCommandTest(Folder):
    def setUp(self):
        super().setUp()
        self.cli("pull", DIR)

    def test_in_step_prints_one_line_and_no_table(self):
        result = self.cli("diff", DIR)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out.strip(), "project PROJ: 6 files, 6 issues in Jira | in step 6")

    def test_what_differs_is_a_table_with_the_side_and_the_fields(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.gateway.issues["PROJ-3"] = dataclasses.replace(self.gateway.issues["PROJ-3"], labels=["x"])
        self.edit("PROJ-4", "priority: High", "priority: Low")
        self.gateway.issues["PROJ-4"] = dataclasses.replace(self.gateway.issues["PROJ-4"], labels=["y"])
        result = self.cli("diff", DIR)
        lines = result.out.splitlines()
        self.assertIn("in step 3 | jira changed 1 | edited here 1 | both sides 1", lines[0])
        rows = {line.split()[0]: line for line in lines[3:]}
        self.assertIn("edited here   here: summary", rows["PROJ-2"])
        self.assertIn("jira changed  Jira: labels", rows["PROJ-3"])
        self.assertIn("both sides    here: priority; Jira: labels", rows["PROJ-4"])
        self.assertIn("`jira pull DIRECTORY --update`", result.err)
        self.assertIn("`jira push FILE --dry-run`", result.err)
        self.assertIn("neither touches the other's", result.err)

    def test_show_same_lists_everything(self):
        out = self.cli("diff", DIR, "--show-same").out
        self.assertEqual(sum(line.startswith("PROJ-") for line in out.splitlines()), 6)

    def test_json(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        data = self.cli("diff", DIR, "--json").json()
        self.assertEqual(sorted(data), ["counts", "entries", "fetchedAt", "files", "issues", "leftOut", "scope"])
        self.assertEqual((data["files"], data["issues"], data["counts"]["same"], data["counts"]["local-changed"]), (6, 6, 5, 1))
        entry = data["entries"][0]
        self.assertEqual(sorted(entry), ["changedInJira", "conflicting", "editedHere", "fields", "file", "key", "message", "skipped", "state", "unknown"])
        self.assertEqual((entry["key"], entry["state"], entry["editedHere"], entry["file"]), ("PROJ-2", "local-changed", ["summary"], "PROJ-2-fix-the-thing.md"))
        self.assertEqual(len(self.cli("diff", DIR, "--json", "--show-same").json()["entries"]), 6)

    def test_the_issue_option_narrows_it(self):
        self.assertEqual(self.cli("diff", DIR, "--issue", "PROJ-1").out.strip(), "PROJ-1: 6 files, 1 issues in Jira | in step 1")

    def test_the_project_option_names_the_project(self):
        data = self.cli("diff", DIR, "--project", "demo", "--json").json()
        self.assertEqual(data["scope"], "project DEMO")
        self.assertEqual(data["counts"]["same"], 0)  # the six files belong to PROJ: they are left out

    def test_a_diff_never_writes_and_never_calls_a_write(self):
        self.files.files[f"{DIR}/PROJ-9-x.md"] = self.files.files[f"{DIR}/PROJ-9-epic-nine.md"]
        before = dict(self.files.files)
        self.gateway.calls.clear()
        self.cli("diff", DIR, "--json")
        self.assertEqual(self.files.files, before)
        self.assertFalse([call for call in self.gateway.calls if call[0] in ("update_issue", "create_issue", "transition_issue", "add_comment")])


class SkippedNoteTest(unittest.TestCase):
    def test_it_names_the_fields_and_at_most_five_keys_each(self):
        from jira_dc_cli.adapters.driving.cli.commands.sync import skipped_note

        many = {f"PROJ-{number}": {"description": "why"} for number in range(1, 8)}
        self.assertEqual(skipped_note(many), "Not compared (`--json` says why): description of PROJ-1, PROJ-2, PROJ-3, PROJ-4, PROJ-5 and 2 more.")
        self.assertEqual(
            skipped_note({"PROJ-1": {"labels": "x", "description": "y"}, "PROJ-2": {"labels": "x"}}),
            "Not compared (`--json` says why): labels of PROJ-1, PROJ-2; description of PROJ-1.",
        )
        self.assertEqual(skipped_note({}), "")
        self.assertEqual(
            skipped_note({"PROJ-1": {"storyPoints": "this Jira has no story points field", "description": "y"}, "PROJ-2": {"storyPoints": "this Jira has no story points field"}}),
            "Not compared (`--json` says why): storyPoints (this Jira has no such field); description of PROJ-1.",
        )


class PushCommandTest(Folder):
    def setUp(self):
        super().setUp()
        self.cli("pull", DIR)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")

    def test_a_dry_run_prints_the_payload(self):
        result = self.cli("push", self.name_of("PROJ-2"), "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("would push PROJ-2 (summary) from PROJ-2-fix-the-thing.md", result.out)
        self.assertIn('"summary": "Fixed here"', result.out)
        self.assertTrue(result.out.rstrip().endswith("Dry run: nothing was sent."))
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_what_was_not_compared_is_said_with_the_push_and_with_the_dry_run(self):
        self.edit("PROJ-2", "\nSome", "\nSee [the ADR](../docs/adr/0001.md).\n\nSome")
        dry = self.cli("push", self.name_of("PROJ-2"), "--dry-run")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertEqual(dry.out.rstrip().splitlines()[-2:], ["not compared: description", "Dry run: nothing was sent."])
        sent = self.cli("push", self.name_of("PROJ-2"))
        self.assertEqual(sent.out.strip(), "pushed PROJ-2 (summary) from PROJ-2-fix-the-thing.md: not compared: description")
        again = self.cli("push", self.name_of("PROJ-2"), "--json").json()["outcomes"][0]
        self.assertEqual((again["action"], again["message"]), ("unchanged", "the file holds what Jira holds (not compared: description)"))

    def test_a_push_prints_what_was_sent(self):
        result = self.cli("push", self.name_of("PROJ-2"))
        self.assertEqual((result.code, result.out.strip()), (0, "pushed PROJ-2 (summary) from PROJ-2-fix-the-thing.md"))
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")

    def test_json(self):
        data = self.cli("push", self.name_of("PROJ-2"), "--json", "--dry-run").json()
        self.assertEqual(sorted(data), ["dryRun", "outcomes", "pushed", "refused", "unchanged"])
        self.assertEqual((data["dryRun"], data["pushed"], data["unchanged"], data["refused"]), (True, 1, 0, 0))
        self.assertEqual(sorted(data["outcomes"][0]), ["action", "fields", "file", "key", "message", "payload"])
        self.assertEqual(data["outcomes"][0]["payload"], {"fields": {"summary": "Fixed here"}})

    def test_a_conflict_is_printed_and_ends_with_exit_code_3_and_json_on_standard_error(self):
        self.gateway.issues["PROJ-2"] = dataclasses.replace(self.gateway.issues["PROJ-2"], summary="Changed there")
        result = self.cli("push", self.name_of("PROJ-2"), "--json")
        self.assertEqual(result.code, 3)
        self.assertEqual(result.json()["refused"], 1)
        self.assertEqual(result.error()["code"], "CONFLICT")
        self.assertIn("jira diff", result.error()["hint"])
        self.assertEqual(self.gateway.called("update_issue"), [])
        text = self.cli("push", self.name_of("PROJ-2"))
        self.assertIn("REFUSED PROJ-2", text.out)

    def test_unchanged_says_why(self):
        self.cli("push", self.name_of("PROJ-2"))
        result = self.cli("push", self.name_of("PROJ-2"))
        self.assertEqual(result.code, 0)
        self.assertIn("unchanged PROJ-2", result.out)

    def test_force_reaches_the_use_case(self):
        self.gateway.issues["PROJ-2"] = dataclasses.replace(self.gateway.issues["PROJ-2"], summary="Changed there")
        self.assertEqual(self.cli("push", self.name_of("PROJ-2")).code, 3)
        result = self.cli("push", self.name_of("PROJ-2"), "--force")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")

    def test_a_file_that_could_not_be_updated_after_the_push_is_said_in_the_line_and_is_no_failure(self):
        class Locked(MemoryFiles):
            def write_text(self, path, text):
                raise OSError("locked by another program")

        self.files = Locked(self.files.files)
        result = self.cli("push", self.name_of("PROJ-2"))
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("pushed PROJ-2 (summary) from PROJ-2-fix-the-thing.md: Jira holds the change, but the file could not be brought up to date (locked by another program)", result.out)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")

    def test_a_placeholder_is_a_validation_error_with_exit_code_1(self):
        self.edit("PROJ-2", "summary: Fixed here", "summary: Ask [~user]")
        result = self.cli("push", self.name_of("PROJ-2"), "--force", "--json")
        self.assertEqual(result.code, 1)
        self.assertIn("placeholder", result.error()["message"])

    def test_a_folder_needs_an_issue(self):
        result = self.cli("push", DIR, "--json")
        self.assertEqual(result.code, 1)
        self.assertIn("--issue", result.error()["hint"])
        self.assertEqual(self.cli("push", DIR, "--issue", "PROJ-2").code, 0)

    def test_a_missing_file_is_not_found(self):
        self.assertEqual(self.cli("push", f"{DIR}/nope.md").code, 4)

    def test_wrong_calls_are_usage_errors(self):
        for argv in (("push",), ("push", DIR, "--nope")):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, 2)


class HelpTest(unittest.TestCase):
    def test_the_commands_say_when_to_use_them(self):
        for name, phrase in (("diff", "Use before you pull or push"), ("pull", "Use to get Jira into a folder"), ("push", "Use to send what you edited")):
            with self.subTest(name=name):
                self.assertIn("When to use: " + phrase, run_cli("help", name).out.replace("\n", " ").replace("  ", " "))

    def test_the_commands_have_their_own_group_in_the_overview(self):
        out = run_cli("help").out
        self.assertIn("Sync with files\n  diff", out)
        self.assertLess(out.index("Create and import"), out.index("Sync with files"))
        self.assertLess(out.index("Sync with files"), out.index("Projects"))


if __name__ == "__main__":
    unittest.main()
