"""A folder of Markdown tickets in step with Jira: diff, pull and push, with the states a file can be in."""
import dataclasses
import re
import unittest

from jira_dc_cli.domain.errors import ConflictError, NotFoundError, UnavailableError, UsageError, ValidationError
from jira_dc_cli.domain.mirror import MirrorFile
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


class ReviewedCasesTest(Fixture):
    """Scenarios that an independent review found: each case damaged a file or an issue until it was fixed."""

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


if __name__ == "__main__":
    unittest.main()
