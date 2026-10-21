"""`jira search` against the mock Jira with the real adapters: what really goes over the wire, and what comes back."""
import json
import unittest

from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_jira import ACCEPT, EPIC, POINTS, text_refused


class SearchFlowTest(EndToEnd):
    def setUp(self):
        super().setUp()
        self.jira.page_cap = 100
        fields = self.jira.issues
        fields["PROJ-1"]["fields"].update(summary="Export the monthly report", description="h2. Steps\n\nOpen the *Dashboard* portal and export the report.\n\nAsk [~jdoe] or jane.doe@example.com", **{ACCEPT: "1. *GIVEN* a user, *WHEN* she asks, *THEN* she gets a report."})
        fields["PROJ-2"]["fields"].update(summary="Audit trail", description="An append only audit trail of every change.")
        fields["PROJ-3"]["fields"].update(summary="Reports of charts", description="Charts come from monthly reports.")

    def keys(self, result):
        return [hit["key"] for hit in result.json()["hits"]]

    def test_a_question_finds_the_issue_and_the_wire_carries_one_search(self):
        result = self.cli("search", "how do I export the monthly report?", "--json", "--snippets", "2")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(data["hits"][0]["key"], "PROJ-1")
        self.assertEqual(data["terms"], ["export", "monthly", "report"])
        searches = self.jira.sent("GET", "/rest/api/2/search")
        self.assertEqual(searches[0].query["jql"], ['project = "PROJ" AND text ~ "export monthly report"'])
        asked = searches[0].query["fields"][0].split(",")
        for wanted in ("summary", "status", "labels", "description", EPIC, POINTS, ACCEPT):
            self.assertIn(wanted, asked)
        for person in ("assignee", "reporter", "comment"):
            self.assertNotIn(person, asked)

    def test_the_passage_is_cut_from_redacted_text_and_marks_are_gone(self):
        data = self.cli("search", "Dashboard portal", "--json", "--snippets", "1").json()
        snippet = data["hits"][0]["snippet"]
        self.assertIn("Dashboard portal", snippet)
        self.assertNotIn("*", snippet)
        self.assertNotIn("jane.doe", self.cli("search", "Dashboard", "--json", "--snippets", "1").out)
        full = self.cli("search", "Ask", "--json", "--snippets", "1").out
        self.assertNotIn("jdoe", full)
        self.assertNotIn("Jane", full)

    def test_loosening_puts_the_best_match_first_and_marks_the_issues_with_every_word(self):
        data = self.cli("search", "audit trail export", "--json").json()
        self.assertTrue(data["relaxed"])
        self.assertEqual(data["match"], "any")
        self.assertEqual([hit["key"] for hit in data["hits"]], ["PROJ-2", "PROJ-1"])  # the issue with most of the words first
        self.assertTrue(all(hit["allWords"] is False for hit in data["hits"]))
        jql = [item.query["jql"][0] for item in self.jira.sent("GET", "/rest/api/2/search")]
        self.assertEqual(len([item for item in jql if not item.startswith("key in")]), 2)
        self.assertIn(" OR ", jql[1])

    def test_an_issue_that_holds_every_word_is_marked(self):
        data = self.cli("search", "audit trail change", "--json").json()
        self.assertEqual([(hit["key"], hit["allWords"]) for hit in data["hits"]][:1], [("PROJ-2", True)])

    def test_what_jira_refuses_is_made_safe_before_it_is_sent(self):
        for text in ("[x] ~ ! ( audit x) {y} ^", "~audit", "audit ^", "a ( b"):
            with self.subTest(text=text):
                result = self.cli("search", text, "--json")
                self.assertEqual(result.code, 0, result.err)
        for sent in self.jira.sent("GET", "/rest/api/2/search"):
            for clause in __import__("re").findall(r'text ~ ("(?:\\.|[^"\\])*")', sent.query["jql"][0]):
                self.assertEqual(text_refused(clause.strip('"')), "")

    def test_the_mock_refuses_what_jira_refuses_so_the_test_above_means_something(self):
        raw = self.cli("issues", "--jql", 'project = "PROJ" AND text ~ "[x]"', "--json")
        self.assertEqual(raw.code, 6)
        self.assertIn("Unable to parse", raw.error()["message"] + json.dumps(raw.error()))

    def test_filters_and_the_epic_children(self):
        self.jira.issues["PROJ-1"]["fields"][EPIC] = "PROJ-9"
        children = self.cli("search", "--under", "PROJ-9", "--json").json()
        self.assertEqual(sorted(hit["key"] for hit in children["hits"]), ["PROJ-1", "PROJ-2"])
        self.assertIn(f"cf[{EPIC.removeprefix('customfield_')}] = PROJ-9", children["jql"])
        done = self.cli("search", "--status", "Done", "--json").json()
        self.assertEqual([hit["key"] for hit in done["hits"]], ["PROJ-3"])
        self.assertEqual(self.cli("search", "audit", "--type", "Bug", "--json").json()["total"], 1)
        self.assertEqual(self.cli("search", "--sprint", "active", "--status", "To Do", "--json").json()["total"], 3)

    def test_the_epic_names_come_with_one_more_request(self):
        data = self.cli("search", "audit reports", "--json", "--match", "any").json()
        epics = {hit["epicKey"]: hit["epicSummary"] for hit in data["hits"] if hit["epicKey"]}
        self.assertEqual(epics["PROJ-9"], "Epic nine")
        self.assertEqual(len([s for s in self.jira.sent("GET", "/rest/api/2/search") if s.query["jql"][0].startswith("key in")]), 1)

    def test_a_server_that_answers_two_at_a_time_still_gives_the_page(self):
        self.jira.page_cap = 2
        data = self.cli("search", "--status", "To Do", "--limit", "3", "--json").json()
        self.assertEqual(data["count"], 3)
        pages = [s for s in self.jira.sent("GET", "/rest/api/2/search") if not s.query["jql"][0].startswith("key in")]
        self.assertEqual([page.query["startAt"] for page in pages], [["0"], ["2"]])  # the second request asked for what was missing

    def test_since_and_a_condition_of_your_own(self):
        self.assertEqual(self.cli("search", "--since", "7d", "--json").json()["total"], 6)
        self.assertEqual(self.cli("search", "--since", "2026-10-11", "--json").json()["total"], 0)
        self.assertEqual(self.cli("search", "--jql", "priority = High", "--label", "backend", "--json").json()["total"], 6)

    def test_all_projects_widens_the_search(self):
        self.jira.issues["DEMO-1"] = json.loads(json.dumps(self.jira.issues["PROJ-1"]))
        self.jira.issues["DEMO-1"]["key"] = "DEMO-1"
        self.assertNotIn("DEMO-1", self.keys(self.cli("search", "Export", "--json")))
        self.assertIn("DEMO-1", self.keys(self.cli("search", "Export", "--all-projects", "--json")))

    def test_issues_warns_that_a_jql_without_a_project_searches_every_project(self):
        self.assertIn("every project", self.cli("issues", "--jql", "status = Done").err)


if __name__ == "__main__":
    unittest.main()
