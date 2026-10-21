"""The query of `jira search`: words, filters and the project, built so that no text can add a clause."""
import unittest

from jira_dc_cli.domain import jql
from jira_dc_cli.domain.errors import UsageError, ValidationError
from jira_dc_cli.domain.searchterms import Term, parse_terms


def query(text="", **options):
    return jql.search_query(terms=parse_terms(text), **options)


class TextClauseTest(unittest.TestCase):
    def test_all_words_are_one_clause_and_a_phrase_is_its_own(self):
        self.assertEqual(jql.text_clause([Term("audit"), Term("trail")]), 'text ~ "audit trail"')
        self.assertEqual(jql.text_clause([Term("audit"), Term("append only", True)]), 'text ~ "audit" AND text ~ "\\"append only\\""')

    def test_any_is_a_group_of_alternatives_and_one_word_stays_plain(self):
        self.assertEqual(jql.text_clause([Term("audit"), Term("trail")], "any"), '(text ~ "audit" OR text ~ "trail")')
        self.assertEqual(jql.text_clause([Term("audit")], "any"), 'text ~ "audit"')

    def test_no_terms_no_clause_and_a_wrong_mode_is_refused(self):
        self.assertEqual(jql.text_clause([]), "")
        with self.assertRaises(ValidationError):
            jql.text_clause([Term("a")], "some")

    def test_a_quote_or_a_backslash_in_a_word_cannot_leave_the_string(self):
        clause = jql.text_clause([Term('x" OR project = SECRET OR text ~ "y')])
        self.assertEqual(clause.count('"') - clause.count('\\"'), 2)  # the two quotes that open and close the string
        self.assertEqual(jql.text_clause([Term("a\\b")]), 'text ~ "a\\\\b"')


class SearchQueryTest(unittest.TestCase):
    def test_a_question_in_the_project(self):
        self.assertEqual(query("how do I export the monthly report?", project="PROJ"), 'project = "PROJ" AND text ~ "export monthly report"')

    def test_without_a_project_the_whole_site_is_searched(self):
        self.assertEqual(query("audit"), 'text ~ "audit"')

    def test_the_filters_in_a_fixed_order(self):
        text = query(
            "audit",
            project="PROJ",
            issue_types=["Story", "Bug"],
            statuses=["In Progress"],
            labels=["backend"],
            sprint="active",
            fix_version="Release 1.0",
            since="2026-10-01",
        )
        self.assertEqual(
            text,
            'project = "PROJ" AND text ~ "audit" AND issuetype in ("Story", "Bug") AND status = "In Progress" AND labels = "backend" '
            'AND sprint in openSprints() AND fixVersion = "Release 1.0" AND updated >= "2026-10-01"',
        )

    def test_a_named_sprint_and_a_time_ago(self):
        self.assertEqual(query("x1", sprint="Sprint 3", since="7d"), 'text ~ "x1" AND sprint = "Sprint 3" AND updated >= -7d')
        self.assertIn("updated >= -2w", query("x1", since="-2w"))
        self.assertIn('updated >= "2026-10-01 08:30"', query("x1", since="2026-10-01 08:30"))

    def test_active_is_the_open_sprints_whatever_its_case(self):
        for word in ("active", "Active", "ACTIVE"):
            with self.subTest(word=word):
                self.assertIn("sprint in openSprints()", query("x1", sprint=word))

    def test_a_condition_of_your_own_must_close_its_brackets_and_quotes(self):
        for extra in ("x) OR (project = OTHER", ")", "(a = 1", 'summary ~ "open', "a = 1) OR (b = 2"):
            with self.subTest(extra=extra):
                with self.assertRaises(ValidationError) as caught:
                    query("audit", project="PROJ", extra=extra)
                self.assertIn("does not close", caught.exception.message)
        self.assertIn("(a = 1 AND (b = 2 OR c = 3))", query("audit", project="PROJ", extra="a = 1 AND (b = 2 OR c = 3)"))
        self.assertIn('summary ~ "a ) b"', query("audit", project="PROJ", extra='summary ~ "a ) b"'))  # a bracket inside quotes does not count
        self.assertIn('summary ~ "say \\") now"', query("audit", project="PROJ", extra='summary ~ "say \\") now"'))

    def test_under_covers_the_stories_of_an_epic_and_the_subtasks_of_a_story(self):
        self.assertEqual(query("", project="PROJ", under="PROJ-5"), 'project = "PROJ" AND ("Epic Link" = PROJ-5 OR parent = PROJ-5) ORDER BY updated DESC')
        self.assertIn("(cf[10002] = PROJ-5 OR parent = PROJ-5)", query("", under="PROJ-5", epic_field="cf[10002]"))

    def test_without_words_the_newest_come_first_with_words_jiras_relevance_order_stays(self):
        self.assertTrue(query("", project="PROJ", statuses=["Done"]).endswith("ORDER BY updated DESC"))
        self.assertNotIn("ORDER BY", query("audit", project="PROJ"))

    def test_a_condition_of_your_own_is_added_and_its_order_is_kept(self):
        text = query("audit", project="PROJ", extra="priority = High ORDER BY created ASC")
        self.assertEqual(text, 'project = "PROJ" AND text ~ "audit" AND (priority = High) ORDER BY created ASC')
        self.assertEqual(query("", project="PROJ", labels=["a"], extra="ORDER BY rank"), 'project = "PROJ" AND labels = "a" ORDER BY rank')
        with self.assertRaises(UsageError):  # a project and an order are a listing, which is what `jira issues` is for
            query("", project="PROJ", extra="ORDER BY rank")

    def test_an_order_by_inside_quotes_or_a_word_is_no_order_by(self):
        self.assertEqual(jql.split_order_by('summary ~ "order by me" AND x = 1'), ('summary ~ "order by me" AND x = 1', ""))
        self.assertEqual(jql.split_order_by("reorder by = 1"), ("reorder by = 1", ""))
        self.assertEqual(jql.split_order_by('a = "x\\" ORDER BY y" order by key'), ('a = "x\\" ORDER BY y"', "order by key"))
        self.assertEqual(jql.split_order_by("a = 1 ORDER  BY b"), ("a = 1", "ORDER  BY b"))

    def test_nothing_to_search_for_is_explained(self):
        for options in ({}, {"project": "PROJ"}):
            with self.subTest(options=options):
                with self.assertRaises(UsageError) as caught:
                    query("?!", **options)
                self.assertIn("--status", caught.exception.hint)  # the hint names the filters

    def test_a_filter_alone_is_a_search(self):
        self.assertEqual(query("", project="PROJ", labels=["a", "b"]), 'project = "PROJ" AND labels in ("a", "b") ORDER BY updated DESC')

    def test_a_filter_value_cannot_leave_its_string(self):
        text = query("", statuses=['Done" OR project = X OR status = "Open'])
        self.assertEqual(text.count('"') - text.count('\\"'), 2)
        self.assertNotIn("\x00", query("", labels=["a\x00b"]))

    def test_bad_values_are_refused_before_they_reach_jira(self):
        for options in ({"under": "5"}, {"under": "PROJ-5 OR 1=1"}, {"epic_field": "cf[1] OR x"}, {"since": "yesterday"}, {"since": "2026-13"}):
            with self.subTest(options=options):
                with self.assertRaises(ValidationError):
                    query("x1", **options)


class ExistingQueriesStayTest(unittest.TestCase):
    def test_the_listing_and_the_reports_are_as_before(self):
        self.assertEqual(jql.issue_search(project="PROJ"), 'project = "PROJ" ORDER BY created DESC')
        self.assertEqual(jql.backlog_search("PROJ"), 'project = "PROJ" ORDER BY key ASC')
        self.assertEqual(jql.issue_search(project="PROJ", jql="status = Done"), "status = Done")


if __name__ == "__main__":
    unittest.main()
