"""Keeping personal data out of what the CLI prints: mentions, e-mail addresses, people."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .model import Comment, Issue, IssueRef

# Each pattern is bounded or anchored so that a hostile text of 32,000 characters cannot make it quadratic.
_MENTION = re.compile(r"\[~[^\]]{1,200}\]")
_PERSONAL_LINK = re.compile(r"https?://\S{0,500}/personal/\S*")
# A run of name characters is tried only from its beginning. The last label of a domain begins with a letter, as a top level domain does:
# `lodash@4.17.21` is a package and its version, no address. A login on a host address (`jdoe@10.1.2.3`) is redacted all the same.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[\w-]+(?:\.[\w-]+)*\.[^\W\d_][\w-]*|(?:\d{1,3}\.){3}\d{1,3})")
PLACEHOLDERS = ("[~user]", "[personal link]", "[email]")  # what the CLI writes where Jira holds personal data


def normalize_newlines(text: Any) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def redact_text(text: Any) -> str:
    """Remove user mentions, personal share links and e-mail addresses from free text."""
    cleaned = _PERSONAL_LINK.sub("[personal link]", _MENTION.sub("[~user]", str(text or "")))
    return _EMAIL.sub("[email]", cleaned)


def holds_placeholder(text: Any) -> bool:
    """Whether a text holds what `redact_text` writes in place of personal data. Sending it back would overwrite the real text."""
    value = str(text or "")
    return any(mark in value for mark in PLACEHOLDERS)


def looks_like_user(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and "displayName" in value
        and any(key in value for key in ("emailAddress", "avatarUrls", "key", "name"))
    )


def strip_users(value: Any) -> Any:
    """Replace every Jira user object in raw JSON with a marker and redact all text."""
    if looks_like_user(value):
        return {"hidden": True}
    if isinstance(value, dict):
        return {key: strip_users(child) for key, child in value.items()}
    if isinstance(value, list):
        return [strip_users(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def hide_users(issue: Issue) -> Issue:
    """A copy of the issue without people and with redacted text. The names that people give (labels, components, versions, sprints) are redacted too."""
    links = [replace(link, other=_redact_ref(link.other)) for link in issue.links]
    return replace(
        issue,
        summary=redact_text(issue.summary),
        description=redact_text(issue.description),
        acceptance=redact_text(issue.acceptance),
        labels=[redact_text(label) for label in issue.labels],
        components=[redact_text(name) for name in issue.components],
        fix_versions=[redact_text(name) for name in issue.fix_versions],
        sprints=[replace(sprint, name=redact_text(sprint.name)) for sprint in issue.sprints],
        assignee=None,
        reporter=None,
        links=links,
        subtasks=[_redact_ref(ref) for ref in issue.subtasks],
        comments=[hide_comment_author(comment) for comment in issue.comments],
        extra=strip_users(issue.extra),
        raw=None,
    )


def hide_comment_author(comment: Comment) -> Comment:
    return replace(comment, author=None, body=redact_text(comment.body))


def _redact_ref(ref: IssueRef) -> IssueRef:
    return replace(ref, summary=redact_text(ref.summary))


__all__ = [
    "PLACEHOLDERS",
    "hide_comment_author",
    "hide_users",
    "holds_placeholder",
    "looks_like_user",
    "normalize_newlines",
    "redact_text",
    "strip_users",
]
