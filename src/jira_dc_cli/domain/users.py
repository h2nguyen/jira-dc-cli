"""People as the rules see them: which user a name, a display name or an address stands for, and the words that `--assignee` knows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

ME, UNASSIGNED, AUTOMATIC = "me", "unassigned", "automatic"
AUTOMATIC_NAME = "-1"  # what Jira reads as "the default assignee of the project"


@dataclass(frozen=True)
class Person:
    name: str
    display_name: str = ""
    email: str = ""
    active: bool = True


@dataclass(frozen=True)
class Resolution:
    """What a query stands for: how many users match it, and the user when exactly one does."""

    matches: int
    person: Person | None = None


def keyword(text: str) -> str:
    """`me`, `unassigned` or `automatic` when the text is one of them (in any case; `-1` is `automatic`), else an empty text."""
    word = (text or "").strip().casefold()
    if word == AUTOMATIC_NAME:
        return AUTOMATIC
    return word if word in (ME, UNASSIGNED, AUTOMATIC) else ""


def resolve_person(query: str, people: Sequence[Person]) -> Resolution:
    """The user that `query` stands for: an exact user name, else an exact display name, else an exact e-mail address, else a partial
    match. The first step that finds anybody decides, and the answer is a user only when that step finds exactly one."""
    wanted = query.strip().casefold()
    if not wanted:
        return Resolution(0)  # an empty text is part of every name
    distinct = list({person.name.casefold(): person for person in people}.values())
    steps = (
        lambda person: person.name.casefold() == wanted,
        lambda person: person.display_name.casefold() == wanted,
        lambda person: person.email.casefold() == wanted,
        lambda person: any(wanted in text.casefold() for text in (person.name, person.display_name, person.email)),
    )
    for step in steps:
        found = [person for person in distinct if step(person)]
        if found:
            return Resolution(len(found), found[0] if len(found) == 1 else None)
    return Resolution(0)
