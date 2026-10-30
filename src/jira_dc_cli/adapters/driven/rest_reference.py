"""The reference calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse
from typing import Any

from ...domain.refdata import (
    ComponentInfo,
    DashboardPage,
    IssueTypeDetail,
    PermissionInfo,
    ProjectRole,
    RefItem,
    RoleRef,
    ServerSettings,
    StatusDetail,
    TypeStatuses,
    VersionCounts,
    VersionInfo,
)
from . import map_reference as mapping
from . import map_values as values


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _query(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


class RestReference:
    """Implements `ReferencePort` (application/ports_reference.py) over Jira Data Center's REST API."""

    def list_priorities(self) -> list[RefItem]:
        return [mapping.ref_item_from(item) for item in values.objects(self._call("GET", "/priority"))]

    def list_resolutions(self) -> list[RefItem]:
        return [mapping.ref_item_from(item) for item in values.objects(self._call("GET", "/resolution"))]

    def list_all_issue_types(self) -> list[IssueTypeDetail]:
        return [mapping.issue_type_from(item) for item in values.objects(self._call("GET", "/issuetype"))]

    def list_project_issue_types(self, project_key: str) -> list[IssueTypeDetail]:
        data = self._call("GET", f"/project/{_quote(project_key)}?expand=issueTypes")
        return [mapping.issue_type_from(item) for item in values.objects(data.get("issueTypes") if isinstance(data, dict) else None)]

    def list_all_statuses(self) -> list[StatusDetail]:
        return [mapping.status_from(item) for item in values.objects(self._call("GET", "/status"))]

    def list_project_statuses(self, project_key: str) -> list[TypeStatuses]:
        data = self._call("GET", f"/project/{_quote(project_key)}/statuses")
        return [mapping.type_statuses_from(item) for item in values.objects(data)]

    def list_project_components(self, project_key: str) -> list[ComponentInfo]:
        data = self._call("GET", f"/project/{_quote(project_key)}/components")
        return [mapping.component_from(item) for item in values.objects(data)]

    def list_project_versions(self, project_key: str) -> list[VersionInfo]:
        data = self._call("GET", f"/project/{_quote(project_key)}/versions")
        return [mapping.version_from(item) for item in values.objects(data)]

    def get_version_counts(self, version_id: str) -> VersionCounts:
        base = f"/version/{_quote(version_id)}"
        return mapping.version_counts_from(self._call("GET", f"{base}/relatedIssueCounts"), self._call("GET", f"{base}/unresolvedIssueCount"))

    def get_my_permissions(self, *, project_key: str = "", issue_key: str = "") -> list[PermissionInfo]:
        params = {"issueKey": issue_key} if issue_key else {"projectKey": project_key} if project_key else {}
        return mapping.permissions_from(self._call("GET", "/mypermissions" + (f"?{_query(params)}" if params else "")))

    def list_project_roles(self, project_key: str) -> list[RoleRef]:
        return mapping.role_refs_from(self._call("GET", f"/project/{_quote(project_key)}/role"))

    def get_project_role(self, project_key: str, role_id: str) -> ProjectRole:
        return mapping.project_role_from(self._call("GET", f"/project/{_quote(project_key)}/role/{_quote(role_id)}"))

    def list_dashboards(self, *, filter_name: str = "", start_at: int = 0, max_results: int = 50) -> DashboardPage:
        params: dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if filter_name:
            params["filter"] = filter_name
        return mapping.dashboard_page_from(self._call("GET", f"/dashboard?{_query(params)}"))

    def get_server_settings(self) -> ServerSettings:
        return mapping.server_settings_from(self._call("GET", "/configuration"))

    def get_attachment_upload_limit(self) -> int | None:
        return mapping.upload_limit_from(self._call("GET", "/attachment/meta"))
