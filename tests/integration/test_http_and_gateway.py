"""The HTTP transport and the Jira gateway against a mock Jira Data Center."""
import dataclasses
import socket
import tempfile
import unittest

from jira_dc_cli.adapters.driven.file_cache import FileCache
from jira_dc_cli.adapters.driven.http_transport import HttpTransport
from jira_dc_cli.adapters.driven.jira_rest import RestJiraGateway
from jira_dc_cli.application.settings import Settings
from jira_dc_cli.domain.errors import (
    AuthError,
    ConfigError,
    NotFoundError,
    RejectedError,
    UnavailableError,
)
from jira_dc_cli.domain.payloads import comment_payload, link_payload, transition_payload
from tests.support.fakes import MemoryCache
from tests.support.mock_jira import MockJira


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.jira = MockJira()
        self.jira.__enter__()
        self.addCleanup(self.jira.__exit__, None, None, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache_dir = folder.name
        self.gateway, self.transport, self.settings = self.make()

    def make(self, jira=None, cache=None, sleep=None, **changes):
        """A gateway on its own transport; the connection is closed when the test ends."""
        jira = jira or self.jira
        values = dict(base_url=jira.base_url, pat=jira.token, retry=2)
        values.update(changes)
        settings = Settings(**values)
        transport = HttpTransport(settings, sleep=sleep or (lambda seconds: None))
        self.addCleanup(transport.close)
        return RestJiraGateway(settings, transport, cache or FileCache(self.cache_dir)), transport, settings

    def gets(self, prefix):
        return [item for item in self.jira.requests if item.method == "GET" and item.path.startswith(prefix)]


class TransportTest(GatewayTestCase):
    def test_context_path_and_bearer_token(self):
        self.assertEqual(self.gateway.current_user().name, "jdoe")
        request = self.jira.requests[0]
        self.assertEqual(request.path, "/rest/api/2/myself")
        self.assertEqual(request.headers["Authorization"], f"Bearer {self.jira.token}")
        self.assertIn("jira-dc-cli/", request.headers["User-Agent"])
        self.assertIn("gzip", request.headers["Accept-Encoding"])

    def test_the_connection_is_kept_alive(self):
        self.gateway.current_user()
        first = self.transport._connection
        self.gateway.server_info()
        self.assertIsNotNone(first)
        self.assertIs(self.transport._connection, first)

    def test_gzip_answers_are_decoded(self):
        self.assertGreater(len(self.gateway.list_fields()), 3)

    def test_a_wrong_token_is_an_auth_error(self):
        gateway, _, _ = self.make(pat="wrong")
        with self.assertRaises(AuthError) as caught:
            gateway.current_user()
        self.assertEqual((caught.exception.exit_code, caught.exception.details["status"]), (5, 401))
        self.assertIn("setup-pat", caught.exception.hint)

    def test_errors_follow_the_http_status(self):
        cases = ((403, AuthError), (404, NotFoundError), (400, RejectedError), (500, UnavailableError), (302, ConfigError))
        for status, expected in cases:
            with self.subTest(status=status):
                self.jira.fail_next("/rest/api/2/myself", status, times=5)
                with self.assertRaises(expected):
                    self.make(retry=0)[0].current_user()
                self.jira._failures.clear()

    def test_the_explanation_of_jira_is_kept(self):
        with self.assertRaises(RejectedError) as caught:
            self.gateway.search("BROKEN", fields=["summary"])
        self.assertIn("Error in the JQL Query", caught.exception.message)
        self.assertEqual(caught.exception.exit_code, 6)

    def test_reads_are_retried(self):
        self.jira.fail_next("/rest/api/2/myself", 503, times=2)
        self.assertEqual(self.gateway.current_user().name, "jdoe")
        self.assertEqual(len(self.gets("/rest/api/2/myself")), 3)

    def test_429_is_retried(self):
        self.jira.fail_next("/rest/api/2/myself", 429)
        self.assertEqual(self.gateway.current_user().name, "jdoe")

    def test_429_waits_as_long_as_the_server_asks_but_not_forever(self):
        delays = []
        gateway, _, _ = self.make(sleep=delays.append)
        self.jira.fail_next("/rest/api/2/myself", 429, headers={"Retry-After": "3"})
        gateway.current_user()
        self.jira.fail_next("/rest/api/2/myself", 429, headers={"Retry-After": "86400"})
        gateway.current_user()
        self.jira.fail_next("/rest/api/2/myself", 429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
        gateway.current_user()
        self.assertEqual(delays, [3.0, 30.0, 1.0])  # the server's wish, capped, and the default for a date

    def test_a_rate_limit_that_does_not_end_is_a_temporary_failure_not_a_rejection(self):
        self.jira.fail_next("/rest/api/2/myself", 429, times=10)
        with self.assertRaises(UnavailableError) as caught:
            self.gateway.current_user()
        self.assertEqual(caught.exception.exit_code, 7)
        self.assertIn("rate", caught.exception.message)

    def test_a_damaged_gzip_answer_is_a_clean_failure(self):
        self.jira.broken_gzip = True
        with self.assertRaises(UnavailableError) as caught:
            self.gateway.current_user()
        self.assertIn("could not be read", caught.exception.message)
        self.assertEqual(len(self.gets("/rest/api/2/myself")), 3)  # a read is tried again

    def test_plain_http_is_refused_because_the_token_would_travel_unencrypted(self):
        gateway, transport, _ = self.make(base_url="http://jira.example.com/jira")
        with self.assertRaises(ConfigError) as caught:
            gateway.current_user()
        self.assertIn("plain http", caught.exception.message)
        self.assertIn("JIRA_ALLOW_HTTP", caught.exception.hint)
        self.assertEqual(self.jira.requests, [])

    def test_plain_http_can_be_allowed_and_this_computer_is_always_fine(self):
        _, allowed, _ = self.make(base_url="http://jira.example.com/jira", allow_http=True)
        self.assertEqual(allowed._target(), ("http", "jira.example.com", None, "/jira"))
        for host in ("localhost", "127.0.0.1", "127.1.2.3", "[::1]"):
            with self.subTest(host=host):
                _, local, _ = self.make(base_url=f"http://{host}:8080/jira")
                self.assertEqual(local._target()[0], "http")

    def test_a_refusal_never_echoes_the_token(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1", 400, body={"errorMessages": [f"bad request, the header said Bearer {self.jira.token}"], "errors": {"x": self.jira.token}})
        with self.assertRaises(RejectedError) as caught:
            self.gateway.get_issue("PROJ-1")
        shown = f"{caught.exception.message} {caught.exception.details}"
        self.assertNotIn(self.jira.token, shown)
        self.assertIn("********", shown)

    def test_under_the_privacy_policy_a_refusal_carries_no_mentions_or_addresses(self):
        gateway, _, _ = self.make(hide_users=True)
        self.jira.fail_next("/rest/api/2/issue/PROJ-1", 400, body={"errorMessages": ["Cannot assign to [~jdoe] (jane.doe@example.com)"], "errors": {}})
        with self.assertRaises(RejectedError) as caught:
            gateway.get_issue("PROJ-1")
        shown = f"{caught.exception.message} {caught.exception.details}"
        for personal in ("[~jdoe]", "jane.doe", "example.com"):
            self.assertNotIn(personal, shown)

    def test_by_default_too_a_refusal_carries_no_mentions_addresses_or_personal_links(self):
        text = "The user [~asmith] (asmith@example.com, see https://contoso-my.sharepoint.com/personal/a_b/x) may not edit this."
        for status, error, times in ((400, RejectedError, 1), (403, AuthError, 1), (404, NotFoundError, 1), (500, UnavailableError, 5)):
            with self.subTest(status=status):
                self.jira.fail_next("/rest/api/2/issue/PROJ-1", status, body={"errorMessages": [text], "errors": {"summary": text}}, times=times)
                with self.assertRaises(error) as caught:
                    self.gateway.get_issue("PROJ-1")
                shown = f"{caught.exception.message} {caught.exception.details}"
                for personal in ("asmith", "example.com", "sharepoint"):
                    self.assertNotIn(personal, shown)
                self.assertIn("[~user]", shown)

    def test_retries_end(self):
        self.jira.fail_next("/rest/api/2/myself", 503, times=10)
        with self.assertRaises(UnavailableError):
            self.gateway.current_user()
        self.assertEqual(len(self.gets("/rest/api/2/myself")), 3)  # one try and two retries

    def test_writes_are_never_retried(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/comment", 503, times=5, method="POST")
        with self.assertRaises(UnavailableError):
            self.gateway.add_comment("PROJ-1", comment_payload("hello"))
        self.assertEqual(len(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/comment")), 1)

    def test_nothing_listening(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        gateway, _, _ = self.make(base_url=f"http://127.0.0.1:{port}", retry=0)
        with self.assertRaises(UnavailableError) as caught:
            gateway.current_user()
        self.assertEqual(caught.exception.exit_code, 7)
        self.assertIn("jira doctor", caught.exception.hint)

    def test_a_silent_server_times_out(self):
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen(1)
            gateway, _, _ = self.make(base_url=f"http://127.0.0.1:{silent.getsockname()[1]}", retry=0, max_time=0.3)
            with self.assertRaises(UnavailableError):
                gateway.current_user()

    def test_configuration_is_checked_when_used_not_when_created(self):
        for changes, word in (({"base_url": ""}, "JIRA_BASE_URL"), ({"pat": ""}, "JIRA_PAT"), ({"base_url": "ftp://x"}, "valid web address")):
            with self.subTest(changes=changes):
                gateway, _, _ = self.make(**changes)
                with self.assertRaises(ConfigError) as caught:
                    gateway.current_user()
                self.assertIn(word, caught.exception.message)

    def test_an_unreadable_ca_bundle_is_a_config_error(self):
        gateway, _, _ = self.make(base_url="https://localhost:1", ca_bundle="missing.pem")
        with self.assertRaises(ConfigError) as caught:
            gateway.current_user()
        self.assertIn("JIRA_CA_BUNDLE", caught.exception.message)


class IssueGatewayTest(GatewayTestCase):
    def test_get_issue_maps_everything(self):
        issue = self.gateway.get_issue("PROJ-1")
        self.assertEqual((issue.key, issue.issue_type, issue.status.name, issue.status.category), ("PROJ-1", "Story", "To Do", "new"))
        self.assertEqual((issue.story_points, issue.epic_key, issue.labels, issue.components, issue.fix_versions), (5.0, "PROJ-9", ["backend"], ["Demo"], ["Release 1.0"]))
        self.assertEqual([(sprint.name, sprint.state, sprint.id) for sprint in issue.sprints], [("Demo Sprint 3", "ACTIVE", 7)])
        self.assertEqual(issue.acceptance, "1. GIVEN a, WHEN b, THEN c.")
        self.assertEqual((issue.comment_total, issue.comments[0].id), (1, "10"))
        self.assertEqual(issue.assignee.name, "jdoe")
        self.assertIsNotNone(issue.raw)

    def test_logical_fields_are_sent_as_custom_field_ids(self):
        self.gateway.get_issue("PROJ-1", fields=["summary", "story_points", "epic_link"])
        query = self.jira.requests[-1].query["fields"][0]
        self.assertEqual(sorted(query.split(",")), sorted(["summary", "customfield_10001", "customfield_10002"]))

    def test_unknown_issue(self):
        with self.assertRaises(NotFoundError) as caught:
            self.gateway.get_issue("PROJ-999")
        self.assertIn("Issue Does Not Exist", caught.exception.message)

    def test_search_pages_and_total(self):
        page = self.gateway.search('project = "PROJ" ORDER BY created DESC', fields=["summary"], start_at=2, max_results=50)
        self.assertEqual((page.total, page.start_at, [issue.key for issue in page.issues]), (6, 2, ["PROJ-4", "PROJ-3"]))
        self.assertEqual(len(page.raw_issues), 2)

    def test_extra_search_fields_land_in_extra(self):
        page = self.gateway.search('project = "PROJ"', fields=["summary", "labels", "customfield_10004"], max_results=1)
        self.assertEqual(page.issues[0].extra, {"customfield_10004": "1. GIVEN a, WHEN b, THEN c."})
        self.assertEqual(page.issues[0].labels, ["backend"])

    def test_transitions(self):
        self.assertEqual([item.name for item in self.gateway.list_transitions("PROJ-2")], ["Reopen", "Finish"])
        self.gateway.transition_issue("PROJ-2", transition_payload("81", "All done"))
        self.assertEqual(self.gateway.get_issue("PROJ-2", fields=["status"]).status.name, "Done")
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue/PROJ-2/transitions")[0].body["update"]["comment"][0]["add"]["body"], "All done")
        with self.assertRaises(RejectedError):
            self.gateway.transition_issue("PROJ-2", transition_payload("999"))

    def test_comments(self):
        created = self.gateway.add_comment("PROJ-1", comment_payload("A note"))
        self.assertEqual(created.body, "A note")
        self.assertEqual([comment.body for comment in self.gateway.list_comments("PROJ-1")][-1], "A note")
        with self.assertRaises(RejectedError):
            self.gateway.add_comment("PROJ-1", comment_payload("REJECT this"))

    def test_links(self):
        self.assertEqual([link.name for link in self.gateway.list_link_types()], ["Blocks", "Has to be done after", "Pre-requisite"])
        self.gateway.create_link(link_payload("Blocks", "PROJ-1", "PROJ-2"))
        issue = self.gateway.get_issue("PROJ-2")
        self.assertEqual((issue.links[0].phrase, issue.links[0].other.key, issue.links[0].direction), ("is blocked by", "PROJ-1", "inward"))

    def test_create_and_update(self):
        created = self.gateway.create_issue({"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "Fresh"}})
        self.assertEqual(created.key, "PROJ-101")
        self.assertEqual(created.url, f"{self.jira.base_url}/browse/PROJ-101")
        self.gateway.update_issue("PROJ-101", {"fields": {"summary": "Renamed"}})
        self.assertEqual(self.gateway.get_issue("PROJ-101").summary, "Renamed")
        with self.assertRaises(RejectedError) as caught:
            self.gateway.create_issue({"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": ""}})
        self.assertIn("summary", caught.exception.message)

    def test_project_calls(self):
        self.assertEqual([project.key for project in self.gateway.list_projects()], ["PROJ", "DEMO"])
        project = self.gateway.get_project("PROJ")
        self.assertEqual((project.name, project.issue_types, project.lead.name), ("Project PROJ", ["Epic", "Story", "Bug"], "jdoe"))
        self.assertEqual(self.gateway.list_versions("PROJ"), ["Release 1.0", "Release 2.0"])
        self.assertTrue(self.gateway.has_permission("PROJ", "ADMINISTER_PROJECTS"))
        self.assertFalse(self.gateway.has_permission("DEMO", "ADMINISTER_PROJECTS"))
        with self.assertRaises(NotFoundError):
            self.gateway.get_project("NOPE")

    def test_raw_requests(self):
        self.assertEqual(self.gateway.request("GET", "/rest/agile/1.0/board")["values"][0]["id"], 1200)
        self.assertEqual(self.gateway.request("GET", "/issue/PROJ-1?fields=summary")["key"], "PROJ-1")
        self.assertEqual(self.jira.requests[-1].path, "/rest/api/2/issue/PROJ-1")

    def test_a_custom_api_path(self):
        gateway, _, _ = self.make(api_path="/rest/api/latest")
        self.jira.fail_next("/rest/api/latest/myself", 404)
        with self.assertRaises(NotFoundError):
            gateway.current_user()


class FieldsAndCachingTest(GatewayTestCase):
    def test_field_ids_by_name_and_by_setting(self):
        self.assertEqual(self.gateway.field_ids(), {"story_points": "customfield_10001", "epic_link": "customfield_10002", "sprint": "customfield_10003", "acceptance": "customfield_10004"})
        gateway, _, _ = self.make(story_points_field="customfield_777")
        self.assertEqual(gateway.field_ids()["story_points"], "customfield_777")

    def test_the_field_list_is_cached_between_runs(self):
        self.gateway.list_fields()
        second, _, _ = self.make()
        second.list_fields()
        self.assertEqual(len(self.gets("/rest/api/2/field")), 1)
        second.list_fields(refresh=True)
        self.assertEqual(len(self.gets("/rest/api/2/field")), 2)

    def test_ids_are_resolved_once_per_run(self):
        self.gateway.field_ids()
        self.gateway.field_ids()
        self.gateway.get_issue("PROJ-1")
        self.assertEqual(len(self.gets("/rest/api/2/field")), 1)

    def test_no_cache_setting(self):
        for _ in range(2):
            gateway, _, _ = self.make(no_cache=True)
            gateway.list_fields()
        self.assertEqual(len(self.gets("/rest/api/2/field")), 2)

    def test_any_cache_that_follows_the_port_can_stand_in(self):
        memory = MemoryCache()
        for _ in range(2):
            gateway, _, _ = self.make(cache=memory)
            gateway.list_fields()
        self.assertEqual(len(self.gets("/rest/api/2/field")), 1)

    def test_the_cache_is_per_jira_instance(self):
        self.gateway.list_fields()
        with MockJira(context_path="/other") as other:
            gateway, _, _ = self.make(jira=other)
            gateway.list_fields()
            self.assertEqual(len(other.sent("GET", "/rest/api/2/field")), 1)

    def test_issue_types_are_cached_per_project(self):
        self.assertEqual([item.name for item in self.gateway.list_issue_types("PROJ")], ["Epic", "Story", "Bug"])
        self.gateway.list_issue_types("PROJ")
        self.assertEqual(len(self.gets("/rest/api/2/issue/createmeta/PROJ")), 1)
        self.gateway.list_issue_types("PROJ", refresh=True)
        self.assertEqual(len(self.gets("/rest/api/2/issue/createmeta/PROJ")), 2)

    def test_create_metadata_for_one_type(self):
        self.assertEqual(self.gateway.create_metadata("PROJ", "10200")["values"][0]["fieldId"], "summary")

    def test_server_info(self):
        self.assertEqual(self.gateway.server_info()["version"], "11.3.12")

    def test_a_custom_field_from_the_settings_replaces_the_catalog_lookup(self):
        settings = dataclasses.replace(self.settings, epic_link_field="customfield_10002")
        self.assertEqual(settings.field_overrides(), {"epic_link": "customfield_10002"})


if __name__ == "__main__":
    unittest.main()
