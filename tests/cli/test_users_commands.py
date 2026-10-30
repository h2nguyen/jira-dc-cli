"""`jira users` and `jira assign`, and `--assignee` of `update` and `create`: what is printed, what is kept out, and the exit codes."""
import dataclasses
import unittest

from jira_dc_cli.domain.errors import AuthError
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, MemoryFiles

PEOPLE = ("Jane", "Alex", "Smith", "Doe", "Roe", "example.com", "jane.doe", "alex.smith")


class UsersTestCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles({"query.txt": "Alex Smith\n"})

    def run_cli(self, *argv, **options):
        options.setdefault("files", self.files)
        return run_cli(*argv, gateway=self.gateway, **options)

    def hidden(self):
        return dataclasses.replace(DEFAULT_SETTINGS, hide_users=True)

    def assert_no_people(self, text):
        for personal in PEOPLE:
            self.assertNotIn(personal, text)


class ResolveTest(UsersTestCase):
    def test_a_unique_match_prints_the_user_name_and_nothing_personal(self):
        result = self.run_cli("users", "resolve", "Alex", "Smith")  # the words need no quotes
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out.splitlines(), ["Query:   Alex Smith", "Matches: 1", "Name:    asmith", "Active:  yes"])
        self.assertNotIn("alex.smith", result.out)

    def test_json_has_exactly_four_keys(self):
        data = self.run_cli("users", "resolve", "Alex Smith", "--json").json()
        self.assertEqual(data, {"query": "Alex Smith", "matches": 1, "name": "asmith", "active": True})

    def test_an_ambiguous_name_says_how_many_and_prints_no_list_of_people(self):
        result = self.run_cli("users", "resolve", "Jane", "--json")
        self.assertEqual(result.json(), {"query": "Jane", "matches": 2, "name": None, "active": None})
        text = self.run_cli("users", "resolve", "Jane")
        self.assertEqual(text.out.splitlines(), ["Query:   Jane", "Matches: 2"])
        self.assertIn("2 users fit 'Jane'", text.err)
        for output in (result.out + result.err, text.out + text.err):
            for personal in ("jdoe", "jroe", "Jane Doe", "Jane Roe", "jane.doe", "jane.roe", "example.com"):
                self.assertNotIn(personal, output)

    def test_nobody(self):
        result = self.run_cli("users", "resolve", "zed", "--json")
        self.assertEqual((result.code, result.json()), (0, {"query": "zed", "matches": 0, "name": None, "active": None}))
        self.assertIn("No user fits 'zed'", self.run_cli("users", "resolve", "zed").err)

    def test_an_inactive_user_is_active_false(self):
        self.assertEqual(self.run_cli("users", "resolve", "olduser", "--json").json()["active"], False)
        self.assertIn("Active:  no", self.run_cli("users", "resolve", "olduser").out)

    def test_me_is_the_token_user(self):
        self.assertEqual(self.run_cli("users", "resolve", "me", "--json").json(), {"query": "me", "matches": 1, "name": "jdoe", "active": True})

    def test_an_issue_or_a_project_narrows_the_search_to_the_users_who_can_be_assigned(self):
        self.assertEqual(self.run_cli("users", "resolve", "olduser", "--issue", "PROJ-1", "--json").json()["matches"], 0)
        self.assertEqual(self.gateway.called("find_users")[0][1:], ("olduser", "", "PROJ-1", True))
        self.run_cli("users", "resolve", "asmith", "--project", "PROJ")
        self.assertEqual(self.gateway.called("find_users")[1][1:], ("asmith", "PROJ", "", True))

    def test_a_project_and_an_issue_together_are_a_usage_error(self):
        self.assertEqual(self.run_cli("users", "resolve", "x", "--project", "PROJ", "--issue", "PROJ-1").code, 2)

    def test_with_users_adds_the_display_name_and_the_address(self):
        data = self.run_cli("users", "resolve", "asmith", "--with-users", "--json").json()
        self.assertEqual((data["displayName"], data["email"], data["name"]), ("Alex Smith", "alex.smith@example.com", "asmith"))
        text = self.run_cli("users", "resolve", "asmith", "--with-users").out
        self.assertIn("Display name: Alex Smith", text)
        self.assertEqual(self.run_cli("users", "resolve", "Jane", "--with-users", "--json").json()["displayName"], None)

    def test_the_privacy_policy_still_resolves_but_refuses_with_users(self):
        result = self.run_cli("users", "resolve", "asmith", "--json", settings=self.hidden())
        self.assertEqual(result.json(), {"query": "asmith", "matches": 1, "name": "asmith", "active": True})
        for personal in ("Alex", "Smith", "alex.smith", "example.com"):
            self.assertNotIn(personal, result.out)
        refused = self.run_cli("users", "resolve", "asmith", "--with-users", "--json", settings=self.hidden())
        self.assertEqual((refused.code, refused.error()["code"]), (1, "INVALID"))
        self.assertIn("JIRA_HIDE_USERS=true", refused.error()["message"])

    def test_the_query_may_come_from_a_file_or_standard_input(self):
        self.assertEqual(self.run_cli("users", "resolve", "@query.txt", "--json").json()["name"], "asmith")
        self.assertEqual(self.run_cli("users", "resolve", "-", "--json", stdin="Alex Smith\n").json()["name"], "asmith")

    def test_a_missing_or_empty_query_is_a_usage_error(self):
        self.assertEqual(self.run_cli("users", "resolve").code, 2)
        self.assertEqual(self.run_cli("users", "resolve", "", "--json").code, 2)
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_select_and_format_belong_to_search(self):
        self.assertEqual(self.run_cli("users", "resolve", "asmith", "--select", "name").code, 2)
        self.assertEqual(self.run_cli("users", "resolve", "asmith", "--format", "md").code, 2)

    def test_an_unknown_action_is_a_usage_error(self):
        self.assertEqual(self.run_cli("users", "list", "x").code, 2)
        self.assertEqual(self.run_cli("users").code, 2)

    def test_resolve_is_a_read(self):
        self.run_cli("users", "resolve", "Alex Smith")
        self.assertFalse([call for call in self.gateway.calls if call[0] in ("assign_issue", "update_issue", "create_issue")])


class SearchTest(UsersTestCase):
    def test_it_lists_the_people_and_needs_with_users(self):
        result = self.run_cli("users", "search", "Jane", "--with-users")
        lines = result.out.splitlines()
        self.assertEqual(lines[0].split(), ["NAME", "DISPLAY", "NAME", "EMAIL", "ACTIVE"])
        self.assertEqual([line.split()[0] for line in lines[1:]], ["jdoe", "jroe"])
        self.assertIn("jane.roe@example.com", result.out)

    def test_without_with_users_it_is_a_usage_error_that_points_to_resolve(self):
        result = self.run_cli("users", "search", "Jane")
        self.assertEqual(result.code, 2)
        self.assertIn("--with-users", result.err)
        self.assertIn("jira users resolve", result.err)
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_the_privacy_policy_refuses_it_whatever_the_flags(self):
        for argv in (("users", "search", "Jane"), ("users", "search", "Jane", "--with-users")):
            result = self.run_cli(*argv, settings=self.hidden())
            with self.subTest(argv=argv):
                self.assertEqual(result.code, 1)
                self.assertIn("JIRA_HIDE_USERS=true", result.err)
                self.assertIn("jira users resolve", result.err)
                self.assertEqual(result.out, "")
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_json_and_select_and_format(self):
        data = self.run_cli("users", "search", "smith", "--with-users", "--json").json()
        self.assertEqual(data, {"query": "smith", "count": 1, "users": [{"name": "asmith", "displayName": "Alex Smith", "email": "alex.smith@example.com", "active": True}]})
        picked = self.run_cli("users", "search", "Jane", "--with-users", "--select", "name,active", "--json").json()
        self.assertEqual(picked["users"], [{"name": "jdoe", "active": True}, {"name": "jroe", "active": True}])
        tsv = self.run_cli("users", "search", "Jane", "--with-users", "--format", "tsv").out.splitlines()
        self.assertEqual(tsv[0], "NAME\tDISPLAY NAME\tEMAIL\tACTIVE")
        self.assertEqual(tsv[1], "jdoe\tJane Doe\tjane.doe@example.com\ttrue")

    def test_nobody(self):
        self.assertEqual(self.run_cli("users", "search", "zed", "--with-users").out, "No users.\n")
        self.assertEqual(self.run_cli("users", "search", "zed", "--with-users", "--json").json()["users"], [])

    def test_inactive_users_are_listed_and_marked(self):
        out = self.run_cli("users", "search", "old", "--with-users").out
        self.assertIn("olduser", out)
        self.assertTrue(out.rstrip().endswith("no"))


class AssigneeOptionTest(UsersTestCase):
    def test_update_resolves_a_display_name_and_the_dry_run_shows_only_the_user_name(self):
        result = self.run_cli("update", "PROJ-1", "--assignee", "Alex Smith", "--dry-run", "--json")
        self.assertEqual(result.json()["payload"], {"fields": {"assignee": {"name": "asmith"}}})
        self.assert_no_people(result.out)
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_accepts_me_unassigned_and_automatic(self):
        for word, expected in (("me", "jdoe"), ("unassigned", None), ("automatic", "-1")):
            with self.subTest(word=word):
                payload = self.run_cli("update", "PROJ-1", "--assignee", word, "--dry-run", "--json").json()["payload"]
                self.assertEqual(payload["fields"]["assignee"], {"name": expected})

    def test_update_refuses_an_ambiguous_assignee_with_the_count_and_no_names(self):
        result = self.run_cli("update", "PROJ-1", "--assignee", "Jane", "--dry-run", "--json")
        self.assertEqual(result.code, 1)
        error = result.error()
        self.assertIn("matches 2 users", error["message"])
        self.assertEqual(error["details"], {"query": "Jane", "matches": 2})
        self.assert_no_people(error["hint"])
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_refuses_an_unknown_assignee_with_exit_4(self):
        result = self.run_cli("update", "PROJ-1", "--assignee", "zed")
        self.assertEqual(result.code, 4)
        self.assertIn("jira users resolve", result.err)

    def test_a_live_update_sends_the_user_name(self):
        self.run_cli("update", "PROJ-1", "--assignee", "Alex Smith", "--summary", "New")
        self.assertEqual(self.gateway.called("update_issue")[0][2]["fields"]["assignee"], {"name": "asmith"})

    def test_create_resolves_the_assignee_among_the_users_of_the_project(self):
        result = self.run_cli("create", "--type", "Story", "--summary", "S", "--assignee", "Alex Smith", "--dry-run", "--json")
        self.assertEqual(result.json()["payload"]["fields"]["assignee"], {"name": "asmith"})
        self.assertEqual(self.gateway.called("find_users")[0][1:], ("Alex Smith", "PROJ", "", False))
        self.assertEqual(self.run_cli("create", "--type", "Story", "--summary", "S", "--assignee", "Sam Sample").code, 1)
        self.assertEqual(self.gateway.called("create_issue"), [])


class AssignTest(UsersTestCase):
    def test_the_issue_gets_the_user_and_it_is_read_back(self):
        result = self.run_cli("assign", "PROJ-1", "Alex Smith")
        self.assertEqual(result.out, "Assigned PROJ-1 to asmith\n")
        self.assertEqual(self.gateway.called("assign_issue")[0][1:], ("PROJ-1", "asmith"))
        self.assert_no_people(result.out)

    def test_unassigned_and_automatic(self):
        self.assertEqual(self.run_cli("assign", "PROJ-1", "unassigned").out, "PROJ-1 is now unassigned\n")
        self.assertEqual(self.run_cli("assign", "PROJ-2", "automatic").out, "Assigned PROJ-2 to jdoe (the default assignee)\n")

    def test_the_dry_run_says_what_would_happen_and_sends_nothing(self):
        result = self.run_cli("assign", "PROJ-1", "Alex Smith", "--dry-run", "--if-unchanged-since", "2026-10-10T07:00:00Z")
        self.assertIn("Dry run: PROJ-1 exists and is unchanged since 2026-10-10T07:00:00Z; it would be assigned to asmith; nothing was sent", result.out)
        self.assertIn('"name": "asmith"', result.out)
        self.assertEqual(self.gateway.called("assign_issue"), [])
        self.assertIn("to nobody", self.run_cli("assign", "PROJ-1", "unassigned", "--dry-run").out)
        self.assertIn("to the default assignee", self.run_cli("assign", "PROJ-1", "automatic", "--dry-run").out)

    def test_json_has_the_documented_keys_and_no_person(self):
        data = self.run_cli("assign", "PROJ-1", "Alex Smith", "--json").json()
        self.assertEqual(data, {"key": "PROJ-1", "action": "assigned", "payload": {"name": "asmith"}, "dryRun": False, "assignee": "asmith"})
        dry = self.run_cli("assign", "PROJ-1", "unassigned", "--dry-run", "--json").json()
        self.assertEqual(dry, {"key": "PROJ-1", "action": "unassigned", "payload": {"name": None}, "dryRun": True, "assignee": None})

    def test_a_stale_issue_is_exit_3(self):
        result = self.run_cli("assign", "PROJ-1", "asmith", "--if-unchanged-since", "2026-10-01T00:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.gateway.called("assign_issue"), [])

    def test_nobody_is_exit_4_and_an_ambiguous_name_is_exit_1_with_the_count(self):
        self.assertEqual(self.run_cli("assign", "PROJ-1", "zed").code, 4)
        result = self.run_cli("assign", "PROJ-1", "Jane", "--json")
        self.assertEqual((result.code, result.error()["details"]), (1, {"query": "Jane", "matches": 2}))
        self.assertEqual(self.run_cli("assign", "PROJ-999", "asmith").code, 4)

    def test_a_missing_permission_is_exit_5_and_the_hint_names_it(self):
        self.gateway.errors["assign_issue"] = AuthError("Jira did not accept the token (HTTP 401).", details={"status": 401})
        result = self.run_cli("assign", "PROJ-1", "asmith", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("Assign Issues", result.error()["hint"])

    def test_the_arguments_are_required(self):
        self.assertEqual(self.run_cli("assign", "PROJ-1").code, 2)
        self.assertEqual(self.run_cli("assign").code, 2)

    def test_the_privacy_policy_changes_nothing_about_assigning(self):
        result = self.run_cli("assign", "PROJ-1", "Alex Smith", "--json", settings=self.hidden())
        self.assertEqual(result.json()["assignee"], "asmith")
        self.assert_no_people(result.out)

    def test_the_help_and_the_catalog(self):
        self.assertIn("Usage: jira assign ISSUE_KEY ASSIGNEE", run_cli("help", "assign").out)
        self.assertIn("Usage: jira users ACTION NAME ...", run_cli("help", "users").out)


if __name__ == "__main__":
    unittest.main()
