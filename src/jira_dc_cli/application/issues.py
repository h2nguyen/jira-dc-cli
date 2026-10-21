"""Use cases that read issues: view, search, branch name, report, backlog export, fields."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from ..domain import jql
from ..domain.backlog import build_backlog_dump
from ..domain.branching import branch_name
from ..domain.errors import UnavailableError, UsageError, ValidationError
from ..domain.fields import BACKLOG_FIELDS, EPIC_LINK, LIST_FIELDS, REPORT_FIELDS, STORY_POINTS
from ..domain.model import FieldInfo, Issue, parse_issue_key, parse_project_key
from ..domain.privacy import hide_users, redact_text, strip_users
from ..domain.reporting import Report, build_report
from .ports import Clock, Diagnostics, JiraGateway
from .settings import Settings

PAGE_SIZE = 100


def fetch_all(gateway: JiraGateway, query: str, fields: Sequence[str]) -> tuple[list[Issue], int, list[dict[str, Any]]]:
    """Every page of a search: (issues, total reported by Jira, raw issues). Stops at the first empty page."""
    issues: list[Issue] = []
    raw: list[dict[str, Any]] = []
    start, total = 0, 1
    while start < total:
        page = gateway.search(query, fields=fields, start_at=start, max_results=PAGE_SIZE)
        total = page.total
        if not page.issues:
            break
        issues.extend(page.issues)
        raw.extend(page.raw_issues)
        start += len(page.issues)
    return issues, total, raw


def require_project(project: str, settings: Settings) -> str:
    chosen = project or settings.project
    if not chosen:
        raise UsageError("No project selected.", hint="Run `jira use KEY` or pass --project KEY.")
    return parse_project_key(chosen)


@dataclass
class IssueView:
    issue: Issue
    users_shown: bool
    comments_shown: bool
    raw: dict[str, Any] | None = None


class ViewIssue:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(
        self, key: str, *, with_comments: bool = False, with_users: bool = False, want_raw: bool = False
    ) -> IssueView:
        key = parse_issue_key(key)
        if with_users and self._settings.hide_users:
            raise ValidationError(
                "JIRA_HIDE_USERS=true: people are not printed, --with-users is not available.",
                hint="Unset JIRA_HIDE_USERS if you need to see people.",
            )
        issue = self._gateway.get_issue(key)
        raw = issue.raw if want_raw else None
        if not with_users:
            raw = strip_users(raw) if raw is not None else None
            issue = hide_users(issue)
        if not with_comments:
            issue = replace(issue, comments=[])
        return IssueView(issue, with_users, with_comments, raw)


@dataclass
class SearchResult:
    issues: list[Issue]
    total: int
    jql: str
    assignee_hidden: bool
    raw_issues: list[dict[str, Any]] = field(default_factory=list)


class SearchIssues:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(
        self,
        *,
        project: str = "",
        query: str = "",
        mine: bool = False,
        limit: int = 50,
        start_at: int = 0,
        fetch_everything: bool = False,
        extra_fields: Sequence[str] = (),
        hide_assignee: bool = False,
    ) -> SearchResult:
        chosen = project or self._settings.project
        text = jql.issue_search(project=parse_project_key(chosen) if chosen else "", jql=query, mine=mine)
        fields = [*LIST_FIELDS, *extra_fields]
        if fetch_everything:
            issues, total, raw = fetch_all(self._gateway, text, fields)
        else:
            page = self._gateway.search(text, fields=fields, start_at=start_at, max_results=limit)
            issues, total, raw = page.issues, page.total, page.raw_issues
        hidden = hide_assignee or self._settings.hide_users
        cleaned = []
        for issue in issues:
            safe = hide_users(issue)  # text without mentions and e-mail addresses, no people, no raw JSON
            if not hidden:  # people may be shown: keep the assignee, and the reporter or other people the caller asked for
                safe = replace(safe, assignee=issue.assignee, reporter=issue.reporter, extra=issue.extra)
            cleaned.append(safe)
        if hidden:
            raw = [strip_users(item) for item in raw]
        return SearchResult(cleaned, total, text, hidden, raw)


class SuggestBranch:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, key: str, *, branch_type: str = "") -> str:
        key = parse_issue_key(key)
        issue = self._gateway.get_issue(key, fields=["summary", "issuetype"])
        return branch_name(issue.key, issue.summary, issue.issue_type, branch_type)


class BuildReport:
    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(self, *, project: str = "", sprint: str = "", fix_version: str = "", per_epic: bool = False) -> Report:
        project = require_project(project, self._settings)
        query, scope = jql.report_search(project, sprint=sprint, fix_version=fix_version)
        ids = self._gateway.field_ids()
        if per_epic and not ids.get(EPIC_LINK):
            raise ValidationError("No Jira field named 'Epic Link'; --epics is not available.")
        if not ids.get(STORY_POINTS):
            self._diagnostics.warn("No Jira field named 'Story Points': points are reported as 0.")
        issues, _, _ = fetch_all(self._gateway, query, REPORT_FIELDS)
        epics: dict[str, str] = {}
        if per_epic:
            epic_issues, _, _ = fetch_all(self._gateway, jql.epics_search(project), ["summary"])
            epics = {epic.key: redact_text(epic.summary) for epic in epic_issues}
        return build_report(issues, scope, epics=epics, per_epic=per_epic)


class ExportBacklog:
    """Every issue of a project as a backlog dump. Refuses to return a partial dump."""

    def __init__(self, gateway: JiraGateway, settings: Settings, clock: Clock) -> None:
        self._gateway = gateway
        self._settings = settings
        self._clock = clock

    def execute(self, *, project: str = "") -> dict[str, Any]:
        project = require_project(project, self._settings)
        issues, total, _ = fetch_all(self._gateway, jql.backlog_search(project), BACKLOG_FIELDS)
        if len(issues) != total:
            raise UnavailableError(
                f"Jira reports {total} issues but {len(issues)} were fetched; nothing was exported.",
                hint="Run the export again.",
            )
        fetched_at = self._clock.now().isoformat(timespec="seconds")
        return build_backlog_dump(issues, self._gateway.field_ids(), fetched_at)


class ListFields:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, *, search: str = "", custom_only: bool = False, refresh: bool = False) -> list[FieldInfo]:
        wanted = search.strip().casefold()
        fields = self._gateway.list_fields(refresh=refresh)
        return sorted(
            (
                info
                for info in fields
                if (not custom_only or info.custom) and (not wanted or wanted in info.name.casefold() or wanted in info.id.casefold())
            ),
            key=lambda info: (info.name.casefold(), info.id),
        )
