"""Jira Software's JSON into domain objects: boards, sprints, epics, and issues with their history."""
import unittest

from jira_dc_cli.adapters.driven import map_agile
from jira_dc_cli.domain.agile import Board, Epic, SprintInfo

IDS = {"story_points": "customfield_10001", "sprint": "customfield_10003", "epic_link": "customfield_10002"}


def entry(created, *items):
    return {"id": "1", "author": {"name": "jdoe", "displayName": "Jane Doe"}, "created": created, "items": list(items)}


def sprint_item(before, after, **more):
    return {"field": "Sprint", "fieldtype": "custom", "from": before, "fromString": "", "to": after, "toString": "", **more}


def issue(**changes):
    data = {
        "key": "PROJ-1",
        "fields": {
            "summary": "A summary",
            "issuetype": {"name": "Story"},
            "status": {"name": "Done", "statusCategory": {"key": "done"}},
            "created": "2025-08-01T09:00:00.000+0200",
            "customfield_10001": 5,
        },
        "changelog": {"total": 2, "histories": [
            entry("2025-08-02T09:00:00.000+0200", sprint_item("", "6")),
            entry("2025-08-03T09:00:00.000+0200", {"field": "status", "from": "3", "fromString": "In Progress", "to": "5", "toString": "Done"}),
        ]},
    }
    data.update(changes)
    return data


class BoardsSprintsEpicsTest(unittest.TestCase):
    def test_a_board(self):
        self.assertEqual(map_agile.board_from({"id": 1200, "name": "Demo board", "type": "Scrum", "self": "x"}), Board(1200, "Demo board", "scrum"))
        self.assertEqual(map_agile.board_from({}), Board(0, "", ""))

    def test_a_sprint_keeps_its_dates_and_lowers_the_state(self):
        data = {
            "id": 7, "name": "Sprint 7", "state": "ACTIVE", "startDate": "2025-10-01T08:00:00.000+02:00", "endDate": "2025-10-15T08:00:00.000+02:00",
            "activatedDate": "2025-10-01T08:01:00.000+02:00", "goal": "Go", "originBoardId": 1200,
        }
        sprint = map_agile.sprint_from(data)
        self.assertEqual((sprint.id, sprint.state, sprint.goal, sprint.board_id, sprint.complete_date), (7, "active", "Go", 1200, ""))
        self.assertEqual((sprint.start_date, sprint.activated_date), ("2025-10-01T08:00:00.000+02:00", "2025-10-01T08:01:00.000+02:00"))
        self.assertEqual(map_agile.sprint_from({"id": "7"}), SprintInfo(0, "", "", "", "", "", "", None, ""))  # an id that is no number is not taken

    def test_a_number_without_a_finite_value_is_not_an_id(self):
        for value in (float("inf"), float("-inf"), float("nan"), True, "7", None, [7]):
            with self.subTest(value=value):
                self.assertEqual(map_agile.sprint_from({"id": value, "originBoardId": value}).id, 0)
                self.assertIsNone(map_agile.sprint_from({"originBoardId": value}).board_id)
        self.assertEqual(map_agile.sprint_from({"id": 7.0}).id, 7)
        self.assertEqual(map_agile.sprint_from({"id": 10**400}).id, 10**400)  # a whole number of any size is taken as it is

    def test_an_epic(self):
        epic = map_agile.epic_from({"id": 10, "key": "PROJ-9", "name": "Nine", "summary": "Epic nine", "done": True, "color": {"key": "color_4"}})
        self.assertEqual(epic, Epic("PROJ-9", "Nine", "Epic nine", True, 10))
        self.assertEqual(map_agile.epic_from({"key": "PROJ-8"}), Epic("PROJ-8", "", "", False, None))

    def test_a_project_key(self):
        self.assertEqual(map_agile.project_key_from({"id": "1", "key": "PROJ"}), "PROJ")

    def test_statuses(self):
        data = [{"id": "5", "name": "Done", "statusCategory": {"key": "done"}}, {"id": "1", "name": "To Do"}, "odd"]
        self.assertEqual(map_agile.status_triples_from(data), [("5", "Done", "done"), ("1", "To Do", "")])
        self.assertEqual(map_agile.status_triples_from({"not": "a list"}), [])


class IssueWithHistoryTest(unittest.TestCase):
    def test_what_the_review_needs(self):
        found = map_agile.sprint_issue_from(issue(), IDS)
        self.assertEqual((found.key, found.summary, found.issue_type, found.subtask, found.points), ("PROJ-1", "A summary", "Story", False, 5.0))
        self.assertEqual((found.status, found.status_category, found.created), ("Done", "done", "2025-08-01T09:00:00.000+0200"))
        self.assertEqual([(move.at, set(move.before), set(move.after)) for move in found.sprint_moves], [("2025-08-02T09:00:00.000+0200", set(), {6})])
        self.assertEqual([(move.from_id, move.from_name, move.to_id, move.to_name) for move in found.status_moves], [("3", "In Progress", "5", "Done")])
        self.assertTrue(found.history_complete)

    def test_the_sprint_field_is_found_by_its_id_too(self):
        data = issue(changelog={"total": 1, "histories": [entry("2025-08-02T09:00:00.000+0200", {"field": "Iteration", "fieldId": "customfield_10003", "from": "5", "to": "5, 6"})]})
        self.assertEqual([set(move.after) for move in map_agile.sprint_issue_from(data, IDS).sprint_moves], [{5, 6}])

    def test_other_fields_and_odd_items_are_ignored(self):
        data = issue(changelog={"total": 1, "histories": [entry("2025-08-02T09:00:00.000+0200", {"field": "summary", "from": "a", "to": "b"}, "not an item", {})]})
        found = map_agile.sprint_issue_from(data, IDS)
        self.assertEqual((found.sprint_moves, found.status_moves), ((), ()))

    def test_a_sub_task_and_missing_points(self):
        data = issue()
        data["fields"] = {**data["fields"], "issuetype": {"name": "Sub-task", "subtask": True}, "customfield_10001": None}
        found = map_agile.sprint_issue_from(data, IDS)
        self.assertEqual((found.subtask, found.points), (True, None))
        data["fields"]["customfield_10001"] = True  # a flag is no number
        self.assertIsNone(map_agile.sprint_issue_from(data, IDS).points)
        self.assertIsNone(map_agile.sprint_issue_from(data, {"story_points": None}).points)

    def test_points_that_are_no_finite_number_are_missing(self):
        data = issue()
        for value in (float("inf"), float("nan"), 10**400, "5", [5]):
            with self.subTest(value=value):
                data["fields"] = {**data["fields"], "customfield_10001": value}
                self.assertIsNone(map_agile.sprint_issue_from(data, IDS).points)
        for value, expected in ((3, 3.0), (0.1, 0.1), (0, 0.0)):
            data["fields"] = {**data["fields"], "customfield_10001": value}
            self.assertEqual(map_agile.sprint_issue_from(data, IDS).points, expected)

    def test_a_history_that_was_cut_or_not_sent_is_incomplete(self):
        cut = issue(changelog={"total": 5, "histories": [entry("2025-08-02T09:00:00.000+0200", sprint_item("", "6"))]})
        self.assertFalse(map_agile.sprint_issue_from(cut, IDS).history_complete)
        without = issue()
        del without["changelog"]
        self.assertFalse(map_agile.sprint_issue_from(without, IDS).history_complete)  # the server ignored expand=changelog

    def test_the_authors_of_the_history_are_never_read(self):
        found = map_agile.sprint_issue_from(issue(), IDS)
        self.assertNotIn("Jane", repr(found))
        self.assertNotIn("jdoe", repr(found))

    def test_an_issue_with_nothing_in_it(self):
        found = map_agile.sprint_issue_from({"key": "PROJ-2"}, IDS)
        self.assertEqual((found.key, found.summary, found.status, found.points, found.sprint_moves, found.history_complete), ("PROJ-2", "", "", None, (), False))


if __name__ == "__main__":
    unittest.main()
