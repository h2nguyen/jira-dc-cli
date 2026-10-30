"""A folder of Markdown tickets and a mock Jira, with the real adapters: what really goes over the wire and what is written to disk."""
import json
import re
import unittest

from jira_dc_cli.domain.frontmatter import parse_markdown_ticket
from jira_dc_cli.domain.mirror import MirrorFile
from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_jira import ACCEPT, POINTS

CLEAN = "h2. Goal\n\nSome *text* here.\n\nh2. Acceptance criteria\n\n# *GIVEN* a, *WHEN* b, *THEN* c."


class MirrorFolder(EndToEnd):
    """A folder next to the mock Jira. At the end of every test no call was made that the Jira Data Center 11.3 reference does not document."""

    def setUp(self):
        super().setUp()
        for issue in self.jira.issues.values():
            issue["fields"]["description"] = CLEAN
        self.dir = self.folder / "mirror dir"  # a space in the name, as in a real path
        self.target = str(self.dir)

    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])

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


class SyncFlowTest(MirrorFolder):
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


class LinksFlowTest(MirrorFolder):
    """The links between issues, through the real adapters: the file, the requests that go to Jira and what Jira answers."""

    def link(self, key, to, type_name="Blocks", *, inverse=False):
        """Somebody links two issues in Jira (the command `jira link`, which is no part of the sync); the requests it sent are forgotten."""
        argv = ["link", key, "--type", type_name, "--to", to] + (["--inverse"] if inverse else [])
        self.assertEqual(self.cli(*argv).code, 0)
        self.jira.reset_requests()

    def line(self, key):
        return MirrorFile.parse(self.read(key)).get("links")

    def write_line(self, key, line):
        text = self.read(key)
        if MirrorFile.parse(text).has("links"):
            self.edit(key, f"links: {self.line(key)}".rstrip(), f"links: {line}")
        else:
            self.edit(key, "fix_versions: Release 1.0\n", f"fix_versions: Release 1.0\nlinks: {line}\n")

    def states(self):
        return {entry["key"]: entry["state"] for entry in self.cli("diff", self.target, "--json", "--show-same").json()["entries"]}

    def posted(self):
        return [item.body for item in self.jira.sent("POST", "/rest/api/2/issueLink")]

    def test_pull_writes_the_links_of_jira_into_the_files_and_a_second_run_is_quiet(self):
        self.link("PROJ-1", "PROJ-2")
        self.link("PROJ-3", "PROJ-1", "Has to be done after")
        result = self.cli("pull", self.target, "--json")
        self.assertEqual((result.code, result.json()["counts"]["created"]), (0, 6))
        self.assertEqual(self.line("PROJ-1"), "Blocks -> PROJ-2, Has to be done after <- PROJ-3")
        self.assertEqual((self.line("PROJ-2"), self.line("PROJ-3")), ("Blocks <- PROJ-1", "Has to be done after -> PROJ-1"))
        self.assertFalse(MirrorFile.parse(self.read("PROJ-4")).has("links"))
        self.assertIn("links=", self.read("PROJ-4"))
        asked = self.jira.sent("GET", "/rest/api/2/search")[0].query["fields"][0].split(",")
        self.assertIn("issuelinks", asked)
        self.assertEqual(self.writes(), [])  # Jira was only read
        self.assertEqual(set(self.states().values()), {"same"})
        self.assertEqual(self.cli("pull", self.target, "--json").json()["counts"]["unchanged"], 6)

    def test_the_file_is_utf8_with_plain_line_feeds_and_the_importer_reads_it(self):
        self.link("PROJ-1", "PROJ-2")
        self.cli("pull", self.target)
        raw = self.file_of("PROJ-1").read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn(b"\nlinks: Blocks -> PROJ-2\n", raw)
        ticket = parse_markdown_ticket(raw.decode("utf-8"))
        self.assertEqual(ticket.meta["links"], "Blocks -> PROJ-2")
        dry = self.cli("import-md", str(self.file_of("PROJ-1")), "--dry-run", "--wiki", "--json")
        self.assertEqual((dry.code, dry.json()["action"]), (0, "updated"))
        self.assertNotIn("links", json.dumps(dry.json()["payload"]).lower())  # `import-md` leaves the links alone

    def test_a_link_written_into_a_file_is_shown_by_a_dry_run_and_created_by_a_push(self):
        self.cli("pull", self.target)
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        diff = self.cli("diff", self.target, "--json").json()
        self.assertEqual([(e["key"], e["state"], e["editedHere"]) for e in diff["entries"]], [("PROJ-2", "local-changed", ["links"])])
        self.jira.reset_requests()
        dry = self.cli("push", str(self.file_of("PROJ-2")), "--dry-run", "--json")
        self.assertEqual(dry.code, 0, dry.err)
        outcome = dry.json()["outcomes"][0]
        expected = {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-2"}, "inwardIssue": {"key": "PROJ-4"}}
        self.assertEqual((outcome["action"], outcome["fields"], outcome["payload"], outcome["links"][0]["payload"]), ("pushed", ["links"], None, expected))
        self.assertEqual(self.writes(), [])  # nothing was sent
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issueLinkType")), 1)  # the type was checked
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/PROJ-4")), 1)  # and the other issue
        result = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.posted(), [expected])
        self.assertEqual([item.method for item in self.writes()], ["POST"])
        fields = self.jira.issues["PROJ-2"]["fields"]["issuelinks"]
        self.assertEqual((fields[0]["type"]["name"], fields[0]["outwardIssue"]["key"]), ("Blocks", "PROJ-4"))
        self.assertEqual(self.jira.issues["PROJ-4"]["fields"]["issuelinks"][0]["inwardIssue"]["key"], "PROJ-2")
        self.assertEqual(self.states()["PROJ-2"], "same")
        self.assertEqual(self.states()["PROJ-4"], "jira-changed")  # the file of the other end learns of it
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-2")), "--json").json()["unchanged"], 1)  # nothing is created a second time
        self.assertEqual(len(self.posted()), 1)
        self.cli("pull", self.target, "--update")
        self.assertEqual(self.line("PROJ-4"), "Blocks <- PROJ-2")
        self.assertEqual(set(self.states().values()), {"same"})

    def test_the_end_of_the_issue_decides_which_issue_is_the_outward_end(self):
        self.cli("pull", self.target)
        self.write_line("PROJ-2", "Blocks <- PROJ-4")
        self.jira.reset_requests()
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-2"))).code, 0)
        self.assertEqual(self.posted(), [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-4"}, "inwardIssue": {"key": "PROJ-2"}}])
        self.assertEqual(self.jira.issues["PROJ-2"]["fields"]["issuelinks"][0]["inwardIssue"]["key"], "PROJ-4")

    def test_a_type_name_with_blanks_goes_through_the_file_and_the_wire(self):
        self.link("PROJ-3", "PROJ-1", "Has to be done after")
        self.cli("pull", self.target)
        self.write_line("PROJ-3", "Has to be done after -> PROJ-1, has to be done after -> PROJ-4")
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-3"))).code, 0)
        self.assertEqual(self.posted(), [{"type": {"name": "Has to be done after"}, "outwardIssue": {"key": "PROJ-3"}, "inwardIssue": {"key": "PROJ-4"}}])

    def test_a_summary_and_a_link_are_two_requests_the_summary_first(self):
        self.cli("pull", self.target)
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.jira.reset_requests()
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-2"))).code, 0)
        self.assertEqual([(item.method, item.path) for item in self.writes()], [("PUT", "/rest/api/2/issue/PROJ-2"), ("POST", "/rest/api/2/issueLink")])
        self.assertEqual(self.writes()[0].body, {"fields": {"summary": "Fixed here"}})
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_link_that_the_file_lost_stays_in_jira_and_is_named(self):
        self.link("PROJ-2", "PROJ-4")
        self.cli("pull", self.target, "--update")
        self.write_line("PROJ-2", "")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.jira.reset_requests()
        result = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual(result.code, 0, result.err)
        outcome = result.json()["outcomes"][0]
        self.assertEqual((outcome["action"], outcome["fields"], outcome["jiraOnlyLinks"]), ("pushed", ["summary"], ["Blocks -> PROJ-4"]))
        self.assertIn("jira has it; remove it in Jira", outcome["message"])
        self.assertEqual([item.method for item in self.writes()], ["PUT"])  # no DELETE, no POST: the CLI deletes no link
        self.assertEqual(len(self.jira.issues["PROJ-2"]["fields"]["issuelinks"]), 1)
        self.assertEqual(self.states()["PROJ-2"], "local-changed")

    def test_an_unknown_type_and_an_unknown_issue_are_refused_before_anything_is_sent(self):
        self.cli("pull", self.target)
        self.write_line("PROJ-2", "Blokcs -> PROJ-4")
        self.jira.reset_requests()
        result = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
        self.assertIn("Available: Blocks, Has to be done after, Pre-requisite", result.error()["hint"])
        self.write_line("PROJ-2", "Blocks -> PROJ-77")
        missing = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual((missing.code, missing.error()["code"]), (4, "NOT_FOUND"))
        self.assertEqual(self.writes(), [])

    def test_a_refused_permission_is_exit_code_5_with_the_permission_in_the_hint_and_the_file_stays_an_edit(self):
        self.cli("pull", self.target)
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.jira.fail_next("/rest/api/2/issueLink", 403, method="POST", body={"errorMessages": ["You do not have permission to link issues."]})
        before = self.read("PROJ-2")
        result = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("'Link Issues' permission", result.error()["hint"])
        self.assertIn("PROJ-2 blocks PROJ-4", result.error()["message"])
        self.assertEqual((self.read("PROJ-2"), self.jira.issues["PROJ-2"]["fields"]["issuelinks"]), (before, []))
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-2"))).code, 0)  # the next try goes through
        self.assertEqual(len(self.jira.issues["PROJ-2"]["fields"]["issuelinks"]), 1)

    def test_the_same_links_changed_on_both_sides_are_a_conflict_that_changes_nothing_in_that_file_or_in_jira(self):
        self.cli("pull", self.target)
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.link("PROJ-2", "PROJ-3", "Pre-requisite")
        before = self.read("PROJ-2")
        push = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        pull = self.cli("pull", self.target, "--update", "--json")
        self.assertEqual((push.code, push.error()["code"], pull.code, pull.error()["code"]), (3, "CONFLICT", 3, "CONFLICT"))
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.read("PROJ-2"), before)
        self.assertEqual(self.line("PROJ-3"), "Pre-requisite <- PROJ-2")  # the other end of the link had no edit: its file took the news in
        self.assertEqual(self.cli("push", str(self.file_of("PROJ-2")), "--force").code, 0)  # the file wins: its link is created, the other one stays
        self.assertEqual(len(self.jira.issues["PROJ-2"]["fields"]["issuelinks"]), 2)

    def test_a_file_that_version_1_0_0_wrote_keeps_working(self):
        self.cli("pull", self.target)
        for key in ("PROJ-1", "PROJ-2"):  # what 1.0.0 wrote: a record without the links
            text = self.read(key)
            record = re.search(r"^jira_base: (.*)$", text, re.MULTILINE).group(1)
            self.edit(key, record, ",".join(item for item in record.split(",") if not item.startswith("links=")))
        self.assertEqual(set(self.states().values()), {"same"})
        self.jira.reset_requests()
        outcome = self.cli("pull", self.target, "--issue", "PROJ-1", "--json").json()["outcomes"][0]
        self.assertEqual((outcome["action"], outcome["fields"]), ("adopted", []))  # the first pull only records the agreement
        self.assertIn("links=", self.read("PROJ-1"))
        self.assertEqual(self.writes(), [])
        self.link("PROJ-2", "PROJ-4")  # now Jira holds a link that the old file never listed
        self.assertEqual(self.states()["PROJ-2"], "jira-changed")
        self.assertEqual(self.cli("pull", self.target, "--update", "--issue", "PROJ-2").code, 0)
        self.assertEqual(self.line("PROJ-2"), "Blocks -> PROJ-4")
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_file_that_version_1_0_0_wrote_can_push_a_new_link_without_force(self):
        self.cli("pull", self.target)
        text = self.read("PROJ-2")
        record = re.search(r"^jira_base: (.*)$", text, re.MULTILINE).group(1)
        self.edit("PROJ-2", record, ",".join(item for item in record.split(",") if not item.startswith("links=")))
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.jira.reset_requests()
        result = self.cli("push", str(self.file_of("PROJ-2")), "--json")
        self.assertEqual((result.code, result.json()["pushed"]), (0, 1))
        self.assertEqual(len(self.posted()), 1)
        self.assertEqual(self.states()["PROJ-2"], "same")


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
