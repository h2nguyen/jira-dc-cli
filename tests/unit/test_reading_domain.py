"""The pure parts of reading: the history of an issue, the parts of `issue --only`, rendered text, saved filters, JQL with a filter,
exact phrases, and the mapping of Jira's JSON for them."""
import time
import unittest

from jira_dc_cli.adapters.driven import map_reading
from jira_dc_cli.domain import jql
from jira_dc_cli.domain.errors import UsageError, ValidationError
from jira_dc_cli.domain.history import Changelog, FieldChange, HistoryEntry, StatusChange, status_changes
from jira_dc_cli.domain.issueparts import PARTS, parse_parts, split_keys
from jira_dc_cli.domain.rendered import rendered_text
from jira_dc_cli.domain.savedfilters import parse_filter_id
from jira_dc_cli.domain.searchterms import parse_terms, with_phrases

IDS = {"story_points": "customfield_10001", "epic_link": "customfield_10002", "sprint": "customfield_10003", "acceptance": "customfield_10004"}


def entry(created, *changes):
    return HistoryEntry(created, tuple(FieldChange(*change) for change in changes))


PROFILE = 'href="https://jira.example.com/secure/ViewProfile.jspa?name={name}" class="user-hover" rel="{name}"'
LEAK_WORDS = ("Jane", "John", "Jim", "jdoe", "jroe", "jpoe", "ViewProfile", "&")  # what must not be left of a mention that is hidden


def mention(name="jdoe", display="Jane Doe", closing="</a>", more=""):
    """A mention as Jira renders it: a link to the profile whose text is the display name. `closing` and `more` vary the tags."""
    return f"<a {PROFILE.format(name=name)}{more}>{display}{closing}"


def leak_variants():
    """The ways in which a mention may be written (Jira, a proxy or a plugin): name -> (HTML, the text without people, a word that shows when
    people are shown). The text of a link to a person is a display name: none of them may be left of it."""
    long_tag = mention(more=' data-x="' + "x" * 600 + '"')
    long_text = mention(display="Jane Doe" + "&amp;" * 100)
    longest_name = mention(display="&amp;" * 255)  # the longest display name, every character of it escaped
    line_end = mention(closing="</a\n>")
    profile_only = '<A HREF="/secure/ViewProfile.jspa?name=jdoe">Jane Doe</A>'
    return {
        "a closing tag in capitals": (f"<p>Ask {mention(closing='</A>')} now</p>", "Ask [~user] now", "Jane Doe"),
        "a blank inside the closing tag": (f"<p>Ask {mention(closing='</a >')} now</p>", "Ask [~user] now", "Jane Doe"),
        "capitals and blanks inside the closing tag": (f"<p>Ask {mention(closing='</A  >')} now</p>", "Ask [~user] now", "Jane Doe"),
        "a line end inside the closing tag": (f"<p>Ask {line_end} now</p>", "Ask [~user] now", "Jane Doe"),
        "an opening tag of 700 characters": (f"<p>Ask {long_tag} now</p>", "Ask [~user] now", "Jane Doe"),
        "a link text of 500 characters": (f"<p>Ask {long_text} now</p>", "Ask [~user] now", "Jane Doe"),
        "a link text of 1,275 characters": (f"<p>Ask {longest_name} now</p>", "Ask [~user] now", "&" * 255),
        "the link to the profile alone, in capitals": (f"<p>Ask {profile_only} now</p>", "Ask [~user] now", "Jane Doe"),
        "several mentions in one paragraph": (
            f"<p>Ask {mention()}, {mention('jroe', 'John Roe')} and {mention('jpoe', 'Jim Poe', closing='</A>')}.</p>",
            "Ask [~user], [~user] and [~user].",
            "John Roe",
        ),
        "a mention inside a list item": (
            f"<ul><li>Review by {mention()}</li><li>Ask {mention('jroe', 'John Roe', closing='</A >')}</li></ul>",
            "- Review by [~user]\n- Ask [~user]",
            "John Roe",
        ),
        "a mention inside a numbered list": (f"<ol><li>First, {mention()}</li><li>then {mention('jroe', 'John Roe')}</li></ol>", "1. First, [~user]\n2. then [~user]", "Jane Doe"),
    }


class StatusChangesTest(unittest.TestCase):
    def test_only_the_changes_of_the_status_are_kept(self):
        changelog = Changelog(
            (
                entry("2026-10-01T08:00:00.000+0200", ("assignee", "", "jdoe"), ("status", "To Do", "In Progress")),
                entry("2026-10-02T08:00:00.000+0200", ("labels", "", "x")),
                entry("2026-10-03T08:00:00.000+0200", ("Status", "In Progress", "Done")),
            ),
            3,
        )
        self.assertEqual(
            status_changes(changelog),
            [
                StatusChange("2026-10-01T08:00:00.000+0200", "To Do", "In Progress"),
                StatusChange("2026-10-03T08:00:00.000+0200", "In Progress", "Done"),
            ],
        )

    def test_oldest_first_whatever_the_order_that_came(self):
        changelog = Changelog((entry("2026-10-03T08:00:00.000+0200", ("status", "B", "C")), entry("2026-10-01T08:00:00.000+0200", ("status", "A", "B"))), 2)
        self.assertEqual([item.to_status for item in status_changes(changelog)], ["B", "C"])

    def test_the_end_of_summer_time_does_not_confuse_the_order(self):
        # 02:30 at +02:00 is half an hour before 02:10 at +01:00, although the text of its time is the larger one
        first, second = "2026-10-25T02:30:00.000+0200", "2026-10-25T02:10:00.000+0100"
        changelog = Changelog((entry(second, ("status", "B", "C")), entry(first, ("status", "A", "B"))), 2)
        self.assertEqual([item.created for item in status_changes(changelog)], [first, second])

    def test_several_changes_in_one_entry_and_missing_texts(self):
        changelog = Changelog((entry("2026-10-01T08:00:00.000+0200", ("status", "", "Open"), ("status", "Open", "")),), 1)
        self.assertEqual([(item.from_status, item.to_status) for item in status_changes(changelog)], [("", "Open"), ("Open", "")])

    def test_a_time_that_cannot_be_read_does_not_stop_the_history(self):
        changelog = Changelog((entry("2026-10-02T08:00:00.000+0200", ("status", "B", "C")), entry("yesterday", ("status", "A", "B"))), 2)
        self.assertEqual([item.to_status for item in status_changes(changelog)], ["B", "C"])

    def test_no_history_no_changes(self):
        self.assertEqual(status_changes(Changelog()), [])

    def test_complete_says_whether_jira_sent_all_of_it(self):
        self.assertTrue(Changelog((entry("x"),), 1).complete)
        self.assertTrue(Changelog().complete)
        self.assertFalse(Changelog((entry("x"),), 130).complete)


class PartsTest(unittest.TestCase):
    def test_the_names_are_the_documented_ones(self):
        self.assertEqual(
            PARTS,
            ("summary", "status", "priority", "labels", "components", "fixVersions", "epic", "points", "sprints", "dates", "description", "acceptance", "comments", "links", "subtasks", "attachments"),
        )

    def test_names_in_the_order_of_the_parts_without_duplicates(self):
        self.assertEqual(parse_parts("links, STATUS ,summary,status,,"), ("summary", "status", "links"))

    def test_case_and_separators_inside_a_name_do_not_matter(self):
        self.assertEqual(parse_parts("fix-versions"), ("fixVersions",))
        self.assertEqual(parse_parts("FIXVERSIONS,fix_versions"), ("fixVersions",))

    def test_an_unknown_name_is_a_usage_error_that_lists_the_valid_names(self):
        with self.assertRaises(UsageError) as caught:
            parse_parts("summary,nope,labl")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("nope, labl", caught.exception.message)
        for name in PARTS:
            self.assertIn(name, caught.exception.hint)

    def test_no_name_at_all_is_a_usage_error(self):
        for text in ("", " ", ",,"):
            with self.subTest(text=text), self.assertRaises(UsageError):
                parse_parts(text)

    def test_keys_by_blanks_and_commas(self):
        self.assertEqual(split_keys("PROJ-1"), ["PROJ-1"])
        self.assertEqual(split_keys("PROJ-1 PROJ-2,PROJ-3, PROJ-4 ,,"), ["PROJ-1", "PROJ-2", "PROJ-3", "PROJ-4"])
        self.assertEqual(split_keys(""), [])


class RenderedTextTest(unittest.TestCase):
    MENTION = '<p>Ask <a href="https://jira.example.com/secure/ViewProfile.jspa?name=jdoe" class="user-hover" rel="jdoe">Jane Doe</a> or jane.doe@example.com</p>'

    def test_structure_is_kept_and_a_mention_is_not_a_person(self):
        text = rendered_text("<ul><li>a</li><li>b</li></ul><ol><li>one</li></ol><pre>x = 1</pre>" + self.MENTION)
        self.assertEqual(text, "- a\n- b\n\n1. one\n\n```\nx = 1\n```\n\nAsk [~user] or [email]")
        self.assertNotIn("Jane", text)
        self.assertNotIn("jdoe", text)

    def test_people_are_shown_only_when_asked_for(self):
        text = rendered_text(self.MENTION, people_shown=True)
        self.assertIn("Jane Doe", text)
        self.assertIn("jane.doe@example.com", text)

    def test_a_link_to_a_profile_without_the_class_is_a_mention_too(self):
        text = rendered_text('<a href="/secure/ViewProfile.jspa?name=jdoe">Jane Doe</a> and <a href="http://example.com/x">a link</a>')
        self.assertEqual(text, "[~user] and a link (http://example.com/x)")

    def test_no_mention_leaks_a_name_however_it_is_written(self):
        for variant, (html, expected, _) in leak_variants().items():
            with self.subTest(variant=variant):
                text = rendered_text(html)
                self.assertEqual(text, expected)
                for word in LEAK_WORDS:
                    self.assertNotIn(word, text)

    def test_with_people_every_one_of_them_shows_its_name_as_before(self):
        for variant, (html, _, shown) in leak_variants().items():
            with self.subTest(variant=variant):
                self.assertIn(shown, rendered_text(html, people_shown=True))

    def test_what_is_no_mention_stays_whatever_the_case_of_the_tags(self):
        other = '<A HREF="https://example.com/page">a page</A>'
        self.assertEqual(rendered_text(f"<p>{other} and {mention()}</p>"), "a page (https://example.com/page) and [~user]")

    def test_an_address_in_a_mailto_link_is_redacted(self):
        self.assertNotIn("example.com", rendered_text('<a href="mailto:jane.doe@example.com">jane.doe@example.com</a>'))

    def test_empty_input(self):
        for html in ("", "   ", None):
            self.assertEqual(rendered_text(html), "")

    def test_wiki_that_jira_did_not_render_stays_visible(self):
        self.assertEqual(rendered_text("<p>{{token }} stays</p>"), "{{token }} stays")

    def test_hostile_html_is_read_in_linear_time(self):
        started = time.perf_counter()
        rendered_text("<a " * 100_000 + "user-hover")
        rendered_text('<a class="user-hover" ' * 50_000)
        rendered_text("<a href='x' class='user-hover'>" + "x" * 300_000)
        self.assertLess(time.perf_counter() - started, 5.0)


class SavedFilterIdTest(unittest.TestCase):
    def test_a_number_is_an_id(self):
        self.assertEqual(parse_filter_id("10000"), "10000")
        self.assertEqual(parse_filter_id(" 42 "), "42")

    def test_anything_else_is_refused_with_a_hint(self):
        for text in ("", "abc", "-5", "1.5", "10000 OR project = X", '"My filter"', "1" * 30):
            with self.subTest(text=text), self.assertRaises(ValidationError) as caught:
                parse_filter_id(text)
            self.assertIn("jira filters", caught.exception.hint)


class FilterInJqlTest(unittest.TestCase):
    def test_the_clause(self):
        self.assertEqual(jql.filter_clause("10000"), "filter = 10000")
        with self.assertRaises(ValidationError):
            jql.filter_clause("1 OR project = SECRET")

    def test_a_filter_replaces_the_project_query_of_issues(self):
        self.assertEqual(jql.issue_search(project="PROJ", filter_id="10000"), "filter = 10000")

    def test_a_jql_narrows_the_filter_and_its_order_is_kept(self):
        self.assertEqual(jql.issue_search(jql="status = Done", filter_id="7"), "filter = 7 AND (status = Done)")
        self.assertEqual(jql.issue_search(jql="status = Done ORDER BY key DESC", filter_id="7"), "filter = 7 AND (status = Done) ORDER BY key DESC")
        self.assertEqual(jql.issue_search(jql="ORDER BY key", filter_id="7"), "filter = 7 ORDER BY key")

    def test_a_jql_that_does_not_close_cannot_end_the_brackets(self):
        with self.assertRaises(ValidationError):
            jql.issue_search(jql='status = Done) OR (project = "X"', filter_id="7")

    def test_mine_and_a_filter_exclude_each_other(self):
        with self.assertRaises(ValidationError):
            jql.issue_search(mine=True, filter_id="7")

    def test_without_a_filter_nothing_changed(self):
        self.assertEqual(jql.issue_search(project="PROJ"), 'project = "PROJ" ORDER BY created DESC')
        self.assertEqual(jql.issue_search(jql="status = Done"), "status = Done")

    def test_a_search_adds_the_filter_after_the_project_like_every_other_condition(self):
        text = jql.search_query(terms=parse_terms("audit"), project="PROJ", statuses=["To Do"], filter_id="10000")
        self.assertEqual(text, 'project = "PROJ" AND filter = 10000 AND text ~ "audit" AND status = "To Do"')

    def test_a_filter_alone_is_a_search_that_comes_newest_first(self):
        self.assertEqual(jql.search_query(filter_id="10000"), "filter = 10000 ORDER BY updated DESC")
        self.assertEqual(jql.search_query(project="PROJ", filter_id="10000"), 'project = "PROJ" AND filter = 10000 ORDER BY updated DESC')

    def test_a_search_checks_the_id(self):
        with self.assertRaises(ValidationError):
            jql.search_query(filter_id="x OR 1=1")


class PhrasesTest(unittest.TestCase):
    def test_phrases_come_first_and_stand_in_quotes(self):
        self.assertEqual(with_phrases("roles", ["audit trail", "append only"]), '"audit trail" "append only" roles')
        self.assertEqual(with_phrases("", ["audit trail"]), '"audit trail"')
        self.assertEqual(with_phrases("  roles  ", []), "roles")

    def test_a_phrase_becomes_a_phrase_term(self):
        terms = parse_terms(with_phrases("roles", ["audit trail"]))
        self.assertEqual([(term.text, term.phrase) for term in terms.used], [("audit trail", True), ("roles", False)])

    def test_quotes_inside_a_phrase_cannot_break_out(self):
        for phrase in ('say "hello" now', "say \u201chello\u201d now", 'say "hello now'):
            with self.subTest(phrase=phrase):
                self.assertEqual(with_phrases("", [phrase]).count('"'), 2)

    def test_an_unbalanced_quote_in_the_text_does_not_swallow_a_phrase(self):
        terms = parse_terms(with_phrases('foo "bar', ["audit trail"]))
        self.assertIn(("audit trail", True), [(term.text, term.phrase) for term in terms.used])

    def test_a_single_word_phrase_is_kept_even_when_it_is_filler(self):
        self.assertEqual([term.text for term in parse_terms(with_phrases("", ["the"])).used], ["the"])

    def test_an_empty_phrase_is_a_usage_error(self):
        for phrase in ("", "  ", '""'):
            with self.subTest(phrase=phrase), self.assertRaises(UsageError):
                with_phrases("roles", [phrase])


class MappingTest(unittest.TestCase):
    def test_the_changelog_loses_its_authors_and_keeps_the_texts(self):
        changelog = map_reading.changelog_from(
            {
                "startAt": 0,
                "maxResults": 100,
                "total": 130,
                "histories": [
                    {
                        "id": "1",
                        "author": {"name": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com"},
                        "created": "2026-10-01T08:00:00.000+0200",
                        "items": [{"field": "status", "fieldtype": "jira", "from": "1", "fromString": "To Do", "to": "3", "toString": "In Progress"}],
                    }
                ],
            }
        )
        self.assertEqual(changelog, Changelog((entry("2026-10-01T08:00:00.000+0200", ("status", "To Do", "In Progress")),), 130))
        self.assertNotIn("jdoe", repr(changelog))
        self.assertNotIn("Jane", repr(changelog))

    def test_only_the_changes_of_the_status_come_in_the_others_may_name_people(self):
        changelog = map_reading.changelog_from(
            {
                "total": 2,
                "histories": [
                    {"created": "a", "items": [{"field": "assignee", "fromString": "Jane Doe", "toString": "John Roe"}, {"field": "Status", "fromString": "A", "toString": "B"}]},
                    {"created": "b", "items": [{"field": "Reporter", "fromString": "Jane Doe", "toString": "John Roe"}]},
                ],
            }
        )
        self.assertEqual(changelog, Changelog((entry("a", ("Status", "A", "B")), entry("b")), 2))
        self.assertNotIn("Jane", repr(changelog))
        self.assertTrue(changelog.complete)  # an entry without a change of the status still counts against the total

    def test_a_custom_field_that_is_called_status_is_not_the_status(self):
        changelog = map_reading.changelog_from(
            {
                "total": 1,
                "histories": [
                    {
                        "created": "a",
                        "items": [
                            {"field": "status", "fieldtype": "custom", "fieldId": "customfield_10500", "fromString": "Jane Doe", "toString": "John Roe"},
                            {"field": "Status", "fieldtype": "jira", "fieldId": "status", "fromString": "A", "toString": "B"},
                        ],
                    }
                ],
            }
        )
        self.assertEqual(changelog, Changelog((entry("a", ("Status", "A", "B")),), 1))
        self.assertNotIn("Jane", repr(changelog))

    def test_the_type_of_the_field_is_asked_only_when_it_is_there(self):
        for item, kept in (
            ({"field": "status"}, True),
            ({"field": "status", "fieldtype": None}, True),
            ({"field": "status", "fieldtype": ""}, True),
            ({"field": "status", "fieldtype": "jira"}, True),
            ({"field": "STATUS", "fieldtype": "JIRA"}, True),
            ({"field": "status", "fieldtype": "custom"}, False),
            ({"field": "status", "fieldtype": "Custom"}, False),
            ({"field": "status", "fieldtype": "other"}, False),
            ({"field": "status", "fieldtype": 5}, False),
            ({"field": "assignee", "fieldtype": "jira"}, False),
        ):
            with self.subTest(item=item):
                changelog = map_reading.changelog_from({"histories": [{"created": "a", "items": [{**item, "fromString": "x", "toString": "y"}]}]})
                self.assertEqual(len(changelog.entries), 1)  # the entry stays, so that it counts against the total
                self.assertEqual(len(changelog.entries[0].changes), 1 if kept else 0)

    def test_no_changelog_in_the_answer_is_none_and_odd_ones_do_not_crash(self):
        self.assertIsNone(map_reading.changelog_from(None))
        self.assertIsNone(map_reading.changelog_from("x"))
        self.assertEqual(map_reading.changelog_from({}), Changelog())
        odd = map_reading.changelog_from({"total": "many", "histories": [None, {"created": None, "items": [None, {"field": None}, {"field": "status"}]}]})
        self.assertEqual(odd, Changelog((HistoryEntry("", (FieldChange("status", "", ""),)),), 1))

    def test_a_total_smaller_than_what_came_is_not_believed(self):
        changelog = map_reading.changelog_from({"total": 0, "histories": [{"created": "x", "items": []}]})
        self.assertEqual(changelog.total, 1)

    def test_an_expanded_issue_has_the_rendered_texts_and_the_history(self):
        data = {
            "key": "PROJ-1",
            "fields": {"summary": "S", "description": "* a", "customfield_10004": "1. x"},
            "renderedFields": {"description": "<ul><li>a</li></ul>", "customfield_10004": "<ol><li>x</li></ol>"},
            "changelog": {"total": 0, "histories": []},
        }
        expanded = map_reading.expanded_from(data, IDS)
        self.assertEqual((expanded.issue.key, expanded.issue.description, expanded.issue.acceptance), ("PROJ-1", "* a", "1. x"))
        self.assertEqual((expanded.rendered_description, expanded.rendered_acceptance), ("<ul><li>a</li></ul>", "<ol><li>x</li></ol>"))
        self.assertEqual(expanded.changelog, Changelog())

    def test_an_issue_without_the_expansions(self):
        expanded = map_reading.expanded_from({"key": "PROJ-1", "fields": {}}, IDS)
        self.assertEqual((expanded.rendered_description, expanded.rendered_acceptance, expanded.changelog), ("", "", None))
        self.assertEqual(map_reading.expanded_from({"key": "PROJ-1", "fields": {}, "renderedFields": []}, {}).rendered_description, "")

    def test_a_filter(self):
        item = map_reading.filter_from(
            {
                "id": "10000",
                "name": "Open bugs",
                "description": "d",
                "jql": "project = PROJ",
                "favourite": True,
                "owner": {"name": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com"},
                "searchUrl": "https://jira.example.com/rest/api/2/search?jql=x",
                "viewUrl": "https://jira.example.com/issues/?filter=10000",
            }
        )
        self.assertEqual((item.id, item.name, item.description, item.jql, item.favourite), ("10000", "Open bugs", "d", "project = PROJ", True))
        self.assertEqual((item.owner.name, item.owner.display_name, item.owner.email), ("jdoe", "Jane Doe", "jane.doe@example.com"))
        self.assertEqual((item.search_url, item.view_url), ("https://jira.example.com/rest/api/2/search?jql=x", "https://jira.example.com/issues/?filter=10000"))

    def test_a_filter_that_is_nearly_empty(self):
        item = map_reading.filter_from({"id": 7})
        self.assertEqual((item.id, item.name, item.jql, item.favourite, item.owner), ("7", "", "", False, None))


if __name__ == "__main__":
    unittest.main()
