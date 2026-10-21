"""Progress reports: issues and story points by status and by epic."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .model import CATEGORY_DONE, CATEGORY_IN_PROGRESS, CATEGORY_NEW, Issue

NO_EPIC = "(no epic)"
_CATEGORY_ORDER = {CATEGORY_NEW: 0, CATEGORY_IN_PROGRESS: 1, CATEGORY_DONE: 2}


@dataclass(frozen=True)
class StatusRow:
    status: str
    category: str
    issues: int
    points: float


@dataclass(frozen=True)
class EpicRow:
    key: str
    summary: str
    issues: int
    done: int
    points: float
    done_points: float


@dataclass(frozen=True)
class Report:
    scope: str
    issues: int
    done: int
    points: float
    done_points: float
    without_points: int
    by_status: list[StatusRow] = field(default_factory=list)
    by_epic: list[EpicRow] = field(default_factory=list)

    @property
    def open_points(self) -> float:
        return self.points - self.done_points


def _points(issue: Issue) -> float:
    return float(issue.story_points) if isinstance(issue.story_points, (int, float)) else 0.0


def build_report(
    issues: Sequence[Issue], scope: str, *, epics: Mapping[str, str] | None = None, per_epic: bool = False
) -> Report:
    """Epics are not counted themselves; "done" means the status category, not a status name."""
    work = [issue for issue in issues if issue.issue_type != "Epic"]
    done = [issue for issue in work if issue.status is not None and issue.status.is_done]

    statuses: dict[str, list] = {}
    for issue in work:
        name = issue.status.name if issue.status else "?"
        category = issue.status.category if issue.status else CATEGORY_NEW
        row = statuses.setdefault(name, [category, 0, 0.0])
        row[1] += 1
        row[2] += _points(issue)
    by_status = [
        StatusRow(name, row[0], row[1], row[2])
        for name, row in sorted(statuses.items(), key=lambda item: (_CATEGORY_ORDER.get(item[1][0], 1), item[0]))
    ]

    by_epic: list[EpicRow] = []
    if per_epic:
        groups: dict[str, list] = {}
        for issue in work:
            key = issue.epic_key or NO_EPIC
            row = groups.setdefault(key, [0, 0, 0.0, 0.0])
            row[0] += 1
            row[2] += _points(issue)
            if issue.status is not None and issue.status.is_done:
                row[1] += 1
                row[3] += _points(issue)
        names = epics or {}
        by_epic = [
            EpicRow(key, names.get(key, ""), row[0], row[1], row[2], row[3])
            for key, row in sorted(groups.items(), key=lambda item: (item[0] == NO_EPIC, len(item[0]), item[0]))
        ]

    return Report(
        scope=scope,
        issues=len(work),
        done=len(done),
        points=sum(_points(issue) for issue in work),
        done_points=sum(_points(issue) for issue in done),
        without_points=sum(1 for issue in work if _points(issue) == 0.0),
        by_status=by_status,
        by_epic=by_epic,
    )
