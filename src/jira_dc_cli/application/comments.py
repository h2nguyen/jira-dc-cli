"""Use cases around comments."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.errors import ValidationError
from ..domain.model import Comment, parse_issue_key
from ..domain.payloads import comment_payload
from ..domain.privacy import hide_comment_author
from .ports import JiraGateway
from .settings import Settings


@dataclass
class CommentResult:
    key: str
    payload: dict[str, Any]
    dry_run: bool
    comment_id: str = ""


class AddComment:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, key: str, body: str, *, dry_run: bool = False) -> CommentResult:
        key = parse_issue_key(key)
        if not body.strip():
            raise ValidationError("The comment is empty.", hint="Pass text with --text or --file.")
        payload = comment_payload(body)
        if dry_run:
            self._gateway.get_issue(key, fields=["summary"])  # proves that the issue exists
            return CommentResult(key, payload, True)
        created = self._gateway.add_comment(key, payload)
        return CommentResult(key, payload, False, created.id)


class ListComments:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, key: str, *, with_users: bool = False) -> list[Comment]:
        key = parse_issue_key(key)
        if with_users and self._settings.hide_users:
            raise ValidationError(
                "JIRA_HIDE_USERS=true: people are not printed, --with-users is not available.",
                hint="Unset JIRA_HIDE_USERS if you need to see people.",
            )
        comments = self._gateway.list_comments(key)
        return comments if with_users else [hide_comment_author(comment) for comment in comments]
