"""Jira's user JSON as domain people. Pure functions: no HTTP, no state."""
from __future__ import annotations

from typing import Any

from ...domain.users import Person


def person_from(data: Any) -> Person | None:
    """A user as Jira describes it; None when it names nobody. Jira says `active: false` for a user that was switched off."""
    if not isinstance(data, dict) or not data.get("name"):
        return None
    shown = (str(data.get("displayName") or ""), str(data.get("emailAddress") or ""))
    return Person(str(data["name"]), *shown, data.get("active") is not False)
