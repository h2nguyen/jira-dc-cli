"""Keeping personal data out of what the CLI prints: mentions, e-mail addresses, people."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .jqlpeople import redact_jql
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
    """Replace every Jira user object in raw JSON with a marker and redact all text. The two structures that carry a person's name
    without a user object are cleaned as well: the items of a changelog for a user field, and the JQL of a saved filter."""
    if looks_like_user(value):
        return {"hidden": True}
    if isinstance(value, dict):
        cleaned = {key: strip_users(child) for key, child in value.items()}
        _hide_names_of_a_change(value, cleaned)
        _hide_people_in_a_filter(value, cleaned)
        return cleaned
    if isinstance(value, list):
        return [strip_users(child) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


_USER_FIELDS_OF_A_CHANGE = frozenset({"assignee", "reporter", "creator", "watcher", "voter"})
_VALUES_OF_A_CHANGE = ("from", "to", "fromString", "toString")  # the user key, then the display name


def _hide_names_of_a_change(raw: dict[str, Any], cleaned: dict[str, Any]) -> None:
    """A changelog item for a user field holds the user key (`from`, `to`) and the display name (`fromString`, `toString`)."""
    if str(raw.get("field", "")).casefold() in _USER_FIELDS_OF_A_CHANGE:
        for key in _VALUES_OF_A_CHANGE:
            if raw.get(key):
                cleaned[key] = None


def _hide_people_in_a_filter(raw: dict[str, Any], cleaned: dict[str, Any]) -> None:
    """The `jql` of a saved filter names people (`assignee = jdoe`, an address in `text ~ "..."`); its `searchUrl` holds the same text,
    percent-encoded, which no pattern for the plain text finds: the address goes as soon as anything in the JQL had to be redacted."""
    jql = raw.get("jql")
    if isinstance(jql, str):
        redacted = redact_text(redact_jql(jql))
        if redacted != jql:
            cleaned["jql"] = redacted
            if "searchUrl" in cleaned:
                cleaned["searchUrl"] = ""


def redact_json(value: Any) -> Any:
    """Raw JSON with all text redacted and the objects left as they are (what `strip_users` does for the privacy policy, minus the people)."""
    if isinstance(value, dict):
        return {key: redact_json(child) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_json(child) for child in value]
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
        attachments=[replace(attachment, author=None) for attachment in issue.attachments],
        extra=strip_users(issue.extra),
        raw=None,
    )


def hide_comment_author(comment: Comment) -> Comment:
    return replace(comment, author=None, body=redact_text(comment.body), rendered_body=redact_text(comment.rendered_body))


def _redact_ref(ref: IssueRef) -> IssueRef:
    return replace(ref, summary=redact_text(ref.summary))


__all__ = [
    "PLACEHOLDERS",
    "hide_comment_author",
    "hide_users",
    "holds_placeholder",
    "looks_like_user",
    "normalize_newlines",
    "redact_json",
    "redact_text",
    "strip_users",
]
