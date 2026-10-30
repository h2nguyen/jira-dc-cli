"""The reading commands on the command line: `issue` (several keys, parts, cuts, outline, rendered text, history), `issues`, `search`, `fields`,
`filters` and `filter`, with fakes behind them."""
import dataclasses
import json
import unittest

from jira_dc_cli.domain.errors import UnavailableError
from jira_dc_cli.domain.history import Changelog, FieldChange, HistoryEntry
from jira_dc_cli.domain.model import Attachment, Comment, IssueLink, IssueRef, User
from jira_dc_cli.domain.savedfilters import SavedFilter
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, JANE, FakeGateway, MemoryFiles, sample_issue
from tests.unit.test_reading_domain import LEAK_WORDS, leak_variants

LONG = "h1. Goal\nShip it.\n\nh2. Steps\n# open\n# click\n\nh2. Result\nIt works for [~jdoe] (jane.doe@example.com)."
CODE_FIRST = "{code}\n" + "\n".join(f"at line {n}" for n in range(60)) + "\n{code}\n\nWhat happened after the stack trace."
REVIEW_JQL = 'assignee = jdoe AND reporter in ("asmith","jane.doe") AND watcher = "Alex Smith" ORDER BY created'
REVIEW_SHOWN = "assignee = [user] AND reporter in ([user],[user]) AND watcher = [user] ORDER BY created"
PEOPLE = ("jdoe", "asmith", "jane.doe", "Alex", "Smith", "Jane")
DEFAULT_TEXT = """PROJ-1  [Story | To Do | High]
Export the report

Labels:       backend
Components:   Demo
Fix versions: Release 1.0
Epic:         PROJ-9
Story points: 5
Sprint:       Demo Sprint 3 (ACTIVE)
Created:      2026-10-01T08:00:00.000+0200
Updated:      2026-10-10T09:00:00.000+0200

Description:
Ask [~user] or [email]
Second line

Acceptance criteria:
1. GIVEN a, WHEN b, THEN c.

Comments: 1 (show with --comments)
"""
MODEL_KEYS = [
    "acceptance", "assignee", "attachmentTotal", "attachments", "commentTotal", "comments", "components", "created", "description", "epicKey", "extra", "fixVersions",
    "issueType", "key", "labels", "links", "parentKey", "priority", "reporter", "sprints", "status", "storyPoints", "subtasks", "summary", "updated",
]
OPTION_KEYS = {"truncated", "totalChars", "outline", "section", "renderedDescription", "renderedAcceptance", "history", "historyComplete"}


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.gateway.page_cap = 100

    def run_cli(self, *argv, **options):
        return run_cli(*argv, gateway=self.gateway, **options)


class IssueDefaultsTest(Fixture):
    def test_the_text_of_one_issue_is_what_it_was(self):
        result = self.run_cli("issue", "PROJ-1")
        self.assertEqual((result.code, result.err, result.out), (0, "", DEFAULT_TEXT))

    def test_the_json_of_one_issue_is_the_object_with_the_keys_of_the_model_and_no_key_of_an_option(self):
        data = self.run_cli("issue", "PROJ-1", "--json").json()
        self.assertEqual(sorted(data), MODEL_KEYS)
        self.assertFalse(OPTION_KEYS & set(data))
        self.assertEqual((data["attachments"], data["attachmentTotal"]), ([], 0))

    def test_one_issue_is_one_ordinary_request(self):
        self.run_cli("issue", "PROJ-1")
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_issue"])

    def test_an_issue_with_nothing_in_it_and_one_with_everything_print_what_they_always_printed(self):
        self.gateway.issues["PROJ-5"] = sample_issue(
            "PROJ-5", description="", acceptance="", labels=[], components=[], fix_versions=[], sprints=[], story_points=None, comments=[], comment_total=0
        )
        self.assertEqual(
            self.run_cli("issue", "PROJ-5").out,
            "PROJ-5  [Story | To Do | High]\nA summary\n\nCreated: 2026-10-01T08:00:00.000+0200\nUpdated: 2026-10-10T09:00:00.000+0200\n\nComments: 0 (show with --comments)\n",
        )
        self.gateway.issues["PROJ-6"] = sample_issue(
            "PROJ-6",
            links=[IssueLink("Blocks", "blocks", "outward", IssueRef("PROJ-2", "Fix the thing", "In Progress"))],
            subtasks=[IssueRef("PROJ-7", "A subtask", "To Do")],
            parent_key="PROJ-9",
            story_points=2.5,
            description="  padded text  \n\n\nlast",
            comments=[Comment("1", "2026-10-02", "one"), Comment("2", "2026-10-03", "two", JANE)],
            comment_total=2,
        )
        self.assertEqual(
            self.run_cli("issue", "PROJ-6", "--comments", "--with-users").out,
            "PROJ-6  [Story | To Do | High]\nA summary\n\nAssignee:     Jane Doe\nReporter:     Jane Doe\nLabels:       backend\nComponents:   Demo\nFix versions: Release 1.0\n"
            "Epic:         PROJ-9\nStory points: 2.5\nSprint:       Demo Sprint 3 (ACTIVE)\nCreated:      2026-10-01T08:00:00.000+0200\n"
            "Updated:      2026-10-10T09:00:00.000+0200\n\nLinks:\n  blocks PROJ-2 (In Progress) Fix the thing\n\nSubtasks:\n  PROJ-7 (To Do) A subtask\n\n"
            "Description:\npadded text  \n\n\nlast\n\nAcceptance criteria:\n1. GIVEN a, WHEN b, THEN c.\n\nComments: 2\n--- #1 2026-10-02\none\n--- #2 2026-10-03 Jane Doe\ntwo\n",
        )

    def test_the_comments_and_people_of_the_whole_view(self):
        result = self.run_cli("issue", "PROJ-1", "--comments", "--with-users")
        self.assertIn("Assignee:     Jane Doe", result.out)
        self.assertIn("--- #10 2026-10-02T10:00:00.000+0200 Jane Doe\nLooks fine, [~jdoe].", result.out)

    def test_a_missing_key_is_an_error_with_exit_code_4(self):
        result = self.run_cli("issue", "PROJ-999", "--json")
        self.assertEqual((result.code, result.out, result.error()["code"]), (4, "", "NOT_FOUND"))

    def test_no_key_is_a_usage_error_with_the_usual_hint(self):
        result = self.run_cli("issue", "--json")
        self.assertEqual((result.code, result.out), (2, ""))
        self.assertEqual(result.error()["hint"], "Run `jira help issue` for the usage and examples.")
        self.assertEqual(self.gateway.calls, [])

    def test_a_bad_key_is_refused_before_a_request(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "nonsense").code, 1)
        self.assertEqual(self.gateway.calls, [])

    def test_the_policy_refuses_people(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "PROJ-2", "--with-users", settings=settings(hide_users=True)).code, 1)


class AttachmentsBlockTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue(
            "PROJ-1",
            attachments=[Attachment("1", "design.png", "image/png", 1200, author=JANE), Attachment("2", "spec v2.pdf", "application/pdf", 90000)],
            attachment_total=2,
        )

    def test_the_text_names_them_after_the_links_and_before_the_description(self):
        out = self.run_cli("issue", "PROJ-1").out
        self.assertIn("\n\nAttachments: 2\n  design.png\n  spec v2.pdf\n\nDescription:", out)

    def test_an_issue_without_attachments_has_no_block(self):
        self.assertNotIn("Attachments", self.run_cli("issue", "PROJ-2").out)

    def test_the_json_has_them_without_the_uploader(self):
        data = self.run_cli("issue", "PROJ-1", "--json").json()
        self.assertEqual(data["attachmentTotal"], 2)
        self.assertEqual([(item["filename"], item["mimeType"], item["size"], item["author"]) for item in data["attachments"]], [("design.png", "image/png", 1200, None), ("spec v2.pdf", "application/pdf", 90000, None)])

    def test_with_people_the_json_names_the_uploader(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--with-users", "--json").json()["attachments"][0]["author"]["name"], "jdoe")

    def test_only_attachments(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--only", "attachments").out, "PROJ-1\n\nAttachments: 2\n  design.png\n  spec v2.pdf\n")
        self.assertEqual(self.run_cli("issue", "PROJ-2", "--only", "attachments").out, "PROJ-2\n\nAttachments: (none)\n")


class OnlyTest(Fixture):
    def test_the_key_and_the_facts_asked_for(self):
        result = self.run_cli("issue", "PROJ-1", "--only", "summary,status,priority,labels")
        self.assertEqual(result.out, "PROJ-1\nSummary:  Export the report\nStatus:   To Do\nPriority: High\nLabels:   backend\n")

    def test_the_json_has_the_key_and_the_keys_of_those_parts_only(self):
        data = self.run_cli("issue", "PROJ-1", "--only", "summary,epic,points,dates", "--json").json()
        self.assertEqual(list(data), ["key", "summary", "epicKey", "parentKey", "storyPoints", "created", "updated"])
        self.assertEqual((data["epicKey"], data["storyPoints"]), ("PROJ-9", 5.0))
        data = self.run_cli("issue", "PROJ-1", "--only", "status,sprints,fixVersions,components,priority,labels", "--json").json()
        self.assertEqual(list(data), ["key", "status", "priority", "labels", "components", "fixVersions", "sprints"])

    def test_each_part_has_its_keys(self):
        gateway = FakeGateway()
        expected = {
            "summary": ["summary"], "status": ["status"], "priority": ["priority"], "labels": ["labels"], "components": ["components"],
            "fixVersions": ["fixVersions"], "epic": ["epicKey", "parentKey"], "points": ["storyPoints"], "sprints": ["sprints"], "dates": ["created", "updated"],
            "description": ["description"], "acceptance": ["acceptance"], "comments": ["comments", "commentTotal"], "links": ["links"], "subtasks": ["subtasks"],
            "attachments": ["attachments", "attachmentTotal"],
        }
        for part, keys in expected.items():
            with self.subTest(part=part):
                data = run_cli("issue", "PROJ-1", "--only", part, "--json", gateway=gateway).json()
                self.assertEqual(sorted(data), sorted(["key", *keys]))

    def test_long_texts_and_lists_keep_the_layout_of_the_whole_view(self):
        self.gateway.issues["PROJ-1"] = sample_issue(
            "PROJ-1",
            links=[IssueLink("Blocks", "blocks", "outward", IssueRef("PROJ-2", "Fix the thing", "In Progress"))],
            subtasks=[IssueRef("PROJ-5", "A subtask", "To Do")],
        )
        out = self.run_cli("issue", "PROJ-1", "--only", "links,subtasks,description,acceptance").out
        self.assertEqual(
            out,
            "PROJ-1\n\nLinks:\n  blocks PROJ-2 (In Progress) Fix the thing\n\nSubtasks:\n  PROJ-5 (To Do) A subtask\n\nDescription:\nAsk [~user] or [email]\nSecond line\n\n"
            "Acceptance criteria:\n1. GIVEN a, WHEN b, THEN c.\n",
        )

    def test_a_part_that_is_empty_says_so(self):
        self.gateway.issues["PROJ-4"] = sample_issue("PROJ-4", labels=[], description="", acceptance="", story_points=None)
        out = self.run_cli("issue", "PROJ-4", "--only", "labels,points,links,subtasks,description,acceptance,attachments").out
        self.assertEqual(
            out,
            "PROJ-4\nLabels:       (none)\nStory points: (none)\n\nLinks: (none)\n\nSubtasks: (none)\n\nAttachments: (none)\n\nDescription: (none)\n\nAcceptance criteria: (none)\n",
        )

    def test_comments_by_name(self):
        out = self.run_cli("issue", "PROJ-1", "--only", "comments").out
        self.assertEqual(out, "PROJ-1\n\nComments: 1\n--- #10 2026-10-02T10:00:00.000+0200\nLooks fine, [~user].\n")

    def test_the_comments_flag_adds_them_to_the_parts(self):
        out = self.run_cli("issue", "PROJ-1", "--only", "status", "--comments").out
        self.assertIn("Status: To Do", out)
        self.assertIn("Comments: 1\n--- #10", out)

    def test_people_are_added_by_with_users(self):
        out = self.run_cli("issue", "PROJ-1", "--only", "status", "--with-users").out
        self.assertEqual(out, "PROJ-1\nAssignee: Jane Doe\nReporter: Jane Doe\nStatus:   To Do\n")
        data = self.run_cli("issue", "PROJ-1", "--only", "status", "--with-users", "--json").json()
        self.assertEqual(sorted(data), ["assignee", "key", "reporter", "status"])
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--only", "status", "--json").json(), {"key": "PROJ-1", "status": {"name": "To Do", "category": "new"}})

    def test_the_names_may_be_written_in_any_case(self):
        self.assertEqual(sorted(self.run_cli("issue", "PROJ-1", "--only", "Fix-Versions,SUMMARY", "--json").json()), ["fixVersions", "key", "summary"])

    def test_an_unknown_name_is_a_usage_error_that_lists_the_valid_ones(self):
        result = self.run_cli("issue", "PROJ-1", "--only", "summary,nope", "--json")
        self.assertEqual((result.code, result.out), (2, ""))
        error = result.error()
        self.assertEqual(error["code"], "USAGE")
        self.assertIn("nope", error["message"])
        self.assertIn("summary, status, priority, labels, components, fixVersions, epic, points, sprints, dates, description, acceptance, comments, links, subtasks, attachments", error["hint"])
        self.assertEqual(self.gateway.calls, [])

    def test_the_text_of_the_error_names_the_valid_ones_too(self):
        err = self.run_cli("issue", "PROJ-1", "--only", "x").err
        self.assertIn("ERROR: Unknown name in --only: x.", err)
        self.assertIn("Hint: Valid names: summary,", err)

    def test_an_empty_list_is_a_usage_error(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--only", ",").code, 2)


class MaxCharsTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue(
            "PROJ-1",
            summary="Export the report",
            description="first paragraph\n\nsecond paragraph is a bit longer\n\nthird",
            acceptance="short",
            comments=[Comment("10", "2026-10-02T10:00:00.000+0200", "a long one\n\n" + "z " * 100, JANE)],
            comment_total=1,
        )

    def test_the_text_says_under_every_text_that_was_cut(self):
        out = self.run_cli("issue", "PROJ-1", "--max-chars", "40", "--comments").out
        self.assertIn("Description:\nfirst paragraph\n(cut: 15 of 56 characters; --outline lists the headings, --section TITLE reads one part, --max-chars N shows more)\n", out)
        self.assertIn("Acceptance criteria:\nshort\n\nComments: 1\n--- #10 2026-10-02T10:00:00.000+0200\na long one\n(cut: 10 of 212 characters; --max-chars N shows more)", out)
        self.assertNotIn("second paragraph", out)

    def test_a_text_that_fits_has_no_line(self):
        self.assertNotIn("(cut:", self.run_cli("issue", "PROJ-1", "--max-chars", "5000", "--comments").out)

    def test_the_json_has_the_two_objects_by_field_only_with_the_option(self):
        data = self.run_cli("issue", "PROJ-1", "--max-chars", "40", "--comments", "--json").json()
        self.assertEqual(data["truncated"], {"description": True, "acceptance": False, "comments.10": True})
        self.assertEqual(data["totalChars"], {"description": 56, "acceptance": 5, "comments.10": 212})
        self.assertEqual(data["description"], "first paragraph")
        self.assertNotIn("truncated", self.run_cli("issue", "PROJ-1", "--json").json())
        self.assertNotIn("totalChars", self.run_cli("issue", "PROJ-1", "--comments", "--json").json())

    def test_the_objects_are_there_even_when_nothing_is_measured(self):
        data = self.run_cli("issue", "PROJ-1", "--max-chars", "40", "--only", "status", "--json").json()
        self.assertEqual((data["truncated"], data["totalChars"]), ({}, {}))

    def test_a_limit_below_one_is_a_usage_error(self):
        for value in ("0", "-3"):
            with self.subTest(value=value):
                self.assertEqual(self.run_cli("issue", "PROJ-1", "--max-chars", value).code, 2)
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--max-chars", "many").code, 2)

    def test_a_limit_that_only_looks_like_a_number_is_a_usage_error_and_a_huge_one_cuts_nothing(self):
        for value in ("\u00b2", "\u2460", "9" * 5000, "1.5", "1e3", ""):
            with self.subTest(value=value[:8], length=len(value)):
                result = self.run_cli("issue", "PROJ-1", "--max-chars", value)
                self.assertEqual((result.code, result.out), (2, ""))
                self.assertNotIn("unexpected", result.err)
        data = self.run_cli("issue", "PROJ-1", "--max-chars", "9" * 40, "--json").json()
        self.assertEqual(sorted(data["truncated"]), ["acceptance", "description"])
        self.assertFalse(any(data["truncated"].values()))

    def test_a_description_that_opens_with_a_code_block_is_cut_at_a_line_and_the_block_is_closed(self):
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description=CODE_FIRST)
        shown = "{code}\n" + "\n".join(f"at line {n}" for n in range(8)) + "\n{code}"
        out = self.run_cli("issue", "PROJ-1", "--only", "description", "--max-chars", "100").out
        self.assertEqual(
            out,
            f"PROJ-1\n\nDescription:\n{shown}\n(cut: {len(shown)} of {len(CODE_FIRST)} characters; the code block is closed at the cut; "
            "--outline lists the headings, --section TITLE reads one part, --max-chars N shows more)\n",
        )
        data = self.run_cli("issue", "PROJ-1", "--only", "description", "--max-chars", "100", "--json").json()
        self.assertEqual(data, {"key": "PROJ-1", "description": shown, "truncated": {"description": True}, "totalChars": {"description": len(CODE_FIRST)}})

    def test_a_cut_before_a_code_block_says_nothing_about_the_block(self):
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description="Intro.\n\n" + CODE_FIRST)
        out = self.run_cli("issue", "PROJ-1", "--only", "description", "--max-chars", "100").out
        self.assertIn("Description:\nIntro.\n(cut: 6 of ", out)
        self.assertNotIn("code block", out)

    def test_every_issue_of_a_list_is_cut_and_measured(self):
        data = self.run_cli("issue", "PROJ-1", "PROJ-2", "--max-chars", "40", "--json").json()
        self.assertEqual([item["truncated"]["description"] for item in data["issues"]], [True, False])


class OutlineAndSectionTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", summary="Export the report", description=LONG)

    def test_the_outline_in_text(self):
        out = self.run_cli("issue", "PROJ-1", "--outline", "--only", "summary,description").out
        self.assertEqual(
            out,
            "PROJ-1\nSummary: Export the report\n\nOutline of the description (--section TITLE or NUMBER reads one part):\n"
            "   1. h1 Goal  (87 characters)\n   2. h2   Steps  (24 characters)\n   3. h2   Result  (42 characters)\n",
        )

    def test_the_outline_in_the_whole_view_replaces_the_description(self):
        out = self.run_cli("issue", "PROJ-1", "--outline").out
        self.assertIn("Outline of the description", out)
        self.assertNotIn("Ship it.", out)
        self.assertIn("Acceptance criteria:", out)

    def test_the_outline_in_json(self):
        data = self.run_cli("issue", "PROJ-1", "--outline", "--only", "description", "--json").json()
        self.assertEqual(
            data,
            {
                "key": "PROJ-1",
                "description": None,
                "outline": [
                    {"index": 1, "level": 1, "title": "Goal", "chars": 87},
                    {"index": 2, "level": 2, "title": "Steps", "chars": 24},
                    {"index": 3, "level": 2, "title": "Result", "chars": 42},
                ],
            },
        )

    def test_a_description_without_headings(self):
        out = self.run_cli("issue", "PROJ-2", "--outline", "--only", "description").out
        self.assertIn("(no headings: read the description whole, or cut it with --max-chars)", out)
        self.assertEqual(self.run_cli("issue", "PROJ-2", "--outline", "--only", "description", "--json").json()["outline"], [])

    def test_a_section_by_its_heading(self):
        result = self.run_cli("issue", "PROJ-1", "--section", "STEPS", "--only", "description")
        self.assertEqual(result.out, "PROJ-1\n\nDescription (section 2, Steps):\nh2. Steps\n# open\n# click\n")

    def test_a_section_by_its_number_in_json(self):
        data = self.run_cli("issue", "PROJ-1", "--section", "3", "--only", "description", "--json").json()
        self.assertEqual(data["section"], {"index": 3, "level": 2, "title": "Result"})
        self.assertEqual(data["description"], "h2. Result\nIt works for [~user] ([email]).")

    def test_a_section_with_a_cut(self):
        out = self.run_cli("issue", "PROJ-1", "--section", "goal", "--max-chars", "30", "--only", "description").out
        self.assertEqual(out, "PROJ-1\n\nDescription (section 1, Goal):\nh1. Goal\nShip it.\n(cut: 17 of 87 characters; --max-chars N shows more)\n")

    def test_a_heading_that_is_not_there_lists_the_headings(self):
        result = self.run_cli("issue", "PROJ-1", "--section", "nothing")
        self.assertEqual((result.code, result.out), (1, ""))
        self.assertIn("No heading matches 'nothing'.", result.err)
        self.assertIn("The headings: 1. Goal | 2. Steps | 3. Result.", result.err)

    def test_a_selector_that_only_looks_like_a_number_is_a_clean_error(self):
        for selector in ("\u00b2", "\u2460", "\u0662", "1" * 5000):
            for extra in ((), ("--json",)):
                with self.subTest(selector=selector[:6], length=len(selector), json=bool(extra)):
                    result = self.run_cli("issue", "PROJ-1", "--section", selector, "--only", "description", *extra)
                    self.assertEqual((result.code, result.out), (1, ""))
                    self.assertIn("No heading matches", result.err)
                    self.assertIn("1. Goal | 2. Steps | 3. Result", result.err)
                    self.assertNotIn("unexpected", result.err)
                    self.assertNotIn("ValueError", result.err)

    def test_the_outline_and_a_section_read_one_issue_and_exclude_each_other(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "PROJ-2", "--outline").code, 2)
        self.assertEqual(self.run_cli("issue", "PROJ-1", "PROJ-2", "--section", "Goal").code, 2)
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--outline", "--section", "Goal").code, 2)

    def test_without_the_description_in_only_they_are_refused(self):
        result = self.run_cli("issue", "PROJ-1", "--outline", "--only", "status")
        self.assertEqual(result.code, 2)
        self.assertIn("Add description to --only", result.err)


class RenderedTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description="* a\n* b", acceptance="1. x")
        self.gateway.rendered["PROJ-1"] = (
            '<ul><li>a</li><li>b</li></ul><p><a href="/secure/ViewProfile.jspa?name=jdoe" class="user-hover" rel="jdoe">Jane Doe</a></p>',
            "<ol><li>x</li></ol>",
        )

    def test_the_text_shows_the_markup_and_then_the_rendering(self):
        out = self.run_cli("issue", "PROJ-1", "--rendered", "--only", "description,acceptance").out
        self.assertEqual(
            out,
            "PROJ-1\n\nDescription:\n* a\n* b\n\nDescription as Jira renders it:\n- a\n- b\n\n[~user]\n\n"
            "Acceptance criteria:\n1. x\n\nAcceptance criteria as Jira renders them:\n1. x\n",
        )

    def test_the_json_keys_exist_only_with_the_option(self):
        data = self.run_cli("issue", "PROJ-1", "--rendered", "--json").json()
        self.assertEqual((data["renderedDescription"], data["renderedAcceptance"], data["description"]), ("- a\n- b\n\n[~user]", "1. x", "* a\n* b"))
        self.assertFalse({"renderedDescription", "renderedAcceptance"} & set(self.run_cli("issue", "PROJ-1", "--json").json()))

    def test_people_in_the_rendered_text_only_with_users(self):
        self.assertNotIn("Jane", self.run_cli("issue", "PROJ-1", "--rendered").out)
        self.assertIn("Jane Doe", self.run_cli("issue", "PROJ-1", "--rendered", "--with-users").out)

    def test_it_is_one_request_with_the_expansion(self):
        self.run_cli("issue", "PROJ-1", "--rendered")
        self.assertEqual(self.gateway.calls, [("get_issue_with", "PROJ-1", ("renderedFields",))])

    def test_only_what_is_shown_is_rendered(self):
        data = self.run_cli("issue", "PROJ-1", "--rendered", "--only", "description", "--json").json()
        self.assertEqual(sorted(data), ["description", "key", "renderedDescription"])

    def test_it_needs_a_text_in_only(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "--rendered", "--only", "status").code, 2)


class RenderedMentionsTest(Fixture):
    """A link to a person in the text that Jira renders, written in any way that Jira, a proxy or a plugin may write it, names nobody unless
    people were asked for (`--with-users`), and nobody at all under JIRA_HIDE_USERS=true."""

    ONLY = ("--only", "description,acceptance")

    def serve(self, html, *keys):
        for key in keys or ("PROJ-1",):
            self.gateway.issues[key] = sample_issue(key, description="plain", acceptance="plain")
            self.gateway.rendered[key] = (html, html)

    def test_no_variant_leaks_a_name_into_the_text_or_the_json_also_under_the_policy(self):
        for variant, (html, expected, _) in leak_variants().items():
            for policy in (False, True):
                with self.subTest(variant=variant, hide_users=policy):
                    options = {"settings": settings(hide_users=True)} if policy else {}
                    self.serve(html)
                    text = self.run_cli("issue", "PROJ-1", "--rendered", *self.ONLY, **options)
                    data = self.run_cli("issue", "PROJ-1", "--rendered", *self.ONLY, "--json", **options)
                    self.assertEqual((text.code, data.code), (0, 0), text.err + data.err)
                    self.assertIn(f"Description as Jira renders it:\n{expected}\n", text.out)
                    self.assertIn(f"Acceptance criteria as Jira renders them:\n{expected}\n", text.out)
                    document = data.json()
                    self.assertEqual((document["renderedDescription"], document["renderedAcceptance"]), (expected, expected))
                    for word in LEAK_WORDS:
                        self.assertNotIn(word, text.out + text.err + data.out + data.err)

    def test_with_users_the_names_are_shown_as_before(self):
        for variant, (html, _, shown) in leak_variants().items():
            with self.subTest(variant=variant):
                self.serve(html)
                data = self.run_cli("issue", "PROJ-1", "--rendered", "--with-users", *self.ONLY, "--json").json()
                self.assertIn(shown, data["renderedDescription"])
                self.assertIn(shown, data["renderedAcceptance"])
                self.assertIn(shown, self.run_cli("issue", "PROJ-1", "--rendered", "--with-users", *self.ONLY).out)

    def test_the_policy_refuses_people_for_the_rendered_text_too(self):
        self.serve(leak_variants()["a closing tag in capitals"][0])
        result = self.run_cli("issue", "PROJ-1", "--rendered", "--with-users", "--json", settings=settings(hide_users=True))
        self.assertEqual((result.code, result.out, result.error()["code"]), (1, "", "INVALID"))
        self.assertEqual(self.gateway.calls, [])

    def test_several_issues_are_cleaned_alike_from_the_one_search(self):
        html, expected, shown = leak_variants()["several mentions in one paragraph"]
        self.serve(html, "PROJ-1", "PROJ-2")
        for options in ({}, {"settings": settings(hide_users=True)}):
            data = self.run_cli("issue", "PROJ-1", "PROJ-2", "--rendered", *self.ONLY, "--json", **options)
            self.assertEqual([(item["renderedDescription"], item["renderedAcceptance"]) for item in data.json()["issues"]], [(expected, expected)] * 2)
            for word in LEAK_WORDS:
                self.assertNotIn(word, data.out + data.err)
        self.assertEqual(set(self.gateway.calls), {("get_issues_by_key", ("PROJ-1", "PROJ-2"), ("renderedFields",))})
        shown_to_people = self.run_cli("issue", "PROJ-1", "PROJ-2", "--rendered", "--with-users", *self.ONLY, "--json").json()
        self.assertTrue(all(shown in item["renderedDescription"] for item in shown_to_people["issues"]))


class HistoryTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.changelogs["PROJ-1"] = Changelog(
            (
                HistoryEntry("2026-10-02T08:00:00.000+0200", (FieldChange("status", "In Progress", "Done"),)),
                HistoryEntry("2026-10-01T08:00:00.000+0200", (FieldChange("assignee", "", "jdoe"), FieldChange("status", "To Do", "In Progress"))),
            ),
            2,
        )

    def test_one_line_per_status_change_oldest_first(self):
        out = self.run_cli("issue", "PROJ-1", "--history", "--only", "summary").out
        self.assertEqual(
            out,
            "PROJ-1\nSummary: Export the report\n\nHistory (status changes, oldest first):\n"
            "  2026-10-01T08:00:00.000+0200  To Do -> In Progress\n  2026-10-02T08:00:00.000+0200  In Progress -> Done\n",
        )

    def test_the_json_has_the_changes_without_people(self):
        data = self.run_cli("issue", "PROJ-1", "--history", "--json").json()
        self.assertEqual(
            data["history"],
            [
                {"created": "2026-10-01T08:00:00.000+0200", "from": "To Do", "to": "In Progress"},
                {"created": "2026-10-02T08:00:00.000+0200", "from": "In Progress", "to": "Done"},
            ],
        )
        self.assertTrue(data["historyComplete"])
        self.assertNotIn("jdoe", json.dumps(data["history"]))
        self.assertNotIn("history", self.run_cli("issue", "PROJ-1", "--json").json())

    def test_an_issue_that_never_changed_status(self):
        self.assertIn("History: no status changes.", self.run_cli("issue", "PROJ-2", "--history").out)
        self.assertEqual(self.run_cli("issue", "PROJ-2", "--history", "--json").json()["history"], [])

    def test_a_history_that_came_only_in_part_says_so_in_both_forms(self):
        self.gateway.changelogs["PROJ-1"] = dataclasses.replace(self.gateway.changelogs["PROJ-1"], total=130)
        result = self.run_cli("issue", "PROJ-1", "--history")
        self.assertIn("(Jira sent only part of the history, and Data Center cannot page it: older or newer changes may be missing.)", result.out)
        self.assertIn("[jira] Jira sent only part of the history of PROJ-1", result.err)
        as_json = self.run_cli("issue", "PROJ-1", "--history", "--json")
        self.assertFalse(as_json.json()["historyComplete"])
        self.assertIn("Jira sent only part of the history of PROJ-1", as_json.err)
        self.assertEqual(self.run_cli("issue", "PROJ-2", "--history").err, "")

    def test_it_is_one_request_with_the_expansion_and_it_goes_with_only(self):
        data = self.run_cli("issue", "PROJ-1", "--only", "status", "--history", "--json").json()
        self.assertEqual(sorted(data), ["history", "historyComplete", "key", "status"])
        self.assertEqual(self.gateway.calls, [("get_issue_with", "PROJ-1", ("changelog",))])

    def test_the_whole_view_ends_with_the_history(self):
        out = self.run_cli("issue", "PROJ-1", "--history").out
        self.assertTrue(out.index("Comments: 1") < out.index("History (status changes"))


class SeveralKeysTest(Fixture):
    def test_the_text_has_every_issue_in_the_order_of_the_keys(self):
        result = self.run_cli("issue", "PROJ-3", "PROJ-1", "--only", "summary")
        self.assertEqual(result.out, "PROJ-3\nSummary: Finished story\n\n=====\n\nPROJ-1\nSummary: Export the report\n")
        self.assertEqual(result.code, 0)

    def test_the_json_is_a_count_the_issues_and_the_errors(self):
        data = self.run_cli("issue", "PROJ-3", "PROJ-1", "--only", "summary", "--json").json()
        self.assertEqual(data, {"count": 2, "issues": [{"key": "PROJ-3", "summary": "Finished story"}, {"key": "PROJ-1", "summary": "Export the report"}], "errors": []})

    def test_one_search_for_all_keys(self):
        self.run_cli("issue", "PROJ-1", "PROJ-2", "PROJ-3")
        self.assertEqual(self.gateway.calls, [("get_issues_by_key", ("PROJ-1", "PROJ-2", "PROJ-3"), ())])

    def test_keys_by_commas_or_in_one_argument(self):
        for argv in (("PROJ-1,PROJ-2",), ("PROJ-1, PROJ-2",), ("PROJ-1", "PROJ-2"), ("proj-1 proj-2",)):
            with self.subTest(argv=argv):
                gateway = FakeGateway()
                data = run_cli("issue", *argv, "--only", "status", "--json", gateway=gateway).json()
                self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-1", "PROJ-2"])

    def test_a_missing_key_has_its_own_entry_and_the_exit_code_is_4(self):
        result = self.run_cli("issue", "PROJ-1", "PROJ-999", "PROJ-2", "--only", "summary", "--json")
        self.assertEqual(result.code, 4)
        data = result.json()
        self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-1", "PROJ-2"])
        self.assertEqual(data["count"], 2)
        (error,) = data["errors"]
        self.assertEqual(sorted(error), ["code", "key", "message"])
        self.assertEqual((error["key"], error["code"]), ("PROJ-999", "NOT_FOUND"))
        self.assertIn("PROJ-999", error["message"])
        self.assertEqual(result.err, "")

    def test_a_missing_key_has_a_line_in_the_text_where_it_stands(self):
        result = self.run_cli("issue", "PROJ-1", "PROJ-999", "PROJ-2", "--only", "summary")
        self.assertEqual(result.code, 4)
        self.assertEqual(
            result.out.split("\n\n=====\n\n"),
            ["PROJ-1\nSummary: Export the report", "PROJ-999  [NOT_FOUND] Not found: GET /issue/PROJ-999 - Issue Does Not Exist", "PROJ-2\nSummary: Fix the thing\n"],
        )

    def test_the_fallback_asks_for_each_key_on_its_own(self):
        self.run_cli("issue", "PROJ-1", "PROJ-999")
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_issues_by_key", "get_issue_with", "get_issue_with"])

    def test_every_key_missing(self):
        result = self.run_cli("issue", "PROJ-998", "PROJ-999", "--json")
        self.assertEqual(result.code, 4)
        self.assertEqual((result.json()["count"], result.json()["issues"], len(result.json()["errors"])), (0, [], 2))

    def test_the_whole_view_of_each_issue_is_unchanged(self):
        out = self.run_cli("issue", "PROJ-1", "PROJ-2").out
        first, second = out.split("\n\n=====\n\n")
        self.assertEqual(first + "\n", DEFAULT_TEXT)
        self.assertTrue(second.startswith("PROJ-2  [Bug | In Progress | High]\nFix the thing"))

    def test_the_options_apply_to_every_issue(self):
        out = self.run_cli("issue", "PROJ-1", "PROJ-2", "--comments", "--with-users", "--only", "status").out
        self.assertEqual((out.count("Assignee: Jane Doe"), out.count("--- #10 2026-10-02T10:00:00.000+0200 Jane Doe")), (2, 2))

    def test_the_expansions_go_into_the_one_search(self):
        self.run_cli("issue", "PROJ-1", "PROJ-2", "--history")
        self.assertEqual(self.gateway.calls, [("get_issues_by_key", ("PROJ-1", "PROJ-2"), ("changelog",))])

    def test_people_stay_out_of_every_issue(self):
        out = self.run_cli("issue", "PROJ-1", "PROJ-2", "--comments").out
        for personal in ("Jane", "jdoe", "jane.doe"):
            self.assertNotIn(personal, out)

    def test_raw_is_for_one_issue(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "PROJ-2", "--raw").code, 2)

    def test_a_key_given_twice_is_one_issue_in_a_list_like_two_keys_are(self):
        twice = self.run_cli("issue", "PROJ-1", "PROJ-1", "--only", "status", "--json").json()
        two = self.run_cli("issue", "PROJ-1", "PROJ-2", "--only", "status", "--json").json()
        self.assertEqual(sorted(twice), sorted(two))
        self.assertEqual((twice["count"], [item["key"] for item in twice["issues"]], twice["errors"]), (1, ["PROJ-1"], []))
        for argv in (("proj-1,PROJ-1",), ("PROJ-1 PROJ-1",), ("PROJ-1", "proj-1")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli("issue", *argv, "--only", "status", "--json").json(), twice)

    def test_a_key_given_twice_prints_the_issue_once_in_the_text(self):
        self.assertEqual(self.run_cli("issue", "PROJ-1", "PROJ-1").out, self.run_cli("issue", "PROJ-1").out)

    def test_a_missing_key_given_twice_is_a_list_with_an_error_entry_like_two_missing_keys(self):
        twice = self.run_cli("issue", "PROJ-999", "PROJ-999", "--json")
        two = self.run_cli("issue", "PROJ-999", "PROJ-998", "--json")
        self.assertEqual((twice.code, two.code), (4, 4))
        self.assertEqual((twice.json()["count"], twice.json()["issues"], [item["key"] for item in twice.json()["errors"]]), (0, [], ["PROJ-999"]))
        self.assertEqual(sorted(twice.json()), sorted(two.json()))
        text = self.run_cli("issue", "PROJ-999", "PROJ-999")
        self.assertEqual((text.code, text.err), (4, ""))
        self.assertIn("PROJ-999  [NOT_FOUND]", text.out)
        single = self.run_cli("issue", "PROJ-999", "--json")  # one key, given once: an error, as it always was
        self.assertEqual((single.code, single.out, single.error()["code"]), (4, "", "NOT_FOUND"))

    def test_a_key_given_twice_is_two_keys_for_raw_the_outline_and_a_section(self):
        for options in (("--raw",), ("--outline",), ("--section", "Goal")):
            with self.subTest(options=options):
                result = self.run_cli("issue", "PROJ-1", "PROJ-1", *options)
                self.assertEqual((result.code, result.out), (2, ""))
        self.assertEqual(self.gateway.calls, [])

    def test_a_server_that_is_down_is_an_error(self):
        self.gateway.errors["get_issues_by_key"] = UnavailableError("Cannot reach Jira.", hint="Check the VPN.")
        result = self.run_cli("issue", "PROJ-1", "PROJ-2")
        self.assertEqual((result.code, result.err), (7, "ERROR: Cannot reach Jira.\nHint: Check the VPN.\n"))


class QueryInputTest(Fixture):
    """`--jql @FILE`, `--jql -` and the text of a search from a file: the forms that survive Windows PowerShell 5.1."""

    JQL = 'project = "PROJ" AND "Epic Link" = PROJ-9 ORDER BY key'

    def setUp(self):
        super().setUp()
        self.sent = []

        def answer(jql):
            self.sent.append(jql)
            return [self.gateway.issues["PROJ-1"]]

        self.gateway.search_handler = answer

    def files(self, name="q.jql", data=None):
        files = MemoryFiles()
        files.write_bytes(name, self.JQL.encode("utf-8") if data is None else data)
        return files

    def test_a_query_from_a_file_reaches_jira_with_its_double_quotes(self):
        result = self.run_cli("issues", "--jql", "@q.jql", "--json", files=self.files())
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.sent, [self.JQL])
        self.assertEqual(result.json()["jql"], self.JQL)

    def test_the_file_may_be_utf8_with_a_mark_or_utf16_as_powershell_writes_it(self):
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding):
                data = self.JQL.encode(encoding)
                if encoding in ("utf-16-le", "utf-16-be"):
                    data = (b"\xff\xfe" if encoding.endswith("le") else b"\xfe\xff") + data
                self.sent.clear()
                result = self.run_cli("issues", "--jql", "@q.jql", "--json", files=self.files(data=data))
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.sent, [self.JQL])

    def test_a_query_with_a_line_end_at_the_end_is_cleaned(self):
        self.run_cli("issues", "--jql", "@q.jql", "--json", files=self.files(data=(self.JQL + "\r\n").encode("utf-16")))
        self.assertEqual(self.sent, [self.JQL])

    def test_a_query_from_standard_input(self):
        result = self.run_cli("issues", "--jql", "-", "--json", stdin=self.JQL + "\n")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.sent, [self.JQL])
        self.sent.clear()
        self.run_cli("issues", "--jql", "@-", "--json", stdin=self.JQL)
        self.assertEqual(self.sent, [self.JQL])

    def test_an_inline_query_is_as_before(self):
        self.run_cli("issues", "--jql", "status = Done", "--json")
        self.assertEqual(self.sent, ["status = Done"])

    def test_an_empty_file_or_input_is_a_usage_error(self):
        self.assertEqual(self.run_cli("issues", "--jql", "@q.jql", files=self.files(data=b"  \r\n")).code, 2)
        self.assertEqual(self.run_cli("issues", "--jql", "-", stdin="").code, 2)
        self.assertEqual(self.sent, [])

    def test_a_file_that_does_not_exist_is_an_error_that_names_it(self):
        result = self.run_cli("issues", "--jql", "@missing.jql", files=MemoryFiles())
        self.assertEqual(result.code, 1)
        self.assertIn("missing.jql", result.err)

    def test_the_note_about_a_query_without_a_project_looks_at_the_text_of_the_file(self):
        self.assertNotIn("every project", self.run_cli("issues", "--jql", "@q.jql", files=self.files()).err)
        self.assertIn("every project", self.run_cli("issues", "--jql", "@q.jql", files=self.files(data=b"status = Done")).err)

    def test_search_takes_a_condition_from_a_file(self):
        result = self.run_cli("search", "audit", "--jql", "@q.jql", "--json", files=self.files(data=b'priority = "High"'))
        self.assertEqual(result.code, 0, result.err)
        self.assertIn('AND (priority = "High")', self.sent[0])

    def test_search_takes_its_text_from_a_file(self):
        files = self.files("question.txt", 'how do I export the "monthly report"?'.encode("utf-16"))
        result = self.run_cli("search", "@question.txt", "--json", files=files)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json()["terms"], ["export", '"monthly report"'])

    def test_search_takes_its_text_from_standard_input(self):
        result = self.run_cli("search", "-", "--json", stdin='audit "append only"\n')
        self.assertEqual(result.json()["terms"], ["audit", '"append only"'])

    def test_a_search_text_that_is_not_a_file_is_as_before(self):
        self.assertEqual(self.run_cli("search", "audit", "trail", "--json").json()["terms"], ["audit", "trail"])

    def test_phrases_without_double_quotes(self):
        result = self.run_cli("search", "roles", "--phrase", "audit trail", "--phrase", "append only", "--json", "--match", "all")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(data["terms"], ['"audit trail"', '"append only"', "roles"])
        self.assertIn('text ~ "\\"audit trail\\""', data["jql"])
        self.assertIn('text ~ "\\"append only\\""', data["jql"])

    def test_a_phrase_alone_is_a_search(self):
        self.assertEqual(self.run_cli("search", "--phrase", "audit trail", "--json", "--match", "all").code, 0)
        self.assertEqual(self.run_cli("search", "--phrase", " ").code, 2)


class ListOutputTest(Fixture):
    def test_issues_default_table_and_json_are_unchanged(self):
        lines = self.run_cli("issues", "--limit", "5").out.splitlines()
        self.assertEqual(lines[0].split(), ["KEY", "TYPE", "STATUS", "ASSIGNEE", "SUMMARY"])
        data = self.run_cli("issues", "--json").json()
        self.assertEqual(sorted(data), ["count", "issues", "jql", "total"])
        self.assertEqual(list(data["issues"][0]), ["key", "summary", "issueType", "status", "priority", "assignee", "created", "updated"])

    def test_select_keeps_only_the_named_keys_in_json(self):
        data = self.run_cli("issues", "--all", "--json", "--select", "key,status.name,summary").json()
        self.assertEqual(data["total"], 6)
        self.assertEqual(data["issues"][0], {"key": "PROJ-1", "status": {"name": "To Do"}, "summary": "Export the report"})

    def test_select_alone_prints_a_table_of_those_columns(self):
        out = self.run_cli("issues", "--select", "key,status.name").out.splitlines()
        self.assertEqual(out[0].split(), ["key", "status.name"])
        self.assertEqual(out[1].split(), ["PROJ-1", "To", "Do"])

    def test_format_md_and_tsv_use_the_columns_of_the_table(self):
        md = self.run_cli("issues", "--format", "md", "--no-assignee").out.splitlines()
        self.assertEqual(md[0], "| KEY | TYPE | STATUS | SUMMARY |")
        self.assertEqual(md[2], "| PROJ-1 | Story | To Do | Export the report |")
        tsv = self.run_cli("issues", "--format", "tsv").out.splitlines()
        self.assertEqual(tsv[0], "KEY\tTYPE\tSTATUS\tASSIGNEE\tSUMMARY")
        self.assertEqual(tsv[1], "PROJ-1\tStory\tTo Do\tJane Doe\tExport the report")

    def test_the_assignee_column_stays_out_when_people_are_hidden(self):
        out = self.run_cli("issues", "--format", "tsv", settings=settings(hide_users=True)).out
        self.assertEqual(out.splitlines()[0], "KEY\tTYPE\tSTATUS\tSUMMARY")
        self.assertNotIn("Jane", out)

    def test_an_unknown_name_in_select_is_a_usage_error_that_lists_the_valid_ones(self):
        result = self.run_cli("issues", "--json", "--select", "key,nope")
        self.assertEqual((result.code, result.out), (2, ""))
        self.assertIn("nope", result.err)
        self.assertIn("status.name", result.err)

    def test_json_and_a_format_exclude_each_other(self):
        self.assertEqual(self.run_cli("issues", "--json", "--format", "md").code, 2)

    def test_raw_has_no_columns(self):
        self.assertEqual(self.run_cli("issues", "--raw", "--select", "key").code, 2)
        self.assertEqual(self.run_cli("issues", "--raw", "--format", "md").code, 2)

    def test_the_rest_note_comes_with_a_table_but_not_with_json(self):
        self.assertIn("Showing 5 of 6", self.run_cli("issues", "--limit", "5", "--format", "md").err)
        self.assertEqual(self.run_cli("issues", "--limit", "5", "--json", "--select", "key").err, "")

    def test_search_select_and_format(self):
        data = self.run_cli("search", "--status", "To Do", "--json", "--select", "key,epicKey").json()
        self.assertEqual(sorted(data), ["count", "hits", "ignored", "jql", "match", "notes", "relaxed", "scope", "startAt", "terms", "total", "truncated"])
        self.assertEqual(list(data["hits"][0]), ["key", "epicKey"])
        md = self.run_cli("search", "--status", "To Do", "--format", "md").out.splitlines()
        self.assertEqual(md[0], "| KEY | TYPE | STATUS | EPIC | SUMMARY |")
        self.assertEqual(md[2], "| PROJ-1 | Story | To Do | PROJ-9 | Export the report |")

    def test_search_prints_nothing_when_nothing_is_found_unless_a_format_is_asked_for(self):
        self.gateway.search_handler = lambda jql: []
        result = self.run_cli("search", "alpha")
        self.assertEqual((result.code, result.out), (0, ""))
        self.assertEqual(self.run_cli("search", "alpha", "--format", "tsv").out, "KEY\tTYPE\tSTATUS\tEPIC\tSUMMARY\n")

    def test_search_with_snippets_keeps_its_own_text(self):
        out = self.run_cli("search", "--status", "To Do", "--snippets", "1").out
        self.assertIn("PROJ-1  Story | To Do | High  Export the report", out)

    def test_fields(self):
        data = self.run_cli("fields", "--custom", "--json").json()
        self.assertEqual(data[0], {"id": "customfield_10004", "name": "Acceptance criteria (DoD)", "custom": True})
        self.assertEqual(self.run_cli("fields", "--custom", "--json", "--select", "id").json()[0], {"id": "customfield_10004"})
        tsv = self.run_cli("fields", "--search", "epic", "--format", "tsv").out
        self.assertEqual(tsv, "ID\tNAME\tCUSTOM\ncustomfield_10002\tEpic Link\ttrue\n")
        self.assertEqual(self.run_cli("fields", "--search", "epic").out.splitlines()[1].split(), ["customfield_10002", "Epic", "Link", "yes"])

    def test_the_commands_describe_the_new_options(self):
        catalog = {item["name"]: item for item in self.run_cli("commands", "--json").json()["commands"]}
        for name in ("issues", "search", "fields", "filters"):
            names = {arg["names"][0] for arg in catalog[name]["arguments"]}
            self.assertTrue({"--select", "--format"} <= names, name)
        self.assertTrue({"--filter", "--jql"} <= {arg["names"][0] for arg in catalog["issues"]["arguments"]})
        self.assertTrue({"--filter", "--jql", "--phrase"} <= {arg["names"][0] for arg in catalog["search"]["arguments"]})
        self.assertTrue(all(not catalog[name]["changesData"] for name in ("issue", "issues", "search", "fields", "filters", "filter")))


class FilterOptionTest(Fixture):
    def setUp(self):
        super().setUp()
        self.sent = []

        def answer(jql):
            self.sent.append(jql)
            return [self.gateway.issues["PROJ-1"]]

        self.gateway.search_handler = answer

    def test_issues_runs_a_filter(self):
        result = self.run_cli("issues", "--filter", "10000", "--json")
        self.assertEqual((result.code, self.sent, result.json()["jql"]), (0, ["filter = 10000"], "filter = 10000"))
        self.assertEqual(result.err, "")  # a filter has its own scope: no note about the project

    def test_a_jql_narrows_the_filter(self):
        self.run_cli("issues", "--filter", "10000", "--jql", "status = Done ORDER BY key", "--json")
        self.assertEqual(self.sent, ["filter = 10000 AND (status = Done) ORDER BY key"])

    def test_an_id_that_is_not_a_number_is_refused_with_a_hint(self):
        result = self.run_cli("issues", "--filter", "My filter")
        self.assertEqual(result.code, 1)
        self.assertIn("jira filters", result.err)
        self.assertEqual(self.sent, [])

    def test_mine_and_a_filter_exclude_each_other(self):
        self.assertEqual(self.run_cli("issues", "--filter", "10000", "--mine").code, 1)

    def test_search_adds_the_filter_to_the_other_conditions(self):
        result = self.run_cli("search", "audit", "--filter", "10000", "--status", "To Do", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.sent[0], 'project = "PROJ" AND filter = 10000 AND text ~ "audit" AND status = "To Do"')
        self.assertTrue(any("Filter 10000 is searched inside project PROJ only" in note for note in result.json()["notes"]))
        self.assertIn("Filter 10000 is searched inside project PROJ only", result.err)

    def test_search_in_all_projects_runs_the_whole_filter(self):
        self.run_cli("search", "--filter", "10000", "--all-projects", "--json")
        self.assertEqual(self.sent[0], "filter = 10000 ORDER BY updated DESC")


class FiltersCommandTest(Fixture):
    def setUp(self):
        super().setUp()
        owner = User("jdoe", "Jane Doe", "jane.doe@example.com")
        self.gateway.saved_filters = {
            "10000": SavedFilter("10000", "Open bugs", "Bugs that are open", 'project = "PROJ" AND type = Bug', True, owner, "https://jira.example.com/rest/api/2/search?jql=x", "https://jira.example.com/issues/?filter=10000"),
            "10001": SavedFilter("10001", "Mine", "", "assignee = currentUser()", True, owner, "https://jira.example.com/rest/api/2/search?jql=y", "https://jira.example.com/issues/?filter=10001"),
            "10002": SavedFilter("10002", "Hidden gem", "", "project = DEMO", False, owner, "", ""),
        }

    def test_the_favourites_as_a_table(self):
        lines = self.run_cli("filters").out.splitlines()
        self.assertEqual(lines, ["ID     NAME       JQL", '10000  Open bugs  project = "PROJ" AND type = Bug', "10001  Mine       assignee = currentUser()"])
        self.assertNotIn("Jane", "\n".join(lines))

    def test_the_json_is_a_list_with_no_owner_unless_asked_for(self):
        data = self.run_cli("filters", "--json").json()
        self.assertEqual([item["id"] for item in data], ["10000", "10001"])
        self.assertEqual(data[0], {"id": "10000", "name": "Open bugs", "description": "Bugs that are open", "jql": 'project = "PROJ" AND type = Bug', "favourite": True, "owner": None})

    def test_the_owner_with_users(self):
        data = self.run_cli("filters", "--json", "--with-users").json()
        self.assertEqual(data[0]["owner"], {"name": "jdoe", "displayName": "Jane Doe", "email": "jane.doe@example.com"})
        self.assertEqual(self.run_cli("filters", "--with-users").out.splitlines()[0].split(), ["ID", "NAME", "OWNER", "JQL"])
        self.assertIn("Jane Doe", self.run_cli("filters", "--with-users").out)

    def test_the_policy_refuses_the_owner(self):
        self.assertEqual(self.run_cli("filters", "--with-users", settings=settings(hide_users=True)).code, 1)

    def test_select_and_format(self):
        self.assertEqual(self.run_cli("filters", "--json", "--select", "id,name").json(), [{"id": "10000", "name": "Open bugs"}, {"id": "10001", "name": "Mine"}])
        self.assertEqual(self.run_cli("filters", "--format", "tsv", "--select", "id,jql").out.splitlines()[1], '10000\tproject = "PROJ" AND type = Bug')
        self.assertEqual(self.run_cli("filters", "--format", "md").out.splitlines()[0], "| ID | NAME | JQL |")
        self.assertEqual(self.run_cli("filters", "--json", "--select", "nope").code, 2)

    def test_no_favourites_says_what_to_do(self):
        self.gateway.saved_filters = {}
        result = self.run_cli("filters")
        self.assertEqual(result.out.strip(), "ID  NAME  JQL")
        self.assertIn("no favourite filters", result.err)
        self.assertEqual(self.run_cli("filters", "--json").json(), [])

    def test_one_filter(self):
        out = self.run_cli("filter", "10000").out
        self.assertEqual(
            out,
            "10000  Open bugs\nFavourite:   yes\nDescription: Bugs that are open\nJQL:         project = \"PROJ\" AND type = Bug\n"
            "Search URL:  https://jira.example.com/rest/api/2/search?jql=x\nOpen:        https://jira.example.com/issues/?filter=10000\n"
            "Run it:      jira issues --filter 10000\n",
        )

    def test_one_filter_in_json_has_the_addresses(self):
        data = self.run_cli("filter", "10002", "--json").json()
        self.assertEqual(
            data,
            {"id": "10002", "name": "Hidden gem", "description": "", "jql": "project = DEMO", "favourite": False, "owner": None, "searchUrl": "", "viewUrl": ""},
        )

    def test_one_filter_with_its_owner(self):
        self.assertIn("Owner:       Jane Doe", self.run_cli("filter", "10000", "--with-users").out)
        self.assertEqual(self.run_cli("filter", "10000", "--with-users", "--json").json()["owner"]["name"], "jdoe")
        self.assertNotIn("Jane", self.run_cli("filter", "10000").out)

    def test_an_unknown_filter_is_not_found(self):
        result = self.run_cli("filter", "99999", "--json")
        self.assertEqual((result.code, result.error()["code"]), (4, "NOT_FOUND"))
        self.assertIn("jira filters", result.error()["hint"])

    def test_an_id_that_is_not_a_number(self):
        self.assertEqual(self.run_cli("filter", "My filter").code, 1)
        self.assertEqual(self.run_cli("filter", "abc").code, 1)
        self.assertEqual(self.run_cli("filter", "1", "2").code, 2)  # one id only
        self.assertEqual(self.run_cli("filter").code, 2)
        self.assertEqual(self.gateway.calls, [])

    def test_the_text_is_redacted_without_people(self):
        self.gateway.saved_filters["10000"] = dataclasses.replace(self.gateway.saved_filters["10000"], jql='reporter = "jane.doe@example.com"')
        self.assertNotIn("jane.doe", self.run_cli("filter", "10000").out)
        self.assertNotIn("jane.doe", self.run_cli("filters").out)
        self.assertNotIn("jane.doe", self.run_cli("filter", "10000", "--json").out)


class FilterPeopleTest(Fixture):
    """The people that the JQL of a saved filter names stay out of `filters` and `filter`, and so does the address that holds the JQL."""

    ADDRESS = (
        "https://jira.example.com/rest/api/2/search?jql=assignee%20%3D%20jdoe%20AND%20reporter%20in%20(%22asmith%22%2C%22jane.doe%22)"
        "%20AND%20watcher%20%3D%20%22Alex%20Smith%22%20ORDER%20BY%20created"
    )
    CLEAN = 'project = "PROJ" AND assignee = currentUser() AND resolution = Unresolved'
    CLEAN_ADDRESS = "https://jira.example.com/rest/api/2/search?jql=project%20%3D%20PROJ"

    def setUp(self):
        super().setUp()
        owner = User("jdoe", "Jane Doe", "jane.doe@example.com")
        self.gateway.saved_filters = {
            "10000": SavedFilter("10000", "Theirs", "", REVIEW_JQL, True, owner, self.ADDRESS, "https://jira.example.com/issues/?filter=10000"),
            "10001": SavedFilter("10001", "Open", "", self.CLEAN, True, owner, self.CLEAN_ADDRESS, "https://jira.example.com/issues/?filter=10001"),
        }

    def both(self, *argv):
        """The same call without a policy and under `JIRA_HIDE_USERS=true`: nobody is named in either."""
        return {"no policy": self.run_cli(*argv), "the policy": self.run_cli(*argv, settings=settings(hide_users=True))}

    @staticmethod
    def lines_of(out):
        """The `Label: value` lines of `jira filter` as a dict, whatever the width of the label column."""
        return {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in out.splitlines()[1:]}

    def test_the_list_in_text_names_nobody_and_says_so(self):
        for name, result in self.both("filters").items():
            with self.subTest(name):
                self.assertEqual(result.code, 0)
                self.assertEqual(result.out.splitlines()[1:], [f"10000  Theirs  {REVIEW_SHOWN}", f"10001  Open    {self.CLEAN}"])
                for person in PEOPLE:
                    self.assertNotIn(person, result.out + result.err)
                self.assertIn("The JQL of 1 filter names people or personal data: [user] or [email] stands in their place.", result.err)
                self.assertIn("jira issues --filter ID", result.err)

    def test_the_list_in_json_names_nobody_and_stdout_stays_json(self):
        for name, result in self.both("filters", "--json").items():
            with self.subTest(name):
                data = result.json()
                self.assertEqual([item["jql"] for item in data], [REVIEW_SHOWN, self.CLEAN])
                self.assertEqual(data[0], {"id": "10000", "name": "Theirs", "description": "", "jql": REVIEW_SHOWN, "favourite": True, "owner": None})
                for person in PEOPLE:
                    self.assertNotIn(person, result.out)

    def test_select_and_format_show_the_same_jql(self):
        self.assertEqual(self.run_cli("filters", "--json", "--select", "id,jql").json()[0], {"id": "10000", "jql": REVIEW_SHOWN})
        self.assertEqual(self.run_cli("filters", "--format", "tsv", "--select", "id,jql").out.splitlines()[1], f"10000\t{REVIEW_SHOWN}")
        self.assertIn(REVIEW_SHOWN, self.run_cli("filters", "--format", "md").out)

    def test_one_filter_in_text_has_no_address_that_names_people(self):
        for name, result in self.both("filter", "10000").items():
            with self.subTest(name):
                self.assertEqual(result.out.splitlines()[0], "10000  Theirs")
                self.assertEqual(
                    self.lines_of(result.out),
                    {
                        "Favourite": "yes",
                        "JQL": REVIEW_SHOWN,
                        "Search URL": "(left out: the JQL names people)",
                        "Open": "https://jira.example.com/issues/?filter=10000",
                        "Run it": "jira issues --filter 10000",
                    },
                )
                for person in PEOPLE:
                    self.assertNotIn(person, result.out + result.err)
                self.assertIn("The JQL names people or personal data: [user] or [email] stands in their place, and the search address is left out.", result.err)
                self.assertIn("`jira issues --filter 10000` still runs the filter as it is.", result.err)

    def test_one_filter_in_json_has_an_empty_address(self):
        for name, result in self.both("filter", "10000", "--json").items():
            with self.subTest(name):
                self.assertEqual(
                    result.json(),
                    {
                        "id": "10000",
                        "name": "Theirs",
                        "description": "",
                        "jql": REVIEW_SHOWN,
                        "favourite": True,
                        "owner": None,
                        "searchUrl": "",
                        "viewUrl": "https://jira.example.com/issues/?filter=10000",
                    },
                )
                self.assertIn("the search address is left out", result.err)
                for person in PEOPLE:
                    self.assertNotIn(person, result.out)

    def test_a_filter_that_names_nobody_prints_as_before_and_says_nothing(self):
        for name, result in self.both("filter", "10001").items():
            with self.subTest(name):
                fields = self.lines_of(result.out)
                self.assertEqual((fields["JQL"], fields["Search URL"]), (self.CLEAN, self.CLEAN_ADDRESS))
                self.assertEqual(result.err, "")
        self.assertEqual(self.run_cli("filter", "10001", "--json").json()["searchUrl"], self.CLEAN_ADDRESS)
        self.assertEqual(self.run_cli("filters", "--json", "--select", "jql").json()[1], {"jql": self.CLEAN})

    def test_with_users_the_filter_is_as_it_is(self):
        result = self.run_cli("filter", "10000", "--with-users")
        fields = self.lines_of(result.out)
        self.assertEqual((fields["JQL"], fields["Owner"], fields["Search URL"]), (REVIEW_JQL, "Jane Doe", self.ADDRESS))
        self.assertEqual(result.err, "")
        data = self.run_cli("filter", "10000", "--with-users", "--json").json()
        self.assertEqual((data["jql"], data["searchUrl"], data["owner"]["name"]), (REVIEW_JQL, self.ADDRESS, "jdoe"))
        listed = self.run_cli("filters", "--with-users", "--json")
        self.assertEqual((listed.json()[0]["jql"], listed.err), (REVIEW_JQL, ""))

    def test_the_policy_still_refuses_with_users(self):
        for argv in (("filters", "--with-users"), ("filter", "10000", "--with-users", "--json")):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv, settings=settings(hide_users=True))
                self.assertEqual((result.code, result.out), (1, ""))
                self.assertNotIn("jdoe", result.err)
        self.assertEqual(self.gateway.calls, [])


class BranchTest(Fixture):
    SUMMARY = "Ask [~jdoe] or jane.doe@example.com about the Jane Doe report"
    NAME = "feature/PROJ-1-ask-user-or-email-about-the-jane-doe"

    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", summary=self.SUMMARY)

    def test_the_name_has_no_mention_and_no_address_in_it(self):
        result = self.run_cli("branch", "PROJ-1")
        self.assertEqual((result.code, result.out, result.err), (0, self.NAME + "\n", ""))
        self.assertEqual(self.run_cli("branch", "PROJ-1", "--json").json(), {"key": "PROJ-1", "branch": self.NAME})
        for personal in ("jdoe", "jane.doe", "example"):
            self.assertNotIn(personal, result.out)

    def test_the_policy_makes_no_difference(self):
        self.assertEqual(self.run_cli("branch", "PROJ-1", settings=settings(hide_users=True)).out, self.NAME + "\n")

    def test_it_is_made_of_the_summary_that_issue_shows(self):
        shown = self.run_cli("issue", "PROJ-1", "--only", "summary", "--json").json()["summary"]
        self.assertEqual(shown, "Ask [~user] or [email] about the Jane Doe report")
        self.assertEqual(self.run_cli("branch", "PROJ-1").out.strip().split("PROJ-1-")[1], "ask-user-or-email-about-the-jane-doe")

    def test_the_type_is_as_before(self):
        self.assertEqual(self.run_cli("branch", "PROJ-1", "--type", "hotfix").out, self.NAME.replace("feature", "hotfix") + "\n")


if __name__ == "__main__":
    unittest.main()
