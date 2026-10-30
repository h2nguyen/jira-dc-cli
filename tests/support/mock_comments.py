"""The comments routes of the mock Jira: one comment (read, edit) and the list with `expand` and paging.

Adding a comment stays with the routes of `MockJira` itself. A test puts a comment of its own into an issue with `routes(jira).seed(...)`.
"""
from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from .fakes_comments import later_than

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

COMMENT_PATH = re.compile(r"^/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/comment(?:/(\d+))?$")
TOKEN_USER = {"name": "jdoe", "key": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com"}  # `USER` of mock_jira
DISPLAY_NAMES = {"jdoe": "Jane Doe", "asmith": "Alex Smith"}  # what a mention of them is rendered as


def _mention(match: "re.Match[str]") -> str:
    """What Jira renders for `[~name]`: a link to the profile, whose text is the display name."""
    name = match.group(1)
    return f'<a href="/jira/secure/ViewProfile.jspa?name={name}" class="user-hover" rel="{name}">{DISPLAY_NAMES.get(name, name)}</a>'


def _inline(text: str) -> str:
    """`[~name]` is a link to a person, `{{code}}` is monospace; a `{{` or `}}` with a blank next to it is no markup for Jira, and stays."""
    shown = re.sub(r"\[~([\w.-]+)\]", _mention, html.escape(text, quote=False))
    return re.sub(r"\{\{(?!\s)(.+?)(?<!\s)\}\}", r"<tt>\1</tt>", shown)


def render(body: str) -> str:
    """The HTML that Jira shows for a wiki text, as far as the tests need it: paragraphs, bullets, numbers, mentions and monospace."""
    out: list[str] = []
    open_list = ""
    for line in body.splitlines() + [""]:
        item = re.match(r"^([*#])\s+(.*)$", line)
        kind = {"*": "ul", "#": "ol"}[item.group(1)] if item else ""
        if open_list and kind != open_list:
            out.append(f"</{open_list}>")
            open_list = ""
        if kind and not open_list:
            out.append(f"<{kind}>")
            open_list = kind
        if item:
            out.append(f"<li>{_inline(item.group(2))}</li>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    return "\n".join(out)


class CommentsRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.edit_all_comments = False  # whether the token's user may edit the comments that others wrote
        self.page_cap: int | None = None  # a server may answer a page with fewer comments than asked for

    def seed(
        self,
        key: str,
        comment_id: str,
        body: str,
        *,
        author: str = "jdoe",
        created: str = "2026-10-03T10:00:00.000+0200",
        updated: str = "",
        visibility: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """A comment of the issue, written by the user with this name (`jdoe` is the token's user)."""
        person = {**TOKEN_USER, "name": author, "key": author, "displayName": author.title(), "emailAddress": f"{author}@example.com"}
        comment: dict[str, Any] = {"id": comment_id, "author": person, "created": created, "updated": updated or created, "body": body}
        if visibility:
            comment["visibility"] = dict(visibility)
        self.jira.issues[key]["fields"]["comment"]["comments"].append(comment)
        return comment

    def _all(self, key: str) -> list[dict[str, Any]]:
        return self.jira.issues[key]["fields"]["comment"]["comments"] + self.jira.comments.get(key, [])

    def _shown(self, key: str, comment: dict[str, Any], query: dict[str, list[str]]) -> dict[str, Any]:
        address = f"{self.jira.base_url}/rest/api/2/issue/{key}/comment/{comment['id']}"
        shown = {**comment, "updated": comment.get("updated") or comment["created"], "self": address}
        if "renderedBody" in ",".join(query.get("expand", [])):
            shown["renderedBody"] = render(comment["body"])
        return shown

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area.

        `path` is below the context path and keeps its `/rest/...` prefix; `query` holds the decoded parameters.
        """
        match = COMMENT_PATH.match(path)
        if not match or (method, bool(match.group(2))) not in (("GET", False), ("GET", True), ("PUT", True)):
            return False
        key, comment_id = match.groups()
        if key not in self.jira.issues:
            handler.reply(404, {"errorMessages": [f"Issue Does Not Exist {key}"], "errors": {}})
            return True
        if comment_id is None:
            return self._list(handler, key, query)
        comment = next((item for item in self._all(key) if item["id"] == comment_id), None)
        if comment is None:
            handler.reply(404, {"errorMessages": [f"Can not find a comment with the id {comment_id}."], "errors": {}})
        elif method == "GET":
            handler.reply(200, self._shown(key, comment, query))
        else:
            self._edit(handler, key, comment, query, body)
        return True

    def _list(self, handler: "_Handler", key: str, query: dict[str, list[str]]) -> bool:
        comments = self._all(key)
        start = int(query.get("startAt", ["0"])[0])
        size = min(int(query.get("maxResults", ["50"])[0]), self.page_cap or 1000)
        page = [self._shown(key, item, query) for item in comments[start : start + size]]
        handler.reply(200, {"startAt": start, "maxResults": size, "total": len(comments), "comments": page})
        return True

    def _edit(self, handler: "_Handler", key: str, comment: dict[str, Any], query: dict[str, list[str]], body: Any) -> None:
        text = body.get("body") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            handler.reply(400, {"errorMessages": ["Comment body can not be empty!"], "errors": {}})
            return
        if comment["author"]["name"] != TOKEN_USER["name"] and not self.edit_all_comments:
            handler.reply(403, {"errorMessages": ["You do not have permission to edit this comment."], "errors": {}})
            return
        comment["body"] = text
        restriction = body.get("visibility")
        if restriction:  # like the worst case on a real server: a restriction that the request does not send again is gone
            comment["visibility"] = {"type": restriction.get("type"), "value": restriction.get("value")}
        else:
            comment.pop("visibility", None)
        stamp = later_than(comment.get("updated", ""), comment["created"], self.jira.issues[key]["fields"]["updated"])
        comment["updated"] = stamp
        comment["updateAuthor"] = dict(TOKEN_USER)
        self.jira.issues[key]["fields"]["updated"] = stamp
        handler.reply(200, self._shown(key, comment, query))


def routes(jira: "MockJira") -> CommentsRoutes:
    """The comments routes of this mock Jira: a test sets `edit_all_comments` or `page_cap` on them, or seeds a comment."""
    return next(item for item in jira.extensions if isinstance(item, CommentsRoutes))
