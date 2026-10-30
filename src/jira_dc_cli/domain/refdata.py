"""Reference data of a Jira instance (priorities, statuses, versions, permissions, roles, settings) as plain data, and the rules around it."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .compat import Compatibility, assess
from .errors import UsageError
from .model import CATEGORY_DONE, User

MAX_COUNTED_VERSIONS = 25  # `--counts` costs two requests per version: a project with hundreds of versions must not turn into hundreds of calls


@dataclass(frozen=True)
class RefItem:
    """A priority or a resolution."""

    id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class IssueTypeDetail:
    id: str
    name: str
    description: str = ""
    subtask: bool = False


@dataclass(frozen=True)
class StatusDetail:
    id: str
    name: str
    category: str = ""  # new | indeterminate | done, the key of Jira's status category: it is what decides that an issue is done
    description: str = ""


@dataclass(frozen=True)
class TypeStatuses:
    """An issue type of a project with the statuses its workflow knows."""

    id: str
    name: str
    subtask: bool = False
    statuses: list[StatusDetail] = field(default_factory=list)


@dataclass(frozen=True)
class ComponentInfo:
    id: str
    name: str
    description: str = ""
    lead: User | None = None  # a person: only with --with-users


@dataclass(frozen=True)
class VersionCounts:
    fixed: int  # issues that have the version as fix version
    affected: int  # issues that have it as affected version
    unresolved: int  # of the fixed ones, the ones that are not resolved


@dataclass(frozen=True)
class VersionInfo:
    id: str
    name: str
    description: str = ""
    released: bool = False
    archived: bool = False
    release_date: str = ""
    counts: VersionCounts | None = None  # None: not asked for, or beyond the bound of `MAX_COUNTED_VERSIONS`


@dataclass(frozen=True)
class PermissionInfo:
    key: str
    name: str
    type: str = ""  # GLOBAL or PROJECT
    have_permission: bool = False  # whether the token's user has it, in the project or on the issue that was asked about


@dataclass(frozen=True)
class RoleRef:
    id: str
    name: str


@dataclass(frozen=True)
class RoleActor:
    name: str
    display_name: str = ""
    kind: str = ""  # user or group


@dataclass(frozen=True)
class ProjectRole:
    id: str
    name: str
    actors: list[RoleActor] | None = None  # people: only with --with-users


@dataclass(frozen=True)
class DashboardInfo:
    id: str
    name: str
    description: str = ""
    view: str = ""  # the address of the dashboard in the web interface


@dataclass(frozen=True)
class DashboardPage:
    total: int
    dashboards: list[DashboardInfo]


@dataclass(frozen=True)
class TimeTracking:
    working_hours_per_day: float | None = None
    working_days_per_week: float | None = None
    time_format: str = ""
    default_unit: str = ""


@dataclass(frozen=True)
class ServerSettings:
    """The optional features of the Jira, from its configuration. None: Jira did not say."""

    attachments_enabled: bool | None = None
    attachment_upload_limit: int | None = None  # bytes
    sub_tasks_enabled: bool | None = None
    issue_linking_enabled: bool | None = None
    voting_enabled: bool | None = None
    watching_enabled: bool | None = None
    unassigned_issues_allowed: bool | None = None
    time_tracking_enabled: bool | None = None
    time_tracking: TimeTracking | None = None


@dataclass(frozen=True)
class ServerReport:
    base_url: str  # as the CLI is configured, not as the server thinks of itself
    version: str
    deployment_type: str
    build_number: int | None
    compatibility: Compatibility
    settings: ServerSettings


def server_report(info: Mapping[str, Any], settings: ServerSettings, base_url: str) -> ServerReport:
    """The answer of `serverInfo` and the settings as one report, with the verdict on the release line."""
    version, deployment = str(info.get("version") or ""), str(info.get("deploymentType") or "")
    build = info.get("buildNumber")
    number = build if isinstance(build, int) and not isinstance(build, bool) else None
    return ServerReport(base_url, version, deployment, number, assess(version, deployment), settings)


def split_names(text: str | None) -> list[str]:
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def _normal(name: str) -> str:
    return re.sub(r"[\s-]+", "_", name.strip()).upper()


def select_permissions(found: Sequence[PermissionInfo], names: Sequence[str]) -> list[PermissionInfo]:
    """The permissions that were asked for, by key (`EDIT_ISSUES`) or by name (`Edit Issues`), by key; all of them without names.

    The valid names are the ones Jira sent: a permission that an add-on brings is valid on the Jira that has it."""
    ordered = sorted(found, key=lambda item: item.key)
    if not names:
        return ordered
    by_key = {item.key.upper(): item for item in ordered}
    by_name = {_normal(item.name): item for item in ordered}
    chosen: dict[str, PermissionInfo] = {}
    unknown: list[str] = []
    for name in names:
        item = by_key.get(_normal(name)) or by_name.get(_normal(name))
        if item is None:
            close = difflib.get_close_matches(_normal(name), list(by_key), n=1, cutoff=0.6)
            unknown.append(f"{name} (did you mean {close[0]}?)" if close else name)
        else:
            chosen[item.key] = item
    if unknown:
        raise UsageError(
            f"Unknown permission: {', '.join(unknown)}.",
            hint="Valid names: " + ", ".join(item.key for item in ordered) + ".",
        )
    return sorted(chosen.values(), key=lambda item: item.key)


def done_statuses(statuses: Iterable[StatusDetail]) -> list[str]:
    """The names of the statuses whose category is `done`, once each: what `done` means in this Jira or project."""
    return sorted({status.name for status in statuses if status.category == CATEGORY_DONE})


def released_filter(released: bool, unreleased: bool) -> bool | None:
    """True for only the released versions, False for only the unreleased ones, None for all; asking for both is a usage error."""
    if released and unreleased:
        raise UsageError("Use either --released or --unreleased, not both.", hint="Leave both out for every version.")
    return True if released else False if unreleased else None


def pick_versions(versions: Sequence[VersionInfo], released: bool | None = None) -> list[VersionInfo]:
    """The versions that are released (True), unreleased (False) or all (None), in the order Jira keeps them."""
    return [version for version in versions if released is None or version.released == released]
