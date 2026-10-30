"""The commands of the reference area, run through the command line with a fake Jira behind it: text, JSON, lists, privacy, errors."""
import dataclasses
import unittest

from jira_dc_cli.domain.errors import AuthError
from jira_dc_cli.domain.refdata import VersionCounts, VersionInfo
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class CommandCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()

    def run_cli(self, *argv, **options):
        return run_cli(*argv, gateway=self.gateway, **options)

    def ok(self, *argv, **options):
        result = self.run_cli(*argv, **options)
        self.assertEqual(result.code, 0, result.err)
        return result


class SimpleListsTest(CommandCase):
    def test_priorities_as_a_table_and_as_json(self):
        lines = self.ok("priorities").out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "NAME", "DESCRIPTION"])
        self.assertEqual(lines[1].split(None, 2), ["1", "Highest", "This problem will block progress."])
        data = self.ok("priorities", "--json").json()
        self.assertEqual(data[0], {"id": "1", "name": "Highest", "description": "This problem will block progress."})
        self.assertEqual(len(self.ok("priorities", "--json").out.strip().splitlines()), 1)  # piped: one compact line

    def test_resolutions(self):
        self.assertEqual([item["name"] for item in self.ok("resolutions", "--json").json()], ["Done", "Won't Do", "Duplicate"])
        self.assertIn("Won't Do", self.ok("resolutions").out)

    def test_select_and_format_work_like_in_every_list(self):
        self.assertEqual(self.ok("priorities", "--json", "--select", "name").json()[:2], [{"name": "Highest"}, {"name": "High"}])
        tsv = self.ok("priorities", "--format", "tsv").out.splitlines()
        self.assertEqual((tsv[0], tsv[1].split("\t")[:2]), ("ID\tNAME\tDESCRIPTION", ["1", "Highest"]))
        self.assertTrue(self.ok("resolutions", "--format", "md").out.startswith("| ID | NAME | DESCRIPTION |"))
        self.assertEqual(self.ok("priorities", "--select", "id,name").out.splitlines()[0].split(), ["id", "name"])

    def test_an_unknown_name_in_select_and_json_with_a_format_are_usage_errors(self):
        result = self.run_cli("priorities", "--select", "nope")
        self.assertEqual(result.code, 2)
        self.assertIn("name", result.err)
        self.assertEqual(self.run_cli("priorities", "--json", "--format", "md").code, 2)

    def test_the_settings_can_ask_for_json(self):
        self.assertEqual(self.ok("priorities", settings=settings(output="json")).json()[0]["name"], "Highest")

    def test_an_empty_list_says_so(self):
        self.gateway.ref_priorities = []
        self.assertEqual(self.ok("priorities").out, "No priorities.\n")
        self.assertEqual(self.ok("priorities", "--json").json(), [])


class IssueTypesTest(CommandCase):
    def test_every_type_of_the_jira_and_the_types_of_a_project(self):
        self.assertEqual([row["name"] for row in self.ok("issue-types", "--json").json()], ["Epic", "Story", "Bug", "Sub-task"])
        self.assertEqual([row["name"] for row in self.ok("issue-types", "--project", "proj", "--json").json()], ["Epic", "Story", "Bug"])
        self.assertEqual(self.ok("issue-types", "--json").json()[3], {"id": "10202", "name": "Sub-task", "description": "The sub-task of the issue.", "subtask": True})
        self.assertIn("true", self.ok("issue-types").out)

    def test_a_project_that_does_not_exist_and_a_bad_key(self):
        self.assertEqual(self.run_cli("issue-types", "--project", "NOPE").code, 4)
        bad = self.run_cli("issue-types", "--project", "not a key", "--json")
        self.assertEqual((bad.code, bad.error()["code"]), (1, "INVALID"))

    def test_a_refusal_for_the_whole_list_points_to_the_project_form(self):
        self.gateway.errors["list_all_issue_types"] = AuthError("Jira refused this request (HTTP 403).")
        result = self.run_cli("issue-types", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("issue-types --project KEY", result.error()["hint"])


class StatusesTest(CommandCase):
    def test_the_statuses_of_the_jira_say_what_done_means(self):
        result = self.ok("statuses")
        self.assertEqual(result.out.splitlines()[0].split(), ["ID", "NAME", "CATEGORY", "DESCRIPTION"])
        self.assertTrue(result.out.rstrip().endswith("Done means: Closed, Done."))
        data = self.ok("statuses", "--json").json()
        self.assertEqual((data["project"], data["doneStatuses"], [row["name"] for row in data["statuses"]]), (None, ["Closed", "Done"], ["To Do", "In Progress", "Done", "Closed"]))
        self.assertEqual(data["statuses"][0], {"id": "1", "name": "To Do", "category": "new", "description": "The issue is waiting."})
        self.assertNotIn("issueTypes", data)

    def test_the_statuses_of_a_project_come_per_issue_type_with_their_category(self):
        result = self.ok("statuses", "--project", "PROJ")
        self.assertEqual(result.out.splitlines()[0].split(), ["ISSUE", "TYPE", "STATUSES", "CATEGORIES"])
        self.assertIn("To Do, In Progress, Done", result.out)
        data = self.ok("statuses", "--project", "PROJ", "--json").json()
        self.assertEqual((data["project"], data["doneStatuses"]), ("PROJ", ["Closed", "Done"]))
        story = data["issueTypes"][0]
        self.assertEqual((story["name"], story["subtask"], [status["category"] for status in story["statuses"]]), ("Story", False, ["new", "indeterminate", "done"]))
        self.assertNotIn("statuses", data)

    def test_select_reaches_into_the_issue_types(self):
        data = self.ok("statuses", "--project", "PROJ", "--json", "--select", "name,statuses.name").json()
        self.assertEqual(data["issueTypes"][0], {"name": "Story", "statuses": [{"name": "To Do"}, {"name": "In Progress"}, {"name": "Done"}]})
        self.assertEqual(data["doneStatuses"], ["Closed", "Done"])
        tsv = self.ok("statuses", "--project", "PROJ", "--format", "tsv").out.splitlines()
        self.assertEqual(tsv[0], "ISSUE TYPE\tSTATUSES\tCATEGORIES")
        self.assertEqual(tsv[1], "Story\tTo Do, In Progress, Done\tnew, indeterminate, done")

    def test_an_unknown_project(self):
        self.assertEqual(self.run_cli("statuses", "--project", "NOPE").code, 4)


class ComponentsTest(CommandCase):
    def test_the_lead_is_not_shown_without_with_users(self):
        result = self.ok("components", "PROJ")
        self.assertEqual(result.out.splitlines()[0].split(), ["ID", "NAME", "DESCRIPTION"])
        self.assertNotIn("Jane", result.out)
        data = self.ok("components", "PROJ", "--json").json()
        self.assertEqual(data[0], {"id": "10100", "name": "Backend", "description": "Server side.", "lead": None})

    def test_the_lead_with_with_users(self):
        self.assertIn("Jane Doe", self.ok("components", "PROJ", "--with-users").out)
        self.assertEqual(self.ok("components", "PROJ", "--with-users", "--json").json()[0]["lead"], {"name": "jdoe", "displayName": "Jane Doe", "email": "jane.doe@example.com"})

    def test_the_policy_refuses_with_users_and_never_shows_the_lead(self):
        result = self.run_cli("components", "PROJ", "--with-users", "--json", settings=settings(hide_users=True))
        self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
        self.assertIn("JIRA_HIDE_USERS=true", result.error()["message"])
        plain = self.ok("components", "PROJ", "--json", settings=settings(hide_users=True))
        self.assertNotIn("jdoe", plain.out + plain.err)

    def test_the_current_project_is_the_default(self):
        self.assertEqual(len(self.ok("components", "--json").json()), 2)
        self.assertEqual(self.run_cli("components", settings=settings(project="")).code, 2)

    def test_a_project_without_components_and_an_unknown_one(self):
        self.assertEqual(self.ok("components", "DEMO").out, "No components in DEMO.\n")
        self.assertEqual(self.run_cli("components", "NOPE").code, 4)


class FixVersionsTest(CommandCase):
    def test_every_version_with_its_state(self):
        lines = self.ok("fix-versions", "PROJ").out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "NAME", "RELEASED", "ARCHIVED", "RELEASE", "DATE"])
        self.assertEqual(lines[1].split(), ["10300", "Release", "1.0", "true", "false", "2026-06-01"])
        data = self.ok("fix-versions", "PROJ", "--json").json()
        self.assertEqual(data[1], {"id": "10301", "name": "Release 2.0", "description": "", "released": False, "archived": False, "releaseDate": "2026-12-01", "counts": None})

    def test_released_and_unreleased_and_not_both(self):
        self.assertEqual([row["name"] for row in self.ok("fix-versions", "PROJ", "--released", "--json").json()], ["Release 1.0"])
        self.assertEqual([row["name"] for row in self.ok("fix-versions", "PROJ", "--unreleased", "--json").json()], ["Release 2.0"])
        self.gateway.calls.clear()
        result = self.run_cli("fix-versions", "PROJ", "--released", "--unreleased")
        self.assertEqual(result.code, 2)
        self.assertEqual(self.gateway.calls, [])

    def test_counts_add_columns_and_a_nested_object(self):
        result = self.ok("fix-versions", "PROJ", "--counts")
        self.assertEqual(result.out.splitlines()[0].split()[-3:], ["FIXED", "AFFECTED", "UNRESOLVED"])
        self.assertEqual(self.ok("fix-versions", "PROJ", "--counts", "--json").json()[1]["counts"], {"fixed": 5, "affected": 1, "unresolved": 4})
        self.assertEqual(self.ok("fix-versions", "PROJ", "--counts", "--json", "--select", "name,counts.unresolved").json()[1], {"name": "Release 2.0", "counts": {"unresolved": 4}})

    def test_the_bound_is_said_on_standard_error_and_leaves_the_rest_without_counts(self):
        many = [VersionInfo(str(100 + index), f"V{index}") for index in range(30)]
        self.gateway.ref_versions["PROJ"] = many
        self.gateway.ref_version_counts = {item.id: VersionCounts(1, 0, 0) for item in many}
        result = self.ok("fix-versions", "PROJ", "--counts", "--json")
        rows = result.json()
        self.assertEqual((sum(row["counts"] is not None for row in rows), len(rows)), (25, 30))
        self.assertIn("first 25 of 30 versions", result.err)

    def test_a_project_without_versions_and_an_unknown_one(self):
        self.assertEqual(self.ok("fix-versions", "DEMO").out, "No such versions in DEMO.\n")
        self.assertEqual(self.run_cli("fix-versions", "NOPE").code, 4)


class PermissionsTest(CommandCase):
    def test_what_you_may_do_as_yes_and_no(self):
        self.gateway.ref_denied = {"EDIT_ISSUES"}
        lines = self.ok("permissions", "--issue", "PROJ-1", "--only", "EDIT_ISSUES,transition issues").out.splitlines()
        self.assertEqual(lines[0].split(), ["KEY", "GRANTED", "NAME"])
        self.assertEqual([line.split()[:2] for line in lines[1:]], [["EDIT_ISSUES", "no"], ["TRANSITION_ISSUES", "yes"]])

    def test_json_has_the_key_the_name_the_type_and_the_answer(self):
        data = self.ok("permissions", "--project", "PROJ", "--only", "BROWSE_PROJECTS", "--json").json()
        self.assertEqual(data, [{"key": "BROWSE_PROJECTS", "name": "Browse Projects", "type": "PROJECT", "havePermission": True}])
        self.assertGreater(len(self.ok("permissions", "--json").json()), 5)

    def test_an_unknown_name_is_a_usage_error_that_lists_the_valid_names(self):
        result = self.run_cli("permissions", "--only", "EDIT_ISUES", "--json")
        self.assertEqual((result.code, result.error()["code"]), (2, "USAGE"))
        self.assertIn("did you mean EDIT_ISSUES", result.error()["message"])
        self.assertIn("TRANSITION_ISSUES", result.error()["hint"])

    def test_project_and_issue_together_and_unknown_scopes(self):
        self.assertEqual(self.run_cli("permissions", "--project", "PROJ", "--issue", "PROJ-1").code, 2)
        self.assertEqual(self.run_cli("permissions", "--project", "NOPE").code, 4)
        self.assertEqual(self.run_cli("permissions", "--issue", "PROJ-999").code, 4)

    def test_select_and_format(self):
        self.assertEqual(self.ok("permissions", "--only", "EDIT_ISSUES", "--select", "key,havePermission", "--format", "tsv").out.splitlines(), ["key\thavePermission", "EDIT_ISSUES\ttrue"])


class RolesTest(CommandCase):
    def test_the_roles_without_the_people(self):
        self.assertEqual(self.ok("roles", "PROJ").out.splitlines()[0].split(), ["ID", "NAME"])
        data = self.ok("roles", "PROJ", "--json").json()
        self.assertEqual(data, [{"id": "10002", "name": "Administrators", "actors": None}, {"id": "10001", "name": "Developers", "actors": None}])
        self.assertEqual(self.gateway.called("get_project_role"), [])

    def test_the_actors_with_with_users(self):
        data = self.ok("roles", "PROJ", "--with-users", "--json").json()
        self.assertEqual(data[1]["actors"], [{"name": "jdoe", "displayName": "Jane Doe", "kind": "user"}, {"name": "jira-developers", "displayName": "", "kind": "group"}])
        self.assertIn("jdoe, jira-developers", self.ok("roles", "PROJ", "--with-users").out)

    def test_the_policy_refuses_with_users(self):
        result = self.run_cli("roles", "PROJ", "--with-users", settings=settings(hide_users=True))
        self.assertEqual(result.code, 1)
        self.assertEqual(self.gateway.calls, [])

    def test_a_project_without_roles_and_an_unknown_one(self):
        self.assertEqual(self.ok("roles", "DEMO").out, "No roles in DEMO.\n")
        self.assertEqual(self.run_cli("roles", "NOPE").code, 4)


class DashboardsTest(CommandCase):
    def test_the_dashboards_with_their_address_and_the_total(self):
        data = self.ok("dashboards", "--json").json()
        self.assertEqual((data["total"], [row["id"] for row in data["dashboards"]]), (3, ["10000", "10001", "10002"]))
        self.assertEqual(data["dashboards"][1], {"id": "10001", "name": "Team Dashboard", "description": "What the team looks at.", "view": "https://jira.example.com/secure/Dashboard.jspa?selectPageId=10001"})
        lines = self.ok("dashboards").out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "NAME", "DESCRIPTION", "VIEW"])

    def test_the_limit_and_what_is_left(self):
        result = self.ok("dashboards", "--limit", "1")
        self.assertTrue(result.out.rstrip().endswith("Showing 1 of 3."))
        self.assertEqual(self.ok("dashboards", "--json", "--select", "name", "--limit", "2").json()["dashboards"], [{"name": "System Dashboard"}, {"name": "Team Dashboard"}])

    def test_mine_and_favourite_narrow_the_list_and_do_not_go_together(self):
        self.assertEqual([row["id"] for row in self.ok("dashboards", "--mine", "--json").json()["dashboards"]], ["10002"])
        self.assertEqual([row["id"] for row in self.ok("dashboards", "--favourite", "--json").json()["dashboards"]], ["10001"])
        self.assertIn("Team Dashboard", self.ok("dashboards", "--favourite").out)
        self.gateway.calls.clear()
        result = self.run_cli("dashboards", "--mine", "--favourite", "--json")
        self.assertEqual((result.code, result.error()["code"]), (2, "USAGE"))
        self.assertIn("--mine or --favourite", result.error()["message"])
        self.assertEqual(self.gateway.calls, [])

    def test_the_option_filter_is_not_there_and_a_limit_of_zero_is_refused(self):
        self.assertEqual(self.run_cli("dashboards", "--filter", "my").code, 2)  # `--filter` names a saved filter in `issues` and `search`
        self.assertEqual(self.run_cli("dashboards", "--limit", "0").code, 2)

    def test_the_description_column_is_left_out_when_nobody_has_one(self):
        self.gateway.ref_dashboards = [dataclasses.replace(item, description="") for item in self.gateway.ref_dashboards]
        self.assertEqual(self.ok("dashboards").out.splitlines()[0].split(), ["ID", "NAME", "VIEW"])


class EveryListTest(CommandCase):
    LISTS = (
        (("priorities",), "id"),
        (("resolutions",), "id"),
        (("issue-types", "--project", "PROJ"), "id"),
        (("components", "PROJ"), "id"),
        (("fix-versions", "PROJ"), "id"),
        (("permissions", "--only", "EDIT_ISSUES,BROWSE_PROJECTS"), "key"),
        (("roles", "PROJ"), "id"),
    )

    def test_each_list_takes_select_and_format_like_every_list_of_the_cli(self):
        for argv, column in self.LISTS:
            with self.subTest(argv=argv):
                rows = self.ok(*argv, "--json").json()
                self.assertEqual([list(row) for row in self.ok(*argv, "--json", "--select", column).json()], [[column]] * len(rows))
                tsv = self.ok(*argv, "--format", "tsv", "--select", column).out.splitlines()
                self.assertEqual((tsv[0], len(tsv) - 1), (column, len(rows)))
                self.assertTrue(self.ok(*argv, "--format", "md").out.startswith("| "))
                self.assertEqual(self.run_cli(*argv, "--select", "nope").code, 2)
                self.assertEqual(self.run_cli(*argv, "--json", "--format", "tsv").code, 2)

    def test_the_lists_that_are_wrapped_in_an_object_keep_their_other_keys_when_selected(self):
        shown = self.ok("dashboards", "--json", "--select", "id").json()
        self.assertEqual((shown["total"], shown["dashboards"][0]), (3, {"id": "10000"}))
        statuses = self.ok("statuses", "--json", "--select", "name").json()
        self.assertEqual((statuses["doneStatuses"], statuses["statuses"][0]), (["Closed", "Done"], {"name": "To Do"}))


class ServerTest(CommandCase):
    def test_the_text_says_version_verdict_and_settings(self):
        out = self.ok("server").out
        for pattern in (r"Jira:\s+11\.3\.12 \(Server\)\n", r"Base URL:\s+https://jira\.example\.com\n", r"Compatibility:\s+ok: Jira 11\.3\.12 is on the 11\.3\.x release line",
                        r"Attachments:\s+on, upload limit 10 MiB\n", r"Sub-tasks:\s+on\n", r"Issue linking:\s+on\n", r"Voting:\s+on\n", r"Watching:\s+on\n",
                        r"Unassigned:\s+not allowed\n", r"Time tracking:\s+on \(8 hours a day, 5 days a week, format pretty, default unit minute\)\n"):
            self.assertRegex(out, pattern)

    def test_the_json_keys(self):
        self.gateway.server_info = lambda: {"version": "11.3.12", "deploymentType": "Server", "buildNumber": 11003012}
        data = self.ok("server", "--json").json()
        self.assertEqual(set(data), {"baseUrl", "version", "deploymentType", "buildNumber", "compatibility", "settings"})
        self.assertEqual((data["baseUrl"], data["buildNumber"]), ("https://jira.example.com", 11003012))
        self.assertEqual(data["compatibility"], {"status": "ok", "detail": data["compatibility"]["detail"], "hint": ""})
        self.assertEqual(
            data["settings"],
            {
                "attachmentsEnabled": True, "attachmentUploadLimit": 10485760, "subTasksEnabled": True, "issueLinkingEnabled": True, "votingEnabled": True,
                "watchingEnabled": True, "unassignedIssuesAllowed": False, "timeTrackingEnabled": True,
                "timeTracking": {"workingHoursPerDay": 8.0, "workingDaysPerWeek": 5.0, "timeFormat": "pretty", "defaultUnit": "minute"},
            },
        )

    def test_another_release_line_is_shown_with_its_hint_and_is_no_failure(self):
        self.gateway.server_info = lambda: {"version": "1001.0.0", "deploymentType": "Cloud"}
        out = self.ok("server").out
        self.assertIn("Compatibility: warn: Jira Cloud 1001.0.0", out)
        self.assertIn("Hint:", out)
        self.assertEqual(self.ok("server", "--json").json()["compatibility"]["status"], "warn")

    def test_what_is_off_is_said_and_what_is_unknown_too(self):
        self.gateway.ref_server_settings = dataclasses.replace(self.gateway.ref_server_settings, voting_enabled=False, time_tracking_enabled=None, time_tracking=None, sub_tasks_enabled=None)
        self.gateway.ref_attachment_upload_limit = None
        out = self.ok("server").out
        self.assertRegex(out, r"Voting:\s+off\n")
        self.assertRegex(out, r"Sub-tasks:\s+unknown\n")
        self.assertRegex(out, r"Time tracking:\s+unknown\n")
        self.assertRegex(out, r"Attachments:\s+on\n")

    def test_the_server_leaves_the_upload_limit_out_when_it_cannot_be_read(self):
        self.gateway.errors["get_attachment_upload_limit"] = AuthError("refused")
        result = self.ok("server", "--json")
        self.assertIsNone(result.json()["settings"]["attachmentUploadLimit"])
        self.assertIn("upload limit", result.err)

    def test_the_call_that_is_needed_fails_the_command(self):
        self.gateway.errors["get_server_settings"] = AuthError("Jira refused this request (HTTP 401).")
        self.assertEqual(self.run_cli("server").code, 5)

    def test_it_is_in_the_group_of_setup_and_information_and_the_rest_in_reference(self):
        from jira_dc_cli.adapters.driving.cli.registry import find_command

        self.assertEqual(find_command("server").group, "Setup and information")
        for name in ("priorities", "resolutions", "issue-types", "statuses", "components", "fix-versions", "permissions", "roles", "dashboards"):
            self.assertEqual(find_command(name).group, "Reference", name)
            self.assertFalse(find_command(name).writes, name)
        self.assertFalse(find_command("server").writes)


if __name__ == "__main__":
    unittest.main()
