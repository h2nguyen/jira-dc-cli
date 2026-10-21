"""Use cases around projects and create metadata."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..domain.errors import JiraCliError, UsageError
from ..domain.model import Project, parse_project_key
from .ports import Diagnostics, JiraGateway
from .settings import Settings


def _apply_policy(project: Project, settings: Settings) -> Project:
    return replace(project, lead=None) if settings.hide_users else project


class ListProjects:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self) -> list[Project]:
        projects = sorted(self._gateway.list_projects(), key=lambda project: project.key)
        return [_apply_policy(project, self._settings) for project in projects]


@dataclass
class AdminProjects:
    projects: list[Project]
    checked: int
    failed: list[tuple[str, str]] = field(default_factory=list)


class ListAdminProjects:
    """The projects in which the token's user may administer the project (one request each)."""

    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(
        self, *, timeout: float = 15.0, retries: int = 0, progress: bool = True, fail_fast: bool = False
    ) -> AdminProjects:
        projects = sorted(self._gateway.list_projects(), key=lambda project: project.key)
        admins: list[Project] = []
        failed: list[tuple[str, str]] = []
        for index, project in enumerate(projects, start=1):
            if progress:
                self._diagnostics.info(f"Checking project {project.key} ({index}/{len(projects)})...")
            try:
                if self._gateway.has_permission(project.key, "ADMINISTER_PROJECTS", timeout=timeout, retries=retries):
                    admins.append(_apply_policy(project, self._settings))
            except JiraCliError as error:
                failed.append((project.key, error.message))
                self._diagnostics.warn(f"Permission check failed for {project.key}: {error.message}")
                if fail_fast:
                    raise
        return AdminProjects(admins, len(projects), failed)


class ShowProject:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, key: str = "") -> Project:
        chosen = key or self._settings.project
        if not chosen:
            raise UsageError("No project given and none selected.", hint="Pass a project key or run `jira use KEY`.")
        return _apply_policy(self._gateway.get_project(parse_project_key(chosen)), self._settings)


class ShowCreateMetadata:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, project: str, issue_type_id: str = "") -> dict[str, Any]:
        return self._gateway.create_metadata(parse_project_key(project), issue_type_id.strip())
