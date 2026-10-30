"""The command line of agile: text, `--json`, `--dry-run`, errors that name the candidates, `--select` and `--format`."""
import dataclasses
import json
import unittest

from jira_dc_cli.domain.agile import Board
from jira_dc_cli.domain.errors import AuthError, UnavailableError
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS
from tests.unit.test_agile_application import seeded

PEOPLE = ("Jane", "jane.doe", "jdoe")
WRITES = ("move_issues_to_sprint", "move_issues_to_epic", "rank_issues", "create_sprint", "update_sprint")


class AgileCli(unittest.TestCase):
    def setUp(self):
        self.gateway = seeded()

    def run_json(self, *argv, **options):
        result = run_cli(*argv, "--json", gateway=self.gateway, **options)
        self.assertEqual(result.code, 0, result.err)
        return result.json()

    def sent(self):
        return [call for name in WRITES for call in self.gateway.called(name)]


class BoardsTest(AgileCli):
    def test_text(self):
        result = run_cli("boards", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out.splitlines()[0].split(), ["ID", "NAME", "TYPE", "PROJECTS"])
        self.assertIn("1200  Demo board  scrum  PROJ", result.out)

    def test_json(self):
        data = self.run_json("boards", "--project", "PROJ")
        self.assertEqual(data, {"total": 1, "count": 1, "boards": [{"id": 1200, "name": "Demo board", "type": "scrum", "projects": ["PROJ"]}]})

    def test_no_boards(self):
        self.assertEqual(run_cli("boards", "--name", "nothing", gateway=self.gateway).out, "No boards found.\n")
        self.assertEqual(self.run_json("boards", "--name", "nothing"), {"total": 0, "count": 0, "boards": []})

    def test_type_and_limit_notice(self):
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("DEMO",))
        self.assertEqual([board["id"] for board in self.run_json("boards", "--type", "kanban")["boards"]], [1300])
        result = run_cli("boards", "--limit", "1", gateway=self.gateway)
        self.assertIn("Showing 1 of 2 boards", result.err)
        self.assertEqual(run_cli("boards", "--type", "agile", gateway=self.gateway).code, 2)

    def test_select_and_format(self):
        self.assertEqual(self.run_json("boards", "--select", "id,name")["boards"], [{"id": 1200, "name": "Demo board"}])
        result = run_cli("boards", "--format", "tsv", gateway=self.gateway)
        self.assertEqual(result.out.splitlines(), ["ID\tNAME\tTYPE\tPROJECTS", "1200\tDemo board\tscrum\tPROJ"])
        self.assertEqual(run_cli("boards", "--json", "--format", "md", gateway=self.gateway).code, 2)


class SprintsTest(AgileCli):
    def test_text(self):
        result = run_cli("sprints", "--project", "PROJ", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "NAME", "STATE", "START", "END", "COMPLETED", "GOAL"])
        self.assertEqual([line.split()[0] for line in lines[1:]], ["7", "8", "6", "5"])
        self.assertIn("2026-10-12  2026-10-26", lines[1])

    def test_json_has_the_dates_the_goal_and_the_board(self):
        data = self.run_json("sprints", "--board", "1200", "--state", "closed")
        self.assertEqual((data["boardId"], data["boardName"], data["total"], data["count"]), (1200, "Demo board", 2, 2))
        self.assertEqual(
            data["sprints"][0],
            {
                "id": 6,
                "name": "Sprint 6",
                "state": "closed",
                "startDate": "2026-09-14T08:00:00.000+02:00",
                "endDate": "2026-09-28T08:00:00.000+02:00",
                "completeDate": "2026-09-28T16:00:00.000+02:00",
                "goal": "Ship the export",
                "boardId": 1200,
            },
        )
        self.assertEqual(self.run_json("sprints", "--state", "future")["sprints"][0]["startDate"], "")  # missing text is empty, never null

    def test_select_and_format(self):
        self.assertEqual(self.run_json("sprints", "--select", "id,state", "--state", "active")["sprints"], [{"id": 7, "state": "active"}])
        text = run_cli("sprints", "--format", "md", "--select", "id,name", gateway=self.gateway).out.splitlines()
        self.assertEqual(text[0], "| id | name |")
        result = run_cli("sprints", "--select", "nope", gateway=self.gateway)
        self.assertEqual(result.code, 2)
        self.assertIn("Valid names", result.err)

    def test_a_kanban_board_has_no_sprints(self):
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("DEMO",))
        result = run_cli("sprints", "--board", "1300", gateway=self.gateway)
        self.assertEqual(result.code, 1)
        self.assertIn("it is a kanban board", result.err)
        self.assertIn("no sprints", result.err)

    def test_several_boards_name_each_with_id_and_name(self):
        self.gateway.boards[1201] = Board(1201, "Second board", "scrum", ("PROJ",))
        result = run_cli("sprints", "--project", "PROJ", "--json", gateway=self.gateway)
        error = result.error()
        self.assertEqual((result.code, error["code"]), (1, "INVALID"))
        self.assertIn("1200 'Demo board'", error["message"])
        self.assertIn("1201 'Second board'", error["message"])
        self.assertIn("--board ID", error["hint"])

    def test_a_wrong_state_is_a_clear_error(self):
        result = run_cli("sprints", "--state", "running", gateway=self.gateway)
        self.assertEqual(result.code, 1)
        self.assertIn("future, active, closed", result.err)


class SprintTest(AgileCli):
    def test_text(self):
        result = run_cli("sprint", "7", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertRegex(result.out, r"Sprint:\s+Sprint 7 \(id 7\)")
        self.assertRegex(result.out, r"State:\s+active")
        self.assertRegex(result.out, r"Goal:\s+Stabilise")
        self.assertNotIn("Issues in the sprint", result.out)

    def test_the_name_needs_no_quotes(self):
        self.assertIn("Sprint 8 (id 8)", run_cli("sprint", "Sprint", "8", gateway=self.gateway).out)
        self.assertIn("Sprint 6 (id 6)", run_cli("sprint", "sprint 6", "--board", "1200", gateway=self.gateway).out)

    def test_json_without_issues_and_review(self):
        data = self.run_json("sprint", "7")
        self.assertEqual(data["sprint"]["name"], "Sprint 7")
        self.assertEqual((data["issues"], data["review"]), (None, None))

    def test_the_issues(self):
        text = run_cli("sprint", "7", "--issues", gateway=self.gateway).out
        self.assertIn("Issues in the sprint: 2", text)
        self.assertIn("PROJ-1  Story  To Do", text)
        data = self.run_json("sprint", "7", "--issues")
        self.assertEqual(data["issues"][0], {"key": "PROJ-1", "issueType": "Story", "status": {"name": "To Do", "category": "new"}, "storyPoints": 5.0, "epicKey": "PROJ-9", "summary": "Export the report"})

    def test_the_review_in_text(self):
        text = run_cli("sprint", "6", "--review", gateway=self.gateway).out
        self.assertIn("Review of the sprint, 2026-09-14T08:00:00+02:00 to 2026-09-28T16:00:00+02:00", text)
        self.assertIn("Committed     3       16      0          PROJ-1, PROJ-2, PROJ-3", text)
        self.assertIn("Carried over  1       8       0          PROJ-3", text)
        self.assertIn("- Derived from the change history", text)
        self.assertNotIn("*", text)  # the history is whole: nothing is marked
        self.assertNotIn("incomplete", text)

    def test_the_review_in_text_marks_what_rests_on_an_incomplete_history(self):
        self.gateway.reject_history_search = True
        result = run_cli("sprint", "6", "--review", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        header = next(index for index, line in enumerate(lines) if line.startswith("GROUP"))
        names = [line.split("  ")[0] for line in lines[header + 1 : header + 6]]
        self.assertEqual(names, ["Committed*", "Completed", "Added*", "Removed*", "Carried over"])
        footnote = "* The history of this sprint is incomplete (see the notes): the marked numbers may be too low or wrong."
        self.assertEqual(lines[header + 6], footnote)  # right under the table
        self.assertLess(lines.index(footnote), lines.index("Notes:"))
        self.assertTrue(any(line.startswith("- Jira refused the search for the issues that left the sprint") and "lower bounds" in line for line in lines))
        self.assertEqual(result.err, "")  # the review is the answer: its notes are printed in it

    def test_the_review_in_json_says_whether_the_history_is_complete(self):
        self.assertTrue(self.run_json("sprint", "6", "--review")["review"]["historyComplete"])
        self.gateway.reject_history_search = True
        review = self.run_json("sprint", "6", "--review")["review"]
        self.assertFalse(review["historyComplete"])
        self.assertTrue(any("lower bounds" in note for note in review["notes"]))

    def test_the_review_in_json(self):
        review = self.run_json("sprint", "6", "--review")["review"]
        self.assertEqual(
            set(review), {"start", "end", "ended", "committed", "completed", "added", "removed", "carriedOver", "excludedSubtasks", "notes", "historyComplete"}
        )
        self.assertEqual(review["committed"], {"issues": 3, "points": 16.0, "withoutPoints": 0, "keys": ["PROJ-1", "PROJ-2", "PROJ-3"]})
        self.assertEqual((review["completed"]["keys"], review["added"]["keys"], review["removed"]["keys"]), (["PROJ-1", "PROJ-4"], ["PROJ-4"], ["PROJ-2"]))
        self.assertEqual((review["ended"], review["excludedSubtasks"]), (True, 0))

    def test_a_review_that_cannot_be_done(self):
        result = run_cli("sprint", "8", "--review", gateway=self.gateway)
        self.assertEqual(result.code, 1)
        self.assertIn("not started", result.err)

    def test_an_unknown_name_lists_candidates_and_exits_4(self):
        result = run_cli("sprint", "Sprint 99", "--json", gateway=self.gateway)
        error = result.error()
        self.assertEqual((result.code, error["code"], error["exitCode"]), (4, "NOT_FOUND", 4))
        self.assertIn("7 'Sprint 7' (active", error["message"])
        self.assertTrue(error["details"]["candidates"])

    def test_an_ambiguous_name_exits_1_and_names_both(self):
        self.gateway.add_sprint(dataclasses.replace(self.gateway.sprints[8], id=9, name="sprint 8"))
        result = run_cli("sprint", "Sprint 8", gateway=self.gateway)
        self.assertEqual(result.code, 1)
        self.assertIn("8 'Sprint 8'", result.err)
        self.assertIn("9 'sprint 8'", result.err)

    def test_no_sprint_given(self):
        self.assertEqual(run_cli("sprint", gateway=self.gateway).code, 2)

    def test_a_sprint_id_of_thousands_of_digits_is_a_usage_error_and_no_traceback(self):
        huge = "9" * 5000  # `int()` refuses more than 4300 digits
        for argv in (("sprint", huge), ("sprint", huge, "--review"), ("sprint-update", huge, "--name", "X"), ("sprint-add", "PROJ-4", "--sprint", huge)):
            with self.subTest(argv=argv[0]):
                result = run_cli(*argv, "--json", gateway=self.gateway)
                self.assertEqual(result.code, 2, result.err)
                self.assertEqual(result.error()["code"], "USAGE")
                self.assertIn("Not a sprint id", result.error()["message"])
                self.assertLess(len(result.error()["message"]), 200)
                self.assertNotIn("Traceback", result.err)
        self.assertEqual(self.sent(), [])
        self.assertEqual(run_cli("sprints", "--board", huge, gateway=self.gateway).code, 2)

    def test_no_people_anywhere(self):
        for options in (("--json",), ()):
            result = run_cli("sprint", "7", "--issues", "--review", *options, gateway=self.gateway, settings=dataclasses.replace(DEFAULT_SETTINGS, hide_users=True))
            for person in PEOPLE:
                self.assertNotIn(person, result.out)


class VelocityTest(AgileCli):
    def test_text(self):
        result = run_cli("velocity", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "NAME", "COMPLETED", "ON", "COMMITTED", "POINTS", "COMPLETED", "POINTS", "COMMITTED", "ISSUES", "COMPLETED", "ISSUES"])
        self.assertEqual(lines[1].split()[:6], ["6", "Sprint", "6", "2026-09-28", "16", "5"])
        self.assertIn("Average completed: 2.5 points over 2 sprint(s)", result.out)

    def test_json(self):
        data = self.run_json("velocity", "--last", "1")
        self.assertEqual((data["boardId"], data["count"], data["averageCompleted"]), (1200, 1, 5.0))
        self.assertEqual(
            data["sprints"][0],
            {
                "id": 6,
                "name": "Sprint 6",
                "startDate": "2026-09-14T08:00:00.000+02:00",
                "completeDate": "2026-09-28T16:00:00.000+02:00",
                "committedIssues": 3,
                "committedPoints": 16.0,
                "completedIssues": 2,
                "completedPoints": 5.0,
                "historyComplete": True,
            },
        )
        self.assertEqual(data["leftOut"], [])
        self.assertTrue(any("change history" in note for note in data["notes"]))

    def test_the_text_of_a_complete_history_has_no_marker_and_no_footnote(self):
        self.gateway.sprint_members[5] = []  # the seeded history of PROJ-3 knows sprint 6 only
        result = run_cli("velocity", gateway=self.gateway)
        self.assertNotIn("*", result.out)
        self.assertNotIn("incomplete", result.out)
        self.assertNotIn("could not be reviewed", result.out)
        self.assertEqual(result.out.splitlines()[-1], "Average completed: 2.5 points over 2 sprint(s), the newest first.")

    def test_the_text_marks_the_rows_that_rest_on_an_incomplete_history_and_adds_one_footnote(self):
        self.gateway.reject_history_search = True
        result = run_cli("velocity", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertRegex(lines[1], r"^6\s+Sprint 6\s+2026-09-28\s+13\*\s+5\s+2\*\s+2$")  # committed points and issues, not the completed ones
        self.assertRegex(lines[2], r"^5\s+Sprint 5\s+2026-08-17\s+0\*\s+0\s+0\*\s+0$")
        self.assertEqual(lines[3:], ["", "Average completed: 2.5 points over 2 sprint(s), the newest first.", lines[5]])
        self.assertEqual(lines[5], "* The history of the marked sprints is incomplete (see the warnings): their committed numbers may be too low or wrong.")
        self.assertEqual(result.out.count("incomplete"), 1)
        self.assertIn("WARNING: Sprint 6: Jira refused the search for the issues that left the sprint", result.err)  # the reason is on STDERR

    def test_only_the_rows_that_are_incomplete_are_marked(self):
        self.gateway.sprint_members[5] = []
        self.gateway.sprint_history["PROJ-1"] = dataclasses.replace(self.gateway.sprint_history["PROJ-1"], history_complete=False)
        lines = run_cli("velocity", gateway=self.gateway).out.splitlines()
        self.assertRegex(lines[1], r"\*\s+\d+\s+\d+\*\s+\d+$")
        self.assertNotIn("*", lines[2])
        self.assertIn("incomplete", lines[-1])

    def test_json_and_tsv_carry_the_flag_of_every_row(self):
        self.gateway.reject_history_search = True
        data = self.run_json("velocity")
        self.assertEqual([(row["id"], row["historyComplete"]) for row in data["sprints"]], [(6, False), (5, False)])
        self.assertEqual(self.run_json("velocity", "--select", "id,historyComplete")["sprints"], [{"id": 6, "historyComplete": False}, {"id": 5, "historyComplete": False}])
        lines = run_cli("velocity", "--format", "tsv", gateway=self.gateway).out.splitlines()
        self.assertEqual(lines[0].split("\t")[-2:], ["COMPLETED ISSUES", "HISTORY COMPLETE"])
        self.assertEqual([line.split("\t")[-1] for line in lines[1:]], ["false", "false"])
        self.assertNotIn("*", "\n".join(lines))  # a machine reads the flag, not a marker

    def test_closed_sprints_that_could_not_be_reviewed_are_not_called_no_closed_sprints(self):
        for number in (5, 6):
            self.gateway.sprints[number] = dataclasses.replace(self.gateway.sprints[number], start_date="")
        result = run_cli("velocity", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out, "2 closed sprints could not be reviewed (see the warnings).\n")
        self.assertIn("WARNING: Sprint 6: left out. Sprint 6 'Sprint 6'", result.err)
        self.assertEqual(result.err.count("has no start date: it cannot be reviewed"), 2)
        data = self.run_json("velocity")
        self.assertEqual((data["count"], data["sprints"], data["leftOut"], data["averageCompleted"]), (0, [], [6, 5], None))
        self.gateway.sprints[5] = dataclasses.replace(self.gateway.sprints[5], start_date="2026-08-03T08:00:00.000+02:00")
        del self.gateway.sprints[6]
        self.assertNotIn("could not be reviewed", run_cli("velocity", gateway=self.gateway).out)

    def test_a_sprint_that_is_left_out_among_others_is_said_under_the_table(self):
        self.gateway.sprints[6] = dataclasses.replace(self.gateway.sprints[6], start_date="")
        self.gateway.sprint_members[5] = []
        result = run_cli("velocity", gateway=self.gateway)
        lines = result.out.splitlines()
        self.assertTrue(lines[1].startswith("5 "))
        self.assertEqual(lines[-1], "1 closed sprint could not be reviewed (see the warnings).")
        self.assertEqual(self.run_json("velocity")["leftOut"], [6])

    def test_last_is_bounded_and_select_works(self):
        self.assertEqual(run_cli("velocity", "--last", "99", gateway=self.gateway).code, 2)
        self.assertEqual(self.run_json("velocity", "--select", "id,completedPoints")["sprints"][0], {"id": 6, "completedPoints": 5.0})

    def test_no_closed_sprints(self):
        for number in (5, 6):
            del self.gateway.sprints[number]
        self.assertEqual(run_cli("velocity", gateway=self.gateway).out, "No closed sprints.\n")
        self.assertIsNone(self.run_json("velocity")["averageCompleted"])
        self.assertEqual(self.run_json("velocity")["leftOut"], [])


class BacklogAndEpicsTest(AgileCli):
    def test_backlog(self):
        text = run_cli("backlog", gateway=self.gateway).out
        self.assertEqual(text.splitlines()[0].split(), ["KEY", "TYPE", "STATUS", "POINTS", "EPIC", "SUMMARY"])
        self.assertIn("PROJ-4", text)
        data = self.run_json("backlog", "--board", "1200")
        self.assertEqual((data["boardId"], data["total"], data["count"]), (1200, 1, 1))
        self.assertEqual(data["issues"][0]["key"], "PROJ-4")
        self.assertIsNone(data["issues"][0]["storyPoints"])
        for person in PEOPLE:
            self.assertNotIn(person, json.dumps(data))

    def test_an_empty_backlog(self):
        self.gateway.backlog_keys = []
        self.assertEqual(run_cli("backlog", gateway=self.gateway).out, "The backlog is empty.\n")

    def test_epics(self):
        self.gateway.epics["PROJ-9"] = dataclasses.replace(self.gateway.epics["PROJ-9"], done=True)
        text = run_cli("epics", gateway=self.gateway).out
        self.assertIn("PROJ-8  Epic eight  no", text)
        self.assertNotIn("PROJ-9", text)
        data = self.run_json("epics", "--done")
        self.assertEqual([epic["key"] for epic in data["epics"]], ["PROJ-8", "PROJ-9"])
        self.assertEqual(data["epics"][1], {"key": "PROJ-9", "name": "Epic nine", "summary": "Epic nine", "done": True})


class SprintAddTest(AgileCli):
    def test_a_dry_run_shows_the_payload_and_sends_nothing(self):
        result = run_cli("sprint-add", "PROJ-4", "PROJ-1", "--sprint", "Sprint 8", "--dry-run", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Dry run: would move 2 issues to sprint 8 'Sprint 8' (future); nothing was sent", result.out)
        self.assertIn('"issues": [\n    "PROJ-4",\n    "PROJ-1"\n  ]', result.out)
        self.assertIn("Note: PROJ-1 leaves Sprint 7 (active).", result.out)
        self.assertEqual(self.sent(), [])

    def test_a_dry_run_in_json(self):
        data = self.run_json("sprint-add", "PROJ-4,PROJ-1", "--sprint", "8", "--dry-run")
        self.assertEqual((data["dryRun"], data["payload"], data["keys"], data["requests"]), (True, {"issues": ["PROJ-4", "PROJ-1"]}, ["PROJ-4", "PROJ-1"], 1))
        self.assertEqual((data["confirmed"], data["missing"], data["skipped"]), (None, None, []))  # nothing was checked: null, not empty
        self.assertIs(data["readBack"], False)
        self.assertEqual(data["sprint"]["id"], 8)
        self.assertEqual(self.sent(), [])

    def test_the_real_run_is_read_back(self):
        result = run_cli("sprint-add", "PROJ-4", "--sprint", "8", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Moved 1 issue to sprint 8 'Sprint 8' (future).", result.out)
        self.assertIn("Read back: 1 of 1 are in the sprint", result.out)
        data = self.run_json("sprint-add", "PROJ-2", "--sprint", "8")
        self.assertEqual((data["dryRun"], data["confirmed"], data["missing"], data["readBack"]), (False, ["PROJ-2"], [], True))
        self.assertEqual(self.gateway.sprint_members[8], ["PROJ-4", "PROJ-2"])

    def test_errors_before_anything_is_sent(self):
        cases = (
            (("sprint-add", "PROJ-4", "PROJ-999", "--sprint", "8"), 4),  # an unknown key
            (("sprint-add", "PROJ-4", "--sprint", "99"), 4),  # an unknown sprint
            (("sprint-add", "PROJ-4", "--sprint", "6"), 1),  # a closed sprint
            (("sprint-add", "PROJ-4", "--sprint", "7", "--if-state", "future"), 3),  # the state is not what was expected
            (("sprint-add", "PROJ-4", "--sprint", "8", "--if-unchanged-since", "2026-01-01T00:00:00Z"), 3),
            (("sprint-add", "--sprint", "8"), 2),  # no keys
            (("sprint-add", "PROJ-4"), 2),  # no sprint
            (("sprint-add", "nonsense", "--sprint", "8"), 1),
        )
        for argv, code in cases:
            with self.subTest(argv=argv):
                self.assertEqual(run_cli(*argv, gateway=self.gateway).code, code)
        self.assertEqual(self.sent(), [])

    def test_the_conflict_says_what_to_do(self):
        error = run_cli("sprint-add", "PROJ-4", "--sprint", "7", "--if-state", "future", "--json", gateway=self.gateway).error()
        self.assertEqual((error["code"], error["details"]["state"], error["details"]["expected"]), ("CONFLICT", "active", ["future"]))
        self.assertIn("jira sprint", error["hint"])

    def test_a_refusal_names_the_permission(self):
        self.gateway.errors["move_issues_to_sprint"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        result = run_cli("sprint-add", "PROJ-4", "--sprint", "8", gateway=self.gateway)
        self.assertEqual(result.code, 5)
        self.assertIn("Schedule Issues", result.err)

    def test_what_is_missing_after_the_write_is_a_warning(self):
        self.gateway.list_sprint_issues = lambda sprint_id, *, keys=(): []
        result = run_cli("sprint-add", "PROJ-4", "--sprint", "8", gateway=self.gateway)
        self.assertEqual(result.code, 0)
        self.assertIn("Read back: 0 of 1 are in the sprint; not found there: PROJ-4", result.out)
        self.assertIn("WARNING", result.err)
        data = self.run_json("sprint-add", "PROJ-2", "--sprint", "8")
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"]), (True, [], ["PROJ-2"]))  # read, and not found: a finding

    def test_a_read_back_that_fails_is_a_note_and_not_an_error(self):
        self.gateway.errors["list_sprint_issues"] = UnavailableError("Jira is down.")
        result = run_cli("sprint-add", "PROJ-4", "--sprint", "8", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Note: The issues were moved, but they could not be read back: Jira is down.", result.out)
        self.assertNotIn("Read back:", result.out)
        self.assertNotIn("WARNING", result.err)  # nothing was found missing: nothing was looked at

    def test_a_read_back_that_fails_is_flagged_in_json_and_checks_nothing(self):
        self.gateway.errors["list_sprint_issues"] = UnavailableError("Jira is down.")
        result = run_cli("sprint-add", "PROJ-4", "--sprint", "8", "--json", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"], data["dryRun"]), (False, None, None, False))
        self.assertEqual((data["keys"], data["requests"]), (["PROJ-4"], 1))
        self.assertEqual(len(data["notes"]), 1)
        self.assertIn("The issues were moved, but they could not be read back: Jira is down.", data["notes"][0])
        self.assertEqual(self.gateway.sprint_members[8], ["PROJ-4"])  # the move was made

    def test_nothing_to_send(self):
        result = run_cli("sprint-add", "PROJ-1", "--sprint", "7", gateway=self.gateway)
        self.assertIn("Already there, not sent: PROJ-1", result.out)
        self.assertEqual(self.sent(), [])
        data = self.run_json("sprint-add", "PROJ-1", "--sprint", "7")
        self.assertEqual((data["keys"], data["requests"], data["readBack"], data["confirmed"], data["missing"]), ([], 0, False, None, None))


class RankTest(AgileCli):
    def test_dry_run(self):
        result = run_cli("rank", "PROJ-4", "PROJ-3", "--before", "PROJ-1", "--dry-run", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Dry run: would rank PROJ-4, PROJ-3 before PROJ-1; nothing was sent", result.out)
        self.assertIn('"rankBeforeIssue": "PROJ-1"', result.out)
        self.assertEqual(self.sent(), [])

    def test_dry_run_in_json_has_no_order_to_read(self):
        data = self.run_json("rank", "PROJ-4", "PROJ-3", "--before", "PROJ-1", "--dry-run")
        self.assertEqual((data["dryRun"], data["readBack"], data["order"], data["confirmed"]), (True, False, None, None))
        self.assertEqual(self.gateway.called("rank_order"), [])

    def test_real_run_in_json(self):
        data = self.run_json("rank", "PROJ-4", "--after", "PROJ-3")
        self.assertEqual(
            data,
            {
                "keys": ["PROJ-4"],
                "position": "after",
                "anchor": "PROJ-3",
                "payload": {"issues": ["PROJ-4"], "rankAfterIssue": "PROJ-3"},
                "dryRun": False,
                "order": ["PROJ-3", "PROJ-4"],
                "confirmed": True,
                "notes": [],
                "readBack": True,
            },
        )

    def test_a_read_back_that_fails_is_a_note_and_not_an_error(self):
        self.gateway.errors["rank_order"] = UnavailableError("Jira is down.")
        result = run_cli("rank", "PROJ-4", "--before", "PROJ-1", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Ranked PROJ-4 before PROJ-1.", result.out)
        self.assertIn("Note: The issues were ranked, but their order could not be read back: Jira is down.", result.out)
        self.assertNotIn("Read back", result.out)
        data = run_cli("rank", "PROJ-3", "--before", "PROJ-1", "--json", gateway=self.gateway).json()
        self.assertEqual((data["readBack"], data["order"], data["confirmed"], data["dryRun"]), (False, None, None, False))  # not read: neither empty nor false
        self.assertEqual(len(self.gateway.called("rank_issues")), 2)  # both ranks were made

    def test_an_order_that_comes_back_empty_is_a_finding_and_not_a_missing_read(self):
        self.gateway.rank_order = lambda keys: []
        text = run_cli("rank", "PROJ-4", "--before", "PROJ-1", gateway=self.gateway).out
        self.assertIn("Read back, in the order of the rank: none of them (NOT as asked)", text)
        data = self.run_json("rank", "PROJ-3", "--before", "PROJ-1")
        self.assertEqual((data["readBack"], data["order"], data["confirmed"]), (True, [], False))

    def test_real_run_in_text(self):
        text = run_cli("rank", "PROJ-4", "--before", "PROJ-1", gateway=self.gateway).out
        self.assertIn("Ranked PROJ-4 before PROJ-1.", text)
        self.assertIn("Read back, in the order of the rank: PROJ-4, PROJ-1 (as asked)", text)

    def test_the_rules(self):
        for argv, code in (
            (("rank", "PROJ-4"), 2),
            (("rank", "PROJ-4", "--before", "PROJ-1", "--after", "PROJ-2"), 2),
            (("rank", "PROJ-4", "--before", "PROJ-4"), 1),
            (("rank", "PROJ-4", "--before", "PROJ-999"), 4),
            (("rank", "--before", "PROJ-1"), 2),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run_cli(*argv, gateway=self.gateway).code, code)
        self.assertEqual(self.sent(), [])


class EpicAddTest(AgileCli):
    def test_dry_run(self):
        result = run_cli("epic-add", "PROJ-4", "PROJ-1", "--epic", "PROJ-8", "--dry-run", gateway=self.gateway)
        self.assertIn("Dry run: would move 2 issues to epic PROJ-8 'Epic eight'; nothing was sent", result.out)
        self.assertIn("Note: PROJ-1 leaves epic PROJ-9.", result.out)
        self.assertEqual(self.sent(), [])

    def test_json_and_out_of_the_epic(self):
        data = self.run_json("epic-add", "PROJ-1", "--epic", "none")
        self.assertEqual((data["epic"], data["epicName"], data["keys"], data["confirmed"], data["dryRun"]), ("none", "", ["PROJ-1"], ["PROJ-1"], False))
        self.assertEqual((data["missing"], data["readBack"]), ([], True))
        self.assertIn("no epic", run_cli("epic-add", "PROJ-2", "--epic", "none", "--dry-run", gateway=self.gateway).out)

    def test_a_dry_run_and_nothing_to_send_have_nothing_read_back(self):
        dry = self.run_json("epic-add", "PROJ-4", "--epic", "PROJ-8", "--dry-run")
        self.assertEqual((dry["dryRun"], dry["readBack"], dry["confirmed"], dry["missing"]), (True, False, None, None))
        same = self.run_json("epic-add", "PROJ-1", "--epic", "PROJ-9")  # PROJ-1 is in PROJ-9 already
        self.assertEqual((same["keys"], same["skipped"], same["requests"]), ([], ["PROJ-1"], 0))
        self.assertEqual((same["readBack"], same["confirmed"], same["missing"]), (False, None, None))
        self.assertEqual(self.sent(), [])

    def test_a_read_back_that_fails_is_a_note_and_not_an_error(self):
        self.gateway.errors["list_epic_issues"] = UnavailableError("Jira is down.")
        result = run_cli("epic-add", "PROJ-4", "--epic", "PROJ-8", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Note: The issues were moved, but they could not be read back: Jira is down.", result.out)
        self.assertNotIn("Read back:", result.out)
        self.assertNotIn("WARNING", result.err)
        data = run_cli("epic-add", "PROJ-2", "--epic", "PROJ-8", "--json", gateway=self.gateway).json()
        self.assertEqual((data["readBack"], data["confirmed"], data["missing"], data["dryRun"]), (False, None, None, False))
        self.assertEqual((data["keys"], data["requests"]), (["PROJ-2"], 1))
        self.assertEqual(self.gateway.issues["PROJ-2"].epic_key, "PROJ-8")  # the move was made

    def test_the_rules(self):
        for argv, code in (
            (("epic-add", "PROJ-4", "--epic", "PROJ-77"), 4),
            (("epic-add", "PROJ-8", "--epic", "PROJ-9"), 1),
            (("epic-add", "PROJ-4"), 2),
            (("epic-add", "--epic", "PROJ-8"), 2),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run_cli(*argv, gateway=self.gateway).code, code)
        self.assertEqual(self.sent(), [])


class SprintWritesTest(AgileCli):
    def test_create_dry_run(self):
        result = run_cli("sprint-create", "Sprint", "9", "--board", "1200", "--goal", "Go", "--start", "2026-11-02", "--end", "2026-11-16", "--dry-run", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Dry run: would create the sprint; nothing was sent", result.out)
        self.assertIn('"startDate": "2026-11-02T00:00:00.000+00:00"', result.out)
        self.assertEqual(self.sent(), [])

    def test_create_in_json_is_read_back(self):
        data = self.run_json("sprint-create", "Sprint 9", "--board", "1200")
        self.assertEqual((data["dryRun"], data["before"], data["payload"]), (False, None, {"name": "Sprint 9", "originBoardId": 1200}))
        self.assertEqual((data["sprint"]["name"], data["sprint"]["state"], data["sprint"]["boardId"]), ("Sprint 9", "future", 1200))
        self.assertEqual(self.gateway.called("get_sprint")[0][1], data["sprint"]["id"])

    def test_a_create_that_cannot_be_read_back_is_a_success_with_a_note_that_does_not_advise_a_second_try(self):
        self.gateway.errors["get_sprint"] = UnavailableError("Jira answered with a server error (HTTP 503).")
        result = run_cli("sprint-create", "Sprint 9", "--board", "1200", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Created the sprint; it could not be read back, so this is what Jira answered to the request:", result.out)
        self.assertNotIn("read back from Jira", result.out)
        self.assertRegex(result.out, r"Sprint:\s+Sprint 9 \(id 101\)")
        self.assertIn("Note: The sprint was created (id 101), but it could not be read back: Jira answered with a server error (HTTP 503).", result.out)
        self.assertIn("Do not send the command again; look at the sprint with `jira sprint 101`.", result.out)
        self.assertNotRegex(result.out + result.err, r"(?i)try again|retry")
        self.assertEqual(len(self.gateway.called("create_sprint")), 1)
        data = run_cli("sprint-create", "Sprint 10", "--board", "1200", "--json", gateway=self.gateway).json()
        self.assertEqual((data["readBack"], data["dryRun"], data["sprint"]["id"], data["sprint"]["name"]), (False, False, 102, "Sprint 10"))
        self.assertIn("could not be read back", data["notes"][0])

    def test_an_update_that_cannot_be_read_back_is_a_success_with_a_note_that_does_not_advise_a_second_try(self):
        read = self.gateway.get_sprint
        calls = []

        def second_read_fails(sprint_id):
            calls.append(sprint_id)
            if len(calls) > 1:
                raise UnavailableError("Jira answered with a server error (HTTP 503).")
            return read(sprint_id)

        self.gateway.get_sprint = second_read_fails
        result = run_cli("sprint-update", "8", "--name", "Renamed", gateway=self.gateway)
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Changed the sprint; it could not be read back, so this is what Jira answered to the request:", result.out)
        self.assertRegex(result.out, r"Sprint:\s+Renamed \(id 8\)")
        self.assertIn("Note: The sprint was changed (id 8), but it could not be read back: Jira answered with a server error (HTTP 503).", result.out)
        self.assertNotRegex(result.out + result.err, r"(?i)try again|retry")
        self.assertEqual(len(self.gateway.called("update_sprint")), 1)
        calls.clear()
        data = run_cli("sprint-update", "8", "--goal", "Other", "--json", gateway=self.gateway).json()
        self.assertEqual((data["readBack"], data["sprint"]["goal"], data["before"]["name"]), (False, "Other", "Renamed"))

    def test_a_write_that_was_read_back_says_so_in_text_and_json(self):
        result = run_cli("sprint-create", "Sprint 9", "--board", "1200", gateway=self.gateway)
        self.assertIn("Created the sprint; read back from Jira:", result.out)
        self.assertNotIn("Note:", result.out)
        self.assertTrue(self.run_json("sprint-update", "8", "--name", "Renamed")["readBack"])
        self.assertFalse(self.run_json("sprint-update", "8", "--name", "Again", "--dry-run")["readBack"])

    def test_create_errors(self):
        for argv, code in (
            (("sprint-create", "Sprint 8", "--board", "1200"), 1),  # a duplicate
            (("sprint-create", "Sprint 9"), 2),  # no board
            (("sprint-create", "--board", "1200"), 2),  # no name
            (("sprint-create", "Sprint 9", "--board", "999"), 4),
            (("sprint-create", "Sprint 9", "--board", "1200", "--start", "2026-11-16", "--end", "2026-11-02"), 1),
            (("sprint-create", "Sprint 9", "--board", "1200", "--start", "someday"), 1),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run_cli(*argv, gateway=self.gateway).code, code)
        self.assertEqual(self.sent(), [])

    def test_an_end_that_is_the_start_is_refused_and_one_minute_later_is_not(self):
        for argv in (
            ("sprint-create", "Sprint 9", "--board", "1200", "--start", "2026-11-02", "--end", "2026-11-02"),
            ("sprint-create", "Sprint 9", "--board", "1200", "--start", "2026-11-02T09:00", "--end", "2026-11-02T09:00+00:00"),
            ("sprint-update", "7", "--end", "2026-10-12T08:00:00+02:00"),  # the start of sprint 7
            ("sprint-update", "7", "--start", "2026-10-26T08:00:00+02:00"),  # its end
        ):
            with self.subTest(argv=argv):
                result = run_cli(*argv, "--json", gateway=self.gateway)
                self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
                self.assertIn("is not after the start", result.error()["message"])
                self.assertIn("--end", result.error()["hint"])
        self.assertEqual(self.sent(), [])
        later = run_cli("sprint-create", "Sprint 9", "--board", "1200", "--start", "2026-11-02T09:00", "--end", "2026-11-02T09:01", "--dry-run", gateway=self.gateway)
        self.assertEqual(later.code, 0, later.err)

    def test_update_dry_run(self):
        data = self.run_json("sprint-update", "Sprint 8", "--name", "Renamed", "--dry-run")
        self.assertEqual((data["payload"], data["dryRun"], data["sprint"], data["before"]["name"]), ({"name": "Renamed"}, True, None, "Sprint 8"))
        self.assertEqual(self.sent(), [])

    def test_start_and_complete(self):
        started = self.run_json("sprint-update", "8", "--state", "active", "--start", "now", "--end", "2026-11-03", "--if-state", "future")
        self.assertEqual((started["sprint"]["state"], started["before"]["state"]), ("active", "future"))
        self.assertEqual(started["payload"]["startDate"], "2026-10-20T12:00:00.000+00:00")
        completed = run_cli("sprint-update", "7", "--state", "closed", "--if-state", "active", gateway=self.gateway)
        self.assertEqual(completed.code, 0, completed.err)
        self.assertIn("Changed the sprint; read back from Jira:", completed.out)
        self.assertIn("State:     closed", completed.out)
        self.assertIn("does not move the open issues", completed.out)

    def test_update_errors(self):
        for argv, code in (
            (("sprint-update", "8"), 2),  # nothing to change
            (("sprint-update", "8", "--state", "active"), 1),  # no dates
            (("sprint-update", "6", "--name", "Other"), 1),  # closed
            (("sprint-update", "7", "--state", "closed", "--if-state", "future"), 3),
            (("sprint-update", "8", "--state", "closed"), 1),
            (("sprint-update", "99", "--name", "Other"), 4),
            (("sprint-update", "8", "--state", "future"), 2),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(run_cli(*argv, gateway=self.gateway).code, code)
        self.assertEqual(self.sent(), [])

    def test_the_goal_can_be_removed(self):
        data = self.run_json("sprint-update", "7", "--goal", "")
        self.assertEqual((data["payload"], data["sprint"]["goal"]), ({"goal": ""}, ""))

    def test_the_goal_can_be_removed_without_an_empty_argument(self):
        # Windows PowerShell 5.1 does not pass an empty argument at all
        data = self.run_json("sprint-update", "7", "--no-goal")
        self.assertEqual((data["payload"], data["sprint"]["goal"], data["before"]["goal"]), ({"goal": ""}, "", "Stabilise"))
        clash = run_cli("sprint-update", "7", "--no-goal", "--goal", "Other", gateway=self.gateway)
        self.assertEqual(clash.code, 2)
        self.assertIn("cannot be combined", clash.err)


class CatalogTest(unittest.TestCase):
    def test_the_commands_are_in_the_catalog_with_a_group_and_when_to_use(self):
        catalog = {item["name"]: item for item in run_cli("commands", "--json").json()["commands"]}
        names = ("boards", "sprints", "sprint", "velocity", "backlog", "epics", "sprint-add", "rank", "epic-add", "sprint-create", "sprint-update")
        for name in names:
            with self.subTest(command=name):
                self.assertEqual(catalog[name]["group"], "Agile")
                self.assertTrue(catalog[name]["whenToUse"])
                self.assertTrue(catalog[name]["supportsJson"])
        self.assertEqual({name for name in names if catalog[name]["changesData"]}, {"sprint-add", "rank", "epic-add", "sprint-create", "sprint-update"})

    def test_every_write_has_a_dry_run(self):
        for item in run_cli("commands", "--json").json()["commands"]:
            if item["group"] == "Agile" and item["changesData"]:
                self.assertIn("--dry-run", [name for arg in item["arguments"] for name in arg["names"]], item["name"])


if __name__ == "__main__":
    unittest.main()
