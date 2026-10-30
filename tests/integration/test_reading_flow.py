"""Reading against the mock Jira with the real adapters: what really goes over the wire for several keys, the rendered text, the history,
saved filters and queries from files, and what comes back."""
import json
import os
import subprocess
import sys
import unittest

from tests.integration.test_end_to_end import ROOT, EndToEnd
from tests.support.mock_jira import ACCEPT
from tests.support.mock_reading import add_filter, add_history, reading_routes

LONG = "h1. Goal\nShip *it* for [~jdoe].\n\nh2. Steps\n# open\n# click\n\nh2. Result\n* fast\n* {{small}}\n\n{code}\nx = 1\n{code}"
CODE_FIRST = "{code}\n" + "\n".join(f"at line {n}" for n in range(60)) + "\n{code}\n\nWhat happened after the stack trace."
REVIEW_JQL = 'assignee = jdoe AND reporter in ("asmith","jane.doe") AND watcher = "Alex Smith" ORDER BY created'
REVIEW_SHOWN = "assignee = [user] AND reporter in ([user],[user]) AND watcher = [user] ORDER BY created"
PEOPLE = ("jdoe", "asmith", "jane.doe", "Alex", "Smith", "Jane")


class ReadingFlow(EndToEnd):
    def setUp(self):
        super().setUp()
        self.jira.page_cap = 100

    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])

    def searches(self):
        return self.jira.sent("GET", "/rest/api/2/search")

    def issue_requests(self):
        return [item for item in self.jira.requests if item.path.startswith("/rest/api/2/issue/")]


class SeveralKeysTest(ReadingFlow):
    def test_three_keys_are_one_search_request_in_the_order_given(self):
        result = self.cli("issue", "PROJ-3", "PROJ-1", "PROJ-2", "--only", "summary,status", "--json", "-v")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-3", "PROJ-1", "PROJ-2"])
        self.assertEqual(data["issues"][1], {"key": "PROJ-1", "summary": "Export the report: Ünïcode & more", "status": {"name": "To Do", "category": "new"}})
        self.assertEqual(data["errors"], [])
        (search,) = self.searches()
        self.assertEqual(search.query["jql"], ["key in (PROJ-3, PROJ-1, PROJ-2)"])
        self.assertEqual(search.query["fields"], ["*all"])
        self.assertEqual(self.issue_requests(), [])
        self.assertEqual(result.err.count("GET /rest/api/2/search"), 1)  # -v: one request for the issues (the fields of this Jira come once, and are not an issue)
        self.assertEqual(result.err.count("/rest/api/2/issue/"), 0)

    def test_a_server_that_answers_two_at_a_time_is_asked_again_for_the_rest(self):
        self.jira.page_cap = 2
        data = self.cli("issue", "PROJ-1", "PROJ-2", "PROJ-3", "--only", "status", "--json").json()
        self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-1", "PROJ-2", "PROJ-3"])
        self.assertEqual([item.query["startAt"] for item in self.searches()], [["0"], ["2"]])

    def test_the_issues_come_with_every_field_the_comments_included(self):
        self.cli("comment", "PROJ-1", "--text", "A note.")
        data = self.cli("issue", "PROJ-1", "PROJ-2", "--comments", "--json").json()
        first, second = data["issues"]
        self.assertEqual((first["storyPoints"], first["epicKey"], first["commentTotal"]), (5.0, "PROJ-9", 2))
        self.assertEqual([comment["body"] for comment in first["comments"]], ["Looks fine, [~user].", "A note."])
        self.assertEqual((second["commentTotal"], first["assignee"], first["comments"][0]["author"]), (1, None, None))

    def test_the_text_of_several_issues_is_each_what_one_issue_prints(self):
        several = self.cli("issue", "PROJ-1", "PROJ-2").out.split("\n\n=====\n\n")
        self.assertEqual(several[0] + "\n", self.cli("issue", "PROJ-1").out)
        self.assertEqual(several[1], self.cli("issue", "PROJ-2").out)

    def test_a_missing_key_makes_jira_refuse_the_search_and_each_key_is_asked_for_on_its_own(self):
        result = self.cli("issue", "PROJ-1", "PROJ-999", "PROJ-2", "--only", "summary", "--json")
        self.assertEqual(result.code, 4)
        data = result.json()
        self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-1", "PROJ-2"])
        (error,) = data["errors"]
        self.assertEqual((error["key"], error["code"]), ("PROJ-999", "NOT_FOUND"))
        self.assertIn("Issue Does Not Exist", error["message"])
        (search,) = self.searches()
        self.assertEqual(search.query["jql"], ["key in (PROJ-1, PROJ-999, PROJ-2)"])
        self.assertEqual([item.path for item in self.issue_requests()], ["/rest/api/2/issue/PROJ-1", "/rest/api/2/issue/PROJ-999", "/rest/api/2/issue/PROJ-2"])

    def test_the_text_says_where_a_key_is_missing(self):
        result = self.cli("issue", "PROJ-1", "PROJ-999", "--only", "summary")
        self.assertEqual(result.code, 4)
        self.assertIn("PROJ-999  [NOT_FOUND]", result.out)
        self.assertEqual(result.err, "")

    def test_one_missing_key_is_an_error_as_before(self):
        result = self.cli("issue", "PROJ-999", "--json")
        self.assertEqual((result.code, result.error()["code"]), (4, "NOT_FOUND"))

    def test_a_refusal_of_the_token_is_an_error_not_a_missing_key(self):
        self.jira.fail_next("/rest/api/2/search", 403, body={"errorMessages": ["You do not have the permission to browse projects."]})
        result = self.cli("issue", "PROJ-1", "PROJ-2", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("permission", result.error()["message"])
        self.assertEqual(self.issue_requests(), [])

    def test_a_server_error_is_an_error_too(self):
        self.jira.fail_next("/rest/api/2/search", 503, times=5)
        self.assertEqual(self.cli("issue", "PROJ-1", "PROJ-2", env={"JIRA_RETRY": "0"}).code, 7)

    def test_a_search_that_hiccups_is_retried_as_every_read(self):
        self.jira.fail_next("/rest/api/2/search", 503)
        self.assertEqual(self.cli("issue", "PROJ-1", "PROJ-2", "--only", "status", env={"JIRA_RETRY": "1"}).code, 0)

    def test_many_keys_are_asked_for_in_batches_of_a_hundred(self):
        for number in range(1000, 1150):
            self.jira.issues[f"PROJ-{number}"] = json.loads(json.dumps(self.jira.issues["PROJ-1"]))
            self.jira.issues[f"PROJ-{number}"]["key"] = f"PROJ-{number}"
        keys = [f"PROJ-{number}" for number in range(1000, 1150)]
        result = self.cli("issue", *keys, "--only", "status", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual([item["key"] for item in result.json()["issues"]], keys)
        self.assertEqual(len(self.searches()), 2)
        self.assertEqual(len(self.searches()[0].query["jql"][0].split(",")), 100)

    def test_the_policy_keeps_people_out(self):
        result = self.cli("issue", "PROJ-1", "PROJ-2", "--comments", env={"JIRA_HIDE_USERS": "true"})
        for personal in ("Jane", "jdoe", "jane.doe"):
            self.assertNotIn(personal, result.out + result.err)
        self.assertEqual(self.cli("issue", "PROJ-1", "PROJ-2", "--with-users", env={"JIRA_HIDE_USERS": "true"}).code, 1)

    def test_one_key_is_still_one_ordinary_request(self):
        self.cli("issue", "PROJ-1")
        self.assertEqual(self.searches(), [])
        self.assertEqual([item.path for item in self.issue_requests()], ["/rest/api/2/issue/PROJ-1"])
        self.assertEqual(self.issue_requests()[0].query, {})

    def test_a_key_given_twice_is_a_list_of_one_issue_that_costs_one_ordinary_request(self):
        data = self.cli("issue", "PROJ-1", "proj-1", "--only", "summary", "--json").json()
        self.assertEqual((data["count"], [item["key"] for item in data["issues"]], data["errors"]), (1, ["PROJ-1"], []))
        self.assertEqual(self.searches(), [])
        self.assertEqual([item.path for item in self.issue_requests()], ["/rest/api/2/issue/PROJ-1"])
        self.assertEqual(sorted(data), sorted(self.cli("issue", "PROJ-1", "PROJ-2", "--only", "summary", "--json").json()))
        self.assertEqual(self.cli("issue", "PROJ-1", "PROJ-1", "--raw").code, 2)

    def test_a_missing_key_given_twice_is_a_list_with_one_error_entry(self):
        result = self.cli("issue", "PROJ-999", "proj-999", "--json")
        self.assertEqual(result.code, 4)
        data = result.json()
        self.assertEqual((data["count"], data["issues"], [(item["key"], item["code"]) for item in data["errors"]]), (0, [], [("PROJ-999", "NOT_FOUND")]))
        self.assertEqual([item.path for item in self.issue_requests()], ["/rest/api/2/issue/PROJ-999"])
        self.assertEqual(self.cli("issue", "PROJ-999", "--json").code, 4)  # given once it is an error: nothing on standard output
        self.assertEqual(self.cli("issue", "PROJ-999", "--json").out, "")


class PartsTest(ReadingFlow):
    def setUp(self):
        super().setUp()
        self.jira.issues["PROJ-1"]["fields"]["description"] = LONG

    def test_the_outline_and_a_section(self):
        out = self.cli("issue", "PROJ-1", "--outline", "--only", "description", "--json").json()
        self.assertEqual([(item["level"], item["title"]) for item in out["outline"]], [(1, "Goal"), (2, "Steps"), (2, "Result")])
        section = self.cli("issue", "PROJ-1", "--section", "steps", "--only", "description", "--json").json()
        self.assertEqual(section["description"], "h2. Steps\n# open\n# click")
        self.assertEqual(section["section"]["title"], "Steps")

    def test_a_cut_and_the_sizes(self):
        data = self.cli("issue", "PROJ-1", "--only", "description", "--max-chars", "60", "--json").json()
        self.assertTrue(data["truncated"]["description"])
        self.assertGreater(data["totalChars"]["description"], len(data["description"]))
        self.assertLessEqual(len(data["description"]), 60)
        self.assertTrue(data["description"].endswith("Ship *it* for [~user]."))

    def test_a_description_that_opens_with_a_code_block_is_cut_at_a_line_and_the_block_is_closed(self):
        self.jira.issues["PROJ-1"]["fields"]["description"] = CODE_FIRST
        data = self.cli("issue", "PROJ-1", "--only", "description", "--max-chars", "100", "--json").json()
        lines = data["description"].split("\n")
        self.assertEqual((lines[0], lines[-1], len(lines)), ("{code}", "{code}", 10))
        self.assertTrue(set(lines) <= set(CODE_FIRST.split("\n")))  # whole lines of the description, no half line
        self.assertLessEqual(len(data["description"]), 100)
        self.assertEqual((data["truncated"], data["totalChars"]), ({"description": True}, {"description": len(CODE_FIRST)}))
        text = self.cli("issue", "PROJ-1", "--only", "description", "--max-chars", "100").out
        self.assertIn("characters; the code block is closed at the cut;", text)

    def test_a_selector_that_only_looks_like_a_number_is_a_clean_error(self):
        for selector in ("\u00b2", "\u2460", "1" * 5000):
            with self.subTest(selector=selector[:6], length=len(selector)):
                result = self.cli("issue", "PROJ-1", "--section", selector, "--only", "description", "--json")
                self.assertEqual(result.code, 1)
                self.assertEqual(result.error()["code"], "INVALID")
                self.assertIn("No heading matches", result.error()["message"])
                self.assertIn("1. Goal | 2. Steps | 3. Result", result.error()["hint"])
        self.assertEqual(self.cli("issue", "PROJ-1", "--section", "3", "--only", "description", "--json").json()["section"]["title"], "Result")

    def test_the_attachments_of_an_issue_are_named(self):
        self.jira.issues["PROJ-1"]["fields"]["attachment"] = [
            {"id": "10", "filename": "design.png", "mimeType": "image/png", "size": 2048, "created": "2026-10-03T08:00:00.000+0200", "author": {"name": "jdoe", "displayName": "Jane Doe"}, "content": "x"}
        ]
        text = self.cli("issue", "PROJ-1").out
        self.assertIn("Attachments: 1\n  design.png\n", text)
        data = self.cli("issue", "PROJ-1", "--json").json()
        self.assertEqual((data["attachmentTotal"], data["attachments"][0]["filename"], data["attachments"][0]["author"]), (1, "design.png", None))
        self.assertNotIn("Jane", self.cli("issue", "PROJ-1", "--only", "attachments").out)


class RenderedTest(ReadingFlow):
    def setUp(self):
        super().setUp()
        self.jira.issues["PROJ-1"]["fields"].update(description=LONG, **{ACCEPT: "# one\n# two"})

    def test_the_rendered_description_as_plain_text_with_the_structure_kept(self):
        result = self.cli("issue", "PROJ-1", "--rendered", "--only", "description,acceptance", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(
            data["renderedDescription"],
            "# Goal\n\nShip it for [~user].\n\n## Steps\n\n1. open\n2. click\n\n## Result\n\n- fast\n- `small`\n\n```\nx = 1\n```",
        )
        self.assertEqual(data["renderedAcceptance"], "1. one\n2. two")
        self.assertEqual(data["description"], LONG.replace("[~jdoe]", "[~user]"))  # the markup stays as it is, without the person
        (request,) = self.issue_requests()
        self.assertEqual((request.path, request.query), ("/rest/api/2/issue/PROJ-1", {"expand": ["renderedFields"]}))

    def test_a_mention_is_not_a_person_unless_people_are_asked_for(self):
        self.assertNotIn("Jane", self.cli("issue", "PROJ-1", "--rendered").out)
        self.assertIn("Ship it for Jane Doe", self.cli("issue", "PROJ-1", "--rendered", "--with-users").out)

    def test_several_issues_with_one_search_that_expands_the_rendering(self):
        data = self.cli("issue", "PROJ-1", "PROJ-2", "--rendered", "--only", "description", "--json").json()
        self.assertEqual(len(data["issues"]), 2)
        self.assertEqual(data["issues"][1]["renderedDescription"], "Ask [~user] or [email]\nSecond line")
        (search,) = self.searches()
        self.assertEqual(search.query["expand"], ["renderedFields"])

    def test_text_that_jira_renders_wrongly_shows_in_the_output(self):
        self.jira.issues["PROJ-1"]["fields"]["description"] = "{{token }} and *bold*"
        self.assertIn("{{token }}", self.cli("issue", "PROJ-1", "--rendered", "--only", "description").out)


class HistoryTest(ReadingFlow):
    def setUp(self):
        super().setUp()
        add_history(self.jira, "PROJ-1", "2026-10-02T08:00:00.000+0200", ("status", "In Progress", "Done"))
        add_history(self.jira, "PROJ-1", "2026-10-01T08:00:00.000+0200", ("assignee", "", "jdoe"), ("status", "To Do", "In Progress"))

    def test_the_status_changes_oldest_first_and_without_people(self):
        result = self.cli("issue", "PROJ-1", "--history", "--only", "status", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual([(item["created"][:10], item["from"], item["to"]) for item in data["history"]], [("2026-10-01", "To Do", "In Progress"), ("2026-10-02", "In Progress", "Done")])
        self.assertTrue(data["historyComplete"])
        for personal in ("jdoe", "Jane", "jane.doe", "author"):
            self.assertNotIn(personal, result.out)
        (request,) = self.issue_requests()
        self.assertEqual((request.path, request.query), ("/rest/api/2/issue/PROJ-1", {"expand": ["changelog"]}))  # the changelog is a part of the issue on Data Center

    def test_the_text(self):
        out = self.cli("issue", "PROJ-1", "--history", "--only", "summary").out
        self.assertIn("History (status changes, oldest first):\n  2026-10-01T08:00:00.000+0200  To Do -> In Progress\n  2026-10-02T08:00:00.000+0200  In Progress -> Done", out)

    def test_a_custom_field_that_is_called_status_is_no_history(self):
        add_history(self.jira, "PROJ-1", "2026-10-03T08:00:00.000+0200", ("status", "Jane Doe", "John Roe", "custom"))
        add_history(self.jira, "PROJ-1", "2026-10-04T08:00:00.000+0200", ("Status", "Done", "To Do"), ("status", "Alex Smith", "Jane Doe", "custom"))
        result = self.cli("issue", "PROJ-1", "--history", "--json")
        data = result.json()
        self.assertEqual([(item["created"][:10], item["from"], item["to"]) for item in data["history"]], [("2026-10-01", "To Do", "In Progress"), ("2026-10-02", "In Progress", "Done"), ("2026-10-04", "Done", "To Do")])
        self.assertTrue(data["historyComplete"])
        for personal in ("Jane", "John", "Alex", "Smith"):
            self.assertNotIn(personal, result.out + self.cli("issue", "PROJ-1", "--history").out)

    def test_a_history_that_jira_sends_in_part_is_said_to_be_incomplete(self):
        add_history(self.jira, "PROJ-1", "2026-10-03T08:00:00.000+0200", ("status", "Done", "To Do"))
        reading_routes(self.jira).changelog_limit = 2
        data = self.cli("issue", "PROJ-1", "--history", "--json").json()
        self.assertEqual(len(data["history"]), 2)  # the two entries that came: both changed the status
        self.assertFalse(data["historyComplete"])
        self.assertIn("Jira sent only part of the history", self.cli("issue", "PROJ-1", "--history").out)

    def test_several_issues_have_their_own_history_from_one_search(self):
        data = self.cli("issue", "PROJ-1", "PROJ-2", "--history", "--only", "summary", "--json").json()
        self.assertEqual([len(item["history"]) for item in data["issues"]], [2, 0])
        (search,) = self.searches()
        self.assertEqual(search.query["expand"], ["changelog"])

    def test_the_history_and_the_rendering_in_one_request(self):
        self.cli("issue", "PROJ-1", "--history", "--rendered")
        (request,) = self.issue_requests()
        self.assertEqual(request.query, {"expand": ["renderedFields,changelog"]})

    def test_no_changelog_endpoint_is_used(self):
        self.cli("issue", "PROJ-1", "PROJ-2", "--history")
        self.cli("issue", "PROJ-1", "--history")
        self.assertFalse([item for item in self.jira.requests if item.path.endswith("/changelog")])


class FiltersTest(ReadingFlow):
    def setUp(self):
        super().setUp()
        add_filter(self.jira, "10000", "Open stories", 'project = "PROJ" AND issuetype = Story ORDER BY key DESC', description="Stories that are not done")
        add_filter(self.jira, "10001", "All of PROJ", 'project = "PROJ"')
        add_filter(self.jira, "10002", "Not a favourite", "project = DEMO", favourite=False)

    def test_the_favourites_are_listed(self):
        result = self.cli("filters", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual([item["id"] for item in data], ["10000", "10001"])
        self.assertEqual(data[0], {"id": "10000", "name": "Open stories", "description": "Stories that are not done", "jql": 'project = "PROJ" AND issuetype = Story ORDER BY key DESC', "favourite": True, "owner": None})
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/filter/favourite")), 1)

    def test_the_owner_with_users(self):
        self.assertEqual(self.cli("filters", "--json", "--with-users").json()[0]["owner"]["name"], "jdoe")
        self.assertNotIn("Jane", self.cli("filters").out)

    def test_the_people_that_the_jql_names_stay_out_and_so_does_the_address(self):
        add_filter(self.jira, "10003", "Theirs", REVIEW_JQL, description="Owned by jane.doe@example.com")
        for env in ({}, {"JIRA_HIDE_USERS": "true"}):
            with self.subTest(env=env):
                listed = self.cli("filters", "--json", env=env)
                one = self.cli("filter", "10003", "--json", env=env)
                text = self.cli("filter", "10003", env=env)
                table = self.cli("filters", env=env)
                self.assertEqual((listed.code, one.code, text.code, table.code), (0, 0, 0, 0))
                theirs = [item for item in listed.json() if item["id"] == "10003"][0]
                self.assertEqual((theirs["jql"], theirs["description"], theirs["owner"]), (REVIEW_SHOWN, "Owned by [email]", None))
                self.assertEqual((one.json()["jql"], one.json()["searchUrl"], one.json()["owner"]), (REVIEW_SHOWN, "", None))
                self.assertRegex(text.out, r"Search URL: +\(left out: the JQL names people\)")
                for shown in (listed, one, text, table):
                    for person in PEOPLE:
                        self.assertNotIn(person, shown.out + shown.err)
                self.assertIn("the search address is left out", one.err)
                self.assertIn("names people or personal data", listed.err)

    def test_a_filter_that_names_nobody_keeps_its_address_and_says_nothing(self):
        one = self.cli("filter", "10001", "--json", env={"JIRA_HIDE_USERS": "true"})
        self.assertEqual((one.json()["jql"], one.err), ('project = "PROJ"', ""))
        self.assertTrue(one.json()["searchUrl"].startswith(self.jira.base_url + "/rest/api/2/search?jql=project"))

    def test_with_users_the_jql_and_the_address_are_as_jira_sent_them(self):
        add_filter(self.jira, "10003", "Theirs", REVIEW_JQL)
        data = self.cli("filter", "10003", "--with-users", "--json").json()
        self.assertEqual((data["jql"], data["owner"]["name"]), (REVIEW_JQL, "jdoe"))
        self.assertIn("jdoe", data["searchUrl"])  # what the test above keeps out
        self.assertEqual(self.cli("filter", "10003", "--with-users", env={"JIRA_HIDE_USERS": "true"}).code, 1)

    def test_one_filter_with_its_addresses(self):
        data = self.cli("filter", "10002", "--json").json()
        self.assertEqual((data["id"], data["favourite"], data["jql"]), ("10002", False, "project = DEMO"))
        self.assertTrue(data["searchUrl"].startswith(self.jira.base_url + "/rest/api/2/search?jql=project"))
        self.assertTrue(data["viewUrl"].startswith(self.jira.base_url))
        self.assertIn("Run it:", self.cli("filter", "10000").out)

    def test_an_unknown_filter_is_not_found(self):
        result = self.cli("filter", "99999", "--json")
        self.assertEqual((result.code, result.error()["code"]), (4, "NOT_FOUND"))
        self.assertIn("jira filters", result.error()["hint"])

    def test_a_refusal_is_an_auth_error(self):
        self.jira.fail_next("/rest/api/2/filter/favourite", 401, body={"errorMessages": ["You are not authenticated."]})
        self.assertEqual(self.cli("filters").code, 5)

    def test_issues_runs_a_filter_and_the_wire_carries_a_filter_clause(self):
        result = self.cli("issues", "--filter", "10000", "--all", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(data["jql"], "filter = 10000")
        self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-4", "PROJ-3", "PROJ-1"])  # the stories, in the order of the filter
        self.assertEqual(self.searches()[0].query["jql"], ["filter = 10000"])

    def test_a_jql_narrows_the_filter(self):
        data = self.cli("issues", "--filter", "10000", "--jql", 'status = "To Do"', "--all", "--json").json()
        self.assertEqual(data["jql"], 'filter = 10000 AND (status = "To Do")')
        self.assertEqual([item["key"] for item in data["issues"]], ["PROJ-4", "PROJ-1"])

    def test_search_adds_the_filter_to_its_words_and_filters(self):
        data = self.cli("search", "--filter", "10001", "--status", "Done", "--json").json()
        self.assertEqual([hit["key"] for hit in data["hits"]], ["PROJ-3"])
        self.assertEqual(data["jql"], 'project = "PROJ" AND filter = 10001 AND status = "Done" ORDER BY updated DESC')

    def test_a_filter_that_does_not_exist_is_refused_with_a_hint_that_says_where_to_look(self):
        for argv in (("issues", "--filter", "99999", "--json"), ("search", "--filter", "99999", "--json")):
            with self.subTest(argv=argv):
                result = self.cli(*argv)
                self.assertEqual(result.code, 6)
                self.assertIn("does not exist for the field 'filter'", result.error()["message"])
                self.assertIn("jira filter 99999", result.error()["hint"])

    def test_the_format_of_a_list(self):
        out = self.cli("filters", "--format", "md").out.splitlines()
        self.assertEqual(out[0], "| ID | NAME | JQL |")
        self.assertEqual(len(out), 4)


class BranchTest(ReadingFlow):
    NAME = "feature/PROJ-1-ask-user-or-email-about-the-jane-doe"

    def setUp(self):
        super().setUp()
        self.jira.issues["PROJ-1"]["fields"]["summary"] = "Ask [~jdoe] or jane.doe@example.com about the Jane Doe report"

    def test_the_name_has_no_mention_and_no_address_in_it_also_under_the_policy(self):
        for env in ({}, {"JIRA_HIDE_USERS": "true"}):
            with self.subTest(env=env):
                self.assertEqual(self.cli("branch", "PROJ-1", env=env).out, self.NAME + "\n")
                self.assertEqual(self.cli("branch", "PROJ-1", "--json", env=env).json(), {"key": "PROJ-1", "branch": self.NAME})

    def test_it_is_made_of_the_summary_that_issue_shows(self):
        self.assertEqual(self.cli("issue", "PROJ-1", "--only", "summary", "--json").json()["summary"], "Ask [~user] or [email] about the Jane Doe report")


class QueryInputTest(ReadingFlow):
    JQL = 'project = "PROJ" AND "Epic Link" = PROJ-9 ORDER BY key'

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data)
        return str(path)

    def test_a_query_with_double_quotes_from_a_file_gives_the_same_result_as_typed(self):
        typed = self.cli("issues", "--jql", self.JQL, "--all", "--json").json()
        path = self.write("q.jql", self.JQL.encode("utf-8"))
        from_file = self.cli("issues", "--jql", "@" + path, "--all", "--json")
        self.assertEqual(from_file.code, 0, from_file.err)
        self.assertEqual(from_file.json(), typed)
        self.assertEqual([item["key"] for item in typed["issues"]], ["PROJ-1", "PROJ-2"])
        self.assertEqual({item.query["jql"][0] for item in self.searches()}, {self.JQL})

    def test_a_file_that_windows_powershell_wrote_in_utf16(self):
        path = self.write("q16.jql", self.JQL.encode("utf-16"))
        result = self.cli("issues", "--jql", "@" + path, "--all", "--json")
        self.assertEqual([item["key"] for item in result.json()["issues"]], ["PROJ-1", "PROJ-2"])

    def test_standard_input(self):
        result = self.cli("issues", "--jql", "-", "--all", "--json", stdin=self.JQL + "\n")
        self.assertEqual([item["key"] for item in result.json()["issues"]], ["PROJ-1", "PROJ-2"])

    def test_a_missing_file_says_which(self):
        result = self.cli("issues", "--jql", "@" + str(self.folder / "nope.jql"))
        self.assertEqual(result.code, 1)
        self.assertIn("nope.jql", result.err)
        self.assertEqual(self.searches(), [])

    def test_a_condition_for_search_from_a_file(self):
        path = self.write("c.jql", b'"Epic Link" = PROJ-9')
        data = self.cli("search", "--jql", "@" + path, "--json").json()
        self.assertEqual(sorted(hit["key"] for hit in data["hits"]), ["PROJ-1", "PROJ-2"])

    def test_the_text_of_a_search_from_a_file_with_a_phrase_in_double_quotes(self):
        self.jira.issues["PROJ-2"]["fields"]["description"] = "An append only audit trail of every change."
        path = self.write("question.txt", 'where is the "audit trail" described?'.encode("utf-16"))
        data = self.cli("search", "@" + path, "--json").json()
        self.assertIn('"audit trail"', data["terms"])
        self.assertEqual([hit["key"] for hit in data["hits"]][:1], ["PROJ-2"])

    def test_phrases_without_any_double_quote(self):
        self.jira.issues["PROJ-2"]["fields"]["description"] = "An append only audit trail of every change."
        data = self.cli("search", "--phrase", "audit trail", "--json").json()
        self.assertEqual(data["terms"], ['"audit trail"'])
        self.assertEqual([hit["key"] for hit in data["hits"]], ["PROJ-2"])
        self.assertEqual(self.searches()[0].query["jql"], ['project = "PROJ" AND text ~ "\\"audit trail\\""'])


class ProcessTest(ReadingFlow):
    """The real program in its own process, as a shell starts it: standard input, a file from Windows PowerShell, the exit code, the encoding."""

    def process(self, *argv, stdin=b""):
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_") and key != "PYTHONPATH"}
        environment = {**inherited, **self.env, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"}
        return subprocess.run([sys.executable, str(ROOT / "jira.py"), *argv], input=stdin, capture_output=True, env=environment, cwd=self.folder, timeout=60)

    def test_a_query_from_standard_input_with_a_word_that_is_no_ascii(self):
        done = self.process("issues", "--jql", "-", "--all", "--json", stdin='project = "PROJ" AND summary ~ "Ünïcode"\n'.encode("utf-8"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([item["key"] for item in json.loads(done.stdout)["issues"]], ["PROJ-1"])

    def test_a_query_from_a_file_that_windows_powershell_wrote(self):
        (self.folder / "q.jql").write_bytes('project = "PROJ" AND summary ~ "Ünïcode"'.encode("utf-16"))
        done = self.process("issues", "--jql", "@q.jql", "--all", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([item["key"] for item in json.loads(done.stdout)["issues"]], ["PROJ-1"])

    def test_several_issues_print_utf8_without_carriage_returns_and_a_missing_key_is_exit_code_4(self):
        done = self.process("issue", "PROJ-1", "PROJ-999", "--only", "summary")
        self.assertEqual(done.returncode, 4, done.stderr)
        self.assertIn("Ünïcode".encode("utf-8"), done.stdout)
        self.assertNotIn(b"\r", done.stdout)
        self.assertIn(b"PROJ-999  [NOT_FOUND]", done.stdout)

    def test_no_key_is_exit_code_2(self):
        self.assertEqual(self.process("issue").returncode, 2)
        self.assertEqual(self.process("issue", "PROJ-1", "--only", "nope").returncode, 2)


class ListOutputTest(ReadingFlow):
    def test_select_and_format_on_the_wire(self):
        data = self.cli("issues", "--all", "--json", "--select", "key,status.name").json()
        self.assertEqual(data["issues"][-1], {"key": "PROJ-1", "status": {"name": "To Do"}})  # the newest first, as `issues` lists them
        md = self.cli("issues", "--all", "--format", "md", "--no-assignee").out.splitlines()
        self.assertEqual(md[0], "| KEY | TYPE | STATUS | SUMMARY |")
        self.assertIn("| PROJ-1 | Story | To Do | Export the report: Ünïcode & more |", md)
        tsv = self.cli("fields", "--format", "tsv", "--custom", "--select", "id,name").out.splitlines()
        self.assertEqual(tsv[0], "id\tname")
        self.assertIn("customfield_10002\tEpic Link", tsv)

    def test_the_search_table_as_markdown(self):
        out = self.cli("search", "--status", "Done", "--format", "md").out.splitlines()
        self.assertEqual(out[0], "| KEY | TYPE | STATUS | EPIC | SUMMARY |")
        self.assertEqual(out[2], "| PROJ-3 | Story | Done | PROJ-8 | Finished story |")


if __name__ == "__main__":
    unittest.main()
