"""Jira's JSON for the reading calls (an issue with its expansions, a saved filter) as domain objects. Pure functions: no HTTP, no state."""
from __future__ import annotations

from typing import Any, Mapping

from ...domain.fields import ACCEPTANCE
from ...domain.history import Changelog, FieldChange, HistoryEntry
from ...domain.issueparts import ExpandedIssue
from ...domain.savedfilters import SavedFilter
from . import jira_mapper as mapper


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _is_status(item: dict[str, Any]) -> bool:
    """Whether a changelog item changed Jira's own status field. A custom field may be called "status" too; Jira marks it `fieldtype: custom`."""
    return _text(item.get("field")).casefold() == "status" and _text(item.get("fieldtype")).casefold() in ("", "jira")


def changelog_from(data: Any) -> Changelog | None:
    """The history of an issue; None when the answer has none (`expand=changelog` was not asked for). Nothing that names a person
    comes in: not the author of an entry, and of the changes only those of the status (the assignee, a user picker name people).
    An entry that changed nothing but other fields stays as an empty one, so that the entries can still be counted against `total`."""
    if not isinstance(data, dict):
        return None
    entries = tuple(
        HistoryEntry(
            _text(history.get("created")),
            tuple(
                FieldChange(_text(item.get("field")), _text(item.get("fromString")), _text(item.get("toString")))
                for item in history.get("items") or []
                if isinstance(item, dict) and _is_status(item)
            ),
        )
        for history in data.get("histories") or []
        if isinstance(history, dict)
    )
    total = data.get("total")
    return Changelog(entries, max(total, len(entries)) if isinstance(total, int) and not isinstance(total, bool) else len(entries))


def expanded_from(data: Mapping[str, Any], ids: Mapping[str, str | None]) -> ExpandedIssue:
    """An issue as Jira sends it with `expand=renderedFields,changelog` (each is there only when it was asked for)."""
    rendered = data.get("renderedFields")
    rendered = rendered if isinstance(rendered, dict) else {}
    acceptance_id = ids.get(ACCEPTANCE)
    return ExpandedIssue(
        issue=mapper.issue_from(dict(data), ids),
        rendered_description=_text(rendered.get("description")),
        rendered_acceptance=_text(rendered.get(acceptance_id)) if acceptance_id else "",
        changelog=changelog_from(data.get("changelog")),
    )


def filter_from(data: Mapping[str, Any]) -> SavedFilter:
    return SavedFilter(
        id=_text(data.get("id")),
        name=_text(data.get("name")),
        description=_text(data.get("description")),
        jql=_text(data.get("jql")),
        favourite=bool(data.get("favourite")),
        owner=mapper.user_from(data.get("owner")),
        search_url=_text(data.get("searchUrl")),
        view_url=_text(data.get("viewUrl")),
    )
