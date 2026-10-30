"""Jira's JSON of the collab calls (watchers, votes, worklogs, remote links) into domain objects. Pure functions: no HTTP, no state."""
from __future__ import annotations

from typing import Any

from ...domain.collab import RemoteLink, RemoteLinkRef, Votes, Watchers, Worklog, WorklogPage
from . import map_values as values
from .jira_mapper import user_from


def watchers_from(data: Any) -> Watchers:
    """`watchers` is left out of the answer for someone who may not see them: then there is a count and nothing to list."""
    data = data if isinstance(data, dict) else {}
    listed = data.get("watchers")
    users = [user for user in map(user_from, values.objects(listed)) if user] if isinstance(listed, list) else None
    return Watchers(values.count(data.get("watchCount"), len(users or [])), bool(data.get("isWatching")), users)


def votes_from(data: Any) -> Votes:
    """The count and whether the token's user voted. `voters` is in the answer for some; it is never read."""
    data = data if isinstance(data, dict) else {}
    return Votes(values.count(data.get("votes")), bool(data.get("hasVoted")))


def _visibility(data: Any) -> str:
    if not isinstance(data, dict) or not data.get("value"):
        return ""
    return f"{values.text(data.get('type')) or 'role'}:{values.text(data.get('value'))}"


def worklog_from(data: Any) -> Worklog:
    data = data if isinstance(data, dict) else {}
    return Worklog(
        id=values.text(data.get("id")),
        started=values.text(data.get("started")),
        time_spent=values.text(data.get("timeSpent")),
        time_spent_seconds=values.count(data.get("timeSpentSeconds")),
        comment=values.text(data.get("comment")),
        author=user_from(data.get("author")),
        created=values.text(data.get("created")),
        updated=values.text(data.get("updated")),
        visibility=_visibility(data.get("visibility")),
    )


def worklog_page_from(data: Any) -> WorklogPage:
    data = data if isinstance(data, dict) else {}
    found = [worklog_from(item) for item in values.objects(data.get("worklogs"))]
    return WorklogPage(values.count(data.get("total"), len(found)), found)


def remote_link_from(data: dict[str, Any]) -> RemoteLink:
    target = data.get("object") if isinstance(data.get("object"), dict) else {}
    application = data.get("application") if isinstance(data.get("application"), dict) else {}
    return RemoteLink(
        id=values.text(data.get("id")),
        title=values.text(target.get("title")),
        url=values.text(target.get("url")),
        relationship=values.text(data.get("relationship")),
        application=values.text(application.get("name")),
        global_id=values.text(data.get("globalId")),
    )


def remote_links_from(data: Any) -> list[RemoteLink]:
    """A list of links; a single link (Jira's answer to a question for one global id) counts as a list of one."""
    items = [data] if isinstance(data, dict) and "object" in data else values.objects(data)
    return [remote_link_from(item) for item in items]


def remote_link_ref_from(data: Any) -> RemoteLinkRef:
    return RemoteLinkRef(values.text(data.get("id")) if isinstance(data, dict) else "")
