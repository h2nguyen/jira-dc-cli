"""Use cases around comments: add one, list them, read one, edit one."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from typing import Any

from ..domain.comments import (
    author_ref,
    describe_change,
    edit_payload,
    ensure_comment_unchanged_since,
    hide_mentions,
    is_unchanged,
    parse_comment_id,
    parse_since,
    parse_visible_to,
    placeholders_added,
    select_comments,
)
from ..domain.errors import AuthError, NotFoundError, RejectedError, ValidationError
from ..domain.htmltext import html_to_text
from ..domain.model import Comment, parse_issue_key
from ..domain.payloads import comment_payload
from ..domain.privacy import hide_comment_author, redact_text
from .ports import Clock, Diagnostics, Files, JiraGateway
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

    def execute(self, key: str, body: str, *, dry_run: bool = False, visible_to: str = "") -> CommentResult:
        key = parse_issue_key(key)
        if not body.strip():
            raise ValidationError("The comment is empty.", hint="Pass text with --text or --file.")
        payload = comment_payload(body, parse_visible_to(visible_to) if visible_to.strip() else None)
        if dry_run:
            self._gateway.get_issue(key, fields=["summary"])  # proves that the issue exists
            return CommentResult(key, payload, True)
        created = self._gateway.add_comment(key, payload)
        return CommentResult(key, payload, False, created.id)


def _refuse_people(settings: Settings, with_users: bool) -> None:
    if with_users and settings.hide_users:
        raise ValidationError(
            "JIRA_HIDE_USERS=true: people are not printed, --with-users is not available.",
            hint="Unset JIRA_HIDE_USERS if you need to see people.",
        )


def _read(gateway: JiraGateway, key: str, comment_id: str, *, rendered: bool = False) -> Comment:
    try:
        return gateway.get_comment(key, comment_id, rendered=rendered)
    except NotFoundError as error:
        raise NotFoundError(
            error.message,
            hint=f"Check the issue key and the comment id: `jira comment {key} --list` shows the ids of its comments.",
            details=error.details,
        ) from error


_SESSION_SECRET = secrets.token_hex(32)  # the key of the pseudonyms without a token: random, and the same for the whole process


def _reference_secret(settings: Settings) -> str:
    """The key of `author_ref`: the personal access token, which is never printed, so that nobody without it can compute the pseudonym of a
    user name that they know (`jira users resolve` tells user names). Nothing new is stored. Without a token, which a real run never
    has, a random key that lasts as long as the process: never the unkeyed hash."""
    return (settings.pat or "").strip() or _SESSION_SECRET


def _present(comment: Comment, *, me: str, base_url: str, secret: str, with_users: bool, rendered: bool) -> Comment:
    """The comment as it may be printed: `by_me` and `author_ref` say who wrote it without a name, the rendered text is plain text, and the
    author and the mentions and addresses in the text stay out unless people may be shown."""
    name = comment.author.name if comment.author else ""
    html = comment.rendered_body if with_users else hide_mentions(comment.rendered_body)
    shown = replace(
        comment,
        by_me=name.casefold() == me.casefold() if name and me else None,
        author_ref=author_ref(secret, base_url, name),
        rendered_body=html_to_text(html) if rendered else "",
    )
    return shown if with_users else hide_comment_author(shown)


class GetComment:
    """One comment: its times, its text as stored (or as Jira renders it), who may see it, and whether it is yours."""

    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, key: str, comment_id: str, *, with_users: bool = False, rendered: bool = False) -> Comment:
        key, comment_id = parse_issue_key(key), parse_comment_id(comment_id)
        _refuse_people(self._settings, with_users)
        comment = _read(self._gateway, key, comment_id, rendered=rendered)
        me = self._gateway.current_user().name
        secret = _reference_secret(self._settings)
        return _present(comment, me=me, base_url=self._settings.base_url, secret=secret, with_users=with_users, rendered=rendered)


@dataclass
class CommentList:
    key: str
    total: int  # the comments that the issue has
    comments: list[Comment] = field(default_factory=list)  # those that were asked for
    since: str = ""  # the moment that --since named, ISO 8601; empty without it


class FindComments:
    """The comments of an issue, all of them or the recent ones (`since`, `last`), each with `by_me` and a pseudonym of its author."""

    def __init__(self, gateway: JiraGateway, settings: Settings, clock: Clock) -> None:
        self._gateway = gateway
        self._settings = settings
        self._clock = clock

    def execute(self, key: str, *, with_users: bool = False, rendered: bool = False, since: str = "", last: int = 0) -> CommentList:
        key = parse_issue_key(key)
        _refuse_people(self._settings, with_users)
        if last < 0:
            raise ValidationError("--last must be at least 1.", hint="--last 3 lists the three newest comments.")
        cutoff = parse_since(since, self._clock.now()) if since.strip() else None
        comments = self._gateway.fetch_comments(key, rendered=rendered)
        chosen = select_comments(comments, since=cutoff, last=last)
        me = self._gateway.current_user().name if chosen else ""
        secret = _reference_secret(self._settings)
        base = self._settings.base_url
        shown = [_present(item, me=me, base_url=base, secret=secret, with_users=with_users, rendered=rendered) for item in chosen]
        return CommentList(key, len(comments), shown, cutoff.isoformat() if cutoff else "")


@dataclass
class EditResult:
    key: str
    comment_id: str
    action: str  # "updated", or "unchanged" when the comment already has this text and nothing is sent
    dry_run: bool
    payload: dict[str, Any]
    updated: str  # when the comment was last changed: after a live edit the new time, else the time it has now
    by_me: bool | None = None  # a dry run: whether the token's user wrote it (if not, Jira refuses unless it may edit all comments)
    current_body: str = ""  # the text before the edit, without mentions and addresses of people: what puts it back
    diff: str = ""  # a dry run of a small comment: the unified diff of the current and the new text
    warnings: list[str] = field(default_factory=list)


_EDIT_DENIED = (
    "Only the author of a comment, or someone with the permission to edit all comments ('Edit All Comments'), may change it; "
    "'Edit Own Comments' covers a comment of one's own. Ask a Jira administrator, or add a new comment."
)


class EditComment:
    """Replaces the text of a comment and keeps who may see it. Nothing else of the comment changes, and a comment is never deleted."""

    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(
        self, key: str, comment_id: str, body: str, *, dry_run: bool = False, if_unchanged_since: str = ""
    ) -> EditResult:
        key, comment_id = parse_issue_key(key), parse_comment_id(comment_id)
        if not body.strip():
            raise ValidationError(
                "The new text of the comment is empty.", hint="Pass the text with --text or --file. A comment cannot be deleted here."
            )
        current = _read(self._gateway, key, comment_id)
        added = placeholders_added(current.body, body)
        if added:
            raise ValidationError(
                f"The new text holds {', '.join(added)}, which stands in place of a mention or an address that the CLI hides: "
                "sending it would overwrite the real one.",
                hint="Write the real mention ([~username], see `jira users resolve`) or leave that part out. "
                "Edit a comment that holds people in Jira itself.",
            )
        if if_unchanged_since:
            ensure_comment_unchanged_since(key, current, if_unchanged_since)
        payload = edit_payload(body, current.visibility)
        unchanged = is_unchanged(current.body, payload["body"])
        shown = redact_text(current.body)
        if dry_run:
            return self._preview(key, current, payload, shown, unchanged)
        if unchanged:
            return EditResult(key, comment_id, "unchanged", False, payload, current.updated or current.created, current_body=shown)
        if if_unchanged_since:  # read again right before the write: the comment may have changed meanwhile
            current = _read(self._gateway, key, comment_id)
            ensure_comment_unchanged_since(key, current, if_unchanged_since)
            payload = edit_payload(body, current.visibility)
        saved = self._save(key, comment_id, payload)
        return EditResult(key, comment_id, "updated", False, payload, saved.updated or current.updated, current_body=shown)

    def _preview(self, key: str, current: Comment, payload: dict[str, Any], shown: str, unchanged: bool) -> EditResult:
        author = current.author.name if current.author else ""
        me = self._gateway.current_user().name if author else ""
        by_me = author.casefold() == me.casefold() if author and me else None
        warnings = []
        if by_me is False:
            warnings.append("Someone else wrote this comment: Jira refuses the edit unless the token's user may edit all comments.")
        return EditResult(
            key,
            current.id,
            "unchanged" if unchanged else "updated",
            True,
            payload,
            current.updated or current.created,
            by_me,
            shown,
            describe_change(shown, payload["body"]),
            warnings,
        )

    def _save(self, key: str, comment_id: str, payload: dict[str, Any]) -> Comment:
        try:
            return self._gateway.update_comment(key, comment_id, payload)
        except AuthError as error:
            if error.details.get("status", 403) != 403:
                raise
            raise AuthError(error.message, hint=_EDIT_DENIED, details=error.details) from error
        except RejectedError as error:
            hint = "Jira's message says why. Check the text, and for a restricted comment that its role or group still exists."
            raise RejectedError(error.message, hint=hint, details=error.details) from error


@dataclass
class CommentsUseCases:
    """The use cases that read and correct comments, reached as `app.comments`: one field per use case, built in `build`.
    `AddComment` stays where it is (`app.add_comment`)."""

    get: GetComment
    find: FindComments
    edit: EditComment


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> CommentsUseCases:
    return CommentsUseCases(get=GetComment(gateway, settings), find=FindComments(gateway, settings, clock), edit=EditComment(gateway))
