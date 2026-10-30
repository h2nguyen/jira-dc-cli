"""The agile calls of `FakeGateway`, in memory."""
from __future__ import annotations

import dataclasses
from typing import Any, Sequence

from jira_dc_cli.domain.agile import Board, Epic, SprintInfo
from jira_dc_cli.domain.errors import NotFoundError, RejectedError
from jira_dc_cli.domain.model import Issue, Sprint
from jira_dc_cli.domain.sprints import SprintIssue, key_order

FAKE_NOW = "2026-10-20T12:00:00.000+00:00"  # what the fake writes as the time of a start or a completion
STATUS_CATEGORIES = [("1", "To Do", "new"), ("3", "In Progress", "indeterminate"), ("10001", "Done", "done")]


def _number(key: str) -> int:
    return key_order(key)[1]  # 0 for a key whose number is longer than 18 digits


class FakeAgile:
    """Implements `AgilePort` for the tests; its state lives on the gateway and is set up in `_init_agile`.

    Tests fill `sprints`, `sprint_members` (sprint id to the keys in it, in rank order) and, for a review, `sprint_history` (key to the
    `SprintIssue` with its changes) and `sprint_removed` (sprint id to the issues that left it)."""

    def _init_agile(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.boards: dict[int, Board] = {1200: Board(1200, "Demo board", "scrum", ("PROJ",))}
        self.sprints: dict[int, SprintInfo] = {}
        self.sprint_members: dict[int, list[str]] = {}
        self.epics: dict[str, Epic] = {
            "PROJ-8": Epic("PROJ-8", "Epic eight", "Epic eight", False, 8),
            "PROJ-9": Epic("PROJ-9", "Epic nine", "Epic nine", False, 9),
        }
        self.sprint_history: dict[str, SprintIssue] = {}
        self.sprint_removed: dict[int, list[SprintIssue]] = {}
        self.reject_history_search = False  # `sprint was` is refused, as an older Jira does
        self.status_categories_in_jira: list[tuple[str, str, str]] = list(STATUS_CATEGORIES)
        self.rank_list: list[str] = []
        self.backlog_keys: list[str] | None = None
        self.next_sprint_id = 100

    # -- helpers -----------------------------------------------------------------------------

    def add_sprint(self, sprint: SprintInfo, members: Sequence[str] = ()) -> SprintInfo:
        self.sprints[sprint.id] = sprint
        self.sprint_members[sprint.id] = list(members)
        return sprint

    def sync_sprint_fields(self) -> None:
        """Makes the sprint field of every issue agree with the membership of the sprints."""
        for key in self.issues:
            self.issues[key] = dataclasses.replace(self.issues[key], sprints=self._agile_sprint_field(key))

    def _agile_board(self, board_id: int) -> Board:
        if board_id not in self.boards:
            raise NotFoundError(f"Not found: GET /board/{board_id} - The board does not exist or you do not have permission to view it.")
        return self.boards[board_id]

    def _agile_sprint(self, sprint_id: int) -> SprintInfo:
        if sprint_id not in self.sprints:
            raise NotFoundError(f"Not found: GET /sprint/{sprint_id} - Sprint does not exist or you do not have permission to view it.")
        return self.sprints[sprint_id]

    def _agile_ranked(self) -> list[str]:
        known = [key for key in self.rank_list if key in self.issues]
        self.rank_list = known + sorted((key for key in self.issues if key not in known), key=_number)
        return self.rank_list

    def _agile_by_rank(self, keys: Sequence[str]) -> list[str]:
        order = self._agile_ranked()
        return sorted(keys, key=lambda key: order.index(key) if key in order else len(order))

    def _agile_sprint_field(self, key: str) -> list[Sprint]:
        found = [self.sprints[number] for number, members in self.sprint_members.items() if key in members and number in self.sprints]
        return [Sprint(sprint.name, sprint.state.upper(), sprint.id) for sprint in sorted(found, key=lambda sprint: sprint.id)]

    # -- boards ------------------------------------------------------------------------------

    def list_boards(self, *, project: str = "", board_type: str = "", name: str = "") -> list[Board]:
        self._enter("list_boards", project, board_type, name)
        return [
            board
            for board in self.boards.values()
            if (not project or project in board.projects)
            and (not board_type or board.type == board_type)
            and (not name or name.casefold() in board.name.casefold())
        ]

    def get_board(self, board_id: int) -> Board:
        self._enter("get_board", board_id)
        return self._agile_board(board_id)

    def board_projects(self, board_id: int) -> list[str]:
        self._enter("board_projects", board_id)
        return list(self._agile_board(board_id).projects)

    # -- sprints -----------------------------------------------------------------------------

    def list_sprints(self, board_id: int, *, states: Sequence[str] = ()) -> list[SprintInfo]:
        self._enter("list_sprints", board_id, tuple(states))
        board = self._agile_board(board_id)
        if board.type != "scrum":
            raise RejectedError("Jira refused the request (HTTP 400): The board does not support sprints")
        rank = {"closed": 0, "active": 1, "future": 2}
        found = [sprint for sprint in self.sprints.values() if sprint.board_id == board_id and (not states or sprint.state in states)]
        return sorted(found, key=lambda sprint: (rank.get(sprint.state, 3), sprint.id))

    def get_sprint(self, sprint_id: int) -> SprintInfo:
        self._enter("get_sprint", sprint_id)
        return self._agile_sprint(sprint_id)

    def create_sprint(self, payload: dict[str, Any]) -> SprintInfo:
        self._enter("create_sprint", payload)
        board = self._agile_board(payload["originBoardId"])
        if board.type != "scrum":
            raise RejectedError("Jira refused the request (HTTP 400): The board does not support sprints")
        self.next_sprint_id += 1
        sprint = SprintInfo(
            self.next_sprint_id, payload["name"], "future", payload.get("startDate", ""), payload.get("endDate", ""), "", payload.get("goal", ""), board.id
        )
        return self.add_sprint(sprint)

    def update_sprint(self, sprint_id: int, payload: dict[str, Any]) -> SprintInfo:
        self._enter("update_sprint", sprint_id, payload)
        sprint = self._agile_sprint(sprint_id)
        changes: dict[str, Any] = {}
        for key, attribute in (("name", "name"), ("goal", "goal"), ("startDate", "start_date"), ("endDate", "end_date")):
            if key in payload:
                changes[attribute] = payload[key]
        if payload.get("state") == "active":
            changes.update(state="active", activated_date=FAKE_NOW)
        elif payload.get("state") == "closed":
            changes.update(state="closed", complete_date=FAKE_NOW)
        self.sprints[sprint_id] = dataclasses.replace(sprint, **changes)
        return self.sprints[sprint_id]

    def list_sprint_issues(self, sprint_id: int, *, keys: Sequence[str] = ()) -> list[Issue]:
        self._enter("list_sprint_issues", sprint_id, tuple(keys))
        self._agile_sprint(sprint_id)
        members = self._agile_by_rank([key for key in self.sprint_members.get(sprint_id, []) if key in self.issues])
        return [self.issues[key] for key in members if not keys or key in keys]

    def move_issues_to_sprint(self, sprint_id: int, payload: dict[str, Any]) -> None:
        self._enter("move_issues_to_sprint", sprint_id, payload)
        self._agile_sprint(sprint_id)
        for key in payload["issues"]:
            issue = self._issue(key)
            for number, members in self.sprint_members.items():
                if number != sprint_id and key in members and self.sprints[number].state != "closed":
                    members.remove(key)
            members = self.sprint_members.setdefault(sprint_id, [])
            if key not in members:
                members.append(key)
            self.issues[key] = dataclasses.replace(issue, sprints=self._agile_sprint_field(key))

    # -- ranking, epics and the backlog ----------------------------------------------------------------

    def rank_issues(self, payload: dict[str, Any]) -> None:
        self._enter("rank_issues", payload)
        order = self._agile_ranked()
        keys = list(payload["issues"])
        anchor = payload.get("rankBeforeIssue") or payload.get("rankAfterIssue")
        for key in [*keys, anchor]:
            self._issue(key)
        rest = [key for key in order if key not in keys]
        position = rest.index(anchor) + (0 if "rankBeforeIssue" in payload else 1)
        self.rank_list = rest[:position] + keys + rest[position:]

    def rank_order(self, keys: Sequence[str]) -> list[str]:
        self._enter("rank_order", tuple(keys))
        return self._agile_by_rank([key for key in keys if key in self.issues])

    def get_epic(self, epic: str) -> Epic:
        self._enter("get_epic", epic)
        if epic not in self.epics:
            raise NotFoundError(f"Not found: GET /epic/{epic} - The epic does not exist or you do not have permission to view it.")
        return self.epics[epic]

    def list_epics(self, board_id: int, *, include_done: bool = False) -> list[Epic]:
        self._enter("list_epics", board_id, include_done)
        self._agile_board(board_id)
        return [epic for epic in self.epics.values() if include_done or not epic.done]

    def list_epic_issues(self, epic: str, *, keys: Sequence[str] = ()) -> list[Issue]:
        self._enter("list_epic_issues", epic, tuple(keys))
        if epic != "none" and epic not in self.epics:
            raise NotFoundError(f"Not found: GET /epic/{epic}/issue")
        chosen = [
            key
            for key in self._agile_ranked()
            if key in self.issues
            and self.issues[key].issue_type != "Epic"
            and ((self.issues[key].epic_key == epic) if epic != "none" else not self.issues[key].epic_key)
        ]
        return [self.issues[key] for key in chosen if not keys or key in keys]

    def move_issues_to_epic(self, epic: str, payload: dict[str, Any]) -> None:
        self._enter("move_issues_to_epic", epic, payload)
        if epic != "none" and epic not in self.epics:
            raise NotFoundError(f"Not found: POST /epic/{epic}/issue")
        for key in payload["issues"]:
            self.issues[key] = dataclasses.replace(self._issue(key), epic_key="" if epic == "none" else epic)

    def board_backlog(self, board_id: int, *, limit: int) -> tuple[list[Issue], int]:
        self._enter("board_backlog", board_id, limit)
        self._agile_board(board_id)
        active = {key for number, members in self.sprint_members.items() if self.sprints[number].state == "active" for key in members}
        if self.backlog_keys is not None:
            keys = [key for key in self.backlog_keys if key in self.issues]
        else:
            keys = [
                key
                for key in self._agile_ranked()
                if key not in active and self.issues[key].issue_type != "Epic" and not (self.issues[key].status and self.issues[key].status.is_done)
            ]
        return [self.issues[key] for key in keys[:limit]], len(keys)

    # -- the history of issues -------------------------------------------------------------------

    def search_with_history(self, jql: str) -> list[SprintIssue]:
        self._enter("search_with_history", jql)
        if " was " in jql and self.reject_history_search:
            raise RejectedError("Jira refused the request (HTTP 400): The operator 'WAS' is not supported by the 'Sprint' field.")
        if " was " in jql:
            number = int(jql.split(" was ", 1)[1].split()[0])
            return list(self.sprint_removed.get(number, []))
        number = int(jql.split("sprint = ", 1)[1].split()[0])
        return [self.sprint_history[key] for key in self.sprint_members.get(number, []) if key in self.sprint_history]

    def status_category_list(self) -> list[tuple[str, str, str]]:
        self._enter("status_category_list")
        return list(self.status_categories_in_jira)
