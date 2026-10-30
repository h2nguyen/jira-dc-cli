"""The users calls of `FakeGateway`, in memory."""
from __future__ import annotations

import dataclasses

from jira_dc_cli.domain.errors import NotFoundError
from jira_dc_cli.domain.model import User
from jira_dc_cli.domain.users import Person

ASSIGNED_AT = "2026-10-20T09:00:00.000+0200"  # the `updated` time that an assignment gives an issue

DIRECTORY = (
    Person("jdoe", "Jane Doe", "jane.doe@example.com"),  # the token's user
    Person("jroe", "Jane Roe", "jane.roe@example.com"),  # the same start of the display name as jdoe: `Jane` is ambiguous
    Person("asmith", "Alex Smith", "alex.smith@example.com"),
    Person("sam1", "Sam Sample", "sam.one@example.com"),  # two users with one display name: `Sam Sample` is ambiguous
    Person("sam2", "Sam Sample", "sam.two@example.com"),
    Person("olduser", "Old User", "old.user@example.com", active=False),
)


class FakeUsers:
    """Implements `UsersPort` for the tests; its state lives on the gateway and is set up in `_init_users`."""

    def _init_users(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.directory: list[Person] = list(DIRECTORY)
        self.unassignable: set[str] = set()  # user names of people who may not be assigned an issue

    def find_users(self, query: str, *, project_key: str = "", issue_key: str = "", include_inactive: bool = False) -> list[Person]:
        self._enter("find_users", query, project_key, issue_key, include_inactive)
        if issue_key:
            self._issue(issue_key)
        scoped = bool(project_key or issue_key)
        wanted = query.strip().casefold()
        found = [
            person
            for person in self.directory
            if any(wanted in text.casefold() for text in (person.name, person.display_name, person.email))
            and (person.active or (include_inactive and not scoped))
            and not (scoped and person.name in self.unassignable)
        ]
        return found

    def assign_issue(self, key: str, name: str | None) -> None:
        self._enter("assign_issue", key, name)
        issue = self._issue(key)
        if name is None:
            assignee = None
        elif name == "-1":
            assignee = self.user  # the default assignee of the project
        else:
            person = next((item for item in self.directory if item.name == name), None)
            if person is None:
                raise NotFoundError(f"Not found: PUT /issue/{key}/assignee - The user \"{name}\" does not exist: {name}")
            assignee = User(person.name, person.display_name, person.email)
        self.issues[key] = dataclasses.replace(issue, assignee=assignee, updated=ASSIGNED_AT)
