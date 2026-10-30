"""Port operations for comments: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..domain.model import Comment


class CommentsPort(ABC):
    """What the application asks of Jira for comments, besides `list_comments` and `add_comment` of `JiraGateway`.

    `rendered_body` of a comment holds the HTML that Jira renders for it (`expand=renderedBody`) when it was asked for, else it is empty;
    the use cases turn it into text.
    """

    @abstractmethod
    def get_comment(self, key: str, comment_id: str, *, rendered: bool = False) -> Comment:
        """One comment with its author, visibility and `updated` time. Raises `NotFoundError` for an unknown issue or comment."""

    @abstractmethod
    def fetch_comments(self, key: str, *, rendered: bool = False) -> list[Comment]:
        """Every comment of the issue, oldest first, over as many requests as it takes."""

    @abstractmethod
    def update_comment(self, key: str, comment_id: str, payload: dict[str, Any]) -> Comment:
        """Sends `payload` as it is (see `domain.comments.edit_payload`) and returns the comment as Jira stored it."""
