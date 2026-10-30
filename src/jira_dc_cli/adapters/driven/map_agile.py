"""Jira Software's JSON (boards, sprints, epics, issues with their history) into domain objects. Pure functions: no HTTP, no state."""
from __future__ import annotations

import math
from typing import Any, Mapping

from ...domain.agile import Board, Epic, SprintInfo
from ...domain.sprints import SprintIssue, SprintMove, StatusMove, parse_sprint_ids


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _finite(value: Any) -> float | None:
    """A number of the JSON as a float; None for anything else, and for a number that has no finite value (`1e999`, NaN)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        found = float(value)
    except OverflowError:
        return None
    return found if math.isfinite(found) else None


def _number(value: Any) -> int | None:
    """A whole number of the JSON (an id); None for anything else, and for a number that has no finite value."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    found = _finite(value)
    return None if found is None else int(found)


def board_from(data: Mapping[str, Any]) -> Board:
    return Board(id=_number(data.get("id")) or 0, name=_text(data.get("name")), type=_text(data.get("type")).lower())


def sprint_from(data: Mapping[str, Any]) -> SprintInfo:
    return SprintInfo(
        id=_number(data.get("id")) or 0,
        name=_text(data.get("name")),
        state=_text(data.get("state")).lower(),
        start_date=_text(data.get("startDate")),
        end_date=_text(data.get("endDate")),
        complete_date=_text(data.get("completeDate")),
        goal=_text(data.get("goal")),
        board_id=_number(data.get("originBoardId")),
        activated_date=_text(data.get("activatedDate")),
    )


def epic_from(data: Mapping[str, Any]) -> Epic:
    return Epic(
        key=_text(data.get("key")),
        name=_text(data.get("name")),
        summary=_text(data.get("summary")),
        done=bool(data.get("done")),
        id=_number(data.get("id")),
    )


def project_key_from(data: Mapping[str, Any]) -> str:
    return _text(data.get("key"))


def _is_field(item: Mapping[str, Any], name: str, field_id: str | None = None) -> bool:
    return _text(item.get("field")).casefold() == name.casefold() or (bool(field_id) and item.get("fieldId") == field_id)


def _entries(history: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [item for item in history.get("items") or [] if isinstance(item, dict)]


def _status_move(when: str, item: Mapping[str, Any]) -> StatusMove:
    return StatusMove(when, _text(item.get("from")), _text(item.get("fromString")), _text(item.get("to")), _text(item.get("toString")))


def sprint_issue_from(data: Mapping[str, Any], ids: Mapping[str, str | None]) -> SprintIssue:
    """An issue of a search with `expand=changelog`. People in the history (authors) are never read."""
    fields = data.get("fields") or {}
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key")
    points_id = ids.get("story_points")
    points = fields.get(points_id) if points_id else None
    changelog = data.get("changelog")
    histories = [item for item in (changelog or {}).get("histories") or [] if isinstance(item, dict)]
    total = (changelog or {}).get("total")
    sprint_moves: list[SprintMove] = []
    status_moves: list[StatusMove] = []
    for history in histories:
        when = _text(history.get("created"))
        for item in _entries(history):
            if _is_field(item, "Sprint", ids.get("sprint")):
                sprint_moves.append(SprintMove(when, parse_sprint_ids(item.get("from")), parse_sprint_ids(item.get("to"))))
            elif _is_field(item, "status", "status"):
                status_moves.append(_status_move(when, item))
    return SprintIssue(
        key=_text(data.get("key")),
        summary=_text(fields.get("summary")),
        issue_type=_text((fields.get("issuetype") or {}).get("name")),
        subtask=bool((fields.get("issuetype") or {}).get("subtask")),
        created=_text(fields.get("created")),
        status=_text(status.get("name")),
        status_category=_text(category),
        points=_finite(points),
        sprint_moves=tuple(sprint_moves),
        status_moves=tuple(status_moves),
        history_complete=changelog is not None and not (isinstance(total, int) and total > len(histories)),
    )


def status_triples_from(data: Any) -> list[tuple[str, str, str]]:
    """`(id, name, category)` of each status of `GET /status`."""
    found = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            found.append((_text(item.get("id")), _text(item.get("name")), _text((item.get("statusCategory") or {}).get("key"))))
    return found
