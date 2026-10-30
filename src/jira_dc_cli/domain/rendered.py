"""Jira's rendered HTML (`renderedFields`) as plain text, with the people left out unless they were asked for."""
from __future__ import annotations

from .comments import hide_mentions
from .htmltext import html_to_text
from .privacy import redact_text


def rendered_text(html: str, *, people_shown: bool = False) -> str:
    """The text that a reader of Jira sees, as plain text that keeps the structure (bullets, numbers, code fences, headings).
    Without `people_shown` the mentions, e-mail addresses and personal links are replaced as in every other text."""
    if not html or not html.strip():
        return ""
    if people_shown:
        return html_to_text(html)
    return redact_text(html_to_text(hide_mentions(html)))
