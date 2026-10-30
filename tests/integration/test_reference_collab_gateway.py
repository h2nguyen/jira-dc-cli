"""The real gateway for the reference and collab areas against the mock Jira: what goes over the wire, and answers that are odd."""
import tempfile
import unittest

from jira_dc_cli.adapters.driven.file_cache import FileCache
from jira_dc_cli.adapters.driven.http_transport import HttpTransport
from jira_dc_cli.adapters.driven.jira_rest import RestJiraGateway
from jira_dc_cli.application.settings import Settings
from jira_dc_cli.domain.errors import AuthError, NotFoundError, RejectedError
from jira_dc_cli.domain.worktime import worklog_request
from tests.support.mock_jira import MockJira
from tests.support.mock_collab import collab
from tests.support.mock_reference import reference


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self.jira = MockJira()
        self.jira.__enter__()
        self.addCleanup(self.jira.__exit__, None, None, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = Settings(base_url=self.jira.base_url, pat=self.jira.token, retry=0)
        transport = HttpTransport(settings, sleep=lambda seconds: None)
        self.addCleanup(transport.close)
        self.gateway = RestJiraGateway(settings, transport, FileCache(folder.name))
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))

    def answer(self, prefix, body, *, method="GET", status=200):
        """Make Jira answer the next call to `prefix` with an odd body, as a version or a plug-in may."""
        self.jira.fail_next(prefix, status, body=body, method=method)

    def last(self):
        return self.jira.requests[-1]


class ReferenceWireTest(GatewayCase):
    def test_the_lists_are_read_with_the_documented_calls(self):
        self.assertEqual(len(self.gateway.list_priorities()), 4)
        self.assertEqual(len(self.gateway.list_resolutions()), 3)
        self.assertEqual(len(self.gateway.list_all_issue_types()), 4)
        self.assertEqual(len(self.gateway.list_all_statuses()), 4)
        self.assertEqual([item.path for item in self.jira.requests], ["/rest/api/2/priority", "/rest/api/2/resolution", "/rest/api/2/issuetype", "/rest/api/2/status"])

    def test_what_belongs_to_a_project_is_asked_below_the_project(self):
        self.gateway.list_project_statuses("PROJ")
        self.gateway.list_project_components("PROJ")
        self.gateway.list_project_versions("PROJ")
        self.gateway.list_project_roles("PROJ")
        self.gateway.get_project_role("PROJ", "10001")
        self.gateway.list_project_issue_types("PROJ")
        self.assertEqual([item.path for item in self.jira.requests],
                         ["/rest/api/2/project/PROJ/statuses", "/rest/api/2/project/PROJ/components", "/rest/api/2/project/PROJ/versions", "/rest/api/2/project/PROJ/role",
                          "/rest/api/2/project/PROJ/role/10001", "/rest/api/2/project/PROJ"])

    def test_a_version_has_its_id_its_state_and_two_counts(self):
        versions = self.gateway.list_project_versions("PROJ")
        self.assertEqual([(item.name, item.released, item.archived, item.release_date) for item in versions], [("Release 1.0", True, False, "2026-06-01"), ("Release 2.0", False, False, "2026-12-01")])
        counts = self.gateway.get_version_counts(versions[1].id)
        self.assertEqual((counts.fixed, counts.affected, counts.unresolved), (5, 1, 4))
        self.assertEqual([item.path.rsplit("/", 1)[1] for item in self.jira.requests[1:]], ["relatedIssueCounts", "unresolvedIssueCount"])

    def test_the_names_that_the_older_calls_read_are_the_same_names(self):
        self.assertEqual(self.gateway.list_versions("PROJ"), [item.name for item in self.gateway.list_project_versions("PROJ")])

    def test_permissions_are_asked_for_one_scope_at_most(self):
        self.gateway.get_my_permissions()
        self.gateway.get_my_permissions(project_key="PROJ")
        self.gateway.get_my_permissions(issue_key="PROJ-1")
        self.gateway.get_my_permissions(project_key="PROJ", issue_key="PROJ-1")
        self.assertEqual([item.query for item in self.jira.requests], [{}, {"projectKey": ["PROJ"]}, {"issueKey": ["PROJ-1"]}, {"issueKey": ["PROJ-1"]}])
        reference(self.jira).denied.add("EDIT_ISSUES")
        granted = {item.key: item.have_permission for item in self.gateway.get_my_permissions(project_key="PROJ")}
        self.assertEqual((granted["EDIT_ISSUES"], granted["BROWSE_PROJECTS"], granted["ADMINISTER_PROJECTS"]), (False, True, True))

    def test_dashboards_ask_for_a_page_and_a_filter(self):
        page = self.gateway.list_dashboards(filter_name="favourite", start_at=0, max_results=20)
        self.assertEqual((page.total, [item.id for item in page.dashboards]), (1, ["10001"]))
        self.assertEqual(self.last().query, {"startAt": ["0"], "maxResults": ["20"], "filter": ["favourite"]})
        self.gateway.list_dashboards()
        self.assertEqual(self.last().query, {"startAt": ["0"], "maxResults": ["50"]})

    def test_the_settings_and_the_upload_limit(self):
        settings = self.gateway.get_server_settings()
        self.assertEqual((settings.voting_enabled, settings.time_tracking.working_hours_per_day), (True, 8.0))
        self.assertIsInstance(self.gateway.get_attachment_upload_limit(), int)

    def test_a_missing_project_or_version_is_not_found_and_a_refusal_an_auth_error(self):
        for call in (lambda: self.gateway.list_project_components("NOPE"), lambda: self.gateway.get_version_counts("424242"), lambda: self.gateway.get_my_permissions(issue_key="PROJ-999")):
            with self.assertRaises(NotFoundError):
                call()
        reference(self.jira).issue_types_for_administrators_only = True
        with self.assertRaises(AuthError):
            self.gateway.list_all_issue_types()


class ReferenceOddAnswersTest(GatewayCase):
    def test_an_answer_that_is_no_list_is_an_empty_list(self):
        for prefix, call in (("/rest/api/2/priority", self.gateway.list_priorities), ("/rest/api/2/status", self.gateway.list_all_statuses), ("/rest/api/2/issuetype", self.gateway.list_all_issue_types)):
            self.answer(prefix, {"unexpected": True})
            self.assertEqual(call(), [])
        self.answer("/rest/api/2/project/PROJ/components", None)
        self.assertEqual(self.gateway.list_project_components("PROJ"), [])

    def test_items_that_are_no_objects_are_skipped_and_missing_keys_are_empty(self):
        self.answer("/rest/api/2/priority", [{"id": 1, "name": None}, "junk", None, {"name": "Ok", "description": "d"}])
        items = self.gateway.list_priorities()
        self.assertEqual([(item.id, item.name, item.description) for item in items], [("1", "", ""), ("", "Ok", "d")])

    def test_a_status_without_a_category_and_a_project_without_issue_types(self):
        self.answer("/rest/api/2/status", [{"id": "1", "name": "Odd"}, {"id": "2", "name": "Odder", "statusCategory": "none"}])
        self.assertEqual([item.category for item in self.gateway.list_all_statuses()], ["", ""])
        self.answer("/rest/api/2/project/PROJ", {"key": "PROJ"})
        self.assertEqual(self.gateway.list_project_issue_types("PROJ"), [])

    def test_a_component_whose_lead_is_only_a_name(self):
        self.answer("/rest/api/2/project/PROJ/components", [{"id": "1", "name": "A", "leadUserName": "jdoe"}, {"id": "2", "name": "B", "lead": "not an object"}])
        first, second = self.gateway.list_project_components("PROJ")
        self.assertEqual((first.lead.name, second.lead), ("jdoe", None))

    def test_a_version_without_flags_and_counts_without_numbers(self):
        self.answer("/rest/api/2/project/PROJ/versions", [{"id": "5", "name": "V"}])
        version = self.gateway.list_project_versions("PROJ")[0]
        self.assertEqual((version.released, version.archived, version.release_date), (False, False, ""))
        self.answer("/rest/api/2/version/5/relatedIssueCounts", {"issuesFixedCount": "many", "issuesAffectedCount": -3})
        self.answer("/rest/api/2/version/5/unresolvedIssueCount", [])
        counts = self.gateway.get_version_counts("5")
        self.assertEqual((counts.fixed, counts.affected, counts.unresolved), (0, 0, 0))

    def test_permissions_that_are_no_objects_and_roles_without_an_id(self):
        self.answer("/rest/api/2/mypermissions", {"permissions": {"A": {"name": "Alpha", "havePermission": True}, "B": "junk", "C": {"key": "CC", "havePermission": "yes"}}})
        found = self.gateway.get_my_permissions()
        self.assertEqual([(item.key, item.name, item.have_permission) for item in found], [("A", "Alpha", True), ("CC", "", True)])
        self.answer("/rest/api/2/mypermissions", [])
        self.assertEqual(self.gateway.get_my_permissions(), [])
        self.answer("/rest/api/2/project/PROJ/role", {"Odd": "https://example.com/no-id-here", "Developers": "https://example.com/rest/api/2/project/10000/role/10001/", "Bad": None})
        self.assertEqual([(role.id, role.name) for role in self.gateway.list_project_roles("PROJ")], [("", "Odd"), ("10001", "Developers"), ("", "Bad")])

    def test_actors_of_either_kind_and_none_at_all(self):
        self.answer("/rest/api/2/project/PROJ/role/1", {"id": 1, "name": "R", "actors": [{"name": "g", "type": "atlassian-group-role-actor"}, {"name": "u", "displayName": "U", "type": "atlassian-user-role-actor"}, {"name": "x"}, "junk"]})
        role = self.gateway.get_project_role("PROJ", "1")
        self.assertEqual([(actor.name, actor.display_name, actor.kind) for actor in role.actors], [("g", "", "group"), ("u", "U", "user"), ("x", "", "")])
        self.answer("/rest/api/2/project/PROJ/role/2", {"id": 2, "name": "Empty"})
        self.assertEqual(self.gateway.get_project_role("PROJ", "2").actors, [])

    def test_a_dashboard_page_without_a_total_and_settings_without_anything(self):
        self.answer("/rest/api/2/dashboard", {"dashboards": [{"id": 7, "name": "D", "view": "v"}]})
        page = self.gateway.list_dashboards()
        self.assertEqual((page.total, [(item.id, item.view, item.description) for item in page.dashboards]), (1, [("7", "v", "")]))
        self.answer("/rest/api/2/configuration", {})
        settings = self.gateway.get_server_settings()
        self.assertEqual((settings.voting_enabled, settings.time_tracking, settings.attachment_upload_limit), (None, None, None))
        self.answer("/rest/api/2/attachment/meta", {"uploadLimit": "big"})
        self.assertIsNone(self.gateway.get_attachment_upload_limit())


class CollabWireTest(GatewayCase):
    def test_a_watcher_is_added_with_a_json_string_and_removed_with_a_query(self):
        collab(self.jira).users["jane.doe@example.com"] = "Jane Doe"  # a login that looks like an address, to see how it is encoded
        self.gateway.add_watcher("PROJ-2", "jane.doe@example.com")
        post = self.last()
        self.assertEqual((post.method, post.path, post.raw, post.body, post.headers["Content-Type"]), ("POST", "/rest/api/2/issue/PROJ-2/watchers", b'"jane.doe@example.com"', "jane.doe@example.com", "application/json"))
        self.gateway.remove_watcher("PROJ-2", {"username": "jane.doe@example.com"})
        delete = self.last()
        self.assertEqual((delete.method, delete.query, delete.raw), ("DELETE", {"username": ["jane.doe@example.com"]}, b""))
        self.assertEqual(collab(self.jira).watchers["PROJ-2"], [])

    def test_a_vote_has_no_body_and_no_content_type(self):
        self.jira.issues["PROJ-2"]["fields"]["reporter"] = {"name": "asmith"}  # the token's user may not vote for what that user reported
        self.gateway.add_vote("PROJ-2")
        post = self.last()
        self.assertEqual((post.method, post.path, post.raw), ("POST", "/rest/api/2/issue/PROJ-2/votes", b""))
        self.assertNotIn("Content-Type", post.headers)
        self.gateway.remove_vote("PROJ-2")
        self.assertEqual(self.last().method, "DELETE")

    def test_the_answer_to_a_vote_is_200_with_the_count_and_what_comes_back_is_ignored(self):
        """The 11.3 reference answers a vote with 200 ("Returns the vote count") and a withdrawal with 204; the code reads neither body."""
        self.jira.issues["PROJ-2"]["fields"]["reporter"] = {"name": "asmith"}
        self.assertIsNone(self.gateway.add_vote("PROJ-2"))  # the mock answers 200 and the number of votes
        self.assertEqual(self.gateway.get_votes("PROJ-2").count, 1)
        self.assertIsNone(self.gateway.remove_vote("PROJ-2"))
        for body in (3, {"votes": 3, "hasVoted": True}, [], "3"):
            with self.subTest(body=body):
                self.answer("/rest/api/2/issue/PROJ-2/votes", body, method="POST", status=200)
                self.assertIsNone(self.gateway.add_vote("PROJ-2"))
        self.assertEqual(self.gateway.get_votes("PROJ-2").count, 0)  # the injected answers never reached the mock: nothing was voted

    def test_a_vote_for_ones_own_issue_is_a_404_with_jiras_words(self):
        with self.assertRaises(NotFoundError) as caught:
            self.gateway.add_vote("PROJ-1")
        self.assertIn("You cannot vote for an issue you have reported.", caught.exception.message)

    def test_the_watchers_and_the_votes_that_jira_lists_are_read_as_counts_and_users(self):
        watchers = self.gateway.get_watchers("PROJ-1")
        self.assertEqual((watchers.count, watchers.watching, [user.name for user in watchers.users]), (2, False, ["asmith", "bwong"]))
        votes = self.gateway.get_votes("PROJ-1")
        self.assertEqual((votes.count, votes.has_voted), (1, False))
        self.assertFalse(hasattr(votes, "voters"))

    def test_odd_answers_about_watchers_and_votes(self):
        self.answer("/rest/api/2/issue/PROJ-1/watchers", {"isWatching": True})
        found = self.gateway.get_watchers("PROJ-1")
        self.assertEqual((found.count, found.watching, found.users), (0, True, None))
        self.answer("/rest/api/2/issue/PROJ-1/watchers", {"watchers": [{"name": "a"}, "junk", {"displayName": "b"}]})
        self.assertEqual(self.gateway.get_watchers("PROJ-1").count, 2)
        self.answer("/rest/api/2/issue/PROJ-1/votes", [])
        self.assertEqual(self.gateway.get_votes("PROJ-1").count, 0)
        self.answer("/rest/api/2/issue/PROJ-1/votes", {"votes": "3", "hasVoted": 1})
        self.assertEqual((self.gateway.get_votes("PROJ-1").count, self.gateway.get_votes("PROJ-1").has_voted), (0, False))

    def test_a_worklog_is_posted_with_its_query_and_the_new_entry_comes_back(self):
        request = worklog_request("1h 30m", comment="Did it", adjust="manual", reduce_by="30m", started="2026-10-15T08:30:00+02:00")
        entry = self.gateway.add_worklog("PROJ-2", request)
        post = self.last()
        self.assertEqual((post.path, post.query, post.body), ("/rest/api/2/issue/PROJ-2/worklog", {"adjustEstimate": ["manual"], "reduceBy": ["30m"]}, request.body))
        self.assertEqual((entry.time_spent, entry.time_spent_seconds, entry.started, entry.comment, entry.author.name), ("1h 30m", 5400, "2026-10-15T08:30:00.000+0200", "Did it", "jdoe"))
        self.gateway.add_worklog("PROJ-2", worklog_request("1h", adjust="new", new_estimate="1h 30m"))
        self.assertEqual(self.last().query, {"adjustEstimate": ["new"], "newEstimate": ["1h 30m"]})  # the space survives the query string
        self.gateway.add_worklog("PROJ-2", worklog_request("1h"))
        self.assertEqual((self.last().path, self.last().query), ("/rest/api/2/issue/PROJ-2/worklog", {}))

    def test_what_jira_refuses_in_a_worklog_is_a_rejection_or_an_auth_error(self):
        reference(self.jira).configuration["timeTrackingEnabled"] = False
        with self.assertRaises(RejectedError) as caught:
            self.gateway.add_worklog("PROJ-2", worklog_request("1h"))
        self.assertIn("Time tracking is disabled", caught.exception.message)
        reference(self.jira).configuration["timeTrackingEnabled"] = True
        reference(self.jira).denied.add("WORK_ON_ISSUES")
        with self.assertRaises(AuthError) as denied:
            self.gateway.add_worklog("PROJ-2", worklog_request("1h"))
        self.assertEqual(denied.exception.details["status"], 403)

    def test_the_worklogs_come_with_their_total_and_odd_entries_are_zero(self):
        page = self.gateway.list_worklogs("PROJ-1")
        self.assertEqual((page.total, [entry.id for entry in page.worklogs], page.worklogs[0].author.name), (2, ["10100", "10101"], "asmith"))
        self.answer("/rest/api/2/issue/PROJ-1/worklog", {"worklogs": [{"id": 5, "timeSpent": "1h", "timeSpentSeconds": "3600", "visibility": {"type": "group", "value": "staff"}}, "junk"]})
        odd = self.gateway.list_worklogs("PROJ-1")
        self.assertEqual((odd.total, odd.worklogs[0].time_spent_seconds, odd.worklogs[0].visibility), (1, 0, "group:staff"))

    def test_a_web_link_is_posted_as_it_is_and_the_answer_is_only_an_id(self):
        payload = {"object": {"url": "https://example.com/a", "title": "A"}, "globalId": "g1", "relationship": "documents"}
        ref = self.gateway.save_remote_link("PROJ-2", payload)
        post = self.last()
        self.assertEqual((post.path, post.body), ("/rest/api/2/issue/PROJ-2/remotelink", payload))
        self.assertTrue(ref.id)
        self.assertEqual(self.gateway.save_remote_link("PROJ-2", {**payload, "object": {"url": "https://example.com/b", "title": "B"}}).id, ref.id)  # the same global id: the same link
        self.assertEqual([link.title for link in self.gateway.list_remote_links("PROJ-2")], ["B"])

    def test_the_links_that_jira_lists_and_odd_ones(self):
        link = self.gateway.list_remote_links("PROJ-1")[0]
        self.assertEqual((link.id, link.title, link.url, link.relationship, link.global_id, link.application), ("10000", "Runbook", "https://wiki.example.com/runbook", "documentation", "wiki:runbook", ""))
        self.answer("/rest/api/2/issue/PROJ-1/remotelink", {"id": 9, "object": {"title": "Single", "url": "u"}, "application": {"name": "App"}})
        single = self.gateway.list_remote_links("PROJ-1")
        self.assertEqual([(item.id, item.application) for item in single], [("9", "App")])
        self.answer("/rest/api/2/issue/PROJ-1/remotelink", [{"id": 1}, "junk", {"id": 2, "object": "no"}])
        self.assertEqual([(item.id, item.title, item.url) for item in self.gateway.list_remote_links("PROJ-1")], [("1", "", ""), ("2", "", "")])
        self.answer("/rest/api/2/issue/PROJ-1/remotelink", None, method="POST")
        self.assertEqual(self.gateway.save_remote_link("PROJ-1", {"object": {"url": "u", "title": "t"}}).id, "")


if __name__ == "__main__":
    unittest.main()
