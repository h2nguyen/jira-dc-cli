"""The dry run of create and update checks values against Jira's metadata; the transition sends the fields of its screen."""
import dataclasses
import unittest

from jira_dc_cli.domain.drafts import FRESH_LIST, IssueChanges, IssueDraft
from jira_dc_cli.domain.errors import ConflictError, NotFoundError, RejectedError, UsageError, ValidationError
from jira_dc_cli.domain.model import IssueTypeInfo
from jira_dc_cli.domain.validation import FieldRule
from tests.support.fakes import FakeGateway, build_app
from tests.support.fakes_validation import names

EPIC_NAME = FieldRule("customfield_10005", "Epic Name", required=True)
RESOLUTION = FieldRule("resolution", "Resolution", required=True, allowed=names("Fixed", "Won't Fix", "Duplicate"))
COMMENT_FIELD = FieldRule("comment", "Comment")


class ValidationTestCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def draft(self, **changes):
        values = dict(project="PROJ", issue_type="Story", summary="New story")
        values.update(changes)
        return IssueDraft(**values)

    def create(self, **changes):
        return self.app.create_issue.execute(self.draft(**changes), dry_run=True)

    def update(self, changes, **options):
        return self.app.update_issue.execute("PROJ-1", changes, dry_run=True, **options)


class CreateDryRunTest(ValidationTestCase):
    def test_a_valid_payload_passes_the_dry_run_unchanged(self):
        checked = self.create(priority="High", components=["Demo"], fix_versions=["Release 1.0"], labels=["a"])
        expected = self.draft(priority="High", components=["Demo"], fix_versions=["Release 1.0"], labels=["a"]).to_payload("10200", "jdoe")
        self.assertEqual(checked.payload, expected)
        self.assertEqual((checked.warnings, checked.dry_run, checked.key), ([], True, ""))

    def test_a_priority_that_the_project_does_not_offer_is_refused_with_the_allowed_ones(self):
        with self.assertRaises(RejectedError) as caught:
            self.create(priority="Nope")
        error = caught.exception
        self.assertEqual(error.exit_code, 6)
        self.assertEqual(error.message, "priority 'Nope' is not available in PROJ for Story (10200).")
        self.assertEqual(error.hint, "Allowed priorities: Highest, High, Medium, Low, Lowest.")
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_a_priority_by_id_is_checked_too(self):
        self.assertEqual(self.app.create_issue.execute(self.draft(), dry_run=True).warnings, [])
        draft = self.draft(fields={"priority": {"id": "2"}})
        self.assertEqual(self.app.create_issue.execute(draft, dry_run=True).payload["fields"]["priority"], {"id": "2"})
        with self.assertRaises(RejectedError):
            self.app.create_issue.execute(self.draft(fields={"priority": {"id": "99"}}), dry_run=True)

    def test_components_and_fix_versions_are_checked(self):
        with self.assertRaises(RejectedError) as caught:
            self.create(components=["Demo", "Nowhere"], fix_versions=["Release 9"])
        self.assertEqual(caught.exception.message, "2 values would be refused in PROJ for Story (10200): component 'Nowhere'; fix version 'Release 9'.")
        self.assertEqual(caught.exception.hint, "Allowed components: Demo, Backend. Allowed fix versions: Release 1.0, Release 2.0.")

    def test_several_problems_are_one_error(self):
        with self.assertRaises(RejectedError) as caught:
            self.create(priority="Nope", components=["X"])
        self.assertEqual([item["field"] for item in caught.exception.details["problems"]], ["priority", "components"])

    def test_custom_fields_are_not_judged(self):
        checked = self.create(fields={"customfield_10001": "anything", "customfield_77": {"value": "x"}})
        self.assertEqual(checked.warnings, [])

    def test_a_required_field_that_the_options_leave_out_is_a_warning_and_not_an_error(self):
        self.gateway.required_to_create["10200"] = [EPIC_NAME]
        checked = self.create()
        self.assertEqual(len(checked.warnings), 1)
        self.assertIn("Epic Name (customfield_10005) is required in PROJ for Story (10200)", checked.warnings[0])
        self.assertTrue(checked.dry_run)

    def test_a_required_field_that_is_given_is_no_warning(self):
        self.gateway.required_to_create["10200"] = [EPIC_NAME]
        self.assertEqual(self.create(fields={"customfield_10005": "Billing"}).warnings, [])

    def test_a_required_field_with_a_default_is_no_warning(self):
        self.gateway.required_to_create["10200"] = [dataclasses.replace(EPIC_NAME, has_default=True)]
        self.assertEqual(self.create().warnings, [])

    def test_a_field_that_the_create_screen_does_not_have_is_a_warning(self):
        del self.gateway.allowed_values["components"]
        checked = self.create(components=["Demo"])
        self.assertEqual(checked.warnings, ["Component is not on the create screen in PROJ for Story (10200): Jira will probably refuse it."])

    def test_the_issue_type_by_name_costs_no_second_list(self):
        self.create()
        self.assertEqual(len(self.gateway.called("list_issue_types")), 1)

    def test_an_issue_type_id_that_the_project_does_not_have_is_refused_with_the_types_it_has(self):
        with self.assertRaises(RejectedError) as caught:
            self.app.create_issue.execute(self.draft(issue_type="99999"), dry_run=True)
        self.assertEqual(caught.exception.message, "Issue type 99999 is not available in PROJ.")
        self.assertEqual(caught.exception.hint, f"Available: Bug (10201), Epic (10000), Story (10200). {FRESH_LIST}")

    def test_an_issue_type_id_that_it_has_passes(self):
        self.assertEqual(self.app.create_issue.execute(self.draft(issue_type="10201"), dry_run=True).payload["fields"]["issuetype"], {"id": "10201"})

    def test_the_metadata_of_a_type_is_asked_once_for_many_dry_runs(self):
        for _ in range(3):
            self.create(priority="High")
        self.assertEqual(len(self.gateway.called("get_create_fields")), 1)

    def test_a_live_create_is_not_checked_and_asks_for_no_metadata(self):
        self.app.create_issue.execute(self.draft(priority="Nope"))
        self.assertEqual(self.gateway.called("get_create_fields"), [])
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["priority"], {"name": "Nope"})

    def test_an_unknown_project_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.app.create_issue.execute(self.draft(project="NOPE"), dry_run=True)


class CreateJsonDryRunTest(ValidationTestCase):
    def payload(self, **fields):
        return {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S", **fields}}

    def test_a_payload_that_names_the_project_by_key_and_the_type_by_id_is_checked(self):
        with self.assertRaises(RejectedError) as caught:
            self.app.create_issue_from_payload.execute(self.payload(priority={"name": "Nope"}), dry_run=True)
        self.assertEqual(caught.exception.exit_code, 6)
        valid = self.app.create_issue_from_payload.execute(self.payload(priority={"name": "Low"}), dry_run=True)
        self.assertEqual((valid.dry_run, valid.warnings), (True, []))

    def test_a_payload_that_names_them_another_way_is_not_checked(self):
        payload = self.payload(priority={"name": "Nope"})
        payload["fields"]["issuetype"] = {"name": "Story"}
        result = self.app.create_issue_from_payload.execute(payload, dry_run=True)
        self.assertEqual(result.warnings, [])
        self.assertEqual(self.gateway.called("get_create_fields"), [])

    def test_a_live_run_is_not_checked(self):
        self.app.create_issue_from_payload.execute(self.payload(priority={"name": "Nope"}))
        self.assertEqual(len(self.gateway.called("create_issue")), 1)
        self.assertEqual(self.gateway.called("get_create_fields"), [])


class UpdateDryRunTest(ValidationTestCase):
    def test_a_valid_update_passes_unchanged(self):
        result = self.update(IssueChanges(priority="Low", components=["Backend"], fix_versions=["Release 2.0"], summary="New"))
        self.assertEqual(result.payload["fields"]["priority"], {"name": "Low"})
        self.assertEqual((result.warnings, result.dry_run), ([], True))

    def test_a_wrong_priority_is_refused_with_exit_code_6_and_the_allowed_ones(self):
        with self.assertRaises(RejectedError) as caught:
            self.update(IssueChanges(priority="Urgent"))
        self.assertEqual(caught.exception.message, "priority 'Urgent' is not available for PROJ-1.")
        self.assertEqual(caught.exception.hint, "Allowed priorities: Highest, High, Medium, Low, Lowest.")
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_wrong_components_and_versions_in_fields_json_are_refused(self):
        changes = IssueChanges(fields={"fixVersions": [{"name": "Release 9"}]})
        with self.assertRaises(RejectedError) as caught:
            self.update(changes)
        self.assertIn("fix version 'Release 9'", caught.exception.message)

    def test_a_custom_field_is_not_judged(self):
        self.assertEqual(self.update(IssueChanges(fields={"customfield_10001": 5})).warnings, [])

    def test_the_issue_must_exist_before_the_values_are_judged(self):
        with self.assertRaises(NotFoundError):
            self.app.update_issue.execute("PROJ-999", IssueChanges(priority="Nope"), dry_run=True)
        self.assertEqual(self.gateway.called("get_edit_metadata"), [])

    def test_the_stale_guard_comes_before_the_values_are_judged(self):
        with self.assertRaises(ConflictError):
            self.update(IssueChanges(priority="Nope"), if_unchanged_since="2026-10-01T00:00:00Z")
        self.assertEqual(self.gateway.called("get_edit_metadata"), [])

    def test_a_field_that_the_edit_screen_does_not_have_is_a_warning(self):
        result = self.update(IssueChanges(fields={"resolution": {"name": "Fixed"}}))
        self.assertEqual(result.warnings, ["Resolution is not on the edit screen for PROJ-1: Jira will probably refuse it."])

    def test_clearing_a_required_field_is_refused(self):
        self.gateway.allowed_values["priority"] = names("High")
        original = self.gateway.get_edit_metadata

        def required(key):
            rules = original(key)
            rules["priority"] = dataclasses.replace(rules["priority"], required=True)
            return rules

        self.gateway.get_edit_metadata = required
        with self.assertRaises(RejectedError) as caught:
            self.update(IssueChanges(fields={"priority": None}))
        self.assertIn("priority is required for PROJ-1 and cannot be cleared", caught.exception.message)

    def test_a_live_update_is_not_checked(self):
        self.app.update_issue.execute("PROJ-1", IssueChanges(priority="Nope"))
        self.assertEqual(self.gateway.called("get_edit_metadata"), [])
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"priority": {"name": "Nope"}}})

    def test_the_metadata_of_an_issue_is_asked_once(self):
        self.update(IssueChanges(priority="Low"))
        self.update(IssueChanges(priority="High"))
        self.assertEqual(len(self.gateway.called("get_edit_metadata")), 1)


class UpdateJsonDryRunTest(ValidationTestCase):
    def update_json(self, payload, **options):
        return self.app.update_issue_from_payload.execute("PROJ-1", payload, dry_run=True, **options)

    def test_the_standard_fields_that_the_payload_names_are_checked(self):
        with self.assertRaises(RejectedError):
            self.update_json({"fields": {"priority": {"name": "Nope"}}})
        self.assertEqual(self.update_json({"fields": {"priority": {"name": "High"}}}).warnings, [])

    def test_the_add_and_set_of_the_update_syntax_are_checked_and_the_remove_is_not(self):
        with self.assertRaises(RejectedError) as caught:
            self.update_json({"update": {"components": [{"add": {"name": "Nowhere"}}]}})
        self.assertIn("component 'Nowhere'", caught.exception.message)
        valid = {"update": {"components": [{"remove": {"name": "Never existed"}}, {"add": {"name": "Demo"}}], "labels": [{"add": "x"}]}}
        self.assertEqual(self.update_json(valid).warnings, [])

    def test_a_payload_that_names_none_of_them_is_not_judged(self):
        self.assertEqual(self.update_json({"fields": {"summary": "S", "customfield_1": 2}}).warnings, [])

    def test_a_payload_that_is_no_update_is_refused_first(self):
        with self.assertRaises(ValidationError):
            self.update_json({"nothing": 1})
        self.assertEqual(self.gateway.calls, [])

    def test_a_live_run_is_not_checked(self):
        self.app.update_issue_from_payload.execute("PROJ-1", {"fields": {"priority": {"name": "Nope"}}})
        self.assertEqual(self.gateway.called("get_edit_metadata"), [])


class TransitionTest(ValidationTestCase):
    def transition(self, key="PROJ-2", **options):
        return self.app.validation.transition.execute(key, **options)

    def test_without_a_target_it_lists_the_options_and_asks_for_no_screens(self):
        result = self.transition()
        self.assertEqual((result.current_status, [item.id for item in result.options], result.screens), ("In Progress", ["111", "81"], []))
        self.assertIsNone(result.chosen)
        self.assertEqual(self.gateway.called("list_transition_screens"), [])

    def test_a_plain_transition_is_what_it_was(self):
        result = self.transition(to="done", comment=" Shipped. ")
        self.assertTrue(result.executed)
        self.assertEqual(self.gateway.called("transition_issue")[0][1:], ("PROJ-2", {"transition": {"id": "81"}, "update": {"comment": [{"add": {"body": "Shipped."}}]}}))
        self.assertEqual(self.gateway.issues["PROJ-2"].status.name, "Done")
        self.assertEqual((result.warnings, result.screens), ([], []))

    def test_a_dry_run_changes_nothing(self):
        result = self.transition(to="Done", dry_run=True)
        self.assertTrue(result.dry_run and not result.executed)
        self.assertEqual((result.chosen.id, result.payload), ("81", {"transition": {"id": "81"}}))
        self.assertEqual(self.gateway.called("transition_issue"), [])
        self.assertEqual(self.gateway.issues["PROJ-2"].status.name, "In Progress")

    def test_a_live_transition_asks_for_no_screens_and_is_not_checked(self):
        self.transition(to="Done", resolution="Nonsense", fields={"priority": {"name": "Nope"}})
        self.assertEqual(self.gateway.called("list_transition_screens"), [])
        self.assertEqual(
            self.gateway.called("transition_issue")[0][2], {"transition": {"id": "81"}, "fields": {"priority": {"name": "Nope"}, "resolution": {"name": "Nonsense"}}}
        )

    def test_resolution_and_fields_json_go_into_the_payload(self):
        self.gateway.screens["81"] = [RESOLUTION, FieldRule("fixVersions", "Fix Version/s", allowed=names("Release 1.0"))]
        result = self.transition(to="Done", resolution="Fixed", fields={"fixVersions": [{"name": "Release 1.0"}]}, comment="Done.", dry_run=True)
        self.assertEqual(
            result.payload,
            {
                "transition": {"id": "81"},
                "fields": {"fixVersions": [{"name": "Release 1.0"}], "resolution": {"name": "Fixed"}},
                "update": {"comment": [{"add": {"body": "Done."}}]},
            },
        )
        self.assertEqual((result.dry_run, result.executed, result.warnings), (True, False, []))
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_the_dry_run_refuses_a_resolution_that_the_screen_does_not_allow(self):
        self.gateway.screens["81"] = [RESOLUTION]
        with self.assertRaises(RejectedError) as caught:
            self.transition(to="Done", resolution="Done-ish", dry_run=True)
        self.assertEqual(caught.exception.exit_code, 6)
        self.assertEqual(caught.exception.message, "resolution 'Done-ish' is not available for the transition 'Finish' (81) of PROJ-2.")
        self.assertEqual(caught.exception.hint, "Allowed resolutions: Fixed, Won't Fix, Duplicate.")

    def test_the_dry_run_refuses_a_field_that_the_screen_does_not_have_and_lists_the_screen(self):
        self.gateway.screens["81"] = [RESOLUTION, COMMENT_FIELD]
        with self.assertRaises(RejectedError) as caught:
            self.transition(to="Done", fields={"fixVersions": [{"name": "Release 1.0"}]}, dry_run=True)
        self.assertIn("fix version cannot be set for the transition 'Finish' (81) of PROJ-2: it is not on the screen", caught.exception.message)
        self.assertIn("resolution, comment", caught.exception.hint)
        self.assertIn("jira transition PROJ-2 --to 81 --show-fields", caught.exception.hint)

    def test_a_resolution_for_a_transition_without_a_screen_is_refused(self):
        with self.assertRaises(RejectedError) as caught:
            self.transition(to="Done", resolution="Fixed", dry_run=True)
        self.assertIn("The screen has these fields: none", caught.exception.hint)

    def test_a_required_field_that_is_missing_is_a_warning_with_the_allowed_values(self):
        self.gateway.screens["81"] = [RESOLUTION]
        result = self.transition(to="Done", dry_run=True)
        self.assertEqual(result.warnings, ["Resolution (resolution) is required for the transition 'Finish' (81) of PROJ-2, and the payload does not set it (allowed: Fixed, Won't Fix, Duplicate)."])
        self.assertEqual(result.payload, {"transition": {"id": "81"}})

    def test_a_comment_satisfies_a_required_comment_field(self):
        self.gateway.screens["81"] = [FieldRule("comment", "Comment", required=True)]
        self.assertEqual(self.transition(to="Done", comment="Why.", dry_run=True).warnings, [])
        self.assertEqual(len(self.transition(to="Done", dry_run=True).warnings), 1)

    def test_a_dry_run_that_needs_nothing_has_no_warning(self):
        self.assertEqual(self.transition(to="Done", dry_run=True).warnings, [])

    def test_show_fields_lists_every_possible_transition_with_its_screen(self):
        self.gateway.screens["81"] = [RESOLUTION, COMMENT_FIELD]
        result = self.transition(show_fields=True)
        self.assertEqual([(item.transition.id, [rule.field_id for rule in item.fields]) for item in result.screens], [("111", []), ("81", ["resolution", "comment"])])
        self.assertEqual((result.chosen, result.executed, result.payload), (None, False, None))
        self.assertEqual([item.id for item in result.options], ["111", "81"])
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_show_fields_with_a_target_lists_only_that_transition(self):
        self.gateway.screens["81"] = [RESOLUTION]
        result = self.transition(to="Done", show_fields=True)
        self.assertEqual([item.transition.id for item in result.screens], ["81"])
        self.assertIsNone(result.chosen)

    def test_show_fields_with_an_unknown_target_lists_what_is_possible(self):
        with self.assertRaises(ValidationError) as caught:
            self.transition(to="Archived", show_fields=True)
        self.assertIn("Available:", caught.exception.hint)

    def test_show_fields_only_lists(self):
        for options in ({"comment": "x"}, {"resolution": "Fixed"}, {"fields": {"a": 1}}, {"dry_run": True}):
            with self.subTest(options=options), self.assertRaises(UsageError):
                self.transition(to="Done", show_fields=True, **options)
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_fields_and_a_resolution_need_a_target_like_a_comment_does(self):
        for options, expected in (
            ({"comment": "x"}, "--comment needs --to"),
            ({"resolution": "Fixed"}, "--resolution needs --to"),
            ({"fields": {"a": 1}}, "--fields-json needs --to"),
            ({"comment": "x", "resolution": "Fixed"}, "--comment and --resolution need --to"),
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError) as caught:
                self.transition(**options)
            self.assertIn(expected, caught.exception.message)

    def test_a_resolution_in_both_places_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.transition(to="Done", resolution="Fixed", fields={"resolution": {"name": "Duplicate"}})
        self.assertIn("set twice", caught.exception.message)

    def test_an_unknown_target_and_an_unknown_issue(self):
        with self.assertRaises(ValidationError):
            self.transition(to="Archived", dry_run=True)
        with self.assertRaises(NotFoundError):
            self.transition("PROJ-999", to="Done")
        with self.assertRaises(NotFoundError):
            self.transition("PROJ-999")
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_the_stale_guard_refuses_a_changed_issue_before_anything_is_sent(self):
        with self.assertRaises(ConflictError) as caught:
            self.transition(to="Done", if_unchanged_since="2026-10-01T00:00:00Z")  # PROJ-2 was updated on 2026-10-10
        self.assertEqual((caught.exception.exit_code, caught.exception.details["key"]), (3, "PROJ-2"))
        self.assertEqual(self.gateway.called("transition_issue"), [])
        self.assertEqual(self.gateway.called("list_transitions") + self.gateway.called("list_transition_screens"), [])

    def test_the_stale_guard_passes_for_an_issue_not_changed_since(self):
        self.assertTrue(self.transition(to="Done", dry_run=True, if_unchanged_since="2026-10-10T07:00:00Z").dry_run)
        self.assertTrue(self.transition(to="Done", if_unchanged_since="2026-10-10T07:00:00Z").executed)

    def test_the_guard_needs_a_target_and_does_not_go_with_a_listing_of_fields(self):
        with self.assertRaises(ValidationError) as caught:
            self.transition(if_unchanged_since="2026-10-10T07:00:00Z")
        self.assertIn("--if-unchanged-since needs --to", caught.exception.message)
        with self.assertRaises(UsageError):
            self.transition(show_fields=True, if_unchanged_since="2026-10-10T07:00:00Z")

    def test_the_options_of_a_dry_run_are_those_of_the_screens(self):
        result = self.transition(to="Done", dry_run=True)
        self.assertEqual([(item.id, item.name) for item in result.options], [("111", "Reopen"), ("81", "Finish")])
        self.assertEqual(len(self.gateway.called("list_transition_screens")), 1)
        self.assertEqual(self.gateway.called("list_transitions"), [])


class TransitionStandInTest(ValidationTestCase):
    """The `fields` of a transition are written to the issue like those of `update`: what `jira issue` showed in place of a mention, an address
    or a link must not overwrite the real one. The refusal is that of `update`; the read that every transition makes holds what is compared."""

    HOLDS = "which stands in place of a mention or an address that the CLI hides: sending it would overwrite the real one."

    def transition(self, key="PROJ-2", **options):
        return self.app.validation.transition.execute(key, **options)

    def assert_refused(self, message, **options):
        with self.assertRaises(ValidationError) as caught:
            self.transition(**options)
        self.assertEqual((caught.exception.exit_code, caught.exception.message), (1, message))
        self.assertEqual(self.gateway.called("transition_issue"), [])
        self.assertEqual(self.gateway.issues["PROJ-2"].status.name, "In Progress")
        return caught.exception

    def test_stand_ins_in_the_fields_of_a_transition_are_refused_with_the_words_of_update_before_anything_is_sent(self):
        fields = {"description": "x [~user] [email]", "labels": ["[email]"]}
        for options in ({}, {"dry_run": True}):
            with self.subTest(options=options):
                error = self.assert_refused(f"The new description and labels hold [~user], [email], {self.HOLDS}", to="Done", fields=fields, **options)
                self.assertEqual(error.details, {"fields": ["description", "labels"], "placeholders": ["[~user]", "[email]"]})
        self.assertEqual((self.gateway.called("list_transitions"), self.gateway.called("list_transition_screens")), ([], []))

    def test_the_read_that_every_transition_makes_holds_what_is_compared_and_there_is_no_other(self):
        self.assert_refused(f"The new summary, description and fix versions hold [~user], [email], {self.HOLDS}", to="Done", fields={
            "summary": "S [~user]", "description": "D [email]", "fixVersions": [{"name": "R [email]"}]})
        self.assertEqual([call[1:] for call in self.gateway.called("get_issue")], [("PROJ-2", ("status", "updated", "summary", "description", "fixVersions"))])

    def test_each_of_the_texts_and_names_is_judged(self):
        for fields, said in (
            ({"summary": "[email] Add retry"}, "The new summary holds [email]"),
            ({"description": "see [personal link]"}, "The new description holds [personal link]"),
            ({"labels": ["ok", "[~user]"]}, "The new labels hold [~user]"),
            ({"components": [{"name": "[email]"}]}, "The new components hold [email]"),
            ({"fixVersions": [{"name": "Release [email]"}]}, "The new fix versions hold [email]"),
        ):
            with self.subTest(fields=fields):
                self.assert_refused(f"{said}, {self.HOLDS}", to="Done", fields=fields)

    def test_the_hint_names_the_raw_call_of_a_transition_with_the_key_that_was_given(self):
        error = self.assert_refused(f"The new summary holds [email], {self.HOLDS}", to="Done", fields={"summary": "[email]"})
        self.assertIn("a text that holds people is best edited in Jira itself. If the stand-in is really what you want to write", error.hint)
        self.assertTrue(error.hint.endswith(
            'send it with `jira api POST issue/PROJ-2/transitions --data-file FILE --write`, a file with {"transition": {"id": "ID"}, "fields": {...}} '
            "(`jira transition PROJ-2` lists the ids; the raw call has no guard)."
        ))
        self.assertNotIn("PUT", error.hint)  # a transition is a POST to its `transitions`, not an update of the issue

    def test_the_acceptance_criteria_in_a_custom_field_are_judged_by_the_id_that_jira_names(self):
        error = self.assert_refused(f"The new acceptance criteria holds [~user], {self.HOLDS}", to="Done", fields={"customfield_10004": "GIVEN [~user]"})
        self.assertEqual(error.details["fields"], ["acceptance"])
        self.assertEqual(len(self.gateway.called("field_ids")), 1)
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("status", "updated", "acceptance"))

    def test_another_custom_field_and_the_resolution_are_not_judged_and_ask_for_nothing_more(self):
        self.transition(to="Done", fields={"customfield_10099": "Ask [~user]"})  # Jira is asked once whether it is the acceptance criteria: it is not
        self.assertEqual(len(self.gateway.called("field_ids")), 1)
        self.gateway.issues["PROJ-2"] = dataclasses.replace(self.gateway.issues["PROJ-2"], status=self.gateway.transitions["To Do"][0].to_status)  # In Progress again
        self.transition(to="Done", resolution="[email]")  # a resolution is a name from a list: the screen allows it or not
        self.assertEqual(len(self.gateway.called("transition_issue")), 2)
        self.assertEqual({call[2] for call in self.gateway.called("get_issue")}, {("status", "updated")})
        self.assertEqual(len(self.gateway.called("field_ids")), 1)  # the resolution asked for nothing

    def test_a_stand_in_that_jira_holds_already_may_stay_in_a_text_and_in_a_list(self):
        self.gateway.issues["PROJ-2"] = dataclasses.replace(self.gateway.issues["PROJ-2"], description="Write to [email] for details", labels=["[email]"])
        result = self.transition(to="Done", fields={"description": "Write to [email] today", "labels": ["[email]", "new"]})
        self.assertTrue(result.executed)
        self.assertEqual(self.gateway.called("transition_issue")[0][2]["fields"], {"description": "Write to [email] today", "labels": ["[email]", "new"]})
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("status", "updated", "description", "labels"))
        self.assertEqual(len(self.gateway.called("get_issue")), 1)

    def test_a_real_mention_and_names_without_people_are_sent_and_the_read_is_what_it_was(self):
        self.transition(to="Done", fields={"description": "Ask [~jdoe]", "labels": ["a"], "components": [{"name": "Demo"}]})
        self.assertEqual([call[1:] for call in self.gateway.called("get_issue")], [("PROJ-2", ("status", "updated"))])

    def test_a_comment_that_the_transition_adds_is_new_text_and_is_not_refused(self):
        result = self.transition(to="Done", comment="see [~user] and [email]")
        self.assertTrue(result.executed)
        self.assertEqual(self.gateway.called("transition_issue")[0][2]["update"], {"comment": [{"add": {"body": "see [~user] and [email]"}}]})
        self.assertEqual([call[2] for call in self.gateway.called("get_issue")], [("status", "updated")])  # nothing to compare a new comment with

    def test_the_comment_is_not_judged_but_the_fields_next_to_it_are(self):
        self.assert_refused(f"The new labels hold [email], {self.HOLDS}", to="Done", comment="see [~user]", fields={"labels": ["[email]"]})

    def test_the_issue_must_exist_and_the_stand_in_comes_before_the_stale_guard_and_before_the_choice_of_the_transition(self):
        with self.assertRaises(NotFoundError):
            self.transition("PROJ-999", to="Done", fields={"description": "[email]"})
        self.assert_refused(f"The new description holds [email], {self.HOLDS}", to="Done", fields={"description": "[email]"}, if_unchanged_since="2026-10-01T00:00:00Z")
        self.assert_refused(f"The new description holds [email], {self.HOLDS}", to="Archived", fields={"description": "[email]"})

    def test_the_key_is_the_one_the_command_normalises(self):
        with self.assertRaises(ValidationError) as caught:
            self.transition("proj-2", to="Done", fields={"summary": "[email]"})
        self.assertIn("`jira api POST issue/PROJ-2/transitions --data-file FILE --write`", caught.exception.hint)


class StaleIssueTypesTest(ValidationTestCase):
    """The list of issue types is kept for a week: a type that an administrator adds today is not in it. A dry run looks once more, in a
    list that bypasses the cache, before it refuses a type, and the refusal says that the list is fresh."""

    def setUp(self):
        super().setUp()
        stale = list(self.gateway.issue_types)  # the list of the day the cache was filled
        real = self.gateway.list_issue_types

        def listing(project_key, *, refresh=False):
            current = real(project_key, refresh=refresh)  # records the call
            return current if refresh else list(stale)

        self.gateway.list_issue_types = listing
        self.gateway.issue_types.append(IssueTypeInfo("10300", "Task"))

    def asked(self):
        return [call[2] for call in self.gateway.called("list_issue_types")]

    def test_a_new_type_by_id_passes_the_dry_run_without_the_flag(self):
        result = self.app.create_issue.execute(self.draft(issue_type="10300"), dry_run=True)
        self.assertEqual(result.payload["fields"]["issuetype"], {"id": "10300"})
        self.assertEqual(self.asked(), [False, True])  # the cache did not know it, the second read did

    def test_a_new_type_by_name_passes_the_dry_run_and_the_live_run_without_the_flag(self):
        self.assertEqual(self.app.create_issue.execute(self.draft(issue_type="task"), dry_run=True).payload["fields"]["issuetype"], {"id": "10300"})
        self.assertEqual(self.asked(), [False, True])
        self.gateway.calls.clear()
        self.app.create_issue.execute(self.draft(issue_type="Task"))
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["issuetype"], {"id": "10300"})
        self.assertEqual(self.asked(), [False, True])

    def test_create_json_with_a_new_type_passes_the_dry_run(self):
        payload = {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10300"}, "summary": "S"}}
        self.assertEqual(self.app.create_issue_from_payload.execute(payload, dry_run=True).warnings, [])
        self.assertEqual(self.asked(), [False, True])

    def test_the_flag_reaches_the_check_when_the_type_is_an_id(self):
        self.app.create_issue.execute(self.draft(issue_type="10300"), dry_run=True, refresh_metadata=True)
        self.assertEqual(self.asked(), [True])  # one read, and it bypassed the cache: nothing to look at again

    def test_the_flag_reaches_the_list_when_the_type_is_a_name(self):
        self.app.create_issue.execute(self.draft(issue_type="Task"), dry_run=True, refresh_metadata=True)
        self.assertEqual(self.asked(), [True])

    def test_a_type_that_the_cache_has_costs_no_second_read(self):
        self.app.create_issue.execute(self.draft(issue_type="Story"), dry_run=True)
        self.app.create_issue.execute(self.draft(issue_type="10200"), dry_run=True)
        self.assertEqual(self.asked(), [False, False])

    def test_a_type_that_does_not_exist_is_refused_after_one_more_read_and_the_hint_says_the_list_is_fresh(self):
        with self.assertRaises(RejectedError) as by_id:
            self.app.create_issue.execute(self.draft(issue_type="99999"), dry_run=True)
        self.assertEqual(self.asked(), [False, True])
        self.assertIn("Task (10300)", by_id.exception.hint)  # the list that the refusal shows is the fresh one
        self.assertTrue(by_id.exception.hint.endswith(FRESH_LIST))
        self.gateway.calls.clear()
        with self.assertRaises(ValidationError) as by_name:
            self.app.create_issue.execute(self.draft(issue_type="Nope"), dry_run=True)
        self.assertEqual(self.asked(), [False, True])
        self.assertTrue(by_name.exception.hint.endswith(FRESH_LIST))
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_an_ambiguous_name_is_not_a_miss_and_asks_for_no_second_list(self):
        self.gateway.issue_types.append(IssueTypeInfo("10301", "task"))
        stale = [IssueTypeInfo("1", "Same"), IssueTypeInfo("2", "same")]
        self.gateway.list_issue_types = lambda project_key, *, refresh=False: list(stale)
        with self.assertRaises(ValidationError) as caught:
            self.app.create_issue.execute(self.draft(issue_type="Same"), dry_run=True)
        self.assertIn("is ambiguous", caught.exception.message)
        self.assertNotIn(FRESH_LIST, caught.exception.hint)


class ChangedMetadataTest(ValidationTestCase):
    """What a project offers can change while the program runs (`jira mcp` runs for hours). A value that the metadata read earlier does not
    have is looked up once more in what Jira says now before it is refused."""

    def test_a_component_that_is_added_while_the_program_runs_passes_the_next_dry_run(self):
        with self.assertRaises(RejectedError):
            self.create(components=["Fresh"])
        self.assertEqual(len(self.gateway.called("get_create_fields")), 1)  # the first answer is fresh: no second look
        self.gateway.allowed_values["components"] = names("Demo", "Backend", "Fresh")
        self.assertEqual(self.create(components=["Fresh"]).warnings, [])
        self.assertEqual(len(self.gateway.called("get_create_fields")), 2)

    def test_a_value_that_is_still_refused_costs_one_more_read_and_the_refusal_lists_the_fresh_values(self):
        self.create(components=["Demo"])
        self.gateway.allowed_values["components"] = names("Demo", "Backend", "Fresh")
        with self.assertRaises(RejectedError) as caught:
            self.create(components=["Nope"])
        self.assertEqual(len(self.gateway.called("get_create_fields")), 2)
        self.assertEqual(caught.exception.hint, "Allowed components: Demo, Backend, Fresh.")

    def test_a_valid_value_never_reads_the_metadata_again(self):
        for _ in range(4):
            self.create(priority="High", components=["Demo"])
        self.assertEqual(len(self.gateway.called("get_create_fields")), 1)

    def test_a_version_that_is_released_while_the_program_runs_passes_an_update_dry_run(self):
        with self.assertRaises(RejectedError):
            self.update(IssueChanges(fix_versions=["Release 3.0"]))
        self.assertEqual(len(self.gateway.called("get_edit_metadata")), 1)
        self.gateway.allowed_values["fixVersions"] = names("Release 1.0", "Release 2.0", "Release 3.0")
        self.assertEqual(self.update(IssueChanges(fix_versions=["Release 3.0"])).warnings, [])
        self.assertEqual(len(self.gateway.called("get_edit_metadata")), 2)

    def test_the_payload_forms_of_create_and_update_are_looked_up_again_too(self):
        payload = {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S", "components": [{"name": "Fresh"}]}}
        with self.assertRaises(RejectedError):
            self.app.create_issue_from_payload.execute(payload, dry_run=True)
        self.gateway.allowed_values["components"] = names("Demo", "Fresh")
        self.assertEqual(self.app.create_issue_from_payload.execute(payload, dry_run=True).warnings, [])
        with self.assertRaises(RejectedError):
            self.app.update_issue_from_payload.execute("PROJ-1", {"fields": {"priority": {"name": "Urgent"}}}, dry_run=True)
        self.gateway.allowed_values["priority"] = names("Urgent", "High")
        self.assertEqual(self.app.update_issue_from_payload.execute("PROJ-1", {"fields": {"priority": {"name": "Urgent"}}}, dry_run=True).warnings, [])


if __name__ == "__main__":
    unittest.main()
