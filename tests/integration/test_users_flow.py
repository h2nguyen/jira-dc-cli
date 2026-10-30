"""Users through the whole program: resolve a name, list people, assign an issue and `--assignee`, against the mock Jira."""
import unittest

from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_users import _user, routes

SEARCH = "/rest/api/2/user/search"
ASSIGNABLE = "/rest/api/2/user/assignable/search"
ASSIGNEE = "/rest/api/2/issue/PROJ-1/assignee"
PERSONAL = ("Jane", "Alex", "Smith", "Doe", "Roe", "Sample", "example.com", "alex.smith")


class UsersFlowTest(EndToEnd):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))  # every call is one that the 11.3 reference documents
        self.mock = routes(self.jira)

    def assignee_of(self, key="PROJ-1"):
        assignee = self.jira.issues[key]["fields"]["assignee"]
        return assignee["name"] if assignee else None


class ResolveTest(UsersFlowTest):
    def test_a_unique_match_over_the_user_search_with_inactive_users_included(self):
        data = self.cli("users", "resolve", "Alex Smith", "--json").json()
        self.assertEqual(data, {"query": "Alex Smith", "matches": 1, "name": "asmith", "active": True})
        (request,) = self.jira.sent("GET", SEARCH)
        self.assertEqual(request.query, {"username": ["Alex Smith"], "maxResults": ["100"], "includeInactive": ["true"]})

    def test_no_person_is_printed(self):
        for argv in (("users", "resolve", "asmith"), ("users", "resolve", "asmith", "--json"), ("users", "resolve", "Jane", "--json")):
            result = self.cli(*argv)
            with self.subTest(argv=argv):
                self.assertEqual(result.code, 0, result.err)
                for personal in ("Alex Smith", "alex.smith", "Jane Doe", "Jane Roe", "example.com"):
                    self.assertNotIn(personal, result.out + result.err)

    def test_an_ambiguous_query_is_counted(self):
        self.assertEqual(self.cli("users", "resolve", "Jane", "--json").json()["matches"], 2)
        self.assertEqual(self.cli("users", "resolve", "Sam Sample", "--json").json()["matches"], 2)

    def test_nobody(self):
        self.assertEqual(self.cli("users", "resolve", "zed", "--json").json()["matches"], 0)

    def test_an_inactive_user(self):
        data = self.cli("users", "resolve", "olduser", "--json").json()
        self.assertEqual((data["name"], data["active"]), ("olduser", False))

    def test_an_issue_narrows_the_search_to_the_users_who_can_be_assigned_it(self):
        self.assertEqual(self.cli("users", "resolve", "asmith", "--issue", "PROJ-1", "--json").json()["name"], "asmith")
        (request,) = self.jira.sent("GET", ASSIGNABLE)
        self.assertEqual(request.query["issueKey"], ["PROJ-1"])
        self.assertNotIn("project", request.query)
        self.assertEqual(self.cli("users", "resolve", "olduser", "--issue", "PROJ-1", "--json").json()["matches"], 0)
        self.mock.unassignable.add("asmith")
        self.assertEqual(self.cli("users", "resolve", "asmith", "--issue", "PROJ-1", "--json").json()["matches"], 0)

    def test_a_project_narrows_it_for_an_issue_that_is_yet_to_be_created(self):
        self.assertEqual(self.cli("users", "resolve", "asmith", "--project", "PROJ", "--json").json()["name"], "asmith")
        self.assertEqual(self.jira.sent("GET", ASSIGNABLE)[0].query["project"], ["PROJ"])

    def test_an_unknown_issue_or_project_is_exit_4(self):
        self.assertEqual(self.cli("users", "resolve", "asmith", "--issue", "PROJ-999").code, 4)
        self.assertEqual(self.cli("users", "resolve", "asmith", "--project", "NOPE").code, 4)

    def test_me_asks_for_the_token_user_and_searches_for_nobody(self):
        self.assertEqual(self.cli("users", "resolve", "me", "--json").json()["name"], "jdoe")
        self.assertEqual(self.jira.sent("GET", SEARCH), [])
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/myself")), 1)

    def test_a_name_with_a_space_a_quote_and_an_accent_survives_the_query_string(self):
        self.mock.users.append(_user("obrien", "Ó Brien & Sons", "o'brien@example.com", True))
        data = self.cli("users", "resolve", "Ó Brien & Sons", "--json").json()
        self.assertEqual((data["matches"], data["name"]), (1, "obrien"))
        self.assertEqual(self.cli("users", "resolve", "o'brien@example.com", "--json").json()["name"], "obrien")

    def test_under_the_privacy_policy_resolve_works_and_with_users_is_refused(self):
        env = {"JIRA_HIDE_USERS": "true"}
        self.assertEqual(self.cli("users", "resolve", "asmith", "--json", env=env).json()["name"], "asmith")
        refused = self.cli("users", "resolve", "asmith", "--with-users", env=env)
        self.assertEqual(refused.code, 1)
        self.assertIn("JIRA_HIDE_USERS=true", refused.err)

    def test_a_failing_search_is_the_error_of_the_status(self):
        self.jira.fail_next(SEARCH, 403, body={"errorMessages": ["You do not have the permission to browse users."]})
        result = self.cli("users", "resolve", "asmith", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))


class SearchTest(UsersFlowTest):
    def test_the_list_needs_with_users_and_shows_them(self):
        result = self.cli("users", "search", "Jane", "--with-users", "--json")
        self.assertEqual([item["name"] for item in result.json()["users"]], ["jdoe", "jroe"])
        self.assertEqual(result.json()["users"][0], {"name": "jdoe", "displayName": "Jane Doe", "email": "jane.doe@example.com", "active": True})
        self.assertEqual(self.cli("users", "search", "Jane").code, 2)

    def test_the_privacy_policy_refuses_it_before_a_request_is_made(self):
        result = self.cli("users", "search", "Jane", "--with-users", env={"JIRA_HIDE_USERS": "true"})
        self.assertEqual(result.code, 1)
        self.assertEqual(self.jira.sent("GET", SEARCH), [])


class AssignTest(UsersFlowTest):
    def test_the_issue_is_assigned_and_read_back(self):
        result = self.cli("assign", "PROJ-1", "Alex Smith", "--json")
        self.assertEqual(result.json(), {"key": "PROJ-1", "action": "assigned", "payload": {"name": "asmith"}, "dryRun": False, "assignee": "asmith"})
        (sent,) = self.jira.sent("PUT", ASSIGNEE)
        self.assertEqual(sent.body, {"name": "asmith"})
        self.assertEqual(self.assignee_of(), "asmith")
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["updated"], "2026-10-20T09:00:00.000+0200")

    def test_the_users_are_searched_among_those_who_can_be_assigned_that_issue(self):
        self.cli("assign", "PROJ-1", "Alex Smith")
        self.assertEqual(self.jira.sent("GET", ASSIGNABLE)[0].query["issueKey"], ["PROJ-1"])

    def test_unassigned_and_automatic(self):
        self.assertEqual(self.cli("assign", "PROJ-1", "unassigned").out, "PROJ-1 is now unassigned\n")
        self.assertEqual(self.jira.sent("PUT", ASSIGNEE)[0].body, {"name": None})
        self.assertIsNone(self.assignee_of())
        self.mock.default_assignee = "asmith"
        self.assertEqual(self.cli("assign", "PROJ-1", "automatic").out, "Assigned PROJ-1 to asmith (the default assignee)\n")
        self.assertEqual(self.jira.sent("PUT", ASSIGNEE)[1].body, {"name": "-1"})

    def test_me(self):
        self.cli("assign", "PROJ-1", "unassigned")
        self.assertEqual(self.cli("assign", "PROJ-1", "me", "--json").json()["assignee"], "jdoe")
        self.assertEqual(self.jira.sent("GET", SEARCH) + self.jira.sent("GET", ASSIGNABLE), [])  # the token's user needs no search

    def test_a_dry_run_checks_the_issue_and_the_user_and_sends_nothing(self):
        result = self.cli("assign", "PROJ-1", "Alex Smith", "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("it would be assigned to asmith; nothing was sent", result.out)
        self.assertEqual(self.jira.sent("PUT", ASSIGNEE), [])
        self.assertEqual(self.assignee_of(), "jdoe")
        self.assertEqual(self.cli("assign", "PROJ-999", "asmith", "--dry-run").code, 4)

    def test_a_stale_issue_is_exit_3_and_nothing_is_sent(self):
        result = self.cli("assign", "PROJ-1", "asmith", "--if-unchanged-since", "2026-10-01T00:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.jira.sent("PUT", ASSIGNEE), [])

    def test_an_ambiguous_name_nobody_and_somebody_who_may_not_be_assigned(self):
        ambiguous = self.cli("assign", "PROJ-1", "Jane", "--json")
        self.assertEqual((ambiguous.code, ambiguous.error()["details"]), (1, {"query": "Jane", "matches": 2}))
        self.assertEqual(self.cli("assign", "PROJ-1", "zed").code, 4)
        self.mock.unassignable.add("asmith")
        self.assertEqual(self.cli("assign", "PROJ-1", "asmith").code, 4)
        self.assertEqual(self.jira.sent("PUT", ASSIGNEE), [])

    def test_jira_answers_401_for_a_missing_permission_and_the_hint_says_so(self):
        self.jira.fail_next(ASSIGNEE, 401, method="PUT", body={"errorMessages": ["You do not have permission to assign issues."]})
        result = self.cli("assign", "PROJ-1", "asmith", "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"], error["details"]["status"]), (5, "AUTH", 401))
        self.assertIn("Assign Issues", error["hint"])
        self.assertEqual(self.assignee_of(), "jdoe")

    def test_a_user_that_jira_does_not_know_is_exit_4(self):
        self.jira.fail_next(ASSIGNEE, 404, method="PUT", body={"errorMessages": ['The user "asmith" does not exist: asmith']})
        self.assertEqual(self.cli("assign", "PROJ-1", "asmith").code, 4)

    def test_the_result_holds_no_person(self):
        result = self.cli("assign", "PROJ-1", "Alex Smith", "--json")
        for personal in PERSONAL:
            self.assertNotIn(personal, result.out)


class AssigneeOptionTest(UsersFlowTest):
    def test_update_resolves_the_display_name_and_sends_the_user_name(self):
        self.assertEqual(self.cli("update", "PROJ-1", "--assignee", "Alex Smith").out, "Updated PROJ-1\n")
        self.assertEqual(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1")[0].body, {"fields": {"assignee": {"name": "asmith"}}})
        self.assertEqual(self.assignee_of(), "asmith")

    def test_the_dry_run_shows_only_the_resolved_user_name(self):
        result = self.cli("update", "PROJ-1", "--assignee", "Alex Smith", "--dry-run", "--json")
        self.assertEqual(result.json()["payload"], {"fields": {"assignee": {"name": "asmith"}}})
        for personal in PERSONAL:
            self.assertNotIn(personal, result.out)
        self.assertEqual(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1"), [])

    def test_no_user_lookup_through_the_raw_api_is_needed(self):
        self.cli("update", "PROJ-1", "--assignee", "Alex Smith", "--dry-run")
        self.assertEqual([item.path for item in self.jira.requests if "/user/" in item.path], [ASSIGNABLE])

    def test_an_ambiguous_assignee_is_refused_with_the_count_and_nothing_is_sent(self):
        result = self.cli("update", "PROJ-1", "--assignee", "Jane", "--json")
        self.assertEqual((result.code, result.error()["details"]), (1, {"query": "Jane", "matches": 2}))
        self.assertEqual(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1"), [])

    def test_create_resolves_the_assignee_among_the_users_of_the_project(self):
        result = self.cli("create", "--type", "Story", "--summary", "Assigned story", "--assignee", "Alex Smith", "--json")
        self.assertEqual(result.code, 0, result.err)
        fields = self.jira.sent("POST", "/rest/api/2/issue")[0].body["fields"]
        self.assertEqual(fields["assignee"], {"name": "asmith"})
        self.assertEqual(self.jira.sent("GET", ASSIGNABLE)[0].query["project"], ["PROJ"])

    def test_me_unassigned_and_automatic_in_update(self):
        for word, expected in (("me", "jdoe"), ("unassigned", None), ("automatic", "-1")):
            payload = self.cli("update", "PROJ-1", "--assignee", word, "--dry-run", "--json").json()["payload"]
            with self.subTest(word=word):
                self.assertEqual(payload["fields"]["assignee"], {"name": expected})


if __name__ == "__main__":
    unittest.main()
