"""The collab calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse
from typing import Any

from ...domain.collab import RemoteLink, RemoteLinkRef, Votes, Watchers, Worklog, WorklogPage
from ...domain.worktime import WorklogRequest
from . import map_collab as mapping


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _query(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


class RestCollab:
    """Implements `CollabPort` (application/ports_collab.py) over Jira Data Center's REST API."""

    def get_watchers(self, key: str) -> Watchers:
        return mapping.watchers_from(self._call("GET", f"/issue/{_quote(key)}/watchers"))

    def add_watcher(self, key: str, payload: str) -> None:
        self._call("POST", f"/issue/{_quote(key)}/watchers", payload)

    def remove_watcher(self, key: str, query: dict[str, str]) -> None:
        self._call("DELETE", f"/issue/{_quote(key)}/watchers?{_query(query)}")

    def get_votes(self, key: str) -> Votes:
        return mapping.votes_from(self._call("GET", f"/issue/{_quote(key)}/votes"))

    def add_vote(self, key: str) -> None:
        self._call("POST", f"/issue/{_quote(key)}/votes")

    def remove_vote(self, key: str) -> None:
        self._call("DELETE", f"/issue/{_quote(key)}/votes")

    def list_worklogs(self, key: str) -> WorklogPage:
        return mapping.worklog_page_from(self._call("GET", f"/issue/{_quote(key)}/worklog"))

    def add_worklog(self, key: str, request: WorklogRequest) -> Worklog:
        path = f"/issue/{_quote(key)}/worklog" + (f"?{_query(request.query)}" if request.query else "")
        return mapping.worklog_from(self._call("POST", path, request.body))

    def list_remote_links(self, key: str) -> list[RemoteLink]:
        return mapping.remote_links_from(self._call("GET", f"/issue/{_quote(key)}/remotelink"))

    def save_remote_link(self, key: str, payload: dict[str, Any]) -> RemoteLinkRef:
        return mapping.remote_link_ref_from(self._call("POST", f"/issue/{_quote(key)}/remotelink", payload))
