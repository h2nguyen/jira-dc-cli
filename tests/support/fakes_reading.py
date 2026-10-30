"""The reading calls of `FakeGateway`, in memory."""
from __future__ import annotations

from html import escape
from typing import Sequence

from jira_dc_cli.domain.errors import NotFoundError, RejectedError
from jira_dc_cli.domain.history import Changelog
from jira_dc_cli.domain.issueparts import ExpandedIssue
from jira_dc_cli.domain.model import Issue
from jira_dc_cli.domain.savedfilters import SavedFilter


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{escape(line)}</p>" for line in text.split("\n") if line.strip())


class FakeReading:
    """Implements `ReadingPort` for the tests; its state lives on the gateway and is set up in `_init_reading`."""

    def _init_reading(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.saved_filters: dict[str, SavedFilter] = {}  # by id; the favourite ones are listed
        self.changelogs: dict[str, Changelog] = {}  # by issue key; an issue without one has an empty history
        self.rendered: dict[str, tuple[str, str]] = {}  # by issue key: the HTML of the description and of the acceptance criteria

    def _expanded(self, issue: Issue, expand: Sequence[str]) -> ExpandedIssue:
        description, acceptance = self.rendered.get(issue.key) or (_paragraphs(issue.description), _paragraphs(issue.acceptance))
        rendered = "renderedFields" in expand
        return ExpandedIssue(
            issue,
            rendered_description=description if rendered else "",
            rendered_acceptance=acceptance if rendered else "",
            changelog=self.changelogs.get(issue.key, Changelog()) if "changelog" in expand else None,
        )

    def get_issue_with(self, key: str, expand: Sequence[str] = ()) -> ExpandedIssue:
        self._enter("get_issue_with", key, tuple(expand))
        return self._expanded(self._issue(key), expand)

    def get_issues_by_key(self, keys: Sequence[str], expand: Sequence[str] = ()) -> list[ExpandedIssue]:
        """Like Jira: one answer for all keys, in Jira's order (not the order asked for), and HTTP 400 for the whole query when
        one key does not exist."""
        self._enter("get_issues_by_key", tuple(keys), tuple(expand))
        for key in keys:
            if key not in self.issues:
                raise RejectedError(
                    f"Jira refused the request (HTTP 400): An issue with key '{key}' does not exist for field 'key'.",
                    hint="Check the JQL.",
                    details={"status": 400, "request": "GET /search"},
                )
        return [self._expanded(self.issues[key], expand) for key in sorted(set(keys), key=lambda item: int(item.split("-")[1]))]

    def list_filters(self) -> list[SavedFilter]:
        self._enter("list_filters")
        return [item for item in self.saved_filters.values() if item.favourite]

    def get_filter(self, filter_id: str) -> SavedFilter:
        self._enter("get_filter", filter_id)
        if filter_id not in self.saved_filters:
            raise NotFoundError(f"Not found: GET /filter/{filter_id} - The filter with id '{filter_id}' does not exist.")
        return self.saved_filters[filter_id]
