"""Hints after a usage error, `metadata --project --type`, the hint of a refused write, and a search that is too long for a URL."""
import unittest

from jira_dc_cli.application.settings import Settings
from tests.integration.test_end_to_end import EndToEnd
from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway


class UsageHintTest(unittest.TestCase):
    def test_an_option_that_another_command_has_is_named(self):
        result = run_cli("issues", "--type", "Story")
        self.assertEqual(result.code, 2)
        self.assertIn("`--type` is not an option of `jira issues`", result.err)
        self.assertIn("`jira search`", result.err)

    def test_the_error_names_the_commands_in_json_too(self):
        hint = run_cli("issues", "--type", "Story", "--json").error()["hint"]
        self.assertIn("jira search", hint)
        self.assertIn("jira help issues", hint)

    def test_the_text_of_a_query_that_lost_its_quotes_gets_the_powershell_hint(self):
        # Windows PowerShell 5.1 turns  project = X AND "Epic Link" = Y  into two arguments
        result = run_cli("issues", "--jql", "project = X AND Epic", "Link", "=", "Y", "ORDER", "BY", "key", "--json")
        self.assertEqual(result.code, 2)
        hint = result.error()["hint"]
        self.assertIn("Windows PowerShell 5.1 removed them", hint)
        self.assertIn("--jql '@query.jql'", hint)
        self.assertIn('\\"', hint)

    def test_no_such_hint_when_the_cause_is_an_unknown_flag(self):
        self.assertNotIn("PowerShell", run_cli("issues", "--nope", "x", "--json").error()["hint"])

    def test_no_such_hint_for_a_command_that_takes_no_query(self):
        hint = run_cli("issue", "PROJ-1", "--comments", "PROJ-2", "--json").error()["hint"]
        self.assertNotIn("PowerShell", hint)
        self.assertEqual(hint, "Run `jira help issue` for the usage and examples.")

    def test_a_command_without_a_query_gets_only_the_usual_hint(self):
        hint = run_cli("branch", "PROJ-1", "extra", "--json").error()["hint"]
        self.assertEqual(hint, "Run `jira help branch` for the usage and examples.")

    def test_other_usage_errors_keep_their_hint(self):
        self.assertEqual(run_cli("issue", "--json").error()["hint"], "Run `jira help issue` for the usage and examples.")


class HintBuilderTest(unittest.TestCase):
    def test_no_payload_can_make_the_hint_fail(self):
        from jira_dc_cli.adapters.driven.jira_rest import _rejected_hint

        for request in (None, [], "text", {"fields": "x"}, {"fields": {"project": "PROJ", "issuetype": ["Bug"]}}, {"fields": {"project": 7, "issuetype": {"id": None}}}):
            for response in (None, [], "x", {"errors": ["a"]}, {"errors": {"summary": "bad"}}, {"errors": None}):
                for path in ("/issue", "/issue/PROJ-1", "/search", "/rest/agile/1.0/sprint/1"):
                    with self.subTest(request=request, response=response, path=path):
                        self.assertTrue(_rejected_hint("POST", path, request, response))


class MetadataOptionsTest(unittest.TestCase):
    def test_the_options_are_the_same_as_the_positional_form(self):
        positional, optioned = FakeGateway(), FakeGateway()
        first = run_cli("metadata", "PROJ", "10200", gateway=positional)
        second = run_cli("metadata", "--project", "PROJ", "--type", "Story", gateway=optioned)
        self.assertEqual((first.code, second.code), (0, 0), second.err)
        self.assertEqual(first.out, second.out)
        self.assertEqual(positional.called("create_metadata"), [("create_metadata", "PROJ", "10200")])
        self.assertEqual(optioned.called("create_metadata"), [("create_metadata", "PROJ", "10200")])

    def test_a_type_can_be_given_by_id_and_the_name_is_not_case_sensitive(self):
        gateway = FakeGateway()
        run_cli("metadata", "--project", "proj", "--type", "bug", gateway=gateway)
        self.assertEqual(gateway.called("create_metadata"), [("create_metadata", "PROJ", "10201")])

    def test_without_a_project_the_current_one_is_used(self):
        gateway = FakeGateway()
        self.assertEqual(run_cli("metadata", gateway=gateway).code, 0)
        self.assertEqual(gateway.called("create_metadata"), [("create_metadata", "PROJ", "")])

    def test_no_project_and_none_selected(self):
        result = run_cli("metadata", settings=Settings(base_url="https://jira.example.com", pat="secret-token"))
        self.assertEqual(result.code, 2)
        self.assertIn("--project KEY", result.err)

    def test_two_different_projects_are_refused(self):
        result = run_cli("metadata", "PROJ", "--project", "DEMO")
        self.assertEqual(result.code, 2)
        self.assertIn("Two projects", result.err)

    def test_an_id_and_a_type_name_together_are_refused(self):
        self.assertEqual(run_cli("metadata", "PROJ", "10200", "--type", "Story").code, 2)

    def test_an_unknown_type_lists_what_the_project_offers(self):
        result = run_cli("metadata", "--project", "PROJ", "--type", "Nope")
        self.assertEqual(result.code, 1)
        self.assertIn("Available: Bug (10201), Epic (10000), Story (10200)", result.err)


class RefusedWriteHintTest(EndToEnd):
    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])

    def test_a_payload_with_a_text_where_an_object_belongs_does_not_hide_the_refusal(self):
        payload = self.folder / "payload.json"
        payload.write_text('{"fields": {"project": "PROJ", "issuetype": "Bug", "summary": "X"}}', encoding="utf-8")
        self.jira.fail_next("/rest/api/2/issue", 400, method="POST", body={"errorMessages": [], "errors": {"issuetype": "issue type is required"}})
        result = self.cli("create-json", str(payload), "--json")
        self.assertEqual(result.code, 6, result.err)
        self.assertEqual(result.error()["code"], "REJECTED")
        self.assertIn("Jira names issuetype", result.error()["hint"])
        self.assertIn("`jira metadata PROJECT ISSUE_TYPE_ID`", result.error()["hint"])

    def test_a_refused_create_names_the_field_and_the_exact_metadata_command(self):
        self.jira.fail_next(
            "/rest/api/2/issue", 400, method="POST",
            body={"errorMessages": [], "errors": {"priority": "This priority is not available for this project"}},
        )
        result = self.cli("create", "--type", "Story", "--summary", "X", "--priority", "Nope", "--json")
        self.assertEqual(result.code, 6)
        error = result.error()
        self.assertEqual(error["code"], "REJECTED")
        self.assertIn("Jira names priority", error["hint"])
        self.assertIn("`jira metadata PROJ 10200`", error["hint"])

    def test_a_refused_update_points_to_the_project(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1", 400, method="PUT", body={"errorMessages": [], "errors": {"components": "Component name 'X' is not valid"}})
        result = self.cli("update", "PROJ-1", "--components", "X", "--json")
        self.assertEqual(result.code, 6)
        self.assertIn("`jira metadata PROJ`", result.error()["hint"])
        self.assertIn("components", result.error()["hint"])

    def test_the_hint_is_never_empty_whatever_was_refused(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/comment", 400, method="POST", body={"errorMessages": ["Comment body cannot be empty."], "errors": {}})
        result = self.cli("comment", "PROJ-1", "--text", "x", "--json")
        self.assertEqual(result.code, 6)
        self.assertTrue(result.error()["hint"])
        broken = self.cli("issues", "--jql", "BROKEN", "--json")
        self.assertEqual(broken.code, 6)
        self.assertIn("--jql '@query.jql'", broken.error()["hint"])


class LongSearchTest(EndToEnd):
    def test_a_query_too_long_for_a_url_goes_in_the_body_of_a_post(self):
        keys = ",".join(f"PROJ-{number}" for number in range(1, 900))
        result = self.cli("issues", "--jql", f"key in ({keys})", "--all", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual((result.json()["total"], result.json()["count"]), (6, 6))
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/search"), [])
        posts = self.jira.sent("POST", "/rest/api/2/search")
        self.assertGreaterEqual(len(posts), 1)
        self.assertIn("PROJ-899", posts[0].body["jql"])
        self.assertIn("summary", posts[0].body["fields"])
        self.assertEqual(self.jira.unknown_endpoints, [])

    def test_a_short_query_is_still_a_get(self):
        self.cli("issues", "--jql", "project = PROJ", "--all", "--json")
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/search"), [])
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/search")), 3)

    def test_the_post_that_only_reads_is_retried_like_a_get(self):
        keys = ",".join(f"PROJ-{number}" for number in range(1, 900))
        self.jira.fail_next("/rest/api/2/search", 503, method="POST", times=1, headers={"Retry-After": "0"})
        result = self.cli("issues", "--jql", f"key in ({keys})", "--all", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json()["count"], 6)
        self.assertGreaterEqual(len(self.jira.sent("POST", "/rest/api/2/search")), 3)  # the refused one, the retry, and the next pages

    def test_a_write_is_never_retried(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/comment", 503, method="POST", times=1, headers={"Retry-After": "0"})
        result = self.cli("comment", "PROJ-1", "--text", "x")
        self.assertEqual(result.code, 7)
        self.assertEqual(len(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/comment")), 1)


if __name__ == "__main__":
    unittest.main()
