"""A folder of Markdown tickets and a mock Jira, with the real adapters: what really goes over the wire and what is written to disk."""
import json
import re
import unittest

from jira_dc_cli.domain.frontmatter import parse_markdown_ticket
from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_jira import ACCEPT, POINTS

CLEAN = "h2. Goal\n\nSome *text* here.\n\nh2. Acceptance criteria\n\n# *GIVEN* a, *WHEN* b, *THEN* c."


class SyncFlowTest(EndToEnd):
    def setUp(self):
        super().setUp()
        for issue in self.jira.issues.values():
            issue["fields"]["description"] = CLEAN
        self.dir = self.folder / "mirror dir"  # a space in the name, as in a real path
        self.target = str(self.dir)

    def files(self):
        return sorted(path.name for path in self.dir.glob("*.md"))

    def file_of(self, key):
        return next(self.dir.glob(f"{key}-*.md"))

    def read(self, key):
        return self.file_of(key).read_text(encoding="utf-8")

    def edit(self, key, old, new):
        path = self.file_of(key)
        path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8", newline="\n")

    def writes(self):
        return [item for item in self.jira.requests if item.method in ("PUT", "POST", "DELETE")]

    def test_pull_writes_utf8_files_with_plain_line_feeds_and_no_person(self):
        self.jira.issues["PROJ-1"]["fields"]["description"] = "Ask [~jdoe] or jane.doe@example.com\nSecond line"
        result = self.cli("pull", self.target, "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json()["counts"]["created"], 6)
        self.assertEqual(self.files()[0], "PROJ-1-export-the-report-unicode-more.md")
        raw = self.file_of("PROJ-1").read_bytes()
        self.assertNotIn(b"\r", raw)
        text = raw.decode("utf-8")
        self.assertIn("summary: Export the report: Ünïcode & more", text)
        for personal in ("jane", "jdoe", "Jane"):
            self.assertNotIn(personal, text)
        self.assertIn("Ask [~user] or [email]", text)
        self.assertIn("assignee:\n", text)
        self.assertEqual(self.writes(), [])  # Jira was only read

    def test_the_files_are_tickets_the_importer_reads(self):
        self.cli("pull", self.target)
        ticket = parse_markdown_ticket(self.read("PROJ-1"))
        self.assertEqual((ticket.meta["issue_key"], ticket.meta["project"], ticket.meta["issuetype"], ticket.meta["priority"]), ("PROJ-1", "PROJ", "Story", "High"))
        self.assertEqual(json.loads(ticket.meta["fields_json"]), {POINTS: 5.0, ACCEPT: "1. GIVEN a, WHEN b, THEN c."})
        dry = self.cli("import-md", str(self.file_of("PROJ-1")), "--dry-run", "--wiki", "--json")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertEqual((dry.json()["action"], dry.json()["key"]), ("updated", "PROJ-1"))

    def test_everything_is_in_step_after_a_pull_and_a_diff_only_reads(self):
        self.cli("pull", self.target)
        self.jira.reset_requests()
        diff = self.cli("diff", self.target, "--json").json()
        self.assertEqual((diff["counts"]["same"], diff["entries"]), (6, []))
        self.assertEqual(self.writes(), [])
        self.assertGreaterEqual(len(self.jira.sent("GET", "/rest/api/2/search")), 3)  # the server answers two at a time: the CLI asks again

    def test_a_change_in_jira_comes_in_with_update_and_nothing_else_changes(self):
        self.cli("pull", self.target)
        before = self.read("PROJ-3")
        self.jira.issues["PROJ-2"]["fields"]["summary"] = "Changed there"
        self.jira.issues["PROJ-2"]["fields"]["labels"] = ["backend", "api"]
        self.assertEqual(self.cli("diff", self.target, "--json").json()["entries"][0]["changedInJira"], ["summary", "labels"])
        self.cli("pull", self.target)  # without --update nothing is rewritten
        self.assertIn("summary: Fix the thing", self.read("PROJ-2"))
        result = self.cli("pull", self.target, "--update", "--json")
        self.assertEqual(result.json()["counts"]["updated"], 1)
        text = self.read("PROJ-2")
        self.assertIn("summary: Changed there", text)
        self.assertIn("labels: backend,api", text)
        self.assertEqual(self.read("PROJ-3"), before)
        self.assertEqual(self.cli("diff", self.target, "--json").json()["counts"]["same"], 6)

    def test_an_edit_goes_to_jira_as_one_request_with_only_the_changed_fields(self):
        self.cli("pull", self.target)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.edit("PROJ-2", f'"{POINTS}":3.0', f'"{POINTS}":8.0')
        self.jira.reset_requests()
        dry = self.cli("push", str(self.file_of("PROJ-2")), "--dry-run", "--json")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertEqual(dry.json()["outcomes"][0]["payload"], {"fields": {"summary": "Fixed here", POINTS: 8.0}})
        self.assertEqual(self.writes(), [])
        result = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual(result.code, 0, result.err)
        puts = [item for item in self.writes() if item.method == "PUT"]
        self.assertEqual([(item.path, item.body) for item in puts], [("/rest/api/2/issue/PROJ-2", {"fields": {"summary": "Fixed here", POINTS: 8.0}})])
        fields = self.jira.issues["PROJ-2"]["fields"]
        self.assertEqual((fields["summary"], fields[POINTS]), ("Fixed here", 8.0))
        self.assertEqual(self.cli("diff", self.target, "--json").json()["counts"]["same"], 6)
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-2")), "--json").json()["unchanged"], 1)

    def test_the_description_goes_as_wiki_markup_and_comes_back_the_same(self):
        self.cli("pull", self.target)
        self.edit("PROJ-2", "Some **text** here.", "Some **new** text here.")
        self.cli("push", str(self.file_of("PROJ-2")))
        self.assertEqual(self.jira.issues["PROJ-2"]["fields"]["description"].splitlines()[2], "Some *new* text here.")
        self.assertEqual(self.cli("diff", self.target, "--json").json()["counts"]["same"], 6)

    def test_a_conflict_ends_with_exit_code_3_and_changes_nothing_anywhere(self):
        self.cli("pull", self.target)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.jira.issues["PROJ-2"]["fields"]["summary"] = "Changed there"
        files_before = {name: self.dir.joinpath(name).read_text(encoding="utf-8") for name in self.files()}
        self.jira.reset_requests()
        push = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual((push.code, push.error()["code"]), (3, "CONFLICT"))
        pull = self.cli("pull", self.target, "--update", "--json")
        self.assertEqual((pull.code, pull.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.writes(), [])
        self.assertEqual({name: self.dir.joinpath(name).read_text(encoding="utf-8") for name in self.files()}, files_before)

    def test_a_folder_in_the_format_of_a_planning_mirror_works_as_it_is(self):
        self.cli("pull", self.target, "--issue", "PROJ-2")
        text = self.read("PROJ-2")
        planning = "\n".join(line for line in text.split("\n") if not line.startswith(("jira_base:", "jira_synced:"))).replace("issue_key:", "jira_key:")
        self.file_of("PROJ-2").unlink()
        (self.dir / "032-story-SRC-014-fix.md").write_text("---\nsource_id: 32\n" + planning[4:], encoding="utf-8", newline="\n")
        (self.dir / "jira-sync-ignore.txt").write_text("# kept out\nPROJ-4 a test ticket\n", encoding="utf-8")
        diff = self.cli("diff", self.target, "--issue", "PROJ-2", "--json").json()
        self.assertEqual(diff["counts"]["same"], 1)
        self.assertEqual(self.cli("pull", self.target, "--issue", "PROJ-2", "--json").json()["counts"]["adopted"], 1)
        adopted = (self.dir / "032-story-SRC-014-fix.md").read_text(encoding="utf-8")
        self.assertIn("source_id: 32\n", adopted)
        self.assertIn("jira_key: PROJ-2", adopted)
        self.assertIn("jira_base: ", adopted)
        self.assertEqual(self.files(), ["032-story-SRC-014-fix.md"])
        everything = self.cli("diff", self.target, "--json").json()
        self.assertEqual(everything["counts"]["only-jira"], 4)  # PROJ-1, 3, 8 and 9: PROJ-4 is on the ignore list
        self.assertEqual([item["key"] for item in everything["leftOut"]], ["PROJ-4"])

    def test_a_missing_folder_is_created_by_a_pull_and_an_empty_one_by_a_diff(self):
        self.assertEqual(self.cli("diff", self.target, "--json").json()["counts"]["only-jira"], 6)
        self.assertFalse(self.dir.exists())
        self.cli("pull", self.target)
        self.assertEqual(len(self.files()), 6)

    def test_text_mode_prints_what_a_person_reads(self):
        self.cli("pull", self.target)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        diff = self.cli("diff", self.target)
        self.assertTrue(diff.out.startswith("project PROJ: 6 files, 6 issues in Jira | in step 5 | edited here 1"))
        self.assertRegex(diff.out, r"PROJ-2\s+edited here\s+here: summary\s+PROJ-2-fix-the-thing\.md")


class NoPersonTest(EndToEnd):
    def test_not_one_file_holds_a_person_whatever_jira_holds(self):
        fields = self.jira.issues["PROJ-1"]["fields"]
        fields["summary"] = "Review by [~jdoe]"
        fields["description"] = "h2. Goal\n\nMail jane.doe@example.com, see https://files.example.com/personal/jane_doe/x or [~jdoe]."
        fields[ACCEPT] = "1. *GIVEN* [~jdoe] writes jane.doe@example.com"
        folder = self.folder / "files"
        self.cli("pull", str(folder))
        text = "\n".join(path.read_text(encoding="utf-8") for path in folder.glob("*.md"))
        for personal in ("jdoe", "jane", "Jane", "sharepoint.com/personal"):
            self.assertNotIn(personal, text, personal)
        for placeholder in ("[~user]", "[email]", "[personal link]"):
            self.assertIn(placeholder, text)
        self.assertFalse(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text))


if __name__ == "__main__":
    unittest.main()
