"""The dry run checks values against Jira's metadata, and `jira transition` sends the fields of its screen: from the command line."""
import dataclasses
import json
import unittest

from jira_dc_cli.domain.validation import FieldRule
from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway, MemoryFiles
from tests.support.fakes_validation import names

RESOLUTION = FieldRule("resolution", "Resolution", required=True, allowed=names("Fixed", "Won't Fix", "Duplicate"))
FIX_VERSIONS = FieldRule("fixVersions", "Fix Version/s", allowed=names("Release 1.0", "Release 2.0"))
EPIC_NAME = FieldRule("customfield_10005", "Epic Name", required=True)


class ValidationCommandTestCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles(
            {
                "fields.json": '{"fixVersions": [{"name": "Release 1.0"}]}',
                "bad.json": '{"fixVersions": [{"name": "Release 9"}]}',
                "create.json": '{"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S", "priority": {"name": "Nope"}}}',
                "update.json": '{"update": {"components": [{"add": {"name": "Nowhere"}}]}}',
            }
        )

    def run_cli(self, *argv, **options):
        options.setdefault("files", self.files)
        return run_cli(*argv, gateway=self.gateway, **options)


class CreateDryRunTest(ValidationCommandTestCase):
    def test_a_wrong_priority_is_exit_6_and_the_hint_lists_the_allowed_ones(self):
        result = self.run_cli("create", "--type", "Story", "--summary", "S", "--priority", "Nope", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertEqual(result.err, "ERROR: priority 'Nope' is not available in PROJ for Story (10200).\nHint: Allowed priorities: Highest, High, Medium, Low, Lowest.\n")
        self.assertEqual(result.out, "")
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_the_error_is_json_on_standard_error_with_json(self):
        result = self.run_cli("create", "--type", "Story", "--summary", "S", "--priority", "Nope", "--dry-run", "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"], error["exitCode"]), (6, "REJECTED", 6))
        self.assertIn("Highest, High, Medium", error["hint"])
        self.assertEqual(error["details"]["problems"][0]["field"], "priority")

    def test_a_valid_dry_run_shows_the_payload_as_it_was(self):
        result = self.run_cli("create", "--type", "Story", "--summary", "S", "--priority", "High", "--components", "Demo", "--fix-versions", "Release 1.0", "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(data["payload"]["fields"]["priority"], {"name": "High"})
        self.assertEqual((data["dryRun"], data["warnings"], data["key"]), (True, [], ""))
        self.assertEqual(result.err, "")

    def test_the_text_of_a_valid_dry_run_is_unchanged(self):
        result = self.run_cli("create", "--type", "Story", "--summary", "S", "--priority", "High", "--dry-run")
        self.assertTrue(result.out.startswith("Dry run: nothing was sent\n{"))

    def test_a_required_field_that_is_left_out_is_a_warning_on_stderr_and_in_the_json(self):
        self.gateway.required_to_create["10000"] = [EPIC_NAME]
        text = self.run_cli("create", "--type", "Epic", "--summary", "S", "--dry-run")
        self.assertEqual(text.code, 0)
        self.assertIn("WARNING: Epic Name (customfield_10005) is required in PROJ for Epic (10000)", text.err)
        self.assertNotIn("WARNING", text.out)
        data = self.run_cli("create", "--type", "Epic", "--summary", "S", "--dry-run", "--json").json()
        self.assertEqual(len(data["warnings"]), 1)
        given = self.run_cli("create", "--type", "Epic", "--summary", "S", "--fields-json", '{"customfield_10005": "Billing"}', "--dry-run")
        self.assertEqual(given.err, "")

    def test_a_live_create_is_not_checked(self):
        result = self.run_cli("create", "--type", "Story", "--summary", "S", "--priority", "Nope")
        self.assertEqual(result.code, 0)
        self.assertEqual(self.gateway.called("get_create_fields"), [])

    def test_an_issue_type_id_that_the_project_does_not_have(self):
        result = self.run_cli("create", "--type-id", "99999", "--summary", "S", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Available: Bug (10201), Epic (10000), Story (10200).", result.err)


class CreateJsonDryRunTest(ValidationCommandTestCase):
    def test_a_wrong_priority_in_the_payload_is_exit_6(self):
        result = self.run_cli("create-json", "create.json", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed priorities", result.err)

    def test_a_valid_payload_passes_and_a_live_run_is_not_checked(self):
        self.files.files["ok.json"] = self.files.files["create.json"].replace("Nope", "Low")
        self.assertEqual(self.run_cli("create-json", "ok.json", "--dry-run").code, 0)
        self.assertEqual(self.run_cli("create-json", "create.json").code, 0)


class UpdateDryRunTest(ValidationCommandTestCase):
    def test_a_wrong_value_is_exit_6_with_the_allowed_values(self):
        result = self.run_cli("update", "PROJ-1", "--priority", "Urgent", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertEqual(result.err, "ERROR: priority 'Urgent' is not available for PROJ-1.\nHint: Allowed priorities: Highest, High, Medium, Low, Lowest.\n")
        result = self.run_cli("update", "PROJ-1", "--components", "Demo,Nowhere", "--fix-versions", "Release 9", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("Allowed components: Demo, Backend. Allowed fix versions: Release 1.0, Release 2.0.", result.err)

    def test_a_valid_dry_run_is_what_it_was(self):
        result = self.run_cli("update", "PROJ-1", "--priority", "Low", "--dry-run", "--if-unchanged-since", "2026-10-10T07:00:00Z")
        self.assertIn("Dry run: PROJ-1 exists and is unchanged since 2026-10-10T07:00:00Z; nothing was sent", result.out)
        self.assertEqual(result.err, "")
        data = self.run_cli("update", "PROJ-1", "--priority", "Low", "--dry-run", "--json").json()
        self.assertEqual((data["warnings"], data["dryRun"], data["action"]), ([], True, "updated"))

    def test_a_field_that_the_edit_screen_lacks_is_a_warning(self):
        result = self.run_cli("update", "PROJ-1", "--fields-json", '{"resolution": {"name": "Fixed"}}', "--dry-run")
        self.assertEqual(result.code, 0)
        self.assertIn("WARNING: Resolution is not on the edit screen for PROJ-1: Jira will probably refuse it.", result.err)

    def test_fields_json_from_a_file_is_checked(self):
        self.assertEqual(self.run_cli("update", "PROJ-1", "--fields-json", "@fields.json", "--dry-run").code, 0)
        self.assertEqual(self.run_cli("update", "PROJ-1", "--fields-json", "@bad.json", "--dry-run").code, 6)

    def test_update_json_is_checked_when_it_names_the_same_fields(self):
        result = self.run_cli("update-json", "PROJ-1", "update.json", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("component 'Nowhere'", result.err)
        self.files.files["plain.json"] = '{"fields": {"summary": "New"}}'
        self.assertEqual(self.run_cli("update-json", "PROJ-1", "plain.json", "--dry-run", "--json").json()["warnings"], [])

    def test_a_live_update_is_not_checked(self):
        self.assertEqual(self.run_cli("update", "PROJ-1", "--priority", "Urgent").code, 0)
        self.assertEqual(self.run_cli("update-json", "PROJ-1", "update.json").code, 0)
        self.assertEqual(self.gateway.called("get_edit_metadata"), [])

    def test_the_issue_not_found_comes_before_the_values(self):
        self.assertEqual(self.run_cli("update", "PROJ-999", "--priority", "Urgent", "--dry-run").code, 4)

    def test_an_empty_name_is_refused_like_a_blank_one_with_the_allowed_values(self):
        for value in ("", " "):
            with self.subTest(value=value):
                result = self.run_cli("update", "PROJ-1", "--priority", value, "--dry-run")
                self.assertEqual(result.code, 6)
                self.assertIn("Allowed priorities: Highest, High, Medium, Low, Lowest.", result.err)
        empty = self.run_cli("update", "PROJ-1", "--priority", "", "--dry-run")
        self.assertEqual(empty.err.splitlines()[0], "ERROR: priority is empty for PROJ-1: it needs a name or an id.")
        self.assertEqual(self.run_cli("update", "PROJ-1", "--priority", "").code, 0)  # a live run is not checked: Jira answers
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"priority": {"name": ""}}})

    def test_an_empty_name_in_fields_json_is_refused_too(self):
        self.files.files["empty.json"] = '{"components": [{"name": "Demo"}, {"name": ""}]}'
        result = self.run_cli("update", "PROJ-1", "--fields-json", "@empty.json", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("component is empty for PROJ-1", result.err)


READ = "Ask [~user] or [email]\nSecond line"  # what `jira issue PROJ-1` shows of its description: the people are hidden
HOLDS = "which stands in place of a mention or an address that the CLI hides: sending it would overwrite the real one."


class StandInTest(ValidationCommandTestCase):
    """The text that `jira issue` shows has `[~user]` where Jira holds a mention: sending it back would overwrite the real mention."""

    def setUp(self):
        super().setUp()
        self.files.files.update(
            {
                "description.txt": READ + "\nThird line\n",
                "fields.json": '{"description": "Ask [~user]"}',
                "update.json": '{"fields": {"summary": "Mail [email]"}}',
                "set.json": '{"update": {"description": [{"set": "See [personal link]"}]}}',
                "ok.json": '{"fields": {"summary": "Fine", "description": "Ask [~jdoe]"}}',
            }
        )

    def assert_refused(self, *argv):
        result = self.run_cli(*argv)
        self.assertEqual(result.code, 1, result.err)
        self.assertEqual(result.out, "")
        self.assertEqual(self.gateway.called("update_issue"), [])
        return result

    def test_update_with_the_text_that_issue_showed_is_exit_1_with_a_hint_and_nothing_is_sent(self):
        result = self.assert_refused("update", "PROJ-1", "--description", READ + "\nThird line")
        lines = result.err.splitlines()
        self.assertEqual(lines[0], f"ERROR: The new description holds [~user], [email], {HOLDS}")
        self.assertTrue(lines[1].startswith("Hint: The CLI shows [~user], [email] and [personal link] in place of real mentions"))
        self.assertIn("see `jira users resolve`", lines[1])
        self.assertEqual(self.gateway.issues["PROJ-1"].description, "Ask [~jdoe] or jane.doe@example.com\nSecond line")

    def test_the_json_error_names_the_texts_and_the_stand_ins(self):
        error = self.assert_refused("update", "PROJ-1", "--summary", "About [~user]", "--description", "Mail [email]", "--json").error()
        self.assertEqual((error["code"], error["exitCode"]), ("INVALID", 1))
        self.assertIn("The new summary and description hold [~user], [email]", error["message"])
        self.assertEqual(error["details"], {"fields": ["summary", "description"], "placeholders": ["[~user]", "[email]"]})

    def test_the_dry_run_and_the_guard_refuse_it_too(self):
        self.assert_refused("update", "PROJ-1", "--description", READ, "--dry-run")
        self.assert_refused("update", "PROJ-1", "--description", READ, "--if-unchanged-since", "2026-10-10T07:00:00Z")

    def test_the_hint_names_the_way_out_with_the_key_that_was_given(self):
        lines = self.assert_refused("update", "proj-1", "--summary", "[email] Add retry").err.splitlines()
        self.assertEqual(lines[0], f"ERROR: The new summary holds [email], {HOLDS}")
        self.assertIn("a text that holds people is best edited in Jira itself. If the stand-in is really what you want to write", lines[1])
        self.assertTrue(lines[1].endswith("send it with `jira api PUT issue/PROJ-1 --data-file FILE --write` (the raw call has no guard)."))
        self.assertIn("`jira api PUT issue/PROJ-3 --data-file FILE --write`", self.assert_refused("update-json", "PROJ-3", "update.json").err)
        hint = self.assert_refused("update", "PROJ-1", "--summary", "[email]", "--json").error()["hint"]
        self.assertEqual(hint.count("jira api PUT issue/PROJ-1 --data-file FILE --write"), 1)

    def test_labels_components_and_fix_versions_are_refused_with_the_field_in_the_details(self):
        for argv, called, field_name in (
            (("--labels", "[email],backend"), "labels", "labels"),
            (("--components", "[~user]"), "components", "components"),
            (("--fix-versions", "Release [personal link]"), "fix versions", "fixVersions"),
            (("--fields-json", '{"labels": ["[email]"]}'), "labels", "labels"),
        ):
            with self.subTest(argv=argv):
                error = self.assert_refused("update", "PROJ-1", *argv, "--json").error()
                self.assertEqual((error["code"], error["exitCode"]), ("INVALID", 1))
                self.assertTrue(error["message"].startswith(f"The new {called} hold "), error["message"])
                self.assertEqual(error["details"]["fields"], [field_name])
        self.assert_refused("update", "PROJ-1", "--labels", "[email]", "--dry-run")

    def test_update_json_refuses_the_names_of_the_add_and_of_the_fields_too(self):
        self.files.files["add.json"] = '{"update": {"labels": [{"add": "[email]"}]}}'
        self.files.files["names.json"] = '{"fields": {"components": [{"name": "Demo"}, {"name": "[~user]"}]}}'
        self.assert_refused("update-json", "PROJ-1", "add.json")
        self.assert_refused("update-json", "PROJ-1", "names.json", "--dry-run", "--json")

    def test_a_label_that_jira_holds_may_stay_and_ordinary_names_cost_no_read(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], labels=["[email]", "backend"])
        self.assertEqual(self.run_cli("update", "PROJ-1", "--labels", "[email],backend").code, 0)
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"labels": ["[email]", "backend"]}})
        self.gateway.calls.clear()
        self.assertEqual(self.run_cli("update", "PROJ-1", "--labels", "a,b", "--components", "Demo", "--fix-versions", "Release 1.0").code, 0)
        self.assertEqual(self.gateway.called("get_issue"), [])

    def test_the_fields_of_a_transition_are_judged_like_those_of_an_update_in_the_dry_run_too(self):
        fields = '{"description": "x [~user] [email]", "labels": ["[email]"]}'
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra=extra):
                result = self.run_cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", fields, *extra)
                self.assertEqual((result.code, result.out), (1, ""), result.err)
                lines = result.err.splitlines()
                self.assertEqual(lines[0], f"ERROR: The new description and labels hold [~user], [email], {HOLDS}")
                self.assertIn("`jira api POST issue/PROJ-1/transitions --data-file FILE --write`", lines[1])
                self.assertIn("(`jira transition PROJ-1` lists the ids; the raw call has no guard)", lines[1])
        self.assertEqual(self.gateway.called("transition_issue"), [])
        self.assertEqual(self.gateway.issues["PROJ-1"].status.name, "To Do")
        error = self.run_cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", fields, "--json").error()
        self.assertEqual((error["code"], error["exitCode"], error["details"]["fields"]), ("INVALID", 1, ["description", "labels"]))
        self.assertEqual(self.run_cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", "@fields.json").code, 1)  # the file form too

    def test_a_transition_that_sends_what_is_real_goes_through_with_the_read_it_always_makes(self):
        result = self.run_cli("transition", "PROJ-1", "--to", "In Progress", "--fields-json", '{"description": "Ask [~jdoe]", "labels": ["a"]}')
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.gateway.called("transition_issue")[0][2]["fields"], {"description": "Ask [~jdoe]", "labels": ["a"]})
        self.assertEqual([call[2] for call in self.gateway.called("get_issue")], [("status", "updated")])

    def test_the_comment_and_the_resolution_of_a_transition_are_not_judged(self):
        result = self.run_cli("transition", "PROJ-1", "--to", "In Progress", "--comment", "see [~user] and [email]", "--json")
        self.assertEqual(result.code, 0, result.err)  # a new comment has nothing to overwrite: only `comment --edit` refuses a stand-in
        self.assertEqual(result.json()["payload"]["update"], {"comment": [{"add": {"body": "see [~user] and [email]"}}]})
        again = self.run_cli("transition", "PROJ-2", "--to", "Done", "--resolution", "[email]")
        self.assertEqual(again.code, 0, again.err)

    def test_a_file_a_fields_json_and_an_update_json_are_judged_the_same_way(self):
        self.assert_refused("update", "PROJ-1", "--description-file", "description.txt")
        self.assert_refused("update", "PROJ-1", "--description", "**Ask** [~user]", "--wiki")  # the conversion to wiki markup keeps the stand-in
        self.assert_refused("update", "PROJ-1", "--fields-json", "@fields.json")
        self.assert_refused("update", "PROJ-1", "--fields-json", '{"summary": "[email]"}')
        self.assert_refused("update-json", "PROJ-1", "update.json")
        self.assert_refused("update-json", "PROJ-1", "set.json", "--dry-run", "--json")

    def test_a_real_mention_and_a_text_without_people_are_sent_and_a_live_update_asks_for_nothing(self):
        self.assertEqual(self.run_cli("update", "PROJ-1", "--description", "Ask [~jdoe] about it", "--summary", "Plain").code, 0)
        self.assertEqual(self.run_cli("update-json", "PROJ-1", "ok.json").code, 0)
        self.assertEqual(len(self.gateway.called("update_issue")), 2)
        self.assertEqual(self.gateway.called("get_issue"), [])

    def test_a_literal_that_jira_holds_already_may_stay(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], description="Write to [email] for details")
        self.assertEqual(self.run_cli("update", "PROJ-1", "--description", "Write to [email] today").code, 0)
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"description": "Write to [email] today"}})

    def test_the_text_of_a_valid_dry_run_is_what_it_was(self):
        result = self.run_cli("update", "PROJ-1", "--description", "Plain text", "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.err, "")
        self.assertTrue(result.out.startswith("Dry run: PROJ-1 exists; nothing was sent\n{"))

    def test_create_notes_it_in_the_dry_run_and_sends_it_as_it_is(self):
        dry = self.run_cli("create", "--type", "Story", "--summary", "S", "--description", READ, "--dry-run")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertIn("WARNING: The description of the new issue holds [~user], [email], which stands in place of", dry.err)
        data = self.run_cli("create", "--type", "Story", "--summary", "S", "--description", READ, "--dry-run", "--json").json()
        self.assertEqual((len(data["warnings"]), data["payload"]["fields"]["description"]), (1, READ))
        live = self.run_cli("create", "--type", "Story", "--summary", "S", "--description", READ, "--json")
        self.assertEqual((live.code, live.json()["warnings"], live.err), (0, [], ""))
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["description"], READ)

    def test_create_json_notes_it_too(self):
        self.files.files["new.json"] = '{"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "Mail [email]"}}'
        result = self.run_cli("create-json", "new.json", "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("WARNING: The summary of the new issue holds [email]", result.err)

    def test_create_notes_the_names_of_labels_too(self):
        dry = self.run_cli("create", "--type", "Story", "--summary", "S", "--labels", "[email],backend", "--dry-run")
        self.assertEqual(dry.code, 0, dry.err)
        self.assertIn("WARNING: The labels of the new issue hold [email], which stands in place of", dry.err)
        live = self.run_cli("create", "--type", "Story", "--summary", "S", "--labels", "[email],backend", "--json")
        self.assertEqual((live.code, live.json()["warnings"], live.err), (0, [], ""))


class TransitionFieldsTest(ValidationCommandTestCase):
    def setUp(self):
        super().setUp()
        self.gateway.screens["81"] = [RESOLUTION, FIX_VERSIONS]

    def test_show_fields_lists_every_transition_with_the_fields_of_its_screen(self):
        result = self.run_cli("transition", "PROJ-2", "--show-fields")
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertEqual(lines[0], "PROJ-2 is in status: In Progress")
        self.assertEqual(lines[2].split(), ["ID", "TRANSITION", "TO", "STATUS", "FIELD", "REQUIRED", "ALLOWED"])
        reopen = lines[3].split()
        self.assertEqual(reopen, ["111", "Reopen", "To", "Do", "-"])
        finish = lines[4].split(None, 7)
        self.assertEqual(finish[:6], ["81", "Finish", "Done", "resolution", "required", "Fixed,"])
        self.assertIn("Fixed, Won't Fix, Duplicate", lines[4])
        self.assertTrue(lines[5].split()[:2] == ["fixVersions", "Release"])
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_show_fields_for_one_transition(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--show-fields")
        self.assertNotIn("Reopen", result.out)
        self.assertIn("resolution", result.out)

    def test_show_fields_in_json(self):
        data = self.run_cli("transition", "PROJ-2", "--show-fields", "--json").json()
        self.assertEqual(data["currentStatus"], "In Progress")
        self.assertEqual([item["id"] for item in data["screens"]], ["111", "81"])
        finish = data["screens"][1]
        self.assertEqual((finish["name"], finish["toStatus"]), ("Finish", {"name": "Done", "category": "done"}))
        self.assertEqual(finish["fields"][0], {"id": "resolution", "name": "Resolution", "required": True, "allowed": ["Fixed", "Won't Fix", "Duplicate"], "allowedCount": 3})
        self.assertEqual(data["screens"][0]["fields"], [])
        self.assertEqual((data["chosen"], data["executed"], data["payload"], data["warnings"]), (None, False, None, []))
        self.assertEqual([item["id"] for item in data["options"]], ["111", "81"])

    def test_a_long_list_of_values_is_cut_in_the_text_and_in_the_json_and_counted(self):
        self.gateway.screens["81"] = [FieldRule("fixVersions", "Fix Version/s", allowed=names(*[f"V{number}" for number in range(40)]))]
        text = self.run_cli("transition", "PROJ-2", "--to", "Done", "--show-fields").out
        self.assertIn("and 10 more", text)
        field = self.run_cli("transition", "PROJ-2", "--to", "Done", "--show-fields", "--json").json()["screens"][0]["fields"][0]
        self.assertEqual((len(field["allowed"]), field["allowedCount"]), (30, 40))

    def test_a_field_without_a_list_of_values_has_null_for_both(self):
        self.gateway.screens["81"] = [FieldRule("customfield_9", "Team", required=True)]
        field = self.run_cli("transition", "PROJ-2", "--to", "Done", "--show-fields", "--json").json()["screens"][0]["fields"][0]
        self.assertEqual((field["allowed"], field["allowedCount"]), (None, None))

    def test_resolution_and_fields_json_go_into_the_dry_run_payload(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Fixed", "--fields-json", "@fields.json", "--comment", "Done.", "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(
            data["payload"],
            {
                "transition": {"id": "81"},
                "fields": {"fixVersions": [{"name": "Release 1.0"}], "resolution": {"name": "Fixed"}},
                "update": {"comment": [{"add": {"body": "Done."}}]},
            },
        )
        self.assertEqual((data["dryRun"], data["executed"], data["warnings"], data["screens"]), (True, False, [], []))
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_the_text_of_the_dry_run_has_the_payload_with_the_fields(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Fixed", "--dry-run")
        self.assertIn("Dry run: PROJ-2 would move from 'In Progress' to 'Done' via transition 'Finish' (81); nothing was sent", result.out)
        self.assertIn('"resolution"', result.out)

    def test_a_resolution_that_the_screen_does_not_allow_is_exit_6_with_the_allowed_ones(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Whatever", "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("resolution 'Whatever' is not available for the transition 'Finish' (81) of PROJ-2.", result.err)
        self.assertIn("Allowed resolutions: Fixed, Won't Fix, Duplicate.", result.err)

    def test_a_field_that_is_not_on_the_screen_is_exit_6_and_the_hint_lists_the_screen(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--fields-json", '{"components": [{"name": "Demo"}]}', "--dry-run")
        self.assertEqual(result.code, 6)
        self.assertIn("The screen has these fields: resolution, fixVersions.", result.err)
        self.assertIn("jira transition PROJ-2 --to 81 --show-fields", result.err)

    def test_a_missing_required_field_is_a_warning_and_the_dry_run_still_succeeds(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--dry-run")
        self.assertEqual(result.code, 0)
        self.assertIn("WARNING: Resolution (resolution) is required for the transition 'Finish' (81) of PROJ-2", result.err)
        data = self.run_cli("transition", "PROJ-2", "--to", "Done", "--dry-run", "--json").json()
        self.assertEqual(len(data["warnings"]), 1)

    def test_a_live_transition_sends_the_fields_and_is_not_checked(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Whatever", "--comment", "Done.")
        self.assertEqual(result.out, "Moved PROJ-2 from 'In Progress' to 'Done' via transition 'Finish'\n")
        self.assertEqual(
            self.gateway.called("transition_issue")[0][2],
            {"transition": {"id": "81"}, "fields": {"resolution": {"name": "Whatever"}}, "update": {"comment": [{"add": {"body": "Done."}}]}},
        )
        self.assertEqual(self.gateway.called("list_transition_screens"), [])

    def test_a_stale_issue_is_exit_3_and_nothing_is_sent_and_a_fresh_one_moves(self):
        stale = self.run_cli("transition", "PROJ-2", "--to", "Done", "--if-unchanged-since", "2026-10-01T00:00:00Z", "--json")
        self.assertEqual((stale.code, stale.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.gateway.called("transition_issue"), [])
        fresh = self.run_cli("transition", "PROJ-2", "--to", "Done", "--if-unchanged-since", "2026-10-10T07:00:00Z")
        self.assertEqual(fresh.code, 0, fresh.err)
        self.assertEqual(self.run_cli("transition", "PROJ-1", "--if-unchanged-since", "2026-10-10T07:00:00Z").code, 1)  # no transition to guard

    def test_the_plain_options_are_as_they_were(self):
        listing = self.run_cli("transition", "PROJ-2")
        self.assertEqual(listing.out.splitlines()[0], "PROJ-2 is in status: In Progress")
        self.assertEqual(listing.out.splitlines()[2].split(), ["ID", "TRANSITION", "TO", "STATUS"])
        data = self.run_cli("transition", "PROJ-2", "--json").json()
        self.assertEqual(sorted(data), ["chosen", "currentStatus", "dryRun", "executed", "key", "options", "payload", "screens", "warnings"])
        self.assertEqual((data["screens"], data["warnings"]), ([], []))

    def test_what_does_not_go_together_is_refused(self):
        for argv in (
            ("transition", "PROJ-2", "--to", "Done", "--show-fields", "--resolution", "Fixed"),
            ("transition", "PROJ-2", "--show-fields", "--comment", "x"),
            ("transition", "PROJ-2", "--show-fields", "--dry-run"),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(*argv).code, 2)
        for argv, expected in (
            (("transition", "PROJ-2", "--resolution", "Fixed"), "--resolution needs --to"),
            (("transition", "PROJ-2", "--fields-json", "@fields.json"), "--fields-json needs --to"),
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.code, 1)
                self.assertIn(expected, result.err)
        self.assertEqual(self.run_cli("transition", "PROJ-2", "--to", "Done", "--resolution", "Fixed", "--fields-json", '{"resolution": {"name": "Duplicate"}}').code, 1)
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_fields_json_that_powershell_5_mangled_says_how_to_pass_it(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--fields-json", "{resolution: Fixed}")
        self.assertEqual(result.code, 1)
        self.assertIn("'@fields.json'", result.err)

    def test_an_unknown_target_lists_what_is_possible_as_before(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Archived", "--dry-run")
        self.assertEqual(result.code, 1)
        self.assertIn("Available:", result.err)

    def test_the_catalog_describes_the_new_options(self):
        catalog = json.loads(run_cli("commands", "--json").out)
        transition = next(item for item in catalog["commands"] if item["name"] == "transition")
        names_of = [name for arg in transition["arguments"] for name in arg["names"]]
        for option in ("--fields-json", "--resolution", "--show-fields"):
            self.assertIn(option, names_of)
        self.assertIn("screens[", transition["jsonOutput"])


if __name__ == "__main__":
    unittest.main()
