"""The comments calls of `FakeGateway`, in memory."""
from __future__ import annotations

import dataclasses
import html
from datetime import timedelta
from typing import Any

from jira_dc_cli.domain.errors import AuthError, NotFoundError
from jira_dc_cli.domain.model import Comment
from jira_dc_cli.domain.timeutil import parse_timestamp

EDITED_AT = "2026-10-20T09:00:00.000+0200"  # the `updated` time that an edit gives a comment, unless the comment is newer than that


def later_than(*times: str) -> str:
    """The `updated` time that an edit gives a comment: EDITED_AT, or one second after the latest of the times that it replaces, so that an
    edit is always newer than what it replaced and a guard that compares times (`--if-unchanged-since`) sees the difference."""
    moment = parse_timestamp(EDITED_AT)
    known = [parse_timestamp(text) for text in times if text]
    if known and max(known) >= moment:
        moment = max(known) + timedelta(seconds=1)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000%z")


class FakeComments:
    """Implements `CommentsPort` for the tests; its state lives on the gateway and is set up in `_init_comments`.

    The comments themselves are those of `FakeGateway.issues[key].comments`: a test puts its own there with `dataclasses.replace`."""

    def _init_comments(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.rendered: dict[str, str] = {}  # comment id -> the HTML that Jira renders; the default is the escaped text in a paragraph
        self.edit_all_comments = False  # whether the token's user may edit the comments that others wrote

    def _comment(self, key: str, comment_id: str) -> Comment:
        for comment in self._issue(key).comments:
            if comment.id == comment_id:
                return comment
        raise NotFoundError(f"Not found: GET /issue/{key}/comment/{comment_id} - Can not find a comment with the id {comment_id}")

    def _rendered(self, comment: Comment, wanted: bool) -> Comment:
        if not wanted:
            return comment
        return dataclasses.replace(comment, rendered_body=self.rendered.get(comment.id, f"<p>{html.escape(comment.body)}</p>"))

    def get_comment(self, key: str, comment_id: str, *, rendered: bool = False) -> Comment:
        self._enter("get_comment", key, comment_id, rendered)
        return self._rendered(self._comment(key, comment_id), rendered)

    def fetch_comments(self, key: str, *, rendered: bool = False) -> list[Comment]:
        self._enter("fetch_comments", key, rendered)
        return [self._rendered(comment, rendered) for comment in self._issue(key).comments]

    def update_comment(self, key: str, comment_id: str, payload: dict[str, Any]) -> Comment:
        self._enter("update_comment", key, comment_id, payload)
        issue, comment = self._issue(key), self._comment(key, comment_id)
        if comment.author is not None and comment.author.name != self.user.name and not self.edit_all_comments:
            raise AuthError(
                "Jira refused this request (HTTP 403): You do not have permission to edit this comment.",
                hint="Your account lacks the permission, or the token is limited. Ask a Jira administrator.",
                details={"status": 403, "request": f"PUT /issue/{key}/comment/{comment_id}"},
            )
        restriction = payload.get("visibility") or {}
        # like the worst case on a real server: a restriction that the payload does not send again is gone
        visibility = f"{restriction['type']}:{restriction['value']}" if restriction.get("value") else ""
        saved = dataclasses.replace(
            comment, body=payload["body"], visibility=visibility, updated=later_than(comment.updated, comment.created, issue.updated)
        )
        comments = [saved if item.id == comment_id else item for item in issue.comments]
        self.issues[key] = dataclasses.replace(issue, comments=comments)
        return saved
