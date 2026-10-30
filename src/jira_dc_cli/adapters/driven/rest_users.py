"""The users calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse

from ...domain.users import Person
from .map_users import person_from

_MOST = 100  # what Jira answers at most: its search looks at the first 100 users


class RestUsers:
    """Implements `UsersPort` (application/ports_users.py) over Jira Data Center's REST API."""

    def find_users(self, query: str, *, project_key: str = "", issue_key: str = "", include_inactive: bool = False) -> list[Person]:
        parameters: dict[str, str | int] = {"username": query, "maxResults": _MOST}
        if project_key or issue_key:
            path = "/user/assignable/search"
            parameters.update({"issueKey": issue_key} if issue_key else {"project": project_key})
        else:
            path = "/user/search"
            if include_inactive:
                parameters["includeInactive"] = "true"
        data = self._call("GET", f"{path}?{urllib.parse.urlencode(parameters, quote_via=urllib.parse.quote)}")
        return [person for person in map(person_from, data if isinstance(data, list) else []) if person]

    def assign_issue(self, key: str, name: str | None) -> None:
        self._call("PUT", f"/issue/{urllib.parse.quote(key, safe='')}/assignee", {"name": name})
