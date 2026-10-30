"""The use cases of the reference area, with a fake Jira: what they ask, what they leave out, what they refuse."""
import dataclasses
import unittest

from jira_dc_cli.domain.errors import AuthError, JiraCliError, NotFoundError, UsageError, ValidationError
from jira_dc_cli.domain.model import User
from jira_dc_cli.domain.refdata import (
    MAX_COUNTED_VERSIONS,
    ComponentInfo,
    PermissionInfo,
    ProjectRole,
    RefItem,
    RoleRef,
    ServerSettings,
    VersionCounts,
    VersionInfo,
)
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, RecordingDiagnostics, build_app


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class ReferenceCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.diagnostics = RecordingDiagnostics()
        self.app = self.make()

    def make(self, **changes):
        return build_app(gateway=self.gateway, settings=settings(**changes), diagnostics=self.diagnostics)


class PrioritiesAndResolutionsTest(ReferenceCase):
    def test_they_come_in_the_order_jira_keeps_them(self):
        self.assertEqual([item.name for item in self.app.reference.priorities.execute()], ["Highest", "High", "Medium", "Low"])
        self.assertEqual([item.name for item in self.app.reference.resolutions.execute()], ["Done", "Won't Do", "Duplicate"])

    def test_free_text_is_redacted(self):
        self.gateway.ref_priorities = [RefItem("1", "Urgent", "Ask [~jdoe] or jane.doe@example.com")]
        self.assertEqual(self.app.reference.priorities.execute()[0].description, "Ask [~user] or [email]")
        self.gateway.ref_resolutions = [RefItem("2", "Fixed by [~jdoe]", "")]
        self.assertEqual(self.app.reference.resolutions.execute()[0].name, "Fixed by [~user]")


class IssueTypesTest(ReferenceCase):
    def test_every_type_of_the_jira_or_the_types_of_a_project(self):
        self.assertEqual([item.name for item in self.app.reference.issue_types.execute()], ["Epic", "Story", "Bug", "Sub-task"])
        self.assertEqual([item.name for item in self.app.reference.issue_types.execute("proj")], ["Epic", "Story", "Bug"])
        self.assertEqual(self.gateway.called("list_project_issue_types"), [("list_project_issue_types", "PROJ")])

    def test_the_subtask_flag_comes_through(self):
        self.assertEqual([item.subtask for item in self.app.reference.issue_types.execute()], [False, False, False, True])

    def test_a_project_that_does_not_exist_or_a_bad_key(self):
        with self.assertRaises(NotFoundError):
            self.app.reference.issue_types.execute("NOPE")
        with self.assertRaises(ValidationError):
            self.app.reference.issue_types.execute("not a key")
        self.assertEqual(self.gateway.called("list_project_issue_types"), [("list_project_issue_types", "NOPE")])

    def test_a_refusal_for_the_whole_list_points_to_the_project_form_and_stays_an_auth_error(self):
        self.gateway.errors["list_all_issue_types"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.app.reference.issue_types.execute()
        self.assertEqual((caught.exception.exit_code, caught.exception.details), (5, {"status": 403}))
        self.assertIn("--project KEY", caught.exception.hint)


class StatusesTest(ReferenceCase):
    def test_the_statuses_of_the_jira_and_what_counts_as_done(self):
        overview = self.app.reference.statuses.execute()
        self.assertEqual((overview.project, [item.name for item in overview.statuses], overview.issue_types), ("", ["To Do", "In Progress", "Done", "Closed"], []))
        self.assertEqual(overview.done, ["Closed", "Done"])

    def test_the_statuses_of_a_project_per_issue_type(self):
        overview = self.app.reference.statuses.execute("proj")
        self.assertEqual((overview.project, overview.statuses), ("PROJ", []))
        self.assertEqual([(item.name, [status.name for status in item.statuses]) for item in overview.issue_types][0], ("Story", ["To Do", "In Progress", "Done"]))
        self.assertEqual(overview.done, ["Closed", "Done"])
        self.assertEqual(self.app.reference.statuses.execute("DEMO").issue_types, [])

    def test_an_unknown_project(self):
        with self.assertRaises(NotFoundError):
            self.app.reference.statuses.execute("NOPE")


class ComponentsTest(ReferenceCase):
    def test_the_lead_is_a_person_and_stays_out_unless_asked_for(self):
        components = self.app.reference.components.execute("PROJ").components
        self.assertEqual([(item.name, item.lead) for item in components], [("Backend", None), ("Frontend", None)])
        shown = self.app.reference.components.execute("PROJ", with_users=True).components
        self.assertEqual(shown[0].lead.name, "jdoe")

    def test_the_current_project_is_the_default_and_none_is_an_error(self):
        self.assertEqual(self.app.reference.components.execute().project, "PROJ")
        with self.assertRaises(UsageError) as caught:
            self.make(project="").reference.components.execute()
        self.assertIn("jira use KEY", caught.exception.hint)

    def test_the_policy_refuses_the_option_and_asks_nothing(self):
        app = self.make(hide_users=True)
        with self.assertRaises(ValidationError) as caught:
            app.reference.components.execute("PROJ", with_users=True)
        self.assertIn("JIRA_HIDE_USERS=true", caught.exception.message)
        self.assertEqual(self.gateway.called("list_project_components"), [])
        self.assertIsNone(app.reference.components.execute("PROJ").components[0].lead)

    def test_text_is_redacted(self):
        self.gateway.ref_components["PROJ"] = [ComponentInfo("1", "Owned by [~jdoe]", "Mail jane.doe@example.com", User("jdoe", "Jane"))]
        shown = self.app.reference.components.execute("PROJ", with_users=True).components[0]
        self.assertEqual((shown.name, shown.description, shown.lead.name), ("Owned by [~user]", "Mail [email]", "jdoe"))


class VersionsTest(ReferenceCase):
    def names(self, **options):
        return [item.name for item in self.app.reference.versions.execute("PROJ", **options).versions]

    def test_all_released_or_unreleased(self):
        self.assertEqual(self.names(), ["Release 1.0", "Release 2.0"])
        self.assertEqual(self.names(released=True), ["Release 1.0"])
        self.assertEqual(self.names(unreleased=True), ["Release 2.0"])

    def test_both_filters_are_a_usage_error_before_anything_is_asked(self):
        with self.assertRaises(UsageError):
            self.app.reference.versions.execute("PROJ", released=True, unreleased=True)
        self.assertEqual(self.gateway.calls, [])

    def test_counts_cost_two_requests_each_and_only_when_asked(self):
        plain = self.app.reference.versions.execute("PROJ")
        self.assertEqual((plain.counted, [item.counts for item in plain.versions]), (0, [None, None]))
        self.assertEqual(self.gateway.called("get_version_counts"), [])
        counted = self.app.reference.versions.execute("PROJ", counts=True)
        self.assertEqual(counted.counted, 2)
        self.assertEqual(counted.versions[1].counts, VersionCounts(5, 1, 4))

    def test_counts_are_bounded_and_the_rest_says_so(self):
        many = [VersionInfo(str(20000 + index), f"V{index}") for index in range(MAX_COUNTED_VERSIONS + 5)]
        self.gateway.ref_versions["PROJ"] = many
        self.gateway.ref_version_counts = {item.id: VersionCounts(1, 1, 1) for item in many}
        result = self.app.reference.versions.execute("PROJ", counts=True)
        self.assertEqual((result.counted, len(self.gateway.called("get_version_counts"))), (MAX_COUNTED_VERSIONS, MAX_COUNTED_VERSIONS))
        self.assertEqual(sum(item.counts is None for item in result.versions), 5)
        self.assertEqual(len(self.diagnostics.warnings), 1)
        self.assertIn("--released or --unreleased", self.diagnostics.warnings[0])

    def test_a_filter_before_the_bound_brings_more_into_it(self):
        many = [VersionInfo(str(20000 + index), f"V{index}", released=index % 2 == 0) for index in range(60)]
        self.gateway.ref_versions["PROJ"] = many
        self.gateway.ref_version_counts = {item.id: VersionCounts(1, 1, 1) for item in many}
        result = self.app.reference.versions.execute("PROJ", released=True, counts=True)
        self.assertEqual((len(result.versions), result.counted), (30, MAX_COUNTED_VERSIONS))
        self.assertEqual(len(self.diagnostics.warnings), 1)

    def test_a_version_that_cannot_be_counted_is_left_without_counts_and_is_no_failure(self):
        del self.gateway.ref_version_counts["10300"]
        result = self.app.reference.versions.execute("PROJ", counts=True)
        self.assertEqual((result.counted, [item.counts is None for item in result.versions]), (1, [True, False]))
        self.assertIn("No counts for Release 1.0", self.diagnostics.warnings[0])
        self.gateway.errors["get_version_counts"] = AuthError("Jira refused this request (HTTP 403).")
        with self.assertRaises(AuthError):  # a refusal of the token is not a version that went away
            self.app.reference.versions.execute("PROJ", counts=True)

    def test_names_are_redacted(self):
        self.gateway.ref_versions["PROJ"] = [VersionInfo("1", "For [~jdoe]", "jane.doe@example.com")]
        shown = self.app.reference.versions.execute("PROJ").versions[0]
        self.assertEqual((shown.name, shown.description), ("For [~user]", "[email]"))

    def test_an_unknown_project(self):
        with self.assertRaises(NotFoundError):
            self.app.reference.versions.execute("NOPE")


class PermissionsTest(ReferenceCase):
    def test_all_permissions_by_key_or_the_ones_asked_for(self):
        everything = self.app.reference.permissions.execute()
        self.assertEqual([item.key for item in everything], sorted(item.key for item in everything))
        self.assertGreater(len(everything), 5)
        chosen = self.app.reference.permissions.execute(only="EDIT_ISSUES, transition issues")
        self.assertEqual([(item.key, item.have_permission) for item in chosen], [("EDIT_ISSUES", True), ("TRANSITION_ISSUES", True)])

    def test_a_missing_permission_is_shown_as_missing(self):
        self.gateway.ref_denied = {"EDIT_ISSUES"}
        self.assertFalse(self.app.reference.permissions.execute(only="EDIT_ISSUES")[0].have_permission)

    def test_the_scope_is_a_project_or_an_issue(self):
        self.app.reference.permissions.execute(project="proj")
        self.app.reference.permissions.execute(issue="proj-1")
        self.assertEqual(self.gateway.called("get_my_permissions"), [("get_my_permissions", "PROJ", ""), ("get_my_permissions", "", "PROJ-1")])

    def test_both_scopes_are_a_usage_error_without_a_request(self):
        with self.assertRaises(UsageError):
            self.app.reference.permissions.execute(project="PROJ", issue="PROJ-1")
        self.assertEqual(self.gateway.calls, [])

    def test_an_unknown_name_lists_the_valid_ones(self):
        with self.assertRaises(UsageError) as caught:
            self.app.reference.permissions.execute(only="EDIT_ISUES")
        self.assertIn("did you mean EDIT_ISSUES", caught.exception.message)
        self.assertIn("TRANSITION_ISSUES", caught.exception.hint)

    def test_an_unknown_project_or_issue(self):
        with self.assertRaises(NotFoundError):
            self.app.reference.permissions.execute(project="NOPE")
        with self.assertRaises(NotFoundError):
            self.app.reference.permissions.execute(issue="PROJ-999")


class RolesTest(ReferenceCase):
    def test_the_roles_come_by_name_without_the_people(self):
        roles = self.app.reference.roles.execute("PROJ").roles
        self.assertEqual([(role.name, role.actors) for role in roles], [("Administrators", None), ("Developers", None)])
        self.assertEqual(self.gateway.called("get_project_role"), [])

    def test_the_people_cost_one_request_per_role_and_only_when_asked_for(self):
        roles = self.app.reference.roles.execute("PROJ", with_users=True).roles
        self.assertEqual([actor.name for actor in roles[1].actors], ["jdoe", "jira-developers"])
        self.assertEqual(len(self.gateway.called("get_project_role")), 2)

    def test_the_policy_refuses_the_option(self):
        with self.assertRaises(ValidationError):
            self.make(hide_users=True).reference.roles.execute("PROJ", with_users=True)
        self.assertEqual(self.gateway.called("list_project_roles"), [])

    def test_the_requests_are_bounded(self):
        self.gateway.ref_roles["PROJ"] = [ProjectRole(str(index), f"Role {index:02d}", []) for index in range(1, 46)]
        roles = self.app.reference.roles.execute("PROJ", with_users=True).roles
        self.assertEqual(len(self.gateway.called("get_project_role")), 40)
        self.assertEqual((len(roles), roles[-1].actors, roles[0].actors), (45, None, []))
        self.assertEqual(len(self.diagnostics.warnings), 1)

    def test_a_role_without_an_id_is_listed_and_not_asked_for(self):
        self.gateway.list_project_roles = lambda key: [RoleRef("", "Odd")]
        roles = self.app.reference.roles.execute("PROJ", with_users=True).roles
        self.assertEqual([(role.name, role.actors) for role in roles], [("Odd", None)])


class DashboardsTest(ReferenceCase):
    def test_it_asks_again_while_jira_answers_less_than_asked_for(self):
        page = self.app.reference.dashboards.execute(limit=50)
        self.assertEqual((page.total, [item.id for item in page.dashboards]), (3, ["10000", "10001", "10002"]))
        self.assertEqual([call[2] for call in self.gateway.called("list_dashboards")], [0, 2])

    def test_the_limit(self):
        self.assertEqual([item.id for item in self.app.reference.dashboards.execute(limit=1).dashboards], ["10000"])

    def test_mine_and_favourite_are_the_words_of_jiras_filter_and_no_flag_sends_none(self):
        self.assertEqual([item.id for item in self.app.reference.dashboards.execute(mine=True).dashboards], ["10002"])
        self.assertEqual(self.gateway.called("list_dashboards")[-1][1], "my")
        self.assertEqual([item.id for item in self.app.reference.dashboards.execute(favourite=True).dashboards], ["10001"])
        self.assertEqual(self.gateway.called("list_dashboards")[-1][1], "favourite")
        self.assertEqual(len(self.app.reference.dashboards.execute().dashboards), 3)
        self.assertEqual(self.gateway.called("list_dashboards")[-1][1], "")

    def test_the_two_flags_do_not_go_together_and_nothing_is_asked(self):
        with self.assertRaises(UsageError) as caught:
            self.app.reference.dashboards.execute(mine=True, favourite=True)
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("--mine or --favourite", caught.exception.message)
        self.assertEqual(self.gateway.calls, [])

    def test_a_limit_below_one_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            self.app.reference.dashboards.execute(limit=0)

    def test_a_server_that_answers_nothing_does_not_keep_the_cli_asking(self):
        self.gateway.ref_dashboard_page_cap = 0
        self.assertEqual(self.app.reference.dashboards.execute().dashboards, [])
        self.assertEqual(len(self.gateway.called("list_dashboards")), 1)

    def test_names_are_redacted(self):
        self.gateway.ref_dashboards = [dataclasses.replace(self.gateway.ref_dashboards[0], name="Of [~jdoe]")]
        self.assertEqual(self.app.reference.dashboards.execute().dashboards[0].name, "Of [~user]")


class ServerTest(ReferenceCase):
    def test_the_report_has_version_settings_and_the_address_as_configured(self):
        report = self.app.reference.server.execute()
        self.assertEqual((report.version, report.deployment_type, report.base_url), ("11.3.12", "Server", "https://jira.example.com"))
        self.assertEqual(report.compatibility.status, "ok")
        self.assertEqual((report.settings.attachments_enabled, report.settings.attachment_upload_limit, report.settings.watching_enabled), (True, 10485760, True))
        self.assertEqual(report.settings.time_tracking.working_hours_per_day, 8.0)

    def test_another_release_line_is_a_warning_of_the_verdict_and_no_failure(self):
        self.gateway.server_info = lambda: {"version": "9.4.1", "deploymentType": "Server", "buildNumber": 904001}
        report = self.app.reference.server.execute()
        self.assertEqual((report.compatibility.status, report.build_number), ("warn", 904001))

    def test_the_upload_limit_is_no_reason_to_fail(self):
        self.gateway.errors["get_attachment_upload_limit"] = NotFoundError("Not found: GET /attachment/meta")
        report = self.app.reference.server.execute()
        self.assertIsNone(report.settings.attachment_upload_limit)
        self.assertEqual(len(self.diagnostics.warnings), 1)
        self.assertIn("upload limit", self.diagnostics.warnings[0])

    def test_the_other_calls_are_needed(self):
        for name in ("server_info", "get_server_settings"):
            with self.subTest(call=name):
                self.gateway.errors = {name: AuthError("refused")}
                with self.assertRaises(JiraCliError):
                    self.app.reference.server.execute()

    def test_what_jira_does_not_say_stays_unknown(self):
        self.gateway.ref_server_settings = ServerSettings()
        self.gateway.ref_attachment_upload_limit = None
        settings_ = self.app.reference.server.execute().settings
        self.assertEqual((settings_.voting_enabled, settings_.time_tracking, settings_.attachment_upload_limit), (None, None, None))


if __name__ == "__main__":
    unittest.main()
