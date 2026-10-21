"""Use cases that change an issue's state: transitions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.errors import ValidationError
from ..domain.model import Transition, parse_issue_key
from ..domain.payloads import transition_payload
from ..domain.transitions import pick_transition
from .ports import JiraGateway


@dataclass
class TransitionResult:
    key: str
    current_status: str
    options: list[Transition] = field(default_factory=list)
    chosen: Transition | None = None
    dry_run: bool = False
    executed: bool = False
    payload: dict[str, Any] | None = None


class TransitionIssue:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, key: str, *, to: str = "", comment: str = "", dry_run: bool = False) -> TransitionResult:
        """Without `to`, only lists what is possible from the current status."""
        key = parse_issue_key(key)
        if comment.strip() and not to.strip():
            raise ValidationError("--comment needs --to: a comment is added together with a transition.")
        issue = self._gateway.get_issue(key, fields=["status"])
        current = issue.status.name if issue.status else ""
        options = self._gateway.list_transitions(key)
        if not to.strip():
            return TransitionResult(key, current, options)
        chosen = pick_transition(options, to)
        payload = transition_payload(chosen.id, comment)
        if dry_run:
            return TransitionResult(key, current, options, chosen, dry_run=True, payload=payload)
        self._gateway.transition_issue(key, payload)
        return TransitionResult(key, current, options, chosen, executed=True, payload=payload)
