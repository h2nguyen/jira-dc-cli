"""Use cases that create or change issues."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..domain.drafts import (
    IssueChanges,
    IssueDraft,
    issue_types_named,
    resolve_issue_type,
    validate_create_payload,
    validate_update_payload,
)
from ..domain.errors import ConfigError, ValidationError
from ..domain.model import CreatedIssue, IssueTypeInfo, parse_issue_key, parse_project_key
from ..domain.timeutil import ensure_unchanged_since
from ..domain.validation import (
    COMPARED,
    create_target,
    current_values,
    new_stand_ins,
    stand_in_note,
    stand_in_refusal,
)
from .ports import JiraGateway
from .settings import Settings
from .users import UserResolver
from .validation import IssueValidator, stand_ins_of


@dataclass
class WriteResult:
    action: str  # "created" | "updated"
    key: str
    payload: dict[str, Any]
    dry_run: bool
    created: CreatedIssue | None = None
    warnings: list[str] = field(default_factory=list)  # a dry run: what Jira may refuse (a required field left out, a field off the screen)


def _check_issue(
    gateway: JiraGateway, key: str, dry_run: bool, if_unchanged_since: str, stand_ins: Sequence[tuple[str, str]] = ()
) -> None:
    """The issue exists and, with `if_unchanged_since`, was not changed after that time: first, whatever else is wrong with the request.
    A new text or name that holds a stand-in for personal data that Jira does not hold is refused: it would overwrite the real mention or
    address that `jira issue` hides. One read serves all of it, and a live run without a guard reads only for such a text or name. The
    read always asks for the summary and `updated`, then for what a stand-in is compared with."""
    if not (dry_run or if_unchanged_since or stand_ins):
        return
    asked = {name for name, _ in stand_ins}
    current = gateway.get_issue(key, fields=["summary", "updated", *(name for name in COMPARED if name != "summary" and name in asked)])
    found = new_stand_ins(stand_ins, current_values(current))
    if found:
        raise stand_in_refusal(found, key)
    if if_unchanged_since:
        ensure_unchanged_since(key, current.updated, if_unchanged_since)


def _write_update(gateway: JiraGateway, validator: IssueValidator, key: str, payload: dict[str, Any], dry_run: bool) -> WriteResult:
    """A dry run checks the standard fields against the edit metadata; a live run sends the payload and leaves the checking to Jira."""
    if dry_run:
        return WriteResult("updated", key, payload, True, warnings=validator.check_update(key, payload))
    gateway.update_issue(key, payload)
    return WriteResult("updated", key, payload, False)


def _fields_of(changes: IssueChanges) -> dict[str, Any]:
    """The payload that `changes` will send, as far as its texts and names are concerned and without the checks that building it makes."""
    fields: dict[str, Any] = {}
    if changes.summary is not None:
        fields["summary"] = changes.summary
    if changes.description is not None:
        fields["description"] = changes.description
    if changes.labels is not None:
        fields["labels"] = list(changes.labels)
    if changes.components is not None:
        fields["components"] = [{"name": name} for name in changes.components]
    if changes.fix_versions is not None:
        fields["fixVersions"] = [{"name": name} for name in changes.fix_versions]
    return {"fields": {**fields, **changes.fields}}


class UpdateIssue:
    def __init__(self, gateway: JiraGateway, validator: IssueValidator | None = None, resolver: UserResolver | None = None) -> None:
        self._gateway = gateway
        self._validator = validator or IssueValidator(gateway)
        self._resolver = resolver or UserResolver(gateway)

    def execute(
        self, key: str, changes: IssueChanges, *, dry_run: bool = False, if_unchanged_since: str = ""
    ) -> WriteResult:
        key = parse_issue_key(key)
        if changes.is_empty():
            raise ValidationError(
                "No update fields supplied.", hint="Pass at least one option such as --summary, or --fields-json."
            )
        _check_issue(self._gateway, key, dry_run, if_unchanged_since, stand_ins_of(self._gateway, _fields_of(changes)))
        assignee = None if changes.assignee is None else self._resolver.assignee(changes.assignee, issue_key=key)
        return _write_update(self._gateway, self._validator, key, changes.to_payload(assignee), dry_run)


class UpdateIssueFromPayload:
    def __init__(self, gateway: JiraGateway, validator: IssueValidator | None = None) -> None:
        self._gateway = gateway
        self._validator = validator or IssueValidator(gateway)

    def execute(
        self, key: str, payload: Any, *, dry_run: bool = False, if_unchanged_since: str = ""
    ) -> WriteResult:
        key = parse_issue_key(key)
        checked = validate_update_payload(payload)
        _check_issue(self._gateway, key, dry_run, if_unchanged_since, stand_ins_of(self._gateway, checked))
        return _write_update(self._gateway, self._validator, key, checked, dry_run)


class CreateIssue:
    def __init__(
        self,
        gateway: JiraGateway,
        settings: Settings,
        validator: IssueValidator | None = None,
        resolver: UserResolver | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._validator = validator or IssueValidator(gateway)
        self._resolver = resolver or UserResolver(gateway)

    def execute(self, draft: IssueDraft, *, dry_run: bool = False, refresh_metadata: bool = False) -> WriteResult:
        draft.project = draft.project or self._settings.project
        draft.validate()
        draft.project = parse_project_key(draft.project)
        types: list[IssueTypeInfo] | None = None
        fresh = refresh_metadata  # whether the issue types were read from Jira just now and not from the cache
        wanted = draft.issue_type.strip()
        if wanted.isascii() and wanted.isdigit():
            type_id = wanted
        else:
            types = self._gateway.list_issue_types(draft.project, refresh=refresh_metadata)
            if not fresh and not issue_types_named(types, wanted):  # the cached list may lack a type that was added since
                types, fresh = self._gateway.list_issue_types(draft.project, refresh=True), True
            type_id = resolve_issue_type(types, wanted, fresh=fresh).id
        reporter = draft.reporter or self._settings.reporter or self._gateway.current_user().name
        if not reporter:
            raise ConfigError("A reporter is required.", hint="Set JIRA_REPORTER or pass --reporter.")
        assignee = self._resolver.assignee(draft.assignee, project_key=draft.project) if draft.assignee else None
        payload = draft.to_payload(type_id, reporter, assignee)
        if dry_run:
            warnings = self._validator.check_create(draft.project, type_id, payload, issue_types=types, refresh=fresh)
            return WriteResult("created", "", payload, True, warnings=warnings + _stand_in_notes(self._gateway, payload))
        created = self._gateway.create_issue(payload)
        return WriteResult("created", created.key, payload, False, created)


def _stand_in_notes(gateway: JiraGateway, payload: Mapping[str, Any]) -> list[str]:
    """For a dry run of a create: a note, no refusal (there is nothing to overwrite), when a new text or name holds a stand-in."""
    found = new_stand_ins(stand_ins_of(gateway, payload), {})
    return [stand_in_note(found)] if found else []


class CreateIssueFromPayload:
    def __init__(self, gateway: JiraGateway, validator: IssueValidator | None = None) -> None:
        self._gateway = gateway
        self._validator = validator or IssueValidator(gateway)

    def execute(self, payload: Any, *, dry_run: bool = False) -> WriteResult:
        validate_create_payload(payload)
        if dry_run:
            target = create_target(payload)
            warnings = self._validator.check_create(target[0], target[1], payload) if target else []
            return WriteResult("created", "", payload, True, warnings=warnings + _stand_in_notes(self._gateway, payload))
        created = self._gateway.create_issue(payload)
        return WriteResult("created", created.key, payload, False, created)
