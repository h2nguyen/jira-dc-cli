"""`jira search` on the command line: the text view, the JSON, the notes on stderr and the JQL hint of `jira issues`."""
import dataclasses
import json
import unittest

from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway, sample_issue


def gateway_with(*issue_lists):
    gateway = FakeGateway()
    gateway.page_cap = 100
    answers = list(issue_lists)
    gateway.search_handler = lambda jql: answers[1] if " OR " in jql and len(answers) > 1 else answers[0]
    return gateway


def some(count, **overrides):
    return [sample_issue(f"PROJ-{n}", "Story", "To Do", f"Story {n}", 3.0, "PROJ-9", **overrides) for n in range(1, count + 1)]


class SearchCommandTest(unittest.TestCase):
    def test_json_has_the_documented_keys(self):
        gateway = gateway_with(some(6), some(6))
        gateway.issues["PROJ-9"] = dataclasses.replace(gateway.issues["PROJ-9"], summary="Epic nine")
        result = run_cli("search", "story roles", "--snippets", "2", "--json", gateway=gateway)
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(
            sorted(data), ["count", "hits", "ignored", "jql", "match", "notes", "relaxed", "scope", "startAt", "terms", "total", "truncated"]
        )
        self.assertEqual((data["scope"], data["match"], data["relaxed"], data["terms"], data["count"]), ("project PROJ", "all", False, ["story", "roles"], 6))
        self.assertEqual(
            sorted(data["hits"][0]),
            ["allWords", "epicKey", "epicSummary", "issueType", "key", "labels", "matched", "parentKey", "priority", "snippet", "sprints", "status", "storyPoints", "summary", "updated", "url"],
        )
        first = data["hits"][0]
        self.assertEqual((first["key"], first["status"], first["epicKey"], first["allWords"]), ("PROJ-1", {"name": "To Do", "category": "new"}, "PROJ-9", True))
        self.assertEqual(first["url"], "https://jira.example.com/browse/PROJ-1")
        self.assertTrue(first["snippet"])
        self.assertIsNone(data["hits"][2]["snippet"])  # beyond the first two

    def test_a_filter_alone_has_no_words_and_no_all_words(self):
        data = run_cli("search", "--status", "Done", "--json", gateway=gateway_with(some(2))).json()
        self.assertEqual((data["terms"], data["ignored"]), ([], []))
        self.assertTrue(all(hit["allWords"] is None for hit in data["hits"]))

    def test_the_table_view(self):
        result = run_cli("search", "story", gateway=gateway_with(some(2)))
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertEqual(lines[0].split(), ["KEY", "TYPE", "STATUS", "EPIC", "SUMMARY"])
        self.assertEqual(lines[1].split()[:5], ["PROJ-1", "Story", "To", "Do", "PROJ-9"])

    def test_the_list_view_with_passages(self):
        gateway = gateway_with(some(2, description="The portal is described here."))
        result = run_cli("search", "portal", "--snippets", "1", gateway=gateway)
        self.assertIn("PROJ-1  Story | To Do | High  Story 1", result.out)
        self.assertIn("epic PROJ-9 | labels backend | 3 points | sprint Demo Sprint 3", result.out)
        self.assertIn("    The portal is described here.", result.out)

    def test_the_notes_go_to_standard_error_in_every_mode(self):
        text = run_cli("search", "how do I export reports?", gateway=gateway_with(some(6)))
        self.assertIn("[jira] Left out as filler: how, do, I", text.err)
        self.assertNotIn("Left out", text.out)
        data = run_cli("search", "how do I export reports?", "--json", gateway=gateway_with(some(6)))
        self.assertIn("[jira] Left out as filler: how, do, I", data.err)
        self.assertEqual(data.json()["notes"], ["Left out as filler: how, do, I. Put a word in quotes to keep it."])

    def test_nothing_found_prints_nothing_on_standard_output_and_explains_on_standard_error(self):
        result = run_cli("search", "alpha", gateway=gateway_with([]))
        self.assertEqual((result.code, result.out), (0, ""))
        self.assertIn("Nothing found in project PROJ", result.err)
        data = run_cli("search", "alpha", "--json", gateway=gateway_with([])).json()
        self.assertEqual((data["total"], data["hits"]), (0, []))
        self.assertTrue(data["notes"])

    def test_the_count_of_the_rest_is_announced(self):
        gateway = gateway_with(some(12))
        result = run_cli("search", "story", "--limit", "5", gateway=gateway)
        self.assertIn("Showing 5 of 12, the best match first", result.err)
        self.assertNotIn("Showing", run_cli("search", "story", "--all", gateway=gateway).err)
        self.assertEqual((run_cli("search", "story", "--json", gateway=gateway).json()["count"], run_cli("search", "story", "--all", "--json", gateway=gateway).json()["count"]), (10, 12))

    def test_every_option_reaches_the_query(self):
        gateway = gateway_with(some(12))
        result = run_cli(
            "search", "roles", "--project", "demo", "--type", "Story,Bug", "--status", "To Do", "--label", "backend,api", "--under", "DEMO-5",
            "--sprint", "Sprint 3", "--fix-version", "Release 1.0", "--since", "7d", "--jql", "priority = High", "--match", "any", "--start-at", "2", "--limit", "3", "--json",
            gateway=gateway,
        )
        self.assertEqual(result.code, 0, result.err)
        call = gateway.called("search")[0]
        self.assertEqual(
            call[1],
            'project = "DEMO" AND text ~ "roles" AND issuetype in ("Story", "Bug") AND status = "To Do" AND labels in ("backend", "api") '
            'AND (cf[10002] = DEMO-5 OR parent = DEMO-5) AND sprint = "Sprint 3" AND fixVersion = "Release 1.0" AND updated >= -7d AND (priority = High)',
        )
        self.assertEqual((call[3], call[4]), (2, 3))  # where it starts and how many were asked for

    def test_match_reaches_the_search(self):
        gateway = gateway_with(some(2), some(8))
        data = run_cli("search", "alpha beta", "--match", "any", "--json", gateway=gateway).json()
        self.assertEqual((data["match"], data["relaxed"], len(gateway.called("search")) - sum(1 for call in gateway.called("search") if call[1].startswith("key in"))), ("any", False, 1))
        self.assertIn(" OR ", data["jql"])

    def test_wrong_options_are_usage_errors(self):
        for argv in (("search",), ("search", "x", "--limit", "0"), ("search", "x", "--snippets", "26"), ("search", "x", "--match", "some"), ("search", "x", "--since", "yesterday"), ("search", "x", "--under", "5")):
            with self.subTest(argv=argv):
                result = run_cli(*argv, gateway=gateway_with(some(1)))
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.out, "")

    def test_the_help_says_when_to_use_it(self):
        result = run_cli("help", "search")
        self.assertEqual(result.code, 0)
        self.assertIn("When to use: Use first when you do not know which issue holds the information", result.out)
        self.assertIn("Examples:", result.out)

    def test_several_words_need_no_quotes(self):
        gateway = gateway_with(some(2))
        data = run_cli("search", "audit", "trail", "--json", gateway=gateway).json()
        self.assertEqual(data["terms"], ["audit", "trail"])
        self.assertEqual(gateway.called("search")[0][1], 'project = "PROJ" AND text ~ "audit trail"')
        self.assertEqual(run_cli("search", "--json", "audit trail", gateway=gateway_with(some(2))).json()["terms"], ["audit", "trail"])
        self.assertEqual(run_cli("search", "--status", "Done", "--json", gateway=gateway_with(some(2))).json()["terms"], [])
        self.assertIn("Usage: jira search [TEXT ...] [--project KEY]", run_cli("help", "search").out)


class IssuesHintTest(unittest.TestCase):
    def test_a_jql_without_a_project_says_that_it_searches_every_project(self):
        result = run_cli("issues", "--jql", "status = Done", "--json", gateway=gateway_with(some(1)))
        self.assertIn("searches every project", result.err)
        self.assertIn("jira search", result.err)
        self.assertEqual(result.code, 0)

    def test_a_jql_with_a_project_does_not(self):
        for jql in ('project = "PROJ" AND status = Done', "PROJECT=PROJ"):
            with self.subTest(jql=jql):
                self.assertNotIn("every project", run_cli("issues", "--jql", jql, "--json", gateway=gateway_with(some(1))).err)

    def test_the_output_of_issues_is_unchanged(self):
        data = json.loads(run_cli("issues", "--jql", "status = Done", "--json", gateway=gateway_with(some(1))).out)
        self.assertEqual(sorted(data), ["count", "issues", "jql", "total"])


if __name__ == "__main__":
    unittest.main()
