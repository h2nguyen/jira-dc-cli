"""The domain rules: pure functions and data, no ports."""
import time
import unittest
from dataclasses import replace
from datetime import timezone

from jira_dc_cli.domain import jql
from jira_dc_cli.domain.backlog import build_backlog_dump
from jira_dc_cli.domain.branching import branch_name, slugify
from jira_dc_cli.domain.drafts import (
    IssueChanges,
    IssueDraft,
    parse_json_object,
    resolve_issue_type,
    split_list,
    validate_create_payload,
    validate_update_payload,
)
from jira_dc_cli.domain.errors import (
    EXIT_CODES,
    ConflictError,
    JiraCliError,
    UsageError,
    ValidationError,
)
from jira_dc_cli.domain.fields import resolve_field_ids
from jira_dc_cli.domain.frontmatter import parse_csv, parse_markdown_ticket
from jira_dc_cli.domain.links import find_relation, link_ends, relation_phrase, relation_text, resolve_link_type
from jira_dc_cli.domain.model import (
    FieldInfo,
    IssueLink,
    IssueRef,
    IssueTypeInfo,
    LinkType,
    Sprint,
    Status,
    Transition,
    parse_issue_key,
    parse_project_key,
)
from jira_dc_cli.domain.payloads import comment_payload, link_payload, transition_payload
from jira_dc_cli.domain.privacy import hide_users, holds_placeholder, redact_text, strip_users
from jira_dc_cli.domain.reporting import NO_EPIC, build_report
from jira_dc_cli.domain.timeutil import ensure_unchanged_since, parse_timestamp
from jira_dc_cli.domain.transitions import pick_transition
from tests.support.fakes import STATUS, default_issues, sample_issue


class KeysTest(unittest.TestCase):
    def test_issue_key_is_trimmed_and_upper_cased(self):
        self.assertEqual(parse_issue_key(" proj-12 "), "PROJ-12")

    def test_invalid_issue_keys_are_refused(self):
        for text in ("", "PROJ", "12-PROJ", "PROJ-", "PROJ-1x", "PR OJ-1", "../PROJ-1"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_issue_key(text)

    def test_project_key(self):
        self.assertEqual(parse_project_key("proj"), "PROJ")
        for text in ("", "1A", "A B", "A-B"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_project_key(text)


class JqlTest(unittest.TestCase):
    def test_quote_escapes_quotes_and_backslashes(self):
        self.assertEqual(jql.quote('a"b\\c'), '"a\\"b\\\\c"')

    def test_project_search(self):
        self.assertEqual(jql.issue_search(project="PROJ"), 'project = "PROJ" ORDER BY created DESC')

    def test_mine(self):
        self.assertEqual(
            jql.issue_search(project="PROJ", mine=True),
            'project = "PROJ" AND assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC',
        )
        self.assertTrue(jql.issue_search(mine=True).startswith("assignee = currentUser()"))

    def test_explicit_query_wins_and_cannot_be_mixed_with_mine(self):
        self.assertEqual(jql.issue_search(project="PROJ", jql="status = Done"), "status = Done")
        with self.assertRaises(ValidationError):
            jql.issue_search(jql="x", mine=True)

    def test_a_search_needs_a_project_or_a_query(self):
        with self.assertRaises(UsageError):
            jql.issue_search()

    def test_report_scopes(self):
        query, scope = jql.report_search("PROJ", sprint="active", fix_version="Release 1.0")
        self.assertEqual(query, 'project = "PROJ" AND sprint in openSprints() AND fixVersion = "Release 1.0" ORDER BY key ASC')
        self.assertEqual(scope, 'project PROJ, open sprint(s), fix version "Release 1.0"')
        query, scope = jql.report_search("PROJ", sprint="Sprint 3")
        self.assertIn('sprint = "Sprint 3"', query)
        self.assertEqual(scope, 'project PROJ, sprint "Sprint 3"')

    def test_epics_and_backlog(self):
        self.assertIn("issuetype = Epic", jql.epics_search("PROJ"))
        self.assertEqual(jql.backlog_search("PROJ"), 'project = "PROJ" ORDER BY key ASC')


class BranchingTest(unittest.TestCase):
    def test_slug_is_ascii_and_cut_at_a_word(self):
        self.assertEqual(slugify("Export the report: Ünïcode & more"), "export-the-report-unicode-more")
        long = slugify("Export the report: Unicode and more, with a very long summary to cut")
        self.assertLessEqual(len(long), 40)
        self.assertFalse(long.endswith("-"))

    def test_type_follows_the_issue_type(self):
        self.assertEqual(branch_name("PROJ-2", "Fix the thing", "Bug"), "bugfix/PROJ-2-fix-the-thing")
        self.assertEqual(branch_name("PROJ-1", "Export the report", "Story"), "feature/PROJ-1-export-the-report")

    def test_override_and_validation(self):
        self.assertEqual(branch_name("PROJ-1", "x", "Story", "hotfix"), "hotfix/PROJ-1-x")
        with self.assertRaises(ValidationError):
            branch_name("PROJ-1", "x", "Story", "release")

    def test_summary_without_letters_gives_no_slug(self):
        self.assertEqual(branch_name("PROJ-1", "???", "Story"), "feature/PROJ-1")


class PrivacyTest(unittest.TestCase):
    def test_redacts_mentions_links_and_addresses(self):
        text = "Ask [~jdoe], see https://files.example.com/personal/jane/doc or jane.doe@example.com"
        self.assertEqual(redact_text(text), "Ask [~user], see [personal link] or [email]")

    def test_strip_users_replaces_user_objects_everywhere(self):
        raw = {"fields": {"assignee": {"displayName": "Jane", "emailAddress": "j@example.com", "name": "jdoe"}, "text": "[~jdoe]", "n": [1, {"displayName": "A", "key": "a"}]}}
        stripped = strip_users(raw)
        self.assertEqual(stripped["fields"]["assignee"], {"hidden": True})
        self.assertEqual(stripped["fields"]["text"], "[~user]")
        self.assertEqual(stripped["fields"]["n"], [1, {"hidden": True}])

    def test_strip_users_also_cleans_the_changelog_of_user_fields_and_the_jql_of_a_filter(self):
        history = {"items": [
            {"field": "assignee", "fieldtype": "jira", "from": "jdoe", "fromString": "Jane Doe", "to": "asmith", "toString": "Alex Smith"},
            {"field": "status", "fieldtype": "jira", "from": "1", "fromString": "To Do", "to": "3", "toString": "Done"},
        ]}
        items = strip_users(history)["items"]
        self.assertEqual([items[0][key] for key in ("from", "fromString", "to", "toString")], [None] * 4)
        self.assertEqual((items[1]["fromString"], items[1]["toString"]), ("To Do", "Done"))
        saved = {"id": "10000", "name": "Mine", "jql": 'assignee = jdoe AND reporter in ("asmith") AND status = Open', "searchUrl": "https://jira.example.com/rest/api/2/search?jql=assignee%20%3D%20jdoe"}
        cleaned = strip_users(saved)
        self.assertEqual(cleaned["jql"], "assignee = [user] AND reporter in ([user]) AND status = Open")
        self.assertEqual(cleaned["searchUrl"], "")
        plain = {"jql": "project = PROJ ORDER BY key", "searchUrl": "https://jira.example.com/rest/api/2/search?jql=project%20%3D%20PROJ"}
        self.assertEqual(strip_users(plain), plain)
        by_text = {"jql": 'text ~ "john.doe@example.com" OR summary ~ "[~jdoe]"', "searchUrl": "https://jira.example.com/rest/api/2/search?jql=text%20~%20%22john.doe%40example.com%22"}
        cleaned = strip_users(by_text)
        self.assertEqual(cleaned["jql"], 'text ~ "[email]" OR summary ~ "[~user]"')
        self.assertEqual(cleaned["searchUrl"], "")  # the encoded copy would still hold the address

    def test_hide_users_removes_people_from_an_issue(self):
        hidden = hide_users(sample_issue("PROJ-1"))
        self.assertIsNone(hidden.assignee)
        self.assertIsNone(hidden.reporter)
        self.assertIsNone(hidden.raw)
        self.assertNotIn("jane.doe", hidden.description)
        self.assertTrue(all(comment.author is None for comment in hidden.comments))
        self.assertNotIn("[~jdoe]", hidden.comments[0].body)

    def test_the_names_that_people_give_are_redacted_too(self):
        issue = replace(
            sample_issue("PROJ-1"),
            labels=["owner-a.person@example.test", "backend"],
            components=["Team of jane.doe@example.test"],
            fix_versions=["Release [~jdoe]"],
            sprints=[Sprint("Sprint of jane.doe@example.test", "ACTIVE", 1)],
        )
        hidden = hide_users(issue)
        self.assertEqual((hidden.labels, hidden.components, hidden.fix_versions), (["[email]", "backend"], ["Team of [email]"], ["Release [~user]"]))
        self.assertEqual([(sprint.name, sprint.state, sprint.id) for sprint in hidden.sprints], [("Sprint of [email]", "ACTIVE", 1)])

    def test_what_stands_in_place_of_personal_data_is_recognised(self):
        for text in ("Ask [~user]", "see [personal link]", "write to [email]"):
            self.assertTrue(holds_placeholder(text), text)
        for text in ("", None, "Plain text", "[user]", "[emails]"):
            self.assertFalse(holds_placeholder(text), text)

    def test_the_patterns_stay_as_they_were_for_everyday_text(self):
        self.assertEqual(redact_text("a.b+c@sub.example.org, mailto:x@y.example and [~account:abc-123]"), "[email], mailto:[email] and [~user]")
        self.assertEqual(redact_text("see https://files.example.com/personal/jane_doe_example_com/_layouts/15/doc.aspx?id=1 now"), "see [personal link] now")
        self.assertEqual(redact_text("@ alone, a@b, x@y.z.w"), "@ alone, a@b, [email]")

    def test_a_package_with_a_version_is_no_address(self):
        # An address ends in a top level domain, which begins with a letter; `name@1.2` is a pinned version, and redacting it would stop a push of its text.
        for text in ("lodash@4.17.21", "my-lib@2.1", "service@1.4.0", "release@2.0", "build@2024.10", "pkg@1.0.0-beta.1", "numpy@1.26 and react@18.2.0", "node@20.11.1", "x@2024.10.15.3"):
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), text)

    def test_a_login_on_a_host_address_is_still_redacted(self):
        for text, expected in (
            ("jdoe@192.168.1.1", "[email]"),
            ("ssh jdoe@10.20.30.40 -p 22", "ssh [email] -p 22"),
            ("svc_account@172.16.0.5:8080", "[email]:8080"),
            ("log in as jdoe@10.1.2.3.", "log in as [email]."),
        ):
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), expected)

    def test_a_name_that_ends_in_a_domain_is_redacted(self):
        for text, expected in (
            ("x@1.example", "[email]"),
            ("write to jan@example.com.", "write to [email]."),
            ("jan@bücher.example.de", "[email]"),
            ("user@пример.рф", "[email]"),
            ("someone@example.中国", "[email]"),
            ("a.b@c.d-e.f", "[email]"),
        ):
            with self.subTest(text=text):
                self.assertEqual(redact_text(text), expected)


class HostileTextTest(unittest.TestCase):
    """A text field of Jira may hold 32,767 characters of anything: no pattern may need more than a moment for it."""

    LIMIT = 1.0

    def timed(self, function, text):
        started = time.perf_counter()
        function(text)
        return time.perf_counter() - started

    def test_a_pattern_that_was_only_slow_in_a_long_text_stays_fast_in_a_very_long_one(self):
        # 40,000 characters take 0.7 seconds for the unbounded `https?://\S*/personal/` and 0.015 for the bounded one; here the difference is plain.
        self.assertLess(self.timed(redact_text, "http://" * 17_000), self.LIMIT)
        self.assertEqual(redact_text("see https://files.example.com/personal/jane_doe/x end"), "see [personal link] end")

    def test_redaction_of_long_runs(self):
        size = 40_000
        for name, text in {
            "one long word": "a" * size,
            "a long local part without a domain": "a" * size + "@",
            "a long domain without a dot": "x@" + "a" * size,
            "many at signs": "a@" * (size // 2),
            "a domain of numeric labels": "a@" + "1." * (size // 2),
            "a domain of numeric labels ending in a letter": "a@" + "1." * (size // 2) + "a",
            "a domain of lettered labels": "a@" + "a." * (size // 2),
            "many short addresses": "a@b.c " * (size // 6),
            "many short versions": "a@1.2 " * (size // 6),
            "many host addresses": "a@192.0.2.4 " * (size // 12),
            "host address parts that never end": "a@1.2.3." * (size // 8),
            "mentions that never close": "[~" * (size // 2),
            "a mention that never closes": "[~" + "a" * size,
            "addresses that never reach a personal folder": "http://" * (size // 7),
            "addresses with many slashes that almost reach it": "http:///p" * (size // 9),
            "a long address without the folder": "https://" + "a" * size,
            "blanks": " " * size + "a",
        }.items():
            with self.subTest(name=name):
                self.assertLess(self.timed(redact_text, text), self.LIMIT)


class ReportingTest(unittest.TestCase):
    def setUp(self):
        self.issues = list(default_issues().values())

    def test_totals_ignore_epics(self):
        report = build_report(self.issues, "scope")
        self.assertEqual((report.issues, report.done), (4, 1))
        self.assertEqual((report.points, report.done_points, report.open_points), (16.0, 8.0, 8.0))
        self.assertEqual(report.without_points, 1)

    def test_statuses_are_ordered_by_category(self):
        rows = build_report(self.issues, "scope").by_status
        self.assertEqual([(row.status, row.issues, row.points) for row in rows], [("To Do", 2, 5.0), ("In Progress", 1, 3.0), ("Done", 1, 8.0)])

    def test_done_follows_the_status_category_not_the_name(self):
        renamed = [replace(issue, status=Status("Shipped", "done")) if issue.key == "PROJ-1" else issue for issue in self.issues]
        self.assertEqual(build_report(renamed, "scope").done, 2)

    def test_epic_rows(self):
        report = build_report(self.issues, "scope", epics={"PROJ-8": "Epic eight", "PROJ-9": "Epic nine"}, per_epic=True)
        rows = {row.key: row for row in report.by_epic}
        self.assertEqual([row.key for row in report.by_epic], ["PROJ-8", "PROJ-9", NO_EPIC])
        self.assertEqual((rows["PROJ-9"].issues, rows["PROJ-9"].done, rows["PROJ-9"].points), (2, 0, 8.0))
        self.assertEqual((rows["PROJ-8"].issues, rows["PROJ-8"].done, rows["PROJ-8"].done_points), (1, 1, 8.0))
        self.assertEqual(rows["PROJ-9"].summary, "Epic nine")

    def test_no_epic_rows_unless_asked(self):
        self.assertEqual(build_report(self.issues, "scope").by_epic, [])


class BacklogTest(unittest.TestCase):
    IDS = {"epic_link": "customfield_2", "story_points": "customfield_1", "acceptance": "customfield_4", "sprint": "customfield_3"}

    def test_dump_shape(self):
        dump = build_backlog_dump([sample_issue("PROJ-10", epic="PROJ-9", points=3.0), sample_issue("PROJ-2")], self.IDS, "2026-10-20T12:00:00+00:00")
        self.assertEqual(dump["fetchedAt"], "2026-10-20T12:00:00+00:00")
        self.assertEqual(dump["fieldIds"], {"epicLink": "customfield_2", "storyPoints": "customfield_1", "acceptance": "customfield_4", "sprint": "customfield_3"})
        self.assertEqual(list(dump["issues"]), ["PROJ-2", "PROJ-10"])  # numeric order, not text order
        entry = dump["issues"]["PROJ-10"]
        self.assertEqual(entry["epicLink"], "PROJ-9")
        self.assertEqual(entry["storyPoints"], 3.0)
        self.assertEqual(entry["sprint"], [{"name": "Demo Sprint 3", "state": "ACTIVE"}])
        self.assertEqual(entry["type"], "Story")
        self.assertEqual(entry["status"], "To Do")

    def test_a_key_whose_number_has_thousands_of_digits_is_sorted_last_and_never_an_error(self):
        huge = "PROJ-" + "9" * 5000  # `int()` refuses more than 4300 digits
        issues = [sample_issue(huge), sample_issue("PROJ-10"), sample_issue("PROJ-2")]
        dump = build_backlog_dump(issues, self.IDS, "t")
        self.assertEqual(list(dump["issues"]), ["PROJ-2", "PROJ-10", huge])  # the number is no number of Jira: the key sorts after the others
        self.assertEqual(list(build_backlog_dump(issues[::-1], self.IDS, "t")["issues"]), ["PROJ-2", "PROJ-10", huge])
        longest = "PROJ-" + "9" * 18
        self.assertEqual(list(build_backlog_dump([sample_issue(longest), sample_issue("PROJ-3")], self.IDS, "t")["issues"]), ["PROJ-3", longest])
        odd = build_backlog_dump([sample_issue("PROJ-7"), sample_issue("odd")], self.IDS, "t")  # a key without a number is no error either
        self.assertEqual(sorted(odd["issues"]), ["PROJ-7", "odd"])

    def test_dump_has_no_personal_data(self):
        text = str(build_backlog_dump([sample_issue("PROJ-1")], self.IDS, "t"))
        self.assertNotIn("jane.doe", text)
        self.assertNotIn("[~jdoe]", text)
        self.assertNotIn("Jane", text)

    def test_the_names_that_people_give_are_redacted_in_the_dump_too(self):
        issue = replace(sample_issue("PROJ-1"), labels=["owner-a.person@example.test"], components=["Team [~jdoe]"], fix_versions=["Release of jane.doe@example.test"])
        entry = build_backlog_dump([issue], self.IDS, "t")["issues"]["PROJ-1"]
        self.assertEqual((entry["labels"], entry["components"], entry["fixVersions"]), (["[email]"], ["Team [~user]"], ["Release of [email]"]))

    def test_missing_values_are_null(self):
        entry = build_backlog_dump([sample_issue("PROJ-1", epic="", points=None, acceptance="", priority="", sprints=[])], self.IDS, "t")["issues"]["PROJ-1"]
        self.assertIsNone(entry["epicLink"])
        self.assertIsNone(entry["storyPoints"])
        self.assertIsNone(entry["acceptance"])
        self.assertIsNone(entry["priority"])
        self.assertEqual(entry["sprint"], [])


class DraftsTest(unittest.TestCase):
    def test_split_list(self):
        self.assertEqual(split_list("a, b,,c "), ["a", "b", "c"])
        self.assertEqual(split_list(""), [])

    def test_parse_json_object(self):
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})
        for text in ("[1]", "nope", ""):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_json_object(text)

    def test_a_broken_json_can_carry_a_hint(self):
        with self.assertRaises(ValidationError) as caught:
            parse_json_object("{a: 1}", "--fields-json", hint="use @FILE")
        self.assertEqual(caught.exception.hint, "use @FILE")

    def test_changes_tell_when_they_are_empty(self):
        self.assertTrue(IssueChanges().is_empty())
        self.assertFalse(IssueChanges(labels=[]).is_empty())  # an empty list clears the labels
        self.assertFalse(IssueChanges(fields={"x": 1}).is_empty())

    def test_changes_payload(self):
        changes = IssueChanges(summary="S", labels=["a"], components=["C"], fix_versions=["V"], priority="High", assignee="jdoe", fields={"customfield_1": 5})
        fields = changes.to_payload()["fields"]
        self.assertEqual(fields["summary"], "S")
        self.assertEqual(fields["customfield_1"], 5)
        self.assertEqual(fields["components"], [{"name": "C"}])
        self.assertEqual(fields["fixVersions"], [{"name": "V"}])
        self.assertEqual(fields["priority"], {"name": "High"})
        self.assertEqual(fields["assignee"], {"name": "jdoe"})
        self.assertEqual(IssueChanges(labels=[]).to_payload(), {"fields": {"labels": []}})

    def test_a_resolved_assignee_replaces_the_text_that_was_typed(self):
        self.assertEqual(IssueChanges(assignee="Jane Doe").to_payload({"name": "jdoe"}), {"fields": {"assignee": {"name": "jdoe"}}})
        self.assertEqual(IssueChanges(assignee="unassigned").to_payload({"name": None}), {"fields": {"assignee": {"name": None}}})
        self.assertEqual(IssueChanges(summary="S").to_payload({"name": "jdoe"}), {"fields": {"summary": "S"}})  # no assignee asked for: none is sent
        created = IssueDraft("PROJ", "Story", "S", assignee="Jane Doe").to_payload("10200", "jdoe", {"name": "jdoe2"})
        self.assertEqual(created["fields"]["assignee"], {"name": "jdoe2"})
        self.assertNotIn("assignee", IssueDraft("PROJ", "Story", "S").to_payload("10200", "jdoe", {"name": "jdoe2"})["fields"])

    def test_a_field_set_twice_is_refused_instead_of_guessed(self):
        with self.assertRaises(ValidationError) as caught:
            IssueChanges(summary="S", fields={"summary": "other"}).to_payload()
        self.assertIn("summary is set twice", caught.exception.message)

    def test_extra_fields_cannot_undo_the_validated_options_of_a_create(self):
        for name in ("project", "issuetype", "summary", "reporter"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                IssueDraft("PROJ", "Story", "S", fields={name: "x"}).to_payload("10200", "jdoe")
        with self.assertRaises(ValidationError):
            IssueDraft("PROJ", "Story", "S", labels=["a"], fields={"labels": ["b"]}).to_payload("10200", "jdoe")

    def test_draft_validation(self):
        for draft in (IssueDraft("", "Story", "S"), IssueDraft("PROJ", "", "S"), IssueDraft("PROJ", "Story", "")):
            with self.subTest(draft=draft), self.assertRaises(ValidationError):
                draft.validate()

    def test_draft_payload_only_holds_what_was_given(self):
        payload = IssueDraft("PROJ", "Story", "S").to_payload("10200", "jdoe")
        self.assertEqual(payload, {"fields": {"project": {"key": "PROJ"}, "reporter": {"name": "jdoe"}, "issuetype": {"id": "10200"}, "summary": "S"}})
        full = IssueDraft("PROJ", "Story", "S", description="D", labels=["a"], priority="High", assignee="x", components=["C"], fix_versions=["V"], fields={"customfield_1": 3}).to_payload("10200", "jdoe")["fields"]
        self.assertEqual((full["description"], full["labels"], full["customfield_1"]), ("D", ["a"], 3))

    def test_issue_type_resolution(self):
        types = [IssueTypeInfo("10000", "Epic"), IssueTypeInfo("10200", "Story")]
        self.assertEqual(resolve_issue_type(types, "story").id, "10200")
        self.assertEqual(resolve_issue_type(types, "10000").name, "Epic")
        with self.assertRaises(ValidationError) as caught:
            resolve_issue_type(types, "Task")
        self.assertIn("Story (10200)", caught.exception.hint)
        with self.assertRaises(ValidationError):
            resolve_issue_type([*types, IssueTypeInfo("1", "story")], "story")

    def test_payload_validation(self):
        validate_update_payload({"fields": {}})
        validate_update_payload({"update": {}})
        validate_create_payload({"fields": {"summary": "x"}})
        for bad in ([], {"nothing": 1}, "text"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_update_payload(bad)
        with self.assertRaises(ValidationError):
            validate_create_payload({"fields": []})


class TransitionsTest(unittest.TestCase):
    OPTIONS = [Transition("111", "Reopen", STATUS["To Do"]), Transition("81", "Finish", STATUS["Done"]), Transition("82", "Close", STATUS["Done"])]

    def test_id_wins(self):
        self.assertEqual(pick_transition(self.OPTIONS, "81").name, "Finish")

    def test_target_status_before_transition_name(self):
        self.assertEqual(pick_transition(self.OPTIONS, "to do").id, "111")
        self.assertEqual(pick_transition(self.OPTIONS, "reopen").id, "111")

    def test_transition_name(self):
        self.assertEqual(pick_transition(self.OPTIONS, "FINISH").id, "81")

    def test_ambiguous_status_names_the_candidates(self):
        with self.assertRaises(ValidationError) as caught:
            pick_transition(self.OPTIONS, "Done")
        self.assertIn("81", caught.exception.hint)
        self.assertIn("82", caught.exception.hint)

    def test_unknown_lists_what_is_available(self):
        with self.assertRaises(ValidationError) as caught:
            pick_transition(self.OPTIONS, "Archive")
        self.assertIn("Available:", caught.exception.hint)


class LinksTest(unittest.TestCase):
    TYPES = [LinkType("1", "Blocks", "blocks", "is blocked by"), LinkType("2", "Has to be done after", "is required by", "requires"), LinkType("3", "Pre-requisite", "requires", "is a requisite of")]

    def test_exact_name_only(self):
        self.assertEqual(resolve_link_type(self.TYPES, "blocks").id, "1")
        with self.assertRaises(ValidationError):
            resolve_link_type(self.TYPES, "requires")  # a phrase in two types, not a name

    def test_direction(self):
        self.assertEqual(link_ends("A-1", "A-2", inverse=False), ("A-1", "A-2"))
        self.assertEqual(link_ends("A-1", "A-2", inverse=True), ("A-2", "A-1"))
        self.assertEqual(relation_phrase(self.TYPES[0], False), "blocks")
        self.assertEqual(relation_phrase(self.TYPES[0], True), "is blocked by")
        self.assertEqual(relation_text("A-1", "blocks", "A-2"), "A-1 blocks A-2")

    def test_find_relation_reads_the_stored_direction(self):
        issue = sample_issue("A-1", links=[IssueLink("Blocks", "is blocked by", "inward", IssueRef("A-2"))])
        self.assertEqual(find_relation(issue, "A-2", "Blocks"), "A-1 is blocked by A-2")
        self.assertIsNone(find_relation(issue, "A-3", "Blocks"))
        self.assertIsNone(find_relation(issue, "A-2", "Pre-requisite"))


class FieldsTest(unittest.TestCase):
    CATALOG = [FieldInfo("cf1", "Story Points", True), FieldInfo("cf2", "Epic Link", True), FieldInfo("cf3", "Sprint", True), FieldInfo("cf4", "Acceptance Criteria", True)]

    def test_exact_labels_and_acceptance_prefix(self):
        ids = resolve_field_ids(self.CATALOG)
        self.assertEqual(ids, {"story_points": "cf1", "epic_link": "cf2", "sprint": "cf3", "acceptance": "cf4"})

    def test_settings_win(self):
        ids = resolve_field_ids(self.CATALOG, {"story_points": "customfield_99"})
        self.assertEqual(ids["story_points"], "customfield_99")

    def test_missing_fields_are_none(self):
        self.assertEqual(resolve_field_ids([]), {"story_points": None, "epic_link": None, "sprint": None, "acceptance": None})


class TimeTest(unittest.TestCase):
    def test_formats(self):
        for text in ("2026-10-15T08:07:04Z", "2026-10-15T10:07:04+0200", "2026-10-15T10:07:04.123+02:00", "2026-10-15T08:07:04"):
            with self.subTest(text=text):
                utc = parse_timestamp(text).astimezone(timezone.utc)
                self.assertEqual(utc.replace(microsecond=0).isoformat(), "2026-10-15T08:07:04+00:00")

    def test_any_number_of_fraction_digits(self):
        """Python 3.10 reads only three or six digits; the parser pads or cuts to six."""
        for text, microseconds in (("2026-10-15T08:07:04.5Z", 500000), ("2026-10-15T08:07:04.12Z", 120000), ("2026-10-15T08:07:04.1234567+0000", 123456)):
            with self.subTest(text=text):
                self.assertEqual(parse_timestamp(text).microsecond, microseconds)

    def test_garbage_is_refused(self):
        with self.assertRaises(ValidationError):
            parse_timestamp("yesterday")

    def test_stale_guard(self):
        ensure_unchanged_since("A-1", "2026-10-10T09:00:00.000+0200", "2026-10-10T07:00:00Z")  # same moment: fine
        with self.assertRaises(ConflictError) as caught:
            ensure_unchanged_since("A-1", "2026-10-10T09:00:00.000+0200", "2026-10-10T06:59:59Z")
        self.assertEqual(caught.exception.exit_code, 3)


class FrontmatterTest(unittest.TestCase):
    def test_front_matter_and_body(self):
        ticket = parse_markdown_ticket('---\nsummary: "A title"\n# a comment\nlabels: a, b\n---\n\nBody line\n')
        self.assertEqual(ticket.meta, {"summary": "A title", "labels": "a, b"})
        self.assertEqual(ticket.description, "Body line")

    def test_without_front_matter_everything_is_the_body(self):
        ticket = parse_markdown_ticket("# Title\n\ntext")
        self.assertEqual(ticket.meta, {})
        self.assertEqual(ticket.description, "# Title\n\ntext")

    def test_broken_front_matter(self):
        with self.assertRaises(ValidationError):
            parse_markdown_ticket("---\nsummary: x\nno closing")
        with self.assertRaises(ValidationError):
            parse_markdown_ticket("---\nnot a pair\n---\nbody")

    def test_csv_lines_count_the_header(self):
        rows = parse_csv('summary,labels\n"One, 1",a\nTwo,\n')
        self.assertEqual([(row.line, row.get("summary")) for row in rows], [(2, "One, 1"), (3, "Two")])
        self.assertEqual(rows[1].get("labels", "label"), "")


class PayloadsTest(unittest.TestCase):
    def test_transition(self):
        self.assertEqual(transition_payload("21"), {"transition": {"id": "21"}})
        self.assertEqual(transition_payload("21", " Done. ")["update"], {"comment": [{"add": {"body": "Done."}}]})

    def test_a_transition_can_carry_the_fields_of_its_screen(self):
        self.assertEqual(transition_payload("21", fields={"resolution": {"name": "Fixed"}}), {"transition": {"id": "21"}, "fields": {"resolution": {"name": "Fixed"}}})
        self.assertEqual(list(transition_payload("21", "Done.", {"a": 1})), ["transition", "fields", "update"])
        self.assertEqual(transition_payload("21", fields={}), {"transition": {"id": "21"}})  # no fields, no key

    def test_comment_and_link(self):
        self.assertEqual(comment_payload(" hi \n"), {"body": "hi"})
        self.assertEqual(link_payload("Blocks", "A-1", "A-2"), {"type": {"name": "Blocks"}, "outwardIssue": {"key": "A-1"}, "inwardIssue": {"key": "A-2"}})

    def test_a_comment_can_carry_a_restriction(self):
        self.assertEqual(comment_payload(" hi ", {"type": "role", "value": "Developers"}), {"body": "hi", "visibility": {"type": "role", "value": "Developers"}})
        self.assertEqual(comment_payload("hi", None), {"body": "hi"})


class ErrorsTest(unittest.TestCase):
    def test_every_exit_code_is_documented(self):
        classes = {cls for cls in JiraCliError.__subclasses__()} | {JiraCliError}
        documented = {code for code, _, _ in EXIT_CODES}
        self.assertTrue({cls.exit_code for cls in classes} <= documented)
        names = " ".join(names for _, names, _ in EXIT_CODES)
        for cls in classes:
            self.assertIn(cls.code, names)


if __name__ == "__main__":
    unittest.main()
