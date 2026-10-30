"""Use cases that read: view, search, report, export, branch, fields, projects, settings, doctor, extras."""
import dataclasses
import unittest

from jira_dc_cli.application.settings import SETTING_DEFS, Settings, mask_secret
from jira_dc_cli.domain.errors import (
    ConfigError,
    NotFoundError,
    UnavailableError,
    UsageError,
    ValidationError,
)
from tests.support.fakes import (
    DEFAULT_SETTINGS,
    FakeGateway,
    MemoryConfigStore,
    MemoryFiles,
    RecordingDiagnostics,
    build_app,
)


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class ViewIssueTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_people_and_comments_are_hidden_by_default(self):
        view = self.app.view_issue.execute("proj-1")
        self.assertIsNone(view.issue.assignee)
        self.assertEqual(view.issue.comments, [])
        self.assertEqual(view.issue.comment_total, 1)
        self.assertFalse(view.users_shown or view.comments_shown)
        self.assertNotIn("jane.doe", view.issue.description)

    def test_comments_without_authors(self):
        view = self.app.view_issue.execute("PROJ-1", with_comments=True)
        self.assertEqual(len(view.issue.comments), 1)
        self.assertIsNone(view.issue.comments[0].author)
        self.assertNotIn("[~jdoe]", view.issue.comments[0].body)

    def test_with_users_shows_people(self):
        view = self.app.view_issue.execute("PROJ-1", with_users=True, with_comments=True)
        self.assertEqual(view.issue.assignee.display_name, "Jane Doe")
        self.assertEqual(view.issue.comments[0].author.name, "jdoe")

    def test_policy_refuses_with_users(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError):
            app.view_issue.execute("PROJ-1", with_users=True)

    def test_raw_json_has_no_people_unless_asked(self):
        hidden = self.app.view_issue.execute("PROJ-1", want_raw=True).raw
        self.assertEqual(hidden["fields"]["assignee"], {"hidden": True})
        shown = self.app.view_issue.execute("PROJ-1", want_raw=True, with_users=True).raw
        self.assertEqual(shown["fields"]["assignee"]["name"], "jdoe")

    def test_bad_and_unknown_keys(self):
        with self.assertRaises(ValidationError):
            self.app.view_issue.execute("not a key")
        with self.assertRaises(NotFoundError):
            self.app.view_issue.execute("PROJ-999")


class SearchIssuesTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_default_project_and_one_page(self):
        result = self.app.search_issues.execute(limit=3)
        self.assertEqual(result.jql, 'project = "PROJ" ORDER BY created DESC')
        self.assertEqual((len(result.issues), result.total), (2, 6))  # the server caps the page
        self.assertEqual(self.gateway.called("search")[0][4], 3)  # max_results as asked, even if the server returns less

    def test_all_pages(self):
        result = self.app.search_issues.execute(fetch_everything=True)
        self.assertEqual(len(result.issues), 6)
        self.assertEqual(len(self.gateway.called("search")), 3)
        self.assertEqual(len(result.raw_issues), 6)

    def test_explicit_query_and_extra_fields(self):
        self.app.search_issues.execute(query="status = Done", extra_fields=["labels"])
        _, jql, fields, *_ = self.gateway.called("search")[0]
        self.assertEqual(jql, "status = Done")
        self.assertEqual(fields[-1], "labels")

    def test_assignee_is_hidden_on_request_and_by_policy(self):
        self.assertFalse(self.app.search_issues.execute().assignee_hidden)
        self.assertTrue(self.app.search_issues.execute(hide_assignee=True).assignee_hidden)
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        result = app.search_issues.execute()
        self.assertTrue(result.assignee_hidden)
        self.assertTrue(all(issue.assignee is None for issue in result.issues))
        self.assertNotIn("jane", str(result.raw_issues).lower())

    def test_no_project_and_no_query(self):
        app = build_app(gateway=self.gateway, settings=settings(project=""))
        with self.assertRaises(UsageError):
            app.search_issues.execute()


class BranchReportExportFieldsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_branch(self):
        self.assertEqual(self.app.suggest_branch.execute("proj-2"), "bugfix/PROJ-2-fix-the-thing")
        self.assertEqual(self.app.suggest_branch.execute("PROJ-1", branch_type="chore"), "chore/PROJ-1-export-the-report")

    def test_report(self):
        report = self.app.build_report.execute(sprint="active", per_epic=True)
        self.assertEqual((report.issues, report.done, report.points, report.done_points), (4, 1, 16.0, 8.0))
        self.assertEqual({row.key: row.summary for row in report.by_epic if row.key != "(no epic)"}, {"PROJ-8": "Epic eight", "PROJ-9": "Epic nine"})
        self.assertEqual(report.scope, "project PROJ, open sprint(s)")

    def test_report_needs_a_project(self):
        with self.assertRaises(UsageError):
            build_app(gateway=self.gateway, settings=settings(project="")).build_report.execute()

    def test_epic_rows_need_the_epic_link_field(self):
        self.gateway.field_ids = lambda: {"story_points": "cf1", "epic_link": None, "sprint": None, "acceptance": None}
        with self.assertRaises(ValidationError):
            self.app.build_report.execute(per_epic=True)

    def test_missing_story_points_are_reported_as_a_warning(self):
        diagnostics = RecordingDiagnostics()
        app = build_app(gateway=self.gateway, diagnostics=diagnostics)
        self.gateway.field_ids = lambda: {"story_points": None, "epic_link": "cf2", "sprint": None, "acceptance": None}
        app.build_report.execute()
        self.assertTrue(any("Story Points" in message for message in diagnostics.warnings))

    def test_export(self):
        dump = self.app.export_backlog.execute()
        self.assertEqual(dump["fetchedAt"], "2026-10-20T12:00:00+00:00")
        self.assertEqual(list(dump["issues"]), ["PROJ-1", "PROJ-2", "PROJ-3", "PROJ-4", "PROJ-8", "PROJ-9"])
        self.assertEqual(dump["fieldIds"]["storyPoints"], "customfield_10001")
        self.assertNotIn("Jane", str(dump))

    def test_export_refuses_a_partial_dump(self):
        class Short(FakeGateway):
            def search(self, jql, *, fields, start_at=0, max_results=50):
                page = super().search(jql, fields=fields, start_at=start_at, max_results=max_results)
                return dataclasses.replace(page, total=page.total + 1)

        with self.assertRaises(UnavailableError):
            build_app(gateway=Short()).export_backlog.execute()

    def test_fields(self):
        names = [info.name for info in self.app.list_fields.execute()]
        self.assertEqual(names, sorted(names, key=str.casefold))
        self.assertEqual([info.id for info in self.app.list_fields.execute(search="epic")], ["customfield_10002"])
        self.assertNotIn("summary", [info.id for info in self.app.list_fields.execute(custom_only=True)])


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_list_is_sorted_and_policy_hides_the_lead(self):
        self.assertEqual([project.key for project in self.app.list_projects.execute()], ["DEMO", "PROJ"])
        self.assertIsNotNone(self.app.list_projects.execute()[0].lead)
        hidden = build_app(gateway=self.gateway, settings=settings(hide_users=True)).list_projects.execute()
        self.assertTrue(all(project.lead is None for project in hidden))

    def test_admin_projects(self):
        result = self.app.list_admin_projects.execute(timeout=5, retries=1)
        self.assertEqual([project.key for project in result.projects], ["PROJ"])
        self.assertEqual((result.checked, result.failed), (2, []))
        self.assertEqual(self.gateway.called("has_permission")[0][2:], ("ADMINISTER_PROJECTS", 5, 1))

    def test_admin_projects_keep_going_after_a_failed_check(self):
        class Flaky(FakeGateway):
            def has_permission(self, project_key, permission, *, timeout=None, retries=None):
                if project_key == "DEMO":
                    raise UnavailableError("timeout")
                return super().has_permission(project_key, permission, timeout=timeout, retries=retries)

        diagnostics = RecordingDiagnostics()
        result = build_app(gateway=Flaky(), diagnostics=diagnostics).list_admin_projects.execute(progress=False)
        self.assertEqual([project.key for project in result.projects], ["PROJ"])
        self.assertEqual(result.failed, [("DEMO", "timeout")])
        self.assertTrue(diagnostics.warnings)
        with self.assertRaises(UnavailableError):
            build_app(gateway=Flaky()).list_admin_projects.execute(progress=False, fail_fast=True)

    def test_show_project(self):
        self.assertEqual(self.app.show_project.execute().key, "PROJ")
        self.assertEqual(self.app.show_project.execute("demo").key, "DEMO")
        with self.assertRaises(UsageError):
            build_app(gateway=self.gateway, settings=settings(project="")).show_project.execute()

    def test_create_metadata(self):
        self.assertIn("values", self.app.show_create_metadata.execute("proj"))
        self.assertEqual(self.gateway.called("create_metadata")[0][1:], ("PROJ", ""))


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        values = Settings.from_mapping({})
        self.assertEqual((values.api_path, values.retry, values.max_time, values.output), ("/rest/api/2", 2, 120.0, "text"))
        self.assertFalse(values.hide_users)

    def test_parsing(self):
        values = Settings.from_mapping({"JIRA_HIDE_USERS": "Yes", "JIRA_RETRY": "5", "JIRA_MAX_TIME": "2.5", "JIRA_PROJECT": " PROJ ", "JIRA_OUTPUT": "json"})
        self.assertTrue(values.hide_users)
        self.assertEqual((values.retry, values.max_time, values.project), (5, 2.5, "PROJ"))
        self.assertTrue(values.json_by_default)

    def test_wrong_types_are_config_errors(self):
        for name, value in (
            ("JIRA_HIDE_USERS", "maybe"), ("JIRA_RETRY", "x"), ("JIRA_MAX_TIME", "fast"), ("JIRA_OUTPUT", "xml"),
            ("JIRA_RETRY", "-1"), ("JIRA_MAX_TIME", "0"), ("JIRA_MAX_TIME", "-5"), ("JIRA_MAX_TIME", "nan"), ("JIRA_CONNECT_TIMEOUT", "inf"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(ConfigError):
                Settings.from_mapping({name: value})

    def test_field_overrides(self):
        values = Settings.from_mapping({"JIRA_EPIC_LINK_FIELD": "customfield_1", "JIRA_SPRINT_FIELD": "customfield_3"})
        self.assertEqual(values.field_overrides(), {"epic_link": "customfield_1", "sprint": "customfield_3"})

    def test_secret_is_masked(self):
        self.assertEqual(mask_secret(""), "(not set)")
        self.assertNotIn("abc", mask_secret("abc12345"))

    def test_every_setting_has_a_description_and_a_unique_name(self):
        names = [definition.name for definition in SETTING_DEFS]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(definition.description for definition in SETTING_DEFS))

    def test_the_token_never_shows_in_a_repr(self):
        self.assertNotIn("secret-token", repr(DEFAULT_SETTINGS))


class ConfigServiceTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryConfigStore({"JIRA_PROJECT": "PROJ", "JIRA_PAT": "abc12345"})
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway, store=self.store, environ={"JIRA_HIDE_USERS": "true"})

    def test_entries_tell_where_a_value_comes_from(self):
        entries = {entry.name: entry for entry in self.app.config.entries()}
        self.assertEqual((entries["JIRA_HIDE_USERS"].value, entries["JIRA_HIDE_USERS"].source), ("true", "environment"))
        self.assertEqual(entries["JIRA_PROJECT"].source, "config file")
        self.assertEqual(entries["JIRA_RETRY"].source, "default")
        self.assertNotIn("abc12345", entries["JIRA_PAT"].value)
        self.assertTrue(entries["JIRA_PAT"].secret)

    def test_set_validates_the_name_and_the_value(self):
        self.app.config.set_value("JIRA_RETRY", "3")
        self.assertEqual(self.store.values["JIRA_RETRY"], "3")
        for name, value in (("JIRA_NOPE", "x"), ("JIRA_PAT", "x"), ("JIRA_RETRY", "many")):
            with self.subTest(name=name), self.assertRaises((UsageError, ConfigError)):
                self.app.config.set_value(name, value)
        self.assertNotIn("JIRA_NOPE", self.store.values)
        self.assertEqual(self.store.values["JIRA_PAT"], "abc12345")

    def test_unset(self):
        self.assertTrue(self.app.config.unset_value("JIRA_PROJECT"))
        self.assertFalse(self.app.config.unset_value("JIRA_PROJECT"))
        with self.assertRaises(UsageError):
            self.app.config.unset_value("JIRA_NOPE")

    def test_token(self):
        self.app.config.save_pat("  new-token \n")
        self.assertEqual(self.store.values["JIRA_PAT"], "new-token")
        with self.assertRaises(ValidationError):
            self.app.config.save_pat("  ")
        self.assertTrue(self.app.config.remove_pat())
        self.assertNotIn("JIRA_PAT", self.store.values)

    def test_use_checks_access_before_saving(self):
        self.assertEqual(self.app.config.select_project("demo").key, "DEMO")
        self.assertEqual(self.store.values["JIRA_PROJECT"], "DEMO")
        with self.assertRaises(NotFoundError):
            self.app.config.select_project("NOPE")
        self.assertEqual(self.store.values["JIRA_PROJECT"], "DEMO")

    def test_ping(self):
        self.assertEqual(build_app(gateway=self.gateway).ping.execute().user.name, "jdoe")
        self.assertIsNone(build_app(gateway=self.gateway).ping.execute(quiet=True).user)
        self.assertIsNone(self.app.ping.execute().user if self.app.settings.hide_users else None)


class DoctorTest(unittest.TestCase):
    def checks(self, **options):
        gateway = options.pop("gateway", FakeGateway())
        files = MemoryFiles({"memory:/config": ""})
        files.files.update(options.pop("extra_files", {}))
        return {check.name: check for check in build_app(gateway=gateway, files=files, **options).doctor.execute()}

    def test_a_healthy_setup(self):
        checks = self.checks()
        self.assertEqual({check.status for check in checks.values()}, {"ok"})
        self.assertIn("Jira 11.3.12", checks["server"].detail)

    def test_missing_configuration_skips_the_connection(self):
        checks = self.checks(settings=settings(base_url="", pat=""))
        self.assertEqual((checks["base url"].status, checks["token"].status, checks["connection"].status), ("fail", "fail", "warn"))
        self.assertIn("jira setup-pat", checks["token"].hint)

    def test_tls_findings(self):
        self.assertEqual(self.checks(settings=settings(insecure=True))["tls"].status, "warn")
        self.assertEqual(self.checks(settings=settings(ca_bundle="missing.pem"))["tls"].status, "fail")
        self.assertEqual(self.checks(settings=settings(ca_bundle="ca.pem"), extra_files={"ca.pem": "x"})["tls"].status, "ok")

    def test_plain_http_is_a_failure_unless_allowed_or_local(self):
        plain = self.checks(settings=settings(base_url="http://jira.example.com"))
        self.assertEqual((plain["base url"].status, plain["connection"].status), ("fail", "warn"))
        self.assertIn("JIRA_ALLOW_HTTP", plain["base url"].hint)
        self.assertEqual(self.checks(settings=settings(base_url="http://jira.example.com", allow_http=True))["base url"].status, "warn")
        for url in ("http://localhost:8080/jira", "http://127.0.0.1:8080", "http://user@[::1]:8080/jira"):
            with self.subTest(url=url):
                self.assertEqual(self.checks(settings=settings(base_url=url))["base url"].status, "ok")

    def test_connection_failure_carries_the_hint(self):
        gateway = FakeGateway()
        gateway.errors["current_user"] = UnavailableError("Cannot reach Jira", hint="Check the VPN.")
        check = self.checks(gateway=gateway)["connection"]
        self.assertEqual((check.status, check.hint), ("fail", "Check the VPN."))

    def test_missing_fields_and_project_are_findings(self):
        gateway = FakeGateway()
        gateway.fields = []
        gateway.field_ids = lambda: {"story_points": None, "epic_link": "x", "sprint": "y", "acceptance": "z"}
        checks = self.checks(gateway=gateway, settings=settings(project=""))
        self.assertEqual(checks["fields"].status, "warn")
        self.assertIn("Story Points", checks["fields"].detail)
        self.assertEqual(checks["project"].status, "warn")

    def test_unknown_project_fails(self):
        self.assertEqual(self.checks(settings=settings(project="NOPE"))["project"].status, "fail")

    def test_privacy_is_reported(self):
        self.assertIn("never printed", self.checks(settings=settings(hide_users=True))["privacy"].detail)


class ExtrasTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles()
        self.app = build_app(gateway=self.gateway, files=self.files)

    def test_docs(self):
        self.assertEqual([topic.name for topic in self.app.read_docs.topics()], ["daily-workflow", "troubleshooting"])
        self.assertIn("Daily workflow", self.app.read_docs.topic("daily-workflow"))
        self.assertIn("1.0.0", self.app.read_docs.changelog())

    def test_skill_install(self):
        result = self.app.install_skill.execute(".github/skills")
        self.assertEqual((result.path, result.action), (".github/skills/jira-dc-cli/SKILL.md", "installed"))
        self.assertIn("name: jira-dc-cli", self.files.files[result.path])
        dry = self.app.install_skill.execute("elsewhere", dry_run=True)
        self.assertEqual(dry.action, "installed")
        self.assertNotIn(dry.path, self.files.files)

    def test_skill_install_does_not_overwrite_what_differs_without_force(self):
        self.app.install_skill.execute("skills")
        path = "skills/jira-dc-cli/SKILL.md"
        self.assertEqual(self.app.install_skill.execute("skills").action, "unchanged")
        self.files.files[path] = "my own version of the skill"
        with self.assertRaises(ValidationError) as caught:
            self.app.install_skill.execute("skills")
        self.assertIn("--force", caught.exception.hint)
        self.assertEqual(self.files.files[path], "my own version of the skill")
        self.assertEqual(self.app.install_skill.execute("skills", dry_run=True, force=True).action, "replaced")
        self.assertEqual(self.files.files[path], "my own version of the skill")  # a dry run changes nothing
        self.assertEqual(self.app.install_skill.execute("skills", force=True).action, "replaced")
        self.assertIn("name: jira-dc-cli", self.files.files[path])

    def test_convert_markdown(self):
        self.assertEqual(self.app.convert_markdown.execute("# T\n"), "h1. T")

    def test_raw_api_reads_freely_and_writes_on_request(self):
        self.assertTrue(self.app.raw_api.execute("get", "/issue/PROJ-1").response["ok"])
        self.assertEqual(self.app.raw_api.execute("GET", "issue/PROJ-1").path, "/issue/PROJ-1")  # the leading / is optional
        with self.assertRaises(UsageError):
            self.app.raw_api.execute("POST", "/issue", '{"a": 1}')
        with self.assertRaises(UsageError):
            self.app.raw_api.execute("DELETE", "/issue/PROJ-1", write=True)
        with self.assertRaises(UsageError):
            self.app.raw_api.execute("GET", "  ")
        with self.assertRaises(UsageError):
            self.app.raw_api.execute("PUT", "/issue/PROJ-1", write=True)  # needs a body
        with self.assertRaises(ValidationError):
            self.app.raw_api.execute("PUT", "/issue/PROJ-1", "{not json", write=True)

    def test_raw_api_dry_run_sends_nothing(self):
        result = self.app.raw_api.execute("POST", "/issue", '{"a": 1}', write=True, dry_run=True)
        self.assertEqual((result.dry_run, result.body), (True, {"a": 1}))
        self.assertEqual(self.gateway.called("request"), [])

    def test_raw_api_hides_people_by_policy(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        response = app.raw_api.execute("GET", "/x").response
        self.assertEqual(response["user"], {"hidden": True})


if __name__ == "__main__":
    unittest.main()
