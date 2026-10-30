"""The use cases of agile with `FakeGateway`: no HTTP, no files."""
import dataclasses
import unittest

from jira_dc_cli.domain.agile import Board, SprintInfo
from jira_dc_cli.domain.errors import AuthError, ConflictError, NotFoundError, RejectedError, UnavailableError, UsageError, ValidationError
from jira_dc_cli.domain.sprints import SprintIssue, SprintMove, StatusMove
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, build_app, sample_issue

CLOSED_START, CLOSED_END, CLOSED_DONE = "2026-09-14T08:00:00.000+02:00", "2026-09-28T08:00:00.000+02:00", "2026-09-28T16:00:00.000+02:00"
BEFORE, DURING, LATE = "2026-09-10T10:00:00.000+0200", "2026-09-18T10:00:00.000+0200", "2026-09-24T10:00:00.000+0200"


def sprint(ident, name, state, start="", end="", complete="", goal="", board=1200):
    return SprintInfo(ident, name, state, start, end, complete, goal, board)


def moved(at, before, after):
    return SprintMove(at, frozenset(before), frozenset(after))


def done(at):
    return StatusMove(at, "3", "In Progress", "10001", "Done")


def history(key, moves, *, status_moves=(), status="To Do", category="new", points=None, **more):
    return SprintIssue(key, f"Summary of {key}", "Story", False, "2026-08-01T09:00:00.000+0200", status, category, points, tuple(moves), tuple(status_moves), **more)


def seeded():
    """A board with two closed sprints, an active and a future one, and the history of sprint 6 for a review."""
    gateway = FakeGateway()
    gateway.add_sprint(sprint(5, "Sprint 5", "closed", "2026-08-03T08:00:00.000+02:00", "2026-08-17T08:00:00.000+02:00", "2026-08-17T16:00:00.000+02:00"), ["PROJ-3"])
    gateway.add_sprint(sprint(6, "Sprint 6", "closed", CLOSED_START, CLOSED_END, CLOSED_DONE, "Ship the export"), ["PROJ-1", "PROJ-3", "PROJ-4"])
    gateway.add_sprint(sprint(7, "Sprint 7", "active", "2026-10-12T08:00:00.000+02:00", "2026-10-26T08:00:00.000+02:00", goal="Stabilise"), ["PROJ-1", "PROJ-2"])
    gateway.add_sprint(sprint(8, "Sprint 8", "future"), [])
    gateway.sprint_history.update(
        {
            "PROJ-1": history("PROJ-1", [moved(BEFORE, [], [6])], status_moves=[done(DURING)], status="Done", category="done", points=5.0),
            "PROJ-3": history("PROJ-3", [moved(BEFORE, [], [6])], status="In Progress", category="indeterminate", points=8.0),
            "PROJ-4": history("PROJ-4", [moved(DURING, [], [6])], status_moves=[done(LATE)], status="Done", category="done"),
        }
    )
    gateway.sprint_removed[6] = [history("PROJ-2", [moved(BEFORE, [], [6]), moved(DURING, [6], [])], points=3.0)]
    gateway.sync_sprint_fields()
    return gateway


class AgileCase(unittest.TestCase):
    def setUp(self):
        self.gateway = seeded()
        self.app = build_app(gateway=self.gateway)
        self.agile = self.app.agile


class BoardsTest(AgileCase):
    def test_boards_with_their_projects(self):
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("PROJ", "DEMO"))
        found = self.agile.list_boards.execute()
        self.assertEqual([(board.id, board.name, board.type, board.projects) for board in found.boards], [(1200, "Demo board", "scrum", ("PROJ",)), (1300, "Flow board", "kanban", ("PROJ", "DEMO"))])
        self.assertEqual(found.total, 2)

    def test_filters(self):
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("DEMO",))
        self.assertEqual([board.id for board in self.agile.list_boards.execute(board_type="kanban").boards], [1300])
        self.assertEqual([board.id for board in self.agile.list_boards.execute(project="demo").boards], [1300])
        self.assertEqual([board.id for board in self.agile.list_boards.execute(name=" flow ").boards], [1300])
        self.assertEqual(self.gateway.called("list_boards")[0][1:], ("", "kanban", ""))

    def test_the_type_is_checked_and_the_limit_cuts_the_list(self):
        with self.assertRaises(ValidationError):
            self.agile.list_boards.execute(board_type="agile")
        with self.assertRaises(UsageError):
            self.agile.list_boards.execute(limit=0)
        for number in range(1, 6):
            self.gateway.boards[2000 + number] = Board(2000 + number, f"Board {number}", "scrum", ("PROJ",))
        found = self.agile.list_boards.execute(limit=2)
        self.assertEqual((len(found.boards), found.total), (2, 6))
        self.assertEqual(len(self.gateway.called("board_projects")), 2)  # the projects are looked up for the boards that are shown

    def test_a_board_whose_projects_cannot_be_read_is_still_listed(self):
        self.gateway.errors["board_projects"] = NotFoundError("no")
        self.assertEqual(self.agile.list_boards.execute().boards[0].projects, ())

    def test_a_refusal_to_read_names_the_permission_and_a_bad_token_keeps_its_hint(self):
        for name, call in (
            ("list_boards", lambda: self.agile.list_boards.execute()),
            ("list_sprints", lambda: self.agile.list_sprints.execute(board=1200)),
            ("get_sprint", lambda: self.agile.show_sprint.execute("7")),
            ("list_epics", lambda: self.agile.list_epics.execute(board=1200)),
            ("board_backlog", lambda: self.agile.show_backlog.execute(board=1200)),
        ):
            with self.subTest(call=name):
                self.gateway.errors[name] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
                with self.assertRaises(AuthError) as caught:
                    call()
                self.assertIn("Browse Projects", caught.exception.hint)
                self.assertIn("Jira Software licence", caught.exception.hint)
                self.gateway.errors[name] = AuthError("Jira did not accept the token (HTTP 401).", hint="new token", details={"status": 401})
                with self.assertRaises(AuthError) as caught:
                    call()
                self.assertEqual(caught.exception.hint, "new token")
                del self.gateway.errors[name]

    def test_names_are_redacted(self):
        self.gateway.boards[1200] = Board(1200, "Board of jane.doe@example.com", "scrum", ())
        self.assertEqual(self.agile.list_boards.execute().boards[0].name, "Board of [email]")


class SprintsTest(AgileCase):
    def test_the_board_comes_from_the_project(self):
        found = self.agile.list_sprints.execute(project="PROJ")
        self.assertEqual(found.board.id, 1200)
        self.assertEqual([item.id for item in found.sprints], [7, 8, 6, 5])  # active, future, then the closed ones, the newest first
        self.assertEqual(self.gateway.called("list_boards")[0][1:], ("PROJ", "scrum", ""))

    def test_the_current_project_is_the_default(self):
        self.assertEqual(self.agile.list_sprints.execute().board.id, 1200)

    def test_without_any_project(self):
        app = build_app(gateway=self.gateway, settings=dataclasses.replace(DEFAULT_SETTINGS, project=""))
        with self.assertRaises(UsageError) as caught:
            app.agile.list_sprints.execute()
        self.assertIn("--board", caught.exception.hint)

    def test_a_board_by_id(self):
        self.assertEqual(self.agile.list_sprints.execute(board=1200).board.name, "Demo board")
        with self.assertRaises(NotFoundError):
            self.agile.list_sprints.execute(board=999)

    def test_several_scrum_boards_ask_for_the_board_and_list_them(self):
        self.gateway.boards[1201] = Board(1201, "Second board", "scrum", ("PROJ",))
        with self.assertRaises(ValidationError) as caught:
            self.agile.list_sprints.execute(project="PROJ")
        self.assertIn("1200 'Demo board'", caught.exception.message)
        self.assertIn("1201 'Second board'", caught.exception.message)
        self.assertIn("--board ID", caught.exception.hint)

    def test_a_kanban_board_has_no_sprints(self):
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("DEMO",))
        with self.assertRaises(ValidationError) as caught:
            self.agile.list_sprints.execute(board=1300)
        self.assertIn("kanban", caught.exception.message)
        with self.assertRaises(ValidationError) as caught:
            self.agile.list_sprints.execute(project="DEMO")  # only a kanban board
        self.assertIn("no scrum board", caught.exception.message)
        self.assertIn("1300 'Flow board' (kanban)", caught.exception.message)

    def test_a_project_without_boards(self):
        with self.assertRaises(NotFoundError):
            self.agile.list_sprints.execute(project="NONE")

    def test_states_and_limit(self):
        found = self.agile.list_sprints.execute(board=1200, states="closed,future", limit=2)
        self.assertEqual([item.id for item in found.sprints], [8, 6])
        self.assertEqual(found.total, 3)
        self.assertEqual(self.gateway.called("list_sprints")[0][1:], (1200, ("closed", "future")))
        with self.assertRaises(ValidationError):
            self.agile.list_sprints.execute(board=1200, states="running")

    def test_names_and_goals_are_redacted(self):
        self.gateway.sprints[7] = dataclasses.replace(self.gateway.sprints[7], goal="Ask [~jdoe] or jane.doe@example.com")
        shown = next(item for item in self.agile.list_sprints.execute(board=1200).sprints if item.id == 7)
        self.assertEqual(shown.goal, "Ask [~user] or [email]")


class ShowSprintTest(AgileCase):
    def test_by_id(self):
        view = self.agile.show_sprint.execute("7")
        self.assertEqual((view.sprint.name, view.sprint.goal, view.issues, view.review), ("Sprint 7", "Stabilise", None, None))
        self.assertEqual(self.gateway.called("list_sprints"), [])  # an id needs no board

    def test_by_name_on_the_board_of_the_project(self):
        self.assertEqual(self.agile.show_sprint.execute("  sprint   8 ").sprint.id, 8)
        self.assertEqual(self.agile.show_sprint.execute("sprint 6", board=1200).sprint.id, 6)

    def test_an_unknown_name_lists_candidates_and_an_unknown_id_is_not_found(self):
        with self.assertRaises(NotFoundError) as caught:
            self.agile.show_sprint.execute("Sprint 99")
        self.assertIn("Sprint 7", caught.exception.message)
        with self.assertRaises(NotFoundError):
            self.agile.show_sprint.execute("999")

    def test_a_name_twice_is_ambiguous(self):
        self.gateway.add_sprint(sprint(9, "SPRINT 7", "future"))
        with self.assertRaises(ValidationError) as caught:
            self.agile.show_sprint.execute("Sprint 7")
        self.assertEqual({item["id"] for item in caught.exception.details["candidates"]}, {7, 9})

    def test_the_issues_of_a_sprint_have_no_people(self):
        view = self.agile.show_sprint.execute("7", issues=True)
        self.assertEqual([issue.key for issue in view.issues], ["PROJ-1", "PROJ-2"])
        self.assertTrue(all(issue.assignee is None and issue.reporter is None for issue in view.issues))

    def test_the_review_of_a_closed_sprint(self):
        review = self.agile.show_sprint.execute("6", review=True).review
        self.assertEqual(review.committed.keys, ("PROJ-1", "PROJ-2", "PROJ-3"))
        self.assertEqual((review.committed.points, review.completed.keys, review.completed.points), (16.0, ("PROJ-1", "PROJ-4"), 5.0))
        self.assertEqual((review.added.keys, review.removed.keys, review.carried_over.keys), (("PROJ-4",), ("PROJ-2",), ("PROJ-3",)))
        self.assertEqual((review.start, review.ended), ("2026-09-14T08:00:00+02:00", True))
        self.assertEqual(review.end, "2026-09-28T16:00:00+02:00")
        self.assertTrue(review.history_complete)
        self.assertEqual(self.gateway.called("search_with_history")[0][1], "sprint = 6 ORDER BY key ASC")
        self.assertEqual(self.gateway.called("search_with_history")[1][1], "sprint was 6 AND sprint != 6 ORDER BY key ASC")
        self.assertEqual(len(self.gateway.called("status_category_list")), 1)

    def test_a_sprint_that_runs_is_measured_to_now(self):
        self.gateway.sprint_history["PROJ-1"] = history("PROJ-1", [moved("2026-10-10T10:00:00.000+0200", [], [7])], status="Done", category="done", points=5.0)
        review = self.agile.show_sprint.execute("7", review=True).review
        self.assertFalse(review.ended)
        self.assertEqual(review.end, "2026-10-20T12:00:00+00:00")  # the clock of the tests
        self.assertEqual(review.committed.keys, ("PROJ-1",))
        self.assertTrue(any("still running" in note for note in review.notes))

    def test_a_future_sprint_cannot_be_reviewed(self):
        with self.assertRaises(ValidationError) as caught:
            self.agile.show_sprint.execute("8", review=True)
        self.assertIn("not started", caught.exception.message)

    def test_a_closed_sprint_without_a_date_cannot_be_reviewed(self):
        self.gateway.sprints[5] = dataclasses.replace(self.gateway.sprints[5], complete_date="", end_date="")
        with self.assertRaises(ValidationError) as caught:
            self.agile.show_sprint.execute("5", review=True)
        self.assertIn("gives no date", caught.exception.message)

    def test_a_sprint_without_a_start_date_says_so_and_only_a_future_one_has_not_started(self):
        for state in ("closed", "active"):
            with self.subTest(state=state):
                self.gateway.sprints[8] = sprint(8, "Sprint 8", state, "", "", CLOSED_DONE if state == "closed" else "")
                with self.assertRaises(ValidationError) as caught:
                    self.agile.show_sprint.execute("8", review=True)
                self.assertIn("has no start date", caught.exception.message)
                self.assertNotIn("not started", caught.exception.message)
                self.assertEqual(caught.exception.details, {"sprintId": 8, "state": state})
        self.gateway.sprints[8] = sprint(8, "Sprint 8", "future", CLOSED_START)  # even with a date: it has not started
        with self.assertRaises(ValidationError) as caught:
            self.agile.show_sprint.execute("8", review=True)
        self.assertIn("has not started: there is nothing to review", caught.exception.message)

    def test_the_activation_date_stands_in_for_a_missing_start_date(self):
        self.gateway.sprints[7] = dataclasses.replace(self.gateway.sprints[7], start_date="", activated_date="2026-10-12T08:01:00.000+02:00")
        self.assertEqual(self.agile.show_sprint.execute("7", review=True).review.start, "2026-10-12T08:01:00+02:00")

    def test_without_the_history_operator_committed_added_and_removed_are_lower_bounds(self):
        self.gateway.reject_history_search = True
        review = self.agile.show_sprint.execute("6", review=True).review
        self.assertEqual(review.removed.keys, ())
        self.assertEqual(review.committed.keys, ("PROJ-1", "PROJ-3"))  # PROJ-2 was in the sprint at its start and left it: it is missing
        self.assertFalse(review.history_complete)
        warning = next(note for note in review.warnings if "sprint was" in note)
        for word in ("removed", "committed", "added", "lower bounds", "velocity"):
            self.assertIn(word, warning)
        self.assertIn(warning, review.notes)
        self.assertEqual(len(self.gateway.called("search_with_history")), 2)  # the search was tried

    def test_sub_tasks_are_counted_on_request(self):
        self.gateway.sprint_history["PROJ-3"] = dataclasses.replace(self.gateway.sprint_history["PROJ-3"], subtask=True)
        self.assertNotIn("PROJ-3", self.agile.show_sprint.execute("6", review=True).review.committed.keys)
        self.assertIn("PROJ-3", self.agile.show_sprint.execute("6", review=True, with_subtasks=True).review.committed.keys)

    def test_the_sprint_and_its_goal_are_redacted(self):
        self.gateway.sprints[6] = dataclasses.replace(self.gateway.sprints[6], goal="For jane.doe@example.com")
        self.assertEqual(self.agile.show_sprint.execute("6").sprint.goal, "For [email]")


class VelocityTest(AgileCase):
    def test_the_last_closed_sprints_newest_first_and_the_average(self):
        result = self.agile.velocity.execute(project="PROJ", last=5)
        self.assertEqual([row.id for row in result.rows], [6, 5])
        self.assertEqual((result.rows[0].committed_points, result.rows[0].completed_points, result.rows[0].completed_issues), (16.0, 5.0, 2))
        self.assertEqual(result.average_completed, 2.5)
        self.assertEqual(result.board.id, 1200)
        self.assertEqual(len(self.gateway.called("status_category_list")), 1)  # once for every sprint

    def test_last_limits_the_sprints(self):
        result = self.agile.velocity.execute(board=1200, last=1)
        self.assertEqual([row.id for row in result.rows], [6])
        self.assertEqual(len(self.gateway.called("search_with_history")), 2)

    def test_last_is_bounded(self):
        for bad in (0, 21):
            with self.assertRaises(UsageError):
                self.agile.velocity.execute(last=bad)

    def test_a_board_without_closed_sprints(self):
        for number in (5, 6):
            del self.gateway.sprints[number]
        result = self.agile.velocity.execute()
        self.assertEqual((result.rows, result.average_completed), ([], None))
        self.assertEqual(self.gateway.called("status_category_list"), [])

    def test_a_sprint_that_cannot_be_reviewed_is_left_out_and_named(self):
        self.gateway.sprints[6] = dataclasses.replace(self.gateway.sprints[6], complete_date="", end_date="")
        result = self.agile.velocity.execute()
        self.assertEqual([row.id for row in result.rows], [5])
        self.assertEqual(result.left_out, [6])
        self.assertTrue(any(note.startswith("Sprint 6: left out") and "gives no date" in note for note in result.notes))

    def test_closed_sprints_that_are_all_left_out_are_listed_and_the_reason_is_a_start_date_that_is_missing(self):
        for number in (5, 6):
            self.gateway.sprints[number] = dataclasses.replace(self.gateway.sprints[number], start_date="")
        result = self.agile.velocity.execute()
        self.assertEqual((result.rows, result.average_completed, result.left_out), ([], None, [6, 5]))
        left_out = [note for note in result.notes if "left out" in note]
        self.assertEqual(len(left_out), 2)
        self.assertTrue(all("has no start date" in note for note in left_out))

    def test_without_the_history_operator_the_rows_are_marked_and_the_warning_names_the_sprint(self):
        self.gateway.reject_history_search = True
        result = self.agile.velocity.execute()
        self.assertEqual([(row.id, row.history_complete) for row in result.rows], [(6, False), (5, False)])
        self.assertEqual(result.left_out, [])
        self.assertTrue(any(note.startswith("Sprint 6: ") and "lower bounds" in note for note in result.notes))

    def test_the_rows_of_a_complete_history_are_not_marked(self):
        self.gateway.sprint_members[5] = []  # the history of PROJ-3 knows sprint 6 only: in sprint 5 it would not end where the issue is
        result = self.agile.velocity.execute()
        self.assertEqual([(row.id, row.history_complete) for row in result.rows], [(6, True), (5, True)])
        self.assertEqual(result.left_out, [])
        self.assertFalse(any("lower bounds" in note for note in result.notes))

    def test_the_points_of_three_issues_of_a_tenth_are_three_tenths(self):
        for key in ("PROJ-1", "PROJ-3"):
            self.gateway.sprint_history[key] = dataclasses.replace(self.gateway.sprint_history[key], points=0.1)
        self.gateway.sprint_removed[6] = [dataclasses.replace(self.gateway.sprint_removed[6][0], points=0.1)]
        self.assertEqual(self.agile.velocity.execute(last=1).rows[0].committed_points, 0.3)
        self.assertEqual(self.agile.show_sprint.execute("6", review=True).review.committed.points, 0.3)

    def test_the_notes_say_where_the_numbers_come_from(self):
        result = self.agile.velocity.execute()
        self.assertTrue(any("change history" in note for note in result.notes))


class BacklogAndEpicsTest(AgileCase):
    def test_the_backlog_in_rank_order(self):
        view = self.agile.show_backlog.execute(project="PROJ", limit=2)
        self.assertEqual(([issue.key for issue in view.issues], view.total), (["PROJ-4"], 1))  # not in the active sprint, not done, no epic
        self.assertEqual(view.board.id, 1200)
        self.assertEqual(self.gateway.called("board_backlog")[0][1:], (1200, 2))

    def test_the_backlog_has_no_people_and_a_limit(self):
        view = self.agile.show_backlog.execute(board=1200)
        self.assertTrue(all(issue.assignee is None for issue in view.issues))
        with self.assertRaises(UsageError):
            self.agile.show_backlog.execute(limit=0)

    def test_a_backlog_may_be_read_of_a_kanban_board(self):
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("PROJ",))
        self.assertEqual(self.agile.show_backlog.execute(board=1300).board.type, "kanban")

    def test_epics_without_the_done_ones(self):
        self.gateway.epics["PROJ-9"] = dataclasses.replace(self.gateway.epics["PROJ-9"], done=True)
        self.assertEqual([epic.key for epic in self.agile.list_epics.execute(project="PROJ").epics], ["PROJ-8"])
        self.assertEqual([epic.key for epic in self.agile.list_epics.execute(project="PROJ", include_done=True).epics], ["PROJ-8", "PROJ-9"])

    def test_epic_names_are_redacted(self):
        self.gateway.epics["PROJ-8"] = dataclasses.replace(self.gateway.epics["PROJ-8"], summary="Mail jane.doe@example.com")
        self.assertEqual(self.agile.list_epics.execute().epics[0].summary, "Mail [email]")


class AddToSprintTest(AgileCase):
    def test_the_issues_are_read_first_and_a_dry_run_sends_nothing(self):
        result = self.agile.add_to_sprint.execute("proj-4, PROJ-1", "Sprint 8", dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual((result.keys, result.skipped, result.payload, result.requests), (["PROJ-4", "PROJ-1"], [], {"issues": ["PROJ-4", "PROJ-1"]}, 1))
        self.assertEqual(self.gateway.called("move_issues_to_sprint"), [])
        self.assertEqual([call[1] for call in self.gateway.called("get_issue")], ["PROJ-4", "PROJ-1"])
        self.assertEqual((result.read_back, result.confirmed, result.missing), (False, None, None))  # nothing was checked: not "nothing is missing"

    def test_moving_reads_the_sprint_back(self):
        result = self.agile.add_to_sprint.execute("PROJ-4 PROJ-1", "8")
        self.assertFalse(result.dry_run)
        self.assertEqual(self.gateway.called("move_issues_to_sprint")[0][1:], (8, {"issues": ["PROJ-4", "PROJ-1"]}))
        self.assertEqual((result.confirmed, result.missing), (["PROJ-1", "PROJ-4"], []))  # in the order of the rank
        self.assertTrue(result.read_back)
        self.assertEqual(self.gateway.sprint_members[8], ["PROJ-4", "PROJ-1"])
        self.assertEqual(result.sprint.name, "Sprint 8")

    def test_an_issue_that_is_in_the_sprint_already_is_not_sent(self):
        result = self.agile.add_to_sprint.execute("PROJ-1 PROJ-4", "7")
        self.assertEqual((result.keys, result.skipped, result.payload), (["PROJ-4"], ["PROJ-1"], {"issues": ["PROJ-4"]}))

    def test_nothing_to_send(self):
        result = self.agile.add_to_sprint.execute("PROJ-1", "7")
        self.assertEqual((result.keys, result.requests, result.payload), ([], 0, {"issues": []}))
        self.assertEqual((result.read_back, result.confirmed, result.missing), (False, None, None))  # nothing was sent, so nothing was read back
        self.assertEqual(self.gateway.called("list_sprint_issues"), [])
        self.assertEqual(self.gateway.called("move_issues_to_sprint"), [])
        self.assertTrue(any("already in the sprint" in note for note in result.notes))

    def test_a_note_for_every_issue_that_leaves_another_sprint(self):
        notes = self.agile.add_to_sprint.execute("PROJ-1", "Sprint 8", dry_run=True).notes
        self.assertEqual(notes, ["PROJ-1 leaves Sprint 7 (active)."])  # the closed sprint stays in the history of the issue

    def test_more_than_fifty_issues_go_in_batches_of_fifty(self):
        for number in range(100, 220):
            self.gateway.issues[f"PROJ-{number}"] = sample_issue(f"PROJ-{number}", sprints=[])
        keys = " ".join(f"PROJ-{number}" for number in range(100, 220))
        result = self.agile.add_to_sprint.execute(keys, "8")
        self.assertEqual(result.requests, 3)
        self.assertEqual([len(call[2]["issues"]) for call in self.gateway.called("move_issues_to_sprint")], [50, 50, 20])
        self.assertEqual((len(result.confirmed), result.missing), (120, []))
        self.assertEqual(result.payload, {"issues": [f"PROJ-{number}" for number in range(100, 220)]})

    def test_a_failure_in_a_later_batch_says_what_was_already_moved(self):
        for number in range(100, 160):
            self.gateway.issues[f"PROJ-{number}"] = sample_issue(f"PROJ-{number}", sprints=[])
        original = self.gateway.move_issues_to_sprint
        calls = []

        def flaky(sprint_id, payload):
            calls.append(payload)
            if len(calls) == 2:
                raise RejectedError("Jira refused the request (HTTP 400): too many")
            original(sprint_id, payload)

        self.gateway.move_issues_to_sprint = flaky
        with self.assertRaises(RejectedError) as caught:
            self.agile.add_to_sprint.execute(" ".join(f"PROJ-{number}" for number in range(100, 160)), "8")
        self.assertIn("50 of 60 issues were already changed", caught.exception.message)
        self.assertEqual(len(caught.exception.details["changed"]), 50)

    def test_a_failure_in_the_first_batch_is_not_wrapped_because_nothing_was_changed(self):
        for number in range(100, 160):
            self.gateway.issues[f"PROJ-{number}"] = sample_issue(f"PROJ-{number}", sprints=[])
        self.gateway.errors["move_issues_to_sprint"] = RejectedError("Jira refused the request (HTTP 400): too many")
        with self.assertRaises(RejectedError) as caught:
            self.agile.add_to_sprint.execute(" ".join(f"PROJ-{number}" for number in range(100, 160)), "8")
        self.assertEqual(caught.exception.message, "Jira refused the request (HTTP 400): too many")
        self.assertNotIn("changed", caught.exception.details)
        self.assertEqual(len(self.gateway.called("move_issues_to_sprint")), 1)  # and nothing else was sent

    def test_an_old_key_of_an_issue_that_moved_is_sent_checked_and_read_back_under_its_new_key(self):
        self.gateway.issues["OLD-5"] = self.gateway.issues["PROJ-4"]  # Jira answers the old key with the issue as it is now
        dry = self.agile.add_to_sprint.execute("OLD-5", "8", dry_run=True)
        self.assertEqual((dry.keys, dry.payload), (["PROJ-4"], {"issues": ["PROJ-4"]}))
        self.assertEqual(dry.notes, ["OLD-5 is now PROJ-4."])
        result = self.agile.add_to_sprint.execute("OLD-5", "8")
        self.assertEqual(self.gateway.called("move_issues_to_sprint")[0][1:], (8, {"issues": ["PROJ-4"]}))
        self.assertEqual((result.keys, result.confirmed, result.missing, result.read_back), (["PROJ-4"], ["PROJ-4"], [], True))  # no false "not found"
        self.assertEqual(self.gateway.called("list_sprint_issues")[0][2], ("PROJ-4",))

    def test_a_moved_issue_that_is_in_the_sprint_already_is_not_sent_and_one_issue_under_two_keys_is_sent_once(self):
        self.gateway.issues["OLD-1"] = self.gateway.issues["PROJ-1"]
        self.gateway.issues["OLD-4"] = self.gateway.issues["PROJ-4"]
        result = self.agile.add_to_sprint.execute("OLD-1 PROJ-4 OLD-4", "7", dry_run=True)
        self.assertEqual((result.keys, result.skipped, result.payload), (["PROJ-4"], ["PROJ-1"], {"issues": ["PROJ-4"]}))
        self.assertEqual(result.notes[:2], ["OLD-1 is now PROJ-1.", "OLD-4 is now PROJ-4."])

    def test_an_unknown_issue_fails_before_anything_is_sent(self):
        with self.assertRaises(NotFoundError):
            self.agile.add_to_sprint.execute("PROJ-4 PROJ-999", "8")
        self.assertEqual(self.gateway.called("move_issues_to_sprint"), [])

    def test_a_closed_sprint_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.agile.add_to_sprint.execute("PROJ-4", "6")
        self.assertIn("closed", caught.exception.message)
        self.assertEqual(self.gateway.called("get_issue"), [])

    def test_if_state_protects_an_active_sprint(self):
        with self.assertRaises(ConflictError):
            self.agile.add_to_sprint.execute("PROJ-4", "7", if_state="future")
        self.assertEqual(self.agile.add_to_sprint.execute("PROJ-4", "7", if_state="active,future", dry_run=True).keys, ["PROJ-4"])

    def test_if_unchanged_since(self):
        with self.assertRaises(ConflictError):
            self.agile.add_to_sprint.execute("PROJ-4", "8", if_unchanged_since="2026-10-01T00:00:00Z")  # the issue changed on 2026-10-10
        self.assertEqual(self.agile.add_to_sprint.execute("PROJ-4", "8", if_unchanged_since="2026-10-11T00:00:00Z", dry_run=True).keys, ["PROJ-4"])

    def test_a_refusal_says_which_permission_is_missing(self):
        self.gateway.errors["move_issues_to_sprint"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.agile.add_to_sprint.execute("PROJ-4", "8")
        self.assertIn("Schedule Issues", caught.exception.hint)
        self.gateway.errors["move_issues_to_sprint"] = AuthError("Jira did not accept the token (HTTP 401).", hint="new token", details={"status": 401})
        with self.assertRaises(AuthError) as caught:
            self.agile.add_to_sprint.execute("PROJ-4", "8")
        self.assertEqual(caught.exception.hint, "new token")

    def test_what_is_not_found_when_read_back_is_reported(self):
        self.gateway.list_sprint_issues = lambda sprint_id, *, keys=(): []
        result = self.agile.add_to_sprint.execute("PROJ-4", "8")
        self.assertEqual((result.confirmed, result.missing, result.read_back), ([], ["PROJ-4"], True))

    def test_a_read_back_that_fails_does_not_hide_that_the_move_was_made(self):
        self.gateway.errors["list_sprint_issues"] = UnavailableError("Jira is down.")
        result = self.agile.add_to_sprint.execute("PROJ-4", "8")
        self.assertEqual(len(self.gateway.called("move_issues_to_sprint")), 1)
        self.assertEqual((result.confirmed, result.missing, result.read_back), (None, None, False))  # not checked, which is not "nothing is missing"
        self.assertTrue(any("moved, but they could not be read back: Jira is down." in note for note in result.notes))

    def test_a_dry_run_reads_nothing_back(self):
        result = self.agile.add_to_sprint.execute("PROJ-4", "8", dry_run=True)
        self.assertEqual((result.read_back, result.confirmed, result.missing), (False, None, None))
        self.assertEqual(self.gateway.called("list_sprint_issues"), [])

    def test_no_keys_and_a_bad_key(self):
        with self.assertRaises(UsageError):
            self.agile.add_to_sprint.execute("  ", "8")
        with self.assertRaises(ValidationError):
            self.agile.add_to_sprint.execute("nonsense", "8")


class RankTest(AgileCase):
    def test_dry_run(self):
        result = self.agile.rank_issues.execute("PROJ-4 PROJ-3", before="PROJ-1", dry_run=True)
        self.assertEqual((result.position, result.anchor, result.payload), ("before", "PROJ-1", {"issues": ["PROJ-4", "PROJ-3"], "rankBeforeIssue": "PROJ-1"}))
        self.assertEqual(self.gateway.called("rank_issues"), [])
        self.assertEqual(sorted(call[1] for call in self.gateway.called("get_issue")), ["PROJ-1", "PROJ-3", "PROJ-4"])
        self.assertEqual((result.read_back, result.order, result.confirmed), (False, None, None))  # the order was not read: not an empty one
        self.assertEqual(self.gateway.called("rank_order"), [])

    def test_before_is_read_back(self):
        result = self.agile.rank_issues.execute("PROJ-4 PROJ-3", before="PROJ-1")
        self.assertEqual((result.order, result.confirmed, result.read_back), (["PROJ-4", "PROJ-3", "PROJ-1"], True, True))

    def test_after_is_read_back(self):
        result = self.agile.rank_issues.execute("PROJ-1", after="PROJ-3")
        self.assertEqual((result.position, result.order, result.confirmed), ("after", ["PROJ-3", "PROJ-1"], True))

    def test_an_order_that_is_not_as_asked_is_reported(self):
        self.gateway.rank_order = lambda keys: ["PROJ-1", "PROJ-4", "PROJ-3"]
        result = self.agile.rank_issues.execute("PROJ-4 PROJ-3", before="PROJ-1")
        self.assertEqual((result.confirmed, result.read_back), (False, True))  # read, and not as asked
        self.assertTrue(any("do not stand where they were asked" in note for note in result.notes))

    def test_an_order_that_comes_back_empty_was_read_all_the_same(self):
        self.gateway.rank_order = lambda keys: []
        result = self.agile.rank_issues.execute("PROJ-4", before="PROJ-1")
        self.assertEqual((result.order, result.confirmed, result.read_back), ([], False, True))  # [] is a finding, None is no finding

    def test_a_read_back_that_fails_does_not_hide_the_write(self):
        self.gateway.errors["rank_order"] = UnavailableError("down")
        result = self.agile.rank_issues.execute("PROJ-4", before="PROJ-1")
        self.assertEqual((result.order, result.confirmed, result.read_back), (None, None, False))
        self.assertEqual(len(self.gateway.called("rank_issues")), 1)
        self.assertTrue(any("could not be read back: down" in note for note in result.notes))

    def test_the_rules(self):
        with self.assertRaises(UsageError):
            self.agile.rank_issues.execute("PROJ-4")
        with self.assertRaises(ValidationError):
            self.agile.rank_issues.execute("PROJ-4 PROJ-1", before="PROJ-1")
        with self.assertRaises(NotFoundError):
            self.agile.rank_issues.execute("PROJ-4", after="PROJ-999")
        self.assertEqual(self.gateway.called("rank_issues"), [])

    def test_fifty_issues_are_ranked_and_fifty_one_are_refused_before_anything_is_read(self):
        for number in range(100, 151):
            self.gateway.issues[f"PROJ-{number}"] = sample_issue(f"PROJ-{number}", sprints=[])
        keys = [f"PROJ-{number}" for number in range(100, 151)]
        result = self.agile.rank_issues.execute(" ".join(keys[:50]), before="PROJ-1")
        self.assertEqual((len(result.keys), result.confirmed), (50, True))
        self.assertEqual(len(self.gateway.called("rank_issues")[0][1]["issues"]), 50)
        reads = len(self.gateway.called("get_issue"))
        with self.assertRaises(ValidationError) as caught:
            self.agile.rank_issues.execute(" ".join(keys), before="PROJ-1")
        self.assertIn("at most 50", caught.exception.message)
        self.assertEqual((len(self.gateway.called("get_issue")), len(self.gateway.called("rank_issues"))), (reads, 1))

    def test_one_issue_under_its_old_and_its_new_key_is_ranked_once(self):
        self.gateway.issues["OLD-4"] = self.gateway.issues["PROJ-4"]
        result = self.agile.rank_issues.execute("OLD-4 PROJ-4 PROJ-3", before="PROJ-1", dry_run=True)
        self.assertEqual((result.keys, result.payload["issues"]), (["PROJ-4", "PROJ-3"], ["PROJ-4", "PROJ-3"]))

    def test_an_old_key_of_a_moved_issue_is_ranked_under_its_new_key_whether_it_moves_or_is_the_anchor(self):
        self.gateway.issues["OLD-4"] = self.gateway.issues["PROJ-4"]
        self.gateway.issues["OLD-1"] = self.gateway.issues["PROJ-1"]
        dry = self.agile.rank_issues.execute("OLD-4 PROJ-3", before="OLD-1", dry_run=True)
        self.assertEqual((dry.keys, dry.anchor, dry.payload), (["PROJ-4", "PROJ-3"], "PROJ-1", {"issues": ["PROJ-4", "PROJ-3"], "rankBeforeIssue": "PROJ-1"}))
        self.assertEqual(dry.notes, ["OLD-4 is now PROJ-4.", "OLD-1 is now PROJ-1."])
        result = self.agile.rank_issues.execute("OLD-4", after="OLD-1")
        self.assertEqual(self.gateway.called("rank_issues")[0][1], {"issues": ["PROJ-4"], "rankAfterIssue": "PROJ-1"})
        self.assertEqual((result.order, result.confirmed), (["PROJ-1", "PROJ-4"], True))  # checked against the keys that were sent

    def test_a_refusal_says_which_permission_is_missing(self):
        self.gateway.errors["rank_issues"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.agile.rank_issues.execute("PROJ-4", before="PROJ-1")
        self.assertIn("Schedule Issues", caught.exception.hint)

    def test_if_unchanged_since_covers_the_anchor_too(self):
        with self.assertRaises(ConflictError):
            self.agile.rank_issues.execute("PROJ-4", before="PROJ-1", if_unchanged_since="2026-10-01T00:00:00Z", dry_run=True)


class EpicTest(AgileCase):
    def test_dry_run(self):
        result = self.agile.add_to_epic.execute("PROJ-4 PROJ-1", "proj-8", dry_run=True)
        self.assertEqual((result.target, result.name, result.keys, result.skipped), ("PROJ-8", "Epic eight", ["PROJ-4", "PROJ-1"], []))
        self.assertEqual(result.payload, {"issues": ["PROJ-4", "PROJ-1"]})
        self.assertEqual(self.gateway.called("move_issues_to_epic"), [])
        self.assertEqual(self.gateway.called("get_epic")[0][1], "PROJ-8")
        self.assertIn("PROJ-1 leaves epic PROJ-9.", result.notes)
        self.assertEqual((result.read_back, result.confirmed, result.missing), (False, None, None))
        self.assertEqual(self.gateway.called("list_epic_issues"), [])

    def test_moving_reads_the_epic_back(self):
        result = self.agile.add_to_epic.execute("PROJ-4", "PROJ-8")
        self.assertEqual(self.gateway.called("move_issues_to_epic")[0][1:], ("PROJ-8", {"issues": ["PROJ-4"]}))
        self.assertEqual((result.confirmed, result.missing, result.read_back), (["PROJ-4"], [], True))
        self.assertEqual(self.gateway.issues["PROJ-4"].epic_key, "PROJ-8")

    def test_out_of_the_epic(self):
        result = self.agile.add_to_epic.execute("PROJ-1", "none")
        self.assertEqual((result.target, result.keys, result.name), ("none", ["PROJ-1"], ""))
        self.assertEqual(self.gateway.called("move_issues_to_epic")[0][1:], ("none", {"issues": ["PROJ-1"]}))
        self.assertEqual((result.confirmed, self.gateway.issues["PROJ-1"].epic_key), (["PROJ-1"], ""))
        self.assertEqual(self.gateway.called("get_epic"), [])

    def test_an_issue_that_is_there_already_is_not_sent(self):
        result = self.agile.add_to_epic.execute("PROJ-1 PROJ-4", "PROJ-9", dry_run=True)
        self.assertEqual((result.keys, result.skipped), (["PROJ-4"], ["PROJ-1"]))
        none = self.agile.add_to_epic.execute("PROJ-4", "none", dry_run=True)
        self.assertEqual((none.keys, none.skipped), ([], ["PROJ-4"]))

    def test_nothing_to_send_means_nothing_was_read_back(self):
        result = self.agile.add_to_epic.execute("PROJ-1", "PROJ-9")  # PROJ-1 is in PROJ-9 already
        self.assertEqual((result.keys, result.skipped, result.requests), ([], ["PROJ-1"], 0))
        self.assertEqual((result.read_back, result.confirmed, result.missing), (False, None, None))
        self.assertEqual((self.gateway.called("move_issues_to_epic"), self.gateway.called("list_epic_issues")), ([], []))

    def test_without_an_epic_link_field_no_issue_is_taken_for_one_that_is_there(self):
        self.gateway.field_ids = lambda: {"story_points": "customfield_10001", "epic_link": None, "sprint": None, "acceptance": None}
        self.assertEqual(self.agile.add_to_epic.execute("PROJ-4", "none", dry_run=True).keys, ["PROJ-4"])

    def test_an_unknown_epic_and_an_epic_in_an_epic(self):
        with self.assertRaises(NotFoundError):
            self.agile.add_to_epic.execute("PROJ-4", "PROJ-77")
        with self.assertRaises(ValidationError):
            self.agile.add_to_epic.execute("PROJ-8", "PROJ-9")  # PROJ-8 is an epic
        with self.assertRaises(ValidationError):
            self.agile.add_to_epic.execute("PROJ-9", "PROJ-9")
        with self.assertRaises(UsageError):
            self.agile.add_to_epic.execute("PROJ-4", " ")
        self.assertEqual(self.gateway.called("move_issues_to_epic"), [])

    def test_a_done_epic_is_named(self):
        self.gateway.epics["PROJ-8"] = dataclasses.replace(self.gateway.epics["PROJ-8"], done=True)
        self.assertIn("Epic PROJ-8 is done.", self.agile.add_to_epic.execute("PROJ-4", "PROJ-8", dry_run=True).notes)

    def test_a_read_back_that_fails_does_not_hide_that_the_move_was_made(self):
        self.gateway.errors["list_epic_issues"] = UnavailableError("Jira is down.")
        result = self.agile.add_to_epic.execute("PROJ-4", "PROJ-8")
        self.assertEqual(len(self.gateway.called("move_issues_to_epic")), 1)
        self.assertEqual((result.confirmed, result.missing, result.read_back), (None, None, False))
        self.assertTrue(any("could not be read back" in note for note in result.notes))

    def test_an_old_key_of_an_issue_that_moved_is_sent_and_read_back_under_its_new_key(self):
        self.gateway.issues["OLD-4"] = self.gateway.issues["PROJ-4"]
        dry = self.agile.add_to_epic.execute("OLD-4", "PROJ-8", dry_run=True)
        self.assertEqual((dry.keys, dry.payload, dry.notes), (["PROJ-4"], {"issues": ["PROJ-4"]}, ["OLD-4 is now PROJ-4."]))
        result = self.agile.add_to_epic.execute("OLD-4", "PROJ-8")
        self.assertEqual(self.gateway.called("move_issues_to_epic")[0][1:], ("PROJ-8", {"issues": ["PROJ-4"]}))
        self.assertEqual((result.confirmed, result.missing, result.read_back), (["PROJ-4"], [], True))
        self.gateway.issues["OLD-1"] = self.gateway.issues["PROJ-1"]
        there = self.agile.add_to_epic.execute("OLD-1", "PROJ-9", dry_run=True)  # PROJ-1 is in PROJ-9 already
        self.assertEqual((there.keys, there.skipped), ([], ["PROJ-1"]))

    def test_a_failure_in_the_first_batch_is_not_wrapped_because_nothing_was_changed(self):
        for number in range(100, 160):
            self.gateway.issues[f"PROJ-{number}"] = sample_issue(f"PROJ-{number}", sprints=[])
        self.gateway.errors["move_issues_to_epic"] = RejectedError("Jira refused the request (HTTP 400): nope")
        with self.assertRaises(RejectedError) as caught:
            self.agile.add_to_epic.execute(" ".join(f"PROJ-{number}" for number in range(100, 160)), "PROJ-8")
        self.assertEqual(caught.exception.message, "Jira refused the request (HTTP 400): nope")
        self.assertNotIn("changed", caught.exception.details)
        self.assertEqual(len(self.gateway.called("move_issues_to_epic")), 1)

    def test_permission_and_batches(self):
        self.gateway.errors["move_issues_to_epic"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.agile.add_to_epic.execute("PROJ-4", "PROJ-8")
        self.assertIn("Edit Issues", caught.exception.hint)


class SprintWritesTest(AgileCase):
    def test_create_dry_run_sends_nothing(self):
        result = self.agile.create_sprint.execute("Sprint 9", 1200, goal="Go", start="2026-11-02", end="2026-11-16", dry_run=True)
        self.assertEqual(
            result.payload,
            {"name": "Sprint 9", "originBoardId": 1200, "goal": "Go", "startDate": "2026-11-02T00:00:00.000+00:00", "endDate": "2026-11-16T00:00:00.000+00:00"},
        )
        self.assertEqual((result.dry_run, result.sprint), (True, None))
        self.assertEqual(self.gateway.called("create_sprint"), [])
        self.assertEqual(self.gateway.called("get_board")[0][1], 1200)

    def test_create_reads_the_sprint_back(self):
        result = self.agile.create_sprint.execute("Sprint 9", 1200)
        self.assertEqual((result.sprint.name, result.sprint.state, result.sprint.board_id), ("Sprint 9", "future", 1200))
        self.assertEqual(self.gateway.called("create_sprint")[0][1], {"name": "Sprint 9", "originBoardId": 1200})
        self.assertEqual(self.gateway.called("get_sprint")[0][1], result.sprint.id)
        self.assertEqual((result.read_back, result.notes), (True, []))

    def test_a_create_that_cannot_be_read_back_was_done_and_says_so_without_advising_a_second_try(self):
        self.gateway.errors["get_sprint"] = UnavailableError("Jira answered with a server error (HTTP 503).")
        result = self.agile.create_sprint.execute("Sprint 9", 1200)
        self.assertEqual(len(self.gateway.called("create_sprint")), 1)
        self.assertEqual((result.dry_run, result.read_back), (False, False))
        self.assertEqual((result.sprint.id, result.sprint.name, result.sprint.state), (101, "Sprint 9", "future"))  # what the create answered
        self.assertEqual(len(result.notes), 1)
        self.assertIn("The sprint was created (id 101), but it could not be read back: Jira answered with a server error (HTTP 503).", result.notes[0])
        self.assertIn("Do not send the command again", result.notes[0])
        self.assertIn("`jira sprint 101`", result.notes[0])
        self.assertNotRegex(result.notes[0], r"(?i)try again|retry")
        self.assertIn(101, self.gateway.sprints)

    def test_create_refuses_a_duplicate_a_kanban_board_and_a_wrong_order(self):
        with self.assertRaises(ValidationError) as caught:
            self.agile.create_sprint.execute(" sprint  8 ", 1200)
        self.assertIn("duplicate", caught.exception.hint)
        self.gateway.boards[1300] = Board(1300, "Flow board", "kanban", ("PROJ",))
        with self.assertRaises(ValidationError):
            self.agile.create_sprint.execute("Sprint 9", 1300)
        with self.assertRaises(NotFoundError):
            self.agile.create_sprint.execute("Sprint 9", 999)
        with self.assertRaises(ValidationError):
            self.agile.create_sprint.execute("Sprint 9", 1200, start="2026-11-16", end="2026-11-02")
        self.assertEqual(self.gateway.called("create_sprint"), [])

    def test_a_closed_sprint_with_the_same_name_is_no_duplicate(self):
        self.assertEqual(self.agile.create_sprint.execute("Sprint 6", 1200, dry_run=True).payload["name"], "Sprint 6")

    def test_create_permission(self):
        self.gateway.errors["create_sprint"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.agile.create_sprint.execute("Sprint 9", 1200)
        self.assertIn("Manage Sprints", caught.exception.hint)

    def test_update_dry_run_shows_the_partial_payload(self):
        result = self.agile.update_sprint.execute("Sprint 8", name="Sprint 8b", goal="New goal", dry_run=True)
        self.assertEqual((result.payload, result.dry_run, result.sprint, result.before.name), ({"name": "Sprint 8b", "goal": "New goal"}, True, None, "Sprint 8"))
        self.assertEqual(self.gateway.called("update_sprint"), [])

    def test_update_is_read_back(self):
        result = self.agile.update_sprint.execute("8", name="Sprint 8b")
        self.assertEqual((result.sprint.name, result.before.name), ("Sprint 8b", "Sprint 8"))
        self.assertEqual(self.gateway.called("update_sprint")[0][1:], (8, {"name": "Sprint 8b"}))
        self.assertEqual((result.read_back, result.notes), (True, []))

    def test_an_update_that_cannot_be_read_back_was_done_and_says_so_without_advising_a_second_try(self):
        read = self.gateway.get_sprint
        calls = []

        def second_read_fails(sprint_id):
            calls.append(sprint_id)
            if len(calls) > 1:  # the first read finds the sprint, the second one is the read-back
                raise UnavailableError("Jira answered with a server error (HTTP 503).")
            return read(sprint_id)

        self.gateway.get_sprint = second_read_fails
        result = self.agile.update_sprint.execute("8", name="Sprint 8b", goal="New goal")
        self.assertEqual(calls, [8, 8])
        self.assertEqual(len(self.gateway.called("update_sprint")), 1)
        self.assertEqual((result.dry_run, result.read_back), (False, False))
        self.assertEqual((result.sprint.name, result.sprint.goal, result.before.name), ("Sprint 8b", "New goal", "Sprint 8"))  # what the update answered
        self.assertEqual(len(result.notes), 1)
        self.assertIn("The sprint was changed (id 8), but it could not be read back: Jira answered with a server error (HTTP 503).", result.notes[0])
        self.assertIn("Do not send the command again", result.notes[0])
        self.assertIn("`jira sprint 8`", result.notes[0])
        self.assertNotRegex(result.notes[0], r"(?i)try again|retry")

    def test_the_note_of_a_completion_stays_when_the_read_back_fails(self):
        read = self.gateway.get_sprint
        calls = []

        def second_read_fails(sprint_id):
            calls.append(sprint_id)
            if len(calls) > 1:
                raise UnavailableError("down.")
            return read(sprint_id)

        self.gateway.get_sprint = second_read_fails
        result = self.agile.update_sprint.execute("7", state="closed")
        self.assertEqual(result.sprint.state, "closed")
        self.assertTrue(result.notes[0].startswith("This command does not move the open issues"))
        self.assertIn("could not be read back: down.", result.notes[1])  # the full stop of the reason is not doubled

    def test_start_with_dates_and_now(self):
        result = self.agile.update_sprint.execute("8", state="active", start="now", end="2026-11-03")
        self.assertEqual(result.payload, {"startDate": "2026-10-20T12:00:00.000+00:00", "endDate": "2026-11-03T00:00:00.000+00:00", "state": "active"})
        self.assertEqual(result.sprint.state, "active")

    def test_start_without_dates_is_refused_before_anything_is_sent(self):
        with self.assertRaises(ValidationError):
            self.agile.update_sprint.execute("8", state="active")
        self.assertEqual(self.gateway.called("update_sprint"), [])

    def test_complete_says_that_the_open_issues_stay(self):
        result = self.agile.update_sprint.execute("7", state="closed", if_state="active", dry_run=True)
        self.assertEqual(result.payload, {"state": "closed"})
        self.assertTrue(any("does not move the open issues" in note for note in result.notes))

    def test_if_state_and_closed_sprints(self):
        with self.assertRaises(ConflictError):
            self.agile.update_sprint.execute("7", state="closed", if_state="future")
        with self.assertRaises(ValidationError):
            self.agile.update_sprint.execute("6", name="Other")

    def test_update_permission(self):
        self.gateway.errors["update_sprint"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as caught:
            self.agile.update_sprint.execute("8", name="Other")
        self.assertIn("Manage Sprints", caught.exception.hint)


if __name__ == "__main__":
    unittest.main()
