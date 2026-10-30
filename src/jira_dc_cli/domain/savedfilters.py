"""Saved filters (the filters of the issue navigator), as `jira filters` and `jira filter` show them."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .errors import ValidationError
from .jqlpeople import redact_jql
from .model import User
from .privacy import redact_text

_FILTER_ID = re.compile(r"[0-9]{1,18}")
_ESCAPES = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


@dataclass(frozen=True)
class SavedFilter:
    id: str
    name: str = ""
    description: str = ""
    jql: str = ""
    favourite: bool = False
    owner: User | None = None
    search_url: str = ""  # the REST address that runs the filter
    view_url: str = ""  # the address that opens it in the browser
    people_hidden: bool = False  # the JQL named people or personal data: they are left out of it, and so is the search address


def parse_filter_id(text: str) -> str:
    """The id of a saved filter: digits only. A name would need quotes in JQL, and quotes do not survive every shell."""
    value = (text or "").strip()
    if not _FILTER_ID.fullmatch(value):
        raise ValidationError(
            f"Not a filter id: {value or '<missing>'}",
            hint="A filter id is a number, as in the address of the filter in the browser (filter=10000). `jira filters` lists your favourite filters.",
        )
    return value


def _unescaped(address: str) -> str:
    """What a person reads in an address: `+` is a blank and every run of `%XX` is UTF-8 text."""
    return _ESCAPES.sub(lambda found: bytes.fromhex(found.group(0).replace("%", "")).decode("utf-8", "replace"), address.replace("+", " "))


def without_people(item: SavedFilter) -> SavedFilter:
    """A filter as it may be shown when people stay out: no owner, the text redacted like every other text, and the people that the JQL
    names (`assignee = jdoe`) left out of it. The search address holds the JQL in an escaped form, so it is dropped as soon as the JQL
    held something personal; `people_hidden` says so."""
    jql = redact_text(redact_jql(item.jql))
    address = _unescaped(item.search_url)
    named = jql != item.jql or redact_text(redact_jql(address)) != address
    return replace(
        item,
        name=redact_text(item.name),
        description=redact_text(item.description),
        jql=jql,
        owner=None,
        search_url="" if named else item.search_url,
        people_hidden=named,
    )
