"""Port operations for reading: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..domain.issueparts import ExpandedIssue
from ..domain.savedfilters import SavedFilter


class ReadingPort(ABC):
    """What the application asks of Jira for reading.

    Add an abstract method here only after `adapters/driven/rest_reading.py` (the real adapter) and `tests/support/fakes_reading.py`
    (the test double) implement it: `JiraGateway` cannot be instantiated while one of them lacks it.
    """

    @abstractmethod
    def get_issue_with(self, key: str, expand: Sequence[str] = ()) -> ExpandedIssue:
        """The issue as `get_issue(key)` reads it (every field), with what `expand` adds: `renderedFields` (the HTML of the description and of
        the acceptance criteria) and `changelog` (the history, without the people who made the changes)."""

    @abstractmethod
    def get_issues_by_key(self, keys: Sequence[str], expand: Sequence[str] = ()) -> list[ExpandedIssue]:
        """These issues in one search (`key in (...)`, in pages of the server's size), in Jira's order, with the same fields as `get_issue`.
        Jira answers HTTP 400 for the whole query when one key does not exist (or is not visible): that is a `RejectedError`."""

    @abstractmethod
    def list_filters(self) -> list[SavedFilter]:
        """The saved filters that the token's user marked as favourite. Data Center has no call that lists every filter."""

    @abstractmethod
    def get_filter(self, filter_id: str) -> SavedFilter:
        """One saved filter by its id, with the addresses that run it and open it. `NotFoundError` when there is none (or it is not visible)."""
