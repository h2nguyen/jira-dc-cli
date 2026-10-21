"""What to send to Jira when an issue is created or changed."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .errors import ValidationError
from .model import IssueTypeInfo


def split_list(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def parse_json_object(text: str, what: str = "JSON", *, hint: str = "") -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as error:  # a deeply nested text is no JSON object either
        raise ValidationError(f"{what} is not valid JSON: {error}", hint=hint) from error
    if not isinstance(value, dict):
        raise ValidationError(f"{what} must be a JSON object")
    return value


def _merge(named: dict[str, Any], extra: dict[str, Any], source: str = "--fields-json") -> dict[str, Any]:
    """The fields of the options and the extra fields in one dict. A field set twice is an error: which of the two
    should win is a guess, and a `project` or `issuetype` in the extra fields would silently undo a validated option."""
    clash = sorted(set(named) & set(extra))
    if clash:
        raise ValidationError(
            f"{', '.join(clash)} is set twice: by an option and in {source}.",
            hint=f"Set each field once: take it out of {source}, or leave the option out.",
        )
    return {**named, **extra}


@dataclass
class IssueChanges:
    """Fields to change. `None` means "leave as it is"; lists replace the current values."""

    summary: str | None = None
    description: str | None = None
    labels: list[str] | None = None
    components: list[str] | None = None
    fix_versions: list[str] | None = None
    priority: str | None = None
    assignee: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.fields and all(
            value is None
            for value in (
                self.summary,
                self.description,
                self.labels,
                self.components,
                self.fix_versions,
                self.priority,
                self.assignee,
            )
        )

    def to_payload(self) -> dict[str, Any]:
        named: dict[str, Any] = {}
        if self.summary is not None:
            named["summary"] = self.summary
        if self.description is not None:
            named["description"] = self.description
        if self.labels is not None:
            named["labels"] = list(self.labels)
        if self.components is not None:
            named["components"] = [{"name": name} for name in self.components]
        if self.fix_versions is not None:
            named["fixVersions"] = [{"name": name} for name in self.fix_versions]
        if self.priority is not None:
            named["priority"] = {"name": self.priority}
        if self.assignee is not None:
            named["assignee"] = {"name": self.assignee}
        return {"fields": _merge(named, self.fields)}


@dataclass
class IssueDraft:
    project: str
    issue_type: str
    summary: str
    description: str = ""
    labels: list[str] = field(default_factory=list)
    priority: str = ""
    assignee: str = ""
    components: list[str] = field(default_factory=list)
    fix_versions: list[str] = field(default_factory=list)
    reporter: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.project:
            raise ValidationError(
                "No project given.", hint="Pass --project KEY or select one with `jira use KEY`."
            )
        if not self.issue_type:
            raise ValidationError("An issue type is required.", hint="Pass --type NAME (for example Story).")
        if not self.summary:
            raise ValidationError("A summary is required.", hint="Pass --summary TEXT.")

    def to_payload(self, issue_type_id: str, reporter: str) -> dict[str, Any]:
        named: dict[str, Any] = {
            "project": {"key": self.project},
            "reporter": {"name": reporter},
            "issuetype": {"id": issue_type_id},
            "summary": self.summary,
        }
        if self.description:
            named["description"] = self.description
        if self.labels:
            named["labels"] = list(self.labels)
        if self.priority:
            named["priority"] = {"name": self.priority}
        if self.assignee:
            named["assignee"] = {"name": self.assignee}
        if self.components:
            named["components"] = [{"name": name} for name in self.components]
        if self.fix_versions:
            named["fixVersions"] = [{"name": name} for name in self.fix_versions]
        return {"fields": _merge(named, self.fields)}


def resolve_issue_type(types: list[IssueTypeInfo], query: str) -> IssueTypeInfo:
    """By id, else by name (case-insensitive). The names are those the project can create."""
    wanted = query.strip()
    matches = [info for info in types if info.id == wanted] or [
        info for info in types if info.name.casefold() == wanted.casefold()
    ]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(f"{info.name} ({info.id})" for info in sorted(types, key=lambda info: info.name))
    problem = "is ambiguous" if matches else "was not found"
    raise ValidationError(f"Issue type '{wanted}' {problem}.", hint=f"Available: {available}")


def validate_update_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not ("fields" in payload or "update" in payload):
        raise ValidationError("An update payload must be a JSON object with \"fields\" or \"update\".")
    return payload


def validate_create_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), dict):
        raise ValidationError("A create payload must be a JSON object with a \"fields\" object.")
    return payload
