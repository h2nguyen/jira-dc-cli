"""Jira's JSON for attachments into domain objects. Pure functions: no HTTP, no state."""
from __future__ import annotations

import math
from typing import Any

from ...domain.attachments import AttachmentLimits
from ...domain.model import Attachment
from . import jira_mapper as mapper


def attachments_of_issue(data: Any) -> list[Attachment]:
    """The attachments of the answer to `GET /issue/KEY?fields=attachment`: `fields.attachment`, or none when the issue has none."""
    fields = data.get("fields") if isinstance(data, dict) else None
    items = fields.get("attachment") if isinstance(fields, dict) else None
    return [mapper.attachment_from(item) for item in items or [] if isinstance(item, dict)]


def attachments_of_upload(data: Any) -> list[Attachment]:
    """The attachments that Jira reports for an upload: a list of the new ones."""
    return [mapper.attachment_from(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def limits_from(data: Any) -> AttachmentLimits:
    """`enabled` and `uploadLimit` of `GET /attachment/meta`; what is missing counts as on and as no known limit."""
    if not isinstance(data, dict):
        return AttachmentLimits()
    limit = data.get("uploadLimit")
    finite = not isinstance(limit, float) or math.isfinite(limit)  # JSON as Python reads it has `Infinity`; an int of any size is fine
    known = isinstance(limit, (int, float)) and not isinstance(limit, bool) and finite and limit > 0
    return AttachmentLimits(data.get("enabled") is not False, int(limit) if known else 0)
