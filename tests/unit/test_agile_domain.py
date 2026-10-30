"""Boards, sprints and epics: references, picking a board, finding a sprint by name, the life cycle and the payloads."""
import unittest
from datetime import datetime, timezone

from jira_dc_cli.domain import agile
from jira_dc_cli.domain.agile import Board, SprintInfo
from jira_dc_cli.domain.errors import ConflictError, NotFoundError, UsageError, ValidationError

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def sprint(ident, name, state="future", start="", end="", complete="", goal=""):
    return SprintInfo(ident, name, state, start, end, complete, goal, 1200)


SPRINTS = [
    sprint(5, "Sprint 5", "closed", "2026-08-03T08:00:00.000+02:00", "2026-08-17T08:00:00.000+02:00", "2026-08-17T16:00:00.000+02:00"),
    sprint(6, "Sprint 6", "closed", "2026-08-17T08:00:00.000+02:00", "2026-08-31T08:00:00.000+02:00", "2026-08-31T16:00:00.000+02:00"),
    sprint(7, "Sprint 7", "active", "2026-10-12T08:00:00.000+02:00", "2026-10-26T08:00:00.000+02:00"),
    sprint(8, "Sprint 8", "future"),
    sprint(9, "Sprint 9", "future"),
]


class ReferencesTest(unittest.TestCase):
    def test_board_id(self):
        self.assertEqual(agile.parse_board_id(" 1200 "), 1200)
        for bad in ("0", "-3", "abc", "", "1.5", "+5", "1_0", "\u0663"):  # an id is written with the digits 0 to 9 and nothing else
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                agile.parse_board_id(bad)
        self.assertEqual(agile.parse_board_id(7), 7)

    def test_keys_are_upper_case_unique_and_in_order(self):
        self.assertEqual(agile.split_keys("proj-2 PROJ-1,proj-2;  PROJ-10"), ["PROJ-2", "PROJ-1", "PROJ-10"])

    def test_no_key_and_a_bad_key(self):
        with self.assertRaises(UsageError):
            agile.split_keys("  ")
        with self.assertRaises(ValidationError) as caught:
            agile.split_keys("PROJ-1 oops")
        self.assertIn("oops", caught.exception.message)

    def test_a_sprint_is_an_id_or_a_name(self):
        self.assertEqual(agile.parse_sprint_ref(" 12 "), 12)
        self.assertEqual(agile.parse_sprint_ref("Sprint   12"), "Sprint 12")
        self.assertEqual(agile.parse_sprint_ref("2026 Q4"), "2026 Q4")
        with self.assertRaises(UsageError):
            agile.parse_sprint_ref("   ")

    def test_a_sprint_id_of_thousands_of_digits_is_a_usage_error_and_never_a_crash(self):
        huge = "9" * 5000  # `int()` refuses more than 4300 digits
        with self.assertRaises(UsageError) as caught:
            agile.parse_sprint_ref(huge)
        self.assertLess(len(caught.exception.message), 200)  # the text is not echoed whole
        self.assertIn("digits", caught.exception.hint)
        longest = "9" * agile.MAX_ID_DIGITS
        self.assertEqual(agile.parse_sprint_ref(longest), int(longest))
        with self.assertRaises(UsageError):
            agile.parse_sprint_ref("9" * (agile.MAX_ID_DIGITS + 1))
        name = "Sprint " + "9" * 5000
        self.assertEqual(agile.parse_sprint_ref(name), name)  # a text that holds more than digits is a name

    def test_a_board_id_of_thousands_of_digits_is_refused_and_never_a_crash(self):
        with self.assertRaises(ValidationError) as caught:
            agile.parse_board_id("9" * 5000)
        self.assertLess(len(caught.exception.message), 100)  # the text is not echoed whole
        self.assertTrue(caught.exception.message.endswith("..."))
        self.assertEqual(agile.parse_board_id("9" * agile.MAX_ID_DIGITS), 10**agile.MAX_ID_DIGITS - 1)
        with self.assertRaises(ValidationError):
            agile.parse_board_id("9" * (agile.MAX_ID_DIGITS + 1))
        with self.assertRaises(ValidationError) as caught:
            agile.parse_board_id("abc")
        self.assertEqual(caught.exception.message, "Not a board id: abc")  # a short text is shown as it is

    def test_states(self):
        self.assertEqual(agile.parse_states("Active, closed,active"), ["active", "closed"])
        self.assertEqual(agile.parse_states(None), [])
        with self.assertRaises(ValidationError) as caught:
            agile.parse_states("active,done")
        self.assertIn("future, active, closed", caught.exception.hint)

    def test_board_type(self):
        self.assertEqual(agile.parse_board_type("Scrum"), "scrum")
        self.assertEqual(agile.parse_board_type(None), "")
        with self.assertRaises(ValidationError):
            agile.parse_board_type("agile")

    def test_a_moment_is_written_as_jira_writes_it(self):
        self.assertEqual(agile.normalize_moment("2026-10-12", NOW), "2026-10-12T00:00:00.000+00:00")
        self.assertEqual(agile.normalize_moment("2026-10-12T09:30", NOW), "2026-10-12T09:30:00.000+00:00")
        self.assertEqual(agile.normalize_moment("2026-10-12T09:30:15+0200", NOW), "2026-10-12T09:30:15.000+02:00")
        self.assertEqual(agile.normalize_moment("2026-10-12T09:30:15Z", NOW), "2026-10-12T09:30:15.000+00:00")
        self.assertEqual(agile.normalize_moment("NOW", NOW), "2026-10-20T12:00:00.000+00:00")

    def test_a_moment_that_is_no_date(self):
        for bad in ("", "  ", "tomorrow", "2026-13-40"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                agile.normalize_moment(bad, NOW)

    def test_a_date_that_was_not_given_stays_empty(self):
        self.assertEqual(agile.optional_moment("", NOW), "")
        self.assertEqual(agile.optional_moment("  ", NOW), "")
        self.assertEqual(agile.optional_moment("now", NOW), "2026-10-20T12:00:00.000+00:00")


class BoardsTest(unittest.TestCase):
    def test_one_board_is_chosen(self):
        board = Board(1, "A", "scrum")
        self.assertIs(agile.choose_board([board], "PROJ"), board)

    def test_no_board(self):
        with self.assertRaises(NotFoundError) as caught:
            agile.choose_board([], "PROJ")
        self.assertIn("PROJ", caught.exception.message)
        self.assertIn("jira boards", caught.exception.hint)

    def test_several_boards_are_listed_with_id_and_name(self):
        with self.assertRaises(ValidationError) as caught:
            agile.choose_board([Board(1, "Alpha", "scrum"), Board(2, "Beta", "scrum")], "PROJ")
        self.assertIn("1 'Alpha'", caught.exception.message)
        self.assertIn("2 'Beta'", caught.exception.message)
        self.assertIn("--board", caught.exception.hint)
        self.assertEqual([item["id"] for item in caught.exception.details["boards"]], [1, 2])

    def test_a_project_with_only_a_kanban_board_says_so(self):
        error = agile.no_sprint_board("PROJ", [Board(5, "Flow", "kanban")])
        self.assertIsInstance(error, ValidationError)
        self.assertIn("5 'Flow' (kanban)", error.message)
        self.assertIn("no sprints", error.message)
        self.assertIsInstance(agile.no_sprint_board("PROJ", []), NotFoundError)

    def test_a_kanban_board_has_no_sprints(self):
        with self.assertRaises(ValidationError) as caught:
            agile.require_sprints(Board(5, "Flow", "kanban"))
        self.assertIn("kanban", caught.exception.message)
        self.assertEqual(agile.require_sprints(Board(1, "A", "scrum")).id, 1)
        self.assertEqual(agile.require_sprints(Board(2, "Unknown type", "")).id, 2)


class SprintNamesTest(unittest.TestCase):
    def test_names_in_messages_and_details_are_redacted(self):
        odd = [sprint(1, "For jane.doe@example.com", "future")]
        with self.assertRaises(NotFoundError) as caught:
            agile.resolve_sprint(odd, "other")
        self.assertIn("For [email]", caught.exception.message)
        self.assertNotIn("jane.doe", caught.exception.message + str(caught.exception.details))
        self.assertEqual(agile.board_line(Board(1, "Board of [~jdoe]", "scrum")), "1 'Board of [~user]' (scrum)")
    def test_exact_ignoring_case_and_spaces(self):
        self.assertEqual(agile.resolve_sprint(SPRINTS, "sprint   7").id, 7)
        self.assertEqual(agile.resolve_sprint(SPRINTS, "SPRINT 9").id, 9)

    def test_unknown_lists_the_sprints_whose_name_holds_the_text(self):
        with self.assertRaises(NotFoundError) as caught:
            agile.resolve_sprint(SPRINTS, "Sprint")
        self.assertIn("No sprint named 'Sprint'", caught.exception.message)
        self.assertEqual(len(caught.exception.details["candidates"]), 5)
        self.assertIn("jira sprints", caught.exception.hint)

    def test_unknown_without_a_partial_match_lists_the_sprints_that_matter_now(self):
        with self.assertRaises(NotFoundError) as caught:
            agile.resolve_sprint(SPRINTS, "Release")
        ids = [item["id"] for item in caught.exception.details["candidates"]]
        self.assertEqual(ids[:3], [7, 8, 9])  # the active one, then the future ones, then the closed ones
        self.assertEqual(ids[3:], [6, 5])

    def test_unknown_on_a_board_without_sprints(self):
        with self.assertRaises(NotFoundError) as caught:
            agile.resolve_sprint([], "Sprint 1")
        self.assertIn("no sprints", caught.exception.message)

    def test_a_name_used_twice_is_ambiguous_and_lists_both(self):
        twice = [*SPRINTS, sprint(10, "sprint 7", "future")]
        with self.assertRaises(ValidationError) as caught:
            agile.resolve_sprint(twice, "Sprint 7")
        self.assertEqual({item["id"] for item in caught.exception.details["candidates"]}, {7, 10})
        self.assertIn("Use the id", caught.exception.hint)

    def test_at_most_ten_candidates(self):
        many = [sprint(number, f"Sprint {number}", "closed", complete=f"2026-01-{number:02d}") for number in range(1, 20)]
        with self.assertRaises(NotFoundError) as caught:
            agile.resolve_sprint(many, "Sprint")
        self.assertEqual(len(caught.exception.details["candidates"]), 10)

    def test_the_order_puts_what_matters_now_first(self):
        ordered = agile.order_sprints(list(reversed(SPRINTS)))
        self.assertEqual([item.id for item in ordered], [7, 9, 8, 6, 5])  # future keeps the order it came in; closed: the newest first

    def test_a_sprint_in_a_line(self):
        self.assertEqual(agile.sprint_line(SPRINTS[2]), "7 'Sprint 7' (active, 2026-10-12 to 2026-10-26)")
        self.assertEqual(agile.sprint_line(SPRINTS[3]), "8 'Sprint 8' (future)")


class StateGuardsTest(unittest.TestCase):
    def test_if_state(self):
        agile.check_state(SPRINTS[2], ["active"])
        agile.check_state(SPRINTS[2], [])
        agile.check_state(SPRINTS[3], ["active", "future"])
        with self.assertRaises(ConflictError) as caught:
            agile.check_state(SPRINTS[2], ["future"])
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(caught.exception.details["state"], "active")

    def test_issues_do_not_go_into_a_closed_sprint(self):
        agile.require_open(SPRINTS[2])
        agile.require_open(SPRINTS[3])
        with self.assertRaises(ValidationError) as caught:
            agile.require_open(SPRINTS[0])
        self.assertIn("closed", caught.exception.message)


class PayloadsTest(unittest.TestCase):
    def test_create(self):
        payload = agile.sprint_create_payload("  Sprint   10 ", 1200, goal=" Ship it ", start="2026-10-26T08:00:00.000+00:00", end="2026-11-09T08:00:00.000+00:00")
        self.assertEqual(
            payload,
            {"name": "Sprint 10", "originBoardId": 1200, "goal": "Ship it", "startDate": "2026-10-26T08:00:00.000+00:00", "endDate": "2026-11-09T08:00:00.000+00:00"},
        )
        self.assertEqual(agile.sprint_create_payload("S", 3), {"name": "S", "originBoardId": 3})

    def test_create_refuses_an_empty_name_and_an_end_before_the_start(self):
        with self.assertRaises(ValidationError):
            agile.sprint_create_payload("   ", 1)
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_create_payload("S", 1, start="2026-10-26T08:00:00.000+00:00", end="2026-10-20T08:00:00.000+00:00")
        self.assertIn("--end", caught.exception.hint)

    def test_an_end_that_equals_the_start_is_refused_and_one_millisecond_later_is_not(self):
        same = "2026-10-26T08:00:00.000+00:00"
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_create_payload("S", 1, start=same, end=same)
        self.assertIn("not after", caught.exception.message)
        payload = agile.sprint_create_payload("S", 1, start=same, end="2026-10-26T08:00:00.001+00:00")
        self.assertEqual(payload["endDate"], "2026-10-26T08:00:00.001+00:00")
        active = SPRINTS[2]  # from 2026-10-12T08:00+02:00 to 2026-10-26T08:00+02:00
        with self.assertRaises(ValidationError):
            agile.sprint_update_payload(active, end=active.start_date)
        with self.assertRaises(ValidationError):
            agile.sprint_update_payload(active, start=active.end_date)
        self.assertEqual(agile.sprint_update_payload(active, end="2026-10-12T08:00:00.001+02:00"), {"endDate": "2026-10-12T08:00:00.001+02:00"})

    def test_update_changes_only_what_is_given(self):
        future = SPRINTS[3]
        self.assertEqual(agile.sprint_update_payload(future, name=" New "), {"name": "New"})
        self.assertEqual(agile.sprint_update_payload(future, goal="  Goal "), {"goal": "Goal"})
        self.assertEqual(agile.sprint_update_payload(future, goal=""), {"goal": ""})  # an empty goal removes it
        self.assertEqual(agile.sprint_update_payload(future, start="2026-11-02T08:00:00.000+00:00"), {"startDate": "2026-11-02T08:00:00.000+00:00"})

    def test_update_with_nothing_to_change(self):
        with self.assertRaises(UsageError):
            agile.sprint_update_payload(SPRINTS[3])

    def test_a_closed_sprint_cannot_be_changed(self):
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_update_payload(SPRINTS[0], name="Other")
        self.assertIn("closed", caught.exception.message)

    def test_starting_needs_both_dates(self):
        future = SPRINTS[3]
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_update_payload(future, state="active")
        self.assertIn("--start, --end", caught.exception.message)
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_update_payload(future, state="active", start="2026-11-02T08:00:00.000+00:00")
        self.assertIn("missing: --end", caught.exception.message)
        payload = agile.sprint_update_payload(future, state="active", start="2026-11-02T08:00:00.000+00:00", end="2026-11-16T08:00:00.000+00:00")
        self.assertEqual(payload, {"startDate": "2026-11-02T08:00:00.000+00:00", "endDate": "2026-11-16T08:00:00.000+00:00", "state": "active"})

    def test_starting_uses_the_dates_the_sprint_already_has(self):
        dated = sprint(8, "Sprint 8", "future", "2026-11-02T08:00:00.000+00:00", "2026-11-16T08:00:00.000+00:00")
        self.assertEqual(agile.sprint_update_payload(dated, state="active"), {"state": "active"})

    def test_only_a_future_sprint_is_started_and_only_an_active_one_completed(self):
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_update_payload(SPRINTS[3], state="closed")
        self.assertIn("Only an active sprint", caught.exception.message)
        self.assertEqual(agile.sprint_update_payload(SPRINTS[2], state="closed"), {"state": "closed"})

    def test_the_same_state_and_other_states_are_refused(self):
        with self.assertRaises(ValidationError) as caught:
            agile.sprint_update_payload(SPRINTS[2], state="active")
        self.assertIn("already active", caught.exception.message)
        self.assertIn("--if-state", caught.exception.hint)
        with self.assertRaises(ValidationError):
            agile.sprint_update_payload(SPRINTS[2], state="future")

    def test_dates_of_an_active_sprint_can_change_but_not_into_a_wrong_order(self):
        active = SPRINTS[2]
        self.assertEqual(agile.sprint_update_payload(active, end="2026-10-30T08:00:00.000+02:00"), {"endDate": "2026-10-30T08:00:00.000+02:00"})
        with self.assertRaises(ValidationError):
            agile.sprint_update_payload(active, end="2026-10-01T08:00:00.000+02:00")

    def test_assign_and_batches(self):
        keys = [f"PROJ-{number}" for number in range(1, 121)]
        self.assertEqual(agile.assign_payload(keys[:2]), {"issues": ["PROJ-1", "PROJ-2"]})
        groups = agile.batches(keys)
        self.assertEqual([len(group) for group in groups], [50, 50, 20])
        self.assertEqual(sum(groups, []), keys)
        self.assertEqual(agile.batches(keys[:50]), [keys[:50]])
        self.assertEqual(agile.batches([]), [])

    def test_rank(self):
        self.assertEqual(agile.rank_payload(["PROJ-1", "PROJ-2"], before="proj-9"), {"issues": ["PROJ-1", "PROJ-2"], "rankBeforeIssue": "PROJ-9"})
        self.assertEqual(agile.rank_payload(["PROJ-1"], after="PROJ-9"), {"issues": ["PROJ-1"], "rankAfterIssue": "PROJ-9"})

    def test_what_the_read_back_of_a_rank_must_show(self):
        self.assertTrue(agile.ranked_as_asked(["PROJ-1", "PROJ-2", "PROJ-9"], ["PROJ-1", "PROJ-2"], "PROJ-9", before=True))
        self.assertTrue(agile.ranked_as_asked(["PROJ-9", "PROJ-1", "PROJ-2"], ["PROJ-1", "PROJ-2"], "PROJ-9", before=False))
        self.assertFalse(agile.ranked_as_asked(["PROJ-2", "PROJ-1", "PROJ-9"], ["PROJ-1", "PROJ-2"], "PROJ-9", before=True))  # not in the order given
        self.assertFalse(agile.ranked_as_asked(["PROJ-9", "PROJ-1", "PROJ-2"], ["PROJ-1", "PROJ-2"], "PROJ-9", before=True))
        self.assertFalse(agile.ranked_as_asked([], ["PROJ-1"], "PROJ-9", before=True))

    def test_fifty_issues_are_ranked_and_fifty_one_are_refused(self):
        fifty = [f"PROJ-{number}" for number in range(1, 51)]
        self.assertEqual(agile.rank_payload(fifty, after="PROJ-99"), {"issues": fifty, "rankAfterIssue": "PROJ-99"})
        with self.assertRaises(ValidationError) as caught:
            agile.rank_payload([*fifty, "PROJ-51"], after="PROJ-99")
        self.assertIn("51", caught.exception.message)

    def test_rank_needs_one_anchor_that_is_not_moved_and_at_most_fifty_issues(self):
        for options in ({}, {"before": "PROJ-9", "after": "PROJ-8"}):
            with self.subTest(options=options), self.assertRaises(UsageError):
                agile.rank_payload(["PROJ-1"], **options)
        with self.assertRaises(ValidationError):
            agile.rank_payload(["PROJ-1", "PROJ-2"], before="PROJ-2")
        with self.assertRaises(ValidationError) as caught:
            agile.rank_payload([f"PROJ-{number}" for number in range(1, 52)], after="PROJ-99")
        self.assertIn("at most 50", caught.exception.message)
        with self.assertRaises(ValidationError):
            agile.rank_payload(["PROJ-1"], before="not a key")


if __name__ == "__main__":
    unittest.main()
