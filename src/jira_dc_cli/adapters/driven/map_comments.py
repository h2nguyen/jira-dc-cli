"""Jira's comment JSON as a page of domain comments. Pure functions: no HTTP, no state."""
from __future__ import annotations

from typing import Any

from ...domain.model import Comment
from . import jira_mapper as mapper


def comments_page(data: Any) -> tuple[list[Comment], int]:
    """The comments of one answer of `GET .../comment` and the total that Jira says the issue has (the page itself when it says nothing)."""
    document = data if isinstance(data, dict) else {}
    comments = [mapper.comment_from(item) for item in document.get("comments") or [] if isinstance(item, dict)]
    total = document.get("total")
    return comments, total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else len(comments)
