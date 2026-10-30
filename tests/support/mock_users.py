"""The users routes of the mock Jira: the user search, the search of assignable users, and the assignment of an issue.

The users are those of `fakes_users.DIRECTORY` (`jdoe` is the token's user; two have a display name that starts alike, two have the same
display name, one is inactive). A test changes `routes(jira).users` or `.unassignable`.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .fakes_users import DIRECTORY

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

ASSIGNED_AT = "2026-10-20T09:00:00.000+0200"  # the `updated` time that an assignment gives an issue
ISSUE_PATH = re.compile(r"^/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/assignee$")


def _user(name: str, display_name: str, email: str, active: bool) -> dict[str, Any]:
    return {"key": name, "name": name, "emailAddress": email, "displayName": display_name, "active": active, "timeZone": "Europe/Berlin"}


class UsersRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.users = [_user(person.name, person.display_name, person.email, person.active) for person in DIRECTORY]
        self.unassignable: set[str] = set()  # user names of people who may not be assigned an issue
        self.default_assignee = "jdoe"  # who gets an issue that is assigned `-1`

    def add_user(self, name: str, display_name: str, email: str, *, active: bool = True) -> None:
        """An administrator creates a user while the program runs."""
        self.users.append(_user(name, display_name, email, active))

    def _matching(self, query: dict[str, list[str]], *, assignable: bool, include_inactive: bool) -> list[dict[str, Any]]:
        wanted = query.get("username", [""])[0].casefold()
        found = [
            user
            for user in self.users
            if any(wanted in str(user[field]).casefold() for field in ("name", "displayName", "emailAddress"))
            and (user["active"] or include_inactive)
            and not (assignable and user["name"] in self.unassignable)
        ]
        start = int(query.get("startAt", ["0"])[0])
        return found[start : start + min(int(query.get("maxResults", ["50"])[0]), 100)]

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area.

        `path` is below the context path and keeps its `/rest/...` prefix; `query` holds the decoded parameters.
        """
        if method == "GET" and path == "/rest/api/2/user/search":
            if "username" not in query:
                handler.reply(400, {"errorMessages": ["The username query parameter was not provided"], "errors": {}})
                return True
            inactive = query.get("includeInactive", ["false"])[0] == "true"
            handler.reply(200, self._matching(query, assignable=False, include_inactive=inactive))
            return True
        if method == "GET" and path == "/rest/api/2/user/assignable/search":
            return self._assignable(handler, query)
        match = ISSUE_PATH.match(path)
        if method == "PUT" and match:
            return self._assign(handler, match.group(1), body)
        return False

    def _assignable(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        key = query.get("issueKey", [""])[0]
        project = query.get("project", [""])[0]
        if key and key not in self.jira.issues:
            handler.reply(404, {"errorMessages": [f"The issue key '{key}' does not exist"], "errors": {}})
        elif not key and project not in self.jira.versions:
            handler.reply(404, {"errorMessages": [f"No project could be found with key '{project}'."], "errors": {}})
        else:
            handler.reply(200, self._matching(query, assignable=True, include_inactive=False))
        return True

    def _assign(self, handler: "_Handler", key: str, body: Any) -> bool:
        if key not in self.jira.issues:
            handler.reply(404, {"errorMessages": [f"Issue Does Not Exist {key}"], "errors": {}})
            return True
        if not isinstance(body, dict) or "name" not in body:
            handler.reply(400, {"errorMessages": ["A user name is needed."], "errors": {}})
            return True
        name = body["name"]
        if name == "-1":
            name = self.default_assignee
        user = next((item for item in self.users if item["name"] == name), None) if name is not None else None
        if name is not None and user is None:
            handler.reply(404, {"errorMessages": [f"The user \"{name}\" does not exist: {name}"], "errors": {}})
            return True
        fields = self.jira.issues[key]["fields"]
        fields["assignee"] = dict(user) if user else None
        fields["updated"] = ASSIGNED_AT
        handler.reply(204)
        return True


def routes(jira: "MockJira") -> UsersRoutes:
    """The users routes of this mock Jira."""
    return next(item for item in jira.extensions if isinstance(item, UsersRoutes))
