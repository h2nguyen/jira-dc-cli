"""`jira api`: JSON dry run and `--param`; `--json-envelope`: one JSON object on stdout whatever happens."""
import json
import unittest

from jira_dc_cli.application.settings import Settings
from jira_dc_cli.adapters.driving.cli.spec import Arg, Command, Example
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway

BODY = '{"fields": {"summary": "New"}}'


class ApiDryRunTest(unittest.TestCase):
    def dry_run(self, *extra, gateway=None):
        return run_cli("api", "PUT", "issue/PROJ-1", "--data", BODY, "--write", "--dry-run", *extra, gateway=gateway or FakeGateway())

    def test_the_text_dry_run_is_unchanged(self):
        result = self.dry_run()
        self.assertEqual(result.code, 0)
        self.assertTrue(result.out.startswith("Dry run: PUT /issue/PROJ-1; nothing was sent\n"))
        self.assertIn('"summary": "New"', result.out)

    def test_the_json_dry_run_is_one_json_object_and_nothing_else(self):
        gateway = FakeGateway()
        result = self.dry_run("--json", gateway=gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json(), {"dryRun": True, "method": "PUT", "path": "/issue/PROJ-1", "query": {}, "body": {"fields": {"summary": "New"}}})
        self.assertEqual(len(result.out.splitlines()), 1)
        self.assertEqual(gateway.called("request"), [])

    def test_the_query_of_the_path_is_in_the_json_dry_run(self):
        result = run_cli("api", "GET", "issue/PROJ-1?fields=summary,status&expand=changelog", "--dry-run", "--json")
        self.assertEqual(result.json()["query"], {"fields": "summary,status", "expand": "changelog"})
        self.assertEqual(result.json()["path"], "/issue/PROJ-1")
        self.assertIsNone(result.json()["body"])

    def test_json_output_of_the_environment_works_as_well(self):
        result = self.dry_run(gateway=FakeGateway())
        self.assertFalse(result.out.startswith("{"))
        piped = run_cli("api", "PUT", "issue/PROJ-1", "--data", BODY, "--write", "--dry-run", environ={"JIRA_OUTPUT": "json"})
        self.assertEqual(piped.json()["dryRun"], True)


class ApiParamTest(unittest.TestCase):
    def test_param_adds_encoded_query_parameters_without_an_ampersand_in_the_path(self):
        gateway = FakeGateway()
        result = run_cli("api", "GET", "rest/agile/1.0/board", "--param", "projectKeyOrId=PROJ", "--param", "name=Demo board", gateway=gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(gateway.called("request"), [("request", "GET", "/rest/agile/1.0/board?projectKeyOrId=PROJ&name=Demo+board", None)])

    def test_param_is_added_to_the_query_that_the_path_has(self):
        gateway = FakeGateway()
        run_cli("api", "GET", "search?jql=project=PROJ", "--param", "maxResults=1", gateway=gateway)
        self.assertEqual(gateway.called("request")[0][2], "/search?jql=project%3DPROJ&maxResults=1")

    def test_a_name_that_repeats_is_a_list_in_the_dry_run(self):
        result = run_cli("api", "GET", "search", "--param", "fields=a", "--param", "fields=b", "--dry-run", "--json")
        self.assertEqual(result.json()["query"], {"fields": ["a", "b"]})

    def test_a_value_with_special_characters_survives(self):
        result = run_cli("api", "GET", "search", "--param", "jql=a & b = \"c\"", "--dry-run", "--json")
        self.assertEqual(result.json()["query"], {"jql": 'a & b = "c"'})

    def test_param_needs_a_name_and_a_value(self):
        for bad in ("nokey", "=value"):
            with self.subTest(bad=bad):
                result = run_cli("api", "GET", "search", "--param", bad)
                self.assertEqual(result.code, 2)
                self.assertIn("KEY=VALUE", result.err)

    def test_a_response_with_json_is_compact_when_piped(self):
        gateway = FakeGateway()
        gateway.responses[("GET", "/myself")] = {"name": "x", "n": [1, 2]}
        result = run_cli("api", "GET", "myself", "--json", gateway=gateway)
        self.assertEqual(result.out, '{"name":"x","n":[1,2]}\n')


class EnvelopeTest(unittest.TestCase):
    def test_a_result_is_ok_and_data(self):
        result = run_cli("issue", "PROJ-1", "--json-envelope")
        self.assertEqual(result.code, 0, result.err)
        message = result.json()
        self.assertEqual((message["ok"], message["data"]["key"]), (True, "PROJ-1"))
        self.assertEqual(len(result.out.splitlines()), 1)
        self.assertEqual(result.err, "")

    def test_the_environment_switches_it_on(self):
        result = run_cli("issue", "PROJ-1", environ={"JIRA_JSON_ENVELOPE": "1"})
        self.assertTrue(result.json()["ok"])

    def test_an_error_is_ok_false_on_stdout_and_stderr_and_the_exit_code_stay(self):
        result = run_cli("issue", "PROJ-999", "--json-envelope")
        self.assertEqual(result.code, 4)
        message = result.json()
        self.assertEqual(message["ok"], False)
        self.assertEqual((message["error"]["code"], message["error"]["exitCode"]), ("NOT_FOUND", 4))
        self.assertTrue(result.err.startswith("ERROR: Not found"))
        self.assertNotIn("data", message)

    def test_the_error_on_stderr_is_json_only_with_json(self):
        result = run_cli("issue", "PROJ-999", "--json-envelope", "--json")
        self.assertEqual(result.error()["code"], "NOT_FOUND")
        self.assertEqual(result.json()["error"]["code"], "NOT_FOUND")

    def test_a_usage_error_is_enveloped_too(self):
        result = run_cli("issue", "PROJ-1", "--nope", "--json-envelope")
        self.assertEqual(result.code, 2)
        self.assertEqual((result.json()["ok"], result.json()["error"]["code"]), (False, "USAGE"))

    def test_a_command_without_json_has_its_text_as_data(self):
        result = run_cli("wiki", "-", "--json-envelope", stdin="# Title\n")
        self.assertEqual(result.json(), {"ok": True, "data": {"text": "h1. Title"}})

    def test_a_non_zero_exit_code_without_an_error_is_ok_false_with_the_data(self):
        result = run_cli("current", "--json-envelope", settings=Settings(base_url="https://jira.example.com", pat="secret-token"))
        self.assertEqual(result.code, 1)
        message = result.json()
        self.assertEqual((message["ok"], message["data"], message["error"]["exitCode"]), (False, {"project": None}, 1))

    def test_help_is_not_wrapped(self):
        result = run_cli("issue", "--help", "--json-envelope")
        self.assertTrue(result.out.startswith("jira issue - "))

    def test_a_bug_is_enveloped_as_well(self):
        gateway = FakeGateway()
        gateway.errors["get_issue"] = RuntimeError("boom")
        result = run_cli("issue", "PROJ-1", "--json-envelope", gateway=gateway)
        self.assertEqual(result.code, 1)
        self.assertEqual(result.json()["error"]["message"], "unexpected RuntimeError: boom")

    def test_without_the_switch_nothing_changes(self):
        result = run_cli("issue", "PROJ-1", "--json")
        self.assertNotIn("ok", result.json())
        self.assertEqual(json.loads(result.out)["key"], "PROJ-1")

    def test_the_switch_is_in_the_help_of_every_command(self):
        self.assertIn("--json-envelope", run_cli("help", "issue").out)
        self.assertIn("--json-envelope", run_cli("help", "wiki").out)

    def test_a_command_that_streams_is_never_wrapped_not_even_when_it_fails(self):
        from unittest import mock

        from jira_dc_cli.domain.errors import ConfigError

        def handler(ctx, args):
            ctx.print("a protocol line")
            if args.fail:
                raise ConfigError("the server cannot go on")
            return 0

        command = Command(
            name="stream-demo", group="Tools", summary="Talk on the streams", handler=handler, streams=True, needs_app=False,
            args=(Arg(("--fail",), "Fail after the first line.", kind="flag"),), examples=(Example("jira stream-demo"),),
        )
        with mock.patch("jira_dc_cli.adapters.driving.cli.main.find_command", lambda name: command if name == "stream-demo" else None):
            for env in ({"JIRA_JSON_ENVELOPE": "1"}, {}):
                ok = run_cli("stream-demo", "--json-envelope", environ=env)
                self.assertEqual((ok.code, ok.out), (0, "a protocol line\n"))
                failed = run_cli("stream-demo", "--fail", "--json-envelope", environ=env)
                self.assertEqual(failed.code, 1)
                self.assertEqual(failed.out, "a protocol line\n")  # no envelope appended to the protocol
                self.assertIn("the server cannot go on", failed.err)


if __name__ == "__main__":
    unittest.main()
