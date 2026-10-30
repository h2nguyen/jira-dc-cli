"""The reading calls of the real gateway against the mock Jira: what is sent, and how the answers are turned into domain objects."""
import json
import unittest

from jira_dc_cli.domain.errors import AuthError, NotFoundError, RejectedError
from tests.integration import test_http_and_gateway as base
from tests.support.mock_jira import ACCEPT
from tests.support.mock_reading import add_filter, add_history, reading_routes


class ReadingGatewayTest(base.GatewayTestCase):
    def setUp(self):
        super().setUp()
        self.jira.page_cap = 100

    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])

    def searches(self):
        return self.gets("/rest/api/2/search")


class IssueWithTest(ReadingGatewayTest):
    def test_without_an_expansion_it_is_the_ordinary_request(self):
        expanded = self.gateway.get_issue_with("PROJ-1")
        (request,) = self.gets("/rest/api/2/issue/PROJ-1")
        self.assertEqual(request.query, {})
        self.assertEqual((expanded.issue.key, expanded.issue.story_points, expanded.rendered_description, expanded.rendered_acceptance, expanded.changelog), ("PROJ-1", 5.0, "", "", None))

    def test_the_expansions_are_asked_for_in_one_parameter(self):
        self.jira.issues["PROJ-1"]["fields"].update(description="* a\n* b", **{ACCEPT: "# x"})
        add_history(self.jira, "PROJ-1", "2026-10-01T08:00:00.000+0200", ("status", "To Do", "In Progress"))
        expanded = self.gateway.get_issue_with("PROJ-1", ("renderedFields", "changelog"))
        (request,) = self.gets("/rest/api/2/issue/PROJ-1")
        self.assertEqual(request.query, {"expand": ["renderedFields,changelog"]})
        self.assertEqual((expanded.rendered_description, expanded.rendered_acceptance), ("<ul><li>a</li><li>b</li></ul>", "<ol><li>x</li></ol>"))
        self.assertEqual(expanded.issue.description, "* a\n* b")
        self.assertEqual(len(expanded.changelog.entries), 1)
        self.assertEqual((expanded.changelog.entries[0].created, expanded.changelog.entries[0].changes[0].to_text), ("2026-10-01T08:00:00.000+0200", "In Progress"))
        self.assertNotIn("jdoe", repr(expanded.changelog))  # the author is in Jira's answer, not in ours

    def test_the_total_of_a_changelog_that_jira_cut_is_kept(self):
        for day in (1, 2, 3):
            add_history(self.jira, "PROJ-1", f"2026-10-0{day}T08:00:00.000+0200", ("status", "A", "B"))
        reading_routes(self.jira).changelog_limit = 2
        changelog = self.gateway.get_issue_with("PROJ-1", ("changelog",)).changelog
        self.assertEqual((len(changelog.entries), changelog.total, changelog.complete), (2, 3, False))

    def test_a_custom_field_that_is_called_status_does_not_make_history(self):
        add_history(self.jira, "PROJ-1", "2026-10-01T08:00:00.000+0200", ("status", "Jane Doe", "John Roe", "custom"), ("status", "To Do", "Done"))
        add_history(self.jira, "PROJ-1", "2026-10-02T08:00:00.000+0200", ("Status", "Jane Doe", "John Roe", "custom"))
        changelog = self.gateway.get_issue_with("PROJ-1", ("changelog",)).changelog
        self.assertEqual([[(change.from_text, change.to_text) for change in entry.changes] for entry in changelog.entries], [[("To Do", "Done")], []])
        self.assertEqual((changelog.total, changelog.complete), (2, True))
        self.assertNotIn("Jane", repr(changelog))

    def test_an_issue_that_does_not_exist_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.gateway.get_issue_with("PROJ-999", ("changelog",))


class IssuesByKeyTest(ReadingGatewayTest):
    def test_one_search_for_the_keys_with_every_field(self):
        found = self.gateway.get_issues_by_key(["PROJ-3", "PROJ-1", "PROJ-2"])
        (request,) = self.searches()
        self.assertEqual(request.query, {"jql": ["key in (PROJ-3, PROJ-1, PROJ-2)"], "startAt": ["0"], "maxResults": ["3"], "fields": ["*all"]})
        self.assertEqual([item.issue.key for item in found], ["PROJ-1", "PROJ-2", "PROJ-3"])  # Jira's order: the caller puts them in the order of its keys
        self.assertEqual(found[0].issue.comment_total, 1)
        self.assertEqual(found[0].issue.epic_key, "PROJ-9")

    def test_the_expansions_are_one_parameter_of_the_search(self):
        add_history(self.jira, "PROJ-2", "2026-10-01T08:00:00.000+0200", ("status", "To Do", "In Progress"))
        found = self.gateway.get_issues_by_key(["PROJ-1", "PROJ-2"], ("renderedFields", "changelog"))
        (request,) = self.searches()
        self.assertEqual(request.query["expand"], ["renderedFields,changelog"])
        self.assertTrue(all(item.rendered_description.startswith("<p>") for item in found))
        self.assertEqual([len(item.changelog.entries) for item in found], [0, 1])

    def test_a_server_that_answers_with_fewer_than_asked_is_asked_again_for_the_rest(self):
        self.jira.page_cap = 2
        found = self.gateway.get_issues_by_key(["PROJ-1", "PROJ-2", "PROJ-3", "PROJ-4", "PROJ-8"])
        self.assertEqual([item.issue.key for item in found], ["PROJ-1", "PROJ-2", "PROJ-3", "PROJ-4", "PROJ-8"])
        self.assertEqual([request.query["startAt"] for request in self.searches()], [["0"], ["2"], ["4"]])

    def test_a_key_that_does_not_exist_makes_jira_refuse_the_whole_query(self):
        with self.assertRaises(RejectedError) as caught:
            self.gateway.get_issues_by_key(["PROJ-1", "PROJ-999"])
        self.assertEqual(caught.exception.details["status"], 400)
        self.assertIn("An issue with key 'PROJ-999' does not exist for field 'key'", caught.exception.message)

    def test_no_keys_no_request(self):
        self.assertEqual(self.gateway.get_issues_by_key([]), [])
        self.assertEqual(self.jira.requests, [])

    def test_more_than_a_hundred_keys_are_asked_for_in_batches(self):
        for number in range(1000, 1130):
            self.jira.issues[f"PROJ-{number}"] = json.loads(json.dumps(self.jira.issues["PROJ-1"]))
            self.jira.issues[f"PROJ-{number}"]["key"] = f"PROJ-{number}"
        keys = [f"PROJ-{number}" for number in range(1000, 1130)]
        found = self.gateway.get_issues_by_key(keys)
        self.assertEqual(len(found), 130)
        self.assertEqual([request.query["maxResults"] for request in self.searches()], [["100"], ["30"]])
        self.assertTrue(all(len(request.query["jql"][0]) < 1500 for request in self.searches()))

    def test_a_refusal_of_the_token_is_an_auth_error(self):
        self.jira.fail_next("/rest/api/2/search", 403, body={"errorMessages": ["No permission."]})
        with self.assertRaises(AuthError):
            self.gateway.get_issues_by_key(["PROJ-1"])

    def test_a_read_that_hiccups_is_retried(self):
        self.jira.fail_next("/rest/api/2/search", 503)
        self.assertEqual(len(self.gateway.get_issues_by_key(["PROJ-1"])), 1)
        self.assertEqual(len(self.searches()), 2)


class FilterGatewayTest(ReadingGatewayTest):
    def setUp(self):
        super().setUp()
        add_filter(self.jira, "10000", "Open bugs", 'project = "PROJ" AND issuetype = Bug', description="d")
        add_filter(self.jira, "10001", "Not a favourite", "project = DEMO", favourite=False)

    def test_the_favourites(self):
        found = self.gateway.list_filters()
        self.assertEqual([(item.id, item.name, item.description, item.jql, item.favourite) for item in found], [("10000", "Open bugs", "d", 'project = "PROJ" AND issuetype = Bug', True)])
        self.assertEqual(found[0].owner.name, "jdoe")
        self.assertEqual(found[0].view_url, f"{self.jira.base_url}/secure/IssueNavigator.jspa?mode=hide&requestId=10000")
        self.assertTrue(found[0].search_url.startswith(f"{self.jira.base_url}/rest/api/2/search?jql=project"))

    def test_the_gateway_hands_over_the_jql_as_jira_sends_it_and_the_use_case_leaves_the_people_out(self):
        add_filter(self.jira, "10003", "Theirs", 'assignee = jdoe AND reporter in ("asmith","jane.doe")')
        item = self.gateway.get_filter("10003")
        self.assertEqual((item.jql, item.people_hidden), ('assignee = jdoe AND reporter in ("asmith","jane.doe")', False))
        self.assertIn("jdoe", item.search_url)

    def test_one_filter_even_when_it_is_not_a_favourite(self):
        item = self.gateway.get_filter("10001")
        self.assertEqual((item.id, item.jql, item.favourite), ("10001", "project = DEMO", False))

    def test_a_filter_that_does_not_exist(self):
        with self.assertRaises(NotFoundError) as caught:
            self.gateway.get_filter("99999")
        self.assertIn("The filter with id '99999' does not exist", caught.exception.message)

    def test_no_filters_at_all(self):
        reading_routes(self.jira).filters.clear()
        self.assertEqual(self.gateway.list_filters(), [])

    def test_a_refusal_is_an_auth_error(self):
        self.jira.fail_next("/rest/api/2/filter/favourite", 401, body={"errorMessages": ["You are not authenticated."]})
        with self.assertRaises(AuthError):
            self.gateway.list_filters()


class SearchWithAFilterTest(ReadingGatewayTest):
    def setUp(self):
        super().setUp()
        add_filter(self.jira, "10000", "Stories", "issuetype = Story ORDER BY key DESC")

    def search(self, jql):
        return self.gateway.search(jql, fields=["summary"], max_results=50)

    def test_the_filter_clause_is_resolved_by_the_mock_like_jira_does(self):
        self.assertEqual([item.key for item in self.search("filter = 10000").issues], ["PROJ-4", "PROJ-3", "PROJ-1"])
        self.assertEqual([item.key for item in self.search('filter = 10000 AND status = "Done"').issues], ["PROJ-3"])
        self.assertEqual([item.key for item in self.search('project = "PROJ" AND filter = 10000 AND status = "To Do" ORDER BY key ASC').issues], ["PROJ-1", "PROJ-4"])

    def test_an_unknown_filter_is_refused_with_jiras_words(self):
        with self.assertRaises(RejectedError) as caught:
            self.search("filter = 99999")
        self.assertIn("The value '99999' does not exist for the field 'filter'", caught.exception.message)

    def test_a_search_for_known_keys_with_no_expansion_is_left_to_the_built_in_routes(self):
        self.assertEqual([item.key for item in self.search("key in (PROJ-1, PROJ-2)").issues], ["PROJ-1", "PROJ-2"])


class MockBehaviourTest(ReadingGatewayTest):
    """The mock must behave like Jira for the tests above to mean something."""

    def test_the_search_that_asks_for_every_field_returns_them(self):
        found = self.gateway.get_issues_by_key(["PROJ-1"])
        self.assertEqual(found[0].issue.sprints[0].name, "Demo Sprint 3")
        self.assertEqual(found[0].issue.acceptance, "1. GIVEN a, WHEN b, THEN c.")

    def test_the_comments_that_a_write_added_are_in_the_search_too(self):
        self.jira.comments["PROJ-1"] = [{"id": "11", "author": {"name": "jdoe", "displayName": "Jane Doe"}, "created": "2026-10-15T10:00:00.000+0200", "body": "Added."}]
        issue = self.gateway.get_issues_by_key(["PROJ-1"])[0].issue
        self.assertEqual((issue.comment_total, [comment.body for comment in issue.comments]), (2, ["Looks fine, [~jdoe].", "Added."]))

    def test_a_changelog_has_the_shape_of_jira(self):
        add_history(self.jira, "PROJ-1", "2026-10-01T08:00:00.000+0200", ("status", "To Do", "Done"))
        data = self.gateway.request("GET", "/issue/PROJ-1?expand=changelog")
        changelog = data["changelog"]
        self.assertEqual((changelog["startAt"], changelog["maxResults"], changelog["total"]), (0, 1, 1))
        entry = changelog["histories"][0]
        self.assertEqual(sorted(entry), ["author", "created", "id", "items"])
        self.assertEqual(sorted(entry["items"][0]), ["field", "fieldtype", "from", "fromString", "to", "toString"])
        self.assertEqual(self.gateway.request("GET", "/issue/PROJ-1?expand=names")["key"], "PROJ-1")  # an expansion that the mock does not know is ignored

    def test_the_default_answers_are_unchanged(self):
        data = self.gateway.request("GET", "/issue/PROJ-1")
        self.assertEqual(sorted(data), ["fields", "id", "key"])
        self.assertEqual(self.gateway.request("GET", "/search?jql=project%20%3D%20PROJ&fields=summary")["total"], 6)


if __name__ == "__main__":
    unittest.main()
