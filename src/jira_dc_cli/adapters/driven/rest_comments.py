"""The comments calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse
from typing import Any

from ...domain.model import Comment
from . import jira_mapper as mapper
from .map_comments import comments_page

_PAGE = 100  # comments asked for per request; Jira may answer with fewer and the next request starts where the last one ended


def _comment_path(key: str, comment_id: str = "") -> str:
    path = f"/issue/{urllib.parse.quote(key, safe='')}/comment"
    return f"{path}/{urllib.parse.quote(comment_id, safe='')}" if comment_id else path


class RestComments:
    """Implements `CommentsPort` (application/ports_comments.py) over Jira Data Center's REST API."""

    def get_comment(self, key: str, comment_id: str, *, rendered: bool = False) -> Comment:
        suffix = "?expand=renderedBody" if rendered else ""
        return mapper.comment_from(self._call("GET", _comment_path(key, comment_id) + suffix))

    def fetch_comments(self, key: str, *, rendered: bool = False) -> list[Comment]:
        comments: list[Comment] = []
        while True:
            query = {"startAt": len(comments), "maxResults": _PAGE, **({"expand": "renderedBody"} if rendered else {})}
            page, total = comments_page(self._call("GET", f"{_comment_path(key)}?{urllib.parse.urlencode(query)}"))
            comments += page
            if not page or len(comments) >= total:
                return comments

    def update_comment(self, key: str, comment_id: str, payload: dict[str, Any]) -> Comment:
        data = self._call("PUT", _comment_path(key, comment_id), payload)
        if isinstance(data, dict) and data.get("id"):
            return mapper.comment_from(data)
        return Comment(comment_id, "", str(payload.get("body", "")))
