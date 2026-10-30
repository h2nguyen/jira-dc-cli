"""A folder of Markdown tickets in step with Jira: diff, pull and push, with the states a file can be in."""
import dataclasses
import re
import unittest

from jira_dc_cli.domain.errors import AuthError, ConflictError, NotFoundError, RejectedError, UnavailableError, UsageError, ValidationError
from jira_dc_cli.domain.mirror import MirrorFile
from jira_dc_cli.domain.model import IssueLink, IssueRef, LinkType
from jira_dc_cli.domain.payloads import link_payload
from tests.support.fakes import FakeGateway, MemoryFiles, build_app, sample_issue

DIR = "mirror"
CLEAN = "h2. Goal\n\nSome *text* here.\n\nh2. Acceptance criteria\n\n# *GIVEN* a, *WHEN* b, *THEN* c."


class SpyFiles(MemoryFiles):
    def __init__(self, files=None):
        super().__init__(files)
        self.writes: list[str] = []
        self.locked: set[str] = set()  # a file that another program holds open

    def write_text(self, path, text):
        if path in self.locked:
            raise OSError("locked by another program")
        self.writes.append(path)
        super().write_text(path, text)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.gateway.page_cap = 100
        self.gateway.search_handler = self.answer
        for key in list(self.gateway.issues):
            self.put(key, description=CLEAN)
        self.files = SpyFiles()
        self.app = build_app(gateway=self.gateway, files=self.files)

    def answer(self, jql):
        issues = sorted(self.gateway.issues.values(), key=lambda issue: int(issue.key.split("-")[1]))
        found = re.search(r"key in \(([^)]*)\)", jql)
        if found:
            wanted = {item.strip() for item in found.group(1).split(",")}
            return [issue for issue in issues if issue.key in wanted]
        return [issue for issue in issues if issue.key.startswith("PROJ-")]

    def put(self, key, **changes):
        self.gateway.issues[key] = dataclasses.replace(self.gateway.issues[key], **changes)

    def pull(self, **options):
        return self.app.pull_mirror.execute(DIR, **options)

    def diff(self, **options):
        return self.app.diff_mirror.execute(DIR, **options)

    def path_of(self, key):
        return next(path for path in self.files.files if path.startswith(f"{DIR}/{key}-"))

    def text_of(self, key):
        return self.files.files[self.path_of(key)]

    def edit(self, key, old, new):
        path = self.path_of(key)
        self.files.files[path] = self.files.files[path].replace(old, new, 1)
        self.assertNotEqual(self.files.files[path], self.files.files[path].replace(new, old, 1) if old != new else "")

    def states(self, **options):
        return {entry.key: entry.state for entry in self.diff(**options).entries}

    def updates(self):
        return self.gateway.called("update_issue")


class PullTest(Fixture):
    def test_an_empty_folder_gets_a_file_per_issue(self):
        result = self.pull()
        self.assertEqual((result.count("created"), len(self.files.files)), (6, 6))
        self.assertEqual(sorted(path.split("/")[1] for path in self.files.files)[:2], ["PROJ-1-export-the-report.md", "PROJ-2-fix-the-thing.md"])
        ticket = MirrorFile.parse(self.text_of("PROJ-1"))
        self.assertEqual((ticket.key, ticket.key_line, ticket.get("project"), ticket.get("issuetype"), ticket.get("priority")), ("PROJ-1", "issue_key", "PROJ", "Story", "High"))
        self.assertEqual((ticket.get("epic_key"), ticket.get("assignee"), ticket.get("labels"), ticket.get("components")), ("PROJ-9", "", "backend", "Demo"))
        self.assertEqual(ticket.fields(), {"customfield_10001": 5.0, "customfield_10004": "1. GIVEN a, WHEN b, THEN c."})
        self.assertIsNotNone(ticket.base)
        self.assertTrue(ticket.get("jira_synced"))
        self.assertIn("## Goal", ticket.rest)
        self.assertIn("Some **text** here.", ticket.rest)

    def test_no_person_gets_into_a_file(self):
        self.put("PROJ-1", description="Ask [~jdoe] or jane.doe@example.com\nSecond line", summary="Mail jane.doe@example.com")
        self.pull()
        text = "\n".join(self.files.files.values())
        for personal in ("jdoe", "Jane", "jane.doe"):
            self.assertNotIn(personal, text)
        self.assertIn("[~user]", text)
        self.assertIn("[email]", text)

    def test_a_second_pull_changes_nothing(self):
        self.pull()
        self.files.writes.clear()
        before = dict(self.files.files)
        result = self.pull()
        self.assertEqual((result.count("unchanged"), len(result.outcomes)), (6, 6))
        self.assertEqual((self.files.writes, self.files.files), ([], before))

    def test_a_dry_run_writes_nothing_and_says_what_it_would_do(self):
        result = self.pull(dry_run=True)
        self.assertEqual((self.files.files, result.count("created")), ({}, 6))
        self.assertTrue(all(outcome.dry_run for outcome in result.outcomes))

    def test_no_new_leaves_the_issues_without_a_file_alone(self):
        result = self.pull(new_files=False)
        self.assertEqual((self.files.files, result.count("skipped")), ({}, 6))

    def test_the_issues_asked_for_are_the_only_ones_pulled(self):
        result = self.pull(keys=["proj-2"])
        self.assertEqual(list(self.files.files), [f"{DIR}/PROJ-2-fix-the-thing.md"])
        self.assertEqual(result.scope, "PROJ-2")
        self.assertEqual(self.gateway.called("search")[0][1], "key in (PROJ-2)")

    def test_a_folder_that_uses_jira_key_gets_new_files_with_jira_key(self):
        self.files.files[f"{DIR}/001-epic.md"] = "---\nsource_id: 7\njira_key: PROJ-9\nsummary: Epic nine\n---\nText\n"
        self.pull()
        self.assertEqual(MirrorFile.parse(self.text_of("PROJ-1")).key_line, "jira_key")

    def test_the_epic_of_a_new_file_is_named_by_the_source_id_of_its_file(self):
        self.files.files[f"{DIR}/001-epic.md"] = "---\nsource_id: 7\njira_key: PROJ-9\nsummary: Epic nine\n---\nText\n"
        self.pull()
        self.assertEqual(MirrorFile.parse(self.text_of("PROJ-1")).get("parent_id"), "7")
        self.assertEqual(MirrorFile.parse(self.text_of("PROJ-3")).get("parent_id"), "")  # its epic has no file with a source id

    def test_a_file_of_that_name_that_holds_another_key_is_not_overwritten(self):
        self.files.files[f"{DIR}/PROJ-1-export-the-report.md"] = "---\nsummary: other\n---\n"
        result = self.pull(keys=["PROJ-1"])
        self.assertEqual(result.outcomes[0].action, "skipped")
        self.assertEqual(self.files.files[f"{DIR}/PROJ-1-export-the-report.md"], "---\nsummary: other\n---\n")

    def test_what_markdown_cannot_show_is_said_and_marked_in_the_file(self):
        self.put("PROJ-1", description="h2. Goal\n\n||a||b||\n|c|d|\n")
        result = self.pull(keys=["PROJ-1"])
        self.assertEqual(result.outcomes[0].lossy, ["table"])
        self.assertEqual(MirrorFile.parse(self.text_of("PROJ-1")).get("lossy"), "table")

    def test_a_project_is_needed_when_no_issue_is_named(self):
        app = build_app(gateway=self.gateway, files=self.files, settings=dataclasses.replace(__import__("tests.support.fakes", fromlist=["DEFAULT_SETTINGS"]).DEFAULT_SETTINGS, project=""))
        with self.assertRaises(UsageError):
            app.pull_mirror.execute(DIR)

    def test_a_path_that_is_a_file_is_no_folder(self):
        self.files.files["notes.md"] = "x"
        with self.assertRaises(ValidationError):
            self.app.pull_mirror.execute("notes.md")

    def test_a_partial_read_compares_and_writes_nothing(self):
        real = self.gateway.search

        def one_short(jql, *, fields, start_at=0, max_results=50):
            page = real(jql, fields=fields, start_at=start_at, max_results=max_results)
            return dataclasses.replace(page, total=page.total + 1)  # Jira said one more issue than it sent

        self.gateway.search = one_short
        for action in (self.pull, self.diff):
            with self.subTest(action=action.__name__):
                with self.assertRaises(UnavailableError):
                    action()
        self.assertEqual(self.files.files, {})


class AdoptTest(Fixture):
    def test_a_file_that_holds_what_jira_holds_gets_two_lines_and_nothing_else(self):
        self.pull(keys=["PROJ-1"])
        pulled = self.text_of("PROJ-1")
        original = "\n".join(line for line in pulled.split("\n") if not line.startswith(("jira_base:", "jira_synced:")))
        self.files.files[self.path_of("PROJ-1")] = original
        self.assertEqual(self.states(keys=["PROJ-1"]), {"PROJ-1": "same"})
        result = self.pull(keys=["PROJ-1"])
        self.assertEqual(result.outcomes[0].action, "adopted")
        adopted = self.text_of("PROJ-1")
        self.assertEqual([line for line in adopted.split("\n") if not line.startswith(("jira_base:", "jira_synced:"))], original.split("\n"))
        self.assertIsNotNone(MirrorFile.parse(adopted).base)
        self.assertEqual(self.pull(keys=["PROJ-1"]).outcomes[0].action, "unchanged")

    def test_a_dry_run_adopts_nothing(self):
        self.pull(keys=["PROJ-1"])
        original = "\n".join(line for line in self.text_of("PROJ-1").split("\n") if not line.startswith(("jira_base:", "jira_synced:")))
        self.files.files[self.path_of("PROJ-1")] = original
        self.files.writes.clear()
        self.assertEqual(self.pull(keys=["PROJ-1"], dry_run=True).outcomes[0].action, "adopted")
        self.assertEqual((self.files.writes, self.text_of("PROJ-1")), ([], original))

    def test_a_file_in_the_format_of_a_planning_mirror_is_read_as_it_is(self):
        self.put("PROJ-4", summary="Schedule the report", labels=["reporting", "export"], priority="Very High", components=["Reporting"], fix_versions=["Release 1.0"], story_points=5.0, acceptance="1. *GIVEN* a user.", description="h2. User story\n\nAs an admin.\n")
        self.files.files[f"{DIR}/032-story.md"] = (
            "---\nsource_id: 32\nparent_id: 3\nproject:\nissuetype: Story\nsummary: Schedule the report\nlabels: reporting,export\npriority: Very High\n"
            "components: Reporting\nfix_versions: Release 1.0\nassignee:\nsource_key: SRC-014\njira_key: PROJ-4\n"
            'fields_json: {"customfield_10001":5.0,"customfield_10004":"1. *GIVEN* a user."}\n---\n\n## User story\n\nAs an admin.\n'
        )
        self.assertEqual(self.states(keys=["PROJ-4"]), {"PROJ-4": "same"})


class DiffTest(Fixture):
    def setUp(self):
        super().setUp()
        self.pull()

    def test_everything_is_in_step_after_a_pull(self):
        plan = self.diff()
        self.assertEqual(plan.counts["same"], 6)
        self.assertTrue(all(entry.state == "same" for entry in plan.entries))
        self.assertEqual((plan.files, plan.issues, plan.scope), (6, 6, "project PROJ"))

    def test_jira_changed(self):
        self.put("PROJ-2", summary="Changed there", labels=["x"])
        entry = next(e for e in self.diff().entries if e.key == "PROJ-2")
        self.assertEqual((entry.state, entry.fields, entry.file), ("jira-changed", ["summary", "labels"], "PROJ-2-fix-the-thing.md"))

    def test_edited_here(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.assertEqual(self.states()["PROJ-2"], "local-changed")

    def test_both_changed(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there")
        self.assertEqual(self.states()["PROJ-2"], "conflict")

    def test_different_and_never_synced(self):
        text = self.text_of("PROJ-2")
        self.files.files[self.path_of("PROJ-2")] = "\n".join(line for line in text.split("\n") if not line.startswith("jira_base:")).replace("summary: Fix the thing", "summary: Other")
        self.assertEqual(self.states()["PROJ-2"], "differs")

    def test_an_issue_without_a_file_and_a_file_without_an_issue(self):
        del self.files.files[self.path_of("PROJ-3")]
        self.files.files[f"{DIR}/PROJ-77-gone.md"] = "---\nissue_key: PROJ-77\nsummary: x\n---\n"
        states = self.states()
        self.assertEqual((states["PROJ-3"], states["PROJ-77"]), ("only-jira", "only-local"))
        self.assertIn("moved, deleted", next(e for e in self.diff().entries if e.key == "PROJ-77").message)

    def test_two_files_with_one_key_are_reported_and_left_alone(self):
        self.files.files[f"{DIR}/copy.md"] = self.text_of("PROJ-2")
        entry = next(e for e in self.diff().entries if e.key == "PROJ-2")
        self.assertEqual(entry.state, "duplicate")
        self.assertIn("copy.md", entry.message)

    def test_a_file_that_cannot_be_read_does_not_stop_the_others(self):
        self.files.files[f"{DIR}/broken.md"] = "---\nsummary: no end\n"
        self.files.files[f"{DIR}/README.md"] = "# no front matter\n"
        self.files.files[f"{DIR}/nokey.md"] = "---\nsummary: x\n---\n"
        plan = self.diff()
        self.assertEqual(plan.counts["same"], 6)
        reasons = {item["file"]: item["why"] for item in plan.left_out}
        self.assertIn("cannot be read", reasons["broken.md"])
        self.assertIn("front matter", reasons["README.md"])
        self.assertIn("no jira_key or issue_key", reasons["nokey.md"])

    def test_a_broken_fields_json_is_an_unreadable_entry(self):
        self.edit("PROJ-2", "fields_json:", "fields_json: {broken ")
        entry = next(e for e in self.diff().entries if e.key == "PROJ-2")
        self.assertEqual(entry.state, "unreadable")
        self.assertIn("fields_json", entry.message)

    def test_the_ignore_file_and_other_projects_are_left_out(self):
        self.files.files[f"{DIR}/jira-sync-ignore.txt"] = "\ufeff# tests\nproj-4 a test ticket\n\nPROJ-9\n"
        self.files.files[f"{DIR}/DEMO-1-x.md"] = "---\nissue_key: DEMO-1\nsummary: x\n---\n"
        plan = self.diff()
        self.assertEqual(plan.counts["same"], 4)
        why = {item.get("key"): item["why"] for item in plan.left_out}
        self.assertIn("jira-sync-ignore.txt", why["PROJ-4"])
        self.assertIn("another project", why["DEMO-1"])
        self.assertNotIn("PROJ-4", self.states())

    def test_a_sanitized_file_is_compared_without_its_description(self):
        path = self.path_of("PROJ-2")
        self.files.files[path] = self.files.files[path].replace("assignee:", "mirror: sanitized\nassignee:", 1) + "\nWords that are not Jira's.\n"
        self.put("PROJ-2", description="Something else entirely")
        entry = next((e for e in self.diff().entries if e.key == "PROJ-2"), None)
        self.assertEqual(entry.state, "same")
        self.assertIn("description", entry.skipped)

    def test_a_relative_link_leaves_the_description_out_too(self):
        path = self.path_of("PROJ-2")
        self.files.files[path] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        entry = next(e for e in self.diff().entries if e.key == "PROJ-2")
        self.assertEqual(entry.state, "same")
        self.assertIn("relative link", entry.skipped["description"])

    def test_the_issues_asked_for_are_the_only_ones_compared(self):
        plan = self.diff(keys=["PROJ-1", "PROJ-2"])
        self.assertEqual((sorted(entry.key for entry in plan.entries), plan.files, plan.issues), (["PROJ-1", "PROJ-2"], 6, 2))

    def test_an_issue_that_jira_does_not_give_back_is_never_a_partial_picture(self):
        real = self.gateway.search_handler
        self.gateway.search_handler = lambda jql: real(jql)[:3]  # the total says 3, so all of them arrive
        self.assertEqual(self.diff().issues, 3)

    def test_a_missing_folder_is_an_empty_one(self):
        plan = self.app.diff_mirror.execute("nowhere")
        self.assertEqual((plan.files, plan.counts["only-jira"]), (0, 6))

    def test_nothing_is_written_by_a_diff(self):
        self.files.writes.clear()
        self.put("PROJ-2", summary="Changed there")
        self.diff()
        self.assertEqual(self.files.writes, [])
        self.assertEqual(self.updates(), [])


class PullUpdateTest(Fixture):
    def setUp(self):
        super().setUp()
        self.pull()
        self.files.writes.clear()

    def test_what_jira_changed_waits_for_update(self):
        self.put("PROJ-2", summary="Changed there")
        outcome = self.pull().outcomes[1]
        self.assertEqual((outcome.key, outcome.action, outcome.fields), ("PROJ-2", "skipped", ["summary"]))
        self.assertIn("--update", outcome.message)
        self.assertEqual(self.files.writes, [])

    def test_update_rewrites_the_fields_that_jira_changed_and_nothing_else(self):
        self.put("PROJ-2", summary="Changed there", labels=["a", "b"])
        before = self.text_of("PROJ-2")
        outcome = self.pull(update=True).outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("updated", ["summary", "labels"]))
        after = self.text_of("PROJ-2")
        self.assertIn("summary: Changed there", after)
        self.assertIn("labels: a,b", after)
        self.assertEqual(self.files.writes, [self.path_of("PROJ-2")])
        changed = [(old, new) for old, new in zip(before.split("\n"), after.split("\n")) if old != new]
        self.assertEqual(sorted(old.split(":")[0] for old, _ in changed), ["jira_base", "labels", "summary"])  # the time is the same: the clock stands still
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_the_description_is_rewritten_as_markdown(self):
        self.put("PROJ-2", description="h2. New goal\n\nA *new* text.")
        self.pull(update=True)
        self.assertIn("## New goal", self.text_of("PROJ-2"))
        self.assertIn("A **new** text.", self.text_of("PROJ-2"))
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_story_points_and_acceptance_are_rewritten_in_fields_json(self):
        self.put("PROJ-2", story_points=13.0, acceptance="1. *GIVEN* other")
        self.pull(update=True)
        self.assertEqual(MirrorFile.parse(self.text_of("PROJ-2")).fields(), {"customfield_10001": 13.0, "customfield_10004": "1. *GIVEN* other"})

    def test_what_was_edited_here_is_never_overwritten(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        before = self.text_of("PROJ-2")
        outcome = self.pull(update=True, force=True).outcomes[1]
        self.assertEqual((outcome.action, self.text_of("PROJ-2")), ("skipped", before))
        self.assertIn("jira push", outcome.message)

    def test_an_edit_here_and_a_change_in_another_field_there_are_both_kept_by_a_pull(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", labels=["changed", "there"])
        entry = next(e for e in self.diff().entries if e.key == "PROJ-2")
        self.assertEqual((entry.state, entry.local_fields, entry.jira_fields, entry.conflicts), ("mixed", ["summary"], ["labels"], []))
        outcome = self.pull(update=True).outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("updated", ["labels"]))
        self.assertIn("your edit of summary stays", outcome.message)
        text = self.text_of("PROJ-2")
        self.assertIn("summary: Fixed here", text)
        self.assertIn("labels: changed,there", text)
        self.assertEqual(self.states()["PROJ-2"], "local-changed")  # the edit still waits to be sent

    def test_force_in_a_conflict_takes_jiras_field_and_keeps_an_edit_of_another_field(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.edit("PROJ-2", "priority: High", "priority: Low")
        self.put("PROJ-2", summary="Changed there")
        result = self.pull(update=True, force=True)
        self.assertEqual((result.outcomes[1].action, result.outcomes[1].fields, result.first_error), ("updated", ["summary"], None))
        text = self.text_of("PROJ-2")
        self.assertIn("summary: Changed there", text)
        self.assertIn("priority: Low", text)
        self.assertEqual(self.states()["PROJ-2"], "local-changed")

    def test_a_conflict_overwrites_nothing_and_ends_with_exit_code_3(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there")
        before = self.text_of("PROJ-2")
        result = self.pull(update=True)
        self.assertEqual(result.outcomes[1].action, "conflict")
        self.assertIsInstance(result.first_error, ConflictError)
        self.assertEqual(result.first_error.exit_code, 3)
        self.assertEqual(result.first_error.details, {"keys": ["PROJ-2"]})
        self.assertIn("PROJ-2", result.first_error.message)
        self.assertEqual(self.text_of("PROJ-2"), before)

    def test_force_lets_jira_win_over_a_conflict(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there")
        result = self.pull(update=True, force=True)
        self.assertEqual((result.outcomes[1].action, result.first_error), ("updated", None))
        self.assertIn("summary: Changed there", self.text_of("PROJ-2"))

    def test_force_without_update_changes_nothing(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there")
        before = self.text_of("PROJ-2")
        result = self.pull(force=True)
        self.assertEqual((result.outcomes[1].action, self.text_of("PROJ-2")), ("conflict", before))
        self.assertIsInstance(result.first_error, ConflictError)

    def test_a_file_that_differs_without_a_record_waits_for_force(self):
        text = self.text_of("PROJ-2")
        self.files.files[self.path_of("PROJ-2")] = "\n".join(line for line in text.split("\n") if not line.startswith("jira_base:")).replace("summary: Fix the thing", "summary: Other")
        skipped = self.pull(update=True).outcomes[1]
        self.assertEqual(skipped.action, "skipped")
        self.assertIn("--update --force", skipped.message)
        self.assertEqual(self.pull(update=True, force=True).outcomes[1].action, "updated")
        self.assertIn("summary: Fix the thing", self.text_of("PROJ-2"))

    def test_a_dry_run_of_an_update_writes_nothing(self):
        self.put("PROJ-2", summary="Changed there")
        outcome = self.pull(update=True, dry_run=True).outcomes[1]
        self.assertEqual((outcome.action, outcome.dry_run, self.files.writes), ("updated", True, []))
        self.assertIn("summary: Fix the thing", self.text_of("PROJ-2"))

    def test_the_files_in_the_folder_that_jira_does_not_know_are_named_and_kept(self):
        self.files.files[f"{DIR}/PROJ-77-gone.md"] = "---\nissue_key: PROJ-77\nsummary: x\n---\n"
        result = self.pull(update=True)
        self.assertEqual(result.only_local, ["PROJ-77"])
        self.assertIn(f"{DIR}/PROJ-77-gone.md", self.files.files)


class PushTest(Fixture):
    def setUp(self):
        super().setUp()
        self.pull()
        self.files.writes.clear()
        self.gateway.calls.clear()

    def push(self, key="PROJ-2", **options):
        return self.app.push_mirror.execute(self.path_of(key), **options)

    def test_a_dry_run_shows_the_payload_and_sends_nothing(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        result = self.push(dry_run=True)
        outcome = result.outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, outcome.payload, outcome.dry_run), ("pushed", ["summary"], {"fields": {"summary": "Fixed here"}}, True))
        self.assertEqual((self.updates(), self.files.writes), ([], []))

    def test_only_the_fields_that_differ_are_sent(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.edit("PROJ-2", "labels: backend", "labels: backend,api")
        self.push()
        self.assertEqual(self.updates(), [("update_issue", "PROJ-2", {"fields": {"summary": "Fixed here", "labels": ["backend", "api"]}})])

    def test_after_a_push_the_file_and_jira_are_in_step_and_a_second_push_has_nothing_to_do(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        result = self.push()
        self.assertEqual((result.first_error, result.outcomes[0].action), (None, "pushed"))
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")
        self.assertEqual(self.states()["PROJ-2"], "same")
        again = self.push()
        self.assertEqual(again.outcomes[0].action, "unchanged")
        self.assertEqual(len(self.updates()), 1)

    def test_the_description_goes_as_wiki_markup(self):
        self.edit("PROJ-2", "Some **text** here.", "Some **new** text here.")
        self.push()
        self.assertEqual(self.gateway.issues["PROJ-2"].description.splitlines()[2], "Some *new* text here.")

    def test_story_points_and_acceptance_go_to_their_fields(self):
        self.edit("PROJ-2", '"customfield_10001":3.0', '"customfield_10001":8.0')
        self.push()
        self.assertEqual(self.updates()[0][2], {"fields": {"customfield_10001": 8.0}})
        self.assertEqual(self.gateway.issues["PROJ-2"].story_points, 8.0)

    def test_what_jira_changed_is_not_overwritten_and_not_sent(self):
        self.put("PROJ-2", summary="Changed there")
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.action, self.updates()), ("unchanged", []))
        self.assertIn("jira pull --update", outcome.message)

    def test_a_conflict_is_refused_with_exit_code_3_and_nothing_is_sent(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there")
        result = self.push()
        self.assertIsInstance(result.first_error, ConflictError)
        self.assertEqual((result.first_error.exit_code, result.outcomes[0].action, self.updates()), (3, "refused", []))
        self.assertIn("jira diff", result.first_error.hint)

    def test_force_sends_the_file_over_a_conflict(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there")
        self.push(force=True)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")

    def test_a_file_that_was_never_synced_and_differs_is_refused_until_force(self):
        text = self.text_of("PROJ-2")
        self.files.files[self.path_of("PROJ-2")] = "\n".join(line for line in text.split("\n") if not line.startswith("jira_base:")).replace("summary: Fix the thing", "summary: Other")
        result = self.push()
        self.assertEqual((result.first_error.exit_code, self.updates()), (3, []))
        self.assertIn("never synced", result.first_error.message)
        self.push(force=True)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Other")

    def test_a_change_in_the_moment_between_reading_and_writing_is_caught(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        real = self.gateway.get_issue

        def racing(key, *, fields=None):
            issue = real(key, fields=fields)
            if fields is not None and tuple(fields) == ("summary", "updated"):  # the check just before the write
                return dataclasses.replace(issue, updated="2026-10-25T09:00:00.000+0200")
            return issue

        self.gateway.get_issue = racing
        result = self.push()
        self.assertEqual((result.first_error.exit_code, self.updates()), (3, []))

    def test_a_placeholder_is_never_sent_not_even_with_force(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Ask [~user]")
        for force in (False, True):
            result = self.push(force=force)
            self.assertIsInstance(result.first_error, ValidationError)
            self.assertIn("summary", result.first_error.message)
        self.assertEqual(self.updates(), [])

    def test_a_placeholder_that_is_not_sent_does_not_matter(self):
        self.put("PROJ-2", description="Ask [~jdoe] or jane.doe@example.com", summary="Fix the thing")
        self.pull(update=True)  # the file now holds the redacted text
        self.gateway.calls.clear()
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.assertEqual(self.push().outcomes[0].action, "pushed")
        self.assertEqual(self.updates()[0][2], {"fields": {"summary": "Fixed here"}})

    def test_a_description_that_markdown_cannot_carry_is_not_overwritten(self):
        self.put("PROJ-2", description="h2. Goal\n\n{panel}\nImportant\n{panel}\n")
        self.pull(update=True)
        self.gateway.calls.clear()
        self.edit("PROJ-2", "Important", "Very important")
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("panel", result.first_error.message)
        self.assertEqual(self.updates(), [])
        self.push(force=True)
        self.assertEqual(len(self.updates()), 1)

    def test_a_sanitized_file_does_not_send_its_description(self):
        path = self.path_of("PROJ-2")
        self.files.files[path] = self.files.files[path].replace("assignee:", "mirror: sanitized\nassignee:", 1).replace("Some **text** here.", "Words that are not Jira's.")
        self.assertEqual(self.states()["PROJ-2"], "same")  # the description is not compared, so the words that are not Jira's make no difference
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        self.assertEqual(self.updates()[0][2], {"fields": {"summary": "Fixed here"}})

    def test_an_edit_here_and_a_change_in_another_field_there_are_no_conflict(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", labels=["changed", "there"])
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, self.updates()[0][2]), ("pushed", ["summary"], {"fields": {"summary": "Fixed here"}}))
        self.assertIn("labels", outcome.message)
        self.assertEqual(self.gateway.issues["PROJ-2"].labels, ["changed", "there"])  # the old labels of the file did not overwrite Jira's
        self.assertEqual(self.states()["PROJ-2"], "jira-changed")  # what Jira changed still waits for a pull
        self.app.pull_mirror.execute(DIR, update=True)
        self.assertIn("labels: changed,there", self.text_of("PROJ-2"))
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_force_sends_what_is_in_conflict_or_unknown_but_never_reverts_what_only_jira_changed(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.put("PROJ-2", summary="Changed there", labels=["changed", "there"])
        self.push(force=True)
        issue = self.gateway.issues["PROJ-2"]
        self.assertEqual((issue.summary, issue.labels), ("Fixed here", ["changed", "there"]))

    def test_a_folder_needs_the_issue_to_change(self):
        with self.assertRaises(ValidationError) as caught:
            self.app.push_mirror.execute(DIR)
        self.assertIn("--issue", caught.exception.hint)
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.app.push_mirror.execute(DIR, keys=["proj-2"])
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")

    def test_the_files_must_exist_and_belong_to_an_issue(self):
        with self.assertRaises(NotFoundError):
            self.app.push_mirror.execute(f"{DIR}/nope.md")
        with self.assertRaises(NotFoundError):
            self.app.push_mirror.execute(DIR, keys=["PROJ-55"])
        self.files.files[f"{DIR}/nokey.md"] = "---\nsummary: x\n---\n"
        with self.assertRaises(ValidationError) as caught:
            self.app.push_mirror.execute(f"{DIR}/nokey.md")
        self.assertIn("import-md", caught.exception.hint)
        with self.assertRaises(ValidationError):
            self.app.push_mirror.execute(self.path_of("PROJ-2"), keys=["PROJ-3"])

    def test_a_file_whose_issue_is_gone_is_refused_with_not_found(self):
        self.files.files[f"{DIR}/PROJ-77-gone.md"] = "---\nissue_key: PROJ-77\nsummary: x\n---\n"
        result = self.app.push_mirror.execute(f"{DIR}/PROJ-77-gone.md")
        self.assertIsInstance(result.first_error, NotFoundError)
        self.assertEqual(result.outcomes[0].action, "refused")

    def test_several_issues_stop_at_the_first_refusal(self):
        self.edit("PROJ-1", "summary: Export the report", "summary: First")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Second")
        self.edit("PROJ-3", "summary: Finished story", "summary: Third")
        self.put("PROJ-2", summary="Changed there")
        result = self.app.push_mirror.execute(DIR, keys=["PROJ-1", "PROJ-2", "PROJ-3"])
        self.assertEqual([outcome.action for outcome in result.outcomes], ["pushed", "refused"])
        self.assertEqual(self.gateway.issues["PROJ-1"].summary, "First")
        self.assertEqual(self.gateway.issues["PROJ-3"].summary, "Finished story")

    def test_the_file_is_the_only_thing_that_is_written_locally(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        self.assertEqual(self.files.writes, [self.path_of("PROJ-2")])

    def test_people_never_reach_jira(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        asked = [call[2] for call in self.gateway.called("get_issue")]
        self.assertTrue(all("assignee" not in (fields or ()) and "reporter" not in (fields or ()) for fields in asked))


class RegressionCasesTest(Fixture):
    """Scenarios that each damaged a file or an issue until they were fixed."""

    def setUp(self):
        super().setUp()
        self.pull()
        self.files.writes.clear()
        self.gateway.calls.clear()

    def push(self, key="PROJ-2", **options):
        return self.app.push_mirror.execute(self.path_of(key), **options)

    def test_adopting_a_file_does_not_rewrite_its_type_or_its_epic(self):
        text = self.text_of("PROJ-2")
        stripped = "\n".join(line for line in text.split("\n") if not line.startswith(("jira_base:", "jira_synced:")))
        changed = stripped.replace("issuetype: Bug", "issuetype: Task").replace("epic_key: PROJ-9", "epic_key: PROJ-77")
        self.assertNotEqual(changed, stripped)
        self.files.files[self.path_of("PROJ-2")] = changed
        self.assertEqual(self.pull(keys=["PROJ-2"]).outcomes[0].action, "adopted")
        self.assertEqual([line for line in self.text_of("PROJ-2").split("\n") if not line.startswith(("jira_base:", "jira_synced:"))], changed.split("\n"))
        self.put("PROJ-2", summary="Changed there")
        self.pull(keys=["PROJ-2"], update=True)
        self.assertIn("issuetype: Task", self.text_of("PROJ-2"))  # an update brings in the changed field and leaves the other lines
        self.assertIn("epic_key: PROJ-77", self.text_of("PROJ-2"))

    def test_an_edit_of_nothing_but_the_indentation_is_not_lost_when_jira_changes_the_text(self):
        self.put("PROJ-2", description="h2. Goal\n\n{code}\n    indented\n{code}\n")
        self.pull(update=True)
        self.edit("PROJ-2", "    indented", "        indented")
        self.assertEqual(self.states()["PROJ-2"], "same")  # spacing alone is no difference to send
        before = self.text_of("PROJ-2")
        self.put("PROJ-2", description="h2. Goal\n\n{code}\n    indented\n{code}\n\nA new line.")
        result = self.pull(update=True)
        self.assertEqual((result.outcomes[1].action, self.text_of("PROJ-2")), ("conflict", before))

    def test_a_description_that_does_not_come_back_unchanged_is_not_sent_without_force(self):
        self.put("PROJ-2", description="h2. Goal\n\n{code:java|linenumbers=true}\nint a;\n{code}\n\nSecond paragraph.")
        self.pull(update=True)
        self.assertIn("lossy: changes on the way back", self.text_of("PROJ-2"))
        self.edit("PROJ-2", "Second paragraph.", "Second paragraph, fixed.")
        self.gateway.calls.clear()
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("cannot carry unchanged", result.first_error.message)
        self.assertIn("text that changes on the way back", result.first_error.message)
        self.assertEqual(self.updates(), [])
        self.push(force=True)
        self.assertEqual(len(self.updates()), 1)

    def test_a_mark_that_holds_code_or_a_link_survives_a_push_of_another_paragraph(self):
        self.put("PROJ-2", description="*Read the {{spec}} first* and see *[the guide|https://x.example/guide]*.\n\nSecond paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "Second paragraph.", "Second paragraph, fixed.")
        self.gateway.calls.clear()
        self.push()
        sent = self.updates()[0][2]["fields"]["description"]
        self.assertEqual(sent, "*Read the {{spec}} first* and see *[the guide|https://x.example/guide]*.\n\nSecond paragraph, fixed.")
        self.assertNotIn("\x00", sent)

    def test_a_nul_character_in_the_file_is_not_sent(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fix\x00 the thing")
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("NUL", result.first_error.message)
        self.assertEqual(self.updates(), [])

    def test_a_file_that_cannot_be_written_after_the_update_is_no_refusal(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.files.locked.add(self.path_of("PROJ-2"))
        result = self.push()
        outcome = result.outcomes[0]
        self.assertEqual((result.first_error, outcome.action), (None, "pushed"))
        self.assertIn("Jira holds the change", outcome.message)
        self.assertIn("locked by another program", outcome.message)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")
        self.files.locked.clear()
        self.assertEqual(self.states()["PROJ-2"], "same")
        self.assertEqual(self.pull().outcomes[1].action, "adopted")  # the next pull notes the state

    def test_pull_names_the_files_it_leaves_alone(self):
        self.edit("PROJ-3", "fields_json:", "fields_json: {broken ")
        self.files.files[f"{DIR}/copy.md"] = self.text_of("PROJ-2")
        result = self.pull(update=True)
        said = {outcome.key: outcome.message for outcome in result.outcomes if outcome.action == "skipped"}
        self.assertIn("fields_json", said["PROJ-3"])
        self.assertIn("copy.md", said["PROJ-2"])
        self.assertEqual(result.count("skipped"), 2)

    def test_a_summary_with_quotes_at_both_ends_survives_pull_edit_and_push(self):
        self.put("PROJ-2", summary='"Audit" trail for "users"')
        self.pull(update=True)
        self.assertEqual(self.states()["PROJ-2"], "same")
        self.edit("PROJ-2", 'for "users""', 'for "all users""')
        self.assertEqual(self.push().outcomes[0].payload, {"fields": {"summary": '"Audit" trail for "all users"'}})

    def test_story_points_written_as_text_are_read_and_a_word_is_refused_instead_of_clearing_the_field(self):
        self.edit("PROJ-2", '"customfield_10001":3.0', '"customfield_10001":"8"')
        self.push()
        self.assertEqual(self.updates()[0][2], {"fields": {"customfield_10001": 8.0}})
        self.edit("PROJ-2", '"customfield_10001":"8"', '"customfield_10001":"many"')
        self.assertEqual(self.states()["PROJ-2"], "unreadable")
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("story points", result.first_error.message)
        self.assertEqual(self.gateway.issues["PROJ-2"].story_points, 8.0)

    def test_a_component_with_a_comma_is_left_out_and_never_split(self):
        self.put("PROJ-2", components=["Data, Quality"])
        entry = next(entry for entry in self.diff().entries if entry.key == "PROJ-2")
        self.assertIn("components", entry.skipped)
        self.edit("PROJ-2", "components: Demo", "components: Demo,Backend")  # an edit of that very line is not sent either: it could not say the name
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        self.assertEqual(self.updates()[0][2], {"fields": {"summary": "Fixed here"}})
        self.assertEqual(self.gateway.issues["PROJ-2"].components, ["Data, Quality"])

    def test_a_label_that_holds_an_address_is_redacted_in_the_file_and_never_sent_back(self):
        self.put("PROJ-2", labels=["owner-a.person@example.test", "backend"])
        self.pull(update=True)
        self.assertIn("labels: [email],backend", self.text_of("PROJ-2"))
        self.assertNotIn("owner-a", self.text_of("PROJ-2"))
        self.edit("PROJ-2", "labels: [email],backend", "labels: [email],backend,api")
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("labels", result.first_error.message)
        self.assertEqual(self.updates(), [])
        self.assertEqual(self.gateway.issues["PROJ-2"].labels, ["owner-a.person@example.test", "backend"])

    def test_a_deeply_nested_fields_json_is_an_unreadable_file_and_not_a_crash(self):
        self.edit("PROJ-3", "fields_json:", "fields_json: " + "[" * 50_000 + " ")
        entry = next(entry for entry in self.diff().entries if entry.key == "PROJ-3")
        self.assertEqual(entry.state, "unreadable")

    def test_a_link_to_an_issue_does_not_leave_the_description_out_but_a_link_to_a_document_does(self):
        self.put("PROJ-2", description="See [the related story|PROJ-3] and `handlers[0](event)`.")
        self.pull(update=True)
        self.assertEqual(self.pull().outcomes[1].action, "unchanged")
        entry = next(entry for entry in self.diff().entries if entry.key == "PROJ-2")
        self.assertEqual(entry.skipped, {})
        self.put("PROJ-2", description="Something else entirely.")
        self.assertEqual(self.states()["PROJ-2"], "jira-changed")  # the description takes part
        self.files.files[self.path_of("PROJ-2")] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        entry = next(entry for entry in self.diff().entries if entry.key == "PROJ-2")
        self.assertIn("description", entry.skipped)
        self.assertIn("description", self.pull().skipped["PROJ-2"])

    def test_an_attached_image_does_not_take_the_description_out_of_the_sync(self):
        self.put("PROJ-2", description="Steps:\n\n!shot.png!\n\nThe dialog is wrong.")
        self.pull(update=True)
        self.assertIn("![](shot.png)", self.text_of("PROJ-2"))
        self.assertEqual(next(entry for entry in self.diff().entries if entry.key == "PROJ-2").skipped, {})
        self.put("PROJ-2", description="Steps:\n\n!shot.png!\n\nThe dialog is right now.")
        self.assertEqual(self.states()["PROJ-2"], "jira-changed")
        self.pull(update=True)
        self.assertIn("The dialog is right now.", self.text_of("PROJ-2"))

    def test_a_push_that_has_nothing_to_send_says_what_was_not_compared(self):
        self.files.files[self.path_of("PROJ-2")] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        message = self.push().outcomes[0].message
        self.assertEqual(message, "the file holds what Jira holds (not compared: description)")
        self.assertEqual(self.push("PROJ-3").outcomes[0].message, "the file holds what Jira holds")

    def test_an_edit_of_nothing_but_the_spacing_stays_an_edit_when_the_file_is_pulled(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "First paragraph.\n\nSecond paragraph.", "First paragraph.\nSecond paragraph.")
        first = self.pull().outcomes[1]
        self.assertEqual(first.action, "skipped")
        self.assertIn("spacing", first.message)
        self.assertEqual(self.pull().outcomes[1].action, "skipped")  # and it stays so
        before = self.text_of("PROJ-2")
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        result = self.pull(update=True)
        self.assertEqual((result.outcomes[1].action, self.text_of("PROJ-2")), ("conflict", before))

    def test_a_pull_that_brings_in_another_field_leaves_the_spacing_edit_an_edit(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "First paragraph.\n\nSecond paragraph.", "First paragraph.\nSecond paragraph.")
        self.put("PROJ-2", labels=["x"])
        self.assertEqual(self.pull(update=True).outcomes[1].action, "updated")
        self.assertIn("labels: x", self.text_of("PROJ-2"))
        outcome = self.pull().outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("skipped", ["description"]))
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.assertEqual(self.pull(update=True).outcomes[1].action, "conflict")

    def test_a_record_of_a_field_that_no_longer_takes_part_is_dropped_by_the_next_pull(self):
        self.assertIn("description=", self.text_of("PROJ-2").split("jira_base:")[1].split("\n")[0])
        path = self.path_of("PROJ-2")
        self.files.files[path] = self.files.files[path].replace("assignee:", "mirror: sanitized\nassignee:", 1)
        outcome = self.pull().outcomes[1]
        self.assertEqual(outcome.action, "adopted")
        self.assertEqual(outcome.fields, [])  # the field that left the sync is no edit of the spacing
        self.assertNotIn("spacing", outcome.message)
        self.assertNotIn("description=", self.text_of("PROJ-2").split("jira_base:")[1].split("\n")[0])
        self.assertEqual(self.pull().outcomes[1].action, "unchanged")

    def test_a_change_of_the_spacing_in_jira_does_not_turn_the_spacing_edit_here_into_an_agreement(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "Second paragraph.\n\nThird paragraph.", "Second paragraph.\nThird paragraph.")
        self.put("PROJ-2", description="First  paragraph.\n\nSecond paragraph.\n\nThird paragraph.")  # a blank in Jira, in another place
        self.assertEqual(self.states()["PROJ-2"], "same")
        first = self.pull().outcomes[1]
        self.assertEqual((first.action, first.fields), ("adopted", ["description"]))  # Jira's spacing is noted, the edit stays an edit
        self.files.writes.clear()
        self.assertEqual(self.pull().outcomes[1].action, "skipped")
        self.assertEqual(self.files.writes, [])  # and nothing is written on every run
        before = self.text_of("PROJ-2")
        self.put("PROJ-2", description="First  paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n\nFourth paragraph.")
        result = self.pull(update=True)
        self.assertEqual((result.outcomes[1].action, self.text_of("PROJ-2")), ("conflict", before))

    def test_after_a_spacing_change_in_jira_a_real_edit_here_is_no_conflict(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "Second paragraph.\n\nThird paragraph.", "Second paragraph.\nThird paragraph.")
        self.put("PROJ-2", description="First  paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull()  # Jira's new spacing is noted, so that it does not look like a change of Jira's text later
        self.edit("PROJ-2", "Third paragraph.", "Third paragraph, edited.")
        self.assertEqual(self.states()["PROJ-2"], "local-changed")
        sent = self.push().outcomes[0]
        self.assertEqual((sent.action, sent.fields), ("pushed", ["description"]))
        self.assertIn("Third paragraph, edited.", self.gateway.issues["PROJ-2"].description)

    def test_a_spacing_edit_with_the_spacing_in_jira_back_as_it_was_costs_one_write_and_is_still_an_edit(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "Second paragraph.\n\nThird paragraph.", "Second paragraph.\nThird paragraph.")
        original = self.gateway.issues["PROJ-2"].description
        self.put("PROJ-2", description="First  paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull()
        self.put("PROJ-2", description=original)
        self.files.writes.clear()
        self.assertEqual(self.pull().outcomes[1].action, "adopted")
        self.assertEqual(self.pull().outcomes[1].action, "skipped")
        self.assertEqual(len(self.files.writes), 1)
        self.put("PROJ-2", description=original + "\n\nFourth paragraph.")
        self.assertEqual(self.pull(update=True).outcomes[1].action, "conflict")

    def test_a_spacing_edit_that_jira_got_too_is_no_edit_any_more(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "Second paragraph.\n\nThird paragraph.", "Second paragraph.\nThird paragraph.")
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\nThird paragraph.")  # the same change, made in Jira
        outcome = self.pull().outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("adopted", []))
        self.assertEqual(self.pull().outcomes[1].action, "unchanged")
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\nThird paragraph.\n\nFourth paragraph.")
        self.assertEqual(self.states()["PROJ-2"], "jira-changed")  # the file holds nothing that Jira lacks
        self.assertEqual(self.pull(update=True).outcomes[1].action, "updated")

    def test_the_spacing_of_jira_copied_into_the_file_later_is_no_edit_either(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull(update=True)
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\nThird paragraph.")
        self.assertEqual(self.pull().outcomes[1].action, "adopted")  # Jira's spacing is noted
        self.edit("PROJ-2", "Second paragraph.\n\nThird paragraph.", "Second paragraph.\nThird paragraph.")  # and copied into the file
        outcome = self.pull().outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("adopted", []))
        self.assertEqual(self.pull().outcomes[1].action, "unchanged")
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\nThird paragraph.\n\nFourth paragraph.")
        self.assertEqual(self.pull(update=True).outcomes[1].action, "updated")

    def test_a_push_names_what_was_left_out_and_what_jira_changed_on_every_path(self):
        self.files.files[self.path_of("PROJ-2")] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        self.put("PROJ-2", summary="Changed there")
        self.edit("PROJ-2", "labels: backend", "labels: backend,api")
        both = "Jira changed summary meanwhile: `jira pull --update` brings it in; not compared: description"
        self.assertEqual(self.push(dry_run=True).outcomes[0].message, both)
        self.files.locked.add(self.path_of("PROJ-2"))
        message = self.push().outcomes[0].message
        self.assertTrue(message.startswith("Jira holds the change, but the file could not be brought up to date (locked by another program)"), message)
        self.assertTrue(message.endswith("; " + both), message)

    def test_a_field_that_this_jira_does_not_have_is_no_news_in_a_push(self):
        self.gateway.field_ids = lambda: {"story_points": None, "epic_link": "customfield_10002", "sprint": "customfield_10003", "acceptance": None}
        self.assertEqual(self.push().outcomes[0].message, "the file holds what Jira holds")
        self.edit("PROJ-2", "labels: backend", "labels: backend,api")
        self.assertEqual(self.push(dry_run=True).outcomes[0].message, "")
        self.files.files[self.path_of("PROJ-2")] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        self.assertEqual(self.push(dry_run=True).outcomes[0].message, "not compared: description")  # what the file made impossible is still said

    def test_a_file_that_holds_what_jira_held_when_they_last_agreed_has_no_edit_of_the_spacing(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\n\nThird paragraph.")
        self.pull(update=True)
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.\nThird paragraph.")
        self.assertEqual(self.pull().outcomes[1].action, "adopted")  # Jira's spacing is noted
        self.edit("PROJ-2", "Second paragraph.\n\nThird paragraph.", "Second paragraph.\nThird paragraph.")  # the file takes it over, no pull yet
        self.put("PROJ-2", description="First paragraph.\nSecond paragraph.\nThird paragraph.")  # and Jira moves on
        outcome = self.pull().outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("adopted", []))  # what the file holds is what Jira held: no edit of the spacing
        self.put("PROJ-2", description="First paragraph.\nSecond paragraph.\nThird paragraph.\n\nFourth paragraph.")
        self.assertEqual(self.pull(update=True).outcomes[1].action, "updated")

    def test_the_message_about_a_spacing_edit_says_how_to_get_rid_of_it(self):
        self.put("PROJ-2", description="First paragraph.\n\nSecond paragraph.")
        self.pull(update=True)
        self.edit("PROJ-2", "First paragraph.\n\nSecond paragraph.", "First paragraph.\nSecond paragraph.")
        message = self.pull().outcomes[1].message
        self.assertIn("put the spacing back", message)
        self.assertIn("`jira_base`", message)
        self.edit("PROJ-2", "First paragraph.\nSecond paragraph.", "First paragraph.\n\nSecond paragraph.")  # the first way out
        self.assertEqual(self.pull().outcomes[1].action, "unchanged")
        self.edit("PROJ-2", "First paragraph.\n\nSecond paragraph.", "First paragraph.\nSecond paragraph.")
        self.assertEqual(self.pull().outcomes[1].action, "skipped")
        path = self.path_of("PROJ-2")  # the second one: without the line the next pull notes the file as it is
        self.files.files[path] = "\n".join(line for line in self.files.files[path].split("\n") if not line.startswith("jira_base:"))
        self.assertEqual(self.pull().outcomes[1].action, "adopted")
        self.assertEqual(self.pull().outcomes[1].action, "unchanged")

    def test_a_push_that_sends_something_says_what_it_did_not_compare(self):
        self.files.files[self.path_of("PROJ-2")] += "\nSee [the ADR](../docs/adr/0001.md).\n"
        self.edit("PROJ-2", "labels: backend", "labels: backend,api")
        shown = self.push(dry_run=True).outcomes[0]
        self.assertEqual((shown.action, shown.fields, shown.message), ("pushed", ["labels"], "not compared: description"))
        sent = self.push().outcomes[0]
        self.assertEqual((sent.action, sent.fields, sent.message), ("pushed", ["labels"], "not compared: description"))
        self.edit("PROJ-3", "labels: backend", "labels: backend,api")
        self.assertEqual(self.push("PROJ-3").outcomes[0].message, "")  # nothing left out: nothing to say
        self.put("PROJ-2", summary="Changed there")
        self.edit("PROJ-2", "labels: backend,api", "labels: backend,api,x")
        self.assertEqual(self.push().outcomes[0].message, "Jira changed summary meanwhile: `jira pull --update` brings it in; not compared: description")

    def test_a_nul_in_the_name_of_a_label_is_not_sent(self):
        self.edit("PROJ-2", "labels: backend", "labels: backend,x\x00y")
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("NUL", result.first_error.message)
        self.assertEqual(self.updates(), [])


class LinksFixture(Fixture):
    """The issue links are one more field of the sync: the `links:` line, kept in step like the others, created in Jira by a push, never removed."""

    def setUp(self):
        super().setUp()
        self.gateway.link_types.append(LinkType("4", "Relates", "relates to", "relates to"))  # a type that reads the same from both ends

    def pending(self):
        """What is not in step: key -> state."""
        return {key: state for key, state in self.states().items() if state != "same"}

    def link(self, type_name, outward, inward):
        """Somebody links two issues in the web interface of Jira."""
        self.gateway.create_link(link_payload(type_name, outward, inward))
        self.gateway.calls.pop()

    def unlink(self, outward, inward):
        """Somebody removes a link there."""
        for key, other in ((outward, inward), (inward, outward)):
            self.put(key, links=[item for item in self.gateway.issues[key].links if item.other.key != other])

    def synced(self):
        """Every file pulled; what is sent from now on is what the test did."""
        self.pull()
        self.files.writes.clear()
        self.gateway.calls.clear()

    def bring_in(self):
        """What Jira holds now is in the files; what is sent from now on is what the test did."""
        self.pull(update=True)
        self.files.writes.clear()
        self.gateway.calls.clear()

    def line(self, key):
        return MirrorFile.parse(self.text_of(key)).get("links")

    def write_line(self, key, line):
        """The person adds the `links:` line to a file, or changes it."""
        if MirrorFile.parse(self.text_of(key)).has("links"):
            self.edit(key, f"links: {self.line(key)}".rstrip(), f"links: {line}")
        else:
            self.edit(key, "fix_versions: Release 1.0\n", f"fix_versions: Release 1.0\nlinks: {line}\n")

    def push(self, key="PROJ-2", **options):
        return self.app.push_mirror.execute(self.path_of(key), **options)

    def entry(self, key):
        return next(entry for entry in self.diff().entries if entry.key == key)

    def creations(self):
        return [call[1] for call in self.gateway.called("create_link")]


class LinksPullTest(LinksFixture):
    def test_a_new_file_lists_the_links_of_its_issue_from_its_side(self):
        self.link("Blocks", "PROJ-1", "PROJ-2")
        self.link("Has to be done after", "PROJ-3", "PROJ-1")
        self.pull()
        self.assertEqual(self.line("PROJ-1"), "Blocks -> PROJ-2, Has to be done after <- PROJ-3")  # PROJ-1 is the outward end of the first link and the inward end of the second
        self.assertEqual(self.line("PROJ-2"), "Blocks <- PROJ-1")
        self.assertEqual(self.line("PROJ-3"), "Has to be done after -> PROJ-1")
        lines = self.text_of("PROJ-1").split("\n")
        self.assertTrue(lines[lines.index("fix_versions: Release 1.0") + 1].startswith("links: "))
        self.assertEqual(self.pending(), {})  # everything is in step

    def test_a_file_of_an_issue_without_links_has_no_such_line_but_a_record(self):
        self.pull()
        for key in ("PROJ-1", "PROJ-4"):
            file = MirrorFile.parse(self.text_of(key))
            self.assertFalse(file.has("links"))
            self.assertIn("links", file.base)
        self.assertEqual(self.pull().count("unchanged"), 6)

    def test_what_is_written_holds_a_key_and_a_type_name_and_nothing_else(self):
        self.put("PROJ-2", summary="Ask [~jdoe] about the report", description="Mail jane.doe@example.com")
        self.link("Blocks", "PROJ-1", "PROJ-2")
        self.pull()
        self.assertEqual(self.line("PROJ-1"), "Blocks -> PROJ-2")
        for personal in ("jdoe", "Jane", "jane.doe"):
            self.assertNotIn(personal, "\n".join(self.files.files.values()))
        self.assertNotIn("about the report", self.text_of("PROJ-1"))  # the summary of the other issue is no part of the line

    def test_a_link_that_jira_gained_is_news_to_the_file_and_waits_for_update(self):
        self.synced()
        self.link("Blocks", "PROJ-1", "PROJ-2")
        entry = self.entry("PROJ-1")
        self.assertEqual((entry.state, entry.fields, entry.jira_fields, entry.local_fields), ("jira-changed", ["links"], ["links"], []))
        outcome = self.pull().outcomes[0]
        self.assertEqual((outcome.key, outcome.action, outcome.fields), ("PROJ-1", "skipped", ["links"]))
        self.assertIn("--update", outcome.message)
        self.assertEqual(self.files.writes, [])
        before = self.text_of("PROJ-1")
        self.assertEqual(self.pull(update=True, keys=["PROJ-1"]).outcomes[0].action, "updated")
        after = self.text_of("PROJ-1")
        self.assertEqual(self.line("PROJ-1"), "Blocks -> PROJ-2")
        gone, new = set(before.split("\n")) - set(after.split("\n")), set(after.split("\n")) - set(before.split("\n"))
        self.assertEqual((sorted(line.split(":")[0] for line in gone), sorted(line.split(":")[0] for line in new)), (["jira_base"], ["jira_base", "links"]))  # nothing else changed
        self.assertEqual(self.states()["PROJ-2"], "jira-changed")  # the other end of the link is news to its file too
        self.pull(update=True)
        self.assertEqual(self.pending(), {})

    def test_a_link_that_left_jira_leaves_the_line_of_a_file_that_was_not_edited(self):
        self.link("Blocks", "PROJ-1", "PROJ-2")
        self.synced()
        self.unlink("PROJ-1", "PROJ-2")
        self.assertEqual(self.entry("PROJ-1").jira_fields, ["links"])
        self.pull(update=True)
        self.assertEqual(self.line("PROJ-1"), "")
        self.assertTrue(MirrorFile.parse(self.text_of("PROJ-1")).has("links"))  # the line stays, blank
        self.assertEqual(self.pending(), {})

    def test_a_link_written_in_the_file_is_an_edit_that_no_pull_touches_not_even_with_force(self):
        self.synced()
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        entry = self.entry("PROJ-2")
        self.assertEqual((entry.state, entry.fields, entry.local_fields), ("local-changed", ["links"], ["links"]))
        before = self.text_of("PROJ-2")
        for options in ({"update": True}, {"update": True, "force": True}):
            outcome = self.pull(**options).outcomes[1]
            self.assertEqual((outcome.action, self.text_of("PROJ-2")), ("skipped", before))
            self.assertIn("jira push", outcome.message)
        self.assertEqual(self.files.writes, [])

    def test_the_same_links_changed_on_both_sides_are_a_conflict_that_force_decides_for_jira(self):
        self.synced()
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.link("Relates", "PROJ-2", "PROJ-3")
        self.assertEqual(self.entry("PROJ-2").state, "conflict")
        before = self.text_of("PROJ-2")
        result = self.pull(update=True)
        self.assertEqual((result.outcomes[1].action, result.outcomes[1].fields, self.text_of("PROJ-2")), ("conflict", ["links"], before))
        self.assertEqual((result.first_error.exit_code, result.first_error.details), (3, {"keys": ["PROJ-2"]}))
        self.assertEqual(self.pull(update=True, force=True).first_error, None)
        self.assertEqual(self.line("PROJ-2"), "Relates -> PROJ-3")  # Jira's links: the entry that was written here and not pushed is gone
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_an_edit_of_another_field_stays_when_a_link_comes_in(self):
        self.synced()
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.link("Blocks", "PROJ-2", "PROJ-4")
        entry = self.entry("PROJ-2")
        self.assertEqual((entry.state, entry.local_fields, entry.jira_fields), ("mixed", ["summary"], ["links"]))
        outcome = self.pull(update=True).outcomes[1]
        self.assertEqual((outcome.action, outcome.fields), ("updated", ["links"]))
        self.assertIn("your edit of summary stays", outcome.message)
        self.assertIn("summary: Fixed here", self.text_of("PROJ-2"))
        self.assertEqual(self.line("PROJ-2"), "Blocks -> PROJ-4")

    def test_a_dry_run_of_an_update_writes_no_link(self):
        self.synced()
        self.link("Blocks", "PROJ-1", "PROJ-2")
        outcome = self.pull(update=True, dry_run=True).outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, outcome.dry_run, self.files.writes), ("updated", ["links"], True, []))
        self.assertFalse(MirrorFile.parse(self.text_of("PROJ-1")).has("links"))

    def test_the_links_of_two_issues_are_both_written_by_one_pull(self):
        self.synced()
        self.link("Blocks", "PROJ-1", "PROJ-2")
        result = self.pull(update=True)
        self.assertEqual(sorted((outcome.key, outcome.action) for outcome in result.outcomes if outcome.action != "unchanged"), [("PROJ-1", "updated"), ("PROJ-2", "updated")])

    def test_a_link_type_that_a_line_cannot_hold_is_left_out_and_said(self):
        self.put("PROJ-1", links=[IssueLink("Data, Quality", "relates to", "outward", IssueRef("PROJ-2"))])
        self.pull()
        self.assertFalse(MirrorFile.parse(self.text_of("PROJ-1")).has("links"))
        entry = self.entry("PROJ-1")
        self.assertEqual((entry.state, entry.fields), ("same", []))  # the links of that issue are not compared
        self.assertIn("comma", entry.skipped["links"])
        self.assertIn("comma", self.pull().skipped["PROJ-1"]["links"])
        self.assertNotIn("links", MirrorFile.parse(self.text_of("PROJ-1")).base)  # no record of what is not compared
        self.assertIn("links", MirrorFile.parse(self.text_of("PROJ-2")).base)


class LinksDiffTest(LinksFixture):
    def test_a_link_only_the_file_lists_is_edited_here_and_a_link_only_jira_holds_is_changed_there(self):
        self.synced()
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.link("Relates", "PROJ-1", "PROJ-3")
        entries = {entry.key: entry for entry in self.diff().entries}
        self.assertEqual((entries["PROJ-2"].state, entries["PROJ-2"].local_fields), ("local-changed", ["links"]))
        self.assertEqual((entries["PROJ-1"].state, entries["PROJ-1"].jira_fields), ("jira-changed", ["links"]))
        self.assertEqual((entries["PROJ-3"].state, entries["PROJ-3"].jira_fields), ("jira-changed", ["links"]))
        self.assertEqual(entries["PROJ-4"].state, "same")  # its file lists nothing, and Jira has not heard of the link that was written into the other file

    def test_a_link_in_both_places_is_in_step_whatever_its_spelling(self):
        self.synced()
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.write_line("PROJ-2", "blocks->proj-4")
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_the_end_of_a_link_is_part_of_it(self):
        self.synced()
        self.link("Relates", "PROJ-2", "PROJ-4")
        self.write_line("PROJ-2", "Relates <- PROJ-4")
        entry = self.entry("PROJ-2")
        self.assertEqual((entry.state, entry.conflicts), ("conflict", ["links"]))  # the file lists the link the other way round, and Jira has changed too

    def test_a_diff_that_reads_links_changes_nothing(self):
        self.synced()
        self.link("Blocks", "PROJ-1", "PROJ-2")
        self.write_line("PROJ-3", "Blocks -> PROJ-4")
        before = dict(self.files.files)
        self.diff()
        self.assertEqual((self.files.files, self.files.writes), (before, []))
        self.assertEqual([call for call in self.gateway.calls if call[0] in ("create_link", "update_issue")], [])

    def test_the_links_are_asked_for_with_the_other_fields(self):
        self.synced()
        self.diff()
        asked = self.gateway.called("search")
        self.assertTrue(asked and all("issuelinks" in call[2] for call in asked))

    def test_a_line_that_cannot_be_read_makes_that_file_unreadable_and_no_other(self):
        self.synced()
        self.write_line("PROJ-2", "Blocks > PROJ-4")
        entries = {entry.key: entry for entry in self.diff().entries}
        self.assertEqual(self.pending(), {"PROJ-2": "unreadable"})
        self.assertIn("links line", entries["PROJ-2"].message)
        outcome = next(item for item in self.pull(update=True).outcomes if item.key == "PROJ-2")
        self.assertEqual(outcome.action, "skipped")
        self.assertIn("Type -> KEY", outcome.message)

    def test_a_file_that_links_its_issue_to_itself_is_unreadable(self):
        self.synced()
        self.write_line("PROJ-2", "Blocks -> PROJ-2")
        self.assertIn("to itself", self.entry("PROJ-2").message)


class LinksPushTest(LinksFixture):
    def setUp(self):
        super().setUp()
        self.synced()

    def test_a_dry_run_shows_the_requests_and_sends_nothing(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4, Has to be done after <- PROJ-3")
        outcome = self.push(dry_run=True).outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, outcome.payload, outcome.dry_run), ("pushed", ["links"], None, True))
        self.assertEqual([link.entry for link in outcome.links], ["Blocks -> PROJ-4", "Has to be done after <- PROJ-3"])
        self.assertEqual([link.payload for link in outcome.links], [link_payload("Blocks", "PROJ-2", "PROJ-4"), link_payload("Has to be done after", "PROJ-3", "PROJ-2")])
        self.assertEqual([link.relation for link in outcome.links], ["PROJ-2 blocks PROJ-4", "PROJ-2 requires PROJ-3"])
        self.assertEqual((self.creations(), self.updates(), self.files.writes), ([], [], []))
        self.assertEqual(self.gateway.called("get_issue")[-1][1:], ("PROJ-3", ("summary",)))  # the other issues were looked at: they exist

    def test_a_push_creates_the_link_reads_the_issue_back_and_notes_the_agreement(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        result = self.push()
        outcome = result.outcomes[0]
        self.assertEqual((result.first_error, outcome.action, outcome.fields, outcome.payload, outcome.dry_run), (None, "pushed", ["links"], None, False))
        self.assertEqual(self.creations(), [link_payload("Blocks", "PROJ-2", "PROJ-4")])
        self.assertEqual(self.updates(), [])  # no field was edited: nothing is changed in the issue itself
        self.assertEqual([(item.type_name, item.direction, item.other.key) for item in self.gateway.issues["PROJ-2"].links], [("Blocks", "outward", "PROJ-4")])
        self.assertEqual([(item.type_name, item.direction, item.other.key) for item in self.gateway.issues["PROJ-4"].links], [("Blocks", "inward", "PROJ-2")])
        self.assertEqual(self.gateway.called("get_issue")[-1][1], "PROJ-2")  # read back
        self.assertEqual(self.files.writes, [self.path_of("PROJ-2")])
        self.assertEqual(self.entry("PROJ-4").state, "jira-changed")  # the file of the other end is news
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_second_push_creates_nothing_and_a_link_is_never_created_twice(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.push()
        again = self.push()
        self.assertEqual((again.outcomes[0].action, again.outcomes[0].links), ("unchanged", []))
        self.assertEqual(len(self.creations()), 1)

    def test_the_end_of_the_issue_decides_which_issue_is_the_outward_one(self):
        self.write_line("PROJ-2", "Blocks <- PROJ-4")
        self.push()
        self.assertEqual(self.creations(), [{"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-4"}, "inwardIssue": {"key": "PROJ-2"}}])
        self.assertEqual(self.gateway.issues["PROJ-2"].links[0].direction, "inward")

    def test_a_link_that_jira_holds_already_is_not_created_whatever_its_spelling(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.write_line("PROJ-2", "blocks -> proj-4, Relates -> PROJ-3")
        result = self.push()
        self.assertEqual(self.creations(), [link_payload("Relates", "PROJ-2", "PROJ-3")])
        self.assertEqual(result.outcomes[0].fields, ["links"])
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_link_that_jira_gained_since_is_not_created_a_second_time_either(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4, Relates -> PROJ-3")
        self.link("Blocks", "PROJ-2", "PROJ-4")  # made in Jira after the file was edited: both sides changed the links
        self.assertEqual(self.push().first_error.exit_code, 3)
        self.assertEqual(self.creations(), [])
        self.push(force=True)
        self.assertEqual(self.creations(), [link_payload("Relates", "PROJ-2", "PROJ-3")])
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_file_that_lists_what_jira_holds_has_nothing_to_send(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        outcome = self.push().outcomes[0]
        self.assertEqual(outcome.action, "unchanged")
        self.assertEqual((self.creations(), self.updates()), ([], []))

    def test_the_same_link_written_into_both_files_is_created_once(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.write_line("PROJ-4", "Blocks <- PROJ-2")
        self.push("PROJ-2")
        self.assertEqual(self.states()["PROJ-4"], "same")  # Jira holds what that file lists, whoever created it
        again = self.push("PROJ-4").outcomes[0]
        self.assertEqual((again.action, again.links), ("unchanged", []))
        self.assertEqual(len(self.creations()), 1)
        self.assertEqual(self.pull().outcomes[3].action, "adopted")  # the next pull notes the agreement of the file that sent nothing
        self.assertEqual(self.pending(), {})

    def test_a_file_that_lost_the_whole_line_names_what_jira_still_holds(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.edit("PROJ-2", "links: Blocks -> PROJ-4\n", "")  # the line is gone, not blank
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.action, outcome.jira_only_links), ("unchanged", ["Blocks -> PROJ-4"]))
        self.assertEqual(self.creations(), [])

    def test_a_file_whose_links_do_not_differ_does_not_ask_jira_for_anything_about_links(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        self.assertEqual((self.creations(), self.gateway.called("list_link_types")), ([], []))
        self.assertEqual([call[1] for call in self.gateway.called("get_issue")], ["PROJ-2", "PROJ-2", "PROJ-2"])  # the read, the guard and the read back

    def test_both_ends_of_one_relation_are_two_links(self):
        self.link("Relates", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.write_line("PROJ-2", "Relates -> PROJ-4, Relates <- PROJ-4")
        outcome = self.push().outcomes[0]
        self.assertEqual(outcome.action, "pushed")
        self.assertEqual(self.creations(), [link_payload("Relates", "PROJ-4", "PROJ-2")])  # Jira stores a link with a direction

    def test_an_unknown_type_is_refused_with_the_names_that_exist_and_nothing_is_sent(self):
        self.write_line("PROJ-2", "Blokcs -> PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")  # the whole push is refused, the summary too
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("Blokcs", result.first_error.message)
        self.assertIn("PROJ-2-fix-the-thing.md", result.first_error.message)
        self.assertIn("Available: Blocks, Has to be done after, Pre-requisite", result.first_error.hint)
        self.assertEqual((result.outcomes[0].action, self.creations(), self.updates(), self.files.writes), ("refused", [], [], []))
        self.assertEqual(self.push(dry_run=True).first_error.message, result.first_error.message)  # a dry run finds it too

    def test_a_phrase_is_no_type(self):
        self.write_line("PROJ-2", "blocks -> PROJ-4")  # the name is Blocks, which is also the phrase: fine
        self.assertIsNone(self.push(dry_run=True).first_error)
        self.write_line("PROJ-2", "is blocked by -> PROJ-4")
        self.assertIsInstance(self.push(dry_run=True).first_error, ValidationError)

    def test_a_type_name_with_umlauts_goes_through_the_file_and_is_found_whatever_its_case(self):
        self.gateway.link_types.append(LinkType("5", "Abhängig von", "hängt ab von", "ist Voraussetzung für"))
        self.write_line("PROJ-2", "ABHÄNGIG VON -> PROJ-4")
        self.assertEqual(self.push().first_error, None)
        self.assertEqual(self.creations(), [link_payload("Abhängig von", "PROJ-2", "PROJ-4")])
        self.pull(update=True)
        self.assertEqual(self.line("PROJ-4"), "Abhängig von <- PROJ-2")  # Jira's spelling comes back into the file
        self.assertEqual(self.states()["PROJ-4"], "same")

    def test_an_unknown_issue_is_refused_with_not_found_and_nothing_is_sent(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-77")
        for dry_run in (True, False):
            result = self.push(dry_run=dry_run)
            self.assertIsInstance(result.first_error, NotFoundError)
            self.assertEqual(result.first_error.exit_code, 4)
            self.assertIn("PROJ-77 does not exist", result.first_error.message)
        self.assertEqual((self.creations(), self.files.writes), ([], []))

    def test_the_fields_go_first_with_their_guard_and_the_links_after_them(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.fields, outcome.payload), (["summary", "links"], {"fields": {"summary": "Fixed here"}}))
        names = [call[0] for call in self.gateway.calls if call[0] in ("update_issue", "create_link")]
        self.assertEqual(names, ["update_issue", "create_link"])
        self.assertEqual(self.updates(), [("update_issue", "PROJ-2", {"fields": {"summary": "Fixed here"}})])
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_change_of_the_issue_since_it_was_read_stops_the_fields_and_the_links(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        real = self.gateway.get_issue

        def racing(key, *, fields=None):
            issue = real(key, fields=fields)
            if fields is not None and tuple(fields) == ("summary", "updated"):
                return dataclasses.replace(issue, updated="2026-10-25T09:00:00.000+0200")
            return issue

        self.gateway.get_issue = racing
        result = self.push()
        self.assertEqual((result.first_error.exit_code, self.updates(), self.creations()), (3, [], []))

    def test_a_dry_run_with_a_summary_and_a_link_shows_both_requests(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        outcome = self.push(dry_run=True).outcomes[0]
        self.assertEqual((outcome.fields, outcome.payload, len(outcome.links)), (["summary", "links"], {"fields": {"summary": "Fixed here"}}, 1))

    def test_a_link_that_the_file_no_longer_lists_is_named_and_not_removed(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.link("Relates", "PROJ-3", "PROJ-2")
        self.bring_in()
        self.assertEqual(self.line("PROJ-2"), "Blocks -> PROJ-4, Relates <- PROJ-3")
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, outcome.links, outcome.jira_only_links), ("unchanged", ["links"], [], ["Relates <- PROJ-3"]))
        self.assertIn("Relates <- PROJ-3: jira has it; remove it in Jira", outcome.message)
        self.assertEqual((self.creations(), self.updates(), self.files.writes), ([], [], []))
        self.assertEqual(len(self.gateway.issues["PROJ-2"].links), 2)  # still there

    def test_that_is_no_conflict_and_the_other_fields_go_with_it(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.write_line("PROJ-2", "")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        result = self.push()
        outcome = result.outcomes[0]
        self.assertEqual((result.first_error, outcome.action, outcome.fields, outcome.payload), (None, "pushed", ["summary"], {"fields": {"summary": "Fixed here"}}))
        self.assertEqual(outcome.jira_only_links, ["Blocks -> PROJ-4"])
        self.assertIn("jira has it; remove it in Jira", outcome.message)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")
        self.assertEqual(self.creations(), [])

    def test_what_was_removed_stays_a_difference_until_it_is_removed_in_jira(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.write_line("PROJ-2", "")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        entry = self.entry("PROJ-2")
        self.assertEqual((entry.state, entry.fields, entry.local_fields), ("local-changed", ["links"], ["links"]))  # the summary is agreed, the link is not
        again = self.push().outcomes[0]
        self.assertEqual((again.action, again.jira_only_links), ("unchanged", ["Blocks -> PROJ-4"]))  # and it is said every time
        self.unlink("PROJ-2", "PROJ-4")  # removed in Jira, where only a person can do it
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_removed_link_that_jira_still_holds_is_not_put_back_by_a_pull(self):
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.synced()
        self.pull(update=True)
        self.write_line("PROJ-2", "")
        before = self.text_of("PROJ-2")
        self.assertEqual(self.pull(update=True, force=True).outcomes[1].action, "skipped")
        self.assertEqual(self.text_of("PROJ-2"), before)

    def test_force_in_a_conflict_creates_what_the_file_lists_and_names_what_only_jira_holds(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.link("Relates", "PROJ-2", "PROJ-3")
        refused = self.push()
        self.assertEqual((refused.first_error.exit_code, refused.outcomes[0].action, self.creations()), (3, "refused", []))
        self.assertIn("links", refused.first_error.message)
        forced = self.push(force=True).outcomes[0]
        self.assertEqual((forced.action, forced.fields, forced.jira_only_links), ("pushed", ["links"], ["Relates -> PROJ-3"]))
        self.assertEqual(self.creations(), [link_payload("Blocks", "PROJ-2", "PROJ-4")])

    def test_links_that_jira_gained_are_not_reverted_and_an_edit_of_another_field_goes(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.link("Blocks", "PROJ-2", "PROJ-4")
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, self.creations()), ("pushed", ["summary"], []))
        self.assertIn("Jira changed links meanwhile", outcome.message)

    def test_a_link_that_is_to_be_created_names_its_trouble_when_jira_refuses_the_permission(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.gateway.errors["create_link"] = AuthError("Jira refused this request (HTTP 403).", hint="Ask a Jira administrator.", details={"status": 403})
        result = self.push()
        self.assertIsInstance(result.first_error, AuthError)
        self.assertEqual(result.first_error.exit_code, 5)
        self.assertIn("PROJ-2 blocks PROJ-4", result.first_error.message)
        self.assertIn("'Link Issues' permission", result.first_error.hint)
        self.assertEqual((result.outcomes[0].action, self.files.writes), ("refused", []))

    def test_a_401_on_the_link_is_a_missing_permission_too_as_the_reference_says(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.gateway.errors["create_link"] = AuthError("Jira did not accept the token (HTTP 401).", hint="Create a new token.", details={"status": 401})
        result = self.push()
        self.assertEqual((result.first_error.exit_code, result.outcomes[0].action), (5, "refused"))
        self.assertIn("'Link Issues' permission", result.first_error.hint)
        self.assertEqual(result.first_error.details["link"], "Blocks -> PROJ-4")

    def test_a_404_on_the_link_says_that_linking_may_be_disabled_or_an_issue_not_visible(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.gateway.errors["create_link"] = NotFoundError("Not found: POST /issueLink", details={"status": 404})
        result = self.push()
        self.assertEqual((result.first_error.exit_code, result.outcomes[0].action), (4, "refused"))
        self.assertIn("PROJ-2 blocks PROJ-4", result.first_error.message)
        self.assertIn("disabled", result.first_error.hint)

    def test_a_jira_without_link_types_says_that_linking_seems_disabled_and_nothing_is_sent(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.gateway.errors["list_link_types"] = NotFoundError("Not found: GET /issueLinkType", details={"status": 404})
        for dry_run in (True, False):
            result = self.push(dry_run=dry_run)
            self.assertEqual(result.first_error.exit_code, 4)
            self.assertIn("issue linking seems to be disabled", result.first_error.message)
        self.assertEqual((self.creations(), self.files.writes), ([], []))

    def test_the_types_are_asked_for_only_when_a_link_is_to_be_created(self):
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        self.push()
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.bring_in()
        self.write_line("PROJ-2", "")
        self.push()
        self.assertEqual(self.gateway.called("list_link_types"), [])

    def test_a_link_that_fails_after_another_went_through_ends_the_run_and_the_next_push_does_the_rest(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4, Relates -> PROJ-3")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        real, calls = self.gateway.create_link, []

        def failing_second(payload):
            calls.append(payload)
            if len(calls) == 2:
                raise RejectedError("Jira refused the request (HTTP 400): no.", hint="No.")
            real(payload)

        self.gateway.create_link = failing_second
        result = self.push()
        outcome = result.outcomes[0]
        self.assertIsInstance(result.first_error, RejectedError)
        self.assertEqual((outcome.action, outcome.fields, [link.entry for link in outcome.links]), ("pushed", ["summary", "links"], ["Blocks -> PROJ-4"]))
        self.assertIn("links not created (Relates -> PROJ-3): Jira refused the request", outcome.message)
        self.assertEqual(self.gateway.issues["PROJ-2"].summary, "Fixed here")
        self.assertEqual([item.other.key for item in self.gateway.issues["PROJ-2"].links], ["PROJ-4"])
        self.assertEqual(self.entry("PROJ-2").fields, ["links"])  # the summary is agreed, the links wait
        self.gateway.create_link = real
        self.gateway.calls.clear()
        self.assertEqual(self.push().first_error, None)
        self.assertEqual(self.creations(), [link_payload("Relates", "PROJ-2", "PROJ-3")])  # only what is missing: nothing is created twice
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_failure_before_anything_went_through_is_a_refusal(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.gateway.errors["create_link"] = RejectedError("Jira refused the request (HTTP 400): no.", hint="No.")
        result = self.push()
        self.assertEqual((result.outcomes[0].action, result.first_error.exit_code, self.files.writes), ("refused", 6, []))

    def test_a_file_that_cannot_be_written_after_the_link_is_no_refusal(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.files.locked.add(self.path_of("PROJ-2"))
        outcome = self.push().outcomes[0]
        self.assertEqual((outcome.action, outcome.fields), ("pushed", ["links"]))
        self.assertIn("Jira holds the change", outcome.message)
        self.files.locked.clear()
        self.assertEqual(self.pull().outcomes[1].action, "adopted")  # the next pull notes the state
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_several_files_push_their_links_one_after_the_other_and_stop_at_the_first_refusal(self):
        self.write_line("PROJ-1", "Blocks -> PROJ-3")
        self.write_line("PROJ-2", "Blokcs -> PROJ-3")
        self.write_line("PROJ-4", "Blocks -> PROJ-3")
        result = self.app.push_mirror.execute(DIR, keys=["PROJ-1", "PROJ-2", "PROJ-4"])
        self.assertEqual([outcome.action for outcome in result.outcomes], ["pushed", "refused"])
        self.assertEqual(self.creations(), [link_payload("Blocks", "PROJ-1", "PROJ-3")])

    def test_the_file_is_the_only_thing_written_and_no_person_is_asked_for(self):
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.push()
        asked = [call[2] for call in self.gateway.called("get_issue")]
        self.assertTrue(all("assignee" not in (fields or ()) and "reporter" not in (fields or ()) for fields in asked))
        self.assertEqual(self.files.writes, [self.path_of("PROJ-2")])

    def test_a_line_that_cannot_be_read_is_refused_and_nothing_is_sent(self):
        self.write_line("PROJ-2", "Blocks > PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        result = self.push()
        self.assertIsInstance(result.first_error, ValidationError)
        self.assertIn("links line", result.first_error.message)
        self.assertEqual((result.outcomes[0].action, self.updates(), self.creations()), ("refused", [], []))

    def test_a_link_type_that_a_line_cannot_hold_is_named_as_not_compared(self):
        self.put("PROJ-3", links=[IssueLink("Data, Quality", "relates to", "outward", IssueRef("PROJ-4"))])
        self.edit("PROJ-3", "labels: backend", "labels: backend,api")
        outcome = self.push("PROJ-3").outcomes[0]
        self.assertEqual((outcome.action, outcome.fields, outcome.message), ("pushed", ["labels"], "not compared: links"))
        self.assertEqual(self.creations(), [])


class LinksFromVersion1Test(LinksFixture):
    """A file that `jira pull` of version 1.0.0 wrote: its record has no entry for the links, and the file has no line for them."""

    def strip_record(self, key):
        path = self.path_of(key)
        file = MirrorFile.parse(self.files.files[path])
        file.remove("links")
        kept = {name: pair for name, pair in (file.base or {}).items() if name != "links"}
        file.set("jira_base", ",".join(f"{name}={own}/{theirs}" for name, (own, theirs) in kept.items()))
        self.files.files[path] = file.render()

    def old_files(self, *keys):
        self.pull()
        for key in keys:
            self.strip_record(key)
        self.files.writes.clear()
        self.gateway.calls.clear()

    def test_the_first_pull_of_a_file_in_step_only_records_the_agreement(self):
        self.old_files("PROJ-1", "PROJ-2")
        self.assertNotIn("links", MirrorFile.parse(self.text_of("PROJ-2")).base)
        self.assertEqual(self.pending(), {})
        before = self.text_of("PROJ-2")
        outcome = self.pull(keys=["PROJ-2"]).outcomes[0]
        self.assertEqual((outcome.action, outcome.fields), ("adopted", []))
        after = self.text_of("PROJ-2")
        self.assertIn("links", MirrorFile.parse(after).base)
        self.assertEqual([line for line in after.split("\n") if not line.startswith(("jira_base:", "jira_synced:"))], [line for line in before.split("\n") if not line.startswith(("jira_base:", "jira_synced:"))])
        self.assertEqual(self.pull(keys=["PROJ-2"]).outcomes[0].action, "unchanged")

    def test_a_dry_run_of_that_first_pull_writes_nothing(self):
        self.old_files("PROJ-2")
        self.assertEqual(self.pull(keys=["PROJ-2"], dry_run=True).outcomes[0].action, "adopted")
        self.assertEqual(self.files.writes, [])

    def test_the_links_that_jira_holds_are_news_to_such_a_file_and_update_writes_them(self):
        self.old_files("PROJ-2")
        self.link("Blocks", "PROJ-2", "PROJ-4")
        entry = self.entry("PROJ-2")
        self.assertEqual((entry.state, entry.jira_fields, entry.unknown), ("jira-changed", ["links"], []))  # no `differs`: nobody agreed on what did not exist
        self.assertEqual(self.pull().outcomes[1].action, "skipped")
        self.assertEqual(self.pull(update=True).outcomes[1].action, "updated")
        self.assertEqual(self.line("PROJ-2"), "Blocks -> PROJ-4")
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_push_of_another_field_works_whatever_jira_holds(self):
        self.old_files("PROJ-2")
        self.link("Blocks", "PROJ-2", "PROJ-4")
        self.edit("PROJ-2", "summary: Fix the thing", "summary: Fixed here")
        result = self.push()
        outcome = result.outcomes[0]
        self.assertEqual((result.first_error, outcome.action, outcome.fields), (None, "pushed", ["summary"]))
        self.assertIn("Jira changed links meanwhile", outcome.message)
        self.assertEqual(self.creations(), [])

    def test_a_link_written_into_such_a_file_is_pushed_without_force(self):
        self.old_files("PROJ-2")
        self.write_line("PROJ-2", "Blocks -> PROJ-4")
        self.assertEqual(self.entry("PROJ-2").state, "local-changed")
        result = self.push()
        self.assertEqual((result.first_error, result.outcomes[0].action), (None, "pushed"))
        self.assertEqual(self.creations(), [link_payload("Blocks", "PROJ-2", "PROJ-4")])
        self.assertIn("links", MirrorFile.parse(self.text_of("PROJ-2")).base)
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_two_links_written_into_such_a_file_of_which_one_fails_leave_the_other_to_the_next_push(self):
        self.old_files("PROJ-2")
        self.write_line("PROJ-2", "Blocks -> PROJ-4, Has to be done after -> PROJ-3")
        real, calls = self.gateway.create_link, []

        def failing_second(payload):
            calls.append(payload)
            if len(calls) == 2:
                raise RejectedError("Jira refused the request (HTTP 400): no.", hint="No.")
            real(payload)

        self.gateway.create_link = failing_second
        self.assertIsInstance(self.push().first_error, RejectedError)
        self.assertEqual(self.entry("PROJ-2").state, "local-changed")  # an edit that is half done, not a conflict
        self.gateway.create_link = real
        self.gateway.calls.clear()
        self.assertIsNone(self.push().first_error)
        self.assertEqual(self.creations(), [link_payload("Has to be done after", "PROJ-2", "PROJ-3")])
        self.assertEqual(self.states()["PROJ-2"], "same")

    def test_a_file_without_any_record_is_judged_the_same_for_its_links(self):
        self.pull()
        path = self.path_of("PROJ-2")
        self.files.files[path] = "\n".join(line for line in self.files.files[path].split("\n") if not line.startswith(("jira_base:", "jira_synced:")))
        self.link("Blocks", "PROJ-2", "PROJ-4")
        entry = self.entry("PROJ-2")
        self.assertEqual((entry.state, entry.jira_fields, entry.unknown), ("jira-changed", ["links"], []))

    def test_a_planning_mirror_file_that_lists_no_links_is_in_step_for_an_issue_that_has_none(self):
        self.put("PROJ-4", summary="Schedule the report", labels=["reporting"], priority="Very High", components=["Reporting"], fix_versions=["Release 1.0"], story_points=5.0, acceptance="1. *GIVEN* a user.", description="h2. User story\n\nAs an admin.\n")
        self.files.files[f"{DIR}/032-story.md"] = (
            "---\nsource_id: 32\nproject:\nissuetype: Story\nsummary: Schedule the report\nlabels: reporting\npriority: Very High\n"
            "components: Reporting\nfix_versions: Release 1.0\nassignee:\njira_key: PROJ-4\n"
            'fields_json: {"customfield_10001":5.0,"customfield_10004":"1. *GIVEN* a user."}\n---\n\n## User story\n\nAs an admin.\n'
        )
        self.assertEqual(self.states(keys=["PROJ-4"]), {"PROJ-4": "same"})
        self.assertEqual(self.pull(keys=["PROJ-4"]).outcomes[0].action, "adopted")
        self.assertIn("links", MirrorFile.parse(self.files.files[f"{DIR}/032-story.md"]).base)


class LinksOfTheFirstPullTest(LinksFixture):
    def test_the_first_pull_writes_the_links_and_the_second_has_nothing_to_do(self):
        self.link("Blocks", "PROJ-1", "PROJ-2")
        self.pull()
        self.files.writes.clear()
        result = self.pull()
        self.assertEqual((result.count("unchanged"), self.files.writes), (6, []))

    def test_a_dry_run_of_the_first_pull_writes_no_line(self):
        self.link("Blocks", "PROJ-1", "PROJ-2")
        result = self.pull(dry_run=True)
        self.assertEqual((result.count("created"), self.files.files), (6, {}))


if __name__ == "__main__":
    unittest.main()
