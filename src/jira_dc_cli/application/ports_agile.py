"""Port operations for agile: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from ..domain.agile import Board, Epic, SprintInfo
from ..domain.model import Issue
from ..domain.sprints import SprintIssue


class AgilePort(ABC):
    """What the application asks of Jira for agile: boards, sprints, epics, ranking, and the history of issues for a sprint review.

    Only calls that the Jira Data Center 11.3 reference documents (the Agile API of Jira Software and the platform search).
    Every `create`, `update`, `move` and `rank` sends the payload as it is: what a dry run shows is what is sent
    (see `domain.agile`).
    """

    @abstractmethod
    def list_boards(self, *, project: str = "", board_type: str = "", name: str = "") -> list[Board]:
        """Every board that matches, all pages. Without the projects of the boards: see `board_projects`."""

    @abstractmethod
    def get_board(self, board_id: int) -> Board: ...

    @abstractmethod
    def board_projects(self, board_id: int) -> list[str]:
        """The keys of the projects that a board is built on."""

    @abstractmethod
    def list_sprints(self, board_id: int, *, states: Sequence[str] = ()) -> list[SprintInfo]:
        """Every sprint of a scrum board in the given states (all when none is given), all pages."""

    @abstractmethod
    def get_sprint(self, sprint_id: int) -> SprintInfo: ...

    @abstractmethod
    def create_sprint(self, payload: dict[str, Any]) -> SprintInfo:
        """Sends `payload` as it is (see `domain.agile.sprint_create_payload`)."""

    @abstractmethod
    def update_sprint(self, sprint_id: int, payload: dict[str, Any]) -> SprintInfo:
        """Sends `payload` as it is, a partial update (see `domain.agile.sprint_update_payload`)."""

    @abstractmethod
    def list_sprint_issues(self, sprint_id: int, *, keys: Sequence[str] = ()) -> list[Issue]:
        """The issues in a sprint, in the order of the rank; with `keys` only those of them."""

    @abstractmethod
    def move_issues_to_sprint(self, sprint_id: int, payload: dict[str, Any]) -> None:
        """Sends `payload` as it is (at most 50 issues, see `domain.agile.assign_payload`)."""

    @abstractmethod
    def rank_issues(self, payload: dict[str, Any]) -> None:
        """Sends `payload` as it is (see `domain.agile.rank_payload`); raises when Jira could not rank every issue."""

    @abstractmethod
    def rank_order(self, keys: Sequence[str]) -> list[str]:
        """The given issues (at most 51) in the order of their rank."""

    @abstractmethod
    def get_epic(self, epic: str) -> Epic:
        """An epic by key or id."""

    @abstractmethod
    def list_epics(self, board_id: int, *, include_done: bool = False) -> list[Epic]: ...

    @abstractmethod
    def list_epic_issues(self, epic: str, *, keys: Sequence[str] = ()) -> list[Issue]:
        """The issues of an epic, or with `epic="none"` those that have none; with `keys` only those of them."""

    @abstractmethod
    def move_issues_to_epic(self, epic: str, payload: dict[str, Any]) -> None:
        """Sends `payload` as it is (at most 50 issues); `epic="none"` takes the issues out of their epic."""

    @abstractmethod
    def board_backlog(self, board_id: int, *, limit: int) -> tuple[list[Issue], int]:
        """The first `limit` issues of the backlog of a board, in the order of the rank, and how many there are."""

    @abstractmethod
    def search_with_history(self, jql: str) -> list[SprintIssue]:
        """The issues of a search with their change history, every page: the input of the review of a sprint."""

    @abstractmethod
    def status_category_list(self) -> list[tuple[str, str, str]]:
        """`(id, name, category)` of every status of the Jira: the category (new, indeterminate, done) of a status."""
