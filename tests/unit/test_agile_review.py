"""The review of a sprint, derived from the history of its issues: every case of the timeline with synthetic histories."""
import unittest
from datetime import datetime, timedelta, timezone

from jira_dc_cli.domain import sprints as review
from jira_dc_cli.domain.agile import MAX_ID_DIGITS, SprintInfo
from jira_dc_cli.domain.sprints import SprintIssue, SprintMove, StatusMove

START = datetime(2026, 9, 14, 8, 0, tzinfo=timezone(timedelta(hours=2)))
END = datetime(2026, 9, 28, 16, 0, tzinfo=timezone(timedelta(hours=2)))
CATEGORIES = review.status_categories([("1", "To Do", "new"), ("3", "In Progress", "indeterminate"), ("10001", "Done", "done")])
BEFORE_START, DURING, DURING_LATE, AFTER_END = (
    "2026-09-10T10:00:00.000+0200",
    "2026-09-18T10:00:00.000+0200",
    "2026-09-24T10:00:00.000+0200",
    "2026-10-02T10:00:00.000+0200",
)
START_TEXT, JUST_AFTER_START = "2026-09-14T08:00:00.000+0200", "2026-09-14T08:00:00.001+0200"  # START and one millisecond later
END_TEXT, JUST_AFTER_END = "2026-09-28T16:00:00.000+0200", "2026-09-28T16:00:00.001+0200"  # END and one millisecond later


def move(at, before, after):
    return SprintMove(at, frozenset(before), frozenset(after))


def done(at):
    return StatusMove(at, "3", "In Progress", "10001", "Done")


def reopened(at):
    return StatusMove(at, "10001", "Done", "1", "To Do")


def issue(key, *, moves=(), status_moves=(), status="To Do", category="new", points=None, created="2026-08-01T09:00:00.000+0200", **more):
    return SprintIssue(key=key, status=status, status_category=category, points=points, created=created, sprint_moves=tuple(moves), status_moves=tuple(status_moves), **more)


def run(issues, members=None, **options):
    members = {item.key for item in issues} if members is None else members
    values = dict(start=START, end=END, members=members, categories=CATEGORIES)
    values.update(options)
    return review.review_sprint(7, issues, **values)


class ParsingTest(unittest.TestCase):
    def test_a_list_of_ids_is_a_set_of_numbers(self):
        self.assertEqual(review.parse_sprint_ids("6, 7"), {6, 7})
        self.assertEqual(review.parse_sprint_ids("7"), {7})
        self.assertEqual(review.parse_sprint_ids("6, 7, 6"), {6, 7})
        self.assertEqual(review.parse_sprint_ids(" 6 ,7 "), {6, 7})

    def test_no_value_and_odd_values_are_an_empty_or_cleaned_set(self):
        for value in (None, "", "  ", ",", "none", "6, x, 7", "6;7", "²"):
            with self.subTest(value=value):
                self.assertLessEqual(review.parse_sprint_ids(value), {6, 7})
        self.assertEqual(review.parse_sprint_ids(None), frozenset())
        self.assertEqual(review.parse_sprint_ids("6, x, 7"), {6, 7})

    def test_a_longer_id_is_not_a_shorter_one(self):
        self.assertNotIn(6, review.parse_sprint_ids("16, 60, 106"))
        self.assertIn(16, review.parse_sprint_ids("16"))

    def test_a_run_of_thousands_of_digits_is_ignored_and_never_an_error(self):
        huge = "9" * 5000  # `int()` refuses more than 4300 digits
        self.assertEqual(review.parse_sprint_ids(f"6, {huge}, 7"), {6, 7})
        self.assertEqual(review.parse_sprint_ids(huge), frozenset())
        longest = "9" * MAX_ID_DIGITS
        self.assertEqual(review.parse_sprint_ids(longest), {int(longest)})
        self.assertEqual(review.parse_sprint_ids("9" * (MAX_ID_DIGITS + 1)), frozenset())

    def test_the_jql_that_finds_the_issues(self):
        self.assertEqual(review.members_jql(7), "sprint = 7 ORDER BY key ASC")
        self.assertEqual(review.removed_jql(7), "sprint was 7 AND sprint != 7 ORDER BY key ASC")
        self.assertEqual(review.keys_jql(["proj-1", "PROJ-2"]), "key in (PROJ-1, PROJ-2)")
        self.assertEqual(review.rank_order_jql(["PROJ-1"]), "key in (PROJ-1) ORDER BY Rank ASC")
        with self.assertRaises(Exception):
            review.keys_jql(["x) OR (1=1"])  # an odd key cannot reach the query


class IntervalsTest(unittest.TestCase):
    def intervals(self, item, member_now=True):
        return review.sprint_intervals(item, 7, member_now=member_now)

    def test_without_history_the_issue_was_always_where_it_is(self):
        found, agrees = self.intervals(issue("PROJ-1"))
        self.assertEqual([leave for _, leave in found], [None])
        self.assertEqual(found[0][0], review._moment("2026-08-01T09:00:00.000+0200"))
        self.assertTrue(agrees)
        self.assertEqual(self.intervals(issue("PROJ-1"), member_now=False), ([], True))

    def test_the_first_entry_says_where_the_issue_was_before(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, {6}, {6, 7})])
        found, agrees = self.intervals(item)
        self.assertEqual(found, [(review._moment(BEFORE_START), None)])
        self.assertTrue(agrees)

    def test_in_and_out_and_in_again(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7}), move(DURING, {7}, set()), move(DURING_LATE, set(), {7})])
        found, _ = self.intervals(item)
        self.assertEqual(found, [(review._moment(BEFORE_START), review._moment(DURING)), (review._moment(DURING_LATE), None)])

    def test_an_issue_that_was_in_the_sprint_from_its_creation_and_left(self):
        item = issue("PROJ-1", moves=[move(DURING, {7}, set())])
        found, agrees = self.intervals(item, member_now=False)
        self.assertEqual(found, [(review._moment("2026-08-01T09:00:00.000+0200"), review._moment(DURING))])
        self.assertTrue(agrees)

    def test_entries_out_of_order_are_read_in_time_order(self):
        item = issue("PROJ-1", moves=[move(DURING, {7}, set()), move(BEFORE_START, set(), {7})])
        found, _ = self.intervals(item, member_now=False)
        self.assertEqual(found, [(review._moment(BEFORE_START), review._moment(DURING))])

    def test_an_entry_with_a_time_that_cannot_be_read_is_left_out_of_the_timeline_the_review_names_it(self):
        item = issue("PROJ-1", moves=[move("yesterday", set(), {7})])
        found, agrees = self.intervals(item, member_now=False)
        self.assertEqual((found, agrees), ([], True))

    def test_a_history_that_does_not_end_where_the_issue_is_says_so(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})])
        self.assertFalse(self.intervals(item, member_now=False)[1])

    def test_the_moment_of_entering_is_in_and_the_moment_of_leaving_is_out(self):
        found = [(review._moment(DURING), review._moment(DURING_LATE))]
        self.assertTrue(review.in_sprint_at(found, review._moment(DURING)))
        self.assertFalse(review.in_sprint_at(found, review._moment("2026-09-18T09:59:59.999+0200")))
        self.assertTrue(review.in_sprint_at(found, review._moment("2026-09-24T09:59:59.999+0200")))
        self.assertFalse(review.in_sprint_at(found, review._moment(DURING_LATE)))

    def test_membership_at_a_moment(self):
        found = [(review._moment(BEFORE_START), review._moment(DURING)), (review._moment(DURING_LATE), None)]
        self.assertTrue(review.in_sprint_at(found, START))
        self.assertFalse(review.in_sprint_at(found, review._moment(DURING)))  # the moment it left
        self.assertFalse(review.in_sprint_at(found, review._moment("2026-09-20T10:00:00.000+0200")))
        self.assertTrue(review.in_sprint_at(found, END))
        self.assertFalse(review.in_sprint_at([], END))


class StatusTest(unittest.TestCase):
    def test_without_a_change_it_is_the_current_status(self):
        self.assertEqual(review.status_at(issue("PROJ-1", status="In Progress"), END), ("", "In Progress"))

    def test_the_status_at_a_moment_comes_from_the_last_change_before_it(self):
        item = issue("PROJ-1", status_moves=[done(DURING), reopened(AFTER_END)])
        self.assertEqual(review.status_at(item, START), ("3", "In Progress"))  # before the first change: where it came from
        self.assertEqual(review.status_at(item, END), ("10001", "Done"))
        self.assertEqual(review.status_at(item, review._moment("2026-10-05T00:00:00+0200")), ("1", "To Do"))

    def test_the_category_by_id_then_by_name_then_the_current_status(self):
        item = issue("PROJ-1", status="Blocked", category="indeterminate", status_moves=[StatusMove(DURING, "3", "In Progress", "7", "Blocked")])
        self.assertEqual(review.category_at(item, END, CATEGORIES), ("indeterminate", "Blocked"))  # not in the list: it is the current one
        by_name = issue("PROJ-1", status_moves=[StatusMove(DURING, "3", "In Progress", "999", "done")])
        self.assertEqual(review.category_at(by_name, END, CATEGORIES)[0], "done")  # an unknown id, a known name in other case
        unknown = issue("PROJ-1", status_moves=[StatusMove(DURING, "3", "In Progress", "999", "Parked")])
        self.assertEqual(review.category_at(unknown, END, CATEGORIES), ("", "Parked"))

    def test_the_current_category_is_used_without_a_history_of_status(self):
        self.assertEqual(review.category_at(issue("PROJ-1", status="Done", category="done"), END, {}), ("done", "Done"))


class ReviewTest(unittest.TestCase):
    def keys(self, result):
        return {name: set(getattr(result, name).keys) for name in ("committed", "completed", "added", "removed", "carried_over")}

    def test_committed_added_removed_completed_and_carried_over(self):
        issues = [
            issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status_moves=[done(DURING)], status="Done", category="done", points=5.0),
            issue("PROJ-2", moves=[move(BEFORE_START, set(), {7}), move(DURING, {7}, set())], points=3.0),
            issue("PROJ-3", moves=[move(BEFORE_START, set(), {7})], status="In Progress", category="indeterminate", points=8.0),
            issue("PROJ-4", moves=[move(DURING, set(), {7})], status_moves=[done(DURING_LATE)], status="Done", category="done", points=2.0),
        ]
        result = run(issues, members={"PROJ-1", "PROJ-3", "PROJ-4"})
        self.assertEqual(
            self.keys(result),
            {
                "committed": {"PROJ-1", "PROJ-2", "PROJ-3"},
                "completed": {"PROJ-1", "PROJ-4"},
                "added": {"PROJ-4"},
                "removed": {"PROJ-2"},
                "carried_over": {"PROJ-3"},
            },
        )
        self.assertEqual((result.committed.issues, result.committed.points), (3, 16.0))
        self.assertEqual((result.completed.issues, result.completed.points), (2, 7.0))
        self.assertEqual((result.added.points, result.removed.points, result.carried_over.points), (2.0, 3.0, 8.0))
        self.assertEqual((result.start, result.end), (START.isoformat(), END.isoformat()))
        self.assertTrue(result.ended)

    def test_an_issue_created_in_the_sprint_has_no_history_and_counts_from_its_creation(self):
        before = issue("PROJ-1", created="2026-09-01T09:00:00.000+0200")
        after = issue("PROJ-2", created="2026-09-20T09:00:00.000+0200")
        result = run([before, after])
        self.assertEqual((set(result.committed.keys), set(result.added.keys)), ({"PROJ-1"}, {"PROJ-2"}))

    def test_an_issue_completed_after_the_sprint_was_not_completed_in_it(self):
        late = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status_moves=[done(AFTER_END)], status="Done", category="done")
        result = run([late])
        self.assertEqual(self.keys(result)["completed"], set())
        self.assertEqual(self.keys(result)["carried_over"], {"PROJ-1"})

    def test_an_issue_reopened_after_the_sprint_was_completed_in_it(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status_moves=[done(DURING), reopened(AFTER_END)])
        self.assertEqual(self.keys(run([item]))["completed"], {"PROJ-1"})

    def test_an_issue_that_was_done_before_the_sprint_started_and_stayed_in_it(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status="Done", category="done")
        result = run([item])
        self.assertEqual((set(result.committed.keys), set(result.completed.keys)), ({"PROJ-1"}, {"PROJ-1"}))

    def test_several_moves_in_and_out(self):
        back_again = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7}), move(DURING, {7}, set()), move(DURING_LATE, set(), {7})])
        late_comer = issue("PROJ-2", moves=[move("2026-09-11T10:00:00.000+0200", set(), {7}), move("2026-09-12T10:00:00.000+0200", {7}, set()), move(DURING_LATE, set(), {7})])
        gone_again = issue("PROJ-3", moves=[move(DURING, set(), {7}), move(DURING_LATE, {7}, set())])
        result = run([back_again, late_comer, gone_again], members={"PROJ-1", "PROJ-2"})
        keys = self.keys(result)
        self.assertEqual(keys["committed"], {"PROJ-1"})  # in at the start, out for a while, in at the end: still committed
        self.assertEqual(keys["added"], {"PROJ-2", "PROJ-3"})  # PROJ-2 was out when it started
        self.assertEqual(keys["removed"], {"PROJ-3"})
        self.assertEqual(keys["carried_over"], {"PROJ-1", "PROJ-2"})

    def test_an_issue_that_left_before_the_start_is_in_no_group(self):
        item = issue("PROJ-1", moves=[move("2026-09-01T10:00:00.000+0200", set(), {7}), move("2026-09-02T10:00:00.000+0200", {7}, set())])
        self.assertEqual(set().union(*self.keys(run([item], members=set())).values()), set())

    def test_an_issue_carried_over_from_the_sprint_before_enters_this_one(self):
        item = issue("PROJ-1", moves=[move(DURING, {6}, {6, 7})])
        result = run([item])
        self.assertEqual((set(result.added.keys), set(result.committed.keys)), ({"PROJ-1"}, set()))

    def test_the_sprint_in_a_list_of_sprints(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, {3, 6}, {3, 6, 7}), move(DURING, {3, 6, 7}, {3, 6, 7, 16})])
        self.assertEqual(self.keys(run([item]))["committed"], {"PROJ-1"})
        only_sixteen = issue("PROJ-2", moves=[move(BEFORE_START, set(), {16})])
        self.assertEqual(set().union(*self.keys(run([only_sixteen], members=set())).values()), set())

    def test_points_that_are_missing_count_as_zero_and_are_counted(self):
        items = [issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], points=2.5), issue("PROJ-2", moves=[move(BEFORE_START, set(), {7})])]
        result = run(items)
        self.assertEqual((result.committed.points, result.committed.without_points), (2.5, 1))

    def test_sub_tasks_are_left_out_unless_asked_for(self):
        items = [issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})]), issue("PROJ-2", moves=[move(BEFORE_START, set(), {7})], subtask=True)]
        result = run(items)
        self.assertEqual((result.committed.keys, result.excluded_subtasks), (("PROJ-1",), 1))
        self.assertTrue(any("sub-task" in note for note in result.notes))
        everything = run(items, with_subtasks=True)
        self.assertEqual((everything.committed.keys, everything.excluded_subtasks), (("PROJ-1", "PROJ-2"), 0))

    def test_a_status_whose_category_is_not_known_counts_as_not_done_and_is_named(self):
        moves = [StatusMove(DURING, "3", "In Progress", "999", "Parked"), StatusMove(AFTER_END, "999", "Parked", "1", "To Do")]
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status_moves=moves, status="To Do")
        result = run([item])
        self.assertEqual(result.carried_over.keys, ("PROJ-1",))
        self.assertTrue(any("Parked" in note for note in result.notes))

    def test_a_history_that_disagrees_with_the_sprint_field_is_named(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})])
        result = run([item], members=set())
        self.assertTrue(any("PROJ-1" in note and "history" in note for note in result.notes))

    def test_a_partial_history_is_named(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], history_complete=False)
        self.assertTrue(any("only part of the history of PROJ-1" in note for note in run([item]).notes))

    def test_the_notes_say_where_the_numbers_come_from(self):
        notes = run([]).notes
        self.assertTrue(any("change history" in note and "sprint report" in note for note in notes))
        self.assertTrue(any("current values" in note for note in notes))

    def test_what_is_wrong_with_the_data_is_a_warning_and_the_rest_is_a_note_only(self):
        sub_task = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], subtask=True)
        self.assertEqual(run([sub_task]).warnings, ())  # a sub-task that is left out is no problem
        partial = issue("PROJ-2", moves=[move(BEFORE_START, set(), {7})], history_complete=False)
        result = run([partial])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn(result.warnings[0], result.notes)
        self.assertEqual(set(review.DERIVATION_NOTES) & set(result.warnings), set())  # how the numbers are derived is no warning
        self.assertTrue(set(review.DERIVATION_NOTES) <= set(result.notes))
        refused = run([partial], leavers_searched=False)
        self.assertEqual((refused.warnings[0], len(refused.warnings)), (review.NO_LEAVERS_NOTE, 2))
        self.assertEqual(refused.notes[-2:], refused.warnings)

    def test_a_search_for_the_leavers_that_was_refused_makes_committed_added_and_removed_lower_bounds(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})])
        refused = run([item], leavers_searched=False)
        self.assertFalse(refused.history_complete)
        self.assertEqual(refused.warnings, (review.NO_LEAVERS_NOTE,))
        self.assertIn(review.NO_LEAVERS_NOTE, refused.notes)
        for word in ("sprint was", "removed", "committed", "added", "lower bounds", "velocity"):
            self.assertIn(word, review.NO_LEAVERS_NOTE)
        searched = run([item])
        self.assertTrue(searched.history_complete)
        self.assertNotIn(review.NO_LEAVERS_NOTE, searched.notes)

    def test_a_history_that_does_not_end_where_the_issue_is_or_that_was_cut_is_not_complete(self):
        moves = [move(BEFORE_START, set(), {7})]
        self.assertFalse(run([issue("PROJ-1", moves=moves)], members=set()).history_complete)  # it ends in the sprint, the issue is not in it
        self.assertFalse(run([issue("PROJ-1", moves=moves, history_complete=False)]).history_complete)
        self.assertTrue(run([issue("PROJ-1", moves=moves)]).history_complete)
        self.assertTrue(run([issue("PROJ-1")]).history_complete)  # an issue without history is complete: it was always where it is

    def test_an_entry_with_a_time_that_cannot_be_read_is_dropped_and_named(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7}), move("yesterday", {7}, set())])
        result = run([item])
        self.assertEqual(result.committed.keys, ("PROJ-1",))  # the entry is dropped: the issue stays where the readable ones put it
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("PROJ-1", result.warnings[0])
        self.assertIn("cannot be read", result.warnings[0])
        self.assertFalse(result.history_complete)
        status = issue("PROJ-2", moves=[move(BEFORE_START, set(), {7})], status_moves=[StatusMove("", "3", "In Progress", "10001", "Done")])
        self.assertIn("PROJ-2", run([status]).warnings[0])

    def test_a_missing_or_unreadable_creation_time_is_named_too(self):
        for created in ("", "not a time"):
            with self.subTest(created=created):
                result = run([issue("PROJ-1", created=created)])
                self.assertEqual(len(result.warnings), 1)
                self.assertIn("PROJ-1", result.warnings[0])
                self.assertFalse(result.history_complete)
        self.assertEqual(run([issue("PROJ-1", created="", subtask=True)]).warnings, ())  # an issue that is left out is no problem
        self.assertEqual(review.unreadable_times(issue("PROJ-1")), 0)
        self.assertEqual(review.unreadable_times(issue("PROJ-1", created="", moves=[move("x", set(), {7}), move("y", {7}, set())])), 3)

    def test_story_points_are_added_without_the_noise_of_floating_point(self):
        items = [issue(f"PROJ-{number}", moves=[move(BEFORE_START, set(), {7})], points=0.1) for number in (1, 2, 3)]
        self.assertEqual(run(items).committed.points, 0.3)  # a plain sum is 0.30000000000000004
        self.assertEqual(review.group_of(items).points, 0.3)
        self.assertEqual(review.total_points(items), 0.3)
        self.assertEqual(review.total_points([]), 0.0)
        self.assertEqual(review.total_points([issue("PROJ-1", points=2.5), issue("PROJ-2")]), 2.5)

    def test_a_sprint_that_still_runs_is_measured_to_now(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status_moves=[done(AFTER_END)], status="Done", category="done")
        now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        result = run([item], end=now, ended=False)
        self.assertEqual(result.completed.keys, ("PROJ-1",))
        self.assertFalse(result.ended)
        self.assertTrue(any("still running" in note for note in result.notes))

    def test_an_issue_is_counted_once_even_when_it_is_listed_twice(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})])
        self.assertEqual(run([item, item]).committed.issues, 1)

    def test_keys_are_in_the_order_of_their_numbers(self):
        items = [issue(f"PROJ-{number}", moves=[move(BEFORE_START, set(), {7})]) for number in (10, 2, 33, 4)]
        self.assertEqual(run(items).committed.keys, ("PROJ-2", "PROJ-4", "PROJ-10", "PROJ-33"))
        self.assertEqual(review.key_order("ABC-7"), ("ABC", 7))
        self.assertEqual(review.key_order("odd"), ("odd", 0))

    def test_a_key_with_thousands_of_digits_does_not_break_the_order(self):
        huge = "PROJ-" + "9" * 5000
        self.assertEqual(review.key_order(huge), (huge, 0))
        self.assertEqual(review.key_order("PROJ-" + "9" * MAX_ID_DIGITS), ("PROJ", 10**MAX_ID_DIGITS - 1))
        self.assertEqual(review.key_order("PROJ-" + "9" * (MAX_ID_DIGITS + 1))[1], 0)
        self.assertEqual(sorted(review.group_of([issue(huge), issue("PROJ-2")]).keys), sorted([huge, "PROJ-2"]))

    def test_an_empty_sprint(self):
        result = run([])
        for name in ("committed", "completed", "added", "removed", "carried_over"):
            self.assertEqual(getattr(result, name), review.Group(0, 0.0, 0, ()))


class BoundaryTest(unittest.TestCase):
    """The instants that decide the groups, to the millisecond: the start, the end and the complete date of the sprint."""

    def groups(self, item, *, member_now=True):
        result = run([item], members={item.key} if member_now else set())
        return {name for name in ("committed", "completed", "added", "removed", "carried_over") if item.key in getattr(result, name).keys}

    def test_an_issue_that_enters_exactly_at_the_start_is_committed_and_one_millisecond_later_it_is_added(self):
        self.assertEqual(self.groups(issue("PROJ-1", moves=[move(START_TEXT, set(), {7})])), {"committed", "carried_over"})
        self.assertEqual(self.groups(issue("PROJ-1", moves=[move(JUST_AFTER_START, set(), {7})])), {"added", "carried_over"})

    def test_an_issue_that_enters_exactly_at_the_end_is_added_and_one_millisecond_later_it_is_in_no_group(self):
        self.assertEqual(self.groups(issue("PROJ-1", moves=[move(END_TEXT, set(), {7})])), {"added", "carried_over"})
        self.assertEqual(self.groups(issue("PROJ-1", moves=[move(JUST_AFTER_END, set(), {7})])), set())

    def test_an_issue_that_leaves_exactly_at_the_end_is_removed_and_one_millisecond_later_it_stayed(self):
        entered = move(BEFORE_START, set(), {7})
        self.assertEqual(self.groups(issue("PROJ-1", moves=[entered, move(END_TEXT, {7}, set())]), member_now=False), {"committed", "removed"})
        self.assertEqual(
            self.groups(issue("PROJ-1", moves=[entered, move(JUST_AFTER_END, {7}, set())]), member_now=False), {"committed", "carried_over"}
        )

    def test_an_issue_that_leaves_exactly_at_the_start_was_never_counted_and_one_millisecond_later_it_was_committed(self):
        entered = move(BEFORE_START, set(), {7})
        self.assertEqual(self.groups(issue("PROJ-1", moves=[entered, move(START_TEXT, {7}, set())]), member_now=False), set())
        self.assertEqual(
            self.groups(issue("PROJ-1", moves=[entered, move(JUST_AFTER_START, {7}, set())]), member_now=False), {"committed", "removed"}
        )

    def test_an_issue_that_enters_and_leaves_at_the_same_instant_was_never_in_the_sprint(self):
        for at in (START_TEXT, END_TEXT):
            with self.subTest(at=at):
                item = issue("PROJ-1", moves=[move(at, set(), {7}), move(at, {7}, set())])
                self.assertEqual(self.groups(item, member_now=False), set())

    def test_a_status_change_exactly_at_the_complete_date_is_a_completion_and_one_millisecond_later_it_is_not(self):
        options = dict(moves=[move(BEFORE_START, set(), {7})], status="Done", category="done")
        self.assertEqual(self.groups(issue("PROJ-1", status_moves=[done(END_TEXT)], **options)), {"committed", "completed"})
        self.assertEqual(self.groups(issue("PROJ-1", status_moves=[done(JUST_AFTER_END)], **options)), {"committed", "carried_over"})


class VelocityTest(unittest.TestCase):
    def test_a_row_takes_the_numbers_of_the_review(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], status_moves=[done(DURING)], status="Done", category="done", points=5.0)
        sprint = SprintInfo(7, "Sprint 7", "closed", "2026-09-14T08:00:00.000+0200", "2026-09-28T08:00:00.000+0200", "2026-09-28T16:00:00.000+0200")
        row = review.velocity_row(sprint, run([item]))
        self.assertEqual((row.id, row.committed_points, row.completed_points, row.completed_issues), (7, 5.0, 5.0, 1))
        self.assertEqual(row.complete_date, "2026-09-28T16:00:00.000+0200")

    def test_a_row_says_whether_the_history_behind_it_is_complete(self):
        item = issue("PROJ-1", moves=[move(BEFORE_START, set(), {7})], points=5.0)
        sprint = SprintInfo(7, "Sprint 7", "closed", "2026-09-14T08:00:00.000+0200", "2026-09-28T08:00:00.000+0200", "2026-09-28T16:00:00.000+0200")
        self.assertTrue(review.velocity_row(sprint, run([item])).history_complete)
        self.assertFalse(review.velocity_row(sprint, run([item], leavers_searched=False)).history_complete)

    def test_the_average(self):
        self.assertEqual(review.average([30.0, 20.0, 25.0]), 25.0)
        self.assertEqual(review.average([1.0, 2.0]), 1.5)
        self.assertEqual(review.average([1.0, 1.0, 2.0]), 1.33)
        self.assertIsNone(review.average([]))


if __name__ == "__main__":
    unittest.main()
