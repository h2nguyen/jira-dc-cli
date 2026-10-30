"""Port operations for users: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.users import Person


class UsersPort(ABC):
    """What the application asks of Jira for users, besides `current_user` of `JiraGateway`."""

    @abstractmethod
    def find_users(self, query: str, *, project_key: str = "", issue_key: str = "", include_inactive: bool = False) -> list[Person]:
        """The users that Jira finds for `query` (it matches user name, display name and e-mail address), at most the first 100.

        With a project or an issue only the users who can be assigned there, which are active ones; `include_inactive` applies to the
        search without a scope."""

    @abstractmethod
    def assign_issue(self, key: str, name: str | None) -> None:
        """Assigns the issue to the user with this user name; `None` leaves it unassigned and `-1` asks for the default assignee."""
