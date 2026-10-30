"""The issue links of a Markdown ticket: the `links:` line, and what a push has to do about them.

A link is kept the way Jira stores it: the NAME of the link type, which end of the link this issue is (outward or inward) and the key of the
other issue. The front matter line lists them, separated by commas, so that it stays one `key: value` line like `labels:` (the importer reads it):

    links: Blocks -> PROJ-2, Blocks <- PROJ-3, Relates -> PROJ-7

`Type -> KEY`: this issue is the outward end, as in "this issue blocks PROJ-2". `Type <- KEY`: it is the inward end, "PROJ-3 blocks this issue"
(Jira says "is blocked by"). The arrow follows the relation: the issue at the tail does what the type says. A phrase would not tell the type: "requires"
is the outward phrase of one link type and the inward phrase of another. A type name that holds a comma, `<` or `>` cannot stand in the line.

A link is the same link when the type name (without regard to case, as `jira link` reads it), the end and the key are the same: Jira stores a link
with a direction, so `Relates -> PROJ-2` and `Relates <- PROJ-2` are two links for it even though both read "relates to". Nothing here deletes a
link: the CLI creates links and never removes one, so a link that only Jira holds is reported, never removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from .drafts import split_list
from .errors import ValidationError
from .links import link_ends, relation_phrase, relation_text, resolve_link_type
from .model import Issue, LinkType
from .payloads import link_payload

OUTWARD, INWARD = "outward", "inward"
ARROWS = {OUTWARD: "->", INWARD: "<-"}
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")
_SHOWN = 60  # characters of an entry that an error repeats


@dataclass(frozen=True)
class LinkEntry:
    """One link of the line, from this issue's side."""

    type_name: str
    direction: str  # OUTWARD: this issue is the outward end; INWARD: it is the inward end
    key: str  # the other issue

    @property
    def same_link(self) -> tuple[str, str, str]:
        """What makes two entries one link: the type name without regard to case, the end and the key."""
        return (self.type_name.casefold(), self.direction, self.key)

    def text(self) -> str:
        return f"{self.type_name} {ARROWS[self.direction]} {self.key}"


@dataclass(frozen=True)
class LinkPlan:
    """What the line and Jira disagree about, as far as a push can act on it."""

    create: list[LinkEntry]  # the line lists them and Jira does not hold them: a push creates them
    jira_only: list[LinkEntry]  # Jira holds them and the line does not list them: a push cannot remove them


@dataclass(frozen=True)
class NewLink:
    """A link that a push creates: the entry, the relation as Jira shows it and the request body (what a dry run shows is what is sent)."""

    entry: LinkEntry
    relation: str
    type_name: str
    payload: dict[str, Any]


def clean_name(name: Any) -> str:
    """A type name as a line can hold it: every run of blanks is one blank, the way the line is written."""
    return " ".join(str(name or "").split())


def writable(name: str) -> bool:
    """Whether a type name can stand in the line: no comma, which separates the entries, no `<` or `>`, which make the arrows, and no hyphen at
    its end, which would join the arrow to a long one (`-->`)."""
    return bool(name) and not any(mark in name for mark in ",<>") and not name.endswith("-")


def unwritable_types(issue: Issue) -> list[str]:
    """The link types of the issue that cannot be written in the line. The links of such an issue are left out of the comparison."""
    names = {clean_name(link.type_name) for link in issue.links}
    return sorted(name for name in names if name and not writable(name))


def _order(entry: LinkEntry) -> tuple[str, int, str, int, str, str]:
    project, _, number = entry.key.rpartition("-")
    digits = number.lstrip("0")  # by length and then by text: a key of 5,000 digits must not meet the limit of the conversion to an integer
    return (entry.type_name.casefold(), 0 if entry.direction == OUTWARD else 1, project.upper(), len(digits), digits, entry.key)


def _unreadable(item: str, what: str = "") -> ValidationError:
    shown = item if len(item) <= _SHOWN else item[: _SHOWN - 3] + "..."
    return ValidationError(
        f"The links line of the file cannot be read: '{shown}' {what or 'is not `Type -> KEY` or `Type <- KEY`'}.",
        hint="Write each link as `Type -> KEY` (this issue is the outward end: it 'blocks') or `Type <- KEY` (it is the inward end: it 'is blocked by'), "
        "separated by commas, for example `links: Blocks -> PROJ-2, Blocks <- PROJ-3`. `jira link-types` lists the type names.",
    )


def _entry(item: str) -> LinkEntry:
    """`Type -> KEY` or `Type <- KEY`. The last arrow separates, found with `rfind` so that no pattern can backtrack over a hostile line."""
    cut = max(item.rfind("->"), item.rfind("<-"))
    if cut < 0:
        raise _unreadable(item)
    name, key = clean_name(item[:cut]), item[cut + 2 :].strip()
    if not writable(name) or not _KEY.fullmatch(key):
        raise _unreadable(item)
    return LinkEntry(name, INWARD if item.startswith("<-", cut) else OUTWARD, key.upper())


def parse_line(value: str, own_key: str = "") -> list[LinkEntry]:
    """The links a `links:` line lists, each once, in a fixed order. An entry that cannot be read is an error: reading it as `no link` would let
    a pull replace the line with Jira's links, and a push would drop what the person meant."""
    found: dict[tuple[str, str, str], LinkEntry] = {}
    for item in split_list(value):
        entry = _entry(item)
        if own_key and entry.key == own_key.upper():
            raise _unreadable(item, f"links {entry.key} to itself")
        found.setdefault(entry.same_link, entry)
    return sorted(found.values(), key=_order)


def render_line(entries: Sequence[LinkEntry]) -> str:
    return ", ".join(entry.text() for entry in entries)


def entries_of(issue: Issue) -> list[LinkEntry]:
    """The links Jira holds for the issue, as the line would list them. A link whose other end has no usable key is left out."""
    found: dict[tuple[str, str, str], LinkEntry] = {}
    for link in issue.links:
        key, name = str(link.other.key or "").strip(), clean_name(link.type_name)
        if link.direction in ARROWS and name and _KEY.fullmatch(key):
            entry = LinkEntry(name, link.direction, key.upper())
            found.setdefault(entry.same_link, entry)
    return sorted(found.values(), key=_order)


def forms(entries: Sequence[LinkEntry]) -> list[str]:
    """The entries as they are compared and recorded: one text per link, the same for the same link whatever its spelling."""
    return sorted(f"{entry.type_name.casefold()} {ARROWS[entry.direction]} {entry.key}" for entry in entries)


def plan_links(wanted: Sequence[LinkEntry], held: Sequence[LinkEntry]) -> LinkPlan:
    """Compare first: only what Jira does not hold is created, so that a link is never created a second time."""
    have, want = {entry.same_link for entry in held}, {entry.same_link for entry in wanted}
    return LinkPlan([entry for entry in wanted if entry.same_link not in have], [entry for entry in held if entry.same_link not in want])


def resolve_type(types: Sequence[LinkType], name: str) -> LinkType:
    """The link type of that name: by name only (a phrase is not unique), without regard to case and to the blanks inside the name. Else the
    error that `jira link` gives, with the names that exist."""
    wanted = clean_name(name).casefold()
    matches = [link_type for link_type in types if clean_name(link_type.name).casefold() == wanted]
    return matches[0] if len(matches) == 1 else resolve_link_type(types, name)


def new_link(key: str, entry: LinkEntry, link_type: LinkType) -> NewLink:
    """The link of `key` to `entry.key` in the direction the entry says: the outward end of the payload is the issue that does what the type says."""
    inverse = entry.direction == INWARD
    outward, inward = link_ends(key, entry.key, inverse)
    relation = relation_text(key, relation_phrase(link_type, inverse), entry.key)
    return NewLink(entry, relation, link_type.name, link_payload(link_type.name, outward, inward))
