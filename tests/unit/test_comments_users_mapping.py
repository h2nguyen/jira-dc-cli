"""Jira's JSON for comments, users and field metadata as domain objects, including answers that are odd or hostile."""
import unittest

from jira_dc_cli.adapters.driven.map_comments import comments_page
from jira_dc_cli.adapters.driven.map_users import person_from
from jira_dc_cli.adapters.driven.map_validation import rule_from, rules_from_create, rules_from_fields, screens_from
from jira_dc_cli.domain.users import Person
from jira_dc_cli.domain.validation import Allowed


class CommentsPageTest(unittest.TestCase):
    def test_the_comments_and_the_total_of_a_page(self):
        page = {"startAt": 0, "maxResults": 2, "total": 5, "comments": [{"id": "1", "created": "c", "body": "a"}, {"id": "2", "created": "d", "body": "b", "updated": "e", "visibility": {"type": "group", "value": "staff"}}]}
        comments, total = comments_page(page)
        self.assertEqual((total, [item.id for item in comments]), (5, ["1", "2"]))
        self.assertEqual((comments[1].updated, comments[1].visibility), ("e", "group:staff"))

    def test_without_a_total_the_page_is_all_there_is(self):
        comments, total = comments_page({"comments": [{"id": "1"}]})
        self.assertEqual((len(comments), total), (1, 1))

    def test_a_total_that_is_no_number_is_ignored(self):
        for total in ("5", True, -1, None, 2.5):
            with self.subTest(total=total):
                self.assertEqual(comments_page({"comments": [{"id": "1"}], "total": total})[1], 1)

    def test_what_is_not_a_comment_is_skipped_and_garbage_gives_nothing(self):
        self.assertEqual([item.id for item in comments_page({"comments": [None, "x", 3, {"id": "9"}]})[0]], ["9"])
        for data in (None, [], "text", 5, {}, {"comments": None}, {"comments": "x"}):
            with self.subTest(data=data):
                self.assertEqual(comments_page(data)[0], [])

    def test_the_rendered_html_is_kept_as_it_comes(self):
        comments, _ = comments_page({"comments": [{"id": "1", "renderedBody": "<p>x</p>"}]})
        self.assertEqual(comments[0].rendered_body, "<p>x</p>")


class PersonTest(unittest.TestCase):
    def test_a_user(self):
        self.assertEqual(person_from({"name": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com", "active": True}), Person("jdoe", "Jane Doe", "jane.doe@example.com", True))

    def test_a_user_that_was_switched_off(self):
        self.assertEqual(person_from({"name": "old", "active": False}).active, False)

    def test_a_user_without_the_flag_counts_as_active_and_missing_texts_are_empty(self):
        self.assertEqual(person_from({"name": "x"}), Person("x", "", "", True))
        self.assertEqual(person_from({"name": "x", "displayName": None, "emailAddress": None}), Person("x", "", "", True))

    def test_what_names_nobody_is_none(self):
        for data in (None, [], "jdoe", {}, {"name": ""}, {"name": None}, {"displayName": "Jane"}):
            with self.subTest(data=data):
                self.assertIsNone(person_from(data))


class RulesTest(unittest.TestCase):
    def test_a_field_with_values_that_have_a_name(self):
        rule = rule_from("priority", {"name": "Priority", "required": False, "hasDefaultValue": True, "allowedValues": [{"id": "1", "name": "High", "self": "x"}]})
        self.assertEqual((rule.field_id, rule.name, rule.required, rule.has_default), ("priority", "Priority", False, True))
        self.assertEqual(rule.allowed, (Allowed("1", "High"),))

    def test_the_options_of_a_custom_field_have_a_value_and_other_things_a_key_or_just_text(self):
        rule = rule_from("customfield_1", {"allowedValues": [{"id": 10, "value": "Red"}, {"key": "k"}, "plain", {"id": "5"}]})
        self.assertEqual([item.text for item in rule.allowed], ["Red", "k", "plain", "5"])
        self.assertEqual(rule.allowed[0], Allowed("10", "Red"))

    def test_no_list_empty_list_and_a_list_that_is_none_are_different_things(self):
        self.assertIsNone(rule_from("summary", {"name": "Summary"}).allowed)
        self.assertEqual(rule_from("components", {"allowedValues": []}).allowed, ())
        self.assertIsNone(rule_from("x", {"allowedValues": None}).allowed)
        self.assertIsNone(rule_from("x", {"allowedValues": "not a list"}).allowed)

    def test_odd_metadata_does_not_break_it(self):
        for data in (None, [], "x", 5):
            with self.subTest(data=data):
                rule = rule_from("f", data)
                self.assertEqual((rule.field_id, rule.name, rule.required, rule.allowed), ("f", "", False, None))

    def test_the_fields_of_an_edit_or_a_screen_by_field_id(self):
        rules = rules_from_fields({"priority": {"name": "Priority", "allowedValues": [{"id": "1", "name": "High"}]}, "summary": {"required": True}})
        self.assertEqual(sorted(rules), ["priority", "summary"])
        self.assertTrue(rules["summary"].required)
        self.assertEqual(rules["priority"].allowed_names, ["High"])
        for data in (None, [], "x", {}):
            self.assertEqual(rules_from_fields(data), {})

    def test_the_fields_of_the_create_metadata_have_their_field_id_inside(self):
        rules = rules_from_create({"values": [{"fieldId": "summary", "required": True}, {"fieldId": "priority", "allowedValues": [{"name": "High"}]}, {"required": True}, "x", None]})
        self.assertEqual(sorted(rules), ["priority", "summary"])
        self.assertTrue(rules["summary"].required)
        self.assertEqual(rules_from_create([{"fieldId": "labels"}]).keys(), {"labels"})  # older Jira: a list
        for data in (None, "x", {}, {"values": "x"}, {"values": None}):
            self.assertEqual(rules_from_create(data), {})

    def test_the_transitions_with_the_fields_of_their_screens(self):
        data = {
            "transitions": [
                {"id": "81", "name": "Finish", "to": {"name": "Done", "statusCategory": {"key": "done"}}, "fields": {"resolution": {"required": True, "allowedValues": [{"name": "Fixed"}]}}},
                {"id": "111", "name": "Reopen", "to": {"name": "To Do"}},
                "junk",
            ]
        }
        screens = screens_from(data)
        self.assertEqual([(item.transition.id, item.transition.name, item.transition.to_status.name, item.transition.to_status.category) for item in screens], [("81", "Finish", "Done", "done"), ("111", "Reopen", "To Do", "new")])
        self.assertEqual([rule.field_id for rule in screens[0].fields], ["resolution"])
        self.assertEqual(screens[1].fields, ())
        for garbage in (None, [], "x", {}, {"transitions": None}):
            self.assertEqual(screens_from(garbage), [])


if __name__ == "__main__":
    unittest.main()
