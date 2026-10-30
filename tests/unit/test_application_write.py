"""Use cases that change Jira: comment, link, update, create and the imports. The transition lives in test_validation_application."""
import dataclasses
import unittest

from jira_dc_cli.domain.drafts import IssueChanges, IssueDraft
from jira_dc_cli.domain.errors import ConfigError, ConflictError, NotFoundError, RejectedError, ValidationError
from jira_dc_cli.domain.validation import FieldRule
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, MemoryFiles, RecordingDiagnostics, build_app


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class CommentTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_add(self):
        result = self.app.add_comment.execute("PROJ-1", " Hello \n")
        self.assertEqual((result.comment_id, result.payload, result.dry_run), ("11", {"body": "Hello"}, False))
        self.assertEqual(self.gateway.called("add_comment")[0][1:], ("PROJ-1", {"body": "Hello"}))

    def test_dry_run_checks_the_issue_and_sends_nothing(self):
        result = self.app.add_comment.execute("PROJ-1", "Hello", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.gateway.called("add_comment"), [])
        self.assertEqual(self.gateway.called("get_issue")[0][1], "PROJ-1")
        with self.assertRaises(NotFoundError):
            self.app.add_comment.execute("PROJ-999", "Hello", dry_run=True)

    def test_empty_comment(self):
        with self.assertRaises(ValidationError):
            self.app.add_comment.execute("PROJ-1", "  \n")


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_outward(self):
        result = self.app.link_issues.execute("proj-1", "PROJ-2", "blocks")
        self.assertEqual(result.relation, "PROJ-1 blocks PROJ-2")
        self.assertEqual(result.confirmed, "PROJ-1 blocks PROJ-2")
        self.assertEqual(self.gateway.called("create_link")[0][1], {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-1"}, "inwardIssue": {"key": "PROJ-2"}})

    def test_inverse_reads_back_the_stored_direction(self):
        result = self.app.link_issues.execute("PROJ-1", "PROJ-2", "Blocks", inverse=True)
        self.assertEqual(self.gateway.called("create_link")[0][1], {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-2"}, "inwardIssue": {"key": "PROJ-1"}})
        self.assertEqual(result.confirmed, "PROJ-1 is blocked by PROJ-2")

    def test_dry_run(self):
        result = self.app.link_issues.execute("PROJ-1", "PROJ-2", "Blocks", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.payload["type"], {"name": "Blocks"})
        self.assertEqual(self.gateway.called("create_link"), [])

    def test_refusals(self):
        with self.assertRaises(ValidationError):
            self.app.link_issues.execute("PROJ-1", "PROJ-1", "Blocks")
        with self.assertRaises(ValidationError):
            self.app.link_issues.execute("PROJ-1", "PROJ-2", "requires")
        self.assertEqual(self.gateway.called("create_link"), [])

    def test_link_types_are_sorted(self):
        names = [link_type.name for link_type in self.app.list_link_types.execute()]
        self.assertEqual(names, sorted(names))


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def test_update(self):
        result = self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New", labels=["x"]))
        self.assertEqual(result.payload, {"fields": {"summary": "New", "labels": ["x"]}})
        self.assertEqual(self.gateway.called("update_issue")[0][1], "PROJ-1")
        self.assertEqual(self.gateway.issues["PROJ-1"].summary, "New")

    def test_nothing_to_update(self):
        with self.assertRaises(ValidationError):
            self.app.update_issue.execute("PROJ-1", IssueChanges())

    def test_dry_run_checks_the_issue_and_sends_nothing(self):
        result = self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New"), dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.gateway.called("update_issue"), [])
        with self.assertRaises(NotFoundError):
            self.app.update_issue.execute("PROJ-999", IssueChanges(summary="New"), dry_run=True)

    def test_stale_guard_blocks_the_write(self):
        with self.assertRaises(ConflictError):
            self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New"), if_unchanged_since="2026-10-01T00:00:00Z")
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_stale_guard_passes_when_nothing_changed_since(self):
        self.app.update_issue.execute("PROJ-1", IssueChanges(summary="New"), if_unchanged_since="2026-10-10T07:00:00Z")
        self.assertEqual(len(self.gateway.called("update_issue")), 1)

    def test_update_from_payload(self):
        self.app.update_issue_from_payload.execute("PROJ-1", {"update": {"labels": [{"add": "x"}]}})
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"update": {"labels": [{"add": "x"}]}})
        with self.assertRaises(ValidationError):
            self.app.update_issue_from_payload.execute("PROJ-1", {"nothing": 1})


STAND_INS_MESSAGE = "which stands in place of a mention or an address that the CLI hides: sending it would overwrite the real one."
READ_DESCRIPTION = "Ask [~user] or [email]\nSecond line"  # what `jira issue PROJ-1` shows of its description: the people are hidden


class UpdateStandInTest(unittest.TestCase):
    """`jira issue` shows [~user], [email] and [personal link] where Jira holds a mention, an address or a link. An update from that text
    would overwrite the real one: it is refused, as `comment --edit`, `push` and the imports refuse it."""

    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def update(self, changes, **options):
        return self.app.update_issue.execute("PROJ-1", changes, **options)

    def assert_refused(self, call, message):
        with self.assertRaises(ValidationError) as caught:
            call()
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertEqual(caught.exception.message, message)
        self.assertEqual(self.gateway.called("update_issue"), [])
        return caught.exception

    def test_the_description_that_was_read_cannot_be_sent_back_with_one_more_line(self):
        error = self.assert_refused(
            lambda: self.update(IssueChanges(description=READ_DESCRIPTION + "\nThird line")),
            f"The new description holds [~user], [email], {STAND_INS_MESSAGE}",
        )
        self.assertIn("[~username], see `jira users resolve`", error.hint)
        self.assertIn("best edited in Jira itself", error.hint)
        self.assertEqual(error.details, {"fields": ["description"], "placeholders": ["[~user]", "[email]"]})
        self.assertEqual(self.gateway.issues["PROJ-1"].description, "Ask [~jdoe] or jane.doe@example.com\nSecond line")

    def test_the_dry_run_refuses_it_too_and_so_does_a_guard_that_would_pass(self):
        message = f"The new description holds [~user], [email], {STAND_INS_MESSAGE}"
        self.assert_refused(lambda: self.update(IssueChanges(description=READ_DESCRIPTION), dry_run=True), message)
        self.assert_refused(lambda: self.update(IssueChanges(description=READ_DESCRIPTION), if_unchanged_since="2026-10-10T07:00:00Z"), message)

    def test_the_placeholder_is_refused_before_the_stale_guard_like_in_an_edit_of_a_comment(self):
        stale = "2026-10-01T00:00:00Z"  # PROJ-1 changed on 2026-10-10: the guard alone would be a conflict
        self.assert_refused(
            lambda: self.update(IssueChanges(description=READ_DESCRIPTION), if_unchanged_since=stale),
            f"The new description holds [~user], [email], {STAND_INS_MESSAGE}",
        )

    def test_the_summary(self):
        self.assert_refused(lambda: self.update(IssueChanges(summary="Mail [email] now")), f"The new summary holds [email], {STAND_INS_MESSAGE}")

    def test_a_personal_link_is_a_stand_in_too(self):
        self.assert_refused(lambda: self.update(IssueChanges(description="See [personal link]")), f"The new description holds [personal link], {STAND_INS_MESSAGE}")

    def test_a_text_in_fields_json_is_judged_like_a_text_of_an_option(self):
        self.assert_refused(
            lambda: self.update(IssueChanges(fields={"description": "Ask [~user]"})), f"The new description holds [~user], {STAND_INS_MESSAGE}"
        )
        self.assert_refused(lambda: self.update(IssueChanges(fields={"summary": "[email]"}, labels=["x"])), f"The new summary holds [email], {STAND_INS_MESSAGE}")

    def test_several_texts_are_named_together(self):
        self.assert_refused(
            lambda: self.update(IssueChanges(summary="About [~user]", description="Write to [email]")),
            f"The new summary and description hold [~user], [email], {STAND_INS_MESSAGE}",
        )

    def test_the_acceptance_criteria_in_fields_json_are_judged_by_the_id_that_jira_names(self):
        error = self.assert_refused(
            lambda: self.update(IssueChanges(fields={"customfield_10004": "GIVEN [~user]"})),
            f"The new acceptance criteria holds [~user], {STAND_INS_MESSAGE}",
        )
        self.assertEqual(error.details["fields"], ["acceptance"])
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("summary", "updated", "acceptance"))
        self.assertEqual(len(self.gateway.called("field_ids")), 1)

    def test_another_custom_field_is_not_judged_and_the_literal_in_the_acceptance_of_jira_may_stay(self):
        self.update(IssueChanges(fields={"customfield_10099": "Ask [~user]"}))
        self.assertEqual(len(self.gateway.called("update_issue")), 1)
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], acceptance="Mail [email]")
        self.update(IssueChanges(fields={"customfield_10004": "Mail [email] again"}))
        self.assertEqual(len(self.gateway.called("update_issue")), 2)

    def test_a_stand_in_that_jira_holds_already_is_a_text_somebody_wrote_and_may_stay(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], description="Write to [email] for details")
        result = self.update(IssueChanges(description="Write to [email] for details, please"))
        self.assertEqual(result.payload, {"fields": {"description": "Write to [email] for details, please"}})
        self.assertEqual(self.gateway.called("update_issue")[0][2], result.payload)
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("summary", "updated", "description"))

    def test_a_stand_in_that_jira_does_not_hold_is_refused_next_to_one_that_it_does(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], description="Write to [email] for details")
        self.assert_refused(
            lambda: self.update(IssueChanges(description="Write to [email] and ask [~user]")),
            f"The new description holds [~user], {STAND_INS_MESSAGE}",
        )

    def test_a_real_mention_and_a_text_without_people_pass_and_a_live_run_asks_for_nothing(self):
        self.update(IssueChanges(summary="Plain", description="Ask [~jdoe] and mail jane.doe@example.com", labels=["x"]))
        self.update(IssueChanges(fields={"description": "Plain text"}))
        self.assertEqual(len(self.gateway.called("update_issue")), 2)
        self.assertEqual((self.gateway.called("get_issue"), self.gateway.called("field_ids")), ([], []))

    def test_what_a_valid_dry_run_reads_and_says_is_what_it_was(self):
        result = self.update(IssueChanges(summary="Plain", description="Plain too"), dry_run=True)
        self.assertEqual((result.dry_run, result.warnings, result.payload), (True, [], {"fields": {"summary": "Plain", "description": "Plain too"}}))
        self.assertEqual([call[1:] for call in self.gateway.called("get_issue")], [("PROJ-1", ("summary", "updated"))])
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_a_guard_and_a_text_with_a_stand_in_that_may_stay_need_one_read_only(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], description="Write to [email]")
        self.update(IssueChanges(description="Write to [email] today"), if_unchanged_since="2026-10-10T07:00:00Z")
        self.assertEqual([call[1:] for call in self.gateway.called("get_issue")], [("PROJ-1", ("summary", "updated", "description"))])

    def test_the_refusal_comes_before_the_assignee_is_looked_up_and_after_the_issue_is_found(self):
        self.assert_refused(
            lambda: self.update(IssueChanges(description=READ_DESCRIPTION, assignee="zed")), f"The new description holds [~user], [email], {STAND_INS_MESSAGE}"
        )
        self.assertEqual(self.gateway.called("find_users"), [])
        with self.assertRaises(NotFoundError):
            self.app.update_issue.execute("PROJ-999", IssueChanges(description=READ_DESCRIPTION))

    def test_update_json_judges_fields_and_the_set_of_update_the_same_way(self):
        def send(payload, **options):
            return self.app.update_issue_from_payload.execute("PROJ-1", payload, **options)

        for payload, what in (
            ({"fields": {"description": READ_DESCRIPTION}}, "description holds [~user], [email]"),
            ({"fields": {"summary": "Mail [email]"}}, "summary holds [email]"),
            ({"update": {"description": [{"set": "See [personal link]"}]}}, "description holds [personal link]"),
            ({"fields": {"customfield_10004": "[~user]"}}, "acceptance criteria holds [~user]"),
        ):
            for options in ({}, {"dry_run": True}):
                with self.subTest(payload=payload, options=options), self.assertRaises(ValidationError) as caught:
                    send(payload, **options)
                self.assertIn(f"The new {what}, {STAND_INS_MESSAGE}", caught.exception.message)
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_json_with_nothing_to_overwrite_passes_and_reads_nothing(self):
        for payload in (
            {"fields": {"summary": "Fine", "description": "Ask [~jdoe]"}},
            {"update": {"labels": [{"add": "fine"}, {"remove": "[~user]"}], "description": [{"remove": "x"}]}},
            {"fields": {"customfield_10099": "[~user]", "customfield_10100": ["[email]"]}},  # custom fields other than the acceptance are not judged
            {"fields": {"versions": [{"name": "[email]"}]}},  # `jira issue` does not show the affected versions
        ):
            self.app.update_issue_from_payload.execute("PROJ-1", payload)
        self.assertEqual(len(self.gateway.called("update_issue")), 4)
        self.assertEqual(self.gateway.called("get_issue"), [])

    def test_update_json_with_a_literal_that_jira_holds_passes(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], summary="Mail [email]")
        self.app.update_issue_from_payload.execute("PROJ-1", {"fields": {"summary": "Mail [email] now"}})
        self.assertEqual(len(self.gateway.called("update_issue")), 1)

    # -- the names of labels, components and fix versions ------------------------------------------------------------------------------

    def test_the_hint_names_the_way_out_with_the_real_key_for_every_form(self):
        error = self.assert_refused(lambda: self.update(IssueChanges(summary="[email] Add retry")), f"The new summary holds [email], {STAND_INS_MESSAGE}")
        self.assertIn("a text that holds people is best edited in Jira itself. If the stand-in is really what you want to write", error.hint)
        self.assertTrue(error.hint.endswith("send it with `jira api PUT issue/PROJ-1 --data-file FILE --write` (the raw call has no guard)."))
        with self.assertRaises(ValidationError) as other:
            self.app.update_issue.execute("proj-2", IssueChanges(labels=["[email]"]))
        self.assertIn("`jira api PUT issue/PROJ-2 --data-file FILE --write`", other.exception.hint)  # the key as the command normalises it
        with self.assertRaises(ValidationError) as raw:
            self.app.update_issue_from_payload.execute("PROJ-3", {"fields": {"summary": "[email]"}})
        self.assertIn("`jira api PUT issue/PROJ-3 --data-file FILE --write`", raw.exception.hint)

    def test_labels_components_and_fix_versions_are_judged_item_by_item(self):
        for changes, called, mark, field_name in (
            (IssueChanges(labels=["[email]", "backend"]), "labels", "[email]", "labels"),
            (IssueChanges(components=["[~user]"]), "components", "[~user]", "components"),
            (IssueChanges(fix_versions=["Release [personal link]"]), "fix versions", "[personal link]", "fixVersions"),
        ):
            for options in ({}, {"dry_run": True}):
                with self.subTest(field=field_name, options=options):
                    error = self.assert_refused(lambda: self.update(changes, **options), f"The new {called} hold {mark}, {STAND_INS_MESSAGE}")
                    self.assertEqual(error.details, {"fields": [field_name], "placeholders": [mark]})
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("summary", "updated", "labels"))
        self.assertEqual(self.gateway.called("get_issue")[1][2], ("summary", "updated", "labels"))  # the dry run reads the same
        self.assertEqual(self.gateway.called("get_issue")[2][2], ("summary", "updated", "components"))

    def test_the_names_in_fields_json_are_judged_like_those_of_an_option(self):
        for fields, called, mark in (
            ({"labels": ["[email]"]}, "labels", "[email]"),
            ({"components": [{"name": "[email]"}]}, "components", "[email]"),
            ({"fixVersions": [{"name": "[~user]"}]}, "fix versions", "[~user]"),
        ):
            with self.subTest(fields=fields):
                self.assert_refused(lambda: self.update(IssueChanges(fields=fields)), f"The new {called} hold {mark}, {STAND_INS_MESSAGE}")

    def test_a_name_that_jira_holds_verbatim_may_stay_and_the_new_one_is_refused(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], labels=["[email]", "backend"])
        self.update(IssueChanges(labels=["[email]", "backend", "new"]))
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"labels": ["[email]", "backend", "new"]}})
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("summary", "updated", "labels"))
        with self.assertRaises(ValidationError) as caught:  # the stand-in that Jira holds stays out of the message: only the new one is named
            self.update(IssueChanges(labels=["[email]", "[~user]"]))
        self.assertEqual(caught.exception.message, f"The new labels hold [~user], {STAND_INS_MESSAGE}")
        self.assertEqual(len(self.gateway.called("update_issue")), 1)  # nothing more was sent

    def test_the_list_that_was_read_cannot_be_sent_back_when_jira_holds_the_real_names(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], labels=["jane.doe@example.com", "backend"], components=["Demo"])
        self.assert_refused(lambda: self.update(IssueChanges(labels=["[email]", "backend"])), f"The new labels hold [email], {STAND_INS_MESSAGE}")
        self.assertEqual(self.gateway.issues["PROJ-1"].labels, ["jane.doe@example.com", "backend"])

    def test_a_text_and_a_list_are_named_together_and_compared_each_with_its_own_current_value(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], labels=["[email]"])
        error = self.assert_refused(
            lambda: self.update(IssueChanges(summary="About [~user]", labels=["[email]"], components=["[email]"])),
            f"The new summary and components hold [~user], [email], {STAND_INS_MESSAGE}",
        )
        self.assertEqual(error.details, {"fields": ["summary", "components"], "placeholders": ["[~user]", "[email]"]})
        self.assertEqual(self.gateway.called("get_issue")[0][2], ("summary", "updated", "labels", "components"))

    def test_a_guard_and_a_name_that_may_stay_need_one_read_only(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], fix_versions=["[email]"])
        self.update(IssueChanges(fix_versions=["[email]"]), if_unchanged_since="2026-10-10T07:00:00Z")
        self.assertEqual([call[1:] for call in self.gateway.called("get_issue")], [("PROJ-1", ("summary", "updated", "fixVersions"))])

    def test_names_without_stand_ins_cost_no_request_in_a_live_run_and_the_same_read_in_a_dry_run(self):
        self.update(IssueChanges(labels=["a", "b"], components=["Demo"], fix_versions=["Release 1.0"]))
        self.assertEqual((self.gateway.called("get_issue"), self.gateway.called("field_ids")), ([], []))
        self.update(IssueChanges(labels=["a"], components=["Demo"]), dry_run=True)
        self.assertEqual([call[1:] for call in self.gateway.called("get_issue")], [("PROJ-1", ("summary", "updated"))])

    def test_update_json_judges_the_names_in_fields_and_in_the_add_and_the_set_of_update(self):
        def send(payload, **options):
            return self.app.update_issue_from_payload.execute("PROJ-1", payload, **options)

        for payload, what in (
            ({"fields": {"labels": ["[email]"]}}, "labels hold [email]"),
            ({"update": {"labels": [{"add": "[email]"}]}}, "labels hold [email]"),
            ({"update": {"labels": [{"set": ["x", "[email]"]}]}}, "labels hold [email]"),
            ({"update": {"components": [{"add": {"name": "[~user]"}}]}}, "components hold [~user]"),
            ({"update": {"components": [{"set": [{"name": "Demo"}, {"name": "[~user]"}]}]}}, "components hold [~user]"),
            ({"fields": {"fixVersions": [{"name": "[email]"}]}}, "fix versions hold [email]"),
            ({"update": {"fixVersions": [{"add": {"name": "[personal link]"}}]}}, "fix versions hold [personal link]"),
        ):
            for options in ({}, {"dry_run": True}):
                with self.subTest(payload=payload, options=options), self.assertRaises(ValidationError) as caught:
                    send(payload, **options)
                self.assertIn(f"The new {what}, {STAND_INS_MESSAGE}", caught.exception.message)
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_json_with_a_name_that_jira_holds_passes(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], labels=["[email]"])
        self.app.update_issue_from_payload.execute("PROJ-1", {"update": {"labels": [{"add": "[email]"}]}})
        self.assertEqual(len(self.gateway.called("update_issue")), 1)

    def test_an_update_json_with_names_that_hold_no_stand_in_reads_nothing(self):
        self.app.update_issue_from_payload.execute("PROJ-1", {"fields": {"labels": ["backend", "api"], "components": [{"name": "Demo"}]}})
        self.app.update_issue_from_payload.execute("PROJ-1", {"update": {"labels": [{"add": "x"}], "fixVersions": [{"set": [{"name": "Release 1.0"}]}]}})
        self.assertEqual(self.gateway.called("get_issue"), [])


class CreateStandInTest(unittest.TestCase):
    """A new issue has no old text to overwrite: a stand-in in its dry run is a note, not a refusal."""

    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def draft(self, **changes):
        return IssueDraft(**{"project": "PROJ", "issue_type": "Story", "summary": "New story", **changes})

    def test_the_dry_run_notes_it_and_still_shows_the_payload(self):
        result = self.app.create_issue.execute(self.draft(description=READ_DESCRIPTION), dry_run=True)
        self.assertEqual(result.payload["fields"]["description"], READ_DESCRIPTION)
        self.assertEqual(
            result.warnings,
            [
                "The description of the new issue holds [~user], [email], which stands in place of a mention or an address that the CLI hides: "
                "Jira would keep it as plain text, not as the real person or address."
            ],
        )
        self.assertEqual((result.dry_run, self.gateway.called("create_issue")), (True, []))

    def test_the_note_comes_after_the_other_warnings_and_names_every_text(self):
        self.gateway.required_to_create["10200"] = [FieldRule("customfield_10005", "Epic Name", required=True)]
        result = self.app.create_issue.execute(self.draft(summary="About [~user]", description="Write to [email]"), dry_run=True)
        self.assertEqual(len(result.warnings), 2)
        self.assertIn("Epic Name", result.warnings[0])
        self.assertIn("The summary and description of the new issue hold [~user], [email]", result.warnings[1])

    def test_the_acceptance_criteria_of_a_payload_count_when_jira_names_their_field(self):
        result = self.app.create_issue.execute(self.draft(fields={"customfield_10004": "GIVEN [~user]"}), dry_run=True)
        self.assertIn("The acceptance criteria of the new issue holds [~user]", result.warnings[0])

    def test_a_live_create_sends_the_text_as_it_is_with_no_note(self):
        result = self.app.create_issue.execute(self.draft(description=READ_DESCRIPTION))
        self.assertEqual(result.warnings, [])
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["description"], READ_DESCRIPTION)

    def test_a_text_without_stand_ins_has_no_note_and_asks_nothing_more(self):
        result = self.app.create_issue.execute(self.draft(description="Ask [~jdoe]", fields={"customfield_10099": "text"}), dry_run=True)
        self.assertEqual(result.warnings, [])
        self.assertEqual(self.gateway.called("field_ids"), [])

    def test_create_json_notes_it_in_the_dry_run_too(self):
        payload = {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "Mail [email]", "description": "Ask [~user]"}}
        result = self.app.create_issue_from_payload.execute(payload, dry_run=True)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("The summary and description of the new issue hold [~user], [email]", result.warnings[0])
        self.assertEqual(self.app.create_issue_from_payload.execute(payload).warnings, [])
        self.assertEqual(len(self.gateway.called("create_issue")), 1)

    def test_the_names_of_labels_components_and_fix_versions_get_the_same_note(self):
        result = self.app.create_issue.execute(self.draft(labels=["[email]", "backend"]), dry_run=True)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("The labels of the new issue hold [email], which stands in place of a mention or an address", result.warnings[0])
        payload = {"fields": {"project": {"key": "PROJ"}, "issuetype": {"name": "Story"}, "summary": "S", "components": [{"name": "[~user]"}], "fixVersions": [{"name": "[email]"}]}}
        noted = self.app.create_issue_from_payload.execute(payload, dry_run=True)  # the type is named, not given by id: nothing else is checked
        self.assertEqual(len(noted.warnings), 1)
        self.assertIn("The components and fix versions of the new issue hold [~user], [email]", noted.warnings[0])
        self.assertEqual(self.app.create_issue.execute(self.draft(labels=["[email]"])).warnings, [])  # a live create sends it and says nothing


class CreateTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.app = build_app(gateway=self.gateway)

    def draft(self, **changes):
        values = dict(project="", issue_type="story", summary="New story")
        values.update(changes)
        return IssueDraft(**values)

    def test_type_name_is_resolved_and_the_project_defaults(self):
        result = self.app.create_issue.execute(self.draft(labels=["a"]))
        fields = self.gateway.called("create_issue")[0][1]["fields"]
        self.assertEqual((fields["project"], fields["issuetype"], fields["labels"]), ({"key": "PROJ"}, {"id": "10200"}, ["a"]))
        self.assertEqual((result.key, result.created.key), ("PROJ-101", "PROJ-101"))

    def test_numeric_type_needs_no_lookup(self):
        self.app.create_issue.execute(self.draft(issue_type="10201"))
        self.assertEqual(self.gateway.called("list_issue_types"), [])

    def test_reporter_comes_from_the_draft_then_the_settings_then_the_token(self):
        self.app.create_issue.execute(self.draft(reporter="ann"))
        build_app(gateway=self.gateway, settings=settings(reporter="bob")).create_issue.execute(self.draft())
        self.app.create_issue.execute(self.draft())
        reporters = [call[1]["fields"]["reporter"]["name"] for call in self.gateway.called("create_issue")]
        self.assertEqual(reporters, ["ann", "bob", "jdoe"])

    def test_missing_reporter_is_a_config_error(self):
        self.gateway.user = dataclasses.replace(self.gateway.user, name="")
        with self.assertRaises(ConfigError):
            self.app.create_issue.execute(self.draft())

    def test_dry_run_creates_nothing(self):
        result = self.app.create_issue.execute(self.draft(), dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual((result.key, self.gateway.called("create_issue")), ("", []))

    def test_validation(self):
        no_project = build_app(gateway=self.gateway, settings=settings(project=""))
        with self.assertRaises(ValidationError):
            no_project.create_issue.execute(self.draft())
        with self.assertRaises(ValidationError):
            self.app.create_issue.execute(self.draft(summary=""))
        with self.assertRaises(ValidationError):
            self.app.create_issue.execute(self.draft(issue_type="Task"))

    def test_create_from_payload(self):
        payload = {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S"}}
        self.assertEqual(self.app.create_issue_from_payload.execute(payload).key, "PROJ-101")
        self.assertEqual(self.app.create_issue_from_payload.execute(payload, dry_run=True).key, "")
        with self.assertRaises(ValidationError):
            self.app.create_issue_from_payload.execute({"fields": []})


TICKET = """---
source_id: S-1
source_key: SRC-1
summary: Imported story
issuetype: Story
labels: a, b
fix_versions: Release 1.0
---
# Heading

Body *text*
"""


class ImportMarkdownTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles({"t/001-story.md": TICKET})
        self.diagnostics = RecordingDiagnostics()
        self.app = build_app(gateway=self.gateway, files=self.files, diagnostics=self.diagnostics)

    def fields(self):
        return self.gateway.called("create_issue")[-1][1]["fields"]

    def test_create_from_front_matter(self):
        outcome = self.app.import_markdown.execute("t/001-story.md")
        self.assertEqual((outcome.action, outcome.key), ("created", "PROJ-101"))
        self.assertEqual((self.fields()["summary"], self.fields()["labels"], self.fields()["fixVersions"]), ("Imported story", ["a", "b"], [{"name": "Release 1.0"}]))
        self.assertEqual(self.fields()["description"], "# Heading\n\nBody *text*")

    def test_wiki_conversion(self):
        self.app.import_markdown.execute("t/001-story.md", wiki=True)
        self.assertEqual(self.fields()["description"], "h1. Heading\n\nBody _text_")

    def test_issue_key_means_update(self):
        self.files.files["t/up.md"] = "---\nissue_key: PROJ-1\nsummary: Renamed\n---\nNew body\n"
        outcome = self.app.import_markdown.execute("t/up.md")
        self.assertEqual(outcome.action, "updated")
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"summary": "Renamed", "description": "New body"}})
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_an_update_from_a_text_that_stands_in_place_of_personal_data_is_refused(self):
        for name, text in {
            "a mention": "---\nissue_key: PROJ-1\nsummary: Renamed\n---\nAsk [~user] about it.\n",
            "an address": "---\nissue_key: PROJ-1\nsummary: Mail [email]\n---\nBody\n",
            "a personal link": "---\nissue_key: PROJ-1\nsummary: S\n---\nSee [personal link] now.\n",
            "a field": '---\nissue_key: PROJ-1\nsummary: S\nfields_json: {"customfield_10004": "Ask [~user]"}\n---\nBody\n',
        }.items():
            with self.subTest(name=name):
                self.files.files["t/up.md"] = text
                with self.assertRaises(ValidationError) as caught:
                    self.app.import_markdown.execute("t/up.md")
                self.assertIn("jira push", caught.exception.hint)
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_a_new_issue_may_hold_the_text(self):
        self.files.files["t/new.md"] = "---\nsummary: Mail [email]\nissuetype: Story\n---\nAsk [~user].\n"
        self.assertEqual(self.app.import_markdown.execute("t/new.md").action, "created")

    def test_the_names_in_a_list_that_stand_in_place_of_personal_data_are_refused_too(self):
        for name, line in {"labels": "labels: [email],x", "components": "components: Team [~user]", "fix versions": "fix_versions: [email]"}.items():
            with self.subTest(name=name):
                self.files.files["t/up.md"] = f"---\nissue_key: PROJ-1\nsummary: S\n{line}\n---\nBody\n"
                with self.assertRaises(ValidationError):
                    self.app.import_markdown.execute("t/up.md")
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_dry_run_sends_nothing(self):
        outcome = self.app.import_markdown.execute("t/001-story.md", dry_run=True)
        self.assertTrue(outcome.dry_run and outcome.payload)
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_an_assignee_by_display_name_is_resolved_like_in_create_and_update(self):
        self.files.files["t/a.md"] = "---\nsummary: S\nissuetype: Story\nassignee: Alex Smith\n---\nBody\n"
        self.app.import_markdown.execute("t/a.md")
        self.assertEqual(self.fields()["assignee"], {"name": "asmith"})
        self.files.files["t/u.md"] = "---\nissue_key: PROJ-1\nsummary: Renamed\nassignee: Alex Smith\n---\nBody\n"
        self.app.import_markdown.execute("t/u.md")
        self.assertEqual(self.gateway.called("update_issue")[0][2]["fields"]["assignee"], {"name": "asmith"})

    def test_an_ambiguous_assignee_creates_nothing(self):
        self.files.files["t/a.md"] = "---\nsummary: S\nissuetype: Story\nassignee: Sam Sample\n---\nBody\n"
        with self.assertRaises(ValidationError):
            self.app.import_markdown.execute("t/a.md")
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_the_dry_run_checks_the_priority_and_a_live_import_leaves_that_to_jira(self):
        self.files.files["t/p.md"] = "---\nsummary: S\nissuetype: Story\npriority: Nope\n---\nBody\n"
        with self.assertRaises(RejectedError) as caught:
            self.app.import_markdown.execute("t/p.md", dry_run=True)
        self.assertIn("Allowed priorities", caught.exception.hint)
        self.app.import_markdown.execute("t/p.md")
        self.assertEqual(self.fields()["priority"], {"name": "Nope"})

    def test_unknown_fix_version_is_refused_or_skipped(self):
        self.files.files["t/v.md"] = TICKET.replace("Release 1.0", "NOPE")
        with self.assertRaises(ValidationError):
            self.app.import_markdown.execute("t/v.md")
        self.app.import_markdown.execute("t/v.md", skip_invalid_fix_version=True)
        self.assertNotIn("fixVersions", self.fields())
        self.assertTrue(any("NOPE" in warning for warning in self.diagnostics.warnings))

    def test_required_front_matter(self):
        for name, text in (("a", "---\nsummary: x\n---\nb"), ("b", "---\nissuetype: Story\n---\nb"), ("c", "just text")):
            self.files.files[f"t/{name}.md"] = text
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.app.import_markdown.execute(f"t/{name}.md")

    def test_missing_file(self):
        with self.assertRaises(ValidationError):
            self.app.import_markdown.execute("t/none.md")


def story(number, parent=""):
    meta = f"source_id: S-{number}\nsource_key: SRC-{number}\nsummary: Story {number}\nissuetype: Story\n"
    return f"---\n{meta}{('parent_id: ' + parent + chr(10)) if parent else ''}---\nBody {number}\n"


class ImportDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        epic = "---\nsource_id: E-1\nsource_key: SRC-E1\nsummary: Epic\nissuetype: Epic\n---\nEpic body\n"
        self.files = MemoryFiles({"d/001-epic.md": epic, "d/002-story.md": story(2, "E-1"), "d/003-story.md": story(3), "d/notes.txt": "ignored"})
        self.app = build_app(gateway=self.gateway, files=self.files)

    def test_imports_in_order_and_writes_the_map(self):
        batch = self.app.import_markdown_directory.execute("d")
        self.assertEqual([(o.action, o.key) for o in batch.outcomes], [("created", "PROJ-101"), ("created", "PROJ-102"), ("created", "PROJ-103")])
        self.assertEqual(batch.map_file, "d/jira-import-map.csv")
        rows = self.files.files["d/jira-import-map.csv"].splitlines()
        self.assertEqual(rows[0], "source_id,source_key,jira_key,issue_type,status,file")
        self.assertEqual(rows[2], "S-2,SRC-2,PROJ-102,Story,Created,002-story.md")

    def test_a_story_is_linked_to_its_epic(self):
        self.app.import_markdown_directory.execute("d")
        story_fields = self.gateway.called("create_issue")[1][1]["fields"]
        self.assertEqual(story_fields["customfield_10002"], "PROJ-101")
        self.assertNotIn("customfield_10002", self.gateway.called("create_issue")[2][1]["fields"])

    def test_the_epic_link_field_can_be_set_explicitly(self):
        app = build_app(gateway=self.gateway, files=self.files, settings=settings(epic_link_field="customfield_777"))
        app.import_markdown_directory.execute("d")
        self.assertEqual(self.gateway.called("create_issue")[1][1]["fields"]["customfield_777"], "PROJ-101")

    def test_a_second_run_skips_what_was_created(self):
        self.app.import_markdown_directory.execute("d")
        before = len(self.gateway.called("create_issue"))
        batch = self.app.import_markdown_directory.execute("d")
        self.assertEqual([o.action for o in batch.outcomes], ["skipped"] * 3)
        self.assertEqual(len(self.gateway.called("create_issue")), before)

    def test_a_resumed_run_creates_only_the_rest(self):
        self.files.files["d/jira-import-map.csv"] = "source_id,source_key,jira_key,issue_type,status,file\nE-1,SRC-E1,PROJ-50,Epic,Created,001-epic.md"  # no final newline
        batch = self.app.import_markdown_directory.execute("d")
        self.assertEqual([o.action for o in batch.outcomes], ["skipped", "created", "created"])
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["customfield_10002"], "PROJ-50")
        self.assertEqual(len(self.files.files["d/jira-import-map.csv"].splitlines()), 4)

    def test_dry_run_writes_no_map_and_creates_nothing(self):
        batch = self.app.import_markdown_directory.execute("d", dry_run=True)
        self.assertEqual([o.action for o in batch.outcomes], ["created"] * 3)
        self.assertNotIn("d/jira-import-map.csv", self.files.files)
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_a_dry_run_shows_the_epic_link_the_real_run_will_send(self):
        batch = self.app.import_markdown_directory.execute("d", dry_run=True)
        self.assertEqual(batch.outcomes[1].payload["fields"]["customfield_10002"], "<key of E-1, assigned when it is created>")
        self.assertNotIn("customfield_10002", batch.outcomes[2].payload["fields"])

    def test_a_dry_run_knows_the_parents_that_are_already_in_the_map(self):
        self.files.files["d/jira-import-map.csv"] = "source_id,source_key,jira_key,issue_type,status,file\nE-1,SRC-E1,PROJ-50,Epic,Created,001-epic.md\n"
        batch = self.app.import_markdown_directory.execute("d", dry_run=True)
        self.assertEqual(batch.outcomes[1].payload["fields"]["customfield_10002"], "PROJ-50")

    def test_a_dry_run_catches_a_parent_that_comes_too_late_or_never(self):
        files = MemoryFiles({"d/001-story.md": story(1, "E-9"), "d/002-epic.md": "---\nsource_id: E-9\nsummary: Epic\nissuetype: Epic\n---\nx"})
        batch = build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d", dry_run=True, continue_on_error=True)
        self.assertEqual([o.action for o in batch.outcomes], ["failed", "created"])
        self.assertIn("E-9", batch.outcomes[0].message)

    def test_two_files_with_one_source_id_are_refused_before_anything_is_created(self):
        self.files.files["d/004-again.md"] = story(2)  # source_id S-2 is the one of 002-story.md
        with self.assertRaises(ValidationError) as caught:
            self.app.import_markdown_directory.execute("d")
        self.assertIn("S-2", caught.exception.message)
        self.assertEqual(self.gateway.called("create_issue"), [])
        self.assertNotIn("d/jira-import-map.csv", self.files.files)

    def test_the_map_file_is_made_before_the_first_issue(self):
        files = MemoryFiles({"d/001-story.md": story(1)})
        build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d", map_file="deep/er/map.csv")
        self.assertTrue(files.files["deep/er/map.csv"].startswith("source_id,source_key,jira_key"))

    def test_an_unwritable_map_stops_the_run_before_it_creates_anything(self):
        class NoMap(MemoryFiles):
            def write_text(self, path, text):
                raise ValidationError(f"Cannot write {path}: Access denied")

        files = NoMap({"d/001-story.md": story(1)})
        with self.assertRaises(ValidationError) as caught:
            build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d")
        self.assertIn("nothing was created", caught.exception.hint)
        self.assertEqual(self.gateway.called("create_issue"), [])

    def test_when_the_map_cannot_be_updated_the_created_key_is_reported_and_the_run_stops(self):
        class MapFull(MemoryFiles):
            def append_text(self, path, text):
                raise ValidationError(f"Cannot write {path}: Disk full")

        files = MapFull({"d/001-story.md": story(1), "d/002-story.md": story(2)})
        batch = build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d", continue_on_error=True)
        self.assertEqual([o.action for o in batch.outcomes], ["created", "failed"])  # stopped although continue_on_error
        self.assertEqual(len(self.gateway.called("create_issue")), 1)
        self.assertIn("PROJ-101", batch.outcomes[1].message)
        self.assertIn("by hand", batch.outcomes[1].message)
        self.assertIsNotNone(batch.first_error)

    def test_a_failure_stops_the_run_unless_told_to_continue(self):
        self.files.files["d/001-epic.md"] = "---\nsummary: no type\n---\nx"
        stopped = self.app.import_markdown_directory.execute("d")
        self.assertEqual([o.action for o in stopped.outcomes], ["failed"])
        self.assertIsInstance(stopped.first_error, ValidationError)
        carried_on = self.app.import_markdown_directory.execute("d", continue_on_error=True)
        self.assertEqual([o.action for o in carried_on.outcomes], ["failed", "failed", "created"])  # the story's parent was never created

    def test_a_missing_parent_is_a_failure(self):
        files = MemoryFiles({"d/001-story.md": story(1, "E-9")})
        batch = build_app(gateway=self.gateway, files=files).import_markdown_directory.execute("d")
        self.assertEqual(batch.outcomes[0].action, "failed")
        self.assertIn("E-9", batch.outcomes[0].message)

    def test_missing_directory(self):
        with self.assertRaises(ValidationError):
            self.app.import_markdown_directory.execute("nowhere")


CSV = (
    "project,issuetype,summary,labels,fix_versions,issue_key,fields_json\n"
    'PROJ,Story,"One, with comma","a,b",Release 1.0,,"{""customfield_10001"": 5}"\n'
    ",Story,Two,,,,\n"
    ",,No type,,,,\n"
    ",,Renamed,,,PROJ-1,\n"
)


class ImportCsvTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles({"rows.csv": CSV})
        self.app = build_app(gateway=self.gateway, files=self.files)

    def test_rows_create_and_update(self):
        batch = self.app.import_csv.execute("rows.csv", continue_on_error=True)
        self.assertEqual([o.action for o in batch.outcomes], ["created", "created", "failed", "updated"])
        first = self.gateway.called("create_issue")[0][1]["fields"]
        self.assertEqual((first["summary"], first["labels"], first["customfield_10001"]), ("One, with comma", ["a", "b"], 5))
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"summary": "Renamed"}})

    def test_failures_name_the_line_and_stop_unless_told_otherwise(self):
        batch = self.app.import_csv.execute("rows.csv")
        self.assertEqual([o.action for o in batch.outcomes], ["created", "created", "failed"])
        self.assertIn("line 4", batch.outcomes[2].message)
        self.assertIsNotNone(batch.first_error)

    def test_dry_run(self):
        batch = self.app.import_csv.execute("rows.csv", dry_run=True, continue_on_error=True)
        self.assertTrue(all(o.dry_run for o in batch.outcomes if o.action != "failed"))
        self.assertEqual(self.gateway.called("create_issue") + self.gateway.called("update_issue"), [])

    def test_byte_order_mark_is_ignored(self):
        self.files.files["bom.csv"] = "\ufeffsummary,issuetype\nA,Story\n"
        self.assertEqual(self.app.import_csv.execute("bom.csv").outcomes[0].action, "created")

    def test_a_row_resolves_its_assignee_and_a_dry_run_checks_its_priority(self):
        self.files.files["who.csv"] = "summary,issuetype,assignee,priority\nOne,Story,Alex Smith,High\nTwo,Story,Sam Sample,High\nThree,Story,,Nope\n"
        live = self.app.import_csv.execute("who.csv", continue_on_error=True)
        self.assertEqual([o.action for o in live.outcomes], ["created", "failed", "created"])  # the priority is left to Jira in a live run
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["assignee"], {"name": "asmith"})
        self.assertIn("matches 2 users", live.outcomes[1].message)
        dry = self.app.import_csv.execute("who.csv", dry_run=True, continue_on_error=True)
        self.assertEqual([o.action for o in dry.outcomes], ["created", "failed", "failed"])
        self.assertIn("priority 'Nope'", dry.outcomes[2].message)

    def test_a_row_that_updates_from_a_placeholder_is_refused_and_one_that_creates_is_not(self):
        self.files.files["marks.csv"] = "issue_key,summary,issuetype,description\nPROJ-1,Renamed,Story,Ask [~user]\n,New issue,Story,Ask [~user]\n"
        batch = self.app.import_csv.execute("marks.csv", continue_on_error=True)
        self.assertEqual([outcome.action for outcome in batch.outcomes], ["failed", "created"])
        self.assertIn("placeholder", batch.outcomes[0].message)
        self.assertEqual(self.gateway.called("update_issue"), [])
        self.files.files["names.csv"] = "issue_key,summary,labels\nPROJ-1,Renamed,[email]\n"
        self.assertEqual(self.app.import_csv.execute("names.csv").outcomes[0].action, "failed")
        for column, value in (("components", "Team [~user]"), ("fix_versions", "[email]")):
            with self.subTest(column=column):
                self.files.files["names.csv"] = f"issue_key,summary,{column}\nPROJ-1,Renamed,{value}\n"
                self.assertEqual(self.app.import_csv.execute("names.csv").outcomes[0].action, "failed")
        self.assertEqual(self.gateway.called("update_issue"), [])


if __name__ == "__main__":
    unittest.main()
