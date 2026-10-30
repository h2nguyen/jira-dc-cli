"""The command line framework: dispatch, help, the command catalog, error handling."""
import json
import shlex
import unittest

from jira_dc_cli.adapters.driving.cli.parser import build_parser
from jira_dc_cli.adapters.driving.cli.registry import all_commands, find_command
from jira_dc_cli.adapters.driving.cli.spec import GROUP_ORDER
from jira_dc_cli.domain.errors import EXIT_CODES, ConfigError, UnavailableError
from jira_dc_cli.version import __version__
from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway

NAMES = [command.name for command in all_commands()]


class DispatchTest(unittest.TestCase):
    def test_no_arguments_show_the_overview(self):
        result = run_cli()
        self.assertEqual(result.code, 0)
        self.assertIn(f"jira-dc-cli {__version__}", result.out)
        for name in NAMES:
            self.assertIn(name, result.out)
        self.assertEqual(result.err, "")

    def test_help_flags_and_help_command_agree(self):
        overview = run_cli().out
        for argv in (("-h",), ("--help",), ("help",)):
            self.assertEqual(run_cli(*argv).out, overview)

    def test_version_in_every_spelling(self):
        for argv in (("version",), ("-V",), ("--version",)):
            self.assertEqual(run_cli(*argv).out, f"jira-dc-cli {__version__}\n")
        data = run_cli("version", "--json").json()
        self.assertEqual((data["name"], data["version"]), ("jira-dc-cli", __version__))

    def test_unknown_command_suggests_and_exits_2(self):
        result = run_cli("isues")
        self.assertEqual(result.code, 2)
        self.assertIn("Did you mean: issues", result.err)
        self.assertEqual(result.out, "")

    def test_errors_are_json_on_stderr_when_asked(self):
        for argv, environ in ((("issue", "--json"), {}), (("issue",), {"JIRA_OUTPUT": "json"})):
            result = run_cli(*argv, environ=environ)
            error = result.error()
            self.assertEqual((result.code, error["code"], error["exitCode"]), (2, "USAGE", 2))
            self.assertIn("jira help issue", error["hint"])
            self.assertEqual(result.out, "")

    def test_help_for_every_command_works_offline_and_changes_nothing(self):
        for name in NAMES:
            for argv in (("help", name), (name, "--help"), (name, "-h")):
                gateway = FakeGateway()
                result = run_cli(*argv, gateway=gateway)
                with self.subTest(argv=argv):
                    self.assertEqual(result.code, 0, result.err)
                    self.assertIn(f"Usage: jira {name}", result.out)
                    self.assertEqual(gateway.calls, [])

    def test_help_wins_over_missing_arguments(self):
        self.assertEqual(run_cli("issue", "--help").code, 0)
        self.assertEqual(run_cli("create", "--help").code, 0)

    def test_help_of_an_unknown_command(self):
        result = run_cli("help", "transitio")
        self.assertEqual(result.code, 2)
        self.assertIn("transition", result.err)

    def test_aliases(self):
        for alias in ("changes", "history"):
            self.assertIn("1.0.0", run_cli(alias).out)
        for alias in ("howto", "readme", "docs"):
            self.assertIn("daily-workflow", run_cli(alias).out)

    def test_unknown_options_and_bad_values_are_usage_errors(self):
        for argv in (("issue", "PROJ-1", "--nope"), ("issues", "--limit", "many"), ("branch", "PROJ-1", "--type", "release")):
            with self.subTest(argv=argv):
                self.assertEqual(run_cli(*argv).code, 2)

    def test_abbreviated_options_are_not_guessed(self):
        self.assertEqual(run_cli("issue", "PROJ-1", "--comm").code, 2)


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = run_cli("commands", "--json").json()

    def test_everything_a_program_needs(self):
        self.assertEqual(self.catalog["version"], __version__)
        self.assertEqual([item["name"] for item in self.catalog["commands"]], NAMES)
        self.assertEqual([item["code"] for item in self.catalog["exitCodes"]], [code for code, _, _ in EXIT_CODES])
        self.assertIn("JIRA_HIDE_USERS", [item["name"] for item in self.catalog["settings"]])
        self.assertNotIn("secret-token", json.dumps(self.catalog))

    def test_commands_describe_their_arguments_and_output(self):
        issue = next(item for item in self.catalog["commands"] if item["name"] == "issue")
        self.assertTrue(issue["supportsJson"])
        self.assertFalse(issue["changesData"])
        self.assertEqual([arg["names"][0] for arg in issue["arguments"]][:4], ["keys", "--comments", "--with-users", "--raw"])
        self.assertEqual({"--only", "--max-chars", "--outline", "--section", "--rendered", "--history"} - {arg["names"][0] for arg in issue["arguments"]}, set())
        self.assertTrue(issue["arguments"][0]["required"])
        self.assertIn("status{name,category}", issue["jsonOutput"])
        transition = next(item for item in self.catalog["commands"] if item["name"] == "transition")
        self.assertTrue(transition["changesData"])

    def test_the_text_catalog(self):
        result = run_cli("commands")
        self.assertTrue(all(name in result.out for name in NAMES))
        self.assertTrue(result.out.startswith("COMMAND"))


class QualityBarTest(unittest.TestCase):
    """Rules every command must follow, so that a new one cannot ship half described."""

    def test_names_and_aliases_are_unique(self):
        seen = [command.name for command in all_commands()] + [alias for command in all_commands() for alias in command.aliases]
        self.assertEqual(len(seen), len(set(seen)))

    def test_every_command_is_in_a_known_group_and_is_described(self):
        for command in all_commands():
            with self.subTest(command=command.name):
                self.assertIn(command.group, GROUP_ORDER)
                self.assertTrue(command.summary and not command.summary.endswith("."))
                self.assertTrue(command.examples, "a command needs at least one example")
                for arg in command.args:
                    self.assertTrue(arg.help, f"{arg.names} has no help text")

    def test_json_support_is_documented_and_only_then(self):
        for command in all_commands():
            with self.subTest(command=command.name):
                self.assertEqual(bool(command.output), command.json_output)

    def test_commands_that_change_jira_can_be_previewed(self):
        local = {"setup-pat", "remove-pat", "use", "clear-project", "config"}
        for command in all_commands():
            if command.writes and command.name not in local:
                with self.subTest(command=command.name):
                    self.assertTrue(any("--dry-run" in arg.names for arg in command.args))

    def test_no_argument_clashes_with_the_shared_options(self):
        for command in all_commands():
            destinations = [arg.dest for arg in command.args]
            with self.subTest(command=command.name):
                self.assertEqual(len(destinations), len(set(destinations)))
                self.assertFalse({"json", "verbose"} & set(destinations))

    def test_every_documented_example_is_valid_usage(self):
        for command in all_commands():
            for example in command.examples:
                tokens = shlex.split(example.command)
                if "|" in tokens:
                    tokens = tokens[tokens.index("|") + 1 :]
                with self.subTest(example=example.command):
                    self.assertEqual(tokens[:2], ["jira", command.name])
                    build_parser(command).parse_args(tokens[2:])  # raises UsageError when the example is wrong

    def test_find_command_by_name_and_alias(self):
        self.assertEqual(find_command("HISTORY").name, "changelog")
        self.assertIsNone(find_command("nope"))


class ErrorHandlingTest(unittest.TestCase):
    def test_a_known_error_gets_its_exit_code(self):
        result = run_cli("issue", "PROJ-999")
        self.assertEqual(result.code, 4)
        self.assertTrue(result.err.startswith("ERROR: Not found"))

    def test_json_error_shape(self):
        error = run_cli("issue", "PROJ-999", "--json").error()
        self.assertEqual(set(error), {"code", "message", "hint", "exitCode", "details"})
        self.assertEqual((error["code"], error["exitCode"]), ("NOT_FOUND", 4))

    def test_hints_are_printed(self):
        gateway = FakeGateway()
        gateway.errors["get_issue"] = UnavailableError("Cannot reach Jira.", hint="Check the VPN.")
        result = run_cli("issue", "PROJ-1", gateway=gateway)
        self.assertEqual(result.code, 7)
        self.assertEqual(result.err, "ERROR: Cannot reach Jira.\nHint: Check the VPN.\n")

    def test_a_reader_that_closed_early(self):
        gateway = FakeGateway()
        gateway.errors["get_issue"] = BrokenPipeError()
        self.assertEqual(run_cli("issue", "PROJ-1", gateway=gateway).code, 141)

    def test_interrupt(self):
        gateway = FakeGateway()
        gateway.errors["get_issue"] = KeyboardInterrupt()
        result = run_cli("issue", "PROJ-1", gateway=gateway)
        self.assertEqual((result.code, result.err), (130, "Interrupted.\n"))

    def test_a_bug_is_reported_plainly(self):
        gateway = FakeGateway()
        gateway.errors["get_issue"] = RuntimeError("boom")
        result = run_cli("issue", "PROJ-1", gateway=gateway)
        self.assertEqual(result.code, 1)
        self.assertIn("unexpected RuntimeError: boom", result.err)
        self.assertNotIn("Traceback", result.err)
        self.assertIn("Traceback", run_cli("issue", "PROJ-1", "--verbose", gateway=gateway).err)

    def test_a_broken_configuration_is_a_clean_error(self):
        from jira_dc_cli.adapters.driving.cli.main import run
        import io

        def factory(diagnostics):
            raise ConfigError("JIRA_RETRY must be a whole number, not 'x'.", hint="Fix it.")

        err = io.StringIO()
        code = run(["issue", "PROJ-1"], app_factory=factory, environ={}, stdout=io.StringIO(), stderr=err, stdin=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn("JIRA_RETRY", err.getvalue())

    def test_doctor_reports_a_broken_configuration_instead_of_crashing(self):
        from jira_dc_cli.adapters.driving.cli.main import run
        import io

        def factory(diagnostics):
            raise ConfigError("JIRA_RETRY must be a whole number, not 'x'.", hint="Fix it.")

        out = io.StringIO()
        code = run(["doctor"], app_factory=factory, environ={}, stdout=out, stderr=io.StringIO(), stdin=io.StringIO())
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] configuration", out.getvalue())
        as_json = io.StringIO()
        code = run(["doctor", "--json"], app_factory=factory, environ={}, stdout=as_json, stderr=io.StringIO(), stdin=io.StringIO())
        self.assertEqual(code, 1)
        report = json.loads(as_json.getvalue())
        self.assertEqual((report["ok"], report["checks"][0]["name"], report["checks"][0]["status"]), (False, "configuration", "fail"))
        self.assertEqual(report["checks"][0]["hint"], "Fix it.")


if __name__ == "__main__":
    unittest.main()
