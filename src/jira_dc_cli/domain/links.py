"""Issue links: choosing a link type, its direction and reading the relation back."""
from __future__ import annotations

from typing import Sequence

from .errors import ValidationError
from .model import Issue, LinkType


def resolve_link_type(types: Sequence[LinkType], name: str) -> LinkType:
    """By exact name only: phrases such as "requires" occur in several link types."""
    wanted = name.strip().casefold()
    matches = [link_type for link_type in types if link_type.name.casefold() == wanted]
    if len(matches) != 1:
        names = ", ".join(sorted(link_type.name for link_type in types))
        raise ValidationError(
            f"Link type '{name}' not found; use the exact name.",
            hint=f"Available: {names}. See `jira link-types`.",
        )
    return matches[0]


def relation_phrase(link_type: LinkType, inverse: bool) -> str:
    return link_type.inward if inverse else link_type.outward


def link_ends(key: str, other: str, inverse: bool) -> tuple[str, str]:
    """(outward issue, inward issue). `A blocks B`: A is the outward end."""
    return (other, key) if inverse else (key, other)


def relation_text(key: str, phrase: str, other: str) -> str:
    return f"{key} {phrase} {other}"


def find_relation(issue: Issue, other_key: str, type_name: str) -> str | None:
    """The relation exactly as Jira stores it for `issue`, or None when it is not there."""
    for link in issue.links:
        if link.other.key == other_key and link.type_name == type_name:
            return relation_text(issue.key, link.phrase, other_key)
    return None
