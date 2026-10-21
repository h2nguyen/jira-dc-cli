"""Jira's JSON into domain objects. Pure functions: no HTTP, no state."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from ...domain.fields import ACCEPTANCE, EPIC_LINK, SPRINT, STORY_POINTS
from ...domain.model import (
    CATEGORY_NEW,
    Comment,
    FieldInfo,
    Issue,
    IssueLink,
    IssueRef,
    IssueTypeInfo,
    LinkType,
    Project,
    Sprint,
    Status,
    Transition,
    User,
)

# Fields that `issue_from` turns into attributes of Issue; everything else asked for lands in `extra`.
MODELED = frozenset(
    {
        "summary", "issuetype", "status", "priority", "labels", "components", "fixVersions", "description",
        "created", "updated", "assignee", "reporter", "parent", "issuelinks", "subtasks", "comment",
    }
)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def user_from(data: Any) -> User | None:
    if not isinstance(data, dict):
        return None
    return User(_text(data.get("name")), _text(data.get("displayName")), _text(data.get("emailAddress")))


def status_from(data: Any) -> Status | None:
    if not isinstance(data, dict):
        return None
    category = (data.get("statusCategory") or {}).get("key") or CATEGORY_NEW
    return Status(_text(data.get("name")), category)


def sprints_from(value: Any) -> list[Sprint]:
    """Jira Data Center returns each sprint as a string like `...Sprint@1[id=7,state=ACTIVE,name=...]`."""
    sprints: list[Sprint] = []
    for item in value or []:
        if isinstance(item, dict):
            sprints.append(Sprint(_text(item.get("name")), _text(item.get("state")), item.get("id")))
            continue
        text = str(item)
        name = re.search(r"(?:^|[\[,])name=([^,\]]*)", text)
        state = re.search(r"state=(\w+)", text)
        ident = re.search(r"(?:^|[\[,])id=(\d+)", text)
        sprints.append(Sprint(name.group(1) if name else "?", state.group(1) if state else "", int(ident.group(1)) if ident else None))
    return sprints


def ref_from(data: Any) -> IssueRef:
    data = data or {}
    fields = data.get("fields") or {}
    return IssueRef(_text(data.get("key")), _text(fields.get("summary")), _text((fields.get("status") or {}).get("name")))


def link_from(data: dict[str, Any]) -> IssueLink:
    link_type = data.get("type") or {}
    if "outwardIssue" in data:
        return IssueLink(_text(link_type.get("name")), _text(link_type.get("outward")), "outward", ref_from(data["outwardIssue"]))
    return IssueLink(_text(link_type.get("name")), _text(link_type.get("inward")), "inward", ref_from(data.get("inwardIssue")))


def comment_from(data: dict[str, Any]) -> Comment:
    return Comment(_text(data.get("id")), _text(data.get("created")), _text(data.get("body")), user_from(data.get("author")))


def _names(items: Iterable[Any] | None) -> list[str]:
    return [_text(item.get("name")) for item in items or [] if isinstance(item, dict)]


def _points(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def issue_from(data: dict[str, Any], ids: Mapping[str, str | None], extras: Iterable[str] = ()) -> Issue:
    fields = data.get("fields") or {}

    def custom(logical: str) -> Any:
        field_id = ids.get(logical)
        return fields.get(field_id) if field_id else None

    comment = fields.get("comment") or {}
    comments = [comment_from(item) for item in comment.get("comments") or []]
    parent = fields.get("parent") or {}
    epic = custom(EPIC_LINK)
    acceptance = custom(ACCEPTANCE)
    return Issue(
        key=_text(data.get("key")),
        summary=_text(fields.get("summary")),
        issue_type=_text((fields.get("issuetype") or {}).get("name")),
        status=status_from(fields.get("status")),
        priority=_text((fields.get("priority") or {}).get("name")),
        labels=[_text(label) for label in fields.get("labels") or []],
        components=_names(fields.get("components")),
        fix_versions=_names(fields.get("fixVersions")),
        epic_key=_text(epic) if epic else "",
        parent_key=_text(parent.get("key")),
        story_points=_points(custom(STORY_POINTS)),
        sprints=sprints_from(custom(SPRINT)),
        description=_text(fields.get("description")),
        acceptance=_text(acceptance) if acceptance else "",
        created=_text(fields.get("created")),
        updated=_text(fields.get("updated")),
        assignee=user_from(fields.get("assignee")),
        reporter=user_from(fields.get("reporter")),
        links=[link_from(link) for link in fields.get("issuelinks") or []],
        subtasks=[ref_from(task) for task in fields.get("subtasks") or []],
        comments=comments,
        comment_total=int(comment.get("total", len(comments)) or 0),
        extra={name: fields[name] for name in extras if name in fields and name not in MODELED},
        raw=data,
    )


def transition_from(data: dict[str, Any]) -> Transition:
    return Transition(_text(data.get("id")), _text(data.get("name")), status_from(data.get("to")) or Status(""))


def link_type_from(data: dict[str, Any]) -> LinkType:
    return LinkType(_text(data.get("id")), _text(data.get("name")), _text(data.get("outward")), _text(data.get("inward")))


def project_from(data: dict[str, Any]) -> Project:
    return Project(
        key=_text(data.get("key")),
        name=_text(data.get("name")),
        project_type=_text(data.get("projectTypeKey")),
        lead=user_from(data.get("lead")),
        description=_text(data.get("description")),
        issue_types=_names(data.get("issueTypes")),
    )


def field_from(data: dict[str, Any]) -> FieldInfo:
    return FieldInfo(_text(data.get("id")), _text(data.get("name")), bool(data.get("custom")))


def issue_types_from(data: Any) -> list[IssueTypeInfo]:
    """The create-metadata answer is `{"values": [...]}` in current Jira and a list in older ones."""
    items = data.get("values") if isinstance(data, dict) else data
    return [IssueTypeInfo(_text(item.get("id")), _text(item.get("name"))) for item in items or [] if isinstance(item, dict)]
