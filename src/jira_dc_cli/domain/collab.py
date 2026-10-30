"""What people do together on an issue, as plain data: watchers, votes, work logged and web links, and the small requests that change them."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError
from .model import User

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAX_USER_NAME = 255


@dataclass(frozen=True)
class Watchers:
    count: int
    watching: bool  # whether the token's user watches the issue
    users: list[User] | None = None  # None: not listed (Jira lists them only to who may see them) or not asked for


@dataclass(frozen=True)
class Votes:
    """The votes of an issue. The voters are not part of it: the CLI never reads them."""

    count: int
    has_voted: bool  # whether the token's user voted


@dataclass(frozen=True)
class Worklog:
    id: str
    started: str
    time_spent: str
    time_spent_seconds: int
    comment: str = ""
    author: User | None = None
    created: str = ""
    updated: str = ""
    visibility: str = ""  # "role:Developers" or "group:staff" when the entry is restricted


@dataclass(frozen=True)
class WorklogPage:
    total: int  # what Jira says the issue holds; it can exceed what came back
    worklogs: list[Worklog]


@dataclass(frozen=True)
class RemoteLink:
    id: str
    title: str
    url: str
    relationship: str = ""
    application: str = ""
    global_id: str = ""


@dataclass(frozen=True)
class RemoteLinkRef:
    """What Jira answers to a created or updated web link: the id, nothing more."""

    id: str


def user_name(text: str) -> str:
    """A user name as the caller typed it, checked for what Jira could not take at all. Whether the user exists is Jira's answer."""
    value = (text or "").strip()
    if not value:
        raise ValidationError("The user name is empty.", hint="Give the login name of the user, as with --assignee.")
    if _CONTROL.search(value) or len(value) > _MAX_USER_NAME:
        raise ValidationError(
            "The user name holds control characters or is longer than 255 characters.", hint="Give the login name of the user, as with --assignee."
        )
    return value


def watcher_payload(name: str) -> str:
    """The body that adds a watcher: a JSON string that holds the user name (the one place that says so, for the dry run and the call)."""
    return user_name(name)


def watcher_query(name: str) -> dict[str, str]:
    """The query that removes a watcher; a DELETE has no body."""
    return {"username": user_name(name)}
