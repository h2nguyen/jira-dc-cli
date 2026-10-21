"""Use cases that change Jira: transition, comment, link, update, create and the imports."""
import dataclasses
import unittest

from jira_dc_cli.domain.drafts import IssueChanges, IssueDraft
from jira_dc_cli.domain.errors import ConfigError, ConflictError, NotFoundError, ValidationError
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, MemoryFiles, RecordingDiagnostics, build_app


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class TransitionTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_without_a_target_it_lists_the_options(self):
        result = self.app.transition_issue.execute("PROJ-2")
        self.assertEqual((result.current_status, [t.id for t in result.options]), ("In Progress", ["111", "81"]))
        self.assertIsNone(result.chosen)
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_moves_the_issue(self):
        result = self.app.transition_issue.execute("PROJ-2", to="done", comment=" Shipped. ")
        self.assertTrue(result.executed)
        self.assertEqual(
            self.gateway.called("transition_issue")[0][1:],
            ("PROJ-2", {"transition": {"id": "81"}, "update": {"comment": [{"add": {"body": "Shipped."}}]}}),
        )
        self.assertEqual(self.gateway.issues["PROJ-2"].status.name, "Done")
        self.assertEqual(result.payload["update"]["comment"][0]["add"]["body"], "Shipped.")

    def test_dry_run_changes_nothing(self):
        result = self.app.transition_issue.execute("PROJ-2", to="Done", dry_run=True)
        self.assertTrue(result.dry_run and not result.executed)
        self.assertEqual(self.gateway.called("transition_issue"), [])
        self.assertEqual(self.gateway.issues["PROJ-2"].status.name, "In Progress")

    def test_a_comment_needs_a_target(self):
        with self.assertRaises(ValidationError):
            self.app.transition_issue.execute("PROJ-2", comment="x")

    def test_unknown_target(self):
        with self.assertRaises(ValidationError):
            self.app.transition_issue.execute("PROJ-2", to="Archived")

    def test_unknown_issue(self):
        with self.assertRaises(NotFoundError):
            self.app.transition_issue.execute("PROJ-999")


class CommentTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_add(self):
        result = self.app.add_comment.execute("PROJ-1", " Hello \n")
        self.assertEqual((result.comment_id, result.payload, result.dry_run), ("11", {"body": "Hello"}, False))
        self.assertEqual(self.gateway.called("add_comment")[0][1:], ("PROJ-1", {"body": "Hello"}))

    def test_dry_run_checks_the_issue_and_sends_nothing(self):
        result = self.app.add_comment.execute("PROJ-1", "Hello", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.gateway.called("add_comment"), [])
        self.assertEqual(self.gateway.called("get_issue")[0][1], "PROJ-1")
        with self.assertRaises(NotFoundError):
            self.app.add_comment.execute("PROJ-999", "Hello", dry_run=True)

    def test_empty_comment(self):
        with self.assertRaises(ValidationError):
            self.app.add_comment.execute("PROJ-1", "  \n")

    def test_list_hides_authors_unless_asked(self):
        self.assertIsNone(self.app.list_comments.execute("PROJ-1")[0].author)
        self.assertEqual(self.app.list_comments.execute("PROJ-1", with_users=True)[0].author.name, "jdoe")
        with self.assertRaises(ValidationError):
            build_app(gateway=self.gateway, settings=settings(hide_users=True)).list_comments.execute("PROJ-1", with_users=True)


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_outward(self):
        result = self.app.link_issues.execute("proj-1", "PROJ-2", "blocks")
        self.assertEqual(result.relation, "PROJ-1 blocks PROJ-2")
        self.assertEqual(result.confirmed, "PROJ-1 blocks PROJ-2")
        self.assertEqual(self.gateway.called("create_link")[0][1], {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-1"}, "inwardIssue": {"key": "PROJ-2"}})

    def test_inverse_reads_back_the_stored_direction(self):
        result = self.app.link_issues.execute("PROJ-1", "PROJ-2", "Blocks", inverse=True)
        self.assertEqual(self.gateway.called("create_link")[0][1], {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-2"}, "inwardIssue": {"key": "PROJ-1"}})
        self.assertEqual(result.confirmed, "PROJ-1 is blocked by PROJ-2")

    def test_dry_run(self):
        result = self.app.link_issues.execute("PROJ-1", "PROJ-2", "Blocks", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.payload["type"], {"name": "Blocks"})
        self.assertEqual(self.gateway.called("create_link"), [])

    def test_refusals(self):
        with self.assertRaises(ValidationError):
            self.app.link_issues.execute("PROJ-1", "PROJ-1", "Blocks")
        with self.assertRaises(ValidationError):
            self.app.link_issues.execute("PROJ-1", "PROJ-2", "requires")
        self.assertEqual(self.gateway.called("create_link"), [])

    def test_link_types_are_sorted(self):
        names = [link_type.name for link_type in self.app.list_link_types.execute()]
        self.assertEqual(names, sorted(names))


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_update(self):
        result = self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New", labels=["x"]))
        self.assertEqual(result.payload, {"fields": {"summary": "New", "labels": ["x"]}})
        self.assertEqual(self.gateway.called("update_issue")[0][1], "PROJ-1")
        self.assertEqual(self.gateway.issues["PROJ-1"].summary, "New")

    def test_nothing_to_update(self):
        with self.assertRaises(ValidationError):
            self.app.update_issue.execute("PROJ-1", IssueChanges())

    def test_dry_run_checks_the_issue_and_sends_nothing(self):
        result = self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New"), dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.gateway.called("update_issue"), [])
        with self.assertRaises(NotFoundError):
            self.app.update_issue.execute("PROJ-999", IssueChanges(summary="New"), dry_run=True)

    def test_stale_guard_blocks_the_write(self):
        with self.assertRaises(ConflictError):
            self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New"), if_unchanged_since="2026-10-01T00:00:00Z")
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_stale_guard_passes_when_nothing_changed_since(self):
        self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New"), if_unchanged_since="2026-10-10T07:00:00Z")
        self.assertEqual(len(self.gateway.called("update_issue")), 1)

    def test_update_from_payload(self):
        self.app.update_issue_from_payload.execute("PROJ-1", {"update": {"labels": [{"add": "x"}]}})
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"update": {"labels": [{"add": "x"}]}})
        with self.assertRaises(ValidationError):
            self.app.update_issue_from_payload.execute("PROJ-1", {"nothing": 1})


class CreateTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def draft(self, **changes):
        values = dict(project="", issue_type="story", summary="New story")
        values.update(changes)
        return IssueDraft(**values)

    def test_type_name_is_resolved_and_the_project_defaults(self):
        result = self.app.create_issue.execute(self.draft(labels=["a"]))
        fields = self.gateway.called("create_issue")[0][1]["fields"]
        self.assertEqual((fields["project"], fields["issuetype"], fields["labels"]), ({"key": "PROJ"}, {"id": "10200"}, ["a"]))
        self.assertEqual((result.key, result.created.key), ("PROJ-101", "PROJ-101"))

    def test_numeric_type_needs_no_lookup(self):
        self.app.create_issue.execute(self.draft(issue_type="10201"))
        self.assertEqual(self.gateway.called("list_issue_types"), [])

    def test_reporter_comes_from_the_draft_then_the_settings_then_the_token(self):
        self.app.create_issue.execute(self.draft(reporter="ann"))
        build_app(gateway=self.gateway, settings=settings(reporter="bob")).create_issue.execute(self.draft())
        self.app.create_issue.execute(self.draft())
        reporters = [call[1]["fields"]["reporter"]["name"] for call in self.gateway.called("create_issue")]
        self.assertEqual(reporters, ["ann", "bob", "jdoe"])

    def test_missing_reporter_is_a_config_error(self):
        self.gateway.user = dataclasses.replace(self.gateway.user, name="")
        with self.assertRaises(ConfigError):
            self.app.create_issue.execute(self.draft())

    def test_dry_run_creates_nothing(self):
        result = self.app.create_issue.execute(self.draft(), dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual((result.key, self.gateway.called("create_issue")), ("", []))

    def test_validation(self):
        no_project = build_app(gateway=self.gateway, settings=settings(project=""))
        with self.assertRaises(ValidationError):
            no_project.create_issue.execute(self.draft())
        with self.assertRaises(ValidationError):
            self.app.create_issue.execute(self.draft(summary=""))
        with self.assertRaises(ValidationError):
            self.app.create_issue.execute(self.draft(issue_type="Task"))

    def test_create_from_payload(self):
        payload = {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S"}}
        self.assertEqual(self.app.create_issue_from_payload.execute(payload).key, "PROJ-101")
        self.assertEqual(self.app.create_issue_from_payload.execute(payload, dry_run=True).key, "")
        with self.assertRaises(ValidationError):
            self.app.create_issue_from_payload.execute({"fields": []})


TICKET = """---
source_id: S-1
source_key: SRC-1
summary: Imported story
issuetype: Story
labels: a, b
fix_versions: Release 1.0
---
# Heading

Body *text*
"""


class ImportMarkdownTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles({"t/001-story.md": TICKET})
        self.diagnostics = RecordingDiagnostics()
        self.app = build_app(gateway=self.gateway, files=self.files, diagnostics=self.diagnostics)

    def fields(self):
        return self.gateway.called("create_issue")[-1][1]["fields"]

    def test_create_from_front_matter(self):
        outcome = self.app.import_markdown.execute("t/001-story.md")
        self.assertEqual((outcome.action, outcome.key), ("created", "PROJ-101"))
        self.assertEqual((self.fields()["summary"], self.fields()["labels"], self.fields()["fixVersions"]), ("Imported story", ["a", "b"], [{"name": "Release 1.0"}]))
        self.assertEqual(self.fields()["description"], "# Heading\n\nBody *text*")

    def test_wiki_conversion(self):
        self.app.import_markdown.execute("t/001-story.md", wiki=True)
        self.assertEqual(self.fields()["description"], "h1. Heading\n\nBody _text_")

    def test_issue_key_means_update(self):
        self.files.files["t/up.md"] = "---\nissue_key: PROJ-1\nsummary: Renamed\n---\nNew body\n"
        outcome = self.app.import_markdown.execute("t/up.md")
        self.assertEqual(outcome.action, "updated")
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"summary": "Renamed", "description": "New body"}})
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_an_update_from_a_text_that_stands_in_place_of_personal_data_is_refused(self):
        for name, text in {
            "a mention": "---\nissue_key: PROJ-1\nsummary: Renamed\n---\nAsk [~user] about it.\n",
            "an address": "---\nissue_key: PROJ-1\nsummary: Mail [email]\n---\nBody\n",
            "a personal link": "---\nissue_key: PROJ-1\nsummary: S\n---\nSee [personal link] now.\n",
            "a field": '---\nissue_key: PROJ-1\nsummary: S\nfields_json: {"customfield_10004": "Ask [~user]"}\n---\nBody\n',
        }.items():
            with self.subTest(name=name):
                self.files.files["t/up.md"] = text
                with self.assertRaises(ValidationError) as caught:
                    self.app.import_markdown.execute("t/up.md")
                self.assertIn("jira push", caught.exception.hint)
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_a_new_issue_may_hold_the_text(self):
        self.files.files["t/new.md"] = "---\nsummary: Mail [email]\nissuetype: Story\n---\nAsk [~user].\n"
        self.assertEqual(self.app.import_markdown.execute("t/new.md").action, "created")

    def test_the_names_in_a_list_that_stand_in_place_of_personal_data_are_refused_too(self):
        for name, line in {"labels": "labels: [email],x", "components": "components: Team [~user]", "fix versions": "fix_versions: [email]"}.items():
            with self.subTest(name=name):
                self.files.files["t/up.md"] = f"---\nissue_key: PROJ-1\nsummary: S\n{line}\n---\nBody\n"
                with self.assertRaises(ValidationError):
                    self.app.import_markdown.execute("t/up.md")
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_dry_run_sends_nothing(self):
        outcome = self.app.import_markdown.execute("t/001-story.md", dry_run=True)
        self.assertTrue(outcome.dry_run and outcome.payload)
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_unknown_fix_version_is_refused_or_skipped(self):
        self.files.files["t/v.md"] = TICKET.replace("Release 1.0", "NOPE")
        with self.assertRaises(ValidationError):
            self.app.import_markdown.execute("t/v.md")
        self.app.import_markdown.execute("t/v.md", skip_invalid_fix_version=True)
        self.assertNotIn("fixVersions", self.fields())
        self.assertTrue(any("NOPE" in warning for warning in self.diagnostics.warnings))

    def test_required_front_matter(self):
        for name, text in (("a", "---\nsummary: x\n---\nb"), ("b", "---\nissuetype: Story\n---\nb"), ("c", "just text")):
            self.files.files[f"t/{name}.md"] = text
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.app.import_markdown.execute(f"t/{name}.md")

    def test_missing_file(self):
        with self.assertRaises(ValidationError):
            self.app.import_markdown.execute("t/none.md")


def story(number, parent=""):
    meta = f"source_id: S-{number}\nsource_key: SRC-{number}\nsummary: Story {number}\nissuetype: Story\n"
    return f"---\n{meta}{('parent_id: ' + parent + chr(10)) if parent else ''}---\nBody {number}\n"


class ImportDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        epic = "---\nsource_id: E-1\nsource_key: SRC-E1\nsummary: Epic\nissuetype: Epic\n---\nEpic body\n"
        self.files = MemoryFiles({"d/001-epic.md": epic, "d/002-story.md": story(2, "E-1"), "d/003-story.md": story(3), "d/notes.txt": "ignored"})
        self.app = build_app(gateway=self.gateway, files=self.files)

    def test_imports_in_order_and_writes_the_map(self):
        batch = self.app.import_markdown_directory.execute("d")
        self.assertEqual([(o.action, o.key) for o in batch.outcomes], [("created", "PROJ-101"), ("created", "PROJ-102"), ("created", "PROJ-103")])
        self.assertEqual(batch.map_file, "d/jira-import-map.csv")
        rows = self.files.files["d/jira-import-map.csv"].splitlines()
        self.assertEqual(rows[0], "source_id,source_key,jira_key,issue_type,status,file")
        self.assertEqual(rows[2], "S-2,SRC-2,PROJ-102,Story,Created,002-story.md")

    def test_a_story_is_linked_to_its_epic(self):
        self.app.import_markdown_directory.execute("d")
        story_fields = self.gateway.called("create_issue")[1][1]["fields"]
        self.assertEqual(story_fields["customfield_10002"], "PROJ-101")
        self.assertNotIn("customfield_10002", self.gateway.called("create_issue")[2][1]["fields"])

    def test_the_epic_link_field_can_be_set_explicitly(self):
        app = build_app(gateway=self.gateway, files=self.files, settings=settings(epic_link_field="customfield_777"))
        app.import_markdown_directory.execute("d")
        self.assertEqual(self.gateway.called("create_issue")[1][1]["fields"]["customfield_777"], "PROJ-101")

    def test_a_second_run_skips_what_was_created(self):
        self.app.import_markdown_directory.execute("d")
        before = len(self.gateway.called("create_issue"))
        batch = self.app.import_markdown_directory.execute("d")
        self.assertEqual([o.action for o in batch.outcomes], ["skipped"] * 3)
        self.assertEqual(len(self.gateway.called("create_issue")), before)

    def test_a_resumed_run_creates_only_the_rest(self):
        self.files.files["d/jira-import-map.csv"] = "source_id,source_key,jira_key,issue_type,status,file\nE-1,SRC-E1,PROJ-50,Epic,Created,001-epic.md"  # no final newline
        batch = self.app.import_markdown_directory.execute("d")
        self.assertEqual([o.action for o in batch.outcomes], ["skipped", "created", "created"])
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["customfield_10002"], "PROJ-50")
        self.assertEqual(len(self.files.files["d/jira-import-map.csv"].splitlines()), 4)

    def test_dry_run_writes_no_map_and_creates_nothing(self):
        batch = self.app.import_markdown_directory.execute("d", dry_run=True)
        self.assertEqual([o.action for o in batch.outcomes], ["created"] * 3)
        self.assertNotIn("d/jira-import-map.csv", self.files.files)
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_a_dry_run_shows_the_epic_link_the_real_run_will_send(self):
        batch = self.app.import_markdown_directory.execute("d", dry_run=True)
        self.assertEqual(batch.outcomes[1].payload["fields"]["customfield_10002"], "<key of E-1, assigned when it is created>")
        self.assertNotIn("customfield_10002", batch.outcomes[2].payload["fields"])

    def test_a_dry_run_knows_the_parents_that_are_already_in_the_map(self):
        self.files.files["d/jira-import-map.csv"] = "source_id,source_key,jira_key,issue_type,status,file\nE-1,SRC-E1,PROJ-50,Epic,Created,001-epic.md\n"
        batch = self.app.import_markdown_directory.execute("d", dry_run=True)
        self.assertEqual(batch.outcomes[1].payload["fields"]["customfield_10002"], "PROJ-50")

    def test_a_dry_run_catches_a_parent_that_comes_too_late_or_never(self):
        files = MemoryFiles({"d/001-story.md": story(1, "E-9"), "d/002-epic.md": "---\nsource_id: E-9\nsummary: Epic\nissuetype: Epic\n---\nx"})
        batch = build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d", dry_run=True, continue_on_error=True)
        self.assertEqual([o.action for o in batch.outcomes], ["failed", "created"])
        self.assertIn("E-9", batch.outcomes[0].message)

    def test_two_files_with_one_source_id_are_refused_before_anything_is_created(self):
        self.files.files["d/004-again.md"] = story(2)  # source_id S-2 is the one of 002-story.md
        with self.assertRaises(ValidationError) as caught:
            self.app.import_markdown_directory.execute("d")
        self.assertIn("S-2", caught.exception.message)
        self.assertEqual(self.gateway.called("create_issue"), [])
        self.assertNotIn("d/jira-import-map.csv", self.files.files)

    def test_the_map_file_is_made_before_the_first_issue(self):
        files = MemoryFiles({"d/001-story.md": story(1)})
        build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d", map_file="deep/er/map.csv")
        self.assertTrue(files.files["deep/er/map.csv"].startswith("source_id,source_key,jira_key"))

    def test_an_unwritable_map_stops_the_run_before_it_creates_anything(self):
        class NoMap(MemoryFiles):
            def write_text(self, path, text):
                raise ValidationError(f"Cannot write {path}: Access denied")

        files = NoMap({"d/001-story.md": story(1)})
        with self.assertRaises(ValidationError) as caught:
            build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d")
        self.assertIn("nothing was created", caught.exception.hint)
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_when_the_map_cannot_be_updated_the_created_key_is_reported_and_the_run_stops(self):
        class MapFull(MemoryFiles):
            def append_text(self, path, text):
                raise ValidationError(f"Cannot write {path}: Disk full")

        files = MapFull({"d/001-story.md": story(1), "d/002-story.md": story(2)})
        batch = build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d", continue_on_error=True)
        self.assertEqual([o.action for o in batch.outcomes], ["created", "failed"])  # stopped although continue_on_error
        self.assertEqual(len(self.gateway.called("create_issue")), 1)
        self.assertIn("PROJ-101", batch.outcomes[1].message)
        self.assertIn("by hand", batch.outcomes[1].message)
        self.assertIsNotNone(batch.first_error)

    def test_a_failure_stops_the_run_unless_told_to_continue(self):
        self.files.files["d/001-epic.md"] = "---\nsummary: no type\n---\nx"
        stopped = self.app.import_markdown_directory.execute("d")
        self.assertEqual([o.action for o in stopped.outcomes], ["failed"])
        self.assertIsInstance(stopped.first_error, ValidationError)
        carried_on = self.app.import_markdown_directory.execute("d", continue_on_error=True)
        self.assertEqual([o.action for o in carried_on.outcomes], ["failed", "failed", "created"])  # the story's parent was never created

    def test_a_missing_parent_is_a_failure(self):
        files = MemoryFiles({"d/001-story.md": story(1, "E-9")})
        batch = build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d")
        self.assertEqual(batch.outcomes[0].action, "failed")
        self.assertIn("E-9", batch.outcomes[0].message)

    def test_missing_directory(self):
        with self.assertRaises(ValidationError):
            self.app.import_markdown_directory.execute("nowhere")


CSV = (
    "project,issuetype,summary,labels,fix_versions,issue_key,fields_json\n"
    'PROJ,Story,"One, with comma","a,b",Release 1.0,,"{""customfield_10001"": 5}"\n'
    ",Story,Two,,,,\n"
    ",,No type,,,,\n"
    ",,Renamed,,,PROJ-1,\n"
)


class ImportCsvTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles({"rows.csv": CSV})
        self.app = build_app(gateway=self.gateway, files=self.files)

    def test_rows_create_and_update(self):
        batch = self.app.import_csv.execute("rows.csv", continue_on_error=True)
        self.assertEqual([o.action for o in batch.outcomes], ["created", "created", "failed", "updated"])
        first = self.gateway.called("create_issue")[0][1]["fields"]
        self.assertEqual((first["summary"], first["labels"], first["customfield_10001"]), ("One, with comma", ["a", "b"], 5))
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"summary": "Renamed"}})

    def test_failures_name_the_line_and_stop_unless_told_otherwise(self):
        batch = self.app.import_csv.execute("rows.csv")
        self.assertEqual([o.action for o in batch.outcomes], ["created", "created", "failed"])
        self.assertIn("line 4", batch.outcomes[2].message)
        self.assertIsNotNone(batch.first_error)

    def test_dry_run(self):
        batch = self.app.import_csv.execute("rows.csv", dry_run=True, continue_on_error=True)
        self.assertTrue(all(o.dry_run for o in batch.outcomes if o.action != "failed"))
        self.assertEqual(self.gateway.called("create_issue") + self.gateway.called("update_issue"), [])

    def test_byte_order_mark_is_ignored(self):
        self.files.files["bom.csv"] = "\ufeffsummary,issuetype\nA,Story\n"
        self.assertEqual(self.app.import_csv.execute("bom.csv").outcomes[0].action, "created")

    def test_a_row_that_updates_from_a_placeholder_is_refused_and_one_that_creates_is_not(self):
        self.files.files["marks.csv"] = "issue_key,summary,issuetype,description\nPROJ-1,Renamed,Story,Ask [~user]\n,New issue,Story,Ask [~user]\n"
        batch = self.app.import_csv.execute("marks.csv", continue_on_error=True)
        self.assertEqual([outcome.action for outcome in batch.outcomes], ["failed", "created"])
        self.assertIn("placeholder", batch.outcomes[0].message)
        self.assertEqual(self.gateway.called("update_issue"), [])
        self.files.files["names.csv"] = "issue_key,summary,labels\nPROJ-1,Renamed,[email]\n"
        self.assertEqual(self.app.import_csv.execute("names.csv").outcomes[0].action, "failed")
        for column, value in (("components", "Team [~user]"), ("fix_versions", "[email]")):
            with self.subTest(column=column):
                self.files.files["names.csv"] = f"issue_key,summary,{column}\nPROJ-1,Renamed,{value}\n"
                self.assertEqual(self.app.import_csv.execute("names.csv").outcomes[0].action, "failed")
        self.assertEqual(self.gateway.called("update_issue"), [])


if __name__ == "__main__":
    unittest.main()
