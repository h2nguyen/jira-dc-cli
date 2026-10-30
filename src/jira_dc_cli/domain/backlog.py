"""The backlog dump: every issue of a project in a stable, personal-data-free shape.

Format "backlog dump v1". Other tools (for example a markdown mirror comparison) read it, so the
key names are a contract: see docs/reference/json-output.md.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .fields import ACCEPTANCE, EPIC_LINK, EXPORT_KEYS, SPRINT, STORY_POINTS
from .model import Issue
from .privacy import redact_text
from .sprints import key_order


def backlog_entry(issue: Issue) -> dict[str, Any]:
    return {
        "summary": redact_text(issue.summary),
        "type": issue.issue_type,
        "status": issue.status.name if issue.status else None,
        "priority": issue.priority or None,
        "labels": [redact_text(label) for label in issue.labels],
        "components": [redact_text(name) for name in issue.components],
        "fixVersions": [redact_text(name) for name in issue.fix_versions],
        "description": redact_text(issue.description),
        "epicLink": issue.epic_key or None,
        "storyPoints": issue.story_points,
        "acceptance": redact_text(issue.acceptance) if issue.acceptance else None,
        "sprint": [{"name": redact_text(sprint.name), "state": sprint.state} for sprint in issue.sprints],
    }


def build_backlog_dump(
    issues: Sequence[Issue], field_ids: Mapping[str, str | None], fetched_at: str
) -> dict[str, Any]:
    ordered = sorted(issues, key=lambda issue: key_order(issue.key))  # numeric order; an overlong number sorts last in its project
    return {
        "fetchedAt": fetched_at,
        "fieldIds": {EXPORT_KEYS[name]: field_ids.get(name) for name in (EPIC_LINK, STORY_POINTS, ACCEPTANCE, SPRINT)},
        "issues": {issue.key: backlog_entry(issue) for issue in ordered},
    }
