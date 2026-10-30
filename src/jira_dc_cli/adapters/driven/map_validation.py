"""Jira's field metadata (create, edit and transition screens) as domain rules. Pure functions: no HTTP, no state."""
from __future__ import annotations

from typing import Any

from ...domain.validation import Allowed, FieldRule, FieldRules, TransitionScreen
from . import jira_mapper as mapper


def _allowed(item: Any) -> Allowed:
    """One of `allowedValues`: Jira names a value `name`, or `value` for the option of a custom field."""
    if isinstance(item, dict):
        return Allowed(str(item.get("id") or ""), str(item.get("name") or item.get("value") or item.get("key") or ""))
    return Allowed(name=str(item))


def rule_from(field_id: str, data: Any) -> FieldRule:
    meta = data if isinstance(data, dict) else {}
    values = meta.get("allowedValues")
    return FieldRule(
        field_id=field_id,
        name=str(meta.get("name") or ""),
        required=bool(meta.get("required")),
        has_default=bool(meta.get("hasDefaultValue")),
        allowed=tuple(_allowed(item) for item in values) if isinstance(values, list) else None,
    )


def rules_from_fields(fields: Any) -> FieldRules:
    """`fields` of the edit metadata or of a transition: an object keyed by field id."""
    return {str(field_id): rule_from(str(field_id), data) for field_id, data in (fields if isinstance(fields, dict) else {}).items()}


def rules_from_create(document: Any) -> FieldRules:
    """The create metadata of one issue type: `values` holds the fields, each with its `fieldId`."""
    items = document.get("values") if isinstance(document, dict) else document
    rules: FieldRules = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get("fieldId"):
            rules[str(item["fieldId"])] = rule_from(str(item["fieldId"]), item)
    return rules


def screens_from(data: Any) -> list[TransitionScreen]:
    """The transitions of `GET .../transitions?expand=transitions.fields`, each with the fields of its screen."""
    items = data.get("transitions") if isinstance(data, dict) else None
    return [
        TransitionScreen(mapper.transition_from(item), tuple(rules_from_fields(item.get("fields")).values()))
        for item in items or []
        if isinstance(item, dict)
    ]
