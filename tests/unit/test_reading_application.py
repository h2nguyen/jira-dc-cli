"""Reading with fakes: several keys with one request, long issues in parts, the rendered text, the history, saved filters."""
import dataclasses
import unittest

from jira_dc_cli.application.reading import MissingIssue, ReadOptions
from jira_dc_cli.domain.errors import AuthError, NotFoundError, RejectedError, UsageError, ValidationError
from jira_dc_cli.domain.history import Changelog, FieldChange, HistoryEntry
from jira_dc_cli.domain.model import Attachment, Comment, User
from jira_dc_cli.domain.savedfilters import SavedFilter
from tests.support.fakes import DEFAULT_SETTINGS, JANE, FakeGateway, build_app, sample_issue


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


LONG = "h1. Goal\nShip it.\n\nh2. Steps\n# open\n# click\n\nh2. Result\nIt works for [~jdoe] (jane.doe@example.com)."
CODE_FIRST = "{code}\n" + "\n".join(f"at line {n}" for n in range(60)) + "\n{code}\n\nWhat happened after the stack trace."
REVIEW_JQL = 'assignee = jdoe AND reporter in ("asmith","jane.doe") AND watcher = "Alex Smith" ORDER BY created'
PEOPLE = ("jdoe", "asmith", "jane.doe", "Alex Smith", "Alex", "Smith")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def read(self, *keys, **options):
        return self.app.reading.read_issues.execute(keys, ReadOptions(**options))

    def calls(self, name):
        return self.gateway.called(name)


class OneIssueTest(Fixture):
    def test_one_key_is_read_as_before_with_one_ordinary_request(self):
        result = self.read("proj-1")
        self.assertFalse(result.many)
        self.assertEqual(len(result.issues), 1)
        reading = result.issues[0]
        self.assertEqual(reading.issue.key, "PROJ-1")
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_issue"])
        view = self.app.view_issue.execute("PROJ-1")
        self.assertEqual(reading.issue, view.issue)
        self.assertEqual((reading.parts, reading.cuts, reading.outline, reading.history), ((), {}, None, None))

    def test_a_missing_key_is_an_error_as_before(self):
        with self.assertRaises(NotFoundError):
            self.read("PROJ-999")

    def test_a_key_that_is_no_key_is_refused_before_anything_is_asked(self):
        with self.assertRaises(ValidationError):
            self.read("PROJ-1", "not a key")
        self.assertEqual(self.gateway.calls, [])

    def test_no_key_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.app.reading.read_issues.execute([])

    def test_a_key_given_twice_is_read_once_and_the_answer_is_still_a_list(self):
        result = self.read("PROJ-1", "proj-1")
        self.assertEqual((len(result.issues), result.many), (1, True))
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_issue"])  # one issue: one ordinary request
        two = self.read("PROJ-1", "PROJ-2")
        self.assertEqual((result.many, two.many), (True, True))

    def test_a_missing_key_given_twice_is_an_entry_of_a_list_and_given_once_it_is_an_error(self):
        for options in ({}, {"history": True}, {"rendered": True}):
            with self.subTest(options=options):
                result = self.read("PROJ-999", "proj-999", **options)
                self.assertEqual((result.many, result.issues), (True, []))
                self.assertEqual([(item.key, item.code) for item in result.errors], [("PROJ-999", "NOT_FOUND")])
                with self.assertRaises(NotFoundError):
                    self.read("PROJ-999", **options)

    def test_people_comments_and_mentions_stay_out_by_default(self):
        reading = self.read("PROJ-1").issues[0]
        self.assertEqual((reading.issue.assignee, reading.issue.comments), (None, []))
        self.assertNotIn("jane.doe", reading.issue.description)
        self.assertFalse(reading.view.users_shown or reading.view.comments_shown)

    def test_people_and_comments_when_asked_for(self):
        reading = self.read("PROJ-1", with_users=True, with_comments=True).issues[0]
        self.assertEqual(reading.issue.assignee.display_name, "Jane Doe")
        self.assertEqual(reading.issue.comments[0].author.name, "jdoe")

    def test_the_policy_refuses_people_before_a_request(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError):
            app.reading.read_issues.execute(["PROJ-1"], ReadOptions(with_users=True))
        self.assertEqual(self.gateway.calls, [])

    def test_the_raw_json_has_no_people_unless_asked(self):
        self.assertEqual(self.read("PROJ-1", want_raw=True).issues[0].view.raw["fields"]["assignee"], {"hidden": True})
        self.assertEqual(self.read("PROJ-1", want_raw=True, with_users=True).issues[0].view.raw["fields"]["assignee"]["name"], "jdoe")


class SeveralIssuesTest(Fixture):
    def test_three_keys_are_one_request_and_come_back_in_the_order_given(self):
        result = self.read("PROJ-3", "PROJ-1", "PROJ-2")
        self.assertEqual([call for call in self.gateway.calls], [("get_issues_by_key", ("PROJ-3", "PROJ-1", "PROJ-2"), ())])
        self.assertEqual([item.issue.key for item in result.issues], ["PROJ-3", "PROJ-1", "PROJ-2"])  # the fake answers in Jira's order: 1, 2, 3
        self.assertEqual((result.many, result.errors), (True, []))

    def test_keys_are_normalised_and_asked_for_once(self):
        self.read("proj-2", "PROJ-1", "PROJ-2")
        self.assertEqual(self.calls("get_issues_by_key"), [("get_issues_by_key", ("PROJ-2", "PROJ-1"), ())])

    def test_people_comments_and_mentions_stay_out_of_every_issue(self):
        for reading in self.read("PROJ-1", "PROJ-2").issues:
            self.assertEqual((reading.issue.assignee, reading.issue.comments), (None, []))
            self.assertNotIn("jane.doe", reading.issue.description)

    def test_a_missing_key_falls_back_to_one_request_per_key_and_is_reported_on_its_own(self):
        result = self.read("PROJ-1", "PROJ-999", "PROJ-2")
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_issues_by_key", "get_issue_with", "get_issue_with", "get_issue_with"])
        self.assertEqual([call[1] for call in self.calls("get_issue_with")], ["PROJ-1", "PROJ-999", "PROJ-2"])
        self.assertEqual([item.key if isinstance(item, MissingIssue) else item.issue.key for item in result.entries], ["PROJ-1", "PROJ-999", "PROJ-2"])
        self.assertEqual([item.issue.key for item in result.issues], ["PROJ-1", "PROJ-2"])
        (missing,) = result.errors
        self.assertEqual((missing.key, missing.code), ("PROJ-999", "NOT_FOUND"))
        self.assertIn("PROJ-999", missing.message)

    def test_every_key_missing_gives_only_errors(self):
        result = self.read("PROJ-998", "PROJ-999")
        self.assertEqual((result.issues, [item.key for item in result.errors]), ([], ["PROJ-998", "PROJ-999"]))

    def test_a_rejection_that_is_not_about_a_key_is_not_hidden(self):
        self.gateway.errors["get_issues_by_key"] = RejectedError("Jira refused", details={"status": 414})
        with self.assertRaises(RejectedError):
            self.read("PROJ-1", "PROJ-2")
        self.assertEqual(self.calls("get_issue_with"), [])

    def test_an_error_that_is_not_a_missing_issue_stops_the_fallback(self):
        self.gateway.errors["get_issue_with"] = AuthError("Jira refused the token")
        with self.assertRaises(AuthError):
            self.read("PROJ-1", "PROJ-999")

    def test_a_key_that_the_search_left_out_is_asked_for_on_its_own(self):
        original = self.gateway.get_issues_by_key
        self.gateway.get_issues_by_key = lambda keys, expand=(): [item for item in original(keys, expand) if item.issue.key != "PROJ-2"]
        result = self.read("PROJ-1", "PROJ-2")
        self.assertEqual([item.issue.key for item in result.issues], ["PROJ-1", "PROJ-2"])
        self.assertEqual([call[1] for call in self.calls("get_issue_with")], ["PROJ-2"])

    def test_the_expansions_go_into_the_one_request(self):
        self.read("PROJ-1", "PROJ-2", rendered=True, history=True)
        self.assertEqual(self.calls("get_issues_by_key")[0][2], ("renderedFields", "changelog"))
        self.read("PROJ-1", "PROJ-2", history=True)
        self.assertEqual(self.calls("get_issues_by_key")[1][2], ("changelog",))

    def test_the_expansions_go_into_every_request_of_the_fallback(self):
        self.read("PROJ-1", "PROJ-999", rendered=True)
        self.assertEqual([call[2] for call in self.calls("get_issue_with")], [("renderedFields",), ("renderedFields",)])

    def test_the_options_apply_to_every_issue(self):
        result = self.read("PROJ-1", "PROJ-2", only="summary,status")
        self.assertTrue(all(item.parts == ("summary", "status") for item in result.issues))

    def test_people_may_not_be_asked_for_under_the_policy(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError):
            app.reading.read_issues.execute(["PROJ-1", "PROJ-2"], ReadOptions(with_users=True))


class OptionsTest(Fixture):
    def test_the_raw_json_is_for_one_issue_and_has_no_parts(self):
        for keys, options in (
            (("PROJ-1", "PROJ-2"), {}),
            (("PROJ-1",), {"only": "summary"}),
            (("PROJ-1",), {"max_chars": 100}),
            (("PROJ-1",), {"outline": True}),
            (("PROJ-1",), {"section": "x"}),
            (("PROJ-1",), {"rendered": True}),
            (("PROJ-1",), {"history": True}),
        ):
            with self.subTest(keys=keys, options=options), self.assertRaises(UsageError):
                self.read(*keys, want_raw=True, **options)
        self.assertEqual(self.gateway.calls, [])

    def test_the_outline_and_a_section_read_one_description(self):
        with self.assertRaises(UsageError):
            self.read("PROJ-1", "PROJ-2", outline=True)
        with self.assertRaises(UsageError):
            self.read("PROJ-1", "PROJ-2", section="Goal")
        with self.assertRaises(UsageError):
            self.read("PROJ-1", outline=True, section="Goal")
        self.assertEqual(self.gateway.calls, [])

    def test_the_same_key_twice_is_two_keys_for_every_check(self):
        for options in ({"want_raw": True}, {"outline": True}, {"section": "Goal"}):
            with self.subTest(options=options), self.assertRaises(UsageError):
                self.read("PROJ-1", "proj-1", **options)
        self.assertEqual(self.gateway.calls, [])
        self.read("PROJ-1", "PROJ-1", only="summary")  # the options that read every issue are fine

    def test_the_outline_a_section_and_rendered_text_need_what_they_read(self):
        for options in ({"outline": True}, {"section": "Goal"}, {"rendered": True}):
            with self.subTest(options=options), self.assertRaises(UsageError) as caught:
                self.read("PROJ-1", only="status", **options)
            self.assertIn("--only", caught.exception.hint)
        self.read("PROJ-1", only="acceptance", rendered=True)  # the acceptance criteria are enough for --rendered

    def test_an_unknown_name_in_only_is_a_usage_error_that_lists_the_names(self):
        with self.assertRaises(UsageError) as caught:
            self.read("PROJ-1", only="summary,nope")
        self.assertIn("attachments", caught.exception.hint)
        self.assertEqual(self.gateway.calls, [])

    def test_max_chars_must_be_positive(self):
        for value in (0, -5):
            with self.subTest(value=value), self.assertRaises(UsageError):
                self.read("PROJ-1", max_chars=value)

    def test_only_keeps_the_parts_in_their_order(self):
        self.assertEqual(self.read("PROJ-1", only="labels,summary").issues[0].parts, ("summary", "labels"))

    def test_comments_by_name_or_by_flag(self):
        self.assertEqual(self.read("PROJ-1", only="comments").issues[0].issue.comments[0].body, "Looks fine, [~user].")
        self.assertFalse(self.read("PROJ-1", only="summary").issues[0].view.comments_shown)
        both = self.read("PROJ-1", only="summary", with_comments=True).issues[0]
        self.assertEqual((both.parts, both.view.comments_shown), (("summary", "comments"), True))


class MaxCharsTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue(
            "PROJ-1",
            description="first paragraph\n\nsecond paragraph is a bit longer\n\nthird",
            acceptance="x" * 50 + " " + "y" * 50,
            comments=[Comment("10", "2026-10-02T10:00:00.000+0200", "short"), Comment("11", "2026-10-03T10:00:00.000+0200", "a long one\n\n" + "z " * 100)],
            comment_total=2,
        )

    def test_every_long_text_is_cut_and_says_how_much_there_was(self):
        reading = self.read("PROJ-1", max_chars=40, with_comments=True).issues[0]
        self.assertEqual(reading.issue.description, "first paragraph")
        self.assertEqual(reading.cuts["description"].truncated, True)
        self.assertEqual(reading.cuts["description"].total_chars, len("first paragraph\n\nsecond paragraph is a bit longer\n\nthird"))
        self.assertEqual((reading.cuts["comments.10"].truncated, reading.cuts["comments.10"].total_chars), (False, 5))
        self.assertEqual(reading.issue.comments[1].body, "a long one")
        self.assertTrue(reading.cuts["comments.11"].truncated)

    def test_a_long_word_is_cut_at_the_blank_before_it(self):
        reading = self.read("PROJ-1", max_chars=60).issues[0]
        self.assertEqual(reading.issue.acceptance, "x" * 50)
        self.assertEqual(reading.cuts["acceptance"].total_chars, 101)

    def test_without_the_option_nothing_is_cut_and_nothing_is_reported(self):
        reading = self.read("PROJ-1", with_comments=True).issues[0]
        self.assertEqual(reading.cuts, {})
        self.assertIn("third", reading.issue.description)

    def test_only_the_texts_that_are_shown_are_measured(self):
        self.assertEqual(sorted(self.read("PROJ-1", max_chars=40, only="description").issues[0].cuts), ["description"])
        self.assertEqual(sorted(self.read("PROJ-1", max_chars=40).issues[0].cuts), ["acceptance", "description"])  # no comments: they are not shown
        self.assertEqual(self.read("PROJ-1", max_chars=40, only="status").issues[0].cuts, {})

    def test_a_text_that_fits_is_reported_as_not_cut(self):
        cut = self.read("PROJ-1", max_chars=1000).issues[0].cuts["description"]
        self.assertEqual((cut.truncated, cut.total_chars), (False, len("first paragraph\n\nsecond paragraph is a bit longer\n\nthird")))

    def test_a_description_that_opens_with_a_code_block_is_cut_at_a_line_and_the_block_is_closed(self):
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description=CODE_FIRST)
        reading = self.read("PROJ-1", only="description", max_chars=100).issues[0]
        lines = ["{code}", *(f"at line {n}" for n in range(8)), "{code}"]
        self.assertEqual(reading.issue.description, "\n".join(lines))
        cut = reading.cuts["description"]
        self.assertEqual((cut.truncated, cut.closed, cut.total_chars), (True, "{code}", len(CODE_FIRST)))
        self.assertLessEqual(len(reading.issue.description), 100)

    def test_a_cut_before_a_code_block_does_not_close_anything(self):
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description="Intro.\n\n" + CODE_FIRST)
        reading = self.read("PROJ-1", only="description", max_chars=100).issues[0]
        self.assertEqual((reading.issue.description, reading.cuts["description"].closed), ("Intro.", ""))


class OutlineAndSectionTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description=LONG)

    def test_the_outline_replaces_the_text(self):
        reading = self.read("PROJ-1", outline=True).issues[0]
        self.assertEqual([(item.level, item.title) for item in reading.outline], [(1, "Goal"), (2, "Steps"), (2, "Result")])
        self.assertEqual(reading.issue.description, "")
        self.assertIsNone(reading.section)

    def test_a_section_replaces_the_text_with_the_part_under_one_heading(self):
        reading = self.read("PROJ-1", section="steps").issues[0]
        self.assertEqual(reading.issue.description, "h2. Steps\n# open\n# click")
        self.assertEqual((reading.section.index, reading.section.title), (2, "Steps"))
        self.assertIsNone(reading.outline)

    def test_a_section_is_redacted_like_the_description(self):
        text = self.read("PROJ-1", section="result").issues[0].issue.description
        self.assertEqual(text, "h2. Result\nIt works for [~user] ([email]).")

    def test_with_people_the_text_is_as_it_is(self):
        self.assertIn("[~jdoe]", self.read("PROJ-1", section="result", with_users=True).issues[0].issue.description)

    def test_a_heading_that_is_not_there_lists_the_headings(self):
        with self.assertRaises(ValidationError) as caught:
            self.read("PROJ-1", section="missing")
        self.assertIn("1. Goal | 2. Steps | 3. Result", caught.exception.hint)

    def test_a_cut_applies_to_the_section(self):
        reading = self.read("PROJ-1", section="goal", max_chars=30).issues[0]
        self.assertEqual(reading.issue.description, "h1. Goal\nShip it.")
        self.assertTrue(reading.cuts["description"].truncated)

    def test_with_the_outline_the_description_is_not_measured(self):
        self.assertNotIn("description", self.read("PROJ-1", outline=True, max_chars=10).issues[0].cuts)

    def test_a_number_that_is_no_number_of_the_outline_is_a_clean_error(self):
        for selector in ("\u00b2", "\u2460", "\u0662", "1" * 5000, "7", "0", "-1", "1" * 7):
            with self.subTest(selector=selector[:12]), self.assertRaises(ValidationError) as caught:
                self.read("PROJ-1", section=selector)
            self.assertIn("1. Goal | 2. Steps | 3. Result", caught.exception.hint)


class RenderedTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", description="* a\n* b", acceptance="1. x")
        self.gateway.rendered["PROJ-1"] = (
            '<ul><li>a</li><li>b</li></ul><p><a href="/secure/ViewProfile.jspa?name=jdoe" class="user-hover" rel="jdoe">Jane Doe</a> jane.doe@example.com</p>',
            "<ol><li>x</li></ol>",
        )

    def test_the_rendered_texts_are_asked_for_and_reduced_to_plain_text(self):
        reading = self.read("PROJ-1", rendered=True).issues[0]
        self.assertEqual(self.calls("get_issue_with"), [("get_issue_with", "PROJ-1", ("renderedFields",))])
        self.assertEqual(reading.rendered_description, "- a\n- b\n\n[~user] [email]")
        self.assertEqual(reading.rendered_acceptance, "1. x")
        self.assertEqual(reading.issue.description, "* a\n* b")  # the markup stays as it is

    def test_with_people_the_rendered_text_names_them(self):
        self.assertIn("Jane Doe", self.read("PROJ-1", rendered=True, with_users=True).issues[0].rendered_description)

    def test_without_the_option_there_is_no_rendered_text(self):
        reading = self.read("PROJ-1").issues[0]
        self.assertEqual((reading.rendered_description, reading.rendered_acceptance), (None, None))
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_issue"])

    def test_the_rendered_text_follows_the_parts_that_are_shown(self):
        reading = self.read("PROJ-1", rendered=True, only="description").issues[0]
        self.assertEqual((reading.rendered_description is not None, reading.rendered_acceptance), (True, None))

    def test_the_rendered_text_is_cut_too(self):
        reading = self.read("PROJ-1", rendered=True, max_chars=5).issues[0]
        self.assertEqual(reading.rendered_description, "- a")
        self.assertTrue(reading.cuts["renderedDescription"].truncated)

    def test_a_description_that_jira_renders_to_nothing_is_an_empty_text(self):
        self.gateway.rendered["PROJ-1"] = ("", "")
        reading = self.read("PROJ-1", rendered=True).issues[0]
        self.assertEqual((reading.rendered_description, reading.rendered_acceptance), ("", ""))


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

    def test_the_status_changes_come_oldest_first_and_without_people(self):
        reading = self.read("PROJ-1", history=True).issues[0]
        self.assertEqual(self.calls("get_issue_with"), [("get_issue_with", "PROJ-1", ("changelog",))])
        self.assertEqual([(item.created[:10], item.from_status, item.to_status) for item in reading.history], [("2026-10-01", "To Do", "In Progress"), ("2026-10-02", "In Progress", "Done")])
        self.assertTrue(reading.history_complete)
        self.assertNotIn("jdoe", repr(reading.history))

    def test_an_issue_without_status_changes_has_an_empty_history(self):
        self.assertEqual(self.read("PROJ-2", history=True).issues[0].history, [])

    def test_a_history_that_jira_sent_only_in_part_says_so(self):
        self.gateway.changelogs["PROJ-1"] = dataclasses.replace(self.gateway.changelogs["PROJ-1"], total=130)
        reading = self.read("PROJ-1", history=True).issues[0]
        self.assertFalse(reading.history_complete)
        self.assertEqual(len(reading.history), 2)

    def test_without_the_option_there_is_no_history(self):
        self.assertIsNone(self.read("PROJ-1").issues[0].history)

    def test_several_issues_have_their_own_history_from_one_request(self):
        result = self.read("PROJ-1", "PROJ-2", history=True)
        self.assertEqual([len(item.history) for item in result.issues], [2, 0])
        self.assertEqual(len(self.calls("get_issues_by_key")), 1)

    def test_a_history_that_is_not_asked_for_in_a_search_answer_is_empty_not_a_crash(self):
        original = self.gateway.get_issues_by_key
        self.gateway.get_issues_by_key = lambda keys, expand=(): [dataclasses.replace(item, changelog=None) for item in original(keys, expand)]
        self.assertEqual(self.read("PROJ-1", "PROJ-2", history=True).issues[0].history, [])


class AttachmentsTest(Fixture):
    def test_attachments_are_part_of_the_issue(self):
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", attachments=[Attachment("1", "design.png", "image/png", 10, author=JANE)], attachment_total=1)
        reading = self.read("PROJ-1", only="attachments").issues[0]
        self.assertEqual([item.filename for item in reading.issue.attachments], ["design.png"])
        self.assertIsNone(reading.issue.attachments[0].author)


class FiltersTest(Fixture):
    def setUp(self):
        super().setUp()
        owner = User("jdoe", "Jane Doe", "jane.doe@example.com")
        self.gateway.saved_filters = {
            "10000": SavedFilter("10000", "Bugs of jane.doe@example.com", "Open [~jdoe] bugs", 'reporter = "jane.doe@example.com" AND type = Bug', True, owner, "https://jira.example.com/rest/api/2/search?jql=reporter%20%3D%20jane.doe%40example.com", "https://jira.example.com/x"),
            "10001": SavedFilter("10001", "Not a favourite", "", "project = PROJ", False, owner, "https://jira.example.com/rest/api/2/search?jql=project%20%3D%20PROJ", "https://jira.example.com/y"),
        }

    def test_the_favourites_are_listed_without_people(self):
        (item,) = self.app.reading.list_filters.execute()
        self.assertEqual((item.id, item.owner), ("10000", None))
        self.assertEqual((item.name, item.description, item.jql), ("Bugs of [email]", "Open [~user] bugs", "reporter = [user] AND type = Bug"))
        self.assertEqual(item.search_url, "")  # the escaped address held the e-mail address

    def test_with_people_the_owner_and_the_text_are_as_they_are(self):
        (item,) = self.app.reading.list_filters.execute(with_users=True)
        self.assertEqual(item.owner.display_name, "Jane Doe")
        self.assertIn("jane.doe@example.com", item.jql)
        self.assertIn("jane.doe%40example.com", item.search_url)

    def test_the_policy_refuses_people(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError):
            app.reading.list_filters.execute(with_users=True)
        with self.assertRaises(ValidationError):
            app.reading.show_filter.execute("10000", with_users=True)
        self.assertEqual(self.gateway.calls, [])

    def test_one_filter_has_its_addresses(self):
        item = self.app.reading.show_filter.execute("10001")
        self.assertEqual((item.id, item.jql, item.favourite, item.owner), ("10001", "project = PROJ", False, None))
        self.assertEqual((item.search_url, item.view_url), ("https://jira.example.com/rest/api/2/search?jql=project%20%3D%20PROJ", "https://jira.example.com/y"))

    def test_an_unknown_filter_says_where_to_look(self):
        with self.assertRaises(NotFoundError) as caught:
            self.app.reading.show_filter.execute("99999")
        self.assertIn("99999", caught.exception.message)
        self.assertIn("jira filters", caught.exception.hint)

    def test_an_id_that_is_not_a_number_is_refused_before_a_request(self):
        with self.assertRaises(ValidationError):
            self.app.reading.show_filter.execute("My filter")
        self.assertEqual(self.gateway.calls, [])

    def test_no_favourites_is_an_empty_list(self):
        self.gateway.saved_filters = {}
        self.assertEqual(self.app.reading.list_filters.execute(), [])


class FilterPeopleTest(Fixture):
    """The people that the JQL of a saved filter names stay out as the owner does, and so does the address that holds the JQL."""

    ADDRESS = (
        "https://jira.example.com/rest/api/2/search?jql=assignee%20%3D%20jdoe%20AND%20reporter%20in%20(%22asmith%22%2C%22jane.doe%22)"
        "%20AND%20watcher%20%3D%20%22Alex%20Smith%22%20ORDER%20BY%20created"
    )
    CLEAN = 'project = "PROJ" AND assignee = currentUser() AND resolution = Unresolved'
    SHOWN = "assignee = [user] AND reporter in ([user],[user]) AND watcher = [user] ORDER BY created"

    def setUp(self):
        super().setUp()
        owner = User("jdoe", "Jane Doe", "jane.doe@example.com")
        self.stored = SavedFilter("10000", "Theirs", "", REVIEW_JQL, True, owner, self.ADDRESS, "https://jira.example.com/issues/?filter=10000")
        self.clean = SavedFilter("10001", "Mine", "", self.CLEAN, True, owner, "https://jira.example.com/rest/api/2/search?jql=project%20%3D%20PROJ", "https://jira.example.com/y")
        self.gateway.saved_filters = {"10000": self.stored, "10001": self.clean}

    def apps(self):
        return {
            "no policy": self.app,
            "the policy": build_app(gateway=self.gateway, settings=settings(hide_users=True)),
        }

    def test_the_list_has_the_jql_without_the_people(self):
        for name, app in self.apps().items():
            with self.subTest(name):
                theirs, mine = app.reading.list_filters.execute()
                self.assertEqual((theirs.jql, theirs.people_hidden, theirs.owner), (self.SHOWN, True, None))
                self.assertEqual((mine.jql, mine.people_hidden, mine.owner), (self.CLEAN, False, None))
                for person in PEOPLE:
                    self.assertNotIn(person, repr(theirs))

    def test_one_filter_has_no_address_that_names_people_and_the_other_keeps_its_address(self):
        for name, app in self.apps().items():
            with self.subTest(name):
                theirs = app.reading.show_filter.execute("10000")
                self.assertEqual((theirs.jql, theirs.search_url, theirs.people_hidden), (self.SHOWN, "", True))
                self.assertEqual(theirs.view_url, "https://jira.example.com/issues/?filter=10000")
                mine = app.reading.show_filter.execute("10001")
                self.assertEqual((mine.jql, mine.search_url, mine.people_hidden), (self.CLEAN, self.clean.search_url, False))

    def test_with_people_the_filter_is_as_it_is(self):
        theirs, mine = self.app.reading.list_filters.execute(with_users=True)
        self.assertEqual((theirs, mine), (self.stored, self.clean))
        self.assertEqual(self.app.reading.show_filter.execute("10000", with_users=True), self.stored)

    def test_the_policy_refuses_people_before_it_asks_for_anything(self):
        app = self.apps()["the policy"]
        with self.assertRaises(ValidationError):
            app.reading.show_filter.execute("10000", with_users=True)
        self.assertEqual(self.gateway.calls, [])


class SuggestBranchTest(Fixture):
    SUMMARY = "Ask [~jdoe] or jane.doe@example.com about the Jane Doe report"

    def setUp(self):
        super().setUp()
        self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", summary=self.SUMMARY)

    def test_the_name_is_made_of_the_summary_as_issue_shows_it(self):
        shown = self.read("PROJ-1").issues[0].issue.summary
        self.assertEqual(shown, "Ask [~user] or [email] about the Jane Doe report")
        name = self.app.suggest_branch.execute("PROJ-1")
        self.assertEqual(name, "feature/PROJ-1-ask-user-or-email-about-the-jane-doe")
        for personal in ("jdoe", "jane.doe", "example"):
            self.assertNotIn(personal, name)

    def test_the_policy_makes_no_difference(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        self.assertEqual(app.suggest_branch.execute("PROJ-1"), self.app.suggest_branch.execute("PROJ-1"))

    def test_a_summary_that_is_a_mention_or_an_address_only(self):
        for summary, slug in (("[~jdoe]", "user"), ("jane.doe@example.com", "email"), ("Fix the export", "fix-the-export")):
            with self.subTest(summary=summary):
                self.gateway.issues["PROJ-1"] = sample_issue("PROJ-1", summary=summary)
                self.assertEqual(self.app.suggest_branch.execute("PROJ-1"), f"feature/PROJ-1-{slug}")

    def test_the_type_and_the_key_are_as_before(self):
        self.gateway.issues["PROJ-2"] = sample_issue("PROJ-2", kind="Bug", summary="Mail [~jdoe]")
        self.assertEqual(self.app.suggest_branch.execute("proj-2"), "bugfix/PROJ-2-mail-user")
        self.assertEqual(self.app.suggest_branch.execute("PROJ-1", branch_type="chore"), "chore/PROJ-1-ask-user-or-email-about-the-jane-doe")


class SearchWithAFilterTest(Fixture):
    def setUp(self):
        super().setUp()
        self.gateway.page_cap = 100
        self.sent = []

        def answer(jql):
            self.sent.append(jql)
            return [self.gateway.issues["PROJ-1"]]

        self.gateway.search_handler = answer

    def searches(self):
        """The queries of the search itself, without the one that asks for the names of the epics."""
        return [jql for jql in self.sent if not jql.startswith("key in")]
        result = self.app.search_issues.execute(filter_id="10000")
        self.assertEqual((self.sent, result.jql), (["filter = 10000"], "filter = 10000"))

    def test_a_jql_narrows_the_filter(self):
        self.app.search_issues.execute(filter_id="10000", query="status = Done ORDER BY key")
        self.assertEqual(self.sent, ["filter = 10000 AND (status = Done) ORDER BY key"])

    def test_a_filter_that_jira_does_not_know_gets_a_hint_in_issues(self):
        self.gateway.errors["search"] = RejectedError("Jira refused the request (HTTP 400): The value '99999' does not exist for the field 'filter'.", hint="Check the JQL.", details={"status": 400})
        with self.assertRaises(RejectedError) as caught:
            self.app.search_issues.execute(filter_id="99999")
        self.assertIn("The value '99999'", caught.exception.message)
        self.assertIn("jira filter 99999", caught.exception.hint)
        self.assertEqual(caught.exception.details, {"status": 400})

    def test_a_refusal_without_a_filter_keeps_its_hint(self):
        self.gateway.errors["search"] = RejectedError("Jira refused", hint="Check the JQL.")
        with self.assertRaises(RejectedError) as caught:
            self.app.search_issues.execute(query="x")
        self.assertEqual(caught.exception.hint, "Check the JQL.")

    def test_mine_and_a_filter_exclude_each_other(self):
        with self.assertRaises(ValidationError):
            self.app.search_issues.execute(filter_id="10000", mine=True)

    def test_a_search_adds_the_filter_to_the_project_and_the_words(self):
        result = self.app.find_issues.execute(text="audit", filter_id="10000")
        self.assertEqual(self.searches(), ['project = "PROJ" AND filter = 10000 AND text ~ "audit"'])
        self.assertTrue(any("Filter 10000 is searched inside project PROJ only" in note for note in result.notes))

    def test_all_projects_searches_all_of_the_filter(self):
        result = self.app.find_issues.execute(text="audit", filter_id="10000", all_projects=True)
        self.assertEqual(self.searches(), ['filter = 10000 AND text ~ "audit"'])
        self.assertFalse(any("Filter 10000" in note for note in result.notes))

    def test_a_filter_alone_is_a_search(self):
        self.app.find_issues.execute(filter_id="10000", statuses=["To Do"])
        self.assertEqual(self.searches(), ['project = "PROJ" AND filter = 10000 AND status = "To Do" ORDER BY updated DESC'])

    def test_a_filter_that_jira_does_not_know_gets_a_hint_in_search(self):
        self.gateway.errors["search"] = RejectedError("Jira refused", hint="Check the JQL.", details={"status": 400})
        with self.assertRaises(RejectedError) as caught:
            self.app.find_issues.execute(text="audit", filter_id="99999")
        self.assertIn("jira filter 99999", caught.exception.hint)

    def test_a_bad_filter_id_is_refused_before_a_request(self):
        with self.assertRaises(ValidationError):
            self.app.find_issues.execute(text="audit", filter_id="x OR 1=1")
        self.assertEqual(self.sent, [])

    def test_phrases_are_searched_as_phrases(self):
        result = self.app.find_issues.execute(text="roles", phrases=["audit trail"], match="all")
        self.assertEqual(self.searches(), ['project = "PROJ" AND text ~ "roles" AND text ~ "\\"audit trail\\""'])
        self.assertEqual(result.terms, ('"audit trail"', "roles"))

    def test_phrases_alone_are_a_search(self):
        self.app.find_issues.execute(phrases=["audit trail", "append only"], match="all")
        self.assertEqual(self.searches(), ['project = "PROJ" AND text ~ "\\"audit trail\\"" AND text ~ "\\"append only\\""'])

    def test_an_empty_phrase_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.app.find_issues.execute(text="roles", phrases=[" "])


if __name__ == "__main__":
    unittest.main()
