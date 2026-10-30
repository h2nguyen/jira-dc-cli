"""Small readers of Jira's JSON values that the mapping modules of the reference and collab areas share: every key may be missing."""
from __future__ import annotations

from typing import Any


def text(value: Any) -> str:
    return "" if value is None else str(value)


def count(value: Any, default: int = 0) -> int:
    """A whole number of at least zero; the default for anything else (Jira leaves a number out, or sends text)."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else default


def flag(value: Any) -> bool | None:
    """A yes or no that Jira may leave out: None then."""
    return value if isinstance(value, bool) else None


def number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def objects(data: Any) -> list[dict[str, Any]]:
    """The objects of an answer that should be a list; anything else is an empty list."""
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
