"""The agile calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse
from typing import Any, Mapping, Sequence

from ...domain.agile import Board, Epic, SprintInfo, batches
from ...domain.errors import RejectedError, ValidationError
from ...domain.model import Issue
from ...domain.sprints import MAX_REVIEWED_ISSUES, SprintIssue, keys_jql, rank_order_jql
from . import jira_mapper as mapper
from . import map_agile as agile_map

_AGILE = "/rest/agile/1.0"
_PAGE = 50  # what the Agile API gives per page at most
_MAX_PAGES = 400
_BOARD_CAP = 2000
_SPRINT_CAP = 3000


def _quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="")


def _query(params: Mapping[str, object]) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def _rank_failure(entry: Mapping[str, Any]) -> str:
    """One entry of the 207 answer of a rank: the issue and why it was not ranked."""
    reasons = ", ".join(str(reason) for reason in entry.get("errors") or []) or "not ranked"
    return f"{entry.get('issueKey') or entry.get('issueId')}: {reasons}"


class RestAgile:
    """Implements `AgilePort` (application/ports_agile.py) over Jira Data Center's REST API: the Agile API of Jira Software
    (`/rest/agile/1.0`) and, for the history of issues, the platform search with `expand=changelog`."""

    def _agile_pages(
        self, path: str, params: Mapping[str, object], *, key: str = "values", cap: int = _SPRINT_CAP
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Every page of a list (or the first `cap` entries), and the total when Jira says it. A server may give fewer entries than asked
        for, so the paging follows `isLast` and `total`, not the size of a page."""
        items: list[dict[str, Any]] = []
        start = 0
        for _ in range(_MAX_PAGES):
            data = self._call("GET", f"{_AGILE}{path}?{_query({**params, 'startAt': start, 'maxResults': _PAGE})}")
            data = data if isinstance(data, dict) else {}
            page = [item for item in data.get(key) or [] if isinstance(item, dict)]
            items.extend(page)
            start += len(page)
            total = data.get("total") if isinstance(data.get("total"), int) else None
            if not page or data.get("isLast") is True or (total is not None and start >= total) or len(items) >= cap:
                return items, total
        return items, None

    def _agile_fields(self) -> str:
        """The `fields` of an issue list: what the commands show, with the custom fields of this Jira (story points, epic link)."""
        ids = self.field_ids()
        return ",".join(["summary", "issuetype", "status"] + [ids[name] for name in ("story_points", "epic_link") if ids.get(name)])

    def _agile_issues(self, items: Sequence[Mapping[str, Any]]) -> list[Issue]:
        ids = self.field_ids()
        return [mapper.issue_from(dict(item), ids) for item in items]

    # -- boards ----------------------------------------------------------------------------------

    def list_boards(self, *, project: str = "", board_type: str = "", name: str = "") -> list[Board]:
        params: dict[str, object] = {}
        if project:
            params["projectKeyOrId"] = project
        if board_type in ("scrum", "kanban"):  # the reference documents these two values; any other is filtered here
            params["type"] = board_type
        if name:
            params["name"] = name
        items, _ = self._agile_pages("/board", params, cap=_BOARD_CAP)
        boards = [agile_map.board_from(item) for item in items]
        return [board for board in boards if not board_type or board.type == board_type]

    def get_board(self, board_id: int) -> Board:
        data = self._call("GET", f"{_AGILE}/board/{_quote(board_id)}")
        return agile_map.board_from(data if isinstance(data, dict) else {})

    def board_projects(self, board_id: int) -> list[str]:
        items, _ = self._agile_pages(f"/board/{_quote(board_id)}/project", {})
        return [key for key in (agile_map.project_key_from(item) for item in items) if key]

    # -- sprints ---------------------------------------------------------------------------------

    def list_sprints(self, board_id: int, *, states: Sequence[str] = ()) -> list[SprintInfo]:
        params = {"state": ",".join(states)} if states else {}
        items, _ = self._agile_pages(f"/board/{_quote(board_id)}/sprint", params)
        return [agile_map.sprint_from(item) for item in items]

    def get_sprint(self, sprint_id: int) -> SprintInfo:
        data = self._call("GET", f"{_AGILE}/sprint/{_quote(sprint_id)}")
        return agile_map.sprint_from(data if isinstance(data, dict) else {})

    def create_sprint(self, payload: dict[str, Any]) -> SprintInfo:
        data = self._call("POST", f"{_AGILE}/sprint", payload)
        return agile_map.sprint_from(data if isinstance(data, dict) else {})

    def update_sprint(self, sprint_id: int, payload: dict[str, Any]) -> SprintInfo:
        data = self._call("POST", f"{_AGILE}/sprint/{_quote(sprint_id)}", payload)
        return agile_map.sprint_from(data if isinstance(data, dict) else {})

    def list_sprint_issues(self, sprint_id: int, *, keys: Sequence[str] = ()) -> list[Issue]:
        found: list[Issue] = []
        for chunk in batches(keys) if keys else [[]]:
            params: dict[str, object] = {"fields": self._agile_fields()}
            if chunk:
                params["jql"] = keys_jql(chunk)
            items, _ = self._agile_pages(f"/sprint/{_quote(sprint_id)}/issue", params, key="issues")
            found.extend(self._agile_issues(items))
        return found

    def move_issues_to_sprint(self, sprint_id: int, payload: dict[str, Any]) -> None:
        self._call("POST", f"{_AGILE}/sprint/{_quote(sprint_id)}/issue", payload)

    # -- ranking, epics and the backlog --------------------------------------------------------------

    def rank_issues(self, payload: dict[str, Any]) -> None:
        """Ranks the issues. Jira answers 207 when it could not rank some of them; that is a refusal, with the reasons."""
        data = self._call("PUT", f"{_AGILE}/issue/rank", payload)
        entries = data.get("entries") if isinstance(data, dict) else None
        failed = [entry for entry in entries or [] if isinstance(entry, dict) and (entry.get("errors") or entry.get("status") not in (None, 200))]
        if failed:
            reasons = "; ".join(_rank_failure(entry) for entry in failed)
            raise RejectedError(
                f"Jira could not rank every issue: {reasons}",
                hint="The other issues were ranked. Check the ones named, or rank them one by one.",
                details={"entries": failed},
            )

    def get_epic(self, epic: str) -> Epic:
        data = self._call("GET", f"{_AGILE}/epic/{_quote(epic)}")
        return agile_map.epic_from(data if isinstance(data, dict) else {})

    def list_epics(self, board_id: int, *, include_done: bool = False) -> list[Epic]:
        params = {} if include_done else {"done": "false"}
        items, _ = self._agile_pages(f"/board/{_quote(board_id)}/epic", params)
        return [agile_map.epic_from(item) for item in items]

    def list_epic_issues(self, epic: str, *, keys: Sequence[str] = ()) -> list[Issue]:
        found: list[Issue] = []
        for chunk in batches(keys) if keys else [[]]:
            params: dict[str, object] = {"fields": self._agile_fields()}
            if chunk:
                params["jql"] = keys_jql(chunk)
            items, _ = self._agile_pages(f"/epic/{_quote(epic)}/issue", params, key="issues")
            found.extend(self._agile_issues(items))
        return found

    def move_issues_to_epic(self, epic: str, payload: dict[str, Any]) -> None:
        self._call("POST", f"{_AGILE}/epic/{_quote(epic)}/issue", payload)

    def board_backlog(self, board_id: int, *, limit: int) -> tuple[list[Issue], int]:
        items, total = self._agile_pages(f"/board/{_quote(board_id)}/backlog", {"fields": self._agile_fields()}, key="issues", cap=limit)
        shown = self._agile_issues(items[:limit])
        return shown, total if total is not None else len(shown)

    # -- the history of issues -------------------------------------------------------------------

    def search_with_history(self, jql: str) -> list[SprintIssue]:
        """The issues of a search with their change history (`expand=changelog`), for the review of a sprint. Refuses a search that
        finds more issues than a review can read, rather than reviewing part of them."""
        ids = self.field_ids()
        fields = ["summary", "issuetype", "status", "created"] + ([ids["story_points"]] if ids.get("story_points") else [])
        found: list[SprintIssue] = []
        start = 0
        while True:
            query = _query({"jql": jql, "startAt": start, "maxResults": _PAGE, "fields": ",".join(fields), "expand": "changelog"})
            data = self._call("GET", f"/search?{query}")
            data = data if isinstance(data, dict) else {}
            raw = [item for item in data.get("issues") or [] if isinstance(item, dict)]
            found.extend(agile_map.sprint_issue_from(item, ids) for item in raw)
            start += len(raw)
            total = data.get("total") if isinstance(data.get("total"), int) else start
            if total > MAX_REVIEWED_ISSUES:
                raise ValidationError(
                    f"The sprint has {total} issues; a review reads at most {MAX_REVIEWED_ISSUES}.",
                    hint="Review a smaller sprint, or count with `jira report --sprint NAME`.",
                )
            if not raw or start >= total:
                return found

    def status_category_list(self) -> list[tuple[str, str, str]]:
        return agile_map.status_triples_from(self._call("GET", "/status"))

    def rank_order(self, keys: Sequence[str]) -> list[str]:
        """The order of the rank of these issues (at most one page of them: a rank is never asked for more than 51)."""
        order: list[str] = []
        start = 0
        while True:
            query = _query({"jql": rank_order_jql(keys), "fields": "summary", "startAt": start, "maxResults": _PAGE})
            data = self._call("GET", f"/search?{query}")
            data = data if isinstance(data, dict) else {}
            raw = [item for item in data.get("issues") or [] if isinstance(item, dict)]
            order.extend(str(item.get("key")) for item in raw)
            start += len(raw)
            total = data.get("total") if isinstance(data.get("total"), int) else start
            if not raw or start >= total:
                return order
