"""Use cases of users with a fake Jira: resolve a name, list people, assign an issue, and `--assignee` in `update` and `create`."""
import dataclasses
import unittest

from jira_dc_cli.domain.drafts import IssueChanges, IssueDraft
from jira_dc_cli.domain.errors import AuthError, ConflictError, NotFoundError, UsageError, ValidationError
from jira_dc_cli.domain.users import Person
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, build_app


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class ResolveUserTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def resolve(self, query, **options):
        return self.app.users.resolve.execute(query, **options)

    def test_a_unique_match_gives_the_user_name_and_whether_it_is_active(self):
        found = self.resolve("Jane Doe")
        self.assertEqual((found.query, found.matches, found.person.name, found.person.active), ("Jane Doe", 1, "jdoe", True))

    def test_the_display_name_and_the_address_stay_out_unless_people_may_be_shown(self):
        person = self.resolve("jdoe").person
        self.assertEqual((person.display_name, person.email), ("", ""))
        shown = self.resolve("jdoe", with_users=True).person
        self.assertEqual((shown.display_name, shown.email), ("Jane Doe", "jane.doe@example.com"))

    def test_an_ambiguous_name_is_counted_and_not_listed(self):
        found = self.resolve("Jane")
        self.assertEqual((found.matches, found.person), (2, None))
        found = self.resolve("Sam Sample")  # one display name, two users
        self.assertEqual((found.matches, found.person), (2, None))

    def test_nobody(self):
        self.assertEqual((self.resolve("zed").matches, self.resolve("zed").person), (0, None))

    def test_a_user_who_is_created_while_the_program_runs_is_found_the_next_time_the_name_is_asked(self):
        self.assertEqual(self.resolve("newbie").matches, 0)
        self.assertEqual(len(self.gateway.called("find_users")), 1)  # asked now: nothing to look at again
        self.gateway.directory.append(Person("newbie", "New Bie", "new.bie@example.com"))  # `jira mcp` runs for hours
        found = self.resolve("newbie")
        self.assertEqual((found.matches, found.person.name), (1, "newbie"))
        self.assertEqual(len(self.gateway.called("find_users")), 2)  # the earlier answer was "nobody": Jira is asked again once
        self.resolve("newbie")
        self.assertEqual(len(self.gateway.called("find_users")), 2)  # a name that was found stays found without a request

    def test_a_name_that_still_fits_nobody_or_several_costs_one_more_request_each_time_and_stays_refused(self):
        for query, matches in (("zed", 0), ("Jane", 2)):
            with self.subTest(query=query):
                before = len(self.gateway.called("find_users"))
                self.assertEqual(self.resolve(query).matches, matches)
                self.assertEqual(self.resolve(query).matches, matches)
                self.assertEqual(len(self.gateway.called("find_users")) - before, 2)

    def test_an_inactive_user_is_found_and_says_so(self):
        found = self.resolve("olduser")
        self.assertEqual((found.person.name, found.person.active), ("olduser", False))
        self.assertEqual(self.gateway.called("find_users")[0][1:], ("olduser", "", "", True))

    def test_with_a_scope_only_the_users_who_can_be_assigned_count(self):
        self.assertEqual(self.resolve("olduser", issue="proj-1").matches, 0)
        self.assertEqual(self.gateway.called("find_users")[0][1:], ("olduser", "", "PROJ-1", True))
        self.gateway.unassignable.add("asmith")
        self.assertEqual(self.resolve("asmith", project="proj").matches, 0)
        self.assertEqual(self.gateway.called("find_users")[1][1:], ("asmith", "PROJ", "", True))

    def test_me_is_the_token_user_without_a_search(self):
        found = self.resolve("ME")
        self.assertEqual((found.matches, found.person.name), (1, "jdoe"))
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_the_privacy_policy_allows_resolve_but_not_with_users(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        self.assertEqual(app.users.resolve.execute("Jane Doe").person.name, "jdoe")
        with self.assertRaises(ValidationError) as caught:
            app.users.resolve.execute("Jane Doe", with_users=True)
        self.assertIn("JIRA_HIDE_USERS=true", caught.exception.message)

    def test_a_project_and_an_issue_together_are_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.resolve("jdoe", project="PROJ", issue="PROJ-1")

    def test_a_bad_key_is_refused_before_jira_is_asked(self):
        with self.assertRaises(ValidationError):
            self.resolve("jdoe", issue="nonsense")
        with self.assertRaises(ValidationError):
            self.resolve("jdoe", project="not a key")
        self.assertEqual(self.gateway.calls, [])

    def test_an_empty_query_is_a_usage_error(self):
        for text in ("", "  "):
            with self.subTest(text=text), self.assertRaises(UsageError):
                self.resolve(text)
        self.assertEqual(self.gateway.calls, [])

    def test_a_query_that_is_too_long_to_be_a_name_is_refused_before_jira_is_asked(self):
        with self.assertRaises(ValidationError) as caught:
            self.resolve("x" * 300)
        self.assertIn("at most 256", caught.exception.hint.lower())
        with self.assertRaises(ValidationError):
            self.app.update_issue.execute("PROJ-1", IssueChanges(assignee="y" * 300), dry_run=True)
        self.assertEqual(self.gateway.called("find_users"), [])


class SearchUsersTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_it_lists_the_people_sorted_by_user_name(self):
        people = self.app.users.search.execute("Jane", with_users=True)
        self.assertEqual([(person.name, person.display_name) for person in people], [("jdoe", "Jane Doe"), ("jroe", "Jane Roe")])

    def test_it_includes_the_inactive_unless_there_is_a_scope(self):
        self.assertIn("olduser", [person.name for person in self.app.users.search.execute("user", with_users=True)])
        self.assertNotIn("olduser", [person.name for person in self.app.users.search.execute("user", issue="PROJ-1", with_users=True)])

    def test_it_needs_with_users(self):
        with self.assertRaises(UsageError) as caught:
            self.app.users.search.execute("Jane")
        self.assertIn("--with-users", caught.exception.hint)
        self.assertIn("jira users resolve", caught.exception.hint)
        self.assertEqual(self.gateway.calls, [])

    def test_it_is_refused_under_the_privacy_policy_with_or_without_the_flag(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        for with_users in (False, True):
            with self.subTest(with_users=with_users), self.assertRaises(ValidationError) as caught:
                app.users.search.execute("Jane", with_users=with_users)
            self.assertIn("JIRA_HIDE_USERS=true", caught.exception.message)
            self.assertIn("jira users resolve", caught.exception.hint)
        self.assertEqual(self.gateway.calls, [])


class AssigneeValueTest(unittest.TestCase):
    """What `update --assignee`, `create --assignee` and `assign` send for what a person typed."""

    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def update_payload(self, text):
        return self.app.update_issue.execute("PROJ-1", IssueChanges(assignee=text), dry_run=True).payload["fields"]["assignee"]

    def test_a_user_name_a_display_name_and_an_address(self):
        for text in ("asmith", "Alex Smith", "alex.smith@example.com", "smith"):
            with self.subTest(text=text):
                self.assertEqual(self.update_payload(text), {"name": "asmith"})

    def test_me_is_the_token_user(self):
        self.assertEqual(self.update_payload("me"), {"name": "jdoe"})

    def test_unassigned_and_automatic(self):
        self.assertEqual(self.update_payload("unassigned"), {"name": None})
        self.assertEqual(self.update_payload("Automatic"), {"name": "-1"})
        self.assertEqual(self.update_payload("-1"), {"name": "-1"})
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_the_search_is_for_the_users_who_can_be_assigned_that_issue(self):
        self.update_payload("asmith")
        self.assertEqual(self.gateway.called("find_users")[0][1:], ("asmith", "", "PROJ-1", False))

    def test_an_ambiguous_name_is_refused_with_the_count_and_no_names(self):
        with self.assertRaises(ValidationError) as caught:
            self.update_payload("Jane")
        error = caught.exception
        self.assertIn("matches 2 users", error.message)
        self.assertEqual(error.details, {"query": "Jane", "matches": 2})
        for text in (error.message, error.hint, str(error.details)):
            for personal in ("jdoe", "jroe", "Doe", "Roe", "example.com"):
                self.assertNotIn(personal, text)

    def test_nobody_is_refused_and_says_where_to_look(self):
        with self.assertRaises(NotFoundError) as caught:
            self.update_payload("zed")
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertIn("PROJ-1", caught.exception.message)
        self.assertIn("jira users resolve", caught.exception.hint)

    def test_an_inactive_user_cannot_be_assigned(self):
        with self.assertRaises(NotFoundError):
            self.update_payload("olduser")

    def test_an_empty_assignee_says_what_to_say_instead(self):
        with self.assertRaises(ValidationError) as caught:
            self.update_payload("")
        self.assertIn("unassigned", caught.exception.hint)

    def test_the_same_name_twice_in_a_run_asks_jira_once(self):
        self.app.update_issue.execute("PROJ-1", IssueChanges(assignee="asmith"), dry_run=True)
        self.app.update_issue.execute("PROJ-1", IssueChanges(assignee="asmith"), dry_run=True)
        self.assertEqual(len(self.gateway.called("find_users")), 1)

    def test_an_assignee_who_does_not_exist_yet_is_found_when_he_does_and_the_name_is_asked_again(self):
        with self.assertRaises(NotFoundError):
            self.update_payload("newbie")
        self.gateway.directory.append(Person("newbie", "New Bie", "new.bie@example.com"))
        self.assertEqual(self.update_payload("newbie"), {"name": "newbie"})
        self.assertEqual(len(self.gateway.called("find_users")), 2)

    def test_a_live_update_sends_the_resolved_user_name(self):
        self.app.update_issue.execute("PROJ-1", IssueChanges(assignee="Alex Smith", summary="New"))
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"summary": "New", "assignee": {"name": "asmith"}}})

    def test_the_issue_is_checked_before_the_assignee_is_looked_up(self):
        with self.assertRaises(NotFoundError) as caught:
            self.app.update_issue.execute("PROJ-999", IssueChanges(assignee="zed"), dry_run=True)
        self.assertIn("PROJ-999", caught.exception.message)
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_update_json_sends_its_assignee_as_it_is(self):
        payload = {"fields": {"assignee": {"name": "not-looked-up"}}}
        self.app.update_issue_from_payload.execute("PROJ-1", payload)
        self.assertEqual(self.gateway.called("find_users"), [])


class CreateAssigneeTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def draft(self, assignee):
        return IssueDraft("PROJ", "Story", "New story", assignee=assignee)

    def test_the_search_is_for_the_users_who_can_be_assigned_in_the_project(self):
        result = self.app.create_issue.execute(self.draft("Alex Smith"), dry_run=True)
        self.assertEqual(result.payload["fields"]["assignee"], {"name": "asmith"})
        self.assertEqual(self.gateway.called("find_users")[0][1:], ("Alex Smith", "PROJ", "", False))

    def test_the_words_work_for_a_new_issue_too(self):
        self.assertEqual(self.app.create_issue.execute(self.draft("me"), dry_run=True).payload["fields"]["assignee"], {"name": "jdoe"})
        self.assertEqual(self.app.create_issue.execute(self.draft("unassigned"), dry_run=True).payload["fields"]["assignee"], {"name": None})
        self.assertEqual(self.app.create_issue.execute(self.draft("automatic"), dry_run=True).payload["fields"]["assignee"], {"name": "-1"})

    def test_without_an_assignee_the_payload_has_none_and_no_user_is_looked_up(self):
        result = self.app.create_issue.execute(self.draft(""), dry_run=True)
        self.assertNotIn("assignee", result.payload["fields"])
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_a_live_create_sends_the_resolved_name(self):
        self.app.create_issue.execute(self.draft("Alex Smith"))
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["assignee"], {"name": "asmith"})

    def test_an_ambiguous_assignee_creates_nothing(self):
        with self.assertRaises(ValidationError):
            self.app.create_issue.execute(self.draft("Sam Sample"))
        self.assertEqual(self.gateway.called("create_issue"), [])


class AssignIssueTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_the_issue_gets_the_user_and_the_assignee_is_read_back(self):
        result = self.app.users.assign.execute("proj-1", "Alex Smith")
        self.assertEqual((result.key, result.action, result.payload, result.dry_run, result.assignee), ("PROJ-1", "assigned", {"name": "asmith"}, False, "asmith"))
        self.assertEqual(self.gateway.called("assign_issue")[0][1:], ("PROJ-1", "asmith"))
        self.assertEqual(self.gateway.issues["PROJ-1"].assignee.name, "asmith")

    def test_unassigned(self):
        result = self.app.users.assign.execute("PROJ-1", "unassigned")
        self.assertEqual((result.action, result.payload, result.assignee), ("unassigned", {"name": None}, None))
        self.assertIsNone(self.gateway.issues["PROJ-1"].assignee)

    def test_automatic_gives_the_default_assignee_and_says_who_it_is(self):
        result = self.app.users.assign.execute("PROJ-1", "automatic")
        self.assertEqual((result.action, result.payload, result.assignee), ("automatic", {"name": "-1"}, "jdoe"))

    def test_me(self):
        self.assertEqual(self.app.users.assign.execute("PROJ-1", "me").assignee, "jdoe")

    def test_a_dry_run_checks_the_issue_resolves_the_user_and_sends_nothing(self):
        result = self.app.users.assign.execute("PROJ-1", "Alex Smith", dry_run=True)
        self.assertEqual((result.dry_run, result.payload, result.assignee), (True, {"name": "asmith"}, "asmith"))
        self.assertEqual(self.gateway.called("assign_issue"), [])
        self.assertEqual(self.gateway.called("get_issue")[0][1], "PROJ-1")
        self.assertEqual(self.gateway.issues["PROJ-1"].assignee.name, "jdoe")

    def test_a_dry_run_of_unassigned_and_automatic_names_nobody(self):
        self.assertIsNone(self.app.users.assign.execute("PROJ-1", "unassigned", dry_run=True).assignee)
        self.assertIsNone(self.app.users.assign.execute("PROJ-1", "automatic", dry_run=True).assignee)

    def test_the_stale_guard_blocks_the_write(self):
        with self.assertRaises(ConflictError):
            self.app.users.assign.execute("PROJ-1", "asmith", if_unchanged_since="2026-10-01T00:00:00Z")
        self.assertEqual(self.gateway.called("assign_issue"), [])

    def test_the_stale_guard_passes_when_nothing_changed_since(self):
        self.app.users.assign.execute("PROJ-1", "asmith", if_unchanged_since="2026-10-10T07:00:00Z")
        self.assertEqual(len(self.gateway.called("assign_issue")), 1)

    def test_an_unknown_issue_is_not_found_before_anything_else(self):
        with self.assertRaises(NotFoundError):
            self.app.users.assign.execute("PROJ-999", "asmith", dry_run=True)
        self.assertEqual(self.gateway.called("find_users"), [])

    def test_nobody_and_an_ambiguous_name_are_refused_and_nothing_is_assigned(self):
        with self.assertRaises(NotFoundError):
            self.app.users.assign.execute("PROJ-1", "zed")
        with self.assertRaises(ValidationError):
            self.app.users.assign.execute("PROJ-1", "Jane")
        self.assertEqual(self.gateway.called("assign_issue"), [])

    def test_no_assignee_is_refused(self):
        with self.assertRaises(ValidationError):
            self.app.users.assign.execute("PROJ-1", "  ")

    def test_jira_answers_401_for_a_missing_permission_and_the_error_says_so(self):
        self.gateway.errors["assign_issue"] = AuthError("Jira did not accept the token (HTTP 401).", hint="Create a new token.", details={"status": 401, "request": "PUT /issue/PROJ-1/assignee"})
        with self.assertRaises(AuthError) as caught:
            self.app.users.assign.execute("PROJ-1", "asmith")
        error = caught.exception
        self.assertEqual(error.exit_code, 5)
        self.assertIn("HTTP 401", error.message)
        self.assertIn("Assign Issues", error.hint)
        self.assertIn("jira ping", error.hint)
        self.assertEqual(error.details["status"], 401)

    def test_a_403_gets_the_same_explanation(self):
        self.gateway.errors["assign_issue"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.app.users.assign.execute("PROJ-1", "asmith")
        self.assertIn("Assign Issues", caught.exception.hint)

    def test_an_error_that_is_neither_is_not_rewritten(self):
        self.gateway.errors["assign_issue"] = AuthError("odd", hint="odd hint", details={"status": 407})
        with self.assertRaises(AuthError) as caught:
            self.app.users.assign.execute("PROJ-1", "asmith")
        self.assertEqual(caught.exception.hint, "odd hint")


if __name__ == "__main__":
    unittest.main()
