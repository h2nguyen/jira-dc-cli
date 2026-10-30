"""The whole program for the reference area: the command line, the real adapters and the mock Jira, with no fakes."""
import unittest

from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_reference import PERMISSIONS, reference


class ReferenceFlow(EndToEnd):
    def setUp(self):
        super().setUp()
        self.reference = reference(self.jira)
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))

    def gets(self, prefix):
        return [item for item in self.jira.sent("GET", prefix)]

    def paths(self):
        return [item.path.removeprefix("/rest/api/2") for item in self.jira.requests if item.method == "GET"]

    def test_priorities_resolutions_issue_types_and_statuses(self):
        self.assertEqual([row["name"] for row in self.cli("priorities", "--json").json()], ["Highest", "High", "Medium", "Low"])
        self.assertEqual([row["name"] for row in self.cli("resolutions", "--json").json()], ["Done", "Won't Do", "Duplicate"])
        types = self.cli("issue-types", "--json").json()
        self.assertEqual((types[3]["name"], types[3]["subtask"]), ("Sub-task", True))
        statuses = self.cli("statuses", "--json").json()
        self.assertEqual((statuses["doneStatuses"], statuses["statuses"][1]["category"]), (["Closed", "Done"], "indeterminate"))
        self.assertEqual(self.paths(), ["/priority", "/resolution", "/issuetype", "/status"])

    def test_the_types_and_statuses_of_a_project(self):
        self.assertEqual([row["name"] for row in self.cli("issue-types", "--project", "PROJ", "--json").json()], ["Epic", "Story", "Bug"])
        self.assertEqual(self.paths(), ["/project/PROJ"])
        data = self.cli("statuses", "--project", "PROJ", "--json").json()
        self.assertEqual([item["name"] for item in data["issueTypes"]], ["Story", "Bug"])
        self.assertEqual([status["name"] for status in data["issueTypes"][1]["statuses"]], ["To Do", "In Progress", "Done", "Closed"])
        self.assertEqual(self.cli("statuses", "--project", "DEMO", "--json").json()["issueTypes"], [])

    def test_the_lead_of_a_component_is_a_person_and_is_shown_on_request_only(self):
        plain = self.cli("components", "PROJ", "--json")
        self.assertEqual([(row["name"], row["lead"]) for row in plain.json()], [("Backend", None), ("Frontend", None)])
        for personal in ("jdoe", "Jane", "jane.doe"):
            self.assertNotIn(personal, plain.out)
        shown = self.cli("components", "PROJ", "--with-users", "--json").json()
        self.assertEqual((shown[0]["lead"]["name"], shown[1]["lead"]), ("jdoe", None))
        refused = self.cli("components", "PROJ", "--with-users", env={"JIRA_HIDE_USERS": "true"})
        self.assertEqual(refused.code, 1)
        self.assertEqual(self.cli("components", "PROJ", "--json", env={"JIRA_HIDE_USERS": "true"}).json()[0]["lead"], None)

    def test_versions_with_counts_come_from_the_versions_that_the_jira_keeps(self):
        self.jira.versions["PROJ"].append("Release 3.0")
        names = [row["name"] for row in self.cli("fix-versions", "PROJ", "--json").json()]
        self.assertEqual(names, ["Release 1.0", "Release 2.0", "Release 3.0"])
        self.assertEqual([row["name"] for row in self.cli("fix-versions", "PROJ", "--released", "--json").json()], ["Release 1.0"])
        self.jira.reset_requests()
        counted = self.cli("fix-versions", "PROJ", "--unreleased", "--counts", "--json").json()
        self.assertEqual([(row["name"], row["counts"]) for row in counted][0], ("Release 2.0", {"fixed": 5, "affected": 1, "unresolved": 4}))
        self.assertEqual(len(self.gets("/rest/api/2/version/")), 4)  # two questions for each of the two unreleased versions
        self.assertEqual(self.cli("fix-versions", "DEMO").out, "No such versions in DEMO.\n")

    def test_a_version_list_of_the_mock_is_still_what_the_other_commands_read(self):
        self.jira.versions["PROJ"].append("Release 3.0")
        self.cli("fix-versions", "PROJ")
        self.assertEqual(self.jira.versions["PROJ"], ["Release 1.0", "Release 2.0", "Release 3.0"])
        self.assertEqual(self.cli("api", "GET", "project/PROJ/versions").json()[0]["name"], "Release 1.0")

    def test_permissions_in_a_project_on_an_issue_and_in_general(self):
        self.reference.denied.add("EDIT_ISSUES")
        data = self.cli("permissions", "--issue", "PROJ-1", "--only", "EDIT_ISSUES,TRANSITION_ISSUES", "--json").json()
        self.assertEqual([(row["key"], row["havePermission"]) for row in data], [("EDIT_ISSUES", False), ("TRANSITION_ISSUES", True)])
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/mypermissions")[0].query, {"issueKey": ["PROJ-1"]})
        self.cli("permissions", "--project", "PROJ")
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/mypermissions")[1].query, {"projectKey": ["PROJ"]})
        self.assertEqual(len(self.cli("permissions", "--json").json()), len(PERMISSIONS))
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/mypermissions")[2].query, {})

    def test_the_project_administrator_check_of_projects_admin_is_still_answered_as_before(self):
        self.assertEqual(self.cli("permissions", "--project", "DEMO", "--only", "ADMINISTER_PROJECTS", "--json").json()[0]["havePermission"], False)
        self.assertEqual(self.cli("permissions", "--project", "PROJ", "--only", "ADMINISTER_PROJECTS", "--json").json()[0]["havePermission"], True)
        result = self.cli("projects-admin", "--quiet")
        self.assertIn("PROJ", result.out)
        self.assertNotIn("DEMO", result.out)

    def test_an_unknown_permission_name_lists_the_names_that_jira_sent(self):
        result = self.cli("permissions", "--only", "EDIT_ISUES", "--json")
        self.assertEqual((result.code, result.error()["code"]), (2, "USAGE"))
        self.assertIn("did you mean EDIT_ISSUES", result.error()["message"])
        for key, _, _ in PERMISSIONS:
            self.assertIn(key, result.error()["hint"])

    def test_the_roles_cost_one_request_and_the_people_in_them_one_more_for_each_role(self):
        data = self.cli("roles", "PROJ", "--json").json()
        self.assertEqual(data, [{"id": "10002", "name": "Administrators", "actors": None}, {"id": "10001", "name": "Developers", "actors": None}])
        self.assertEqual(self.paths(), ["/project/PROJ/role"])
        shown = self.cli("roles", "PROJ", "--with-users", "--json").json()
        self.assertEqual(shown[1]["actors"], [{"name": "jdoe", "displayName": "Jane Doe", "kind": "user"}, {"name": "jira-developers", "displayName": "jira-developers", "kind": "group"}])
        self.assertEqual(self.paths()[1:], ["/project/PROJ/role", "/project/PROJ/role/10002", "/project/PROJ/role/10001"])

    def test_dashboards_are_asked_for_again_while_the_server_answers_less_than_asked(self):
        data = self.cli("dashboards", "--limit", "3", "--json").json()
        self.assertEqual((data["total"], len(data["dashboards"])), (3, 3))
        self.assertEqual([item.query["startAt"] for item in self.jira.sent("GET", "/rest/api/2/dashboard")], [["0"], ["2"]])
        self.assertNotIn("filter", self.jira.sent("GET", "/rest/api/2/dashboard")[0].query)  # without a flag Jira is not asked to filter
        self.assertTrue(data["dashboards"][0]["view"].startswith(self.jira.base_url))

    def test_mine_and_favourite_are_the_filter_that_jira_knows_and_do_not_go_together(self):
        self.assertEqual([row["id"] for row in self.cli("dashboards", "--mine", "--json").json()["dashboards"]], ["10002"])
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/dashboard")[-1].query["filter"], ["my"])
        self.assertEqual([row["id"] for row in self.cli("dashboards", "--favourite", "--json").json()["dashboards"]], ["10001"])
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/dashboard")[-1].query["filter"], ["favourite"])
        asked = len(self.jira.requests)
        result = self.cli("dashboards", "--mine", "--favourite", "--json")
        self.assertEqual((result.code, result.error()["code"]), (2, "USAGE"))
        self.assertEqual(len(self.jira.requests), asked)  # nothing was asked of Jira

    def test_the_server_command_shows_the_address_as_configured_and_asks_three_things(self):
        data = self.cli("server", "--json").json()
        self.assertEqual((data["baseUrl"], data["version"], data["buildNumber"]), (self.jira.base_url, "11.3.12", 11003012))
        self.assertGreater(data["settings"]["attachmentUploadLimit"], 0)  # from the attachment capabilities, which the mock of the attachments answers
        self.assertEqual(data["compatibility"]["status"], "ok")
        self.assertEqual(sorted(set(self.paths())), ["/attachment/meta", "/configuration", "/serverInfo"])
        self.assertNotIn("jira.example.com", self.cli("server").out)

    def test_another_release_line_is_a_warning_and_a_jira_without_build_number_has_none(self):
        self.reference.server_info = {"version": "9.4.1", "deploymentType": "Server"}
        self.reference.configuration["attachmentsEnabled"] = False
        data = self.cli("server", "--json").json()
        self.assertEqual((data["compatibility"]["status"], data["buildNumber"], data["settings"]["attachmentsEnabled"]), ("warn", None, False))
        self.reference.server_info = {"version": "1001.0.0", "deploymentType": "Cloud"}
        self.assertIn("Jira Cloud", self.cli("server").out)

    def test_a_missing_upload_limit_call_is_a_warning_and_no_failure(self):
        self.jira.fail_next("/rest/api/2/attachment/meta", 403, body={"errorMessages": ["no"]})
        result = self.cli("server", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertIsNone(result.json()["settings"]["attachmentUploadLimit"])
        self.assertIn("upload limit", result.err)

    def test_the_errors_of_the_real_http_stack(self):
        self.assertEqual(self.cli("components", "NOPE").code, 4)
        self.assertIn("No project could be found with key 'NOPE'", self.cli("components", "NOPE").err)
        self.reference.issue_types_for_administrators_only = True
        refused = self.cli("issue-types", "--json")
        self.assertEqual((refused.code, refused.error()["code"]), (5, "AUTH"))
        self.assertIn("issue-types --project KEY", refused.error()["hint"])
        self.assertEqual(self.cli("issue-types", "--project", "PROJ").code, 0)
        self.jira.fail_next("/rest/api/2/priority", 403, body={"errorMessages": ["Nope."]})
        self.assertEqual(self.cli("priorities").code, 5)
        self.jira.fail_next("/rest/api/2/resolution", 503, times=2)
        self.assertEqual(self.cli("resolutions").code, 0)  # a read is retried
        self.assertEqual(self.cli("permissions", "--issue", "PROJ-999").code, 4)

    def test_a_wrong_token_is_an_auth_error_for_every_command(self):
        self.write_config(self.jira.base_url, token="wrong")
        for argv in (("priorities",), ("server",), ("permissions",), ("roles", "PROJ")):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, 5)


if __name__ == "__main__":
    unittest.main()
