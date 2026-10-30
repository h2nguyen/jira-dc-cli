"""Port operations for reference: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.refdata import (
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


class ReferencePort(ABC):
    """What the application asks of Jira for reference: lists and facts about the instance. Everything here only reads.

    Add an abstract method here only after `adapters/driven/rest_reference.py` (the real adapter) and `tests/support/fakes_reference.py`
    (the test double) implement it: `JiraGateway` cannot be instantiated while one of them lacks it.
    """

    @abstractmethod
    def list_priorities(self) -> list[RefItem]: ...

    @abstractmethod
    def list_resolutions(self) -> list[RefItem]: ...

    @abstractmethod
    def list_all_issue_types(self) -> list[IssueTypeDetail]:
        """Every issue type of the instance."""

    @abstractmethod
    def list_project_issue_types(self, project_key: str) -> list[IssueTypeDetail]:
        """The issue types that a project uses."""

    @abstractmethod
    def list_all_statuses(self) -> list[StatusDetail]: ...

    @abstractmethod
    def list_project_statuses(self, project_key: str) -> list[TypeStatuses]:
        """Per issue type of the project, the statuses of its workflow."""

    @abstractmethod
    def list_project_components(self, project_key: str) -> list[ComponentInfo]: ...

    @abstractmethod
    def list_project_versions(self, project_key: str) -> list[VersionInfo]:
        """The versions of a project in the order Jira keeps them, without counts."""

    @abstractmethod
    def get_version_counts(self, version_id: str) -> VersionCounts:
        """The number of issues of a version: two requests."""

    @abstractmethod
    def get_my_permissions(self, *, project_key: str = "", issue_key: str = "") -> list[PermissionInfo]:
        """Every permission of the instance and whether the token's user has it, in a project or on an issue when one is given."""

    @abstractmethod
    def list_project_roles(self, project_key: str) -> list[RoleRef]:
        """The roles of a project: names and ids, without the people in them."""

    @abstractmethod
    def get_project_role(self, project_key: str, role_id: str) -> ProjectRole:
        """One role of a project with its actors (people and groups)."""

    @abstractmethod
    def list_dashboards(self, *, filter_name: str = "", start_at: int = 0, max_results: int = 50) -> DashboardPage:
        """One page of dashboards; `filter_name` is `my` or `favourite`, empty for every dashboard that the user can see."""

    @abstractmethod
    def get_server_settings(self) -> ServerSettings:
        """The optional features of the Jira (`GET /configuration`)."""

    @abstractmethod
    def get_attachment_upload_limit(self) -> int | None:
        """The largest upload in bytes that Jira accepts; None when it does not say."""
