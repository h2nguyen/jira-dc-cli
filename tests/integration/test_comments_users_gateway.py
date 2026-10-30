"""The comments, users and validation calls of `RestJiraGateway` against the mock Jira: paths, parameters, paging and mapping."""
import unittest

from jira_dc_cli.domain.errors import AuthError, NotFoundError, RejectedError, UnavailableError
from jira_dc_cli.domain.users import Person
from tests.integration.test_http_and_gateway import GatewayTestCase
from tests.support.mock_comments import routes as comment_routes
from tests.support.mock_users import routes as user_routes
from tests.support.mock_validation import field_meta, resolution_field, routes as validation_routes


class CommentsGatewayTest(GatewayTestCase):
    def setUp(self):
        super().setUp()
        self.mock = comment_routes(self.jira)
        self.mock.seed("PROJ-1", "20", "* one\n* two", author="asmith", created="2026-10-03T10:00:00.000+0200")
        self.mock.seed("PROJ-1", "21", "Internal", updated="2026-10-05T10:00:00.000+0200", visibility={"type": "role", "value": "Developers"})
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))

    def test_one_comment_is_mapped_with_its_times_author_and_restriction(self):
        comment = self.gateway.get_comment("PROJ-1", "21")
        self.assertEqual((comment.id, comment.body, comment.visibility, comment.updated), ("21", "Internal", "role:Developers", "2026-10-05T10:00:00.000+0200"))
        self.assertEqual((comment.author.name, comment.rendered_body), ("jdoe", ""))
        self.assertEqual(self.jira.requests[-1].query, {})

    def test_rendered_adds_the_expand_parameter_and_the_html_arrives(self):
        comment = self.gateway.get_comment("PROJ-1", "20", rendered=True)
        self.assertEqual(self.jira.requests[-1].query, {"expand": ["renderedBody"]})
        self.assertEqual(comment.rendered_body, "<ul>\n<li>one</li>\n<li>two</li>\n</ul>")

    def test_an_unknown_comment_and_an_unknown_issue_are_not_found(self):
        with self.assertRaises(NotFoundError):
            self.gateway.get_comment("PROJ-1", "999")
        with self.assertRaises(NotFoundError):
            self.gateway.get_comment("PROJ-999", "20")

    def test_the_list_follows_the_pages_until_the_total_is_reached(self):
        self.mock.page_cap = 1  # three comments, one per answer
        comments = self.gateway.fetch_comments("PROJ-1")
        self.assertEqual([item.id for item in comments], ["10", "20", "21"])
        listing = [item for item in self.jira.sent("GET", "/rest/api/2/issue/PROJ-1/comment")]
        self.assertEqual([item.query["startAt"] for item in listing], [["0"], ["1"], ["2"]])
        self.assertEqual({item.query["maxResults"][0] for item in listing}, {"100"})

    def test_a_server_that_returns_everything_is_asked_once(self):
        self.assertEqual(len(self.gateway.fetch_comments("PROJ-1")), 3)
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/PROJ-1/comment")), 1)

    def test_an_issue_without_comments(self):
        self.assertEqual(self.gateway.fetch_comments("PROJ-4")[0].id, "10")  # every mock issue has the one seeded comment
        self.jira.issues["PROJ-4"]["fields"]["comment"]["comments"].clear()
        self.assertEqual(self.gateway.fetch_comments("PROJ-4"), [])

    def test_the_list_with_rendered_asks_for_the_html_on_every_page(self):
        self.mock.page_cap = 2
        comments = self.gateway.fetch_comments("PROJ-1", rendered=True)
        self.assertTrue(all(item.rendered_body for item in comments))
        self.assertTrue(all(item.query["expand"] == ["renderedBody"] for item in self.jira.sent("GET", "/rest/api/2/issue/PROJ-1/comment")))

    def test_an_unknown_issue_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.gateway.fetch_comments("PROJ-999")

    def test_an_edit_sends_the_payload_as_it_is_and_maps_the_answer(self):
        saved = self.gateway.update_comment("PROJ-1", "21", {"body": "New", "visibility": {"type": "role", "value": "Developers"}})
        self.assertEqual((saved.body, saved.updated, saved.visibility), ("New", "2026-10-20T09:00:00.000+0200", "role:Developers"))
        self.assertEqual(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1/comment/21")[0].body, {"body": "New", "visibility": {"type": "role", "value": "Developers"}})

    def test_a_restriction_that_is_not_sent_again_is_gone_on_the_server_which_is_why_the_cli_sends_it(self):
        self.gateway.update_comment("PROJ-1", "21", {"body": "New"})
        self.assertEqual(self.gateway.get_comment("PROJ-1", "21").visibility, "")

    def test_the_statuses_of_an_edit(self):
        with self.assertRaises(AuthError) as caught:
            self.gateway.update_comment("PROJ-1", "20", {"body": "x"})  # the comment of somebody else
        self.assertEqual(caught.exception.details["status"], 403)
        with self.assertRaises(RejectedError):
            self.gateway.update_comment("PROJ-1", "21", {"body": "  "})
        with self.assertRaises(NotFoundError):
            self.gateway.update_comment("PROJ-1", "999", {"body": "x"})

    def test_a_write_is_not_retried(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/comment/21", 503, times=5, method="PUT")
        gateway, _, _ = self.make(retry=3)
        with self.assertRaises(UnavailableError):
            gateway.update_comment("PROJ-1", "21", {"body": "x"})
        self.assertEqual(len(self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1/comment/21")), 1)


class UsersGatewayTest(GatewayTestCase):
    def setUp(self):
        super().setUp()
        self.mock = user_routes(self.jira)
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))

    def test_the_search_is_for_every_user_and_maps_name_display_name_address_and_active(self):
        people = self.gateway.find_users("Jane", include_inactive=True)
        self.assertEqual(people, [Person("jdoe", "Jane Doe", "jane.doe@example.com", True), Person("jroe", "Jane Roe", "jane.roe@example.com", True)])
        request = self.jira.requests[-1]
        self.assertEqual((request.path, request.query), ("/rest/api/2/user/search", {"username": ["Jane"], "maxResults": ["100"], "includeInactive": ["true"]}))

    def test_without_include_inactive_the_parameter_is_left_out_and_the_inactive_are_not_found(self):
        self.assertEqual(self.gateway.find_users("olduser"), [])
        self.assertNotIn("includeInactive", self.jira.requests[-1].query)
        self.assertEqual(self.gateway.find_users("olduser", include_inactive=True)[0].active, False)

    def test_with_an_issue_or_a_project_the_search_is_for_those_who_can_be_assigned(self):
        self.gateway.find_users("smith", issue_key="PROJ-1")
        self.assertEqual((self.jira.requests[-1].path, self.jira.requests[-1].query["issueKey"]), ("/rest/api/2/user/assignable/search", ["PROJ-1"]))
        self.gateway.find_users("smith", project_key="PROJ", include_inactive=True)
        self.assertEqual(self.jira.requests[-1].query["project"], ["PROJ"])
        self.assertNotIn("includeInactive", self.jira.requests[-1].query)
        self.assertEqual(self.gateway.find_users("olduser", issue_key="PROJ-1"), [])

    def test_the_query_survives_spaces_quotes_ampersands_and_accents(self):
        self.mock.users.append({"key": "x", "name": "x", "displayName": "Zoë O'Neil & Co", "emailAddress": "z@example.com", "active": True})
        self.assertEqual([item.name for item in self.gateway.find_users("Zoë O'Neil & Co")], ["x"])

    def test_a_search_that_jira_refuses(self):
        with self.assertRaises(NotFoundError):
            self.gateway.find_users("x", issue_key="PROJ-999")

    def test_assign_sends_the_user_name_and_none_and_minus_one(self):
        self.gateway.assign_issue("PROJ-1", "asmith")
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["assignee"]["name"], "asmith")
        self.gateway.assign_issue("PROJ-1", None)
        self.assertIsNone(self.jira.issues["PROJ-1"]["fields"]["assignee"])
        self.gateway.assign_issue("PROJ-1", "-1")
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["assignee"]["name"], "jdoe")
        self.assertEqual([item.body for item in self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1/assignee")], [{"name": "asmith"}, {"name": None}, {"name": "-1"}])

    def test_the_statuses_of_an_assignment(self):
        with self.assertRaises(NotFoundError):
            self.gateway.assign_issue("PROJ-1", "nobody")
        with self.assertRaises(NotFoundError):
            self.gateway.assign_issue("PROJ-999", "asmith")
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/assignee", 401, method="PUT")
        with self.assertRaises(AuthError) as caught:
            self.gateway.assign_issue("PROJ-1", "asmith")
        self.assertEqual(caught.exception.details["status"], 401)


class ValidationGatewayTest(GatewayTestCase):
    def setUp(self):
        super().setUp()
        self.mock = validation_routes(self.jira)
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))

    def test_the_edit_metadata_is_mapped_into_rules_by_field_id(self):
        rules = self.gateway.get_edit_metadata("PROJ-1")
        self.assertEqual(self.jira.requests[-1].path, "/rest/api/2/issue/PROJ-1/editmeta")
        self.assertEqual(rules["priority"].allowed_names, ["Highest", "High", "Medium", "Low", "Lowest"])
        self.assertEqual((rules["summary"].required, rules["summary"].allowed), (True, None))
        self.assertEqual(rules["components"].allowed_names, ["Demo", "Backend"])
        self.assertEqual(rules["fixVersions"].allowed_names, ["Release 1.0", "Release 2.0"])
        self.assertEqual((rules["priority"].has_default, rules["labels"].allowed), (True, None))

    def test_a_project_without_components_has_an_empty_list_and_no_list_is_something_else(self):
        self.jira.issues["DEMO-1"] = self.jira.issues["PROJ-1"]
        rules = self.gateway.get_edit_metadata("DEMO-1")
        self.assertEqual(rules["components"].allowed, ())
        self.assertIsNone(rules["labels"].allowed)

    def test_an_unknown_issue_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.gateway.get_edit_metadata("PROJ-999")

    def test_the_create_metadata_of_a_type_is_mapped_into_rules(self):
        self.mock.required_to_create["10000"] = {"customfield_10005": field_meta("customfield_10005", "Epic Name", required=True)}
        rules = self.gateway.get_create_fields("PROJ", "10000")
        self.assertEqual(self.jira.requests[-1].path, "/rest/api/2/issue/createmeta/PROJ/issuetypes/10000")
        self.assertEqual((rules["customfield_10005"].name, rules["customfield_10005"].required), ("Epic Name", True))
        self.assertEqual(rules["priority"].allowed_names, ["Highest", "High", "Medium", "Low", "Lowest"])
        self.assertNotIn("customfield_10005", self.gateway.get_create_fields("PROJ", "10200"))

    def test_the_old_create_metadata_call_still_gives_the_summary_first(self):
        self.assertEqual(self.gateway.create_metadata("PROJ", "10200")["values"][0]["fieldId"], "summary")

    def test_a_server_that_caps_the_page_gives_every_field_in_the_end(self):
        self.mock.page_size = 2  # the screen has six fields: three pages
        rules = self.gateway.get_create_fields("PROJ", "10200")
        self.assertEqual(sorted(rules), ["components", "description", "fixVersions", "labels", "priority", "summary"])
        asked = self.jira.sent("GET", "/rest/api/2/issue/createmeta/PROJ/issuetypes/10200")
        self.assertEqual([item.query["startAt"] for item in asked], [["0"], ["2"], ["4"]])
        self.assertEqual({item.query["maxResults"][0] for item in asked}, {"1000"})
        self.assertEqual(rules["components"].allowed_names, ["Demo", "Backend"])  # a field of the second page, with its values

    def test_a_server_that_puts_everything_on_one_page_is_asked_once(self):
        self.gateway.get_create_fields("PROJ", "10200")
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/createmeta/PROJ/issuetypes/10200")), 1)

    def test_an_issue_type_that_the_project_lacks_is_not_found_on_the_first_page(self):
        with self.assertRaises(NotFoundError):
            self.gateway.get_create_fields("NOPE", "10200")

    def test_the_transitions_come_with_the_fields_of_their_screens(self):
        self.mock.screens["81"] = {"resolution": resolution_field()}
        screens = self.gateway.list_transition_screens("PROJ-2")
        self.assertEqual(self.jira.requests[-1].query, {"expand": ["transitions.fields"]})
        self.assertEqual([(item.transition.id, item.transition.name, item.transition.to_status.name) for item in screens], [("111", "Reopen", "To Do"), ("81", "Finish", "Done")])
        self.assertEqual(screens[0].fields, ())
        (resolution,) = screens[1].fields
        self.assertEqual((resolution.field_id, resolution.required, resolution.allowed_names), ("resolution", True, ["Fixed", "Won't Fix", "Duplicate"]))

    def test_the_plain_transitions_call_is_still_answered_by_the_mock_itself(self):
        self.assertEqual([item.name for item in self.gateway.list_transitions("PROJ-2")], ["Reopen", "Finish"])


if __name__ == "__main__":
    unittest.main()
