"""Use cases that create or change issues."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.drafts import (
    IssueChanges,
    IssueDraft,
    resolve_issue_type,
    validate_create_payload,
    validate_update_payload,
)
from ..domain.errors import ConfigError, ValidationError
from ..domain.model import CreatedIssue, parse_issue_key, parse_project_key
from ..domain.timeutil import ensure_unchanged_since
from .ports import JiraGateway
from .settings import Settings


@dataclass
class WriteResult:
    action: str  # "created" | "updated"
    key: str
    payload: dict[str, Any]
    dry_run: bool
    created: CreatedIssue | None = None


def _apply_update(
    gateway: JiraGateway, key: str, payload: dict[str, Any], dry_run: bool, if_unchanged_since: str
) -> WriteResult:
    if dry_run or if_unchanged_since:
        current = gateway.get_issue(key, fields=["summary", "updated"])  # exists, and is unchanged
        if if_unchanged_since:
            ensure_unchanged_since(key, current.updated, if_unchanged_since)
    if not dry_run:
        gateway.update_issue(key, payload)
    return WriteResult("updated", key, payload, dry_run)


class UpdateIssue:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(
        self, key: str, changes: IssueChanges, *, dry_run: bool = False, if_unchanged_since: str = ""
    ) -> WriteResult:
        key = parse_issue_key(key)
        if changes.is_empty():
            raise ValidationError(
                "No update fields supplied.", hint="Pass at least one option such as --summary, or --fields-json."
            )
        return _apply_update(self._gateway, key, changes.to_payload(), dry_run, if_unchanged_since)


class UpdateIssueFromPayload:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(
        self, key: str, payload: Any, *, dry_run: bool = False, if_unchanged_since: str = ""
    ) -> WriteResult:
        key = parse_issue_key(key)
        return _apply_update(self._gateway, key, validate_update_payload(payload), dry_run, if_unchanged_since)


class CreateIssue:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, draft: IssueDraft, *, dry_run: bool = False, refresh_metadata: bool = False) -> WriteResult:
        draft.project = draft.project or self._settings.project
        draft.validate()
        draft.project = parse_project_key(draft.project)
        if draft.issue_type.strip().isdigit():
            type_id = draft.issue_type.strip()
        else:
            types = self._gateway.list_issue_types(draft.project, refresh=refresh_metadata)
            type_id = resolve_issue_type(types, draft.issue_type).id
        reporter = draft.reporter or self._settings.reporter or self._gateway.current_user().name
        if not reporter:
            raise ConfigError("A reporter is required.", hint="Set JIRA_REPORTER or pass --reporter.")
        payload = draft.to_payload(type_id, reporter)
        if dry_run:
            return WriteResult("created", "", payload, True)
        created = self._gateway.create_issue(payload)
        return WriteResult("created", created.key, payload, False, created)


class CreateIssueFromPayload:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, payload: Any, *, dry_run: bool = False) -> WriteResult:
        validate_create_payload(payload)
        if dry_run:
            return WriteResult("created", "", payload, True)
        created = self._gateway.create_issue(payload)
        return WriteResult("created", created.key, payload, False, created)
