"""Jira's JSON of the reference calls into domain objects. Pure functions: no HTTP, no state; every key may be missing."""
from __future__ import annotations

import re
from typing import Any

from ...domain.model import User
from ...domain.refdata import (
    ComponentInfo,
    DashboardInfo,
    DashboardPage,
    IssueTypeDetail,
    PermissionInfo,
    ProjectRole,
    RefItem,
    RoleActor,
    RoleRef,
    ServerSettings,
    StatusDetail,
    TimeTracking,
    TypeStatuses,
    VersionCounts,
    VersionInfo,
)
from . import map_values as values
from .jira_mapper import user_from

_ROLE_ID = re.compile(r"/role/(\d+)/?$")


def ref_item_from(data: dict[str, Any]) -> RefItem:
    return RefItem(values.text(data.get("id")), values.text(data.get("name")), values.text(data.get("description")))


def issue_type_from(data: dict[str, Any]) -> IssueTypeDetail:
    return IssueTypeDetail(
        values.text(data.get("id")), values.text(data.get("name")), values.text(data.get("description")), bool(data.get("subtask"))
    )


def status_from(data: dict[str, Any]) -> StatusDetail:
    category = data.get("statusCategory")
    key = category.get("key") if isinstance(category, dict) else None
    return StatusDetail(values.text(data.get("id")), values.text(data.get("name")), values.text(key), values.text(data.get("description")))


def type_statuses_from(data: dict[str, Any]) -> TypeStatuses:
    return TypeStatuses(
        values.text(data.get("id")),
        values.text(data.get("name")),
        bool(data.get("subtask")),
        [status_from(item) for item in values.objects(data.get("statuses"))],
    )


def component_from(data: dict[str, Any]) -> ComponentInfo:
    lead = user_from(data.get("lead"))
    if lead is None and data.get("leadUserName"):
        lead = User(values.text(data.get("leadUserName")))
    return ComponentInfo(values.text(data.get("id")), values.text(data.get("name")), values.text(data.get("description")), lead)


def version_from(data: dict[str, Any]) -> VersionInfo:
    return VersionInfo(
        values.text(data.get("id")),
        values.text(data.get("name")),
        values.text(data.get("description")),
        bool(data.get("released")),
        bool(data.get("archived")),
        values.text(data.get("releaseDate")),
    )


def version_counts_from(related: Any, unresolved: Any) -> VersionCounts:
    related = related if isinstance(related, dict) else {}
    unresolved = unresolved if isinstance(unresolved, dict) else {}
    return VersionCounts(
        values.count(related.get("issuesFixedCount")),
        values.count(related.get("issuesAffectedCount")),
        values.count(unresolved.get("issuesUnresolvedCount")),
    )


def permissions_from(data: Any) -> list[PermissionInfo]:
    """`{"permissions": {"EDIT_ISSUES": {"name", "type", "havePermission", ...}}}`: one entry per permission, keyed by its key."""
    entries = data.get("permissions") if isinstance(data, dict) else None
    found: list[PermissionInfo] = []
    for key, item in (entries.items() if isinstance(entries, dict) else []):
        if isinstance(item, dict):
            found.append(
                PermissionInfo(
                    values.text(item.get("key") or key),
                    values.text(item.get("name")),
                    values.text(item.get("type")),
                    bool(item.get("havePermission")),
                )
            )
    return found


def role_refs_from(data: Any) -> list[RoleRef]:
    """`{"Developers": "https://.../role/10001"}`: the name is the key and the id the last part of the address."""
    found: list[RoleRef] = []
    for name, address in (data.items() if isinstance(data, dict) else []):
        match = _ROLE_ID.search(str(address or ""))
        found.append(RoleRef(match.group(1) if match else "", values.text(name)))
    return found


def _actor_kind(kind: str) -> str:
    lowered = kind.lower()
    return "group" if "group" in lowered else "user" if "user" in lowered else ""


def project_role_from(data: dict[str, Any]) -> ProjectRole:
    actors = [
        RoleActor(values.text(item.get("name")), values.text(item.get("displayName")), _actor_kind(values.text(item.get("type"))))
        for item in values.objects(data.get("actors"))
    ]
    return ProjectRole(values.text(data.get("id")), values.text(data.get("name")), actors)


def dashboard_from(data: dict[str, Any]) -> DashboardInfo:
    return DashboardInfo(
        values.text(data.get("id")), values.text(data.get("name")), values.text(data.get("description")), values.text(data.get("view"))
    )


def dashboard_page_from(data: Any) -> DashboardPage:
    data = data if isinstance(data, dict) else {}
    found = [dashboard_from(item) for item in values.objects(data.get("dashboards"))]
    return DashboardPage(values.count(data.get("total"), len(found)), found)


def time_tracking_from(data: Any) -> TimeTracking | None:
    if not isinstance(data, dict):
        return None
    return TimeTracking(
        values.number(data.get("workingHoursPerDay")),
        values.number(data.get("workingDaysPerWeek")),
        values.text(data.get("timeFormat")),
        values.text(data.get("defaultUnit")),
    )


def server_settings_from(data: Any) -> ServerSettings:
    data = data if isinstance(data, dict) else {}
    return ServerSettings(
        attachments_enabled=values.flag(data.get("attachmentsEnabled")),
        sub_tasks_enabled=values.flag(data.get("subTasksEnabled")),
        issue_linking_enabled=values.flag(data.get("issueLinkingEnabled")),
        voting_enabled=values.flag(data.get("votingEnabled")),
        watching_enabled=values.flag(data.get("watchingEnabled")),
        unassigned_issues_allowed=values.flag(data.get("unassignedIssuesAllowed")),
        time_tracking_enabled=values.flag(data.get("timeTrackingEnabled")),
        time_tracking=time_tracking_from(data.get("timeTrackingConfiguration")),
    )


def upload_limit_from(data: Any) -> int | None:
    """The upload limit in bytes that `attachment/meta` states; None when it does not."""
    limit = data.get("uploadLimit") if isinstance(data, dict) else None
    found = values.count(limit, -1)
    return found if found >= 0 else None
