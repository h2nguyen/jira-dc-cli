"""The history of an issue as Jira keeps it (`expand=changelog`), reduced to what a reader needs: when the status changed, and to what.

The changelog of Jira Data Center names who made each change, and some of its changes name people (the assignee). That is personal
data, so the mapper never lets the authors or those changes into these types: an entry holds a time and the changes of the status.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import ValidationError
from .timeutil import parse_timestamp


@dataclass(frozen=True)
class FieldChange:
    field: str  # as Jira names it: status, assignee, labels, ...
    from_text: str = ""
    to_text: str = ""


@dataclass(frozen=True)
class HistoryEntry:
    created: str
    changes: tuple[FieldChange, ...] = ()


@dataclass(frozen=True)
class Changelog:
    entries: tuple[HistoryEntry, ...] = ()
    total: int = 0  # how many entries Jira holds; Jira may send fewer (Data Center does not page the changelog of an issue)

    @property
    def complete(self) -> bool:
        return self.total <= len(self.entries)


@dataclass(frozen=True)
class StatusChange:
    created: str
    from_status: str
    to_status: str


def _when(entry: HistoryEntry) -> datetime:
    try:
        return parse_timestamp(entry.created)
    except ValidationError:
        return datetime.min.replace(tzinfo=timezone.utc)


def status_changes(changelog: Changelog) -> list[StatusChange]:
    """The status changes, oldest first. Jira sends them oldest first already; the sort keeps that when it does not (the offset of
    the time can change inside one history, at a switch to summer time, so the text of the time cannot be compared)."""
    found: list[StatusChange] = []
    for entry in sorted(changelog.entries, key=_when):
        for change in entry.changes:
            if change.field.casefold() == "status":
                found.append(StatusChange(entry.created, change.from_text, change.to_text))
    return found
