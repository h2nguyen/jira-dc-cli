"""The agent profile, lengths of time, and the doctor: --quick, --cached, --strict-privacy, the 11.3 check and the launcher."""
import json
import unittest

from jira_dc_cli.application.doctor import FAIL, OK, WARN
from jira_dc_cli.application.settings import Settings
from jira_dc_cli.domain.durations import parse_duration
from jira_dc_cli.domain.errors import ConfigError, NotFoundError, ValidationError
from jira_dc_cli.version import __version__
from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway, MemoryCache, MemoryConfigStore, MemoryFiles, build_app

BASE = {"JIRA_BASE_URL": "https://jira.example.com", "JIRA_PAT": "secret-token", "JIRA_PROJECT": "PROJ"}


def settings(**values):
    return Settings.from_mapping({**BASE, **values})


class OldJira(FakeGateway):
    def server_info(self):
        self._enter("server_info")
        return {"version": "9.12.0", "deploymentType": "Server"}


class DurationTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("10m"), 600)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("3d"), 3 * 86400)
        self.assertEqual(parse_duration("1w"), 7 * 86400)
        self.assertEqual(parse_duration("1.5h"), 5400)
        self.assertEqual(parse_duration(" 10 M "), 600)

    def test_a_bare_number_counts_in_the_default_unit(self):
        self.assertEqual(parse_duration("30"), 30)
        self.assertEqual(parse_duration("30", default_unit="m"), 1800)

    def test_garbage_is_refused_with_a_hint(self):
        for text in ("", "soon", "-5m", "10x", "m10", "9" * 5000, "١٠m", "1" * 10):
            with self.subTest(text=text[:12]), self.assertRaises(ValidationError) as caught:
                parse_duration(text)
            self.assertIn("10m", caught.exception.hint)


class ProfileTest(unittest.TestCase):
    def test_the_agent_profile_sets_the_defaults(self):
        agent = settings(JIRA_PROFILE="agent")
        self.assertEqual((agent.profile, agent.hide_users, agent.output, agent.quiet), ("agent", True, "json", True))

    def test_without_a_profile_nothing_changes(self):
        plain = settings()
        self.assertEqual((plain.profile, plain.hide_users, plain.output, plain.quiet), ("", False, "text", False))

    def test_a_setting_that_was_set_wins_over_the_profile(self):
        self.assertFalse(settings(JIRA_PROFILE="agent", JIRA_HIDE_USERS="false").hide_users)
        self.assertEqual(settings(JIRA_PROFILE="agent", JIRA_OUTPUT="text").output, "text")
        self.assertTrue(settings(JIRA_PROFILE="agent", JIRA_HIDE_USERS="true").hide_users)

    def test_an_empty_setting_counts_as_not_set(self):
        self.assertTrue(settings(JIRA_PROFILE="agent", JIRA_HIDE_USERS="").hide_users)

    def test_the_name_is_not_case_sensitive_and_an_unknown_one_is_refused(self):
        self.assertEqual(settings(JIRA_PROFILE="Agent").profile, "agent")
        with self.assertRaises(ConfigError) as caught:
            settings(JIRA_PROFILE="robot")
        self.assertIn("agent", caught.exception.message)

    def test_config_show_lists_the_profile_and_what_it_implies(self):
        store = MemoryConfigStore({**BASE, "JIRA_PROFILE": "agent"})
        app = build_app(settings=settings(JIRA_PROFILE="agent"), store=store)
        entries = {entry.name: entry for entry in app.config.entries()}
        self.assertEqual((entries["JIRA_PROFILE"].value, entries["JIRA_PROFILE"].source), ("agent", "config file"))
        self.assertEqual((entries["JIRA_HIDE_USERS"].value, entries["JIRA_HIDE_USERS"].source), ("true", "profile agent"))
        self.assertEqual((entries["JIRA_OUTPUT"].value, entries["JIRA_OUTPUT"].source), ("json", "profile agent"))

    def test_a_setting_of_its_own_is_shown_as_such(self):
        store = MemoryConfigStore({**BASE, "JIRA_PROFILE": "agent", "JIRA_HIDE_USERS": "false"})
        app = build_app(settings=settings(JIRA_PROFILE="agent", JIRA_HIDE_USERS="false"), store=store)
        entries = {entry.name: entry for entry in app.config.entries()}
        self.assertEqual((entries["JIRA_HIDE_USERS"].value, entries["JIRA_HIDE_USERS"].source), ("false", "config file"))

    def test_the_profile_is_a_setting_that_can_be_saved(self):
        store = MemoryConfigStore()
        build_app(store=store).config.set_value("JIRA_PROFILE", "agent")
        self.assertEqual(store.values["JIRA_PROFILE"], "agent")
        with self.assertRaises(ConfigError):
            build_app(store=store).config.set_value("JIRA_PROFILE", "robot")


class ProfileBehaviourTest(unittest.TestCase):
    def test_errors_are_json_and_texts_are_json_without_asking(self):
        agent = settings(JIRA_PROFILE="agent")
        result = run_cli("issue", "PROJ-999", settings=agent)
        self.assertEqual(result.code, 4)
        self.assertEqual(result.error()["code"], "NOT_FOUND")
        self.assertEqual(run_cli("issue", "PROJ-1", settings=agent).json()["key"], "PROJ-1")

    def test_people_are_hidden_without_a_variable_in_every_command(self):
        result = run_cli("issue", "PROJ-1", "--with-users", settings=settings(JIRA_PROFILE="agent"))
        self.assertEqual(result.code, 1)
        self.assertIn("JIRA_HIDE_USERS", result.err)

    def test_api_prints_no_people_under_the_profile(self):
        gateway = FakeGateway()
        result = run_cli("api", "GET", "myself", gateway=gateway, settings=settings(JIRA_PROFILE="agent"))
        self.assertNotIn("Jane Doe", result.out)

    def test_informational_messages_are_left_out_but_not_warnings(self):
        query = ("issues", "--jql", "assignee = jdoe")
        self.assertIn("--jql replaces the project query", run_cli(*query, settings=settings()).err)
        self.assertEqual(run_cli(*query, settings=settings(JIRA_PROFILE="agent")).err, "")

    def test_the_text_form_is_still_there_when_asked_for(self):
        result = run_cli("issue", "PROJ-1", settings=settings(JIRA_PROFILE="agent", JIRA_OUTPUT="text"))
        self.assertTrue(result.out.startswith("PROJ-1  ["))

    def test_a_usage_error_is_json_under_the_profile_whether_the_environment_or_the_settings_say_it(self):
        from_environment = run_cli("issue", "PROJ-1", "--nope", environ={"JIRA_PROFILE": "agent"})
        self.assertEqual((from_environment.code, from_environment.error()["code"]), (2, "USAGE"))
        from_settings = run_cli("issue", "PROJ-1", "--nope", settings=settings(JIRA_PROFILE="agent"))  # the profile is in the configuration file
        self.assertEqual((from_settings.code, from_settings.error()["code"]), (2, "USAGE"))
        self.assertEqual(run_cli("nope", settings=settings(JIRA_PROFILE="agent")).error()["code"], "USAGE")

    def test_a_setting_of_its_own_still_wins_for_the_errors(self):
        text = run_cli("issue", "PROJ-1", "--nope", settings=settings(JIRA_PROFILE="agent", JIRA_OUTPUT="text"))
        self.assertTrue(text.err.startswith("ERROR: "))
        self.assertTrue(run_cli("issue", "PROJ-1", "--nope", environ={"JIRA_PROFILE": "agent", "JIRA_OUTPUT": "text"}).err.startswith("ERROR: "))

    def test_without_a_profile_a_usage_error_is_text_as_before(self):
        self.assertTrue(run_cli("issue", "PROJ-1", "--nope").err.startswith("ERROR: "))

    def test_the_commands_that_need_no_settings_follow_the_environment(self):
        self.assertEqual(run_cli("version", environ={"JIRA_PROFILE": "agent"}).json()["name"], "jira-dc-cli")
        self.assertEqual(run_cli("version", environ={"JIRA_PROFILE": "agent", "JIRA_OUTPUT": "text"}).out, f"jira-dc-cli {__version__}\n")


class DoctorTest(unittest.TestCase):
    def run_doctor(self, gateway=None, *, cache=None, runtime=None, options=None, **setting_values):
        app = build_app(gateway=gateway or FakeGateway(), files=MemoryFiles({"memory:/config": ""}), settings=settings(**setting_values), cache=cache, runtime=runtime)
        run = app.doctor.run(**(options or {}))
        return run, {check.name: check for check in run.checks}

    def test_the_release_line_it_is_written_for_is_ok(self):
        _, checks = self.run_doctor()
        self.assertEqual(checks["server"].status, OK)
        self.assertIn("11.3.x release line", checks["server"].detail)

    def test_an_older_jira_is_a_warning_that_names_the_target(self):
        _, checks = self.run_doctor(OldJira())
        self.assertEqual(checks["server"].status, WARN)
        self.assertIn("Jira 9.12.0", checks["server"].detail)
        self.assertIn("11.3.x", checks["server"].hint)

    def test_quick_asks_at_most_twice_and_leaves_out_fields_and_project(self):
        gateway = FakeGateway()
        run, checks = self.run_doctor(gateway, options={"quick": True})
        self.assertEqual([call[0] for call in gateway.calls], ["current_user", "server_info"])
        self.assertNotIn("fields", checks)
        self.assertNotIn("project", checks)
        self.assertIn("connection", checks)
        self.assertFalse(run.cached)

    def test_the_full_run_is_unchanged(self):
        _, checks = self.run_doctor()
        self.assertEqual(list(checks), ["runtime", "config file", "base url", "token", "tls", "connection", "server", "fields", "project", "privacy"])

    def test_cached_makes_no_request_and_says_so(self):
        cache, gateway = MemoryCache(), FakeGateway()
        first, _ = self.run_doctor(gateway, cache=cache, options={"cached_for": 600})
        self.assertFalse(first.cached)
        asked = len(gateway.calls)
        self.assertGreater(asked, 0)
        second, checks = self.run_doctor(gateway, cache=cache, options={"cached_for": 600})
        self.assertTrue(second.cached)
        self.assertEqual(second.cached_at, "2026-10-20T12:00:00+00:00")
        self.assertEqual(len(gateway.calls), asked)
        self.assertEqual(list(checks), ["runtime", "config file", "base url", "token", "tls", "connection", "server", "fields", "project", "privacy"])

    def test_without_cached_it_asks_again_and_a_failed_run_is_not_remembered(self):
        cache, gateway = MemoryCache(), FakeGateway()
        self.run_doctor(gateway, cache=cache)  # records
        self.run_doctor(gateway, cache=cache, options={"cached_for": 0})
        self.assertEqual(len(gateway.called("current_user")), 2)
        broken_cache, broken = MemoryCache(), FakeGateway()
        broken.errors["get_project"] = NotFoundError("gone")  # a project that cannot be read is a failed check
        run, _ = self.run_doctor(broken, cache=broken_cache)
        self.assertTrue(any(check.status == FAIL for check in run.checks))
        self.assertEqual(broken_cache.values, {})

    def test_quick_and_full_results_are_kept_apart(self):
        cache, gateway = MemoryCache(), FakeGateway()
        self.run_doctor(gateway, cache=cache, options={"quick": True, "cached_for": 600})
        full, _ = self.run_doctor(gateway, cache=cache, options={"cached_for": 600})
        self.assertFalse(full.cached)

    def test_a_remembered_run_is_not_reused_for_another_project_or_token(self):
        cache, gateway = MemoryCache(), FakeGateway()
        self.run_doctor(gateway, cache=cache, options={"cached_for": 600})
        calls = len(gateway.calls)
        other_project, _ = self.run_doctor(gateway, cache=cache, options={"cached_for": 600}, JIRA_PROJECT="DEMO")
        self.assertFalse(other_project.cached)
        other_token, _ = self.run_doctor(gateway, cache=cache, options={"cached_for": 600}, JIRA_PAT="another-token")
        self.assertFalse(other_token.cached)
        self.assertGreater(len(gateway.calls), calls)
        self.assertNotIn("secret-token", repr(cache.values))  # the token is not in the cache, not even as part of a name
        self.assertNotIn("another-token", repr(cache.values))

    def test_privacy_is_a_warning_under_the_profile_when_people_can_be_shown(self):
        _, checks = self.run_doctor(JIRA_PROFILE="agent", JIRA_HIDE_USERS="false")
        self.assertEqual(checks["privacy"].status, WARN)
        self.assertIn("jira config unset JIRA_HIDE_USERS", checks["privacy"].hint)

    def test_privacy_is_ok_when_people_are_hidden_and_names_the_profile(self):
        _, checks = self.run_doctor(JIRA_PROFILE="agent")
        self.assertEqual(checks["privacy"].status, OK)
        self.assertIn("profile agent", checks["privacy"].detail)

    def test_strict_privacy_fails_whenever_people_can_be_shown(self):
        _, loose = self.run_doctor(options={"strict_privacy": True})
        self.assertEqual(loose["privacy"].status, FAIL)
        _, hidden = self.run_doctor(options={"strict_privacy": True}, JIRA_HIDE_USERS="true")
        self.assertEqual(hidden["privacy"].status, OK)
        _, normal = self.run_doctor()
        self.assertEqual(normal["privacy"].status, OK)  # without the option the default is unchanged

    def test_the_launcher_check_appears_only_when_the_runtime_says(self):
        _, silent = self.run_doctor()
        self.assertNotIn("launcher", silent)
        _, found = self.run_doctor(runtime={"launcher": "C:/bin/jira.cmd", "version": "1.0.0", "python": "3.13"})
        self.assertEqual(found["launcher"].status, OK)
        _, missing = self.run_doctor(runtime={"launcher": "", "program": "C:/x/jira.py", "install_script": "C:/x/install.py", "version": "1.0.0", "python": "3.13"})
        self.assertEqual(missing["launcher"].status, WARN)
        self.assertIn("C:/x/jira.py", missing["launcher"].detail)
        self.assertIn('python "C:/x/install.py" --bin-dir DIR', missing["launcher"].hint)


    def test_the_hint_without_an_installer_points_to_the_source_folder(self):
        _, checks = self.run_doctor(runtime={"launcher": "", "program": "C:/app/jira.py", "install_script": "", "version": "1.0.0", "python": "3.13"})
        self.assertIn("python install.py --bin-dir DIR, run in the source folder of jira-dc-cli", checks["launcher"].hint)

    def test_credentials_in_the_address_are_a_warning_and_never_shown(self):
        _, checks = self.run_doctor(JIRA_BASE_URL="https://jdoe:s3cr3tpw@jira.example.com/jira")
        self.assertEqual(checks["base url"].status, WARN)
        self.assertIn("user name or password", checks["base url"].detail)
        self.assertNotIn("s3cr3tpw", f"{checks['base url'].detail} {checks['base url'].hint}")
        _, clean = self.run_doctor()
        self.assertEqual(clean["base url"].status, OK)


class AddressAndTimeoutTest(unittest.TestCase):
    def test_credentials_are_taken_out_of_the_address_and_remembered(self):
        for written, kept in (
            ("https://jdoe:s3cr3tpw@jira.example.com/jira", "https://jira.example.com/jira"),
            ("https://jdoe@jira.example.com:8443/jira", "https://jira.example.com:8443/jira"),
            ("HTTP://user:p@ss@127.0.0.1:8080", "HTTP://127.0.0.1:8080"),
            ("jdoe:s3cr3tpw@jira.example.com/jira", "jira.example.com/jira"),
        ):
            with self.subTest(written=written):
                values = settings(JIRA_BASE_URL=written)
                self.assertEqual((values.base_url, values.base_url_credentials), (kept, True))
        for plain in ("https://jira.example.com/jira", "https://jira.example.com/jira?x=1", ""):
            with self.subTest(plain=plain):
                values = Settings.from_mapping({"JIRA_BASE_URL": plain})
                self.assertEqual((values.base_url, values.base_url_credentials), (plain, False))

    def test_the_address_in_a_result_has_no_credentials(self):
        result = run_cli("server", gateway=FakeGateway(), settings=settings(JIRA_BASE_URL="https://jdoe:s3cr3tpw@jira.example.com/jira"), files=MemoryFiles({"memory:/config": ""}))
        self.assertNotIn("s3cr3tpw", result.out + result.err)

    def test_projects_admin_refuses_a_timeout_that_the_socket_layer_cannot_take(self):
        result = run_cli("projects-admin", "--timeout", "1e9", gateway=FakeGateway())
        self.assertEqual(result.code, 2)
        self.assertIn("from 1 to 86400", result.err)

    def test_a_timeout_of_more_than_a_day_is_refused_where_the_socket_layer_would_fail(self):
        for name in ("JIRA_MAX_TIME", "JIRA_CONNECT_TIMEOUT"):
            for value in ("86401", "3000000", "1e9", "1e308"):
                with self.subTest(name=name, value=value), self.assertRaises(ConfigError) as caught:
                    Settings.from_mapping({name: value})
                self.assertIn("at most 86400", caught.exception.message)
            accepted = Settings.from_mapping({name: "86400"})
            self.assertEqual(accepted.max_time if name == "JIRA_MAX_TIME" else accepted.connect_timeout, 86400.0)


class DoctorCommandTest(unittest.TestCase):
    def doctor(self, *argv, **options):
        files = MemoryFiles({"memory:/config": ""})
        return run_cli("doctor", *argv, files=files, **options)

    def test_json_has_the_cached_keys(self):
        report = self.doctor("--json").json()
        self.assertEqual((report["ok"], report["cached"], report["cachedAt"]), (True, False, None))

    def test_cached_in_the_command(self):
        cache = MemoryCache()
        self.doctor("--cached", "10m", cache=cache)
        report = self.doctor("--cached", "10m", "--json", cache=cache).json()
        self.assertEqual((report["cached"], report["cachedAt"]), (True, "2026-10-20T12:00:00+00:00"))
        text = self.doctor("--cached", "10m", cache=cache).out
        self.assertIn("nothing was asked of Jira", text)

    def test_a_bare_number_of_cached_counts_in_minutes_and_garbage_is_refused(self):
        cache = MemoryCache()
        self.doctor("--cached", "5", cache=cache)
        self.assertTrue(self.doctor("--cached", "5", "--json", cache=cache).json()["cached"])
        bad = self.doctor("--cached", "soon")
        self.assertEqual(bad.code, 1)
        self.assertIn("Not a length of time", bad.err)

    def test_strict_privacy_sets_the_exit_code(self):
        result = self.doctor("--strict-privacy", "--json")
        self.assertEqual(result.code, 1)
        report = json.loads(result.out)
        self.assertEqual((report["ok"], next(c for c in report["checks"] if c["name"] == "privacy")["status"]), (False, "fail"))
        self.assertEqual(self.doctor("--strict-privacy", "--json", settings=settings(JIRA_HIDE_USERS="true")).code, 0)

    def test_quick_in_the_command(self):
        gateway = FakeGateway()
        self.doctor("--quick", gateway=gateway)
        self.assertEqual([call[0] for call in gateway.calls], ["current_user", "server_info"])


if __name__ == "__main__":
    unittest.main()
