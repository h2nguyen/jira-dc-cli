"""Use cases of reference, reached as `app.reference`: read-only lists and facts about the instance."""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..domain.errors import AuthError, JiraCliError, NotFoundError, UsageError
from ..domain.model import parse_issue_key, parse_project_key
from ..domain.privacy import redact_text
from ..domain.refdata import (
    MAX_COUNTED_VERSIONS,
    ComponentInfo,
    DashboardInfo,
    DashboardPage,
    IssueTypeDetail,
    PermissionInfo,
    ProjectRole,
    RefItem,
    ServerReport,
    StatusDetail,
    TypeStatuses,
    VersionInfo,
    done_statuses,
    pick_versions,
    released_filter,
    select_permissions,
    server_report,
    split_names,
)
from .people_policy import ensure_people_allowed
from .ports import Clock, Diagnostics, Files, JiraGateway
from .settings import Settings

MAX_ROLES = 40  # roles of a project that `--with-users` asks about one by one
MAX_DASHBOARD_PAGES = 20  # a server that never stops answering the same page must not keep the CLI asking


def _project_key(project: str, settings: Settings) -> str:
    chosen = project or settings.project
    if not chosen:
        raise UsageError("No project given and none selected.", hint="Pass a project key or run `jira use KEY`.")
    return parse_project_key(chosen)


def _item(item: RefItem) -> RefItem:
    return replace(item, name=redact_text(item.name), description=redact_text(item.description))


def _issue_type(item: IssueTypeDetail) -> IssueTypeDetail:
    return replace(item, name=redact_text(item.name), description=redact_text(item.description))


def _status(item: StatusDetail) -> StatusDetail:
    return replace(item, name=redact_text(item.name), description=redact_text(item.description))


class ListPriorities:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self) -> list[RefItem]:
        return [_item(item) for item in self._gateway.list_priorities()]


class ListResolutions:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self) -> list[RefItem]:
        return [_item(item) for item in self._gateway.list_resolutions()]


class ListIssueTypes:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, project: str = "") -> list[IssueTypeDetail]:
        """The types of one project, or every type of the Jira."""
        if project.strip():
            return [_issue_type(item) for item in self._gateway.list_project_issue_types(parse_project_key(project))]
        try:
            return [_issue_type(item) for item in self._gateway.list_all_issue_types()]
        except AuthError as error:
            raise AuthError(
                error.message,
                hint="Jira may let only administrators list every issue type; `jira issue-types --project KEY` lists the types of one project.",
                details=error.details,
            ) from error


@dataclass(frozen=True)
class StatusOverview:
    project: str  # empty for the statuses of the whole Jira
    statuses: list[StatusDetail]  # of the whole Jira; empty for a project
    issue_types: list[TypeStatuses]  # of a project; empty for the whole Jira
    done: list[str]  # the names of the statuses that count as done


class ListStatuses:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, project: str = "") -> StatusOverview:
        if not project.strip():
            statuses = [_status(item) for item in self._gateway.list_all_statuses()]
            return StatusOverview("", statuses, [], done_statuses(statuses))
        key = parse_project_key(project)
        types = [
            replace(item, name=redact_text(item.name), statuses=[_status(status) for status in item.statuses])
            for item in self._gateway.list_project_statuses(key)
        ]
        return StatusOverview(key, [], types, done_statuses(status for item in types for status in item.statuses))


@dataclass(frozen=True)
class ComponentList:
    project: str
    components: list[ComponentInfo]


class ListComponents:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, project: str = "", *, with_users: bool = False) -> ComponentList:
        key = _project_key(project, self._settings)
        if with_users:
            ensure_people_allowed(self._settings)
        found = [
            replace(item, name=redact_text(item.name), description=redact_text(item.description), lead=item.lead if with_users else None)
            for item in self._gateway.list_project_components(key)
        ]
        return ComponentList(key, found)


@dataclass(frozen=True)
class VersionList:
    project: str
    versions: list[VersionInfo]
    counted: int  # how many of them carry counts; fewer than `versions` when the bound cut them


class ListVersions:
    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(self, project: str = "", *, released: bool = False, unreleased: bool = False, counts: bool = False) -> VersionList:
        wanted = released_filter(released, unreleased)
        key = _project_key(project, self._settings)
        shown = [
            replace(item, name=redact_text(item.name), description=redact_text(item.description))
            for item in pick_versions(self._gateway.list_project_versions(key), wanted)
        ]
        if not counts:
            return VersionList(key, shown, 0)
        counted = 0
        for index, version in enumerate(shown[:MAX_COUNTED_VERSIONS]):
            if not version.id:
                continue
            try:
                shown[index] = replace(version, counts=self._gateway.get_version_counts(version.id))
                counted += 1
            except NotFoundError as error:  # the version went away, or may not be counted: the list is still worth having
                self._diagnostics.warn(f"No counts for {version.name}: {error.message}")
        if len(shown) > MAX_COUNTED_VERSIONS:
            self._diagnostics.warn(
                f"Counts for the first {MAX_COUNTED_VERSIONS} of {len(shown)} versions only (two requests each): "
                "narrow the list with --released or --unreleased."
            )
        return VersionList(key, shown, counted)


class ShowPermissions:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, *, project: str = "", issue: str = "", only: str = "") -> list[PermissionInfo]:
        """What the token's user may do: in a project, on an issue, or in general; all permissions, or the ones named in `only`."""
        if project.strip() and issue.strip():
            raise UsageError(
                "Use either --project or --issue, not both.",
                hint="An issue belongs to one project: ask about the issue to include its project's rules.",
            )
        project_key = parse_project_key(project) if project.strip() else ""
        issue_key = parse_issue_key(issue) if issue.strip() else ""
        found = self._gateway.get_my_permissions(project_key=project_key, issue_key=issue_key)
        return select_permissions(found, split_names(only))


@dataclass(frozen=True)
class RoleList:
    project: str
    roles: list[ProjectRole]


class ListRoles:
    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(self, project: str = "", *, with_users: bool = False) -> RoleList:
        """The roles of a project. The people in them are one request per role and only with `with_users`."""
        key = _project_key(project, self._settings)
        if with_users:
            ensure_people_allowed(self._settings)
        refs = sorted(self._gateway.list_project_roles(key), key=lambda ref: ref.name.casefold())
        roles: list[ProjectRole] = []
        for index, ref in enumerate(refs):
            name = redact_text(ref.name)
            if not with_users:
                roles.append(ProjectRole(ref.id, name))
                continue
            if ref.id and index < MAX_ROLES:
                roles.append(ProjectRole(ref.id, name, self._gateway.get_project_role(key, ref.id).actors or []))
            else:
                roles.append(ProjectRole(ref.id, name, None))
        if with_users and len(refs) > MAX_ROLES:
            self._diagnostics.warn(f"The people of the first {MAX_ROLES} of {len(refs)} roles only.")
        return RoleList(key, roles)


class ListDashboards:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, *, mine: bool = False, favourite: bool = False, limit: int = 50) -> DashboardPage:
        """Up to `limit` dashboards, asking again while Jira answers fewer than asked for. Without `mine` or `favourite` every dashboard
        that the user can see; the two do not go together."""
        if mine and favourite:
            raise UsageError("Use either --mine or --favourite, not both.", hint="Leave both out for every dashboard you can see.")
        if limit < 1:
            raise UsageError("--limit must be 1 or more.")
        filter_name = "my" if mine else "favourite" if favourite else ""  # the words that Jira's `filter` parameter knows
        found: list[DashboardInfo] = []
        total = 0
        for _ in range(MAX_DASHBOARD_PAGES):
            page = self._gateway.list_dashboards(filter_name=filter_name, start_at=len(found), max_results=min(limit - len(found), 100))
            total = page.total
            found.extend(page.dashboards)
            if not page.dashboards or len(found) >= limit or len(found) >= total:
                break
        cleaned = [replace(item, name=redact_text(item.name), description=redact_text(item.description)) for item in found[:limit]]
        return DashboardPage(total, cleaned)


class ShowServer:
    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(self) -> ServerReport:
        """The version and the settings of the Jira, with the verdict on whether it is the release line the CLI is written for.

        The upload limit comes from another call than the rest and is no reason to fail: it is left out, with a warning."""
        info = self._gateway.server_info()
        settings = self._gateway.get_server_settings()
        limit = None
        try:
            limit = self._gateway.get_attachment_upload_limit()
        except JiraCliError as error:
            self._diagnostics.warn(f"The upload limit for attachments could not be read: {error.message}")
        return server_report(info, replace(settings, attachment_upload_limit=limit), self._settings.base_url)


@dataclass
class ReferenceUseCases:
    """One field per use case; built in `build`."""

    priorities: ListPriorities
    resolutions: ListResolutions
    issue_types: ListIssueTypes
    statuses: ListStatuses
    components: ListComponents
    versions: ListVersions
    permissions: ShowPermissions
    roles: ListRoles
    dashboards: ListDashboards
    server: ShowServer


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> ReferenceUseCases:
    return ReferenceUseCases(
        priorities=ListPriorities(gateway),
        resolutions=ListResolutions(gateway),
        issue_types=ListIssueTypes(gateway),
        statuses=ListStatuses(gateway),
        components=ListComponents(gateway, settings),
        versions=ListVersions(gateway, settings, diagnostics),
        permissions=ShowPermissions(gateway),
        roles=ListRoles(gateway, settings, diagnostics),
        dashboards=ListDashboards(gateway),
        server=ShowServer(gateway, settings, diagnostics),
    )
