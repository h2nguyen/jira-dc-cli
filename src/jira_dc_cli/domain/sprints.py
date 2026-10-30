"""The review of a sprint, derived from the change history of its issues: what was committed to, completed, added, removed and carried over.

Jira Data Center has no documented call for the numbers of the sprint report, so they are derived from documented ones: the issues that are
(or were) in the sprint, with their change history (`expand=changelog`). The history of the field Sprint says when an issue entered and left
the sprint, the history of the status says whether it was done when the sprint ended. All of it is pure: the callers hand in the issues.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Collection, Iterable, Mapping, Sequence

from .agile import MAX_ID_DIGITS, SprintInfo
from .errors import ValidationError
from .model import CATEGORY_DONE, parse_issue_key
from .timeutil import parse_timestamp

_NUMBER = re.compile(rf"[0-9]{{1,{MAX_ID_DIGITS}}}")  # a longer run of digits is no id: `int()` would refuse thousands of them
_KEY_PARTS = re.compile(rf"^(.*)-([0-9]{{1,{MAX_ID_DIGITS}}})$")
LONG_AGO = datetime.min.replace(tzinfo=timezone.utc)
MAX_REVIEWED_ISSUES = 2000  # a sprint with more issues than this is refused instead of being reviewed from part of its issues
DERIVATION_NOTES = (
    "Derived from the change history of the field Sprint and of the status, not from Jira's own sprint report: its numbers can differ.",
    "Story points are the current values of the issues, and an issue without points counts as 0 (withoutPoints says how many).",
)
NO_LEAVERS_NOTE = (
    "Jira refused the search for the issues that left the sprint (`sprint was`): removed, committed and added are lower bounds, because an "
    "issue that was in the sprint and left it is missing from them. The same holds for the committed numbers of `velocity`."
)

# -- the JQL that finds the issues of a sprint ---------------------------------------------------


def members_jql(sprint_id: int) -> str:
    """The issues that are in the sprint now. A closed sprint stays in the Sprint field of an issue that was carried over, so for a closed
    sprint these are the issues that were in it when it was completed."""
    return f"sprint = {int(sprint_id)} ORDER BY key ASC"


def removed_jql(sprint_id: int) -> str:
    """The issues that were in the sprint at some time and are not now. Uses the history operator `was`, which Jira may refuse for the
    Sprint field: the caller then reviews without them and says so."""
    return f"sprint was {int(sprint_id)} AND sprint != {int(sprint_id)} ORDER BY key ASC"


def keys_jql(keys: Sequence[str]) -> str:
    return "key in (" + ", ".join(parse_issue_key(key) for key in keys) + ")"


def rank_order_jql(keys: Sequence[str]) -> str:
    """The issues in the order of the rank, which is how the backlog and the board show them."""
    return f"{keys_jql(keys)} ORDER BY Rank ASC"


# -- what the history of an issue says ---------------------------------------------------------


@dataclass(frozen=True)
class SprintMove:
    """One entry of the history that changed the field Sprint: the sprints the issue was in before and after it."""

    at: str
    before: frozenset[int]
    after: frozenset[int]


@dataclass(frozen=True)
class StatusMove:
    """One entry of the history that changed the status."""

    at: str
    from_id: str = ""
    from_name: str = ""
    to_id: str = ""
    to_name: str = ""


@dataclass(frozen=True)
class SprintIssue:
    """An issue as the review needs it: its current state and its history, without any person."""

    key: str
    summary: str = ""
    issue_type: str = ""
    subtask: bool = False
    created: str = ""
    status: str = ""
    status_category: str = ""
    points: float | None = None
    sprint_moves: tuple[SprintMove, ...] = ()
    status_moves: tuple[StatusMove, ...] = ()
    history_complete: bool = True  # False when Jira returned only part of the history of the issue


def parse_sprint_ids(value: object) -> frozenset[int]:
    """The sprint ids in a value of the history such as `"6, 7"`. Anything that is not a whole number of at most `MAX_ID_DIGITS` digits
    is ignored; 16 is not 6."""
    return frozenset(int(item) for item in re.split(r"[,\s]+", str(value or "")) if _NUMBER.fullmatch(item))


def _moment(text: str) -> datetime | None:
    try:
        return parse_timestamp(text)
    except ValidationError:
        return None


def unreadable_times(issue: SprintIssue) -> int:
    """How many of the times that the timeline of the issue rests on cannot be read: the entries of its history (they are left out) and its
    creation time (a missing one counts as long ago). The review names such an issue, because it may be sorted wrongly."""
    entries = sum(1 for move in (*issue.sprint_moves, *issue.status_moves) if _moment(move.at) is None)
    return entries + (0 if _moment(issue.created) else 1)


def _in_time_order(moves: Iterable[SprintMove] | Iterable[StatusMove]) -> list[tuple[datetime, SprintMove | StatusMove]]:
    """The entries that have a readable time, oldest first; entries with the same time keep the order of the history."""
    timed = [(when, move) for move in moves if (when := _moment(move.at)) is not None]
    timed.sort(key=lambda pair: pair[0])
    return timed


Interval = tuple[datetime, "datetime | None"]  # in the sprint from, until (None: still)


def sprint_intervals(issue: SprintIssue, sprint_id: int, *, member_now: bool) -> tuple[list[Interval], bool]:
    """When the issue was in the sprint, and whether the history agrees with where the issue is now.

    The first entry of the history says where the issue was before it (an issue that was created in the sprint has no entry for that);
    without any entry the issue was always where it is now."""
    timed = _in_time_order(issue.sprint_moves)
    created = _moment(issue.created) or LONG_AGO
    inside = (sprint_id in timed[0][1].before) if timed else member_now
    since: datetime | None = created if inside else None
    intervals: list[Interval] = []
    for when, move in timed:
        now_in = sprint_id in move.after
        if now_in and not inside:
            since = when
        elif inside and not now_in:
            entered = since or created
            if entered < when:  # an issue that enters and leaves at the same instant was never in the sprint
                intervals.append((entered, when))
            since = None
        inside = now_in
    if inside:
        intervals.append((since or created, None))
    return intervals, inside == member_now


def in_sprint_at(intervals: Sequence[Interval], moment: datetime) -> bool:
    return any(enter <= moment and (leave is None or leave > moment) for enter, leave in intervals)


def status_at(issue: SprintIssue, moment: datetime) -> tuple[str, str]:
    """The status (id, name) of the issue at a moment. Without a change of status it is the current one; the id is then unknown."""
    timed = _in_time_order(issue.status_moves)
    if not timed:
        return "", issue.status
    earlier = [move for when, move in timed if when <= moment]
    if earlier:
        last = earlier[-1]
        return last.to_id, last.to_name
    first = timed[0][1]
    return first.from_id, first.from_name


def status_categories(statuses: Iterable[tuple[str, str, str]]) -> dict[str, str]:
    """The category of a status by its id and by its name (written in lower case): from `(id, name, category)` of every status."""
    known: dict[str, str] = {}
    for ident, name, category in statuses:
        if ident:
            known[ident] = category
        if name:
            known[name.casefold()] = category
    return known


def category_at(issue: SprintIssue, moment: datetime, categories: Mapping[str, str]) -> tuple[str, str]:
    """The category of the status the issue had at a moment, and the name of the status when its category is not known."""
    ident, name = status_at(issue, moment)
    if not ident and not issue.status_moves:
        return issue.status_category, name
    category = categories.get(ident) or categories.get(name.casefold()) or (issue.status_category if name and name == issue.status else "")
    return category, name


# -- the review ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Group:
    """The issues of one group: how many, their story points, how many of them have none, and their keys."""

    issues: int
    points: float
    without_points: int
    keys: tuple[str, ...]


@dataclass(frozen=True)
class SprintReview:
    start: str
    end: str
    ended: bool  # False for a sprint that still runs: its end is now
    committed: Group
    completed: Group
    added: Group
    removed: Group
    carried_over: Group
    excluded_subtasks: int
    notes: tuple[str, ...]
    history_complete: bool = True  # False: committed, added and removed rest on a history that is not whole (lower bounds, or wrong)
    warnings: tuple[str, ...] = field(default=(), metadata={"json": False})  # the notes that say something is wrong with the data


def key_order(key: str) -> tuple[str, int]:
    """The sort key of an issue key: its project, then its number, so that PROJ-2 comes before PROJ-10. A key without a number, or with a number
    of more than `MAX_ID_DIGITS` digits (no number of Jira, and `int()` refuses thousands of digits), is taken as a whole: it sorts after the
    other keys of its project."""
    match = _KEY_PARTS.match(key)
    return (match.group(1), int(match.group(2))) if match else (key, 0)


def total_points(issues: Iterable[SprintIssue]) -> float:
    """The story points of the issues, rounded so that three issues of 0.1 make 0.3 and not 0.30000000000000004; no points count as 0."""
    return round(sum(issue.points or 0.0 for issue in issues), 6)


def group_of(issues: Iterable[SprintIssue]) -> Group:
    chosen = sorted(issues, key=lambda issue: key_order(issue.key))
    return Group(
        issues=len(chosen),
        points=total_points(chosen),
        without_points=sum(1 for issue in chosen if issue.points is None),
        keys=tuple(issue.key for issue in chosen),
    )


def review_sprint(
    sprint_id: int,
    issues: Iterable[SprintIssue],
    *,
    start: datetime,
    end: datetime,
    members: Collection[str],
    categories: Mapping[str, str],
    ended: bool = True,
    with_subtasks: bool = False,
    leavers_searched: bool = True,
) -> SprintReview:
    """Sort the issues of a sprint into committed (in it when it started), added (entered after the start), removed (left before the end),
    completed (in it at the end, in a status of the category done then) and carried over (in it at the end, not done).

    `members` are the keys of the issues that are in the sprint now; `end` is the complete date, or now for a sprint that still runs. An
    issue completed after `end` is not completed in the sprint; the groups overlap (a committed issue can also be completed).
    `leavers_searched` is False when the issues that left the sprint could not be searched for: they are missing from `issues`, so
    committed, added and removed are lower bounds, and the review says so."""
    seen: dict[str, SprintIssue] = {}
    for issue in issues:
        seen.setdefault(issue.key, issue)
    excluded = 0
    committed, added, removed, completed, carried = [], [], [], [], []
    unknown: set[str] = set()
    disagree: list[str] = []
    partial: list[str] = []
    unreadable: list[str] = []
    for issue in seen.values():
        if issue.subtask and not with_subtasks:
            excluded += 1
            continue
        intervals, agrees = sprint_intervals(issue, sprint_id, member_now=issue.key in members)
        if not agrees:
            disagree.append(issue.key)
        if not issue.history_complete:
            partial.append(issue.key)
        if unreadable_times(issue):
            unreadable.append(issue.key)
        at_start = in_sprint_at(intervals, start)
        at_end = in_sprint_at(intervals, end)
        entered_later = any(start < enter <= end for enter, _ in intervals)
        if at_start:
            committed.append(issue)
        elif entered_later:
            added.append(issue)
        if (at_start or entered_later) and not at_end:
            removed.append(issue)
        if at_end:
            category, name = category_at(issue, end, categories)
            if not category and name:
                unknown.add(name)
            (completed if category == CATEGORY_DONE else carried).append(issue)
    notes = list(DERIVATION_NOTES)
    if not ended:
        notes.append("The sprint is still running: completed and carried over are as of now.")
    if excluded:
        notes.append(f"{excluded} sub-task(s) are not counted; --with-subtasks counts them.")
    warnings: list[str] = [] if leavers_searched else [NO_LEAVERS_NOTE]
    if unknown:
        warnings.append("The category of the status is not known for: " + ", ".join(sorted(unknown)) + "; those issues count as not done.")
    if disagree:
        listed = ", ".join(sorted(disagree, key=key_order))
        warnings.append(f"The history of {listed} does not end where the issue is now; it was sorted by its history.")
    if partial:
        listed = ", ".join(sorted(partial, key=key_order))
        warnings.append(f"Jira returned only part of the history of {listed}: those issues may be sorted wrongly.")
    if unreadable:
        listed = ", ".join(sorted(unreadable, key=key_order))
        warnings.append(
            f"The history of {listed} holds a time that cannot be read (an entry is left out, a missing creation time counts as long ago): "
            "those issues may be sorted wrongly."
        )
    return SprintReview(
        start=start.isoformat(),
        end=end.isoformat(),
        ended=ended,
        committed=group_of(committed),
        completed=group_of(completed),
        added=group_of(added),
        removed=group_of(removed),
        carried_over=group_of(carried),
        excluded_subtasks=excluded,
        notes=(*notes, *warnings),
        history_complete=leavers_searched and not (disagree or partial or unreadable),
        warnings=tuple(warnings),
    )


# -- velocity -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class VelocityRow:
    id: int
    name: str
    start_date: str
    complete_date: str
    committed_issues: int
    committed_points: float
    completed_issues: int
    completed_points: float
    history_complete: bool = True  # False: the committed numbers are lower bounds or may be wrong (see the review of the sprint)


def velocity_row(sprint: SprintInfo, review: SprintReview) -> VelocityRow:
    return VelocityRow(
        id=sprint.id,
        name=sprint.name,
        start_date=sprint.start_date,
        complete_date=sprint.complete_date or sprint.end_date,
        committed_issues=review.committed.issues,
        committed_points=review.committed.points,
        completed_issues=review.completed.issues,
        completed_points=review.completed.points,
        history_complete=review.history_complete,
    )


def average(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None
