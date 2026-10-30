"""Rules for comments: a pseudonym for an author, which comments are wanted, what an edit sends and what it changes."""
from __future__ import annotations

import difflib
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from .durations import parse_duration
from .errors import ConflictError, ValidationError
from .model import Comment
from .payloads import comment_payload
from .privacy import PLACEHOLDERS, normalize_newlines
from .timeutil import parse_timestamp

VISIBILITY_TYPES = ("role", "group")
SMALL_EDIT = 3000  # characters of both texts together up to which a dry run shows a diff and not the two texts
_COMMENT_ID = re.compile(r"[0-9]{1,18}")  # ASCII digits only: `\d` takes the digits of other scripts too
_AGO = re.compile(r"[0-9]+(?:\.[0-9]+)?[smhdw]", re.IGNORECASE)
_UNKNOWN = datetime.min.replace(tzinfo=timezone.utc)  # the moment of a comment whose `created` cannot be read: older than any
# What a mention `[~jdoe]` becomes when Jira renders it: a link to the profile whose text is the display name.
_ANCHOR = re.compile(r"<a\b", re.IGNORECASE)
_END_ANCHOR = re.compile(r"</a\s*>", re.IGNORECASE)
_PERSON = re.compile(r"user-hover|ViewProfile\.jspa", re.IGNORECASE)
_TAG_LIMIT, _TEXT_LIMIT = 1000, 1500  # the longest tag and link text that count; a display name has at most 255 characters, before escaping


def hide_mentions(html: str) -> str:
    """The HTML of a rendered text with every link to a person replaced by `[~user]`: the text of such a link is a display name."""
    text = html or ""
    parts: list[str] = []
    position = 0
    closer: re.Match[str] | bool | None = None  # the next closing tag, found once for all the links that come before it; False: none left
    while True:
        start = _ANCHOR.search(text, position)
        tag_end = text.find(">", start.end()) if start else -1
        if start is None or tag_end < 0:
            break
        tag = text[start.start() : tag_end + 1]
        closing = None
        if len(tag) <= _TAG_LIMIT and _PERSON.search(tag):
            if closer is None or (closer and closer.start() <= tag_end):
                closer = _END_ANCHOR.search(text, tag_end + 1) or False
            if closer and closer.start() - (tag_end + 1) <= _TEXT_LIMIT:
                closing = closer
        if closing:
            parts += [text[position : start.start()], "[~user]"]
            position = closing.end()
        else:
            parts.append(text[position : tag_end + 1])
            position = tag_end + 1
    parts.append(text[position:])
    return "".join(parts)


def parse_comment_id(text: str) -> str:
    value = (text or "").strip()
    if not _COMMENT_ID.fullmatch(value):
        raise ValidationError(
            f"Invalid comment id: {value or '<missing>'}", hint="A comment id is a number; `jira comment ISSUE_KEY --list` shows them."
        )
    return value


def author_ref(secret: str | bytes, base_url: str, author: str) -> str:
    """A stable pseudonym for an author, so that comments of one person can be told from those of another while names are hidden.

    It is the start of an HMAC-SHA-256 over the address of the Jira and the lower-cased user name, keyed with `secret`: the same author gives
    the same text on every call that has the same secret, and nobody without the secret can compute the text of a user name that they know,
    or find the name behind a text. `secret` must not be empty: without a key the text could be recomputed from a name."""
    key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if not key:
        raise ValueError("author_ref needs a secret: without one a pseudonym could be recomputed from a user name")
    name = (author or "").strip().casefold()
    if not name:
        return ""
    message = f"jira-dc-cli authorRef 1\n{(base_url or '').strip().rstrip('/').casefold()}\n{name}"
    return "a-" + hmac.new(key, message.encode("utf-8"), hashlib.sha256).hexdigest()[:8]


def parse_since(text: str, now: datetime) -> datetime:
    """The moment that `--since` names: a time ago (`2d`, `12h`, `1w`), a date (`2026-10-15`) or an ISO 8601 time. A date or a time
    without an offset counts as UTC."""
    value = (text or "").strip()
    try:
        if _AGO.fullmatch(value):
            return now - timedelta(seconds=parse_duration(value))
        return parse_timestamp(value)
    except (ValidationError, OverflowError) as error:
        raise ValidationError(
            f"Not a time for --since: {text}",
            hint="Write a time ago (2d, 12h, 1w), a date (2026-10-15) or an ISO 8601 time (2026-10-15T08:00:00Z).",
        ) from error


def _created(comment: Comment) -> datetime | None:
    try:
        return parse_timestamp(comment.created)
    except ValidationError:
        return None


def select_comments(comments: Sequence[Comment], *, since: datetime | None = None, last: int = 0) -> list[Comment]:
    """The comments created at or after `since` and, of those, the `last` newest (all when `last` is 0), in the order Jira lists them."""
    chosen = list(enumerate(comments))
    if since is not None:
        chosen = [(index, comment) for index, comment in chosen if (_created(comment) or _UNKNOWN) >= since]
    if last > 0:
        newest = sorted(chosen, key=lambda item: (_created(item[1]) or _UNKNOWN, item[0]))[-last:]
        chosen = sorted(newest, key=lambda item: item[0])
    return [comment for _, comment in chosen]


def visibility_object(text: str) -> dict[str, str] | None:
    """The `visibility` of a comment payload for the text that `Comment.visibility` holds (`role:Developers`, `group:staff`), else None."""
    kind, _, name = (text or "").partition(":")
    return {"type": kind, "value": name} if kind and name else None


def parse_visible_to(text: str) -> dict[str, str]:
    """`--visible-to role:NAME` or `group:NAME` as the `visibility` of a comment payload."""
    kind, _, name = (text or "").strip().partition(":")
    kind, name = kind.strip().casefold(), name.strip()
    if kind not in VISIBILITY_TYPES or not name:
        raise ValidationError(
            f"Not a restriction for --visible-to: {text}",
            hint="Write role:NAME or group:NAME, for example role:Developers or group:jira-staff.",
        )
    return {"type": kind, "value": name}


def edit_payload(body: str, visibility: str = "") -> dict[str, Any]:
    """What an edit sends: the new text and the restriction the comment has now (Jira may drop one that is not sent again)."""
    return comment_payload(body, visibility_object(visibility))


def is_unchanged(current: str, new: str) -> bool:
    return normalize_newlines(current).strip() == normalize_newlines(new).strip()


def placeholders_added(current: str, new: str) -> list[str]:
    """The stand-ins for personal data (`[~user]`, `[email]`, `[personal link]`) that the new text holds and the current text does not:
    they come from a reading that hid the real mention or address, and sending them back would overwrite it."""
    return [mark for mark in PLACEHOLDERS if mark in new and mark not in current]


def describe_change(current: str, new: str) -> str:
    """For a dry run: a unified diff of the two texts when they are small, else an empty text (the dry run then shows both texts)."""
    before, after = normalize_newlines(current).strip(), normalize_newlines(new).strip()
    if before == after or len(before) + len(after) > SMALL_EDIT:
        return ""
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), "current", "new", lineterm="", n=2))


def ensure_comment_unchanged_since(key: str, comment: Comment, since: str) -> None:
    """Raise a conflict when the comment changed after `since`. Jira has no precondition for a comment: this is a check before a write."""
    changed_at = parse_timestamp(comment.updated or comment.created)
    limit = parse_timestamp(since)
    if changed_at > limit:
        raise ConflictError(
            f"Comment {comment.id} of {key} changed in Jira at {changed_at.isoformat()}, after {limit.isoformat()}.",
            hint=f"Read it again with `jira comment {key} --get {comment.id}` and review the current text before you edit it.",
            details={"key": key, "commentId": comment.id, "updated": changed_at.isoformat(), "since": limit.isoformat()},
        )
