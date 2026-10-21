"""The core concepts of the CLI as plain data. No I/O, no Jira JSON."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import ValidationError

CATEGORY_NEW = "new"
CATEGORY_IN_PROGRESS = "indeterminate"
CATEGORY_DONE = "done"

_ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-[0-9]+$")
_PROJECT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def parse_issue_key(text: str) -> str:
    value = (text or "").strip()
    if not _ISSUE_KEY.match(value):
        raise ValidationError(f"Invalid issue key: {value or '<missing>'}", hint="An issue key looks like PROJECT-123.")
    return value.upper()


def parse_project_key(text: str) -> str:
    value = (text or "").strip()
    if not _PROJECT_KEY.match(value):
        raise ValidationError(f"Invalid project key: {value or '<missing>'}", hint="A project key looks like PROJ.")
    return value.upper()


@dataclass(frozen=True)
class Status:
    name: str
    category: str = CATEGORY_NEW

    @property
    def is_done(self) -> bool:
        return self.category == CATEGORY_DONE


@dataclass(frozen=True)
class User:
    name: str = ""
    display_name: str = ""
    email: str = ""


@dataclass(frozen=True)
class Sprint:
    name: str
    state: str = ""
    id: int | None = None


@dataclass(frozen=True)
class IssueRef:
    key: str
    summary: str = ""
    status: str = ""


@dataclass(frozen=True)
class IssueLink:
    type_name: str
    phrase: str
    direction: str
    other: IssueRef


@dataclass(frozen=True)
class Comment:
    id: str
    created: str
    body: str
    author: User | None = None


@dataclass
class Issue:
    key: str
    summary: str = ""
    issue_type: str = ""
    status: Status | None = None
    priority: str = ""
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    fix_versions: list[str] = field(default_factory=list)
    epic_key: str = ""
    parent_key: str = ""
    story_points: float | None = None
    sprints: list[Sprint] = field(default_factory=list)
    description: str = ""
    acceptance: str = ""
    created: str = ""
    updated: str = ""
    assignee: User | None = None
    reporter: User | None = None
    links: list[IssueLink] = field(default_factory=list)
    subtasks: list[IssueRef] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    comment_total: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = field(default=None, repr=False, compare=False, metadata={"json": False})


@dataclass(frozen=True)
class Transition:
    id: str
    name: str
    to_status: Status


@dataclass(frozen=True)
class LinkType:
    id: str
    name: str
    outward: str
    inward: str


@dataclass(frozen=True)
class Project:
    key: str
    name: str = ""
    project_type: str = ""
    lead: User | None = None
    description: str = ""
    issue_types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FieldInfo:
    id: str
    name: str
    custom: bool = False


@dataclass(frozen=True)
class IssueTypeInfo:
    id: str
    name: str


@dataclass(frozen=True)
class CreatedIssue:
    id: str
    key: str
    url: str = ""


@dataclass
class SearchPage:
    issues: list[Issue]
    total: int
    start_at: int = 0
    max_results: int = 0
    raw_issues: list[dict[str, Any]] = field(default_factory=list, repr=False, metadata={"json": False})
