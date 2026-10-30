"""The agile routes of the mock Jira: boards, sprints, epics, the backlog, ranking and the history of issues for a sprint review.

Behaves like Jira Software 11.3: 404 for what does not exist, 400 for what the rules refuse (a closed sprint, a state that cannot be
reached, more than 50 issues in a call), paging fields (`startAt`, `maxResults`, `isLast`, `total`) with a page that is never larger
than the server's cap, and 207 for a rank that could not be done for every issue. A search with `expand=changelog` for the JQL that the
CLI writes for a sprint is answered here (history of the field Sprint and of the status); every other search goes to the built-in one.

Seed data only when a test asks for it (`add_board`, `add_sprint`, `put_in_sprint`, `seed_review`), except board 1200.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Sequence

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

AGILE = "/rest/agile/1.0"
LIMIT_PER_CALL = 50
STATUS_IDS = {"To Do": "1", "In Progress": "3", "Done": "5"}  # what `GET /status` says, and the history of a status refers to
_BOARD = re.compile(r"^/board/([0-9]{1,18})(?:/(project|sprint|backlog|epic))?$")
_SPRINT = re.compile(r"^/sprint/([0-9]{1,18})(?:/(issue))?$")
_EPIC = re.compile(r"^/epic/([A-Za-z][A-Za-z0-9_]*-\d+|none)(?:/(issue))?$")
_ISSUE = re.compile(r"^/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)$")
_CONDITION = (
    re.compile(r"^sprint (=|was|!=) ([0-9]{1,18})$", re.IGNORECASE),
    re.compile(r"^key in \(([^)]*)\)$", re.IGNORECASE),
)
_NUMBER_AT_END = re.compile(r"-([0-9]{1,18})$")
_ID = re.compile(r"[0-9]{1,18}")  # a run of more digits is no number of Jira, and `int()` refuses thousands of them


def _number(key: str) -> int:
    """The number of an issue key, for the order of the keys; 0 for a key whose number is longer than 18 digits."""
    found = _NUMBER_AT_END.search(key)
    return int(found.group(1)) if found else 0


def _error(*messages: str) -> dict[str, Any]:
    return {"errorMessages": list(messages), "errors": {}}


def sprint_text(sprint: dict[str, Any]) -> str:
    """How the field Sprint of an issue holds a sprint (the string form of Jira Data Center)."""
    state, name, goal = sprint["state"].upper(), sprint["name"], sprint.get("goal", "")
    fields = f"id={sprint['id']},rapidViewId={sprint['originBoardId']},state={state},name={name},goal={goal}"
    return f"com.atlassian.greenhopper.service.sprint.Sprint@{sprint['id']}[{fields}]"


def _ids_of(texts: Sequence[str]) -> list[int]:
    """The sprint ids in the strings of the field Sprint of an issue."""
    return sorted(int(found.group(1)) for found in (re.search(r"\bid=([0-9]{1,18}),", text) for text in texts) if found)


def _numbers(text: str) -> set[int]:
    """The numbers in a text such as `6, 7`, as the history of the field Sprint writes the ids."""
    return {int(part) for part in re.split(r"[,\s]+", text) if _ID.fullmatch(part)}


class AgileRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.boards: dict[int, dict[str, Any]] = {1200: {"id": 1200, "name": "Demo board", "type": "scrum", "projects": ["PROJ"]}}
        self.sprints: dict[int, dict[str, Any]] = {}
        self.histories: dict[str, list[dict[str, Any]]] = {}  # issue key to its history entries, oldest first
        self.order: list[str] = []  # the rank, from the top; issues that are not in it come after, by number
        self.unrankable: dict[str, list[str]] = {}  # keys that Jira cannot rank (the answer is then 207), with its reasons
        self.history_operator = True  # False: `sprint was` is refused with 400, as an older Jira does
        self.changelog_cap: int | None = None  # keep only this many history entries per issue, as a Jira that cuts the changelog does
        self.moved: dict[str, str] = {}  # an old key to the key that its issue has now (it moved to another project); the issue is under the new one
        self.clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
        self.next_sprint_id = 100
        self.next_history_id = 1000

    # -- the state -----------------------------------------------------------------------------

    def stamp(self) -> str:
        return self.clock().strftime("%Y-%m-%dT%H:%M:%S.000%z")

    def ranked(self) -> list[str]:
        known = [key for key in self.order if key in self.jira.issues]
        self.order = known + sorted((key for key in self.jira.issues if key not in known), key=_number)
        return self.order

    def by_rank(self, keys: Sequence[str]) -> list[str]:
        order = self.ranked()
        return sorted(keys, key=order.index)

    def sprint_ids_of(self, key: str) -> list[int]:
        return _ids_of(self.jira.issues[key]["fields"].get(jira_constants().SPRINT) or [])

    def members(self, sprint_id: int) -> list[str]:
        """The issues that are in the sprint now, in the order of the rank."""
        return self.by_rank([key for key in self.jira.issues if sprint_id in self.sprint_ids_of(key)])

    def record(self, key: str, field: str, before: str, after: str, before_text: str = "", after_text: str = "") -> None:
        """A history entry for a change that is made through the mock, so that a review can see it."""
        self.next_history_id += 1
        item = {"field": field, "fieldtype": "custom", "from": before, "fromString": before_text, "to": after, "toString": after_text}
        entry = {"id": str(self.next_history_id), "author": jira_constants().USER, "created": self.stamp(), "items": [item]}
        self.histories.setdefault(key, []).append(entry)
        self.jira.issues[key]["fields"]["updated"] = entry["created"]

    # -- the seams for the tests -----------------------------------------------------------------

    def set_sprints_of(self, key: str, sprint_ids: Sequence[int]) -> None:
        """Puts the issue in these sprints (the field Sprint of the issue), without a history entry."""
        fields = self.jira.issues[key]["fields"]
        fields[jira_constants().SPRINT] = [sprint_text(self.sprints[number]) for number in sprint_ids]

    # -- routes ------------------------------------------------------------------------------------

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area."""
        if path.startswith(AGILE + "/"):
            return self._agile(handler, method, path[len(AGILE) :], query, body)
        found = _ISSUE.match(path) if method == "GET" else None
        if found and found.group(1) in self.moved:  # Jira answers the old key with the issue as it is now, under its new key
            handler.reply(200, self.issue_json(self.moved[found.group(1)], query))
            return True
        if method == "GET" and path == "/rest/api/2/search":
            return self._search(handler, query)
        if method == "GET" and path == "/rest/api/2/status":
            return self._statuses(handler, method, path, query, body)
        return False

    def _agile(self, handler: "_Handler", method: str, rest: str, query: dict[str, list[str]], body: Any) -> bool:
        if method == "GET" and rest == "/board":
            return self._boards(handler, query)
        if method == "POST" and rest == "/sprint":
            return self._create_sprint(handler, body)
        if method == "PUT" and rest == "/issue/rank":
            return self._rank(handler, body)
        found = _BOARD.match(rest)
        if found and method == "GET":
            return self._board(handler, int(found.group(1)), found.group(2), query)
        found = _SPRINT.match(rest)
        if found:
            return self._sprint(handler, method, int(found.group(1)), found.group(2), query, body)
        found = _EPIC.match(rest)
        if found:
            return self._epic(handler, method, found.group(1), found.group(2), query, body)
        return False

    # paging ------------------------------------------------------------------------------------

    def _window(self, query: dict[str, list[str]]) -> tuple[int, int]:
        start = int(query.get("startAt", ["0"])[0])
        return start, min(int(query.get("maxResults", ["50"])[0]), self.jira.page_cap)

    def _values(self, handler: "_Handler", query: dict[str, list[str]], items: list[Any], *, total: bool = True) -> bool:
        start, size = self._window(query)
        page = {"maxResults": size, "startAt": start, "isLast": start + size >= len(items), "values": items[start : start + size]}
        handler.reply(200, {**page, "total": len(items)} if total else page)
        return True

    def _issues(self, handler: "_Handler", query: dict[str, list[str]], keys: list[str]) -> bool:
        start, size = self._window(query)
        page = [self.issue_json(key, query) for key in keys[start : start + size]]
        handler.reply(200, {"expand": "schema,names", "startAt": start, "maxResults": size, "total": len(keys), "issues": page})
        return True

    def issue_json(self, key: str, query: dict[str, list[str]]) -> dict[str, Any]:
        issue = self.jira.issues[key]
        shown = {"id": issue["id"], "key": key, "fields": self.jira.select(issue["fields"], query)}
        if "changelog" in ",".join(query.get("expand", [])):
            histories = self.histories.get(key, [])
            kept = histories if self.changelog_cap is None else histories[: self.changelog_cap]
            shown["changelog"] = {"startAt": 0, "maxResults": len(kept), "total": len(histories), "histories": kept}
        return shown

    def _keys_filter(self, query: dict[str, list[str]], keys: list[str]) -> list[str] | None:
        """The keys that a `jql` of the form `key in (...)` leaves; None when the JQL is anything else (the answer is then 400)."""
        jql = query.get("jql", [""])[0].strip()
        if not jql:
            return keys
        found = _CONDITION[1].match(jql)
        if not found:
            return None
        wanted = {item.strip().upper() for item in found.group(1).split(",") if item.strip()}
        return [key for key in keys if key in wanted]

    # boards --------------------------------------------------------------------------------------

    def _boards(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        name = query.get("name", [""])[0].casefold()
        project = query.get("projectKeyOrId", [""])[0].upper()
        types = {item for value in query.get("type", []) for item in value.split(",") if item}
        found = []
        for board in self.boards.values():
            if (name and name not in board["name"].casefold()) or (project and project not in board["projects"]) or (types and board["type"] not in types):
                continue
            found.append({"id": board["id"], "name": board["name"], "type": board["type"], "self": f"{self.jira.base_url}{AGILE}/board/{board['id']}"})
        return self._values(handler, query, found)

    def _board(self, handler: "_Handler", board_id: int, part: str | None, query: dict[str, list[str]]) -> bool:
        board = self.boards.get(board_id)
        if board is None:
            handler.reply(404, _error("The board does not exist or you do not have permission to view it."))
            return True
        if part is None:
            handler.reply(200, {"id": board_id, "name": board["name"], "type": board["type"]})
            return True
        if part == "project":
            projects = [{"id": str(10000 + index), "key": key, "name": f"Project {key}"} for index, key in enumerate(board["projects"])]
            return self._values(handler, query, projects)
        if part == "sprint":
            return self._board_sprints(handler, board, query)
        if part == "backlog":
            return self._issues(handler, query, self._backlog(board))
        return self._epics(handler, board, query)

    def _board_sprints(self, handler: "_Handler", board: dict[str, Any], query: dict[str, list[str]]) -> bool:
        if board["type"] != "scrum":
            handler.reply(400, _error("The board does not support sprints"))
            return True
        states = {item for value in query.get("state", []) for item in value.split(",") if item}
        rank = {"closed": 0, "active": 1, "future": 2}
        found = [item for item in self.sprints.values() if item["originBoardId"] == board["id"] and (not states or item["state"] in states)]
        found.sort(key=lambda item: (rank[item["state"]], item["id"]))
        return self._values(handler, query, [self.sprint_json(item) for item in found], total=False)

    def _backlog(self, board: dict[str, Any]) -> list[str]:
        """The issues of the projects of the board that are not done, not epics and not in an active sprint, in the order of the rank."""
        active = {number for number, item in self.sprints.items() if item["state"] == "active"}
        keys = []
        for key in self.ranked():
            fields = self.jira.issues[key]["fields"]
            in_board = key.rsplit("-", 1)[0] in board["projects"]
            open_issue = fields["issuetype"]["name"] != "Epic" and fields["status"]["name"] != "Done"
            if in_board and open_issue and not active & set(self.sprint_ids_of(key)):
                keys.append(key)
        return keys

    def _epics(self, handler: "_Handler", board: dict[str, Any], query: dict[str, list[str]]) -> bool:
        wanted = query.get("done", [""])[0]
        found = []
        for key in self.ranked():
            issue = self.jira.issues[key]
            if issue["fields"]["issuetype"]["name"] != "Epic" or key.rsplit("-", 1)[0] not in board["projects"]:
                continue
            done = issue["fields"]["status"]["name"] == "Done"
            if wanted in ("true", "false") and done != (wanted == "true"):
                continue
            summary = issue["fields"]["summary"]
            found.append({"id": int(issue["id"]), "key": key, "name": summary, "summary": summary, "color": {"key": "color_4"}, "done": done})
        return self._values(handler, query, found)

    # sprints -------------------------------------------------------------------------------------

    @staticmethod
    def sprint_json(sprint: dict[str, Any]) -> dict[str, Any]:
        shown = {key: value for key, value in sprint.items() if value not in ("", None)}
        shown["self"] = f"sprint/{sprint['id']}"
        return shown

    def _sprint(self, handler: "_Handler", method: str, sprint_id: int, part: str | None, query: dict[str, list[str]], body: Any) -> bool:
        sprint = self.sprints.get(sprint_id)
        if sprint is None:
            handler.reply(404, _error("The sprint does not exist or you do not have permission to view it."))
            return True
        if method == "GET" and part is None:
            handler.reply(200, self.sprint_json(sprint))
        elif method == "GET":
            keys = self._keys_filter(query, self.members(sprint_id))
            if keys is None:
                handler.reply(400, _error("Error in the JQL Query: only `key in (...)` is understood by the mock here."))
                return True
            return self._issues(handler, query, keys)
        elif method == "POST" and part is None:
            return self._update_sprint(handler, sprint, body)
        elif method == "POST":
            return self._move_to_sprint(handler, sprint, body)
        else:
            return False
        return True

    def _create_sprint(self, handler: "_Handler", body: Any) -> bool:
        data = body if isinstance(body, dict) else {}
        board_id = data.get("originBoardId")
        board = self.boards.get(board_id) if isinstance(board_id, int) else None
        name = " ".join(str(data.get("name") or "").split())
        problem = self._date_problem(data)
        if not name:
            handler.reply(400, _error("The sprint name must not be empty."))
        elif not isinstance(board_id, int):
            handler.reply(400, _error("The origin board id is required."))
        elif board is None:
            handler.reply(404, _error("The board does not exist or you do not have permission to view it."))
        elif board["type"] != "scrum":
            handler.reply(400, _error("The board does not support sprints"))
        elif problem:
            handler.reply(400, _error(problem))
        else:
            self.next_sprint_id += 1
            sprint = {"id": self.next_sprint_id, "name": name, "state": "future", "originBoardId": board["id"], "goal": str(data.get("goal") or "")}
            sprint.update({key: data[key] for key in ("startDate", "endDate") if data.get(key)})
            self.sprints[sprint["id"]] = sprint
            handler.reply(201, self.sprint_json(sprint))
        return True

    @staticmethod
    def _date_problem(data: dict[str, Any]) -> str:
        for key in ("startDate", "endDate"):
            if data.get(key):
                try:
                    datetime.fromisoformat(str(data[key]).replace("Z", "+00:00"))
                except ValueError:
                    return f"Invalid date format for {key}: {data[key]}"
        return ""

    def _update_sprint(self, handler: "_Handler", sprint: dict[str, Any], body: Any) -> bool:
        data = body if isinstance(body, dict) else {}
        problem = self._update_problem(sprint, data)
        if problem:
            handler.reply(400, _error(problem))
            return True
        for key in ("name", "goal", "startDate", "endDate"):
            if key in data:
                sprint[key] = " ".join(str(data[key]).split()) if key == "name" else data[key]
        if data.get("state") == "active":
            sprint.update(state="active", activatedDate=self.clock().isoformat(timespec="milliseconds"))
        elif data.get("state") == "closed":
            sprint.update(state="closed", completeDate=self.clock().isoformat(timespec="milliseconds"))
        handler.reply(200, self.sprint_json(sprint))
        return True

    def _update_problem(self, sprint: dict[str, Any], data: dict[str, Any]) -> str:
        """Why Jira refuses this update of a sprint, after the rules of the reference; an empty text when it accepts it."""
        if sprint["state"] == "closed":
            return "Sprints that are in a closed state cannot be updated."
        if "completeDate" in data:
            return "The completeDate field cannot be updated manually."
        if "name" in data and not " ".join(str(data["name"]).split()):
            return "The sprint name must not be empty."
        problem = self._date_problem(data)
        state = data.get("state")
        if problem or not state:
            return problem
        if state == "active":
            if sprint["state"] != "future":
                return f"A sprint can only be started from the future state, this one is {sprint['state']}."
            if not (data.get("startDate") or sprint.get("startDate")) or not (data.get("endDate") or sprint.get("endDate")):
                return "A sprint needs a start date and an end date to be started."
        elif state == "closed":
            if sprint["state"] != "active":
                return f"A sprint can only be completed from the active state, this one is {sprint['state']}."
        else:
            return f"Illegal state transition to {state}."
        return ""

    def _check_issues(self, handler: "_Handler", body: Any, *, empty_ok: bool = False) -> list[str] | None:
        """The keys of a body `{"issues": [...]}` (an old key of a moved issue as its current one), or None after the 400 for what is wrong."""
        keys = body.get("issues") if isinstance(body, dict) else None
        if not isinstance(keys, list) or (not keys and not empty_ok):
            handler.reply(400, _error("The list of issues is missing or empty."))
        elif len(keys) > LIMIT_PER_CALL:
            handler.reply(400, _error(f"The maximum number of issues that can be moved in one operation is {LIMIT_PER_CALL}."))
        elif any(self.moved.get(str(key), str(key)) not in self.jira.issues for key in keys):
            missing = [str(key) for key in keys if self.moved.get(str(key), str(key)) not in self.jira.issues]
            handler.reply(400, _error(f"Issue does not exist or you do not have permission to see it: {', '.join(missing)}"))
        else:
            return [self.moved.get(str(key), str(key)) for key in keys]
        return None

    def _move_to_sprint(self, handler: "_Handler", sprint: dict[str, Any], body: Any) -> bool:
        if sprint["state"] == "closed":
            handler.reply(400, _error("Issues can only be moved to open or active sprints."))
            return True
        keys = self._check_issues(handler, body)
        if keys is None:
            return True
        const = jira_constants()
        for key in keys:
            fields = self.jira.issues[key]["fields"]
            texts = list(fields.get(const.SPRINT) or [])
            before = _ids_of(texts)
            kept = [text for text in texts if "state=CLOSED" in text or f"id={sprint['id']}," in text]
            if sprint["id"] not in before:
                kept.append(sprint_text(sprint))
            after = _ids_of(kept)
            fields[const.SPRINT] = kept
            if after != before:
                names = [self.sprints[number]["name"] for number in after if number in self.sprints]
                self.record(key, "Sprint", ", ".join(map(str, before)), ", ".join(map(str, after)), "", ", ".join(names))
        handler.reply(204)
        return True

    # epics ---------------------------------------------------------------------------------------

    def _epic_exists(self, key: str) -> bool:
        issue = self.jira.issues.get(key)
        return issue is not None and issue["fields"]["issuetype"]["name"] == "Epic"

    def _epic(self, handler: "_Handler", method: str, epic: str, part: str | None, query: dict[str, list[str]], body: Any) -> bool:
        if epic != "none" and not self._epic_exists(epic):
            handler.reply(404, _error("The epic does not exist or you do not have permission to view it."))
            return True
        const = jira_constants()
        if method == "GET" and part is None:
            issue = self.jira.issues[epic]
            summary = issue["fields"]["summary"]
            done = issue["fields"]["status"]["name"] == "Done"
            handler.reply(200, {"id": int(issue["id"]), "key": epic, "name": summary, "summary": summary, "done": done})
        elif method == "GET":
            members = []
            for key in self.ranked():
                fields = self.jira.issues[key]["fields"]
                if fields["issuetype"]["name"] != "Epic" and (fields.get(const.EPIC) or "none") == epic:
                    members.append(key)
            keys = self._keys_filter(query, members)
            if keys is None:
                handler.reply(400, _error("Error in the JQL Query: only `key in (...)` is understood by the mock here."))
                return True
            return self._issues(handler, query, keys)
        elif method == "POST" and part == "issue":
            keys = self._check_issues(handler, body)
            if keys is None:
                return True
            for key in keys:
                fields = self.jira.issues[key]["fields"]
                before = fields.get(const.EPIC) or ""
                after = "" if epic == "none" else epic
                fields[const.EPIC] = after or None
                if before != after:
                    self.record(key, "Epic Link", before, after, before, after)
            handler.reply(204)
        else:
            return False
        return True

    # ranking -------------------------------------------------------------------------------------

    def _rank(self, handler: "_Handler", body: Any) -> bool:
        keys = self._check_issues(handler, body)
        if keys is None:
            return True
        data = body if isinstance(body, dict) else {}
        before, after = data.get("rankBeforeIssue"), data.get("rankAfterIssue")
        anchor = before or after
        if bool(before) == bool(after):
            handler.reply(400, _error("Exactly one of rankBeforeIssue and rankAfterIssue is needed."))
        elif anchor not in self.jira.issues:
            handler.reply(400, _error(f"Issue does not exist or you do not have permission to see it: {anchor}"))
        elif anchor in keys:
            handler.reply(400, _error("An issue cannot be ranked relative to itself."))
        else:
            failing = [key for key in keys if key in self.unrankable]
            movable = [key for key in keys if key not in failing]
            rest = [key for key in self.ranked() if key not in movable]
            position = rest.index(anchor) + (0 if before else 1)
            self.order = rest[:position] + movable + rest[position:]
            if failing:
                entries = [self._rank_entry(key, 400, self.unrankable[key]) for key in failing]
                entries += [self._rank_entry(key, 200, []) for key in movable]
                handler.reply(207, {"entries": entries})
            else:
                handler.reply(204)
        return True

    def _rank_entry(self, key: str, status: int, errors: list[str]) -> dict[str, Any]:
        return {"issueKey": key, "issueId": int(self.jira.issues[key]["id"]), "status": status, "errors": errors}

    # search and statuses -------------------------------------------------------------------------

    def _parse_jql(self, jql: str) -> tuple[list[tuple[str, str]], str] | None:
        """`(conditions, order)` for the JQL that the CLI writes for a sprint; None for any other JQL (the built-in search answers it)."""
        condition, order = jql, ""
        found = re.search(r"\s+order\s+by\s+(.+)$", jql, re.IGNORECASE)
        if found:
            condition, order = jql[: found.start()], found.group(1).strip().lower()
        parsed: list[tuple[str, str]] = []
        for part in re.split(r"\s+AND\s+", condition.strip(), flags=re.IGNORECASE):
            sprint, keys = (pattern.match(part.strip()) for pattern in _CONDITION)
            if sprint:
                parsed.append((sprint.group(1).lower(), sprint.group(2)))
            elif keys:
                parsed.append(("key", keys.group(1)))
            else:
                return None
        mine = any(kind != "key" for kind, _ in parsed) or order.startswith("rank")
        return (parsed, order) if mine else None

    def _was_in(self, key: str, sprint_id: int) -> bool:
        """Whether the issue is in the sprint now or was at some time: the history names it in `from` or `to` of the field Sprint."""
        if sprint_id in self.sprint_ids_of(key):
            return True
        for entry in self.histories.get(key, []):
            for item in entry["items"]:
                if item["field"] == "Sprint" and sprint_id in _numbers(f"{item['from']},{item['to']}"):
                    return True
        return False

    def _search(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        parsed = self._parse_jql(query.get("jql", [""])[0])
        if parsed is None:
            return False
        conditions, order = parsed
        keys = sorted(self.jira.issues, key=_number)
        for kind, value in conditions:
            if kind == "key":
                wanted = {item.strip().upper() for item in value.split(",") if item.strip()}
                keys = [key for key in keys if key in wanted]
                continue
            sprint_id = int(value)
            if sprint_id not in self.sprints:
                handler.reply(400, _error(f"A value with ID '{sprint_id}' does not exist for the field 'sprint'."))
                return True
            if kind == "was" and not self.history_operator:
                handler.reply(400, _error("The operator 'WAS' is not supported by the 'Sprint' field."))
                return True
            if kind == "=":
                keys = [key for key in keys if sprint_id in self.sprint_ids_of(key)]
            elif kind == "!=":
                keys = [key for key in keys if sprint_id not in self.sprint_ids_of(key)]
            else:
                keys = [key for key in keys if self._was_in(key, sprint_id)]
        if order.startswith("rank"):
            keys = self.by_rank(keys)
        return self._search_page(handler, query, keys)

    def _search_page(self, handler: "_Handler", query: dict[str, list[str]], keys: list[str]) -> bool:
        start, size = self._window(query)
        page = [self.issue_json(key, query) for key in keys[start : start + size]]
        handler.reply(200, {"startAt": start, "maxResults": size, "total": len(keys), "issues": page})
        return True

    def _statuses(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """`GET /status`: another area may know it better, so it is asked first; this one answers with the three statuses of the mock."""
        for extension in self.jira.extensions:
            if extension is not self and extension.handle(handler, method, path, query, body):
                return True
        const = jira_constants()
        reply = [
            {"id": STATUS_IDS[name], "name": name, "statusCategory": {"id": index, "key": status["statusCategory"]["key"], "name": name}}
            for index, (name, status) in enumerate(const.STATUSES.items(), start=2)
        ]
        handler.reply(200, reply)
        return True


# -- data for the tests ------------------------------------------------------------------------------


def routes_of(jira: "MockJira") -> AgileRoutes:
    return next(extension for extension in jira.extensions if isinstance(extension, AgileRoutes))


def add_board(jira: "MockJira", board_id: int, name: str, board_type: str = "scrum", projects: Sequence[str] = ("PROJ",)) -> dict[str, Any]:
    board = {"id": board_id, "name": name, "type": board_type, "projects": list(projects)}
    routes_of(jira).boards[board_id] = board
    return board


def add_sprint(
    jira: "MockJira",
    sprint_id: int,
    name: str,
    state: str,
    *,
    board: int = 1200,
    start: str = "",
    end: str = "",
    complete: str = "",
    goal: str = "",
) -> dict[str, Any]:
    sprint = {"id": sprint_id, "name": name, "state": state, "originBoardId": board, "goal": goal}
    for key, value in (("startDate", start), ("endDate", end), ("completeDate", complete)):
        if value:
            sprint[key] = value
    routes_of(jira).sprints[sprint_id] = sprint
    return sprint


def add_issue(
    jira: "MockJira",
    key: str,
    state: str = "To Do",
    points: float | None = None,
    *,
    kind: str = "Story",
    created: str = "",
    epic: str | None = None,
    **fields: Any,
) -> None:
    """A new issue of the project, with no sprint, and no history until a test gives it one."""
    const = jira_constants()
    issue = const.make_issue(key, kind, state, f"Summary of {key}", points, epic, **fields)
    issue["fields"][const.SPRINT] = []
    issue["fields"]["comment"] = {"total": 0, "comments": []}
    if created:
        issue["fields"]["created"] = created
    if kind == "Sub-task":
        issue["fields"]["issuetype"] = {"name": kind, "subtask": True}
    jira.issues[key] = issue


def jira_constants() -> Any:
    """The module of the mock Jira, imported late: it imports this module."""
    from . import mock_jira

    return mock_jira


def history_entry(
    routes: AgileRoutes, key: str, created: str, field: str, before: str, after: str, before_text: str = "", after_text: str = ""
) -> None:
    """A history entry at a given time: for `Sprint` the ids are texts such as `"6, 7"`, for `status` the status ids."""
    routes.next_history_id += 1
    kind = "custom" if field == "Sprint" else "jira"
    item = {"field": field, "fieldtype": kind, "from": before, "fromString": before_text, "to": after, "toString": after_text}
    entry = {"id": str(routes.next_history_id), "author": jira_constants().USER, "created": created, "items": [item]}
    routes.histories.setdefault(key, []).append(entry)


def status_change(routes: AgileRoutes, key: str, created: str, before: str, after: str) -> None:
    history_entry(routes, key, created, "status", STATUS_IDS[before], STATUS_IDS[after], before, after)


def seed_review(jira: "MockJira") -> AgileRoutes:
    """A closed sprint with every case of a review, an older closed sprint, an active and a future one, on board 1200.

    The default issues name sprints 6 and 7 in their field Sprint (as the sprints of an older sprint report); here those ids are the sprints
    below, so the default issues are taken out of every sprint first. Sprint 6 ran from 2025-09-01 08:00 to its completion on 2025-09-15
    16:00 (+0200 everywhere). Its issues: PROJ-21 committed, completed; PROJ-22 committed, carried over (and now in sprint 7); PROJ-23 added,
    completed; PROJ-24 committed, removed; PROJ-25 committed, done only after the sprint was completed (carried over); PROJ-26 a sub-task;
    PROJ-27 added, no points, carried over."""
    routes = routes_of(jira)
    for key in list(jira.issues):
        routes.set_sprints_of(key, [])
    older = {"start": "2025-08-04T08:00:00.000+02:00", "end": "2025-08-18T08:00:00.000+02:00", "complete": "2025-08-18T16:00:00.000+02:00"}
    add_sprint(jira, 5, "Sprint 5", "closed", **older)
    reviewed = {"start": "2025-09-01T08:00:00.000+02:00", "end": "2025-09-15T08:00:00.000+02:00", "complete": "2025-09-15T16:00:00.000+02:00"}
    add_sprint(jira, 6, "Sprint 6", "closed", goal="Ship the export", **reviewed)
    add_sprint(jira, 7, "Sprint 7", "active", start="2025-09-15T16:30:00.000+02:00", end="2025-09-29T08:00:00.000+02:00", goal="Stabilise")
    add_sprint(jira, 8, "Sprint 8", "future")
    plan = (
        ("PROJ-21", "Done", 5.0, "Story", "2025-08-20T09:00:00.000+0200", [6]),
        ("PROJ-22", "In Progress", 3.0, "Story", "2025-08-20T09:00:00.000+0200", [6, 7]),
        ("PROJ-23", "Done", 8.0, "Story", "2025-08-21T09:00:00.000+0200", [6]),
        ("PROJ-24", "To Do", 2.0, "Story", "2025-08-21T09:00:00.000+0200", []),
        ("PROJ-25", "Done", 13.0, "Story", "2025-08-22T09:00:00.000+0200", [6]),
        ("PROJ-26", "In Progress", None, "Sub-task", "2025-08-22T09:00:00.000+0200", [6]),
        ("PROJ-27", "In Progress", None, "Story", "2025-08-23T09:00:00.000+0200", [6]),
    )
    for key, state, points, kind, created, sprints in plan:
        add_issue(jira, key, state, points, kind=kind, created=created)
        routes.set_sprints_of(key, sprints)

    def change(key: str, when: str, before: str, after: str) -> None:
        history_entry(routes, key, when, "Sprint", before, after)

    change("PROJ-22", "2025-08-28T10:00:00.000+0200", "", "6")
    change("PROJ-22", "2025-09-15T16:05:00.000+0200", "6", "6, 7")
    change("PROJ-23", "2025-09-05T10:00:00.000+0200", "", "6")
    change("PROJ-24", "2025-08-28T10:00:00.000+0200", "", "6")
    change("PROJ-24", "2025-09-08T10:00:00.000+0200", "6", "")
    change("PROJ-25", "2025-08-28T11:00:00.000+0200", "", "6")
    change("PROJ-27", "2025-09-06T10:00:00.000+0200", "", "6")
    status_change(routes, "PROJ-21", "2025-09-10T10:00:00.000+0200", "In Progress", "Done")
    status_change(routes, "PROJ-23", "2025-09-12T10:00:00.000+0200", "In Progress", "Done")
    status_change(routes, "PROJ-25", "2025-09-20T10:00:00.000+0200", "In Progress", "Done")
    return routes
