"""The pure rules of the reference area: permission names, what counts as done, version filters, the server report."""
import unittest

from jira_dc_cli.domain.errors import UsageError
from jira_dc_cli.domain.refdata import (
    PermissionInfo,
    ServerSettings,
    StatusDetail,
    VersionInfo,
    done_statuses,
    pick_versions,
    released_filter,
    select_permissions,
    server_report,
    split_names,
)

PERMISSIONS = [
    PermissionInfo("TRANSITION_ISSUES", "Transition Issues", "PROJECT", True),
    PermissionInfo("EDIT_ISSUES", "Edit Issues", "PROJECT", False),
    PermissionInfo("COMMENT_EDIT_ALL", "Edit All Comments", "PROJECT", True),
    PermissionInfo("ADMINISTER", "Administer Jira", "GLOBAL", False),
]


class PermissionNamesTest(unittest.TestCase):
    def keys(self, names):
        return [item.key for item in select_permissions(PERMISSIONS, names)]

    def test_without_names_every_permission_comes_by_key(self):
        self.assertEqual(self.keys([]), ["ADMINISTER", "COMMENT_EDIT_ALL", "EDIT_ISSUES", "TRANSITION_ISSUES"])

    def test_a_name_is_a_key_in_any_case_or_the_name_jira_shows(self):
        self.assertEqual(self.keys(["edit_issues"]), ["EDIT_ISSUES"])
        self.assertEqual(self.keys(["Edit Issues", "edit-issues"]), ["EDIT_ISSUES"])  # twice the same: once
        self.assertEqual(self.keys(["Edit All Comments", "transition issues"]), ["COMMENT_EDIT_ALL", "TRANSITION_ISSUES"])

    def test_an_unknown_name_is_a_usage_error_that_lists_the_valid_ones_and_the_nearest(self):
        with self.assertRaises(UsageError) as caught:
            select_permissions(PERMISSIONS, ["EDIT_ISUES", "NOTHING_LIKE_IT"])
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("EDIT_ISUES (did you mean EDIT_ISSUES?)", caught.exception.message)
        self.assertIn("NOTHING_LIKE_IT", caught.exception.message)
        self.assertEqual(caught.exception.hint, "Valid names: ADMINISTER, COMMENT_EDIT_ALL, EDIT_ISSUES, TRANSITION_ISSUES.")

    def test_a_permission_that_an_add_on_brings_is_valid_when_jira_lists_it(self):
        found = [*PERMISSIONS, PermissionInfo("MANAGE_SPRINTS_PERMISSION", "Manage Sprints", "PROJECT", True)]
        self.assertEqual([item.key for item in select_permissions(found, ["manage sprints"])], ["MANAGE_SPRINTS_PERMISSION"])

    def test_names_are_split_at_commas(self):
        self.assertEqual(split_names(" A, B ,,C "), ["A", "B", "C"])
        self.assertEqual(split_names(None), [])
        self.assertEqual(split_names(""), [])


class DoneTest(unittest.TestCase):
    def test_done_is_decided_by_the_category_and_named_once(self):
        statuses = [
            StatusDetail("1", "To Do", "new"),
            StatusDetail("3", "In Progress", "indeterminate"),
            StatusDetail("6", "Closed", "done"),
            StatusDetail("5", "Done", "done"),
            StatusDetail("7", "Done", "done"),
            StatusDetail("8", "Odd", ""),
        ]
        self.assertEqual(done_statuses(statuses), ["Closed", "Done"])
        self.assertEqual(done_statuses([]), [])


class VersionFilterTest(unittest.TestCase):
    VERSIONS = [VersionInfo("1", "A", released=True), VersionInfo("2", "B"), VersionInfo("3", "C", released=True, archived=True)]

    def test_all_released_or_unreleased_in_the_order_jira_keeps_them(self):
        names = lambda released: [item.name for item in pick_versions(self.VERSIONS, released)]  # noqa: E731
        self.assertEqual(names(None), ["A", "B", "C"])
        self.assertEqual(names(True), ["A", "C"])
        self.assertEqual(names(False), ["B"])

    def test_the_two_flags_decide_the_filter_and_do_not_go_together(self):
        self.assertIsNone(released_filter(False, False))
        self.assertIs(released_filter(True, False), True)
        self.assertIs(released_filter(False, True), False)
        with self.assertRaises(UsageError):
            released_filter(True, True)


class ServerReportTest(unittest.TestCase):
    def test_the_report_has_the_version_the_build_and_the_verdict(self):
        report = server_report({"version": "11.3.12", "deploymentType": "Server", "buildNumber": 11003012}, ServerSettings(), "https://jira.example.com")
        self.assertEqual((report.version, report.deployment_type, report.build_number, report.base_url), ("11.3.12", "Server", 11003012, "https://jira.example.com"))
        self.assertEqual(report.compatibility.status, "ok")

    def test_what_the_server_leaves_out_stays_empty_and_a_bad_build_is_no_number(self):
        report = server_report({"buildNumber": "11003012"}, ServerSettings(), "")
        self.assertEqual((report.version, report.deployment_type, report.build_number), ("", "", None))
        self.assertEqual(report.compatibility.status, "warn")
        self.assertIsNone(server_report({"buildNumber": True}, ServerSettings(), "").build_number)

    def test_another_release_line_is_a_warning_with_a_hint(self):
        cloud = server_report({"version": "1001.0.0", "deploymentType": "Cloud"}, ServerSettings(), "")
        self.assertEqual(cloud.compatibility.status, "warn")
        self.assertTrue(cloud.compatibility.hint)
        older = server_report({"version": "9.4.1", "deploymentType": "Server"}, ServerSettings(), "")
        self.assertEqual(older.compatibility.status, "warn")


if __name__ == "__main__":
    unittest.main()
