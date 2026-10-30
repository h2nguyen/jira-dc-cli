"""Validation through the whole program: the dry run of create, update and update-json against the mock's metadata, and the transition with fields."""
import unittest
from unittest import mock

from jira_dc_cli.bootstrap import create_application
from jira_dc_cli.domain.drafts import FRESH_LIST, IssueChanges, IssueDraft
from jira_dc_cli.domain.errors import RejectedError
from tests.integration.test_end_to_end import EndToEnd
from tests.support import mock_jira
from tests.support.fakes import RecordingDiagnostics
from tests.support.mock_users import routes as user_routes
from tests.support.mock_validation import field_meta, resolution_field, routes

CREATE_META = "/rest/api/2/issue/createmeta/PROJ/issuetypes/10200"
EDIT_META = "/rest/api/2/issue/PROJ-1/editmeta"
TRANSITIONS = "/rest/api/2/issue/PROJ-2/transitions"


class ValidationFlowTest(EndToEnd):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))  # every call is one that the 11.3 reference documents
        self.mock = routes(self.jira)


class CreateDryRunTest(ValidationFlowTest):
    def test_a_wrong_priority_is_exit_6_and_the_hint_lists_what_the_project_offers(self):
        result = self.cli("create", "--type", "Story", "--summary", "S", "--priority", "Nope", "--dry-run", "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"], error["exitCode"]), (6, "REJECTED", 6))
        self.assertEqual(error["message"], "priority 'Nope' is not available in PROJ for Story (10200).")
        self.assertEqual(error["hint"], "Allowed priorities: Highest, High, Medium, Low, Lowest.")
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue"), [])

    def test_the_metadata_is_the_create_metadata_of_the_type_and_it_is_asked_for_once(self):
        self.cli("create", "--type", "Story", "--summary", "S", "--priority", "High", "--dry-run")
        (request,) = self.jira.sent("GET", CREATE_META)
        self.assertEqual(request.query, {"startAt": ["0"], "maxResults": ["1000"]})

    def test_a_valid_payload_passes_unchanged_and_has_no_warning(self):
        result = self.cli("create", "--type", "Story", "--summary", "S", "--priority", "High", "--components", "Demo", "--fix-versions", "Release 1.0", "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(data["payload"]["fields"]["fixVersions"], [{"name": "Release 1.0"}])
        self.assertEqual((data["warnings"], result.err), ([], ""))

    def test_a_component_or_a_version_that_the_project_does_not_have(self):
        result = self.cli("create", "--type", "Story", "--summary", "S", "--components", "Nowhere", "--fix-versions", "Release 9", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed components: Demo, Backend. Allowed fix versions: Release 1.0, Release 2.0.", result.err)

    def test_a_project_without_components_allows_none(self):
        result = self.cli("create", "--project", "DEMO", "--type", "Story", "--summary", "S", "--components", "Demo", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed components: none.", result.err)

    def test_a_required_custom_field_that_is_left_out_is_only_a_warning(self):
        self.mock.required_to_create["10000"] = {"customfield_10005": field_meta("customfield_10005", "Epic Name", required=True)}
        result = self.cli("create", "--type", "Epic", "--summary", "S", "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(len(result.json()["warnings"]), 1)
        self.assertIn("WARNING: Epic Name (customfield_10005) is required in PROJ for Epic (10000)", result.err)
        given = self.cli("create", "--type", "Epic", "--summary", "S", "--fields-json", '{"customfield_10005": "Billing"}', "--dry-run")
        self.assertEqual((given.code, given.err), (0, ""))

    def test_an_issue_type_id_that_the_project_does_not_have_is_exit_6(self):
        result = self.cli("create", "--type-id", "99999", "--summary", "S", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Available: Bug (10201), Epic (10000), Story (10200).", result.err)

    def test_the_issue_type_by_name_costs_no_second_request_for_the_types(self):
        self.cli("create", "--type", "Story", "--summary", "S", "--dry-run")
        listings = [item for item in self.jira.requests if item.path == "/rest/api/2/issue/createmeta/PROJ/issuetypes"]
        self.assertEqual(len(listings), 1)

    def test_a_live_create_is_not_checked_and_asks_for_no_field_metadata(self):
        result = self.cli("create", "--type", "Story", "--summary", "Live", "--priority", "Nope")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.jira.sent("GET", CREATE_META), [])
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue")[0].body["fields"]["priority"], {"name": "Nope"})

    def test_create_json_is_checked_when_it_names_the_project_and_the_type(self):
        self.write_payload("bad.json", '{"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S", "priority": {"name": "Nope"}}}')
        result = self.cli("create-json", str(self.folder / "bad.json"), "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed priorities", result.err)

    def write_payload(self, name, text):
        (self.folder / name).write_text(text, encoding="utf-8")


class UpdateDryRunTest(ValidationFlowTest):
    def test_a_wrong_priority_is_exit_6_with_the_allowed_ones(self):
        result = self.cli("update", "PROJ-1", "--priority", "Urgent", "--dry-run", "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"]), (6, "REJECTED"))
        self.assertEqual(error["hint"], "Allowed priorities: Highest, High, Medium, Low, Lowest.")
        self.assertEqual(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1"), [])

    def test_the_metadata_is_the_edit_metadata_of_the_issue(self):
        self.cli("update", "PROJ-1", "--priority", "Low", "--dry-run")
        self.assertEqual(len(self.jira.sent("GET", EDIT_META)), 1)

    def test_a_valid_dry_run_is_what_it_was(self):
        result = self.cli("update", "PROJ-1", "--priority", "Low", "--labels", "x", "--dry-run", "--if-unchanged-since", "2026-10-10T07:00:00Z")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Dry run: PROJ-1 exists and is unchanged since 2026-10-10T07:00:00Z; nothing was sent", result.out)
        self.assertEqual(result.err, "")

    def test_the_issue_not_found_comes_first(self):
        self.assertEqual(self.cli("update", "PROJ-999", "--priority", "Urgent", "--dry-run").code, 4)
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/issue/PROJ-999/editmeta"), [])

    def test_update_json_with_the_update_syntax_is_checked(self):
        (self.folder / "p.json").write_text('{"update": {"components": [{"add": {"name": "Nowhere"}}]}}', encoding="utf-8")
        result = self.cli("update-json", "PROJ-1", str(self.folder / "p.json"), "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("component 'Nowhere'", result.err)
        (self.folder / "ok.json").write_text('{"update": {"components": [{"add": {"name": "Demo"}}], "labels": [{"add": "x"}]}}', encoding="utf-8")
        self.assertEqual(self.cli("update-json", "PROJ-1", str(self.folder / "ok.json"), "--dry-run").code, 0)

    def test_a_live_update_is_not_checked(self):
        self.assertEqual(self.cli("update", "PROJ-1", "--priority", "Urgent").code, 0)
        self.assertEqual(self.jira.sent("GET", EDIT_META), [])
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["priority"], {"name": "Urgent"})

    def test_an_account_that_may_not_edit_is_exit_5_in_the_dry_run_too(self):
        self.jira.fail_next(EDIT_META, 403, body={"errorMessages": ["You do not have permission to edit this issue."]})
        result = self.cli("update", "PROJ-1", "--priority", "Low", "--dry-run", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))


class TransitionFieldsTest(ValidationFlowTest):
    def setUp(self):
        super().setUp()
        self.mock.screens["81"] = {"resolution": resolution_field(), "fixVersions": field_meta("fixVersions", "Fix Version/s", allowed=[{"id": "1", "name": "Release 1.0"}])}

    def test_show_fields_asks_for_the_screens_and_lists_them(self):
        result = self.cli("transition", "PROJ-2", "--show-fields", "--json")
        self.assertEqual(result.code, 0, result.err)
        (request,) = self.jira.sent("GET", TRANSITIONS)
        self.assertEqual(request.query, {"expand": ["transitions.fields"]})
        data = result.json()
        self.assertEqual([item["id"] for item in data["screens"]], ["111", "81"])
        finish = data["screens"][1]
        self.assertEqual([field["id"] for field in finish["fields"]], ["resolution", "fixVersions"])
        self.assertEqual(finish["fields"][0], {"id": "resolution", "name": "Resolution", "required": True, "allowed": ["Fixed", "Won't Fix", "Duplicate"], "allowedCount": 3})
        self.assertEqual(data["screens"][0]["fields"], [])

    def test_show_fields_as_text(self):
        out = self.cli("transition", "PROJ-2", "--to", "Done", "--show-fields").out
        self.assertIn("resolution", out)
        self.assertIn("required", out)
        self.assertIn("Fixed, Won't Fix, Duplicate", out)
        self.assertNotIn("Reopen", out)

    def test_the_dry_run_with_a_resolution_and_fields_shows_the_payload(self):
        result = self.cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Fixed", "--comment", "Done.", "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json()["payload"], {"transition": {"id": "81"}, "fields": {"resolution": {"name": "Fixed"}}, "update": {"comment": [{"add": {"body": "Done."}}]}})
        self.assertEqual(self.jira.sent("POST", TRANSITIONS), [])

    def test_a_resolution_that_the_screen_does_not_allow_is_exit_6(self):
        result = self.cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Whatever", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed resolutions: Fixed, Won't Fix, Duplicate.", result.err)

    def test_a_field_that_the_screen_does_not_have_is_exit_6_and_names_the_screen(self):
        result = self.cli("transition", "PROJ-2", "--to", "Done", "--fields-json", '{"components": [{"name": "Demo"}]}', "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("The screen has these fields: resolution, fixVersions.", result.err)

    def test_a_required_field_that_is_missing_is_a_warning(self):
        result = self.cli("transition", "PROJ-2", "--to", "Done", "--dry-run")
        self.assertEqual(result.code, 0)
        self.assertIn("WARNING: Resolution (resolution) is required for the transition 'Finish' (81) of PROJ-2", result.err)

    def test_a_live_transition_sends_the_fields_and_asks_for_no_screens(self):
        result = self.cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Fixed")
        self.assertEqual(result.code, 0, result.err)
        (sent,) = self.jira.sent("POST", TRANSITIONS)
        self.assertEqual(sent.body, {"transition": {"id": "81"}, "fields": {"resolution": {"name": "Fixed"}}})
        self.assertTrue(all("expand" not in item.query for item in self.jira.sent("GET", TRANSITIONS)))
        self.assertEqual(self.jira.issues["PROJ-2"]["fields"]["status"]["name"], "Done")

    def test_a_stale_issue_is_exit_3_and_nothing_is_sent(self):
        result = self.cli("transition", "PROJ-2", "--to", "Done", "--if-unchanged-since", "2026-10-01T00:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.jira.sent("POST", TRANSITIONS), [])
        self.assertEqual(self.cli("transition", "PROJ-2", "--to", "Done", "--if-unchanged-since", "2026-10-10T07:00:00Z").code, 0)
        self.assertEqual(self.jira.issues["PROJ-2"]["fields"]["status"]["name"], "Done")

    def test_jira_refusing_a_transition_is_exit_6_as_before(self):
        self.jira.fail_next(TRANSITIONS, 400, method="POST", body={"errorMessages": [], "errors": {"resolution": "Resolution is required."}})
        result = self.cli("transition", "PROJ-2", "--to", "Done")
        self.assertEqual(result.code, 6)
        self.assertIn("resolution", result.err)

    def test_without_screens_the_plain_listing_and_the_plain_dry_run_still_work(self):
        self.assertIn("Finish", self.cli("transition", "PROJ-2").out)
        self.mock.screens.clear()
        dry = self.cli("transition", "PROJ-1", "--to", "In Progress", "--dry-run")
        self.assertEqual((dry.code, dry.err), (0, ""))
        self.assertEqual(self.cli("transition", "PROJ-1", "--to", "Archived", "--dry-run").code, 1)


class StandInFlowTest(ValidationFlowTest):
    """What an agent does: read the issue (people hidden), edit the text, send it back. The real mention in Jira must survive."""

    def stored(self):
        return self.jira.issues["PROJ-1"]["fields"]["description"]

    def puts(self):
        return self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1")

    def test_the_text_that_issue_showed_cannot_be_sent_back_and_the_real_mention_stays(self):
        read = self.cli("issue", "PROJ-1", "--json").json()["description"]
        self.assertEqual(read, "Ask [~user] or [email]\nSecond line")
        result = self.cli("update", "PROJ-1", "--description", read + "\nThird line", "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"], error["exitCode"]), (1, "INVALID", 1))
        self.assertIn("The new description holds [~user], [email]", error["message"])
        self.assertIn("edited in Jira itself", error["hint"])
        self.assertEqual((self.puts(), self.stored()), ([], "Ask [~jdoe] or jane.doe@example.com\nSecond line"))
        (asked,) = [item for item in self.jira.sent("GET", "/rest/api/2/issue/PROJ-1") if item.query.get("fields") == ["summary,updated,description"]]
        self.assertEqual(asked.query["fields"], ["summary,updated,description"])  # one read, for the text and nothing else

    def test_the_dry_run_and_update_json_refuse_it_too(self):
        (self.folder / "p.json").write_text('{"fields": {"description": "Ask [~user] or [email]\\nSecond line\\nThird"}}', encoding="utf-8")
        for argv in (("update", "PROJ-1", "--description", "Ask [~user]", "--dry-run"), ("update-json", "PROJ-1", str(self.folder / "p.json")), ("update-json", "PROJ-1", str(self.folder / "p.json"), "--dry-run")):
            with self.subTest(argv=argv):
                result = self.cli(*argv)
                self.assertEqual(result.code, 1, result.err)
                self.assertIn("which stands in place of a mention or an address", result.err)
        self.assertEqual((self.puts(), self.stored()), ([], "Ask [~jdoe] or jane.doe@example.com\nSecond line"))

    def test_a_real_mention_and_a_text_without_people_are_sent_and_cost_no_extra_request(self):
        result = self.cli("update", "PROJ-1", "--description", "Ask [~jdoe] about it", "--summary", "Plain")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.puts()[0].body, {"fields": {"summary": "Plain", "description": "Ask [~jdoe] about it"}})
        self.assertEqual(self.stored(), "Ask [~jdoe] about it")
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/issue/PROJ-1"), [])

    def test_a_literal_that_jira_holds_already_may_stay(self):
        self.jira.issues["PROJ-1"]["fields"]["description"] = "Write to [email] for details"
        self.assertEqual(self.cli("update", "PROJ-1", "--description", "Write to [email] today").code, 0)
        self.assertEqual(self.stored(), "Write to [email] today")

    def test_the_acceptance_criteria_are_judged_by_the_field_that_jira_names(self):
        result = self.cli("update", "PROJ-1", "--fields-json", '{"customfield_10004": "GIVEN [~user]"}', "--json")
        self.assertEqual(result.code, 1)
        self.assertIn("The new acceptance criteria holds [~user]", result.error()["message"])
        self.assertEqual(self.puts(), [])

    def test_a_new_issue_with_a_stand_in_gets_a_note_in_the_dry_run_and_is_created_as_written(self):
        dry = self.cli("create", "--type", "Story", "--summary", "S", "--description", "Ask [~user]", "--dry-run", "--json")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertEqual(len(dry.json()["warnings"]), 1)
        self.assertIn("WARNING: The description of the new issue holds [~user]", dry.err)
        live = self.cli("create", "--type", "Story", "--summary", "S", "--description", "Ask [~user]", "--json")
        self.assertEqual((live.code, live.json()["warnings"]), (0, []))

    # -- labels, components and fix versions --------------------------------------------------------------------------------------------

    def fields(self, key="PROJ-2"):
        return self.jira.issues[key]["fields"]

    def test_the_labels_that_issue_showed_cannot_be_sent_back_and_the_real_label_stays(self):
        self.fields()["labels"] = ["jane.doe@example.com", "backend"]
        read = self.cli("issue", "PROJ-2", "--json").json()["labels"]
        self.assertEqual(read, ["[email]", "backend"])  # what an agent reads
        result = self.cli("update", "PROJ-2", "--labels", ",".join([*read, "new"]), "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"], error["details"]["fields"]), (1, "INVALID", ["labels"]))
        self.assertIn("The new labels hold [email]", error["message"])
        self.assertEqual((self.jira.sent("PUT", "/rest/api/2/issue/PROJ-2"), self.fields()["labels"]), ([], ["jane.doe@example.com", "backend"]))
        (asked,) = [item for item in self.jira.sent("GET", "/rest/api/2/issue/PROJ-2") if item.query.get("fields") == ["summary,updated,labels"]]
        self.assertEqual(asked.query["fields"], ["summary,updated,labels"])  # one read, for the labels and nothing else

    def test_the_components_and_the_fix_versions_and_the_update_json_forms_are_refused_too(self):
        (self.folder / "add.json").write_text('{"update": {"labels": [{"add": "[email]"}]}}', encoding="utf-8")
        (self.folder / "set.json").write_text('{"fields": {"fixVersions": [{"name": "Release 1.0"}, {"name": "[~user]"}]}}', encoding="utf-8")
        for argv in (
            ("update", "PROJ-2", "--components", "Demo,[~user]"),
            ("update", "PROJ-2", "--fix-versions", "Release [personal link]", "--dry-run"),
            ("update", "PROJ-2", "--fields-json", '{"labels": ["[email]"]}'),
            ("update-json", "PROJ-2", str(self.folder / "add.json")),
            ("update-json", "PROJ-2", str(self.folder / "set.json"), "--dry-run"),
        ):
            with self.subTest(argv=argv):
                result = self.cli(*argv)
                self.assertEqual(result.code, 1, result.err)
                self.assertIn("which stands in place of a mention or an address", result.err)
        self.assertEqual(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-2"), [])
        self.assertEqual(self.fields()["labels"], ["backend"])

    def test_a_label_that_jira_holds_may_stay_and_ordinary_names_cost_no_extra_request(self):
        self.fields()["labels"] = ["[email]", "backend"]
        self.assertEqual(self.cli("update", "PROJ-2", "--labels", "[email],backend,new").code, 0)
        self.assertEqual(self.fields()["labels"], ["[email]", "backend", "new"])
        self.jira.reset_requests()
        self.assertEqual(self.cli("update", "PROJ-2", "--labels", "a,b", "--components", "Demo", "--fix-versions", "Release 1.0").code, 0)
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/issue/PROJ-2"), [])

    def test_a_new_issue_with_a_label_that_is_a_stand_in_gets_a_note(self):
        dry = self.cli("create", "--type", "Story", "--summary", "S", "--labels", "[email],backend", "--dry-run", "--json")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertIn("The labels of the new issue hold [email]", dry.json()["warnings"][0])

    # -- the way out that the hint names -----------------------------------------------------------------------------------------------

    def test_the_raw_call_that_the_hint_names_writes_what_update_refuses(self):
        refused = self.cli("update", "PROJ-2", "--summary", "[email] Add retry", "--json")
        self.assertEqual(refused.code, 1)
        hint = refused.error()["hint"]
        self.assertIn("`jira api PUT issue/PROJ-2 --data-file FILE --write` (the raw call has no guard)", hint)
        self.assertEqual(self.fields()["summary"], "Fix the thing")
        (self.folder / "payload.json").write_text('{"fields": {"summary": "[email] Add retry", "labels": ["[email]"]}}', encoding="utf-8")
        done = self.cli("api", "PUT", "issue/PROJ-2", "--data-file", str(self.folder / "payload.json"), "--write")  # the command of the hint
        self.assertEqual(done.code, 0, done.err)
        self.assertEqual((self.fields()["summary"], self.fields()["labels"]), ("[email] Add retry", ["[email]"]))
        self.assertEqual(len(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-2")), 1)

    # -- the fields of a transition ----------------------------------------------------------------------------------------------------

    def test_a_transition_with_stand_ins_in_its_fields_is_refused_and_nothing_is_posted(self):
        fields = '{"description": "x [~user] [email]", "labels": ["[email]"]}'
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra=extra):
                result = self.cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", fields, *extra, "--json")
                error = result.error()
                self.assertEqual((result.code, error["code"], error["details"]["fields"]), (1, "INVALID", ["description", "labels"]))
                self.assertIn("The new description and labels hold [~user], [email]", error["message"])
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/transitions"), [])
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["status"]["name"], "To Do")
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["description"], "Ask [~jdoe] or jane.doe@example.com\nSecond line")
        reads = [item for item in self.jira.sent("GET", "/rest/api/2/issue/PROJ-1") if item.query.get("fields") == ["status,updated,description,labels"]]
        self.assertEqual(len(reads), 2)  # one read for each of the two runs, the read that every transition makes: nothing else was asked for
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/issue/PROJ-1/transitions"), [])  # refused before the possible moves were listed

    def test_a_transition_that_sends_what_is_real_asks_the_read_it_always_asks(self):
        result = self.cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", '{"description": "Ask [~jdoe]", "labels": ["a"]}')
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/transitions")[0].body["fields"], {"description": "Ask [~jdoe]", "labels": ["a"]})
        reads = [item for item in self.jira.sent("GET", "/rest/api/2/issue/PROJ-1") if item.path == "/rest/api/2/issue/PROJ-1"]
        self.assertEqual([item.query.get("fields") for item in reads], [["status,updated"]])  # the one read that every transition makes

    def test_a_label_that_jira_holds_may_stay_in_a_transition_too(self):
        self.jira.issues["PROJ-1"]["fields"]["labels"] = ["[email]", "backend"]
        result = self.cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", '{"labels": ["[email]", "backend", "new"]}')
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/transitions")[0].body["fields"], {"labels": ["[email]", "backend", "new"]})

    def test_the_raw_call_that_the_hint_of_a_transition_names_makes_the_transition_with_the_stand_in(self):
        refused = self.cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", '{"labels": ["[email]"]}', "--json")
        self.assertEqual(refused.code, 1)
        self.assertIn("`jira api POST issue/PROJ-1/transitions --data-file FILE --write`", refused.error()["hint"])
        (self.folder / "transition.json").write_text('{"transition": {"id": "21"}, "fields": {"labels": ["[email]"]}}', encoding="utf-8")
        done = self.cli("api", "POST", "issue/PROJ-1/transitions", "--data-file", str(self.folder / "transition.json"), "--write")  # the hint's command
        self.assertEqual(done.code, 0, done.err)
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["status"]["name"], "In Progress")
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/transitions")[0].body["fields"], {"labels": ["[email]"]})


class StaleIssueTypesFlowTest(ValidationFlowTest):
    """The issue types are kept in the cache for a week. An administrator adds a type today: the dry run must not refuse it."""

    LISTING = "/rest/api/2/issue/createmeta/PROJ/issuetypes"

    def setUp(self):
        super().setUp()
        self.assertEqual(self.cli("create", "--type", "Story", "--summary", "S", "--dry-run").code, 0)  # fills the cache with three types
        patcher = mock.patch.object(mock_jira, "ISSUE_TYPES", [*mock_jira.ISSUE_TYPES, {"id": "10300", "name": "Task"}])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.jira.reset_requests()

    def listings(self):
        return [item for item in self.jira.requests if item.path == self.LISTING]

    def test_create_json_with_the_new_type_passes_the_dry_run_and_the_cache_learns_it(self):
        payload = self.folder / "new.json"
        payload.write_text('{"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10300"}, "summary": "S"}}', encoding="utf-8")
        self.assertEqual(self.cli("create-json", str(payload), "--dry-run").code, 0)
        self.assertEqual(len(self.listings()), 1)  # the cache did not know the type: one read of the list from Jira
        self.assertEqual(self.cli("create-json", str(payload), "--dry-run").code, 0)
        self.assertEqual(len(self.listings()), 1)  # the cache knows it now: no read at all

    def test_the_new_type_by_id_and_by_name(self):
        self.assertEqual(self.cli("create", "--type-id", "10300", "--summary", "S", "--dry-run").code, 0)
        self.assertEqual(self.cli("create", "--type", "Task", "--summary", "S", "--dry-run").code, 0)
        self.assertEqual(len(self.listings()), 1)  # the first run refreshed the cache, the second found the type in it

    def test_a_new_type_is_created_by_a_live_create_without_the_flag(self):
        result = self.cli("create", "--type", "Task", "--summary", "Live", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue")[0].body["fields"]["issuetype"], {"id": "10300"})

    def test_a_type_that_does_not_exist_is_refused_after_one_more_read_and_the_hint_says_the_list_is_fresh(self):
        result = self.cli("create", "--type-id", "99999", "--summary", "S", "--dry-run", "--json")
        self.assertEqual((result.code, result.error()["code"]), (6, "REJECTED"))
        self.assertIn("Task (10300)", result.error()["hint"])
        self.assertTrue(result.error()["hint"].endswith(FRESH_LIST))
        self.assertEqual(len(self.listings()), 1)
        by_name = self.cli("create", "--type", "Nope", "--summary", "S", "--dry-run")
        self.assertEqual(by_name.code, 1)
        self.assertIn(FRESH_LIST, by_name.err)

    def test_the_flag_bypasses_the_cache_at_once(self):
        self.assertEqual(self.cli("create", "--type-id", "10300", "--summary", "S", "--dry-run", "--refresh-metadata").code, 0)
        self.assertEqual(len(self.listings()), 1)


class PagedCreateMetadataFlowTest(ValidationFlowTest):
    """A server may answer a page of the create metadata with fewer fields than the screen has: the rest comes on the next pages."""

    def starts(self):
        return [item.query["startAt"][0] for item in self.jira.sent("GET", CREATE_META)]

    def test_a_server_that_gives_two_fields_a_page_gives_no_false_warning_that_a_field_is_not_on_the_screen(self):
        self.mock.page_size = 2  # summary and description on the first page, the priority, the components and the versions after them
        result = self.cli("create", "--type", "Story", "--summary", "S", "--priority", "High", "--components", "Demo", "--fix-versions", "Release 1.0", "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual((result.json()["warnings"], result.err), ([], ""))
        self.assertEqual(self.starts(), ["0", "2", "4"])

    def test_a_wrong_value_on_a_later_page_is_still_refused_with_the_allowed_values(self):
        self.mock.page_size = 2
        result = self.cli("create", "--type", "Story", "--summary", "S", "--components", "Nowhere", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed components: Demo, Backend.", result.err)

    def test_a_server_that_has_everything_on_one_page_is_asked_once(self):
        self.cli("create", "--type", "Story", "--summary", "S", "--dry-run")
        self.assertEqual(self.starts(), ["0"])


class LongRunningApplicationTest(ValidationFlowTest):
    """`jira mcp` keeps one application for hours. What a project offers and who exists in Jira change meanwhile: a refusal is checked once
    more against what Jira says now."""

    def setUp(self):
        super().setUp()
        self.app = create_application(RecordingDiagnostics(), self.env)  # one application for every call below
        self.addCleanup(self.app.close)

    def test_a_component_that_is_added_after_the_first_refusal_passes_the_next_dry_run(self):
        changes = IssueChanges(components=["Fresh"])
        with self.assertRaises(RejectedError) as caught:
            self.app.update_issue.execute("PROJ-1", changes, dry_run=True)
        self.assertEqual(caught.exception.hint, "Allowed components: Demo, Backend.")
        self.mock.components["PROJ"] += ("Fresh",)
        self.assertEqual(self.app.update_issue.execute("PROJ-1", changes, dry_run=True).payload["fields"]["components"], [{"name": "Fresh"}])
        draft = IssueDraft("PROJ", "Story", "S", components=["Fresh"])
        self.assertEqual(self.app.create_issue.execute(draft, dry_run=True).warnings, [])

    def test_a_fix_version_that_is_released_after_the_first_refusal_passes_too(self):
        changes = IssueChanges(fix_versions=["Release 3.0"])
        with self.assertRaises(RejectedError):
            self.app.update_issue.execute("PROJ-1", changes, dry_run=True)
        self.jira.versions["PROJ"].append("Release 3.0")
        self.assertEqual(self.app.update_issue.execute("PROJ-1", changes, dry_run=True).warnings, [])

    def test_a_user_who_is_created_after_the_first_search_is_found_the_next_time(self):
        self.assertEqual(self.app.users.resolve.execute("newbie").matches, 0)
        user_routes(self.jira).add_user("newbie", "New Bie", "new.bie@example.com")
        found = self.app.users.resolve.execute("newbie")
        self.assertEqual((found.matches, found.person.name), (1, "newbie"))
        self.assertEqual(self.app.update_issue.execute("PROJ-1", IssueChanges(assignee="newbie"), dry_run=True).payload["fields"]["assignee"], {"name": "newbie"})


if __name__ == "__main__":
    unittest.main()
