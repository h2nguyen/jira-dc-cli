"""Timestamps from Jira and the stale-data guard."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .errors import ConflictError, ValidationError


def parse_timestamp(text: str) -> datetime:
    """ISO 8601 with or without fractions; `Z`, `+0200` and `+02:00` offsets; naive means UTC."""
    value = (text or "").strip().replace("Z", "+00:00")
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    value = re.sub(r"\.\d+", lambda match: (match.group(0) + "000000")[:7], value)  # Python 3.10 reads 3 or 6 fraction digits only
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValidationError(
            f"Not an ISO 8601 timestamp: {text}", hint="Example: 2026-10-15T08:07:04+00:00"
        ) from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ensure_unchanged_since(key: str, updated: str, since: str) -> None:
    """Raise a conflict when the issue was changed after `since`."""
    changed_at = parse_timestamp(updated)
    limit = parse_timestamp(since)
    if changed_at > limit:
        raise ConflictError(
            f"{key} changed in Jira at {changed_at.isoformat()}, after {limit.isoformat()}.",
            hint="Fetch it again and review the current state before writing.",
            details={"key": key, "updated": changed_at.isoformat(), "since": limit.isoformat()},
        )
