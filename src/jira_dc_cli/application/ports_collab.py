"""Port operations for collab: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..domain.collab import RemoteLink, RemoteLinkRef, Votes, Watchers, Worklog, WorklogPage
from ..domain.worktime import WorklogRequest


class CollabPort(ABC):
    """What the application asks of Jira for collab: what people do together on an issue. Nothing here deletes content: removing a
    watcher or a vote is the inverse of adding one, and only the person who is watching or voted (or, for a watcher, someone with the
    permission to manage the list) can do it.

    Add an abstract method here only after `adapters/driven/rest_collab.py` (the real adapter) and `tests/support/fakes_collab.py`
    (the test double) implement it: `JiraGateway` cannot be instantiated while one of them lacks it.
    """

    @abstractmethod
    def get_watchers(self, key: str) -> Watchers: ...

    @abstractmethod
    def add_watcher(self, key: str, payload: str) -> None:
        """Sends `payload` as it is (see `domain.collab.watcher_payload`): what a dry run shows is what is sent."""

    @abstractmethod
    def remove_watcher(self, key: str, query: dict[str, str]) -> None:
        """Sends `query` as it is (see `domain.collab.watcher_query`)."""

    @abstractmethod
    def get_votes(self, key: str) -> Votes: ...

    @abstractmethod
    def add_vote(self, key: str) -> None:
        """The token's user votes; there is nothing to send but the request."""

    @abstractmethod
    def remove_vote(self, key: str) -> None: ...

    @abstractmethod
    def list_worklogs(self, key: str) -> WorklogPage: ...

    @abstractmethod
    def add_worklog(self, key: str, request: WorklogRequest) -> Worklog:
        """Sends the body and the query of `request` as they are (see `domain.worktime.worklog_request`); answers the new entry."""

    @abstractmethod
    def list_remote_links(self, key: str) -> list[RemoteLink]: ...

    @abstractmethod
    def save_remote_link(self, key: str, payload: dict[str, Any]) -> RemoteLinkRef:
        """Sends `payload` as it is (see `domain.remotelinks.remote_link_payload`): creates the link, or updates the one with its `globalId`."""
