"""The collab routes of the mock Jira: the watchers, votes, worklogs and web links (remote links) of an issue. They behave as the 11.3
reference says: a 404 for an unknown issue, a 204 for a `DELETE`, a 200 with the number of votes for a vote (the reference says only
"Returns the vote count"), a 201 for a worklog, the refusals that Jira gives (401 or 403 for a missing permission, 404 when voting is not
possible), and the `started` form of a worklog that only `+0000` offsets satisfy. What a test may change:

    reference(jira).denied.add("MANAGE_WATCHERS")      # a permission that the token's user lacks
    reference(jira).configuration["votingEnabled"] = False
    collab(jira).watchers["PROJ-2"] = ["asmith"]       # the state of an issue
    collab(jira).reads_fail_after_a_write = 503        # a write is made, and then Jira cannot answer the read-back
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .mock_reference import reference

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

ISSUE_ROUTE = re.compile(r"/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/(watchers|votes|worklog|remotelink)")
USERS = {
    "jdoe": "Jane Doe",
    "asmith": "Alex Smith",
    "bwong": "Ben Wong",
}
JIRA_TIME = re.compile(r"^(\d+[wdhm])(\s+\d+[wdhm])*$")
JIRA_STARTED = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d{4}$")
SECONDS = {"w": 5 * 8 * 3600, "d": 8 * 3600, "h": 3600, "m": 60}  # the working time of a Jira that has not been configured otherwise


def collab(jira: "MockJira") -> "CollabRoutes":
    """The routes object of a mock Jira, to read and change what it keeps."""
    return next(item for item in jira.extensions if isinstance(item, CollabRoutes))


def _error(*messages: str, **errors: str) -> dict[str, Any]:
    return {"errorMessages": list(messages), "errors": errors}


def _seconds(text: str) -> int:
    return sum(int(amount) * SECONDS[unit] for amount, unit in re.findall(r"(\d+)([wdhm])", text))


class CollabRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.users = dict(USERS)  # the users that exist: login name to display name
        self.watchers: dict[str, list[str]] = {}  # user names per issue
        self.voters: dict[str, list[str]] = {}
        self.worklogs: dict[str, list[dict[str, Any]]] = {}
        self.remote_links: dict[str, list[dict[str, Any]]] = {}
        self.reads_fail_after_a_write = 0  # an HTTP status such as 503: reading a part of an issue that a call has changed answers it
        self._changed: set[tuple[str, str]] = set()
        self._seeded: set[tuple[str, str]] = set()
        self._next_id = 10200  # the entries that a test adds are numbered from here; what is seeded has the numbers below

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area.

        `path` is below the context path and keeps its `/rest/...` prefix; `query` holds the decoded parameters.
        """
        match = ISSUE_ROUTE.fullmatch(path)
        if match is None:
            return False
        key, part = match.groups()
        if key not in self.jira.issues:
            handler.reply(404, _error("Issue Does Not Exist"))
            return True
        self._seed(key, part)
        if method == "GET" and self.reads_fail_after_a_write and (key, part) in self._changed:
            handler.reply(self.reads_fail_after_a_write, _error("The server cannot answer now."))
            return True
        route = {"watchers": self.watcher_route, "votes": self.vote_route, "worklog": self.worklog_route, "remotelink": self.link_route}[part]
        return route(handler, method, key, query, body)

    def _seed(self, key: str, part: str) -> None:
        """What an issue holds before a test changes it, made when the issue is first asked about."""
        if (key, part) in self._seeded:
            return
        self._seeded.add((key, part))
        if key != "PROJ-1":
            return
        if part == "watchers":
            self.watchers.setdefault(key, ["asmith", "bwong"])
        elif part == "votes":
            self.voters.setdefault(key, ["asmith"])
        elif part == "worklog":
            self.worklogs.setdefault(key, [
                self._worklog("1h 30m", "2026-10-14T09:00:00.000+0000", "Reviewed with [~asmith] (alex.smith@example.com)", "asmith", 10100),
                self._worklog("2h", "2026-10-18T13:00:00.000+0000", "", "jdoe", 10101),
            ])
        elif part == "remotelink":
            seeded = {
                "object": {"url": "https://wiki.example.com/runbook", "title": "Runbook"},
                "relationship": "documentation",
                "globalId": "wiki:runbook",
            }
            self.remote_links.setdefault(key, [self._link(10000, seeded)])

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _url(self, *parts: str) -> str:
        return "/".join([f"{self.jira.base_url}/rest/api/2", *parts])

    def _user(self, name: str) -> dict[str, Any]:
        return {"self": self._url("user") + f"?username={name}", "name": name, "key": name, "displayName": self.users[name], "active": True}

    def _may(self, permission: str) -> bool:
        return reference(self.jira).has(permission)

    def _feature(self, name: str) -> bool:
        return bool(reference(self.jira).configuration.get(name))

    # -- watchers --------------------------------------------------------------------------------------

    def watcher_route(self, handler: "_Handler", method: str, key: str, query: dict[str, list[str]], body: Any) -> bool:
        if not self._feature("watchingEnabled"):
            handler.reply(404, _error("Watching is disabled."))
            return True
        names = self.watchers.setdefault(key, [])
        if method == "GET":
            answer: dict[str, Any] = {"self": self._url("issue", key, "watchers"), "isWatching": "jdoe" in names, "watchCount": len(names)}
            if self._may("VIEW_VOTERS_AND_WATCHERS"):
                answer["watchers"] = [self._user(name) for name in names]
            handler.reply(200, answer)
            return True
        if method == "POST":
            if body is not None and not isinstance(body, str):
                handler.reply(400, _error("The body must be the name of the user, as a JSON string."))
                return True
            target = body or query.get("userName", [""])[0] or "jdoe"
        elif method == "DELETE":
            target = (query.get("username") or query.get("userName") or [""])[0]
            if not target:
                handler.reply(400, _error("A user name query parameter is not supplied."))
                return True
        else:
            return False
        if target != "jdoe" and not self._may("MANAGE_WATCHERS"):
            handler.reply(401, _error("You do not have the permission to manage the watcher list of this issue."))
            return True
        if target not in self.users:
            handler.reply(404, _error(f"The user named '{target}' does not exist."))
            return True
        if method == "POST":
            if target not in names:
                names.append(target)
        elif target in names:
            names.remove(target)
        self._changed.add((key, "watchers"))
        handler.reply(204)
        return True

    # -- votes -----------------------------------------------------------------------------------------

    def vote_route(self, handler: "_Handler", method: str, key: str, query: dict[str, list[str]], body: Any) -> bool:
        if not self._feature("votingEnabled"):
            handler.reply(404, _error("Voting is disabled."))
            return True
        names = self.voters.setdefault(key, [])
        if method == "GET":
            answer: dict[str, Any] = {"self": self._url("issue", key, "votes"), "votes": len(names), "hasVoted": "jdoe" in names}
            if self._may("VIEW_VOTERS_AND_WATCHERS"):
                answer["voters"] = [self._user(name) for name in names]
            handler.reply(200, answer)
            return True
        if method not in ("POST", "DELETE"):
            return False
        reporter = ((self.jira.issues[key]["fields"].get("reporter") or {}).get("name"))
        if reporter == "jdoe":
            handler.reply(404, _error("You cannot vote for an issue you have reported."))
        elif (method == "POST") == ("jdoe" in names):
            handler.reply(404, _error("You have already voted for this issue." if method == "POST" else "You have not voted for this issue."))
        else:
            if method == "POST":
                names.append("jdoe")
                handler.reply(200, len(names))  # the reference: 200, "Returns the vote count for particular ticket"; a DELETE answers 204
            else:
                names.remove("jdoe")
                handler.reply(204)
            self._changed.add((key, "votes"))
        return True

    # -- worklogs --------------------------------------------------------------------------------------

    def _worklog(self, spent: str, started: str, comment: str, author: str, identifier: int | None = None) -> dict[str, Any]:
        number = str(identifier or self._new_id())
        return {
            "self": self._url("issue", "x", "worklog", number),
            "author": self._user(author),
            "updateAuthor": self._user(author),
            "comment": comment,
            "created": started,
            "updated": started,
            "started": started,
            "timeSpent": spent,
            "timeSpentSeconds": _seconds(spent),
            "id": number,
            "issueId": "10000",
        }

    def worklog_route(self, handler: "_Handler", method: str, key: str, query: dict[str, list[str]], body: Any) -> bool:
        entries = self.worklogs.setdefault(key, [])
        if method == "GET":
            handler.reply(200, {"startAt": 0, "maxResults": 1048576, "total": len(entries), "worklogs": entries})
            return True
        if method != "POST":
            return False
        if not self._may("WORK_ON_ISSUES"):
            handler.reply(403, _error("You do not have permission to log work on this issue."))
            return True
        problem = self._worklog_problem(body, query)
        if problem is not None:
            handler.reply(400, problem)
            return True
        body = dict(body)
        entry = self._worklog(body["timeSpent"], body.get("started", f"{self.jira.now}.000+0000"), body.get("comment", ""), "jdoe")
        entries.append(entry)
        self._changed.add((key, "worklog"))
        handler.reply(201, entry)
        return True

    def _worklog_problem(self, body: Any, query: dict[str, list[str]]) -> dict[str, Any] | None:
        """Why Jira refuses to log this work (a 400), or None when it takes it."""
        if not self._feature("timeTrackingEnabled"):
            return _error("Time tracking is disabled.")
        if not isinstance(body, dict) or not JIRA_TIME.match(str(body.get("timeSpent", "")).strip()) or _seconds(str(body.get("timeSpent", ""))) == 0:
            return _error(timeLogged="Invalid time duration entered.")
        if "started" in body and not JIRA_STARTED.match(str(body["started"])):
            return _error(f"Can not construct instance of java.util.Date from String value '{body['started']}': not a valid representation")
        mode = query.get("adjustEstimate", ["auto"])[0]
        if mode not in ("new", "leave", "manual", "auto"):
            return _error(adjustEstimate=f"The adjustEstimate value '{mode}' is not valid.")
        if mode == "new" and not JIRA_TIME.match(query.get("newEstimate", [""])[0]):
            return _error(newEstimate="Invalid time duration entered.")
        if mode == "manual" and not JIRA_TIME.match(query.get("reduceBy", [""])[0]):
            return _error(reduceBy="Invalid time duration entered.")
        return None

    # -- web links -------------------------------------------------------------------------------------

    def _link(self, identifier: int, body: dict[str, Any]) -> dict[str, Any]:
        link = {"id": identifier, "self": self._url("issue", "x", "remotelink", str(identifier)), "object": dict(body["object"])}
        for name in ("globalId", "relationship", "application"):
            if body.get(name):
                link[name] = body[name]
        return link

    def link_route(self, handler: "_Handler", method: str, key: str, query: dict[str, list[str]], body: Any) -> bool:
        if not self._feature("issueLinkingEnabled") or (method == "POST" and not self._may("LINK_ISSUES")):
            handler.reply(403, _error("You do not have permission to use web links on this issue, or issue linking is disabled."))
            return True
        links = self.remote_links.setdefault(key, [])
        if method == "GET":
            wanted = query.get("globalId", [""])[0]
            found = [link for link in links if not wanted or link.get("globalId") == wanted]
            if wanted and not found:
                handler.reply(404, _error("The remote issue link does not exist."))
            else:
                handler.reply(200, found[0] if wanted else found)
            return True
        if method != "POST":
            return False
        target = body.get("object") if isinstance(body, dict) else None
        if not isinstance(target, dict) or not target.get("url") or not target.get("title"):
            handler.reply(400, {"errorMessages": [], "errors": {"object.url": "The url is required.", "object.title": "The title is required."}})
            return True
        for index, link in enumerate(links):
            if body.get("globalId") and link.get("globalId") == body["globalId"]:
                links[index] = self._link(link["id"], body)
                self._changed.add((key, "remotelink"))
                handler.reply(200, {"id": link["id"], "self": link["self"]})
                return True
        link = self._link(self._new_id(), body)
        links.append(link)
        self._changed.add((key, "remotelink"))
        handler.reply(200, {"id": link["id"], "self": link["self"]})
        return True
