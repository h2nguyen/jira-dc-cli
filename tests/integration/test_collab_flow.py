"""The whole program for the collab area: the command line, the real adapters and the mock Jira, with no fakes."""
import json
import unittest

from jira_dc_cli.adapters.driving.cli.help import mcp_tools
from jira_dc_cli.adapters.driving.cli.registry import all_commands
from jira_dc_cli.version import __version__
from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_collab import collab
from tests.support.mock_reference import reference

OTHER_REPORTER = {"name": "asmith", "displayName": "Alex Smith"}


class CollabFlow(EndToEnd):
    def setUp(self):
        super().setUp()
        self.reference = reference(self.jira)
        self.routes = collab(self.jira)
        self.jira.issues["PROJ-2"]["fields"]["reporter"] = OTHER_REPORTER  # the default issues are all reported by the token's user, who may not vote for them
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))

    def writes(self):
        return [item for item in self.jira.requests if item.method in ("POST", "PUT", "DELETE")]

    def paths(self):
        return [item.path for item in self.jira.requests]

    def error(self, *argv):
        result = self.cli(*argv, "--json")
        return result.code, result.error()


class WatchersFlow(CollabFlow):
    def test_the_count_without_names_and_the_names_on_request(self):
        data = self.cli("watchers", "PROJ-1", "--json").json()
        self.assertEqual(data, {"key": "PROJ-1", "count": 2, "watching": False, "watchers": None})
        shown = self.cli("watchers", "PROJ-1", "--with-users", "--json").json()
        self.assertEqual([item["name"] for item in shown["watchers"]], ["asmith", "bwong"])
        self.assertEqual(self.cli("watchers", "PROJ-1", "--with-users", env={"JIRA_HIDE_USERS": "true"}).code, 1)
        self.assertNotIn("asmith", self.cli("watchers", "PROJ-1", env={"JIRA_HIDE_USERS": "true"}).out)

    def test_when_jira_gives_no_list_the_count_stands_and_the_names_are_null(self):
        self.reference.denied.add("VIEW_VOTERS_AND_WATCHERS")
        result = self.cli("watchers", "PROJ-1", "--with-users", "--json").json()
        self.assertEqual((result["count"], result["watchers"]), (2, None))
        self.assertIn("View Voters and Watchers", self.cli("watchers", "PROJ-1", "--with-users").out)

    def test_adding_sends_a_json_string_and_removing_a_query_and_a_dry_run_sends_neither(self):
        self.assertEqual(self.cli("watch", "PROJ-2", "--dry-run").code, 0)
        self.assertEqual(self.writes(), [])
        self.assertEqual([item.path for item in self.jira.requests], ["/rest/api/2/myself", "/rest/api/2/issue/PROJ-2/watchers"])
        self.jira.reset_requests()
        result = self.cli("watch", "PROJ-2", "--json").json()
        self.assertEqual((result["watching"], result["confirmed"], result["watchCount"]), (True, True, 1))
        post = self.writes()[0]
        self.assertEqual((post.method, post.path, post.raw, post.body, post.query), ("POST", "/rest/api/2/issue/PROJ-2/watchers", b'"jdoe"', "jdoe", {}))
        self.assertEqual(post.headers["Content-Type"], "application/json")
        self.assertEqual(self.routes.watchers["PROJ-2"], ["jdoe"])
        self.jira.reset_requests()
        self.assertEqual(self.cli("watch", "PROJ-2", "--remove", "--json").json()["watching"], False)
        delete = self.writes()[0]
        self.assertEqual((delete.method, delete.path, delete.query, delete.raw), ("DELETE", "/rest/api/2/issue/PROJ-2/watchers", {"username": ["jdoe"]}, b""))
        self.assertEqual(self.routes.watchers["PROJ-2"], [])

    def test_another_person_needs_a_permission_and_jira_answers_401_for_a_missing_one(self):
        self.assertEqual(self.cli("watch", "PROJ-2", "--user", "bwong").code, 0)
        self.assertEqual(self.routes.watchers["PROJ-2"], ["bwong"])
        self.reference.denied.add("MANAGE_WATCHERS")
        code, error = self.error("watch", "PROJ-2", "--user", "asmith")
        self.assertEqual((code, error["code"], error["details"]["status"]), (5, "AUTH", 401))
        self.assertIn("MANAGE_WATCHERS", error["hint"])
        self.assertEqual(self.cli("watch", "PROJ-2").code, 0)  # for yourself it is not needed
        self.assertEqual(self.routes.watchers["PROJ-2"], ["bwong", "jdoe"])

    def test_an_unknown_user_an_unknown_issue_and_a_switched_off_feature(self):
        code, error = self.error("watch", "PROJ-2", "--user", "nobody")
        self.assertEqual((code, error["code"]), (4, "NOT_FOUND"))
        self.assertIn("The user named 'nobody' does not exist", error["message"])
        self.assertIn("login name", error["hint"])
        self.assertEqual(self.error("watchers", "PROJ-999")[0], 4)
        self.reference.configuration["watchingEnabled"] = False
        code, error = self.error("watchers", "PROJ-2")
        self.assertEqual((code, error["code"]), (4, "NOT_FOUND"))
        self.assertIn("Watching is disabled", error["message"])


class VotesFlow(CollabFlow):
    def test_the_count_and_never_the_voters_although_jira_sends_them(self):
        raw = self.cli("api", "GET", "issue/PROJ-1/votes").json()
        self.assertEqual([voter["name"] for voter in raw["voters"]], ["asmith"])  # the mock does send them, as Jira does to who may see them
        result = self.cli("votes", "PROJ-1", "--json")
        self.assertEqual(result.json(), {"key": "PROJ-1", "count": 1, "hasVoted": False})
        self.assertNotIn("asmith", result.out + self.cli("votes", "PROJ-1").out)

    def test_voting_and_withdrawing(self):
        self.assertEqual(self.cli("vote", "PROJ-2", "--dry-run").code, 0)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.cli("vote", "PROJ-2", "--json").json()["count"], 1)
        post = self.writes()[0]
        self.assertEqual((post.method, post.path, post.raw), ("POST", "/rest/api/2/issue/PROJ-2/votes", b""))
        self.assertEqual(self.cli("vote", "PROJ-2").out, "You have voted for PROJ-2 already; nothing was sent.\n")
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.cli("vote", "PROJ-2", "--remove", "--json").json(), {"key": "PROJ-2", "action": "removed", "dryRun": False, "changed": True, "count": 0, "hasVoted": False, "message": ""})
        self.assertEqual([item.method for item in self.writes()], ["POST", "DELETE"])

    def test_a_vote_for_your_own_issue_is_refused_by_jira_and_its_words_are_shown(self):
        code, error = self.error("vote", "PROJ-1")
        self.assertEqual((code, error["code"]), (6, "REJECTED"))
        self.assertIn("You cannot vote for an issue you have reported.", error["message"])
        self.assertEqual([item.method for item in self.writes()], ["POST"])

    def test_voting_that_is_switched_off_is_a_404_of_jira(self):
        self.reference.configuration["votingEnabled"] = False
        code, error = self.error("votes", "PROJ-2")
        self.assertEqual((code, error["code"]), (4, "NOT_FOUND"))
        self.assertIn("Voting is disabled", error["message"])
        self.assertEqual(self.error("vote", "PROJ-2", "--dry-run")[0], 4)
        self.assertEqual(self.writes(), [])


class WorklogsFlow(CollabFlow):
    def test_the_list_newest_first_with_the_total_and_no_people(self):
        result = self.cli("worklogs", "PROJ-1", "--json")
        data = result.json()
        self.assertEqual(([entry["id"] for entry in data["worklogs"]], data["total"], data["timeSpentSeconds"], data["timeSpent"]), (["10101", "10100"], 2, 12600, "3h 30m"))
        for personal in ("jdoe", "asmith", "Alex", "alex.smith", "[~asmith]"):
            self.assertNotIn(personal, result.out)
        self.assertEqual(data["worklogs"][1]["comment"], "Reviewed with [~user] ([email])")
        self.assertEqual(self.cli("worklogs", "PROJ-1", "--with-users", "--json").json()["worklogs"][1]["author"]["name"], "asmith")

    def test_since_a_date_and_a_limit(self):
        self.assertEqual([entry["id"] for entry in self.cli("worklogs", "PROJ-1", "--since", "2026-10-15", "--json").json()["worklogs"]], ["10101"])
        self.assertEqual(len(self.cli("worklogs", "PROJ-1", "--limit", "1", "--json").json()["worklogs"]), 1)
        self.assertEqual(self.cli("worklogs", "PROJ-2").out, "No worklogs on PROJ-2.\n")
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/PROJ-1/worklog")), 2)  # the filter is applied here: Jira has no parameter for it

    def test_logging_work_sends_jiras_words_and_a_start_in_the_form_jira_reads(self):
        result = self.cli("log-work", "PROJ-2", "1h 30m", "--comment", "Reviewed", "--started", "2026-10-15T08:30:00+02:00", "--json")
        self.assertEqual(result.code, 0, result.err)
        post = self.writes()[0]
        self.assertEqual((post.method, post.path, post.query), ("POST", "/rest/api/2/issue/PROJ-2/worklog", {}))
        self.assertEqual(post.body, {"timeSpent": "1h 30m", "comment": "Reviewed", "started": "2026-10-15T08:30:00.000+0200"})
        data = result.json()
        self.assertEqual((data["confirmed"], data["worklog"]["timeSpentSeconds"], data["worklog"]["started"]), (True, 5400, "2026-10-15T08:30:00.000+0200"))
        self.assertEqual(self.routes.worklogs["PROJ-2"][0]["comment"], "Reviewed")

    def test_the_mock_refuses_an_offset_with_a_colon_so_that_the_form_matters(self):
        raw = self.cli("api", "POST", "issue/PROJ-2/worklog", "--data", '{"timeSpent": "1h", "started": "2026-10-15T08:30:00.000+02:00"}', "--write")
        self.assertEqual(raw.code, 6)
        self.assertIn("Date", raw.err)

    def test_days_are_converted_by_jira_and_the_estimate_is_adjusted_as_asked(self):
        data = self.cli("log-work", "PROJ-2", "2d", "--adjust-estimate", "new", "--new-estimate", "1d", "--json").json()
        self.assertEqual((data["seconds"], data["worklog"]["timeSpentSeconds"]), (None, 57600))
        post = self.writes()[0]
        self.assertEqual((post.path, post.query), ("/rest/api/2/issue/PROJ-2/worklog", {"adjustEstimate": ["new"], "newEstimate": ["1d"]}))
        self.cli("log-work", "PROJ-2", "30m", "--adjust-estimate", "manual", "--reduce-by", "10m")
        self.assertEqual(self.writes()[1].query, {"adjustEstimate": ["manual"], "reduceBy": ["10m"]})
        self.cli("log-work", "PROJ-2", "30m", "--adjust-estimate", "leave")
        self.assertEqual(self.writes()[2].query, {"adjustEstimate": ["leave"]})

    def test_a_dry_run_reads_the_issue_and_sends_nothing(self):
        self.assertEqual(self.cli("log-work", "PROJ-2", "1h", "--dry-run").code, 0)
        self.assertEqual(self.writes(), [])
        self.assertEqual([path for path in self.paths() if path != "/rest/api/2/field"], ["/rest/api/2/issue/PROJ-2"])  # the field list is read for any issue read
        self.assertEqual(self.error("log-work", "PROJ-999", "1h", "--dry-run")[0], 4)

    def test_a_guard_against_a_change_in_between(self):
        self.assertEqual(self.cli("log-work", "PROJ-2", "1h", "--if-unchanged-since", "2026-10-01T00:00:00Z").code, 3)
        self.assertEqual(self.writes(), [])

    def test_where_jira_refuses_the_refusal_is_shown(self):
        self.reference.denied.add("WORK_ON_ISSUES")
        code, error = self.error("log-work", "PROJ-2", "1h")
        self.assertEqual((code, error["code"], error["details"]["status"]), (5, "AUTH", 403))
        self.assertIn("WORK_ON_ISSUES", error["hint"])
        self.reference.denied.clear()
        self.reference.configuration["timeTrackingEnabled"] = False
        code, error = self.error("log-work", "PROJ-2", "1h")
        self.assertEqual((code, error["code"]), (6, "REJECTED"))
        self.assertIn("Time tracking is disabled", error["message"])
        self.assertIn("time tracking", error["hint"])
        self.assertNotIn("metadata", error["hint"])

    def test_a_time_that_jira_would_read_differently_never_reaches_it(self):
        self.assertEqual(self.cli("log-work", "PROJ-2", "1.5h").code, 1)
        self.assertEqual(self.cli("log-work", "PROJ-2", "90").code, 1)
        self.assertEqual(self.requests_to_issue(), [])

    def requests_to_issue(self):
        return [item for item in self.jira.requests if "/issue/" in item.path]


class RemoteLinksFlow(CollabFlow):
    def test_the_links_of_an_issue(self):
        data = self.cli("remote-links", "PROJ-1", "--json").json()
        self.assertEqual(data, [{"id": "10000", "title": "Runbook", "url": "https://wiki.example.com/runbook", "relationship": "documentation", "application": "", "globalId": "wiki:runbook"}])
        self.assertEqual(self.cli("remote-links", "PROJ-2").out, "No web links on PROJ-2.\n")

    def test_adding_a_link_and_reading_it_back(self):
        result = self.cli("remote-link", "PROJ-2", "--url", "https://example.com/pr/7?a=1&b=2", "--title", "Pull request 7", "--relationship", "implements", "--json")
        self.assertEqual(result.code, 0, result.err)
        post = self.writes()[0]
        self.assertEqual((post.method, post.path), ("POST", "/rest/api/2/issue/PROJ-2/remotelink"))
        self.assertEqual(post.body, {"object": {"url": "https://example.com/pr/7?a=1&b=2", "title": "Pull request 7"}, "relationship": "implements"})
        data = result.json()
        self.assertEqual((data["action"], data["confirmed"], data["link"]["title"], data["link"]["relationship"]), ("created", True, "Pull request 7", "implements"))

    def test_the_same_global_id_updates_the_link_and_a_dry_run_says_so(self):
        argv = ("remote-link", "PROJ-1", "--url", "https://wiki.example.com/runbook/v2", "--title", "Runbook v2", "--global-id", "wiki:runbook")
        self.assertIn("would update the web link", self.cli(*argv, "--dry-run").out)
        self.assertEqual(self.writes(), [])
        result = self.cli(*argv, "--json").json()
        self.assertEqual((result["action"], result["link"]["id"], result["link"]["title"]), ("updated", "10000", "Runbook v2"))
        self.assertEqual(len(self.cli("remote-links", "PROJ-1", "--json").json()), 1)

    def test_an_address_that_is_no_web_address_is_never_sent(self):
        for url in ("javascript:alert(1)", "https://user:secret@example.com/", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.assertEqual(self.cli("remote-link", "PROJ-2", "--url", url, "--title", "T").code, 1)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.requests_to_issue(), [])

    def requests_to_issue(self):
        return [item for item in self.jira.requests if "/issue/" in item.path]

    def test_where_jira_refuses_the_refusal_is_shown(self):
        self.reference.denied.add("LINK_ISSUES")
        code, error = self.error("remote-link", "PROJ-2", "--url", "https://example.com/", "--title", "T")
        self.assertEqual((code, error["code"], error["details"]["status"]), (5, "AUTH", 403))
        self.assertIn("LINK_ISSUES", error["hint"])
        self.reference.denied.clear()
        self.reference.configuration["issueLinkingEnabled"] = False
        self.assertEqual(self.error("remote-links", "PROJ-1")[0], 5)

    def test_what_jira_rejects_comes_with_its_own_message_and_a_hint_that_fits(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-2/remotelink", 400, method="POST", body={"errorMessages": [], "errors": {"object.url": "The url is not valid."}})
        code, error = self.error("remote-link", "PROJ-2", "--url", "https://example.com/", "--title", "T")
        self.assertEqual((code, error["code"]), (6, "REJECTED"))
        self.assertIn("The url is not valid.", error["message"])
        self.assertNotIn("metadata", error["hint"])


class WatchOnlyYourselfFlow(CollabFlow):
    def test_removing_another_persons_watch_is_refused_before_anything_is_sent(self):
        for argv in (("watch", "PROJ-1", "--user", "bwong", "--remove"), ("watch", "PROJ-1", "--user", "bwong", "--remove", "--dry-run")):
            with self.subTest(argv=argv):
                code, error = self.error(*argv)
                self.assertEqual((code, error["code"], error["exitCode"]), (2, "USAGE", 2))
                self.assertIn("'bwong' is not you", error["message"])
                self.assertIn("Manage Watchers", error["hint"])
        self.assertEqual(self.paths(), ["/rest/api/2/myself", "/rest/api/2/myself"])  # who you are, and nothing else
        self.assertEqual(self.writes(), [])
        self.assertEqual([item["name"] for item in self.cli("watchers", "PROJ-1", "--with-users", "--json").json()["watchers"]], ["asmith", "bwong"])

    def test_you_may_remove_yourself_as_me_or_by_your_name_in_any_case(self):
        self.assertEqual(self.cli("watch", "PROJ-2").code, 0)
        self.assertEqual(self.cli("watch", "PROJ-2", "--user", "ME", "--remove", "--json").json()["watching"], False)
        self.assertEqual(self.cli("watch", "PROJ-2").code, 0)
        removed = self.cli("watch", "PROJ-2", "--user", "JDoe", "--remove", "--json").json()
        self.assertEqual((removed["user"], removed["query"], removed["confirmed"]), ("JDoe", {"username": "jdoe"}, True))
        deletes = [item for item in self.writes() if item.method == "DELETE"]
        self.assertEqual([(item.path, item.query) for item in deletes], [("/rest/api/2/issue/PROJ-2/watchers", {"username": ["jdoe"]})] * 2)
        self.assertEqual(self.routes.watchers["PROJ-2"], [])

    def test_adding_another_person_still_works_and_is_confirmed_from_the_list(self):
        data = self.cli("watch", "PROJ-2", "--user", "bwong", "--json").json()
        self.assertEqual((data["user"], data["payload"], data["confirmed"], data["watchCount"]), ("bwong", "bwong", True, 1))
        self.assertEqual(self.writes()[0].body, "bwong")

    def test_under_the_policy_the_preview_shows_a_stand_in_and_a_made_call_says_nothing_of_what_it_sent(self):
        env = {"JIRA_HIDE_USERS": "true"}
        previewed = self.cli("watch", "PROJ-2", "--dry-run", "--json", env=env)
        preview = previewed.json()
        self.assertEqual((preview["payload"], preview["query"], preview["redacted"]), ("[you]", {}, True))
        self.assertEqual(self.writes(), [])
        made = self.cli("watch", "PROJ-2", "--json", env=env)
        self.assertEqual(made.code, 0, made.err)
        self.assertEqual((made.json()["payload"], made.json()["query"], made.json()["redacted"], made.json()["confirmed"]), (None, None, True, True))
        self.assertEqual(self.writes()[0].body, "jdoe")  # the call itself carried the real name, and the result does not claim otherwise
        removed = self.cli("watch", "PROJ-2", "--remove", "--json", env=env)
        self.assertEqual((removed.json()["payload"], removed.json()["query"], removed.json()["redacted"]), (None, None, True))
        self.assertEqual(self.writes()[1].query, {"username": ["jdoe"]})
        for result in (previewed, made, removed, self.cli("watch", "PROJ-2", env=env)):
            for personal in ("jdoe", "Jane", "jane.doe"):
                self.assertNotIn(personal, result.out + result.err)
        self.assertFalse(any("[you]" in str(item.raw) for item in self.jira.requests))  # the stand-in never reaches Jira

    def test_without_the_policy_nothing_is_redacted(self):
        data = self.cli("watch", "PROJ-2", "--json").json()
        self.assertEqual((data["payload"], data["query"], data["redacted"]), ("jdoe", {}, False))


class ReadBackFailureFlow(CollabFlow):
    """A write that Jira took and a read-back that Jira cannot answer (HTTP 503): the command must not report a failure, because whoever
    sees one runs it again and the work is logged twice. Exactly one write is sent, the exit code is 0 and the result says what was done."""

    def setUp(self):
        super().setUp()
        self.routes.reads_fail_after_a_write = 503
        self.env["JIRA_RETRY"] = "0"  # a read-back that fails stays failed: no waiting between attempts

    def mcp(self, tool, key, **arguments):
        """One call of `jira mcp --allow-write`: its reply and the result in it. The property that holds the issue key is asked of the
        description of the tool, so that the name of it does not matter here."""
        listed = mcp_tools([command for command in all_commands() if command.name != "mcp"], __version__, only="write")["tools"]
        key_property = next(item for item in listed if item["name"] == tool)["inputSchema"]["required"][0]
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {key_property: key, **arguments}}}
        result = self.cli("mcp", "--allow-write", stdin=json.dumps(request) + "\n")
        self.assertEqual(result.code, 0, result.err)
        reply = json.loads(result.out.splitlines()[0])["result"]
        return reply, json.loads(reply["content"][0]["text"])

    def test_log_work_sends_one_post_exits_0_and_says_that_the_work_was_logged(self):
        result = self.cli("log-work", "PROJ-2", "1h", "--comment", "Once", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["confirmed"], data["worklog"]["id"], data["worklog"]["timeSpent"], data["dryRun"]), (None, "10201", "1h", False))
        for words in ("WAS logged", "(worklog 10201)", "HTTP 503", "Do not run the command again", "jira worklogs PROJ-2"):
            self.assertIn(words, data["message"])
        self.assertIn("WAS logged", result.err)
        self.assertEqual([item.method for item in self.writes()], ["POST"])
        self.routes.reads_fail_after_a_write = 0
        listed = self.cli("worklogs", "PROJ-2", "--json").json()  # `jira worklogs KEY`, as the message says: the entry is there, once
        self.assertEqual((listed["total"], listed["worklogs"][0]["id"]), (1, "10201"))

    def test_log_work_in_text_mode_says_it_on_standard_error(self):
        result = self.cli("log-work", "PROJ-2", "1h")
        self.assertEqual((result.code, result.out), (0, "Logged 1h on PROJ-2 (worklog 10201, 3600 s)\n"))
        self.assertIn("Do not run the command again", result.err)
        self.assertEqual(len(self.writes()), 1)

    def test_remote_link_sends_one_post_exits_0_and_says_that_the_link_was_added(self):
        result = self.cli("remote-link", "PROJ-2", "--url", "https://example.com/pr/7", "--title", "Pull request 7", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["action"], data["link"], data["confirmed"], data["dryRun"]), ("created", None, None, False))
        for words in ("WAS added", "(id 10201)", "HTTP 503", "Do not run the command again", "second link", "jira remote-links PROJ-2"):
            self.assertIn(words, data["message"])
        self.assertEqual([item.method for item in self.writes()], ["POST"])
        self.routes.reads_fail_after_a_write = 0
        self.assertEqual([link["id"] for link in self.cli("remote-links", "PROJ-2", "--json").json()], ["10201"])

    def test_a_link_with_a_global_id_says_that_repeating_it_only_updates(self):
        result = self.cli("remote-link", "PROJ-1", "--url", "https://wiki.example.com/runbook/v2", "--title", "Runbook v2", "--global-id", "wiki:runbook", "--json")
        data = result.json()
        self.assertEqual((result.code, data["action"], data["link"], data["confirmed"]), (0, "updated", None, None))
        self.assertIn("WAS updated", data["message"])
        self.assertIn("with the same --global-id only updates that link", data["message"])
        self.assertEqual(len(self.writes()), 1)

    def test_watch_sends_one_write_exits_0_and_says_that_the_change_was_made(self):
        added = self.cli("watch", "PROJ-2", "--json")
        self.assertEqual(added.code, 0, added.err)
        data = added.json()
        self.assertEqual((data["action"], data["watchCount"], data["watching"], data["confirmed"], data["payload"], data["redacted"]), ("added", None, None, None, "jdoe", False))
        self.assertIn("the watcher was added, but reading the watcher list afterwards failed", data["message"])
        self.assertIn("jira watchers PROJ-2", data["message"])
        self.assertEqual([item.method for item in self.writes()], ["POST"])
        removed = self.cli("watch", "PROJ-2", "--remove", "--json")
        self.assertEqual((removed.code, removed.json()["confirmed"], removed.json()["query"]), (0, None, {"username": "jdoe"}))
        self.assertEqual([item.method for item in self.writes()], ["POST", "DELETE"])
        self.assertEqual(self.routes.watchers["PROJ-2"], [])

    def test_a_vote_sends_one_write_exits_0_and_says_that_the_vote_was_made(self):
        added = self.cli("vote", "PROJ-2", "--json")
        self.assertEqual(added.code, 0, added.err)
        data = added.json()
        self.assertEqual((data["action"], data["changed"], data["count"], data["hasVoted"]), ("added", True, None, None))
        self.assertIn("your vote was added, but reading the votes afterwards failed", data["message"])
        self.assertIn("jira votes PROJ-2", data["message"])
        self.assertEqual([item.method for item in self.writes()], ["POST"])
        self.assertEqual(self.routes.voters["PROJ-2"], ["jdoe"])

    def test_through_mcp_the_result_itself_says_that_the_work_was_logged_and_one_post_is_sent(self):
        reply, data = self.mcp("jira_log_work", "PROJ-2", time="1h", comment="Once")
        self.assertFalse(reply["isError"])
        self.assertEqual((data["confirmed"], data["worklog"]["id"]), (None, "10201"))
        for words in ("WAS logged", "Do not run the command again", "jira worklogs PROJ-2"):
            self.assertIn(words, data["message"])
        self.assertEqual([item.method for item in self.writes()], ["POST"])

    def test_through_mcp_the_other_writes_say_so_too(self):
        reply, link = self.mcp("jira_remote_link", "PROJ-2", url="https://example.com/pr/7", title="Pull request 7")
        self.assertEqual((reply["isError"], link["link"], link["confirmed"]), (False, None, None))
        self.assertIn("WAS added", link["message"])
        reply, watch = self.mcp("jira_watch", "PROJ-2")
        self.assertEqual((reply["isError"], watch["watching"], watch["confirmed"]), (False, None, None))
        self.assertIn("the watcher was added", watch["message"])
        reply, vote = self.mcp("jira_vote", "PROJ-2")
        self.assertEqual((reply["isError"], vote["count"], vote["hasVoted"], vote["changed"]), (False, None, None, True))
        self.assertIn("your vote was added", vote["message"])
        self.assertEqual([item.path for item in self.writes()], [f"/rest/api/2/issue/PROJ-2/{part}" for part in ("remotelink", "watchers", "votes")])

    def test_a_write_that_fails_is_still_a_failure_and_nothing_is_confirmed(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-2/worklog", 503, method="POST")
        code, error = self.error("log-work", "PROJ-2", "1h")
        self.assertEqual((code, error["code"]), (7, "UNAVAILABLE"))
        self.assertEqual(self.routes.worklogs.get("PROJ-2", []), [])

    def test_where_the_read_back_works_there_is_no_message(self):
        self.routes.reads_fail_after_a_write = 0
        data = self.cli("log-work", "PROJ-2", "1h", "--json").json()
        self.assertEqual((data["confirmed"], data["message"]), (True, ""))


class VoteAnswerFlow(CollabFlow):
    def test_a_vote_is_answered_with_200_and_the_count_as_the_reference_says(self):
        raw = self.cli("api", "POST", "issue/PROJ-2/votes", "--data", "{}", "--write")  # `api` insists on a body for a POST; the vote route ignores it
        self.assertEqual((raw.code, raw.out.strip()), (0, "1"))
        self.assertEqual(self.routes.voters["PROJ-2"], ["jdoe"])
        self.assertEqual(self.cli("api", "GET", "issue/PROJ-2/votes").json()["votes"], 1)  # `api` sends no DELETE: a withdrawal (204) goes through `vote --remove`
        withdrawn = self.cli("vote", "PROJ-2", "--remove", "--json").json()
        self.assertEqual((withdrawn["changed"], withdrawn["count"], withdrawn["hasVoted"], withdrawn["message"]), (True, 0, False, ""))
        self.assertEqual(self.routes.voters["PROJ-2"], [])

    def test_the_command_does_not_care_about_the_body_of_the_answer(self):
        data = self.cli("vote", "PROJ-2", "--json").json()
        self.assertEqual((data["changed"], data["count"], data["hasVoted"], data["message"]), (True, 1, True, ""))
        self.assertEqual(self.cli("vote", "PROJ-2", "--remove", "--json").json()["count"], 0)


class EverythingFlow(CollabFlow):
    def test_an_unknown_issue_is_a_404_for_every_command(self):
        for argv in (("watchers", "PROJ-999"), ("watch", "PROJ-999", "--dry-run"), ("votes", "PROJ-999"), ("vote", "PROJ-999", "--dry-run"), ("worklogs", "PROJ-999"),
                     ("log-work", "PROJ-999", "1h", "--dry-run"), ("remote-links", "PROJ-999"), ("remote-link", "PROJ-999", "--url", "https://example.com/", "--title", "T", "--dry-run")):
            with self.subTest(argv=argv):
                code, error = self.error(*argv)
                self.assertEqual((code, error["code"]), (4, "NOT_FOUND"))
                self.assertIn("Issue Does Not Exist", error["message"])
        self.assertEqual(self.writes(), [])

    def test_a_wrong_token_is_an_auth_error_for_every_command(self):
        self.write_config(self.jira.base_url, token="wrong")
        for argv in (("watchers", "PROJ-1"), ("votes", "PROJ-1"), ("worklogs", "PROJ-1"), ("remote-links", "PROJ-1"), ("watch", "PROJ-2"), ("log-work", "PROJ-2", "1h")):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, 5)

    def test_a_server_that_fails_is_unavailable_and_a_write_is_not_retried(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-2/worklog", 503, method="POST", times=3)
        self.assertEqual(self.cli("log-work", "PROJ-2", "1h").code, 7)
        self.assertEqual(len([item for item in self.jira.requests if item.method == "POST"]), 1)
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/votes", 503, times=2)
        self.assertEqual(self.cli("votes", "PROJ-1").code, 0)  # a read is

    def test_the_json_of_every_result_is_valid_and_the_output_holds_no_token(self):
        for argv in (("watchers", "PROJ-1"), ("votes", "PROJ-1"), ("worklogs", "PROJ-1"), ("remote-links", "PROJ-1"), ("watch", "PROJ-2", "--dry-run"),
                     ("vote", "PROJ-2", "--dry-run"), ("log-work", "PROJ-2", "1h", "--dry-run"), ("remote-link", "PROJ-2", "--url", "https://example.com/", "--title", "T", "--dry-run")):
            with self.subTest(argv=argv):
                result = self.cli(*argv, "--json")
                self.assertEqual(result.code, 0, result.err)
                json.loads(result.out)
                self.assertNotIn("test-token", result.out + result.err)


if __name__ == "__main__":
    unittest.main()
