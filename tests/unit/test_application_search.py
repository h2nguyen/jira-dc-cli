"""`jira search`: a question becomes a query, the answer says what it did, and a hit can be judged without opening the issue."""
import dataclasses
import json
import unittest

from jira_dc_cli.domain.errors import RejectedError, UsageError
from jira_dc_cli.domain.model import Sprint
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, RecordingDiagnostics, build_app, sample_issue


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


def issues(count, **overrides):
    return [sample_issue(f"PROJ-{number}", "Story", "To Do", f"Story {number}", 3.0, "", **overrides) for number in range(1, count + 1)]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.gateway.page_cap = 100
        self.sent: list[str] = []
        self.answers = {"all": issues(6), "any": issues(8)}
        self.gateway.search_handler = self.answer
        self.app = build_app(gateway=self.gateway)

    def answer(self, jql):
        self.sent.append(jql)
        if jql.startswith("key in"):
            return [dataclasses.replace(issue, summary=f"Epic {issue.key}") for issue in self.epics]
        return self.answers["any" if " OR " in jql else "all"]

    epics: list = []

    def find(self, text="", **options):
        return self.app.find_issues.execute(text=text, **options)


class QueryTest(Fixture):
    def test_a_question_is_searched_by_its_meaningful_words_in_the_project(self):
        result = self.find("how do I export the monthly report for ACME?")
        self.assertEqual(self.sent, ['project = "PROJ" AND text ~ "export monthly report ACME"'])
        self.assertEqual(result.terms, ("export", "monthly", "report", "ACME"))
        self.assertEqual(result.ignored, ("how", "do", "I", "the", "for"))
        self.assertEqual((result.match, result.relaxed, result.scope), ("all", False, "project PROJ"))
        self.assertTrue(any("Left out as filler: how, do, I, the, for" in note for note in result.notes))

    def test_what_jira_refuses_never_reaches_it(self):
        self.find("[x] ~ ! ( role x) {y} audit^")
        self.assertEqual(self.sent, ['project = "PROJ" AND text ~ "x role y audit"'])  # `x` once: the second one is the same word

    def test_a_hostile_text_cannot_add_a_clause(self):
        self.find('x" OR project = SECRET OR text ~ "y')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].count("project ="), 1)
        self.assertEqual(self.sent[0].count('"') - self.sent[0].count('\\"'), 4)

    def test_the_scope_is_one_project_and_all_projects_widens_it(self):
        self.find("audit", project="demo")
        self.find("audit", all_projects=True)
        self.assertEqual(self.sent, ['project = "DEMO" AND text ~ "audit"', 'text ~ "audit"'])
        self.assertEqual(self.find("audit", all_projects=True).scope, "every project")

    def test_a_search_needs_a_project_or_all_projects(self):
        app = build_app(gateway=self.gateway, settings=settings(project=""))
        with self.assertRaises(UsageError) as caught:
            app.find_issues.execute(text="audit")
        self.assertIn("--all-projects", caught.exception.hint)
        with self.assertRaises(UsageError):
            self.find("audit", project="PROJ", all_projects=True)

    def test_filters_become_clauses_and_a_filter_alone_is_a_search(self):
        self.find("", issue_types=["Story"], statuses=["To Do", "In Progress"], labels=["backend"], sprint="active", fix_version="Release 1.0", since="7d")
        self.assertEqual(
            self.sent[-1],
            'project = "PROJ" AND issuetype = "Story" AND status in ("To Do", "In Progress") AND labels = "backend" AND sprint in openSprints() '
            'AND fixVersion = "Release 1.0" AND updated >= -7d ORDER BY updated DESC',
        )

    def test_under_uses_the_field_id_of_the_instance(self):
        self.find("", under="proj-5")
        self.assertEqual(self.sent[-1], 'project = "PROJ" AND (cf[10002] = PROJ-5 OR parent = PROJ-5) ORDER BY updated DESC')

    def test_a_condition_of_your_own_is_added(self):
        self.find("audit", extra_jql="priority = High ORDER BY created ASC")
        self.assertEqual(self.sent[-1], 'project = "PROJ" AND text ~ "audit" AND (priority = High) ORDER BY created ASC')

    def test_nothing_to_search_for_is_an_error(self):
        for text in ("", "?!", "~"):
            with self.subTest(text=text):
                with self.assertRaises(UsageError):
                    self.find(text)
        self.assertEqual(self.sent, [])

    def test_a_text_of_punctuation_with_a_filter_says_that_only_the_filter_applies(self):
        result = self.find("?!", statuses=["Done"])
        self.assertTrue(any("only punctuation" in note for note in result.notes))

    def test_no_person_is_asked_for(self):
        self.find("audit", snippets=3)
        asked = self.gateway.called("search")[0][2]
        self.assertNotIn("assignee", asked)
        self.assertNotIn("reporter", asked)
        self.assertNotIn("comment", asked)

    def test_wrong_options_are_refused(self):
        for options in ({"match": "some"}, {"snippets": -1}, {"snippets": 26}, {"limit": 0}, {"start_at": -1}):
            with self.subTest(options=options):
                with self.assertRaises(UsageError):
                    self.find("audit", **options)


class LoosenTest(Fixture):
    def test_fewer_than_five_issues_with_every_word_loosen_the_search_and_mark_them(self):
        self.answers["all"] = issues(2)
        self.answers["any"] = issues(8)
        result = self.find("who may write rules for an asset type")
        self.assertEqual(len(self.sent), 2)
        self.assertIn(" OR ", self.sent[1])
        self.assertEqual((result.match, result.relaxed, result.total), ("any", True, 8))
        self.assertEqual([hit.all_words for hit in result.hits], [True, True, False, False, False, False, False, False])
        note = next(note for note in result.notes if "hold" in note and "all of these words" in note)
        self.assertIn("Only 2 issues hold all", note)
        self.assertIn("`allWords`", note)

    def test_one_issue_is_singular(self):
        self.answers["all"] = issues(1)
        result = self.find("alpha beta")
        self.assertTrue(any("Only 1 issue holds all" in note for note in result.notes))

    def test_no_issue_with_every_word_is_said_so(self):
        self.answers["all"] = []
        result = self.find("alpha beta")
        self.assertTrue(any(note.startswith("No issue holds all of these words (alpha, beta)") for note in result.notes))
        self.assertTrue(all(hit.all_words is False for hit in result.hits))

    def test_five_are_enough(self):
        self.answers["all"] = issues(5)
        result = self.find("alpha beta")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((result.relaxed, result.match), (False, "all"))
        self.assertTrue(all(hit.all_words is True for hit in result.hits))

    def test_four_are_not(self):
        self.answers["all"] = issues(4)
        result = self.find("alpha beta")
        self.assertEqual((len(self.sent), result.relaxed, result.match), (2, True, "any"))

    def test_the_strict_hits_are_known_even_when_only_a_few_hits_are_shown(self):
        strict = issues(4)
        self.answers["all"] = strict
        self.answers["any"] = [strict[3], strict[2], *issues(8)[4:]]  # the best matches are the strict ones that are not first in the strict list
        result = self.find("alpha beta", limit=2)
        self.assertEqual([(hit.issue.key, hit.all_words) for hit in result.hits], [("PROJ-4", True), ("PROJ-3", True)])

    def test_match_all_never_loosens_and_match_any_goes_straight_to_it(self):
        self.answers["all"] = issues(1)
        strict = self.find("alpha beta", match="all")
        self.assertEqual((len(self.sent), strict.relaxed, strict.total), (1, False, 1))
        self.sent.clear()
        loose = self.find("alpha beta", match="any")
        self.assertEqual(len(self.sent), 1)
        self.assertIn(" OR ", self.sent[0])
        self.assertEqual((loose.match, loose.relaxed), ("any", False))
        self.assertTrue(all(hit.all_words is None for hit in loose.hits))

    def test_one_word_is_never_loosened(self):
        self.answers["all"] = []
        result = self.find("audit")
        self.assertEqual((len(self.sent), result.relaxed), (1, False))
        self.assertTrue(any(note.startswith("Nothing found in project PROJ") for note in result.notes))

    def test_a_later_page_knows_the_strict_hits_too(self):
        self.answers["all"] = issues(2)
        result = self.find("alpha beta", start_at=2, limit=3)
        self.assertEqual([hit.all_words for hit in result.hits], [False, False, False])  # the two strict ones are on the first page
        self.assertEqual(len(self.sent), 3)  # the strict count, the strict hits again, the loosened page

    def test_the_hits_of_the_strict_page_beyond_the_limit_are_cut(self):
        self.answers["all"] = issues(9)
        result = self.find("alpha beta", limit=3)
        self.assertEqual(len(result.hits), 3)
        self.assertEqual(result.total, 9)

    def test_everything_pages_through_the_loosened_list_too(self):
        self.answers["all"] = []
        self.answers["any"] = issues(30)
        result = self.find("alpha beta", fetch_everything=True)
        self.assertEqual(len(result.hits), 30)


class PagingTest(Fixture):
    def test_a_server_that_answers_with_fewer_per_page_still_fills_the_page(self):
        self.gateway.page_cap = 2
        self.answers["all"] = issues(9)
        result = self.find("audit", limit=5)
        self.assertEqual(len(result.hits), 5)
        self.assertEqual([call[3] for call in self.gateway.called("search")], [0, 2, 4])  # where each request began
        self.assertEqual([call[4] for call in self.gateway.called("search")], [5, 3, 1])  # what was still missing was asked for

    def test_start_at_is_honoured(self):
        self.answers["all"] = issues(9)
        result = self.find("audit", limit=3, start_at=4)
        self.assertEqual([hit.issue.key for hit in result.hits], ["PROJ-5", "PROJ-6", "PROJ-7"])

    def test_everything_stops_at_the_cap_and_says_so(self):
        self.answers["all"] = issues(2400)
        result = self.find("audit", fetch_everything=True)
        self.assertEqual(len(result.hits), 2000)
        self.assertTrue(result.truncated)
        self.assertTrue(any("Stopped after 2000" in note for note in result.notes))

    def test_a_server_that_answers_nothing_does_not_loop(self):
        self.gateway.search_handler = lambda jql: []
        self.assertEqual(self.find("audit").hits, [])


class HitTest(Fixture):
    def test_a_hit_says_where_it_belongs(self):
        self.answers["all"] = [sample_issue("PROJ-33", "Story", "In Progress", "Schedule the report", 5.0, "PROJ-5", labels=["export"], sprints=[Sprint("Sprint 2", "ACTIVE", 1)])]
        self.epics = [sample_issue("PROJ-5", "Epic", "To Do", "Authentication")]
        hit = self.find("roles").hits[0]
        self.assertEqual((hit.issue.epic_key, hit.epic_summary, hit.url), ("PROJ-5", "Epic PROJ-5", "https://jira.example.com/browse/PROJ-33"))
        self.assertEqual(self.sent[-1], "key in (PROJ-5)")  # one more request for the epic names

    def test_an_epic_among_the_hits_needs_no_request(self):
        self.answers["all"] = [sample_issue("PROJ-5", "Epic", "To Do", "Authentication"), sample_issue("PROJ-33", "Story", "To Do", "Roles", 3.0, "PROJ-5")]
        hits = self.find("roles").hits
        self.assertEqual(hits[1].epic_summary, "Authentication")
        self.assertEqual(len(self.sent), 1)

    def test_the_name_of_an_epic_is_redacted_like_every_other_text(self):
        self.answers["all"] = [sample_issue("PROJ-33", "Story", "To Do", "Roles", 3.0, "PROJ-5")]
        real = self.gateway.search_handler

        def handler(jql):
            if jql.startswith("key in"):
                return [sample_issue("PROJ-5", "Epic", "To Do", "Owner jane.doe@example.com and [~jdoe]")]
            return real(jql)

        self.gateway.search_handler = handler
        self.assertEqual(self.find("roles").hits[0].epic_summary, "Owner [email] and [~user]")

    def test_epics_beyond_the_hits_come_in_one_request_and_a_failure_is_a_note(self):
        self.answers["all"] = [sample_issue(f"PROJ-{n}", "Story", "To Do", "S", 1.0, f"PROJ-{90 + n}") for n in range(1, 4)]
        self.epics = [sample_issue("PROJ-91", "Epic", "To Do", "Epic")]
        result = self.find("roles")
        self.assertEqual(self.sent[-1], "key in (PROJ-91, PROJ-92, PROJ-93)")
        self.assertEqual([hit.epic_summary for hit in result.hits], ["Epic PROJ-91", "", ""])
        self.gateway.errors["search"] = RejectedError("refused")
        with self.assertRaises(RejectedError):
            self.find("roles")

    def test_a_failing_name_request_does_not_fail_the_search(self):
        self.answers["all"] = [sample_issue("PROJ-33", "Story", "To Do", "Roles", 3.0, "PROJ-5")]
        calls = []
        real = self.gateway.search_handler

        def handler(jql):
            calls.append(jql)
            if jql.startswith("key in"):
                raise RejectedError("Jira refused it")
            return real(jql)

        self.gateway.search_handler = handler
        result = self.find("roles")
        self.assertEqual(len(result.hits), 1)
        self.assertTrue(any("names of the epics could not be read: Jira refused it" in note for note in result.notes))

    def test_an_epic_key_that_is_no_key_never_reaches_a_query(self):
        self.answers["all"] = [sample_issue("PROJ-33", "Story", "To Do", "Roles", 3.0, "x OR 1=1")]
        self.find("roles")
        self.assertTrue(all(not sent.startswith("key in") for sent in self.sent))

    def test_people_are_not_in_a_hit(self):
        self.answers["all"] = [sample_issue("PROJ-1", "Story", "To Do", "Mail jane.doe@example.com", 1.0, "")]
        text = json.dumps(dataclasses.asdict(self.find("mail", snippets=1).hits[0]), default=str)
        for personal in ("jane.doe", "Jane", "jdoe"):
            self.assertNotIn(personal, text)
        self.assertIn("[email]", text)

    def test_the_names_that_people_give_are_redacted_in_a_hit_too(self):
        self.answers["all"] = [
            sample_issue("PROJ-1", "Story", "To Do", "Roles", 1.0, "", labels=["owner-a.person@example.test"], sprints=[Sprint("Sprint of jane.doe@example.test", "ACTIVE", 1)])
        ]
        hit = self.find("roles").hits[0]
        self.assertEqual(([sprint.name for sprint in hit.issue.sprints], hit.issue.labels), (["Sprint of [email]"], ["[email]"]))


class SnippetTest(Fixture):
    def setUp(self):
        super().setUp()
        long_text = (
            "h2. Background\n\n" + "Filler sentence number one is here to push things apart. " * 12
            + "\n\nh2. Steps\n\nOpen the *Dashboard* self-service portal and choose the template for a new report request.\n\n"
            + "More filler follows here and there. " * 12
        )
        self.three = [
            sample_issue("PROJ-1", "Story", "To Do", "Export a report", 3.0, "", description=long_text, acceptance="1. *GIVEN* a user, *WHEN* x, *THEN* y."),
            sample_issue("PROJ-2", "Story", "To Do", "Other", 3.0, "", description="Nothing of the words here, just text about something else entirely."),
            sample_issue("PROJ-3", "Story", "To Do", "Third", 3.0, "", description="Mail jane.doe@example.com or ask [~jdoe] about the portal."),
        ]
        self.answers = {"all": self.three, "any": self.three}  # the same issues whichever query is asked

    def test_the_text_comes_with_the_search_and_costs_no_request(self):
        self.find("portal", snippets=3)
        self.assertEqual(len(self.sent), 1)
        asked = self.gateway.called("search")[0][2]
        self.assertIn("description", asked)
        self.assertIn("acceptance", asked)

    def test_without_snippets_the_text_is_not_asked_for(self):
        self.find("Dashboard portal")
        self.assertNotIn("description", self.gateway.called("search")[0][2])

    def test_the_passage_is_around_the_words_and_says_which_were_found(self):
        hits = self.find("Dashboard portal", snippets=3).hits
        self.assertIn("Dashboard self-service portal", hits[0].snippet)
        self.assertEqual(hits[0].matched, ("Dashboard", "portal"))
        self.assertNotIn("*", hits[0].snippet)  # the wiki marks are gone
        self.assertNotIn("h2.", hits[0].snippet)

    def test_an_issue_without_the_words_shows_its_beginning(self):
        hit = self.find("Dashboard portal", snippets=3).hits[1]
        self.assertTrue(hit.snippet.startswith("Nothing of the words"))
        self.assertEqual(hit.matched, ())

    def test_a_word_that_only_the_summary_holds_counts_as_found(self):
        hit = self.find("Other", snippets=3).hits[1]
        self.assertEqual(hit.matched, ("Other",))

    def test_the_passage_is_redacted_like_every_other_text(self):
        hit = self.find("portal", snippets=3).hits[2]
        self.assertIn("[email]", hit.snippet)
        self.assertIn("[~user]", hit.snippet)
        self.assertNotIn("jane.doe", hit.snippet)
        self.assertNotIn("jdoe", hit.snippet)

    def test_only_the_first_hits_get_a_passage(self):
        hits = self.find("portal", snippets=1).hits
        self.assertEqual([bool(hit.snippet) for hit in hits], [True, False, False])

    def test_the_words_of_an_own_condition_are_the_words_of_the_passage(self):
        hit = self.find("", extra_jql='text ~ "portal"', snippets=1).hits[0]
        self.assertIn("portal", hit.snippet)

    def test_the_acceptance_criteria_count(self):
        self.answers["all"] = [sample_issue("PROJ-1", "Story", "To Do", "S", 1.0, "", description="Plain.", acceptance="1. *GIVEN* an auditor, *WHEN* she exports, *THEN* it works.")]
        hit = self.find("auditor", snippets=1).hits[0]
        self.assertIn("auditor", hit.snippet)
        self.assertEqual(hit.matched, ("auditor",))


class DiagnosticsTest(unittest.TestCase):
    def test_nothing_found_explains_what_to_try(self):
        gateway = FakeGateway()
        gateway.search_handler = lambda jql: []
        result = build_app(gateway=gateway, diagnostics=RecordingDiagnostics()).find_issues.execute(text="alpha beta")
        note = result.notes[-1]
        for hint in ("another language", "fewer or shorter words", "wildcard", "--match any", "how-to find-information"):
            self.assertIn(hint, note)


if __name__ == "__main__":
    unittest.main()
