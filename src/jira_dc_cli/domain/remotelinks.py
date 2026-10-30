"""The request that adds a web link (what Jira calls a remote link) to an issue."""
from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError

_ADDRESS = re.compile(r"^https?://([^\s/?#]+)(?:[/?#]\S*)?$", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_ADDRESS = 2000
MAX_TEXT = 255


def web_address(url: str) -> str:
    """An `http` or `https` address, without credentials. Any other scheme (`javascript:`, `file:`, `data:`) would become a link that
    everyone who opens the issue can click, so it is refused."""
    value = (url or "").strip()
    match = _ADDRESS.match(value) if len(value) <= MAX_ADDRESS and not _CONTROL.search(value) else None
    if match is None:
        raise ValidationError(
            f"Not a web address: '{value[:60]}'.",
            hint="Only http:// and https:// addresses without spaces are accepted, at most 2,000 characters.",
        )
    if "@" in match.group(1):
        raise ValidationError(
            "The address holds a user name or a password.",
            hint="Take the credentials out of the address: a link is visible to everyone who sees the issue.",
        )
    return value


def _text(value: str, what: str, *, required: bool = False) -> str:
    text = " ".join((value or "").split())
    if required and not text:
        raise ValidationError(f"{what} is empty.", hint=f"Give {what}.")
    if len(text) > MAX_TEXT or _CONTROL.search(text):
        raise ValidationError(f"{what} is longer than {MAX_TEXT} characters or holds control characters.")
    return text


def remote_link_payload(url: str, title: str, *, relationship: str = "", global_id: str = "") -> dict[str, Any]:
    """The body of `POST /issue/{key}/remotelink`; the one place that builds it, for the dry run and the call. With a `globalId` Jira
    updates the link that has it, and creates one when there is none."""
    payload: dict[str, Any] = {"object": {"url": web_address(url), "title": _text(title, "The title", required=True)}}
    relation = _text(relationship, "The relationship")
    identifier = _text(global_id, "The global id")
    if relation:
        payload["relationship"] = relation
    if identifier:
        payload["globalId"] = identifier
    return payload
