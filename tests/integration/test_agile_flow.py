"""The whole program for agile: the real adapters against the mock Jira, and the gateway on its own.

Every class ends by asserting that the mock Jira was asked only for calls that the 11.3 reference documents."""
import json
from unittest import mock

import jira_dc_cli.adapters.driven.rest_agile as rest_agile
from jira_dc_cli.domain.errors import NotFoundError, RejectedError, ValidationError
from tests.integration.test_end_to_end import EndToEnd
from tests.integration.test_http_and_gateway import GatewayTestCase
from tests.support.mock_agile import add_board, add_issue, add_sprint, history_entry, routes_of, seed_review
from tests.support.mock_jira import STATUSES, _Failure

PEOPLE = ("Jane", "jane.doe", "jdoe")
AGILE = "/rest/agile/1.0"


class AgileFlow(EndToEnd):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))
        self.routes = routes_of(self.jira)

    def json(self, *argv, **options):
        result = self.cli(*argv, "--json", **options)
        self.assertEqual(result.code, 0, result.err)
        return result.json()

    def paths(self, method="GET"):
        return [item.path for item in self.jira.requests if item.method == method]

    def calls(self, path, method="GET"):
        """The requests to exactly this path (`sent` matches a prefix)."""
        return [item for item in self.jira.requests if item.method == method and item.path == path]

    def fail_nth(self, prefix, number, status, *, method="POST", body=None, onward=False):
        """Fails the `number`th request (counted from 1) of this method to a path that starts with `prefix`: only that one, or with
        `onward` that one and every later one."""
        original = self.jira.take_failure
        seen = []

        def take(request_method, path):
            if request_method == method and path.startswith(prefix):
                seen.append(path)
                if len(seen) == number or (onward and len(seen) > number):
                    return _Failure(prefix, status, 1, body, method)
            return original(request_method, path)

        self.jira.take_failure = take


class BoardsTest(AgileFlow):
    def test_the_board_that_the_mock_always_has(self):
        data = self.json("boards")
        self.assertEqual(data["boards"], [{"id": 1200, "name": "Demo board", "type": "scrum", "projects": ["PROJ"]}])
        first = self.cli("api", "GET", f"{AGILE}/board").json()["values"][0]  # what the tests that existed before still read
        self.assertEqual((first["id"], first["name"], first["type"]), (1200, "Demo board", "scrum"))

    def test_boards_are_paged_filtered_and_given_their_projects(self):
        add_board(self.jira, 1300, "Flow board", "kanban", ("DEMO",))
        add_board(self.jira, 1201, "Second board", "scrum", ("PROJ", "DEMO"))
        add_board(self.jira, 1202, "Third board", "scrum", ("DEMO",))
        everything = self.json("boards")
        self.assertEqual(everything["total"], 4)
        self.assertEqual([board["name"] for board in everything["boards"]], ["Demo board", "Flow board", "Second board", "Third board"])
        self.assertGreater(len(self.calls(f"{AGILE}/board")), 1)  # the server gives two boards a page: the client pages on
        by_name = {board["id"]: board["projects"] for board in everything["boards"]}
        self.assertEqual((by_name[1201], by_name[1300]), (["PROJ", "DEMO"], ["DEMO"]))
        self.assertEqual([board["id"] for board in self.json("boards", "--type", "kanban")["boards"]], [1300])
        self.assertEqual([board["id"] for board in self.json("boards", "--project", "DEMO", "--type", "scrum")["boards"]], [1201, 1202])
        self.assertEqual([board["id"] for board in self.json("boards", "--name", "second")["boards"]], [1201])
        self.assertEqual(self.calls(f"{AGILE}/board")[-1].query["name"], ["second"])

    def test_the_board_type_that_the_reference_does_not_list_is_filtered_here(self):
        add_board(self.jira, 1400, "Simple board", "simple", ("DEMO",))
        self.assertEqual([board["id"] for board in self.json("boards", "--type", "simple")["boards"]], [1400])
        self.assertNotIn("type", self.calls(f"{AGILE}/board")[-1].query)

    def test_select_and_format(self):
        self.assertEqual(self.json("boards", "--select", "id,projects")["boards"], [{"id": 1200, "projects": ["PROJ"]}])
        self.assertEqual(self.cli("boards", "--format", "tsv").out.splitlines()[1], "1200\tDemo board\tscrum\tPROJ")

    def test_a_refusal_through_the_real_stack(self):
        self.jira.fail_next(f"{AGILE}/board", 403, body={"errorMessages": ["You do not have a valid Jira Software licence."]})
        result = self.cli("boards", "--json")
        self.assertEqual(result.code, 5)
        self.assertEqual(result.error()["code"], "AUTH")
        self.assertIn("licence", result.error()["message"])
        self.assertIn("Browse Projects", result.error()["hint"])


class SprintsTest(AgileFlow):
    def setUp(self):
        super().setUp()
        seed_review(self.jira)

    def test_the_board_comes_from_the_project_and_the_sprints_are_paged(self):
        data = self.json("sprints", "--project", "PROJ")
        self.assertEqual([item["id"] for item in data["sprints"]], [7, 8, 6, 5])
        self.assertEqual((data["boardId"], data["total"], data["count"]), (1200, 4, 4))
        self.assertEqual(self.calls(f"{AGILE}/board")[0].query["projectKeyOrId"], ["PROJ"])
        self.assertEqual(self.calls(f"{AGILE}/board")[0].query["type"], ["scrum"])
        self.assertGreater(len(self.calls(f"{AGILE}/board/1200/sprint")), 1)

    def test_closed_sprints_have_id_name_state_dates_and_goal(self):
        data = self.json("sprints", "--board", "1200", "--state", "closed")
        self.assertEqual(
            data["sprints"][0],
            {
                "id": 6,
                "name": "Sprint 6",
                "state": "closed",
                "startDate": "2025-09-01T08:00:00.000+02:00",
                "endDate": "2025-09-15T08:00:00.000+02:00",
                "completeDate": "2025-09-15T16:00:00.000+02:00",
                "goal": "Ship the export",
                "boardId": 1200,
            },
        )
        self.assertEqual(self.calls(f"{AGILE}/board/1200/sprint")[0].query["state"], ["closed"])
        self.assertEqual(self.json("sprints", "--board", "1200", "--state", "future")["sprints"][0]["startDate"], "")

    def test_text_and_select(self):
        text = self.cli("sprints", "--board", "1200", "--limit", "2").out
        self.assertEqual([line.split()[0] for line in text.splitlines()[1:]], ["7", "8"])
        self.assertEqual(self.json("sprints", "--select", "id,goal", "--state", "active")["sprints"], [{"id": 7, "goal": "Stabilise"}])

    def test_a_kanban_board_has_no_sprints(self):
        add_board(self.jira, 1300, "Flow board", "kanban", ("DEMO",))
        result = self.cli("sprints", "--board", "1300")
        self.assertEqual(result.code, 1)
        self.assertIn("it is a kanban board", result.err)
        only_kanban = self.cli("sprints", "--project", "DEMO")
        self.assertEqual(only_kanban.code, 1)
        self.assertIn("1300 'Flow board' (kanban)", only_kanban.err)
        self.assertEqual(self.cli("api", "GET", f"{AGILE}/board/1300/sprint").code, 6)  # and Jira itself answers 400

    def test_several_scrum_boards_ask_for_the_board(self):
        add_board(self.jira, 1201, "Second board", "scrum", ("PROJ",))
        result = self.cli("sprints", "--project", "PROJ", "--json")
        self.assertEqual(result.code, 1)
        self.assertIn("1200 'Demo board'", result.error()["message"])
        self.assertIn("1201 'Second board'", result.error()["message"])
        self.assertEqual([item["id"] for item in self.json("sprints", "--board", "1201")["sprints"]], [])

    def test_an_unknown_board_and_an_unknown_project(self):
        self.assertEqual(self.cli("sprints", "--board", "999").code, 4)
        self.assertEqual(self.cli("sprints", "--project", "NOPE").code, 4)

    def test_one_sprint_by_id_and_by_name(self):
        by_id = self.json("sprint", "6")
        self.assertEqual((by_id["sprint"]["name"], by_id["sprint"]["goal"], by_id["issues"], by_id["review"]), ("Sprint 6", "Ship the export", None, None))
        self.assertEqual(self.json("sprint", "sprint 6")["sprint"]["id"], 6)
        self.assertEqual(self.json("sprint", "Sprint 8", "--board", "1200")["sprint"]["state"], "future")

    def test_an_unknown_sprint_is_not_found_and_an_unknown_name_lists_candidates(self):
        self.assertEqual(self.cli("sprint", "999").code, 4)
        result = self.cli("sprint", "Sprint 99", "--json")
        self.assertEqual((result.code, result.error()["code"]), (4, "NOT_FOUND"))
        self.assertIn("7 'Sprint 7' (active", result.error()["message"])

    def test_the_issues_of_a_sprint(self):
        data = self.json("sprint", "6", "--issues")
        self.assertEqual([issue["key"] for issue in data["issues"]], ["PROJ-21", "PROJ-22", "PROJ-23", "PROJ-25", "PROJ-26", "PROJ-27"])
        self.assertEqual(data["issues"][0], {"key": "PROJ-21", "issueType": "Story", "status": {"name": "Done", "category": "done"}, "storyPoints": 5.0, "epicKey": "", "summary": "Summary of PROJ-21"})
        self.assertIn("Issues in the sprint: 6", self.cli("sprint", "6", "--issues").out)


class ReviewTest(AgileFlow):
    def setUp(self):
        super().setUp()
        seed_review(self.jira)

    def review(self, *argv):
        return self.json("sprint", "6", "--review", *argv)["review"]

    def test_a_closed_sprint_is_reviewed_without_a_raw_api_call(self):
        review = self.review()
        self.assertEqual(review["committed"], {"issues": 4, "points": 23.0, "withoutPoints": 0, "keys": ["PROJ-21", "PROJ-22", "PROJ-24", "PROJ-25"]})
        self.assertEqual(review["completed"], {"issues": 2, "points": 13.0, "withoutPoints": 0, "keys": ["PROJ-21", "PROJ-23"]})
        self.assertEqual(review["added"], {"issues": 2, "points": 8.0, "withoutPoints": 1, "keys": ["PROJ-23", "PROJ-27"]})
        self.assertEqual(review["removed"], {"issues": 1, "points": 2.0, "withoutPoints": 0, "keys": ["PROJ-24"]})
        self.assertEqual(review["carriedOver"], {"issues": 3, "points": 16.0, "withoutPoints": 1, "keys": ["PROJ-22", "PROJ-25", "PROJ-27"]})
        self.assertEqual((review["excludedSubtasks"], review["ended"]), (1, True))
        self.assertEqual((review["start"], review["end"]), ("2025-09-01T08:00:00+02:00", "2025-09-15T16:00:00+02:00"))
        # what was asked of Jira: documented calls only, none of them `api`, none of the internal greenhopper ones
        self.assertEqual(
            sorted({request.path for request in self.jira.requests}),
            sorted([f"{AGILE}/sprint/6", "/rest/api/2/field", "/rest/api/2/search", "/rest/api/2/status"]),
        )
        searches = [request.query["jql"][0] for request in self.jira.sent("GET", "/rest/api/2/search")]
        self.assertIn("sprint = 6 ORDER BY key ASC", searches)
        self.assertIn("sprint was 6 AND sprint != 6 ORDER BY key ASC", searches)
        self.assertTrue(all(request.query["expand"] == ["changelog"] for request in self.jira.sent("GET", "/rest/api/2/search")))

    def test_by_name_and_in_text(self):
        text = self.cli("sprint", "Sprint 6", "--board", "1200", "--review").out
        self.assertRegex(text, r"Committed\s+4\s+23\s+0\s+PROJ-21, PROJ-22, PROJ-24, PROJ-25")
        self.assertRegex(text, r"Removed\s+1\s+2\s+0\s+PROJ-24")
        self.assertIn("Review of the sprint, 2025-09-01T08:00:00+02:00 to 2025-09-15T16:00:00+02:00", text)

    def test_sub_tasks_on_request(self):
        review = self.review("--with-subtasks")
        self.assertEqual((review["committed"]["keys"], review["excludedSubtasks"]), (["PROJ-21", "PROJ-22", "PROJ-24", "PROJ-25", "PROJ-26"], 0))
        self.assertEqual(review["committed"]["withoutPoints"], 1)

    def add_a_visitor(self):
        """PROJ-28: added to sprint 6 after its start and taken out again before its end, so only `sprint was` finds it."""
        add_issue(self.jira, "PROJ-28", "To Do", 1.0, created="2025-08-24T09:00:00.000+0200")
        self.routes.set_sprints_of("PROJ-28", [])
        history_entry(self.routes, "PROJ-28", "2025-09-03T10:00:00.000+0200", "Sprint", "", "6")
        history_entry(self.routes, "PROJ-28", "2025-09-10T10:00:00.000+0200", "Sprint", "6", "")

    def test_jira_that_does_not_search_the_history_of_the_sprint_field_gives_lower_bounds_for_three_groups(self):
        self.add_a_visitor()
        whole = self.review()
        self.assertTrue(whole["historyComplete"])
        keys = lambda review: [review[name]["keys"] for name in ("committed", "added", "removed")]  # noqa: E731
        self.assertEqual(
            keys(whole), [["PROJ-21", "PROJ-22", "PROJ-24", "PROJ-25"], ["PROJ-23", "PROJ-27", "PROJ-28"], ["PROJ-24", "PROJ-28"]]
        )
        self.routes.history_operator = False
        cut = self.review()
        self.assertFalse(cut["historyComplete"])
        self.assertEqual(keys(cut), [["PROJ-21", "PROJ-22", "PROJ-25"], ["PROJ-23", "PROJ-27"], []])  # none of the three has the issues that left
        self.assertEqual((cut["completed"], cut["carriedOver"]), (whole["completed"], whole["carriedOver"]))  # these do not depend on them
        note = next(note for note in cut["notes"] if "sprint was" in note)
        for word in ("removed", "committed", "added", "lower bounds", "velocity"):
            self.assertIn(word, note)

    def test_the_text_of_the_review_without_sprint_was_marks_the_three_groups_and_has_one_footnote(self):
        whole = self.cli("sprint", "6", "--review").out
        self.assertNotIn("incomplete", whole)
        self.assertNotIn("*", whole)
        self.routes.history_operator = False
        text = self.cli("sprint", "6", "--review").out
        self.assertRegex(text, r"(?m)^Committed\*\s+3\s+21\s")
        self.assertRegex(text, r"(?m)^Added\*\s+2\s+8\s")
        self.assertRegex(text, r"(?m)^Removed\*\s+0\s+0\s")
        self.assertRegex(text, r"(?m)^Completed\s+2\s+13\s")
        self.assertRegex(text, r"(?m)^Carried over\s+3\s+16\s")
        self.assertEqual(text.count("* The history of this sprint is incomplete (see the notes)"), 1)

    def test_a_history_that_jira_cut_is_named(self):
        self.routes.changelog_cap = 1
        notes = self.review()["notes"]
        self.assertTrue(any("only part of the history" in note for note in notes))

    def test_a_key_whose_number_has_thousands_of_digits_is_in_the_review_after_the_others(self):
        huge = "PROJ-" + "9" * 5000  # `int()` refuses more than 4300 digits
        add_issue(self.jira, huge, "To Do", 1.0, created="2025-08-24T09:00:00.000+0200")
        self.routes.set_sprints_of(huge, [6])
        history_entry(self.routes, huge, "2025-08-25T10:00:00.000+0200", "Sprint", "", "6")
        review = self.review()
        self.assertEqual(review["committed"]["keys"], ["PROJ-21", "PROJ-22", "PROJ-24", "PROJ-25", huge])
        self.assertEqual(review["carriedOver"]["keys"], ["PROJ-22", "PROJ-25", "PROJ-27", huge])
        self.assertEqual((review["committed"]["issues"], review["historyComplete"]), (5, True))

    def test_an_active_sprint_is_measured_to_now(self):
        review = self.json("sprint", "7", "--review")["review"]
        self.assertFalse(review["ended"])
        self.assertEqual((review["committed"]["keys"], review["carriedOver"]["keys"], review["completed"]["keys"]), (["PROJ-22"], ["PROJ-22"], []))
        self.assertTrue(any("still running" in note for note in review["notes"]))

    def test_a_future_sprint_cannot_be_reviewed(self):
        result = self.cli("sprint", "8", "--review")
        self.assertEqual(result.code, 1)
        self.assertIn("not started", result.err)

    def test_an_issue_that_was_carried_over_is_in_both_sprints(self):
        self.assertEqual(self.json("sprint", "7", "--issues")["issues"][0]["key"], "PROJ-22")
        self.assertIn("PROJ-22", [issue["key"] for issue in self.json("sprint", "6", "--issues")["issues"]])

    def test_no_people_in_anything_that_is_printed(self):
        for argv in (("sprint", "6", "--review", "--issues"), ("sprints", "--project", "PROJ"), ("velocity",), ("backlog",), ("epics",)):
            for env in ({}, {"JIRA_HIDE_USERS": "true"}):
                result = self.cli(*argv, "--json", env=env)
                self.assertEqual(result.code, 0, result.err)
                for person in PEOPLE:
                    self.assertNotIn(person, result.out + result.err)


class VelocityTest(AgileFlow):
    def setUp(self):
        super().setUp()
        seed_review(self.jira)

    def test_the_newest_closed_sprint_first_and_the_average(self):
        data = self.json("velocity", "--project", "PROJ")
        self.assertEqual([row["id"] for row in data["sprints"]], [6, 5])
        self.assertEqual((data["sprints"][0]["committedPoints"], data["sprints"][0]["completedPoints"]), (23.0, 13.0))
        self.assertEqual((data["sprints"][1]["committedPoints"], data["sprints"][1]["completedPoints"]), (0.0, 0.0))
        self.assertEqual((data["averageCompleted"], data["count"]), (6.5, 2))
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/status")), 1)
        self.assertEqual(([row["historyComplete"] for row in data["sprints"]], data["leftOut"]), ([True, True], []))
        self.assertNotIn("*", self.cli("velocity").out)

    def test_last_and_text(self):
        data = self.json("velocity", "--board", "1200", "--last", "1")
        self.assertEqual((data["count"], data["averageCompleted"]), (1, 13.0))
        self.assertIn("Average completed: 13 points over 1 sprint(s)", self.cli("velocity", "--last", "1").out)

    def test_no_closed_sprints(self):
        add_board(self.jira, 1300, "Other board", "scrum", ("DEMO",))
        data = self.json("velocity", "--board", "1300")
        self.assertEqual((data["count"], data["averageCompleted"], data["sprints"], data["leftOut"]), (0, None, [], []))
        self.assertEqual(self.cli("velocity", "--board", "1300").out, "No closed sprints.\n")

    def test_jira_that_refuses_was_marks_the_committed_numbers_in_the_table_and_the_json(self):
        self.routes.history_operator = False
        data = self.json("velocity")
        self.assertEqual([(row["id"], row["historyComplete"], row["committedIssues"], row["committedPoints"]) for row in data["sprints"]], [(6, False, 3, 21.0), (5, False, 0, 0.0)])
        result = self.cli("velocity")
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertRegex(lines[1], r"^6\s+Sprint 6\s+2025-09-15\s+21\*\s+13\s+3\*\s+2$")
        self.assertRegex(lines[2], r"^5\s+Sprint 5\s+2025-08-18\s+0\*\s+0\s+0\*\s+0$")
        self.assertEqual(lines[-1], "* The history of the marked sprints is incomplete (see the warnings): their committed numbers may be too low or wrong.")
        self.assertEqual(result.out.count("incomplete"), 1)
        self.assertIn("WARNING: Sprint 6: Jira refused the search for the issues that left the sprint", result.err)

    def test_closed_sprints_without_a_start_date_are_left_out_and_not_called_no_closed_sprints(self):
        for number in (5, 6):
            self.routes.sprints[number].pop("startDate")
        result = self.cli("velocity")
        self.assertEqual((result.code, result.out), (0, "2 closed sprints could not be reviewed (see the warnings).\n"))
        self.assertEqual(result.err.count("has no start date"), 2)
        data = self.json("velocity")
        self.assertEqual((data["count"], data["sprints"], data["leftOut"]), (0, [], [6, 5]))


class BacklogAndEpicsTest(AgileFlow):
    def test_the_backlog_is_paged_and_has_no_people(self):
        self.jira.page_cap = 2
        data = self.json("backlog", "--project", "PROJ")
        self.assertEqual([issue["key"] for issue in data["issues"]], ["PROJ-1", "PROJ-2", "PROJ-4"])  # not done, no epic, the rank order
        self.assertEqual((data["total"], data["count"], data["boardId"]), (3, 3, 1200))
        self.assertEqual(data["issues"][0], {"key": "PROJ-1", "issueType": "Story", "status": {"name": "To Do", "category": "new"}, "storyPoints": 5.0, "epicKey": "PROJ-9", "summary": "Export the report: Ünïcode & more"})
        self.assertIn("fields", self.jira.sent("GET", f"{AGILE}/board/1200/backlog")[0].query)
        self.assertEqual(self.json("backlog", "--limit", "1")["count"], 1)

    def test_epics_and_the_done_ones(self):
        self.jira.issues["PROJ-8"]["fields"]["status"] = STATUSES["Done"]
        self.assertEqual([epic["key"] for epic in self.json("epics")["epics"]], ["PROJ-9"])
        data = self.json("epics", "--done", "--board", "1200")
        self.assertEqual(data["epics"], [
            {"key": "PROJ-8", "name": "Epic eight", "summary": "Epic eight", "done": True},
            {"key": "PROJ-9", "name": "Epic nine", "summary": "Epic nine", "done": False},
        ])
        self.assertEqual(self.jira.sent("GET", f"{AGILE}/board/1200/epic")[0].query["done"], ["false"])
        self.assertNotIn("done", self.jira.sent("GET", f"{AGILE}/board/1200/epic")[-1].query)


class BacklogWithABigKeyTest(AgileFlow):
    def test_a_key_whose_number_has_thousands_of_digits_is_listed_like_any_other(self):
        huge = "PROJ-" + "9" * 5000  # `int()` refuses more than 4300 digits
        add_issue(self.jira, huge)
        data = self.json("backlog", "--project", "PROJ", "--limit", "100")
        keys = [issue["key"] for issue in data["issues"]]
        self.assertIn(huge, keys)
        self.assertEqual((data["total"], data["count"]), (len(keys), len(keys)))
        self.assertEqual(self.cli("backlog", "--project", "PROJ", "--select", "key", "--format", "tsv").code, 0)


class SprintAddTest(AgileFlow):
    def setUp(self):
        super().setUp()
        seed_review(self.jira)

    def test_a_dry_run_reads_and_sends_nothing(self):
        data = self.json("sprint-add", "PROJ-24", "PROJ-22", "--sprint", "Sprint 8", "--board", "1200", "--dry-run")
        self.assertEqual((data["dryRun"], data["payload"], data["requests"]), (True, {"issues": ["PROJ-24", "PROJ-22"]}, 1))
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/sprint"), [])
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/PROJ-24")), 1)  # the issues were read first
        self.assertEqual(data["notes"], ["PROJ-22 leaves Sprint 7 (active)."])
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"]), (False, None, None))  # nothing was sent, so nothing was checked
        self.assertEqual(self.jira.sent("GET", f"{AGILE}/sprint/8/issue"), [])

    def test_the_real_run_is_read_back_and_the_issue_shows_it(self):
        data = self.json("sprint-add", "PROJ-24", "PROJ-4", "--sprint", "8")
        self.assertEqual((data["dryRun"], data["confirmed"], data["missing"]), (False, ["PROJ-4", "PROJ-24"], []))  # in the order of the rank
        self.assertIs(data["readBack"], True)
        self.assertEqual([item.body for item in self.jira.sent("POST", f"{AGILE}/sprint/8/issue")], [{"issues": ["PROJ-24", "PROJ-4"]}])
        sprints = self.json("issue", "PROJ-4")["sprints"]
        self.assertEqual([(item["id"], item["state"]) for item in sprints], [(8, "FUTURE")])
        self.assertEqual([issue["key"] for issue in self.json("sprint", "8", "--issues")["issues"]], ["PROJ-4", "PROJ-24"])
        last = self.routes.histories["PROJ-24"][-1]["items"][0]  # a history entry, so that a review sees the move
        self.assertEqual((last["field"], last["from"], last["to"]), ("Sprint", "", "8"))

    def test_a_read_back_that_fails_is_flagged_and_checks_nothing_but_the_move_was_made(self):
        self.jira.fail_next(f"{AGILE}/sprint/8/issue", 503, method="GET")  # the only GET of that path is the read-back
        result = self.cli("sprint-add", "PROJ-24", "--sprint", "8", "--json", env={"JIRA_RETRY": "0"})
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"], data["dryRun"]), (False, None, None, False))
        self.assertEqual((data["keys"], data["requests"]), (["PROJ-24"], 1))
        self.assertEqual(len(data["notes"]), 1)
        self.assertIn("The issues were moved, but they could not be read back", data["notes"][0])
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/sprint/8/issue")), 1)
        self.assertEqual(self.routes.sprint_ids_of("PROJ-24"), [8])  # it is in the sprint
        self.jira.fail_next(f"{AGILE}/sprint/8/issue", 503, method="GET")
        text = self.cli("sprint-add", "PROJ-4", "--sprint", "8", env={"JIRA_RETRY": "0"})
        self.assertEqual(text.code, 0, text.err)
        self.assertIn("Note: The issues were moved, but they could not be read back", text.out)
        self.assertNotIn("Read back:", text.out)
        self.assertNotIn("WARNING", text.err)  # nothing is said to be missing: nothing was looked at

    def test_nothing_to_send_reads_nothing_back(self):
        data = self.json("sprint-add", "PROJ-22", "--sprint", "7")  # PROJ-22 is in sprint 7 already
        self.assertEqual((data["keys"], data["skipped"], data["requests"]), ([], ["PROJ-22"], 0))
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"]), (False, None, None))
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/sprint/7/issue"), [])
        self.assertEqual(self.jira.sent("GET", f"{AGILE}/sprint/7/issue"), [])

    def test_an_issue_that_was_in_another_open_sprint_leaves_it(self):
        self.json("sprint-add", "PROJ-22", "--sprint", "8")
        self.assertEqual(self.routes.sprint_ids_of("PROJ-22"), [6, 8])  # the closed sprint stays in the history of the issue
        entry = self.routes.histories["PROJ-22"][-1]["items"][0]
        self.assertEqual((entry["from"], entry["to"]), ("6, 7", "6, 8"))

    def test_more_than_fifty_issues_go_in_batches_and_the_mock_refuses_more_in_one_call(self):
        self.jira.page_cap = 50
        for number in range(300, 420):
            add_issue(self.jira, f"PROJ-{number}")
        keys = [f"PROJ-{number}" for number in range(300, 420)]
        data = self.json("sprint-add", *keys, "--sprint", "8")
        self.assertEqual((data["requests"], len(data["confirmed"]), data["missing"]), (3, 120, []))
        self.assertEqual([len(item.body["issues"]) for item in self.jira.sent("POST", f"{AGILE}/sprint/8/issue")], [50, 50, 20])
        body = json.dumps({"issues": [f"PROJ-{number}" for number in range(300, 351)]})
        refused = self.cli("api", "POST", f"{AGILE}/sprint/8/issue", "--write", "--data", body)
        self.assertEqual(refused.code, 6)
        self.assertIn("maximum number of issues", refused.err)

    def add_many(self):
        for number in range(300, 420):
            add_issue(self.jira, f"PROJ-{number}")
        return [f"PROJ-{number}" for number in range(300, 420)]

    def test_a_later_batch_that_fails_says_what_was_already_moved(self):
        keys = self.add_many()
        self.fail_nth(f"{AGILE}/sprint/8/issue", 2, 400, body={"errorMessages": ["Too busy."], "errors": {}})
        result = self.cli("sprint-add", *keys, "--sprint", "8", "--json")
        self.assertEqual(result.code, 6)
        self.assertIn("Too busy.", result.error()["message"])
        self.assertIn("50 of 120 issues were already changed", result.error()["message"])
        self.assertEqual(result.error()["details"]["changed"], keys[:50])
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/sprint/8/issue")), 2)

    def test_a_first_batch_that_fails_is_not_wrapped_because_nothing_was_moved(self):
        keys = self.add_many()
        self.jira.fail_next(f"{AGILE}/sprint/8/issue", 400, method="POST", body={"errorMessages": ["Too busy."], "errors": {}})
        result = self.cli("sprint-add", *keys, "--sprint", "8", "--json")
        self.assertEqual(result.code, 6)
        self.assertIn("Too busy.", result.error()["message"])
        self.assertNotIn("already changed", result.error()["message"])
        self.assertNotIn("changed", result.error().get("details") or {})
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/sprint/8/issue")), 1)  # and the other batches were not tried

    def test_what_is_refused_before_anything_is_sent(self):
        for argv, code in (
            (("sprint-add", "PROJ-24", "PROJ-999", "--sprint", "8"), 4),
            (("sprint-add", "PROJ-24", "--sprint", "999"), 4),
            (("sprint-add", "PROJ-24", "--sprint", "6"), 1),
            (("sprint-add", "PROJ-24", "--sprint", "7", "--if-state", "future"), 3),
            (("sprint-add", "PROJ-24", "--sprint", "8", "--if-unchanged-since", "2020-01-01T00:00:00Z"), 3),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, code)
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/sprint"), [])

    def test_a_refusal_by_jira_is_shown_and_a_403_names_the_permission(self):
        self.jira.fail_next(f"{AGILE}/sprint/8/issue", 400, method="POST", body={"errorMessages": ["Issues can only be moved to open or active sprints."], "errors": {}})
        refused = self.cli("sprint-add", "PROJ-24", "--sprint", "8")
        self.assertEqual(refused.code, 6)
        self.assertIn("Issues can only be moved to open or active sprints.", refused.err)
        self.jira.fail_next(f"{AGILE}/sprint/8/issue", 403, method="POST", body={"errorMessages": ["No permission"], "errors": {}})
        forbidden = self.cli("sprint-add", "PROJ-24", "--sprint", "8", "--json")
        self.assertEqual((forbidden.code, forbidden.error()["code"]), (5, "AUTH"))
        self.assertIn("Schedule Issues", forbidden.error()["hint"])

    def test_the_review_sees_what_sprint_add_did(self):
        self.json("sprint-update", "8", "--state", "active", "--start", "2020-01-01", "--end", "2020-01-15", "--if-state", "future")
        self.json("sprint-add", "PROJ-24", "--sprint", "8")
        review = self.json("sprint", "8", "--review")["review"]
        self.assertEqual(review["added"]["keys"], ["PROJ-24"])
        self.assertEqual(review["committed"]["keys"], [])


class MovedIssueTest(AgileFlow):
    """An old key of an issue that moved to another project: Jira answers it with the issue under its new key."""

    def setUp(self):
        super().setUp()
        seed_review(self.jira)
        self.routes.moved["OLD-24"] = "PROJ-24"

    def test_sprint_add_sends_and_checks_the_new_key_and_does_not_warn(self):
        result = self.cli("sprint-add", "OLD-24", "--sprint", "8", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["keys"], data["confirmed"], data["missing"], data["notes"]), (["PROJ-24"], ["PROJ-24"], [], ["OLD-24 is now PROJ-24."]))
        self.assertEqual([item.body for item in self.jira.sent("POST", f"{AGILE}/sprint/8/issue")], [{"issues": ["PROJ-24"]}])
        self.assertEqual(self.jira.sent("GET", f"{AGILE}/sprint/8/issue")[-1].query["jql"], ["key in (PROJ-24)"])
        self.assertEqual(result.err, "")  # no "accepted but not found" warning
        text = self.cli("sprint-add", "OLD-24", "--sprint", "8")
        self.assertEqual(text.code, 0, text.err)
        self.assertIn("Already there, not sent: PROJ-24", text.out)  # the second run finds it where it was put

    def test_epic_add_sends_and_checks_the_new_key_and_does_not_warn(self):
        result = self.cli("epic-add", "OLD-24", "--epic", "PROJ-8", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["keys"], data["confirmed"], data["missing"], data["notes"]), (["PROJ-24"], ["PROJ-24"], [], ["OLD-24 is now PROJ-24."]))
        self.assertEqual([item.body for item in self.jira.sent("POST", f"{AGILE}/epic/PROJ-8/issue")], [{"issues": ["PROJ-24"]}])
        self.assertEqual(result.err, "")

    def test_rank_uses_the_new_key_for_the_issue_and_for_the_anchor(self):
        data = self.json("rank", "OLD-24", "--before", "PROJ-21")
        self.assertEqual((data["keys"], data["anchor"], data["order"], data["confirmed"]), (["PROJ-24"], "PROJ-21", ["PROJ-24", "PROJ-21"], True))
        self.assertEqual(data["notes"], ["OLD-24 is now PROJ-24."])
        again = self.json("rank", "PROJ-25", "--after", "OLD-24")
        self.assertEqual((again["anchor"], again["order"], again["confirmed"]), ("PROJ-24", ["PROJ-24", "PROJ-25"], True))
        self.assertEqual(
            [item.body for item in self.jira.sent("PUT", f"{AGILE}/issue/rank")],
            [{"issues": ["PROJ-24"], "rankBeforeIssue": "PROJ-21"}, {"issues": ["PROJ-25"], "rankAfterIssue": "PROJ-24"}],
        )

    def test_the_old_key_is_read_under_its_own_path(self):
        self.json("sprint-add", "OLD-24", "--sprint", "8", "--dry-run")
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/OLD-24")), 1)
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/issue/PROJ-24"), [])


class RankTest(AgileFlow):
    def test_a_207_entry_with_a_status_that_is_not_200_is_a_refusal_even_without_errors(self):
        entries = [{"issueKey": "PROJ-3", "issueId": 3, "status": 400}, {"issueKey": "PROJ-4", "issueId": 4, "status": 200}]
        self.jira.fail_next(f"{AGILE}/issue/rank", 207, method="PUT", body={"entries": entries})
        result = self.cli("rank", "PROJ-3", "PROJ-4", "--before", "PROJ-1", "--json")
        self.assertEqual((result.code, result.error()["code"]), (6, "REJECTED"))
        self.assertIn("PROJ-3: not ranked", result.error()["message"])
        self.assertNotIn("PROJ-4", result.error()["message"])  # the entry with 200 is no failure
        self.assertEqual(result.error()["details"]["entries"], entries[:1])

    def test_a_dry_run_and_a_real_run_with_the_order_read_back(self):
        dry = self.json("rank", "PROJ-4", "PROJ-3", "--before", "PROJ-1", "--dry-run")
        self.assertEqual((dry["dryRun"], dry["payload"]), (True, {"issues": ["PROJ-4", "PROJ-3"], "rankBeforeIssue": "PROJ-1"}))
        self.assertEqual(self.jira.sent("PUT", f"{AGILE}/issue/rank"), [])
        self.assertEqual((dry["readBack"], dry["order"], dry["confirmed"]), (False, None, None))
        self.assertEqual(self.jira.sent("GET", "/rest/api/2/search"), [])  # no order was read
        data = self.json("rank", "PROJ-4", "PROJ-3", "--before", "PROJ-1")
        self.assertEqual((data["order"], data["confirmed"], data["readBack"]), (["PROJ-4", "PROJ-3", "PROJ-1"], True, True))
        self.assertEqual([item.body for item in self.jira.sent("PUT", f"{AGILE}/issue/rank")], [{"issues": ["PROJ-4", "PROJ-3"], "rankBeforeIssue": "PROJ-1"}])
        self.assertEqual(self.json("rank", "PROJ-1", "--after", "PROJ-3")["order"], ["PROJ-3", "PROJ-1"])
        self.assertEqual(self.json("backlog")["issues"][0]["key"], "PROJ-4")  # the backlog follows the rank

    def test_a_read_back_that_fails_is_flagged_and_checks_nothing_but_the_rank_was_made(self):
        self.jira.fail_next("/rest/api/2/search", 503, method="GET")  # the only search of a rank is the read of the order
        result = self.cli("rank", "PROJ-4", "PROJ-3", "--before", "PROJ-1", "--json", env={"JIRA_RETRY": "0"})
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["readBack"], data["order"], data["confirmed"], data["dryRun"]), (False, None, None, False))
        self.assertEqual(len(data["notes"]), 1)
        self.assertIn("The issues were ranked, but their order could not be read back", data["notes"][0])
        self.assertEqual(len(self.jira.sent("PUT", f"{AGILE}/issue/rank")), 1)
        self.assertEqual(self.routes.ranked()[:3], ["PROJ-4", "PROJ-3", "PROJ-1"])  # the rank was made
        self.jira.fail_next("/rest/api/2/search", 503, method="GET")
        text = self.cli("rank", "PROJ-2", "--after", "PROJ-4", env={"JIRA_RETRY": "0"})
        self.assertEqual(text.code, 0, text.err)
        self.assertIn("Ranked PROJ-2 after PROJ-4.", text.out)
        self.assertIn("Note: The issues were ranked, but their order could not be read back", text.out)
        self.assertNotIn("Read back", text.out)

    def test_jira_that_could_not_rank_every_issue(self):
        self.routes.unrankable = {"PROJ-4": ["The issue is not on a board."]}
        result = self.cli("rank", "PROJ-4", "PROJ-3", "--before", "PROJ-1")
        self.assertEqual(result.code, 6)
        self.assertIn("PROJ-4: The issue is not on a board.", result.err)
        self.assertIn("The other issues were ranked", result.err)
        self.assertEqual(self.routes.ranked()[:3], ["PROJ-3", "PROJ-1", "PROJ-2"])

    def test_what_is_refused(self):
        for argv, code in (
            (("rank", "PROJ-4"), 2),
            (("rank", "PROJ-4", "--before", "PROJ-4"), 1),
            (("rank", "PROJ-4", "--before", "PROJ-999"), 4),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, code)
        self.assertEqual(self.jira.sent("PUT", f"{AGILE}/issue/rank"), [])

    def test_a_400_of_jira(self):
        self.jira.fail_next(f"{AGILE}/issue/rank", 400, method="PUT", body={"errorMessages": ["Issues are on different boards."], "errors": {}})
        result = self.cli("rank", "PROJ-4", "--before", "PROJ-1")
        self.assertEqual(result.code, 6)
        self.assertIn("different boards", result.err)


class EpicAddTest(AgileFlow):
    def test_into_an_epic_and_out_of_it(self):
        dry = self.json("epic-add", "PROJ-4", "--epic", "PROJ-8", "--dry-run")
        self.assertEqual((dry["epic"], dry["epicName"], dry["payload"], dry["dryRun"]), ("PROJ-8", "Epic eight", {"issues": ["PROJ-4"]}, True))
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/epic"), [])
        self.assertEqual((dry["readBack"], dry["confirmed"], dry["missing"]), (False, None, None))
        data = self.json("epic-add", "PROJ-4", "--epic", "PROJ-8")
        self.assertEqual((data["confirmed"], data["missing"], data["readBack"]), (["PROJ-4"], [], True))
        self.assertEqual(self.json("issue", "PROJ-4")["epicKey"], "PROJ-8")
        self.assertEqual([item.body for item in self.jira.sent("POST", f"{AGILE}/epic/PROJ-8/issue")], [{"issues": ["PROJ-4"]}])
        out = self.json("epic-add", "PROJ-4", "PROJ-1", "--epic", "none")
        self.assertEqual((out["epic"], out["keys"], out["confirmed"]), ("none", ["PROJ-4", "PROJ-1"], ["PROJ-1", "PROJ-4"]))
        self.assertEqual((self.json("issue", "PROJ-4")["epicKey"], self.json("issue", "PROJ-1")["epicKey"]), ("", ""))
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/epic/none/issue")), 1)

    def test_what_is_refused(self):
        for argv, code in (
            (("epic-add", "PROJ-4", "--epic", "PROJ-77"), 4),
            (("epic-add", "PROJ-8", "--epic", "PROJ-9"), 1),
            (("epic-add", "PROJ-4"), 2),
            (("epic-add", "PROJ-4", "--epic", "PROJ-8", "--if-unchanged-since", "2020-01-01T00:00:00Z"), 3),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, code)
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/epic"), [])

    def test_a_read_back_that_fails_is_flagged_and_checks_nothing_but_the_move_was_made(self):
        self.jira.fail_next(f"{AGILE}/epic/PROJ-8/issue", 503, method="GET")  # the only GET of that path is the read-back
        result = self.cli("epic-add", "PROJ-4", "--epic", "PROJ-8", "--json", env={"JIRA_RETRY": "0"})
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"], data["dryRun"]), (False, None, None, False))
        self.assertEqual((data["keys"], data["requests"]), (["PROJ-4"], 1))
        self.assertEqual(len(data["notes"]), 1)
        self.assertIn("The issues were moved, but they could not be read back", data["notes"][0])
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/epic/PROJ-8/issue")), 1)
        self.assertEqual(self.json("issue", "PROJ-4")["epicKey"], "PROJ-8")  # the move was made
        self.jira.fail_next(f"{AGILE}/epic/none/issue", 503, method="GET")
        text = self.cli("epic-add", "PROJ-4", "--epic", "none", env={"JIRA_RETRY": "0"})
        self.assertEqual(text.code, 0, text.err)
        self.assertIn("Note: The issues were moved, but they could not be read back", text.out)
        self.assertNotIn("WARNING", text.err)

    def test_nothing_to_send_reads_nothing_back(self):
        data = self.json("epic-add", "PROJ-1", "--epic", "PROJ-9")  # PROJ-1 is in PROJ-9 already
        self.assertEqual((data["keys"], data["skipped"], data["requests"]), ([], ["PROJ-1"], 0))
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"]), (False, None, None))
        self.assertEqual((self.jira.sent("POST", f"{AGILE}/epic"), self.jira.sent("GET", f"{AGILE}/epic/PROJ-9/issue")), ([], []))

    def test_a_403_names_the_permission(self):
        self.jira.fail_next(f"{AGILE}/epic/PROJ-8/issue", 403, method="POST", body={"errorMessages": ["No permission"], "errors": {}})
        result = self.cli("epic-add", "PROJ-4", "--epic", "PROJ-8")
        self.assertEqual(result.code, 5)
        self.assertIn("Edit Issues", result.err)


class SprintWritesTest(AgileFlow):
    def setUp(self):
        super().setUp()
        seed_review(self.jira)

    def test_create_start_and_complete(self):
        dry = self.json("sprint-create", "Sprint 9", "--board", "1200", "--goal", "Go", "--start", "2026-11-02", "--end", "2026-11-16", "--dry-run")
        self.assertEqual((dry["dryRun"], dry["sprint"]), (True, None))
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/sprint"), [])
        created = self.json("sprint-create", "Sprint 9", "--board", "1200", "--goal", "Go", "--start", "2026-11-02", "--end", "2026-11-16")
        sprint = created["sprint"]
        self.assertEqual((sprint["name"], sprint["state"], sprint["goal"], sprint["boardId"]), ("Sprint 9", "future", "Go", 1200))
        self.assertEqual((sprint["startDate"], sprint["endDate"]), ("2026-11-02T00:00:00.000+00:00", "2026-11-16T00:00:00.000+00:00"))
        self.assertEqual(self.jira.sent("GET", f"{AGILE}/sprint/{sprint['id']}")[-1].path, f"{AGILE}/sprint/{sprint['id']}")  # read back
        started = self.json("sprint-update", str(sprint["id"]), "--state", "active", "--if-state", "future")
        self.assertEqual((started["before"]["state"], started["sprint"]["state"], started["payload"]), ("future", "active", {"state": "active"}))
        closed = self.json("sprint-update", "Sprint 9", "--board", "1200", "--state", "closed", "--if-state", "active")
        self.assertEqual(closed["sprint"]["state"], "closed")
        self.assertTrue(closed["sprint"]["completeDate"])  # Jira sets it
        self.assertEqual(self.cli("sprint-update", str(sprint["id"]), "--name", "Late").code, 1)  # a closed sprint cannot be changed

    def test_a_create_whose_read_back_fails_is_a_success_and_is_not_to_be_repeated(self):
        self.jira.fail_next(f"{AGILE}/sprint/101", 503, method="GET")
        result = self.cli("sprint-create", "Sprint 9", "--board", "1200", "--json", env={"JIRA_RETRY": "0"})
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["readBack"], data["dryRun"], data["sprint"]["id"], data["sprint"]["name"], data["sprint"]["state"]), (False, False, 101, "Sprint 9", "future"))
        self.assertEqual(len(data["notes"]), 1)
        self.assertIn("The sprint was created (id 101), but it could not be read back", data["notes"][0])
        self.assertIn("Do not send the command again", data["notes"][0])
        self.assertNotRegex(data["notes"][0], r"(?i)try again|retry")
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/sprint")), 1)
        self.assertEqual(self.routes.sprints[101]["name"], "Sprint 9")  # it exists in Jira
        self.jira.fail_next(f"{AGILE}/sprint/102", 503, method="GET")
        text = self.cli("sprint-create", "Sprint 10", "--board", "1200", env={"JIRA_RETRY": "0"})
        self.assertEqual(text.code, 0, text.err)
        self.assertIn("Created the sprint; it could not be read back, so this is what Jira answered to the request:", text.out)
        self.assertIn("jira sprint 102", text.out)

    def test_an_update_whose_read_back_fails_is_a_success_and_is_not_to_be_repeated(self):
        self.fail_nth(f"{AGILE}/sprint/8", 2, 503, method="GET", onward=True)  # the first read finds the sprint, the read-back fails
        result = self.cli("sprint-update", "8", "--name", "Sprint 8b", "--json", env={"JIRA_RETRY": "0"})
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["readBack"], data["sprint"]["name"], data["before"]["name"]), (False, "Sprint 8b", "Sprint 8"))
        self.assertIn("The sprint was changed (id 8), but it could not be read back", data["notes"][0])
        self.assertNotRegex(data["notes"][0], r"(?i)try again|retry")
        self.assertEqual(len(self.jira.sent("POST", f"{AGILE}/sprint/8")), 1)
        self.assertEqual(self.routes.sprints[8]["name"], "Sprint 8b")

    def test_update_name_goal_and_dates_is_partial(self):
        data = self.json("sprint-update", "8", "--name", "  Sprint   8b ", "--goal", "A goal")
        self.assertEqual((data["payload"], data["sprint"]["name"], data["sprint"]["goal"]), ({"name": "Sprint 8b", "goal": "A goal"}, "Sprint 8b", "A goal"))
        self.assertEqual(self.json("sprint-update", "8", "--goal", "")["sprint"]["goal"], "")
        dated = self.json("sprint-update", "8", "--start", "2026-11-02T09:00:00+01:00", "--end", "2026-11-16")
        self.assertEqual(dated["payload"]["startDate"], "2026-11-02T09:00:00.000+01:00")

    def test_what_is_refused_before_it_is_sent(self):
        for argv, code in (
            (("sprint-create", "Sprint 8", "--board", "1200"), 1),  # a duplicate
            (("sprint-create", "Sprint 9", "--board", "999"), 4),
            (("sprint-update", "8", "--state", "active"), 1),  # no dates
            (("sprint-update", "7", "--state", "closed", "--if-state", "future"), 3),
            (("sprint-update", "8", "--state", "closed"), 1),
            (("sprint-update", "99", "--name", "X"), 4),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv).code, code)
        self.assertEqual(self.jira.sent("POST", f"{AGILE}/sprint"), [])

    def test_the_mock_refuses_what_jira_refuses(self):
        for state in ("future", "closed"):  # a future sprint cannot be completed, no sprint goes back to future
            result = self.cli("api", "POST", f"{AGILE}/sprint/8", "--write", "--data", json.dumps({"state": state}))
            self.assertEqual(result.code, 6, state)
        self.assertEqual(self.cli("api", "POST", f"{AGILE}/sprint/8", "--write", "--data", json.dumps({"state": "active"})).code, 6)  # no dates
        self.assertEqual(self.cli("api", "POST", f"{AGILE}/sprint/6", "--write", "--data", json.dumps({"name": "X"})).code, 6)  # closed
        self.assertEqual(self.cli("api", "POST", f"{AGILE}/sprint/8", "--write", "--data", json.dumps({"completeDate": "2026-01-01T00:00:00.000+00:00"})).code, 6)
        self.assertEqual(self.cli("api", "POST", f"{AGILE}/sprint", "--write", "--data", json.dumps({"name": "X"})).code, 6)  # no board id
        self.assertEqual(self.cli("api", "POST", f"{AGILE}/sprint", "--write", "--data", json.dumps({"name": "X", "originBoardId": 999})).code, 4)

    def test_a_400_of_jira_is_shown(self):
        self.jira.fail_next(f"{AGILE}/sprint/8", 400, method="POST", body={"errorMessages": ["The sprint has offending issues."], "errors": {}})
        result = self.cli("sprint-update", "8", "--name", "X")
        self.assertEqual(result.code, 6)
        self.assertIn("offending issues", result.err)

    def test_a_403_names_the_permission(self):
        self.jira.fail_next(f"{AGILE}/sprint", 403, method="POST", body={"errorMessages": ["No permission"], "errors": {}})
        result = self.cli("sprint-create", "Sprint 9", "--board", "1200")
        self.assertEqual(result.code, 5)
        self.assertIn("Manage Sprints", result.err)


class GatewayTest(GatewayTestCase):
    """The gateway on its own: paging, shapes and the refusals that need no command line."""

    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))
        self.routes = routes_of(self.jira)

    def test_boards_and_sprints_follow_the_paging_of_the_server(self):
        seed_review(self.jira)
        for number in range(1201, 1206):
            add_board(self.jira, number, f"Board {number}")
        self.assertEqual(len(self.gateway.list_boards()), 6)
        self.assertEqual([sprint.id for sprint in self.gateway.list_sprints(1200)], [5, 6, 7, 8])  # Jira's order: closed, active, future
        self.assertEqual([sprint.id for sprint in self.gateway.list_sprints(1200, states=["closed"])], [5, 6])
        sprint = self.gateway.get_sprint(6)
        self.assertEqual((sprint.state, sprint.board_id, sprint.goal, sprint.complete_date), ("closed", 1200, "Ship the export", "2025-09-15T16:00:00.000+02:00"))

    def test_the_sprint_search_follows_the_total_and_maps_the_history(self):
        seed_review(self.jira)
        self.jira.page_cap = 1
        found = self.gateway.search_with_history("sprint = 6 ORDER BY key ASC")
        self.assertEqual([item.key for item in found], ["PROJ-21", "PROJ-22", "PROJ-23", "PROJ-25", "PROJ-26", "PROJ-27"])
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/search")), 6)
        moves = {item.key: item for item in found}
        self.assertEqual([(move.at, sorted(move.before), sorted(move.after)) for move in moves["PROJ-22"].sprint_moves], [("2025-08-28T10:00:00.000+0200", [], [6]), ("2025-09-15T16:05:00.000+0200", [6], [6, 7])])
        self.assertEqual([(move.from_name, move.to_name) for move in moves["PROJ-21"].status_moves], [("In Progress", "Done")])
        self.assertEqual((moves["PROJ-26"].subtask, moves["PROJ-21"].points, moves["PROJ-27"].points), (True, 5.0, None))
        self.assertTrue(all(item.history_complete for item in found))

    def test_a_history_that_jira_cut_is_marked_incomplete(self):
        seed_review(self.jira)
        self.routes.changelog_cap = 0
        found = {item.key: item.history_complete for item in self.gateway.search_with_history("sprint = 6 ORDER BY key ASC")}
        self.assertFalse(found["PROJ-21"])  # it has history entries that were not returned
        self.assertTrue(found["PROJ-26"])  # it has none

    def test_jira_that_refuses_the_history_operator_is_a_rejection(self):
        seed_review(self.jira)
        self.routes.history_operator = False
        with self.assertRaises(RejectedError):
            self.gateway.search_with_history("sprint was 6 AND sprint != 6 ORDER BY key ASC")

    def test_a_sprint_with_too_many_issues_is_refused(self):
        seed_review(self.jira)
        with mock.patch.object(rest_agile, "MAX_REVIEWED_ISSUES", 3):
            with self.assertRaises(ValidationError) as caught:
                self.gateway.search_with_history("sprint = 6 ORDER BY key ASC")
        self.assertIn("reads at most 3", caught.exception.message)

    def test_a_sprint_of_exactly_the_most_issues_that_a_review_reads_is_read_and_one_more_is_refused(self):
        for key in list(self.jira.issues):
            self.routes.set_sprints_of(key, [])
        add_sprint(self.jira, 6, "Sprint 6", "closed", start="2025-09-01T08:00:00.000+02:00", complete="2025-09-15T16:00:00.000+02:00")
        self.jira.page_cap = 50
        for number in range(1, 2001):
            add_issue(self.jira, f"BULK-{number}")
            self.routes.set_sprints_of(f"BULK-{number}", [6])
        found = self.gateway.search_with_history("sprint = 6 ORDER BY key ASC")
        self.assertEqual((len(found), found[0].key, found[-1].key), (2000, "BULK-1", "BULK-2000"))
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/search")), 40)
        add_issue(self.jira, "BULK-2001")
        self.routes.set_sprints_of("BULK-2001", [6])
        with self.assertRaises(ValidationError) as caught:
            self.gateway.search_with_history("sprint = 6 ORDER BY key ASC")
        self.assertIn("The sprint has 2001 issues; a review reads at most 2000.", caught.exception.message)

    def test_a_207_entry_with_a_status_that_is_not_200_and_no_errors_is_a_refusal_and_one_with_200_is_not(self):
        self.jira.fail_next(f"{AGILE}/issue/rank", 207, method="PUT", body={"entries": [{"issueKey": "PROJ-3", "issueId": 3, "status": 400}]})
        with self.assertRaises(RejectedError) as caught:
            self.gateway.rank_issues({"issues": ["PROJ-3"], "rankBeforeIssue": "PROJ-1"})
        self.assertIn("PROJ-3: not ranked", caught.exception.message)
        self.jira.fail_next(f"{AGILE}/issue/rank", 207, method="PUT", body={"entries": [{"issueKey": "PROJ-3", "issueId": 3, "status": 200, "errors": []}]})
        self.gateway.rank_issues({"issues": ["PROJ-3"], "rankBeforeIssue": "PROJ-1"})  # every entry went well: no error
        self.jira.fail_next(f"{AGILE}/issue/rank", 207, method="PUT", body={"entries": [{"issueKey": "PROJ-3", "issueId": 3, "errors": ["No."]}]})
        with self.assertRaises(RejectedError) as caught:
            self.gateway.rank_issues({"issues": ["PROJ-3"], "rankBeforeIssue": "PROJ-1"})
        self.assertIn("PROJ-3: No.", caught.exception.message)

    def test_unknown_things_are_not_found(self):
        for call in (lambda: self.gateway.get_board(999), lambda: self.gateway.get_sprint(999), lambda: self.gateway.get_epic("PROJ-77"), lambda: self.gateway.list_sprints(999)):
            with self.assertRaises(NotFoundError):
                call()
        self.assertEqual(self.jira.unknown_endpoints, [])

    def test_the_status_categories(self):
        triples = self.gateway.status_category_list()
        self.assertIn(("5", "Done", "done"), triples)
        self.assertEqual({category for _, _, category in triples} - {"new", "indeterminate", "done"}, set())

    def test_a_status_that_another_area_answers_is_left_to_it(self):
        class OtherArea:
            def handle(self, handler, method, path, query, body):
                if path != "/rest/api/2/status":
                    return False
                handler.reply(200, [{"id": "7", "name": "Parked", "statusCategory": {"key": "new"}}])
                return True

        other = OtherArea()
        saved = list(self.jira.extensions)
        self.addCleanup(self.jira.extensions.__setitem__, slice(None), saved)
        self.jira.extensions[:] = [self.routes, other]  # no other area but this one: it does not depend on how far the others are
        self.assertEqual(self.gateway.status_category_list(), [("7", "Parked", "new")])
        self.jira.extensions[:] = [self.routes]  # and when nobody else knows `GET /status`, the agile routes answer
        self.assertIn(("5", "Done", "done"), self.gateway.status_category_list())

    def test_the_epics_of_a_board_and_the_issues_of_an_epic(self):
        self.assertEqual([epic.key for epic in self.gateway.list_epics(1200)], ["PROJ-8", "PROJ-9"])
        self.assertEqual([issue.key for issue in self.gateway.list_epic_issues("PROJ-9")], ["PROJ-1", "PROJ-2"])
        self.assertEqual([issue.key for issue in self.gateway.list_epic_issues("PROJ-9", keys=["PROJ-2", "PROJ-4"])], ["PROJ-2"])
        self.assertEqual([issue.key for issue in self.gateway.list_epic_issues("none")], ["PROJ-4"])
        self.assertEqual(self.gateway.get_epic("PROJ-9").name, "Epic nine")

    def test_the_backlog_and_the_rank(self):
        issues, total = self.gateway.board_backlog(1200, limit=2)
        self.assertEqual(([issue.key for issue in issues], total), (["PROJ-1", "PROJ-2"], 3))
        self.gateway.rank_issues({"issues": ["PROJ-4"], "rankBeforeIssue": "PROJ-1"})
        self.assertEqual(self.gateway.rank_order(["PROJ-1", "PROJ-2", "PROJ-4"]), ["PROJ-4", "PROJ-1", "PROJ-2"])
