"""Checking the values of a payload against what Jira's metadata allows: what is refused, what is only a warning, and the message."""
import unittest

from jira_dc_cli.domain.errors import RejectedError
from jira_dc_cli.domain.model import Issue
from jira_dc_cli.domain.validation import (
    COMPARED,
    LIST_LIMIT,
    Allowed,
    FieldRule,
    check_values,
    create_target,
    current_values,
    custom_texts,
    listing,
    missing_required,
    name_changes,
    new_stand_ins,
    refusal,
    required_warning,
    stand_in_note,
    stand_in_refusal,
    stated,
    text_changes,
)

PRIORITY = FieldRule("priority", "Priority", allowed=(Allowed("1", "Highest"), Allowed("2", "High"), Allowed("3", "Medium")))
COMPONENTS = FieldRule("components", "Component/s", allowed=(Allowed("10", "Demo"), Allowed("11", "Backend")))
VERSIONS = FieldRule("fixVersions", "Fix Version/s", allowed=(Allowed("20", "Release 1.0"),))
RULES = {rule.field_id: rule for rule in (PRIORITY, COMPONENTS, VERSIONS)}


def fields(**values):
    return {"fields": values}


class StatedTest(unittest.TestCase):
    def names(self, payload, field_id):
        references, cleared = stated(payload, field_id)
        return [(item.id, item.name) for item in references], cleared

    def test_a_value_by_name_by_id_and_by_both(self):
        self.assertEqual(self.names(fields(priority={"name": "High"}), "priority"), ([("", "High")], False))
        self.assertEqual(self.names(fields(priority={"id": "2"}), "priority"), ([("2", "")], False))
        self.assertEqual(self.names(fields(priority={"id": 2, "name": "High"}), "priority"), ([("2", "High")], False))

    def test_a_list_of_values_and_a_bare_text(self):
        self.assertEqual(self.names(fields(components=[{"name": "Demo"}, {"id": "11"}]), "components"), ([("", "Demo"), ("11", "")], False))
        self.assertEqual(self.names(fields(priority="High"), "priority"), ([("", "High")], False))

    def test_the_add_and_the_set_of_an_update_but_not_the_remove(self):
        payload = {"update": {"components": [{"add": {"name": "Demo"}}, {"remove": {"name": "Old"}}, {"set": [{"name": "Backend"}]}]}}
        self.assertEqual(self.names(payload, "components"), ([("", "Demo"), ("", "Backend")], False))

    def test_an_empty_value_clears_the_field(self):
        for value in (None, "", [], {}):
            with self.subTest(value=value):
                self.assertEqual(self.names(fields(priority=value), "priority"), ([], True))
        self.assertEqual(self.names({"update": {"components": [{"set": []}]}}, "components"), ([], True))

    def test_a_field_that_the_payload_does_not_name(self):
        self.assertEqual(self.names(fields(summary="S"), "priority"), ([], False))
        self.assertEqual(self.names({}, "priority"), ([], False))

    def test_shapes_that_say_nothing_are_ignored(self):
        for value in (True, 3.5, {"self": "x"}, [{"x": 1}, None, True]):
            with self.subTest(value=value):
                self.assertEqual(self.names(fields(priority=value), "priority")[0], [])

    def test_a_value_that_names_nothing_is_kept_as_an_empty_reference_and_is_no_clearing(self):
        for value in ({"name": ""}, {"id": ""}, {"id": None}, {"value": ""}, [""], [{"name": ""}], [{"name": "High"}, {"name": ""}]):
            with self.subTest(value=value):
                references, cleared = self.names(fields(priority=value), "priority")
                self.assertIn(("", ""), references)
                self.assertFalse(cleared)
        self.assertEqual(self.names({"update": {"components": [{"add": {"name": ""}}]}}, "components"), ([("", "")], False))

    def test_a_payload_that_is_not_an_object_is_ignored(self):
        self.assertEqual(self.names({"fields": "x", "update": ["y"]}, "priority"), ([], False))
        self.assertEqual(self.names({"update": {"priority": "x"}}, "priority"), ([], False))


class CheckValuesTest(unittest.TestCase):
    def test_a_valid_payload_has_no_problem(self):
        payload = fields(priority={"name": "High"}, components=[{"name": "Demo"}], fixVersions=[{"id": "20"}], summary="x", customfield_1=5)
        self.assertEqual(check_values(payload, RULES), [])

    def test_a_name_is_compared_without_regard_to_case_and_an_id_exactly(self):
        self.assertEqual(check_values(fields(priority={"name": "hIGH"}), RULES), [])
        self.assertEqual(check_values(fields(priority={"id": "2"}), RULES), [])
        self.assertEqual([item.value for item in check_values(fields(priority={"id": "9"}), RULES)], ["9"])

    def test_a_value_that_the_project_does_not_offer(self):
        (problem,) = check_values(fields(priority={"name": "Nope"}), RULES)
        self.assertEqual((problem.field_id, problem.kind, problem.value), ("priority", "value", "Nope"))
        self.assertEqual(problem.allowed, ("Highest", "High", "Medium"))

    def test_every_wrong_value_of_a_list_is_a_problem(self):
        problems = check_values(fields(components=[{"name": "Demo"}, {"name": "X"}, {"name": "Y"}]), RULES)
        self.assertEqual([item.value for item in problems], ["X", "Y"])

    def test_the_update_syntax_is_checked_too(self):
        payload = {"update": {"fixVersions": [{"add": {"name": "Release 9"}}]}}
        self.assertEqual([item.value for item in check_values(payload, RULES)], ["Release 9"])

    def test_a_field_without_a_list_of_values_is_not_judged(self):
        free = {"priority": FieldRule("priority", "Priority")}
        self.assertEqual(check_values(fields(priority={"name": "Whatever"}), free), [])
        self.assertEqual(check_values(fields(priority={"name": ""}), free), [])

    def test_an_empty_name_or_id_is_a_value_that_the_project_does_not_offer(self):
        for value in ({"name": ""}, {"id": ""}, [""], {"id": None}):
            with self.subTest(value=value):
                (problem,) = check_values(fields(priority=value), RULES)
                self.assertEqual((problem.field_id, problem.kind, problem.value), ("priority", "value", ""))
                self.assertEqual(problem.allowed, ("Highest", "High", "Medium"))
        (problem,) = check_values(fields(components=[{"name": "Demo"}, {"name": ""}]), RULES)
        self.assertEqual((problem.field_id, problem.value), ("components", ""))
        self.assertEqual(check_values(fields(priority={"name": " "}), RULES)[0].value, " ")  # a blank one was refused before

    def test_the_refusal_of_an_empty_value_says_that_it_is_empty_and_lists_what_is_allowed(self):
        error = refusal(check_values(fields(priority={"name": ""}), RULES), "for PROJ-1")
        self.assertEqual(error.message, "priority is empty for PROJ-1: it needs a name or an id.")
        self.assertEqual(error.hint, "Allowed priorities: Highest, High, Medium.")
        both = refusal(check_values(fields(priority={"name": ""}, components=[{"name": "X"}]), RULES), "for PROJ-1")
        self.assertEqual(both.message, "2 values would be refused for PROJ-1: empty priority; component 'X'.")

    def test_an_empty_list_of_values_allows_nothing(self):
        none = {"components": FieldRule("components", "Component/s", allowed=())}
        (problem,) = check_values(fields(components=[{"name": "Demo"}]), none)
        self.assertEqual(problem.allowed, ())

    def test_clearing_a_required_field(self):
        required = {"priority": FieldRule("priority", "Priority", required=True, allowed=PRIORITY.allowed)}
        (problem,) = check_values(fields(priority=None), required)
        self.assertEqual(problem.kind, "required")
        self.assertEqual(check_values(fields(components=[]), RULES), [])  # not required: clearing is fine

    def test_a_field_that_the_metadata_does_not_have_is_a_problem_of_the_screen(self):
        (problem,) = check_values(fields(resolution={"name": "Fixed"}), RULES)
        self.assertEqual((problem.field_id, problem.kind), ("resolution", "screen"))

    def test_custom_fields_and_other_standard_fields_are_never_judged(self):
        payload = fields(customfield_10001={"value": "x"}, labels=["a"], assignee={"name": "x"}, summary="")
        self.assertEqual(check_values(payload, {}), [])


class MissingRequiredTest(unittest.TestCase):
    def test_what_is_required_has_no_default_and_is_not_in_the_payload(self):
        rules = {
            "customfield_5": FieldRule("customfield_5", "Epic Name", required=True),
            "priority": FieldRule("priority", "Priority", required=True, has_default=True),
            "labels": FieldRule("labels", "Labels"),
            "summary": FieldRule("summary", "Summary", required=True),
        }
        missing = missing_required(fields(summary="S"), rules)
        self.assertEqual([rule.field_id for rule in missing], ["customfield_5"])

    def test_a_field_in_the_update_syntax_is_mentioned(self):
        rules = {"components": FieldRule("components", "Component/s", required=True)}
        self.assertEqual(missing_required({"update": {"components": [{"add": {"name": "Demo"}}]}}, rules), [])

    def test_a_field_that_is_mentioned_but_empty_is_not_reported_here(self):
        rules = {"components": FieldRule("components", "Component/s", required=True)}
        self.assertEqual(missing_required(fields(components=[]), rules), [])

    def test_the_warning_names_the_field_and_a_few_values(self):
        rule = FieldRule("resolution", "Resolution", required=True, allowed=tuple(Allowed(str(n), f"R{n}") for n in range(20)))
        text = required_warning(rule, "for the transition 'Finish'")
        self.assertIn("Resolution (resolution) is required for the transition 'Finish'", text)
        self.assertIn("R0, R1", text)
        self.assertIn("12 more", text)  # at most eight values


class ListingTest(unittest.TestCase):
    def test_few_names_are_all_listed(self):
        self.assertEqual(listing(["a", "b"]), "a, b")

    def test_no_names(self):
        self.assertEqual(listing([]), "none")

    def test_many_names_are_cut_and_the_rest_is_counted(self):
        names = [f"n{number}" for number in range(LIST_LIMIT + 12)]
        text = listing(names)
        self.assertTrue(text.endswith("and 12 more"))
        self.assertEqual(text.count(","), LIST_LIMIT - 1)

    def test_exactly_the_limit_is_not_cut(self):
        self.assertNotIn("more", listing([str(number) for number in range(LIST_LIMIT)]))


class RefusalTest(unittest.TestCase):
    def test_one_wrong_value_names_it_and_lists_what_is_allowed(self):
        error = refusal(check_values(fields(priority={"name": "Nope"}), RULES), "for PROJ-1")
        self.assertIsInstance(error, RejectedError)
        self.assertEqual(error.exit_code, 6)
        self.assertEqual(error.message, "priority 'Nope' is not available for PROJ-1.")
        self.assertEqual(error.hint, "Allowed priorities: Highest, High, Medium.")
        self.assertEqual(error.details["problems"][0], {"field": "priority", "kind": "value", "value": "Nope", "allowed": ["Highest", "High", "Medium"]})

    def test_several_wrong_values_are_one_error_with_one_hint_per_field(self):
        problems = check_values(fields(priority={"name": "Nope"}, components=[{"name": "X"}, {"name": "Y"}]), RULES)
        error = refusal(problems, "in PROJ for Story (10200)")
        self.assertEqual(error.message, "3 values would be refused in PROJ for Story (10200): priority 'Nope'; component 'X'; component 'Y'.")
        self.assertEqual(error.hint, "Allowed priorities: Highest, High, Medium. Allowed components: Demo, Backend.")

    def test_a_long_list_is_cut_in_the_hint_and_in_the_details(self):
        many = FieldRule("fixVersions", "Fix Version/s", allowed=tuple(Allowed(str(n), f"V{n}") for n in range(LIST_LIMIT + 5)))
        error = refusal(check_values(fields(fixVersions=[{"name": "Nope"}]), {"fixVersions": many}), "for PROJ-1")
        self.assertIn("and 5 more", error.hint)
        self.assertEqual(len(error.details["problems"][0]["allowed"]), LIST_LIMIT)

    def test_a_field_that_cannot_be_cleared(self):
        required = {"priority": FieldRule("priority", "Priority", required=True)}
        error = refusal(check_values(fields(priority=None), required), "for PROJ-1")
        self.assertEqual(error.message, "priority is required for PROJ-1 and cannot be cleared.")
        self.assertIn("a value instead of clearing", error.hint)

    def test_a_field_that_is_not_on_the_screen_gets_the_hint_of_the_caller(self):
        error = refusal(check_values(fields(resolution={"name": "Fixed"}), {}), "for the transition 'Reopen' (111) of PROJ-2", screen_hint="The screen has: none.")
        self.assertEqual(error.message, "resolution cannot be set for the transition 'Reopen' (111) of PROJ-2: it is not on the screen.")
        self.assertEqual(error.hint, "The screen has: none.")

    def test_the_hint_is_never_empty_for_a_wrong_value(self):
        for payload in (fields(priority={"name": "x"}), fields(components=[{"name": "x"}]), fields(fixVersions=[{"name": "x"}])):
            with self.subTest(payload=payload):
                self.assertTrue(refusal(check_values(payload, RULES), "for PROJ-1").hint)


class CreateTargetTest(unittest.TestCase):
    def test_the_project_key_and_the_type_id(self):
        self.assertEqual(create_target({"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}}}), ("PROJ", "10200"))
        self.assertEqual(create_target({"fields": {"project": {"key": " PROJ "}, "issuetype": {"id": 10200}}}), ("PROJ", "10200"))

    def test_another_way_of_naming_them_is_not_a_target(self):
        for payload in (
            {"fields": {"project": {"id": "10000"}, "issuetype": {"id": "10200"}}},
            {"fields": {"project": {"key": "PROJ"}, "issuetype": {"name": "Story"}}},
            {"fields": {"project": "PROJ", "issuetype": "10200"}},
            {"fields": {"project": {"key": ""}, "issuetype": {"id": "1"}}},
            {"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": {"x": 1}}}},
            {"fields": {}},
            {"fields": []},
            {},
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(create_target(payload))


class TextsTest(unittest.TestCase):
    def test_the_texts_of_a_payload_in_the_fields_that_can_hold_people(self):
        payload = {"fields": {"summary": "S", "description": "D", "priority": {"name": "High"}, "customfield_10004": "A", "labels": ["x"]}}
        self.assertEqual(text_changes(payload), [("summary", "S"), ("description", "D")])
        self.assertEqual(text_changes(payload, "customfield_10004"), [("summary", "S"), ("description", "D"), ("acceptance", "A")])

    def test_the_set_of_the_update_syntax_counts_and_the_other_operations_do_not(self):
        payload = {"update": {"description": [{"set": "New"}], "summary": [{"add": "x"}, {"remove": "y"}, {"set": "S2"}], "labels": [{"add": "a"}]}}
        self.assertEqual(text_changes(payload), [("description", "New"), ("summary", "S2")])

    def test_a_value_that_is_no_text_and_a_payload_that_is_no_object_say_nothing(self):
        self.assertEqual(text_changes({"fields": {"summary": None, "description": 5, "customfield_1": ["a"]}}, "customfield_1"), [])
        self.assertEqual(text_changes({"fields": "x", "update": ["y"]}), [])
        self.assertEqual(text_changes({"update": {"description": "set it"}}), [])
        self.assertEqual(text_changes({}), [])

    def test_the_texts_of_the_custom_fields(self):
        payload = {"fields": {"summary": "S", "customfield_1": "one", "customfield_2": 3}, "update": {"customfield_3": [{"set": "three"}], "description": [{"set": "D"}]}}
        self.assertEqual(sorted(custom_texts(payload)), ["one", "three"])
        self.assertEqual(custom_texts({}), [])

    def test_a_stand_in_that_the_current_text_does_not_hold_is_new(self):
        changes = [("description", "Ask [~user] or [email]"), ("summary", "Plain")]
        self.assertEqual(new_stand_ins(changes, {"description": "Ask [~jdoe] or jane.doe@example.com", "summary": "Plain"}), {"description": ["[~user]", "[email]"]})

    def test_a_stand_in_that_the_current_text_holds_is_the_text_of_somebody_who_wrote_it_and_may_stay(self):
        changes = [("description", "Mail [email] now")]
        self.assertEqual(new_stand_ins(changes, {"description": "Write to [email] for details"}), {})
        self.assertEqual(new_stand_ins(changes, {"description": "Write to jane.doe@example.com"}), {"description": ["[email]"]})
        self.assertEqual(new_stand_ins(changes, {}), {"description": ["[email]"]})
        self.assertEqual(new_stand_ins(changes, {"description": None}), {"description": ["[email]"]})

    def test_each_text_is_compared_with_the_current_text_of_its_own_field(self):
        changes = [("summary", "About [~user]"), ("description", "About [~user]")]
        self.assertEqual(new_stand_ins(changes, {"summary": "About [~user]", "description": "About [~jdoe]"}), {"description": ["[~user]"]})

    def test_the_same_stand_in_twice_is_named_once(self):
        self.assertEqual(new_stand_ins([("description", "[~user] and [~user]"), ("description", "[~user]")], {}), {"description": ["[~user]"]})

    def test_the_refusal_has_the_words_of_the_refusal_of_a_comment_and_the_marks_in_a_fixed_order(self):
        error = stand_in_refusal({"description": ["[email]", "[~user]"]}, "PROJ-2")
        self.assertEqual(error.exit_code, 1)
        self.assertEqual(
            error.message,
            "The new description holds [~user], [email], which stands in place of a mention or an address that the CLI hides: sending it would overwrite the real one.",
        )
        self.assertIn("[~username], see `jira users resolve`", error.hint)
        self.assertIn("edited in Jira itself", error.hint)
        self.assertEqual(error.details, {"fields": ["description"], "placeholders": ["[~user]", "[email]"]})

    def test_the_hint_names_the_way_out_with_the_key_of_the_issue(self):
        error = stand_in_refusal({"summary": ["[email]"]}, "PROJ-2")
        self.assertEqual(
            error.hint,
            "The CLI shows [~user], [email] and [personal link] in place of real mentions, addresses and links. Write the real mention "
            "([~username], see `jira users resolve`) or leave that part out; a text that holds people is best edited in Jira itself. If the "
            "stand-in is really what you want to write, send it with `jira api PUT issue/PROJ-2 --data-file FILE --write` (the raw call has no guard).",
        )
        self.assertIn("`jira api PUT issue/KEY --data-file FILE --write`", stand_in_refusal({"summary": ["[email]"]}).hint)  # no key given

    def test_the_hint_of_a_transition_names_its_own_raw_call_a_post_to_the_transitions_with_the_id_in_the_file(self):
        error = stand_in_refusal({"labels": ["[email]"]}, "PROJ-2", transition=True)
        self.assertEqual(
            error.hint,
            "The CLI shows [~user], [email] and [personal link] in place of real mentions, addresses and links. Write the real mention "
            "([~username], see `jira users resolve`) or leave that part out; a text that holds people is best edited in Jira itself. If the "
            "stand-in is really what you want to write, send it with `jira api POST issue/PROJ-2/transitions --data-file FILE --write`, a file with "
            '{"transition": {"id": "ID"}, "fields": {...}} (`jira transition PROJ-2` lists the ids; the raw call has no guard).',
        )
        self.assertIn("issue/KEY/transitions", stand_in_refusal({"labels": ["[email]"]}, transition=True).hint)
        self.assertEqual(error.message, stand_in_refusal({"labels": ["[email]"]}, "PROJ-2").message)  # the words are those of an update
        self.assertEqual(error.details, {"fields": ["labels"], "placeholders": ["[email]"]})

    def test_several_texts_are_named_in_one_sentence_and_take_a_plural_verb(self):
        self.assertIn("The new summary and description hold [~user]", stand_in_refusal({"summary": ["[~user]"], "description": ["[~user]"]}).message)
        three = stand_in_refusal({"summary": ["[~user]"], "description": ["[email]"], "acceptance": ["[personal link]"]})
        self.assertIn("The new summary, description and acceptance criteria hold [~user], [personal link], [email]", three.message)

    def test_a_list_of_names_takes_a_plural_verb_and_is_named_by_its_field(self):
        for field, said in (("labels", "labels"), ("components", "components"), ("fixVersions", "fix versions")):
            with self.subTest(field=field):
                error = stand_in_refusal({field: ["[email]"]}, "PROJ-1")
                self.assertEqual(error.message, f"The new {said} hold [email], which stands in place of a mention or an address that the CLI hides: sending it would overwrite the real one.")
                self.assertEqual(error.details, {"fields": [field], "placeholders": ["[email]"]})
        mixed = stand_in_refusal({"summary": ["[~user]"], "labels": ["[email]"], "fixVersions": ["[email]"]}, "PROJ-1")
        self.assertIn("The new summary, labels and fix versions hold [~user], [email]", mixed.message)

    def test_the_note_for_a_new_issue_is_no_refusal(self):
        note = stand_in_note({"description": ["[~user]"]})
        self.assertEqual(
            note,
            "The description of the new issue holds [~user], which stands in place of a mention or an address that the CLI hides: Jira would keep it as plain text, not as the real person or address.",
        )
        self.assertIn("The labels of the new issue hold [email]", stand_in_note({"labels": ["[email]"]}))
        self.assertIn("The summary and labels of the new issue hold [~user], [email]", stand_in_note({"summary": ["[~user]"], "labels": ["[email]"]}))


class CurrentValuesTest(unittest.TestCase):
    def test_what_the_issue_in_jira_holds_by_the_names_that_are_compared(self):
        issue = Issue("PROJ-1", summary="S", issue_type="Story", description="D", acceptance="A", labels=["x", "[email]"], components=["Demo"], fix_versions=["R1"])
        self.assertEqual(
            current_values(issue),
            {"summary": "S", "description": "D", "acceptance": "A", "labels": ["x", "[email]"], "components": ["Demo"], "fixVersions": ["R1"]},
        )
        self.assertEqual(sorted(current_values(issue)), sorted(COMPARED))

    def test_a_field_that_was_not_asked_for_is_the_default_of_the_issue(self):
        values = current_values(Issue("PROJ-1", summary="S"))
        self.assertEqual((values["description"], values["labels"], values["fixVersions"]), ("", [], []))


class NamesTest(unittest.TestCase):
    """The labels, the components and the fix versions: `jira issue` redacts their names like a text, and an item is compared one by one."""

    def test_the_items_of_a_list_in_fields_by_the_field_that_holds_them(self):
        payload = {"fields": {"labels": ["[email]", "backend"], "components": [{"name": "Demo"}, {"name": "[~user]"}], "fixVersions": [{"name": "R1"}], "summary": "S"}}
        self.assertEqual(
            name_changes(payload),
            [("labels", "[email]"), ("labels", "backend"), ("components", "Demo"), ("components", "[~user]"), ("fixVersions", "R1")],
        )

    def test_the_add_and_the_set_of_the_update_syntax_count_and_the_remove_does_not(self):
        payload = {
            "update": {
                "labels": [{"add": "[email]"}, {"remove": "[personal link]"}, {"set": ["a", "b"]}],
                "components": [{"add": {"name": "Demo"}}, {"set": [{"name": "Backend"}]}, {"remove": {"name": "Old"}}],
                "fixVersions": [{"add": {"name": "R2"}}],
            }
        }
        self.assertEqual(
            name_changes(payload),
            [("labels", "[email]"), ("labels", "a"), ("labels", "b"), ("components", "Demo"), ("components", "Backend"), ("fixVersions", "R2")],
        )

    def test_a_bare_name_counts_too_and_what_has_no_name_does_not(self):
        payload = {"fields": {"components": "Demo", "fixVersions": [{"id": "10"}, {"name": 5}, None, ["x"], {"name": "R1"}], "labels": "single"}}
        self.assertEqual(name_changes(payload), [("components", "Demo"), ("fixVersions", "R1"), ("labels", "single")])

    def test_other_fields_and_a_payload_that_is_no_object_say_nothing(self):
        self.assertEqual(name_changes({"fields": {"summary": "S", "customfield_1": ["a"], "versions": [{"name": "V"}]}, "update": {"customfield_2": [{"add": "x"}]}}), [])
        self.assertEqual(name_changes({"fields": "x", "update": ["y"]}), [])
        self.assertEqual(name_changes({"update": {"labels": "add it"}}), [])
        self.assertEqual(name_changes({}), [])

    def test_the_texts_of_a_payload_are_not_names_and_the_other_way_round(self):
        payload = {"fields": {"summary": "S", "labels": ["x"]}}
        self.assertEqual(text_changes(payload), [("summary", "S")])
        self.assertEqual(name_changes(payload), [("labels", "x")])

    def test_an_item_that_the_current_list_holds_verbatim_may_stay(self):
        changes = [("labels", "[email]"), ("labels", "backend")]
        self.assertEqual(new_stand_ins(changes, {"labels": ["[email]", "backend"]}), {})
        self.assertEqual(new_stand_ins(changes, {"labels": ["jane.doe@example.com", "backend"]}), {"labels": ["[email]"]})
        self.assertEqual(new_stand_ins(changes, {"labels": []}), {"labels": ["[email]"]})
        self.assertEqual(new_stand_ins(changes, {"labels": None}), {"labels": ["[email]"]})
        self.assertEqual(new_stand_ins(changes, {}), {"labels": ["[email]"]})

    def test_an_item_is_compared_whole_not_as_a_part_of_another(self):
        self.assertEqual(new_stand_ins([("components", "Mail [email]")], {"components": ["[email]"]}), {"components": ["[email]"]})
        self.assertEqual(new_stand_ins([("components", "[email]")], {"components": ["Mail [email]"]}), {"components": ["[email]"]})

    def test_each_list_is_compared_with_its_own_current_items(self):
        changes = [("labels", "[~user]"), ("components", "[~user]"), ("fixVersions", "[~user]")]
        current = {"labels": ["[~user]"], "components": ["Demo"], "fixVersions": ["[~user]", "R1"]}
        self.assertEqual(new_stand_ins(changes, current), {"components": ["[~user]"]})

    def test_a_text_and_a_list_with_the_same_stand_in_are_judged_each_in_its_way(self):
        changes = [("summary", "Mail [email]"), ("labels", "[email]")]
        self.assertEqual(new_stand_ins(changes, {"summary": "Write to [email]", "labels": ["backend"]}), {"labels": ["[email]"]})
        self.assertEqual(new_stand_ins(changes, {"summary": "Plain", "labels": ["[email]"]}), {"summary": ["[email]"]})


if __name__ == "__main__":
    unittest.main()
