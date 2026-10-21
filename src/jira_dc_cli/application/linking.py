"""Use cases around issue links."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.errors import ValidationError
from ..domain.links import find_relation, link_ends, relation_phrase, relation_text, resolve_link_type
from ..domain.model import LinkType, parse_issue_key
from ..domain.payloads import link_payload
from .ports import JiraGateway


@dataclass
class LinkResult:
    key: str
    other: str
    type_name: str
    phrase: str
    relation: str
    payload: dict[str, Any]
    dry_run: bool
    confirmed: str | None = None


class ListLinkTypes:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self) -> list[LinkType]:
        return sorted(self._gateway.list_link_types(), key=lambda link_type: link_type.name)


class LinkIssues:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, key: str, other: str, type_name: str, *, inverse: bool = False, dry_run: bool = False) -> LinkResult:
        key, other = parse_issue_key(key), parse_issue_key(other)
        if key == other:
            raise ValidationError("An issue cannot be linked to itself.")
        link_type = resolve_link_type(self._gateway.list_link_types(), type_name)
        phrase = relation_phrase(link_type, inverse)
        outward, inward = link_ends(key, other, inverse)
        payload = link_payload(link_type.name, outward, inward)
        relation = relation_text(key, phrase, other)
        if dry_run:
            self._gateway.get_issue(key, fields=["summary"])
            self._gateway.get_issue(other, fields=["summary"])
            return LinkResult(key, other, link_type.name, phrase, relation, payload, True)
        self._gateway.create_link(payload)
        stored = self._gateway.get_issue(key, fields=["issuelinks"])
        confirmed = find_relation(stored, other, link_type.name)
        return LinkResult(key, other, link_type.name, phrase, relation, payload, False, confirmed)
