"""Boards, sprints and epics as plain data, and the rules around them: references, picking a board, finding a sprint by name,
the life cycle of a sprint, and the payloads that change them. No I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from .errors import ConflictError, NotFoundError, UsageError, ValidationError
from .model import parse_issue_key
from .privacy import redact_text
from .timeutil import parse_timestamp

BOARD_TYPES = ("scrum", "kanban", "simple")
SPRINT_STATES = ("future", "active", "closed")
MAX_PER_CALL = 50  # issues that one call of the Agile API may move or rank (the reference says 50)
RANK_MAX = MAX_PER_CALL
MAX_ID_DIGITS = 18  # an id of Jira has a few digits; `int()` refuses a text of thousands of them, so a longer one is no id
_ID = re.compile(rf"[0-9]{{1,{MAX_ID_DIGITS}}}")
_CANDIDATES_SHOWN = 10
_SEPARATORS = re.compile(r"[\s,;]+")


@dataclass(frozen=True)
class Board:
    id: int
    name: str
    type: str = ""
    projects: tuple[str, ...] = ()


@dataclass(frozen=True)
class SprintInfo:
    id: int
    name: str
    state: str = ""  # future, active or closed
    start_date: str = ""
    end_date: str = ""
    complete_date: str = ""
    goal: str = ""
    board_id: int | None = None
    activated_date: str = field(default="", metadata={"json": False})  # when Jira really started it; a fallback for a missing start date


@dataclass(frozen=True)
class Epic:
    key: str
    name: str = ""
    summary: str = ""
    done: bool = False
    id: int | None = None


# -- references typed on the command line -------------------------------------------------------


def parse_board_id(value: Any) -> int:
    text = str(value).strip()
    number = int(text) if _ID.fullmatch(text) else 0  # a text of more than MAX_ID_DIGITS digits is no id, and `int()` would refuse it
    if number < 1:
        shown = text if len(text) <= 40 else text[:40] + "..."
        raise ValidationError(f"Not a board id: {shown}", hint="A board id is a number; `jira boards` lists them.")
    return number


def split_keys(text: str, *, what: str = "issue key") -> list[str]:
    """The issue keys in a text, separated by spaces or commas: upper case, each once, in the order given."""
    keys: list[str] = []
    for item in _SEPARATORS.split(text or ""):
        if not item:
            continue
        key = parse_issue_key(item)
        if key not in keys:
            keys.append(key)
    if not keys:
        raise UsageError(f"No {what} given.", hint="Give one or more keys such as PROJ-12, separated by spaces or commas.")
    return keys


def parse_sprint_ref(text: str) -> int | str:
    """A sprint id (all digits, at most `MAX_ID_DIGITS`) or a sprint name."""
    value = " ".join((text or "").split())
    if not value:
        raise UsageError("No sprint given.", hint="Give the id or the name of a sprint; `jira sprints` lists them.")
    if not re.fullmatch(r"[0-9]+", value):
        return value
    if len(value) > MAX_ID_DIGITS:
        raise UsageError(
            f"Not a sprint id: {value[:12]}... has {len(value)} digits.",
            hint=f"A sprint id has at most {MAX_ID_DIGITS} digits; `jira sprints` lists the sprints with their ids.",
        )
    return int(value)


def parse_states(text: str | None) -> list[str]:
    """`active,closed` as a list; each of future, active and closed at most once."""
    states: list[str] = []
    for item in _SEPARATORS.split((text or "").lower()):
        if not item:
            continue
        if item not in SPRINT_STATES:
            raise ValidationError(f"Unknown sprint state: {item}", hint=f"Use {', '.join(SPRINT_STATES)}, separated by commas.")
        if item not in states:
            states.append(item)
    return states


def parse_board_type(text: str | None) -> str:
    value = (text or "").strip().lower()
    if value and value not in BOARD_TYPES:
        raise ValidationError(f"Unknown board type: {text}", hint=f"Use one of: {', '.join(BOARD_TYPES)}.")
    return value


def normalize_moment(text: str, now: datetime) -> str:
    """A date or time as Jira's sprint dates are written (`2026-10-12T09:00:00.000+00:00`). `now` is the current time; a time without an
    offset counts as UTC."""
    value = (text or "").strip()
    if not value:
        raise ValidationError("A date is empty.", hint="Write 2026-10-12, 2026-10-12T09:00 or 2026-10-12T09:00:00+02:00, or `now`.")
    moment = now if value.lower() == "now" else parse_timestamp(value)
    return moment.isoformat(timespec="milliseconds")


def optional_moment(text: str, now: datetime) -> str:
    """`normalize_moment`, or an empty text for a date that was not given."""
    return normalize_moment(text, now) if (text or "").strip() else ""


# -- boards -------------------------------------------------------------------------------------


def board_line(board: Board) -> str:
    return f"{board.id} '{redact_text(board.name)}'" + (f" ({board.type})" if board.type else "")


def choose_board(boards: Sequence[Board], project: str, *, kind: str = "scrum") -> Board:
    """The one board of a project. With several, the error lists them: the person picks with --board."""
    what = f"{kind} board" if kind else "board"
    if not boards:
        raise NotFoundError(
            f"No {what} found for project {project}.",
            hint="`jira boards` lists the boards you may see; pass --board ID.",
            details={"project": project},
        )
    if len(boards) > 1:
        listed = "; ".join(board_line(board) for board in boards[:_CANDIDATES_SHOWN])
        raise ValidationError(
            f"Project {project} has {len(boards)} {what}s: {listed}.",
            hint="Pass --board ID with one of them.",
            details={"project": project, "boards": [{"id": board.id, "name": redact_text(board.name), "type": board.type} for board in boards]},
        )
    return boards[0]


def no_sprint_board(project: str, others: Sequence[Board]) -> NotFoundError | ValidationError:
    """Why a project has no board with sprints: it has no board at all, or only boards (kanban) that have no sprints."""
    if not others:
        return NotFoundError(
            f"No board found for project {project}.",
            hint="`jira boards` lists the boards you may see; pass --board ID.",
            details={"project": project},
        )
    listed = "; ".join(board_line(board) for board in others[:_CANDIDATES_SHOWN])
    return ValidationError(
        f"Project {project} has no scrum board; its boards have no sprints: {listed}.",
        hint="Sprints belong to scrum boards. `jira boards --type scrum` lists them.",
        details={"project": project, "boards": [{"id": board.id, "name": redact_text(board.name), "type": board.type} for board in others]},
    )


def require_sprints(board: Board) -> Board:
    """A board that has sprints: scrum. A kanban board has none, and says so."""
    if board.type and board.type != "scrum":
        raise ValidationError(
            f"Board {board_line(board)} has no sprints: it is a {board.type} board.",
            hint="Sprints belong to scrum boards. `jira boards --type scrum` lists them.",
            details={"boardId": board.id, "type": board.type},
        )
    return board


# -- sprints ------------------------------------------------------------------------------------


def day(text: str) -> str:
    """The date part of a timestamp, for a compact table."""
    return (text or "")[:10]


def sprint_line(sprint: SprintInfo) -> str:
    dates = f", {day(sprint.start_date)} to {day(sprint.end_date)}" if sprint.start_date or sprint.end_date else ""
    return f"{sprint.id} '{redact_text(sprint.name)}' ({sprint.state}{dates})"


def same_name(left: str, right: str) -> bool:
    """Two sprint names that differ only in case or in the spaces between words."""
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _candidates(sprints: Sequence[SprintInfo], name: str) -> list[SprintInfo]:
    """Sprints to name in an error: those whose name holds the text, else the ones that matter now (active, future, the last closed)."""
    wanted = " ".join(name.split()).casefold()
    close = [sprint for sprint in sprints if wanted and wanted in " ".join(sprint.name.split()).casefold()]
    return (close or order_sprints(sprints))[:_CANDIDATES_SHOWN]


def resolve_sprint(sprints: Sequence[SprintInfo], name: str) -> SprintInfo:
    """The sprint of a board with this name (exact, ignoring case and extra spaces). Several or none is an error that lists candidates."""
    found = [sprint for sprint in sprints if same_name(sprint.name, name)]
    if len(found) == 1:
        return found[0]
    listing = found or _candidates(sprints, name)
    details = {"candidates": [{"id": sprint.id, "name": redact_text(sprint.name), "state": sprint.state} for sprint in listing]}
    shown = "; ".join(sprint_line(sprint) for sprint in listing)
    if found:
        raise ValidationError(
            f"{len(found)} sprints are named '{name}': {shown}.",
            hint="Use the id of the one you mean.",
            details=details,
        )
    raise NotFoundError(
        f"No sprint named '{name}' on this board." + (f" Candidates: {shown}." if shown else " The board has no sprints."),
        hint="`jira sprints` lists the sprints of a board; the name must match exactly (case does not matter), or use the id.",
        details=details,
    )


def _closed_at(sprint: SprintInfo) -> str:
    return sprint.complete_date or sprint.end_date or ""


def order_sprints(sprints: Sequence[SprintInfo]) -> list[SprintInfo]:
    """Active sprints first, then the future ones in the board's order, then the closed ones, the newest first."""
    active = [sprint for sprint in sprints if sprint.state == "active"]
    future = [sprint for sprint in sprints if sprint.state == "future"]
    closed = [sprint for sprint in sprints if sprint.state == "closed"]
    others = [sprint for sprint in sprints if sprint.state not in SPRINT_STATES]
    closed.sort(key=lambda sprint: (_closed_at(sprint), sprint.id), reverse=True)
    return active + future + closed + others


def check_state(sprint: SprintInfo, expected: Sequence[str]) -> None:
    """`--if-state`: refuse when the sprint is not in one of the states the person expected."""
    if expected and sprint.state not in expected:
        raise ConflictError(
            f"Sprint {sprint_line(sprint)} is {sprint.state}, not {' or '.join(expected)}.",
            hint="Read the sprint again (`jira sprint ID`) and decide on what is there now.",
            details={"sprintId": sprint.id, "state": sprint.state, "expected": list(expected)},
        )


def require_open(sprint: SprintInfo) -> None:
    """Issues can only be moved into a future or an active sprint."""
    if sprint.state == "closed":
        raise ValidationError(
            f"Sprint {sprint_line(sprint)} is closed: issues can only be moved into a future or an active sprint.",
            hint="Pick another sprint; `jira sprints --state active,future` lists them.",
            details={"sprintId": sprint.id, "state": sprint.state},
        )


# -- payloads -----------------------------------------------------------------------------------


def clean_name(text: str | None) -> str:
    name = " ".join((text or "").split())
    if not name:
        raise ValidationError("The sprint name is empty.", hint="Give the name of the sprint.")
    return name


def _check_order(start: str, end: str) -> None:
    if start and end and parse_timestamp(end) <= parse_timestamp(start):
        raise ValidationError(f"The end ({end}) is not after the start ({start}).", hint="Give an --end that comes after --start.")


def sprint_create_payload(name: str, board_id: int, *, goal: str = "", start: str = "", end: str = "") -> dict[str, Any]:
    """The body of `POST /agile/1.0/sprint`: a future sprint; dates are optional and are given as `normalize_moment` writes them."""
    _check_order(start, end)
    payload: dict[str, Any] = {"name": clean_name(name), "originBoardId": board_id}
    if goal.strip():
        payload["goal"] = goal.strip()
    if start:
        payload["startDate"] = start
    if end:
        payload["endDate"] = end
    return payload


def sprint_update_payload(
    sprint: SprintInfo,
    *,
    name: str | None = None,
    goal: str | None = None,
    start: str = "",
    end: str = "",
    state: str = "",
) -> dict[str, Any]:
    """The body of the partial update `POST /agile/1.0/sprint/{id}`, after the rules of the reference: a closed sprint cannot be changed,
    only `active` (from future, with a start and an end) and `closed` (from active) are possible, and the complete date is Jira's."""
    if sprint.state == "closed":
        raise ValidationError(
            f"Sprint {sprint_line(sprint)} is closed: Jira does not allow changing a closed sprint.",
            hint="Create a new sprint with `jira sprint-create`.",
            details={"sprintId": sprint.id, "state": sprint.state},
        )
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = clean_name(name)
    if goal is not None:
        payload["goal"] = goal.strip()  # an empty goal removes it
    if start:
        payload["startDate"] = start
    if end:
        payload["endDate"] = end
    if state:
        payload["state"] = _target_state(sprint, state, start or sprint.start_date, end or sprint.end_date)
    if not payload:
        raise UsageError("Nothing to change.", hint="Pass --name, --goal, --start, --end or --state.")
    _check_order(start or sprint.start_date, end or sprint.end_date)
    return payload


def _target_state(sprint: SprintInfo, state: str, start: str, end: str) -> str:
    if state not in ("active", "closed"):
        raise ValidationError(f"A sprint can only be started (--state active) or completed (--state closed), not set to {state}.")
    if state == sprint.state:
        raise ValidationError(
            f"Sprint {sprint_line(sprint)} is already {state}.",
            hint="Use --if-state to make a repeated call safe: it stops when the sprint is not in the state you expect.",
        )
    if state == "active":
        if sprint.state != "future":
            raise ValidationError(f"Only a future sprint can be started; this one is {sprint.state}.")
        missing = [label for label, value in (("--start", start), ("--end", end)) if not value]
        if missing:
            raise ValidationError(
                f"A sprint needs a start and an end date before it can be started; missing: {', '.join(missing)}.",
                hint="Pass --start and --end (`--start now` starts it now), or set them first with `jira sprint-update`.",
            )
    elif sprint.state != "active":
        raise ValidationError(f"Only an active sprint can be completed; this one is {sprint.state}.")
    return state


def assign_payload(keys: Sequence[str]) -> dict[str, Any]:
    """The body that moves issues to a sprint, to an epic or out of an epic: `{"issues": [...]}`."""
    return {"issues": list(keys)}


def batches(keys: Sequence[str], size: int = MAX_PER_CALL) -> list[list[str]]:
    """The keys in groups of at most `size`, the size one call of the Agile API accepts."""
    return [list(keys[index : index + size]) for index in range(0, len(keys), size)]


def rank_payload(keys: Sequence[str], *, before: str = "", after: str = "") -> dict[str, Any]:
    """The body of `PUT /agile/1.0/issue/rank`: the issues go before or after one issue."""
    if bool(before) == bool(after):
        raise UsageError("Say where the issues go: --before KEY or --after KEY, not both and not neither.")
    if len(keys) > RANK_MAX:
        raise ValidationError(
            f"{len(keys)} issues cannot be ranked in one go: at most {RANK_MAX}.",
            hint="Rank them in several commands, each after the previous result.",
        )
    anchor = parse_issue_key(before or after)
    if anchor in keys:
        raise ValidationError(f"{anchor} cannot be ranked relative to itself.", hint="Pick an anchor that is not one of the issues you move.")
    return {"issues": list(keys), "rankBeforeIssue" if before else "rankAfterIssue": anchor}


def ranked_as_asked(order: Sequence[str], keys: Sequence[str], anchor: str, *, before: bool) -> bool:
    """Whether the moved issues and the anchor, read back in the order of their rank, stand where they were asked to."""
    return list(order) == ([*keys, anchor] if before else [anchor, *keys])
