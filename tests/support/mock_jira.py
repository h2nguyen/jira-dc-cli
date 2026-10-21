"""A small Jira Data Center in a thread, with synthetic data, for the tests. Standard library only.

    with MockJira() as jira:
        settings = Settings(base_url=jira.base_url, pat=jira.token)
        ...
        jira.requests          # everything the CLI sent
        jira.fail_next("/issue/PROJ-1", 503, times=2)

It keeps its state (created issues, comments, links, transitions), speaks HTTP/1.1 with keep-alive
and honours the `fields` parameter like Jira does. The search understands only the JQL the CLI builds.
"""
from __future__ import annotations

import copy
import gzip
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

POINTS, EPIC, SPRINT, ACCEPT = "customfield_10001", "customfield_10002", "customfield_10003", "customfield_10004"
FIELD_NAMES = {POINTS: "Story Points", EPIC: "Epic Link", SPRINT: "Sprint", ACCEPT: "Acceptance criteria (DoD)"}
USER = {"name": "jdoe", "key": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com", "self": "x"}
SPRINT_TEXT = "com.atlassian.greenhopper.service.sprint.Sprint@1[id=7,rapidViewId=3,state=ACTIVE,name=Demo Sprint 3,goal=]"
CLOSED_SPRINT_TEXT = "com.atlassian.greenhopper.service.sprint.Sprint@2[id=6,rapidViewId=3,state=CLOSED,name=Demo Sprint 2,goal=]"

STATUSES = {
    "To Do": {"name": "To Do", "statusCategory": {"key": "new"}},
    "In Progress": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
    "Done": {"name": "Done", "statusCategory": {"key": "done"}},
}
TRANSITIONS = {
    "To Do": [{"id": "21", "name": "Start", "to": STATUSES["In Progress"]}],
    "In Progress": [
        {"id": "111", "name": "Reopen", "to": STATUSES["To Do"]},
        {"id": "81", "name": "Finish", "to": STATUSES["Done"]},
    ],
    "Done": [{"id": "111", "name": "Reopen", "to": STATUSES["To Do"]}],
}
LINK_TYPES = [
    {"id": "1", "name": "Blocks", "outward": "blocks", "inward": "is blocked by"},
    {"id": "2", "name": "Has to be done after", "outward": "is required by", "inward": "requires"},
    {"id": "3", "name": "Pre-requisite", "outward": "requires", "inward": "is a requisite of"},
]
ISSUE_TYPES = [{"id": "10000", "name": "Epic"}, {"id": "10200", "name": "Story"}, {"id": "10201", "name": "Bug"}]


def make_issue(key: str, kind: str, state: str, summary: str, points: float | None, epic: str | None, **fields: Any) -> dict[str, Any]:
    values = {
        "summary": summary,
        "issuetype": {"name": kind},
        "status": STATUSES[state],
        "priority": {"name": "High"},
        "assignee": USER,
        "reporter": USER,
        "labels": ["backend"],
        "components": [{"name": "Demo"}],
        "fixVersions": [{"name": "Release 1.0"}],
        "description": "Ask [~jdoe] or jane.doe@example.com\nSecond line",
        POINTS: points,
        EPIC: epic,
        SPRINT: [SPRINT_TEXT] if state != "Done" else [CLOSED_SPRINT_TEXT, SPRINT_TEXT],
        ACCEPT: "1. GIVEN a, WHEN b, THEN c.",
        "created": "2026-10-01T08:00:00.000+0200",
        "updated": "2026-10-10T09:00:00.000+0200",
        "issuelinks": [],
        "subtasks": [],
        "comment": {
            "total": 1,
            "comments": [{"id": "10", "author": USER, "created": "2026-10-02T10:00:00.000+0200", "body": "Looks fine, [~jdoe]."}],
        },
    }
    values.update(fields)
    return {"id": key.split("-")[1], "key": key, "fields": values}


def default_issues() -> dict[str, dict[str, Any]]:
    issues = [
        make_issue("PROJ-1", "Story", "To Do", "Export the report: Ünïcode & more", 5.0, "PROJ-9"),
        make_issue("PROJ-2", "Bug", "In Progress", "Fix the thing", 3.0, "PROJ-9"),
        make_issue("PROJ-3", "Story", "Done", "Finished story", 8.0, "PROJ-8"),
        make_issue("PROJ-4", "Story", "To Do", "No points yet", None, None),
        make_issue("PROJ-8", "Epic", "To Do", "Epic eight", None, None),
        make_issue("PROJ-9", "Epic", "In Progress", "Epic nine", None, None),
    ]
    return {issue["key"]: issue for issue in issues}


@dataclass
class Recorded:
    method: str
    path: str  # below the context path
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: Any = None

    @property
    def target(self) -> str:
        return f"{self.method} {self.path}"


# -- a small JQL: the part of it that the CLI sends, evaluated the way Jira answers ------------------


def _split_top(text: str, keyword: str) -> list[str]:
    """The text cut at ` KEYWORD ` outside quotes and brackets."""
    parts: list[str] = []
    depth, quote, escaped, start, index = 0, "", False, 0, 0
    upper = text.upper()
    token = f" {keyword} "
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and upper.startswith(token, index):
            parts.append(text[start:index].strip())
            index += len(token)
            start = index
            continue
        index += 1
    parts.append(text[start:].strip())
    return parts


def _split_order(jql: str) -> tuple[str, str]:
    quote, escaped = "", False
    for index, char in enumerate(jql):
        if escaped:
            escaped = False
        elif quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif re.match(r"order\s+by\s", jql[index:], re.IGNORECASE) and (index == 0 or jql[index - 1] == " "):
            return jql[:index].strip(), re.sub(r"^order\s+by\s+", "", jql[index:], flags=re.IGNORECASE).strip()
    return jql.strip(), ""


def _unwrap(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth, quote, escaped = 0, "", False
        closes_at_end = True
        for index, char in enumerate(text):
            if escaped:
                escaped = False
            elif quote:
                if char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index < len(text) - 1:
                    closes_at_end = False
                    break
        if not closes_at_end:
            break
        text = text[1:-1].strip()
    return text


def _string(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def _values(value: str) -> list[str]:
    inner = value.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    return [_string(item) for item in re.findall(r'"(?:\\.|[^"\\])*"|[^,\s][^,]*', inner)]


_WORD = re.compile(r"\w+")


def _stem(word: str) -> str:
    word = word.lower()
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _haystack(jira: "MockJira", key: str) -> str:
    fields = jira.issues[key]["fields"]
    comments = [item["body"] for item in (fields.get("comment") or {}).get("comments", [])] + [item["body"] for item in jira.comments.get(key, [])]
    return "\n".join(str(part) for part in (fields.get("summary"), fields.get("description"), fields.get(ACCEPT), *comments) if part)


def text_refused(text: str) -> str:
    """Jira's reason for refusing a `text ~` string, as measured on Jira 11 Data Center; empty when it accepts it."""
    value = text.strip()
    if not value:
        return "The field 'text' does not support searching for an empty string."
    if re.search(r"[\[\]{}]", value) or value.count("(") != value.count(")") or value.startswith("~") or re.search(r"[\^!]$", value) or value in ("-", "+"):
        return f"Unable to parse the text '{value}' for field 'text'."
    return ""


def _text_terms(condition: str) -> list[str]:
    return [_string(match) for match in re.findall(r'text ~ ("(?:\\.|[^"\\])*")', condition)]


def _term_count(haystack: str, term: str) -> int:
    """How often a word, a phrase or a wildcard of the query occurs in the text (0: it does not)."""
    if len(term) > 1 and term.startswith('"') and term.endswith('"'):
        return " ".join(haystack.lower().split()).count(" ".join(term[1:-1].lower().split()))
    tokens = _WORD.findall(haystack.lower())
    total = 0
    for word in term.split():
        if "*" in word or "?" in word:
            pattern = re.compile(re.escape(word.lower()).replace(r"\*", r"\w*").replace(r"\?", r"\w") + r"$")
            total += sum(1 for token in tokens if pattern.match(token)) or 0
        else:
            total += sum(1 for token in tokens if _stem(token) == _stem(word))
        if total == 0:
            return 0
    return max(total, 1)


def _words_all_present(haystack: str, term: str) -> bool:
    if len(term) > 1 and term.startswith('"') and term.endswith('"'):
        return _term_count(haystack, term) > 0
    return all(_term_count(haystack, word) > 0 for word in term.split())


def _score(jira: "MockJira", key: str, terms: list[str]) -> int:
    haystack = _haystack(jira, key)
    return sum(_term_count(haystack, word) for term in terms for word in ([term] if term.startswith('"') else term.split()))


def _holds(jira: "MockJira", key: str, clause: str) -> bool:
    clause = _unwrap(clause)
    alternatives = _split_top(clause, "OR")
    if len(alternatives) > 1:
        return any(_holds(jira, key, part) for part in alternatives)
    conjuncts = _split_top(clause, "AND")
    if len(conjuncts) > 1:
        return all(_holds(jira, key, part) for part in conjuncts)
    return _atom(jira, key, clause)


def _atom(jira: "MockJira", key: str, clause: str) -> bool:
    found = re.fullmatch(r'("[^"]+"|cf\[\d+\]|\w+)\s*(>=|<=|!=|=|~|in|is)\s*(.+)', clause.strip(), re.IGNORECASE | re.DOTALL)
    if not found:
        return True
    field, operator, raw = found.group(1).strip('"'), found.group(2).lower(), found.group(3).strip()
    fields = jira.issues[key]["fields"]
    wanted = _values(raw) if operator == "in" else [_string(raw)]
    name = field.lower()
    if name == "project":
        return key.startswith(wanted[0] + "-")
    if name == "key":
        return key in wanted
    if name == "text" and operator == "~":
        return _words_all_present(_haystack(jira, key), wanted[0])
    if name in ("summary", "description") and operator == "~":
        return _words_all_present(str(fields.get(name) or ""), wanted[0])
    if name == "comment" and operator == "~":
        bodies = [item["body"] for item in (fields.get("comment") or {}).get("comments", [])] + [item["body"] for item in jira.comments.get(key, [])]
        return _words_all_present("\n".join(bodies), wanted[0])
    if name == "issuetype":
        return fields["issuetype"]["name"] in wanted
    if name == "status":
        return fields["status"]["name"] in wanted
    if name == "priority":
        return (fields.get("priority") or {}).get("name") in wanted
    if name == "labels":
        return any(label in wanted for label in fields.get("labels") or [])
    if name == "fixversion":
        return any(version["name"] in wanted for version in fields.get("fixVersions") or [])
    if name == "sprint":
        sprints = fields.get(SPRINT) or []
        if raw.strip() == "openSprints()":
            return any("state=ACTIVE" in text for text in sprints)
        return any(f"name={wanted[0]}," in text for text in sprints)
    if name in ("epic link", f"cf[{EPIC.removeprefix('customfield_')}]"):
        return fields.get(EPIC) in wanted
    if name == "parent":
        return (fields.get("parent") or {}).get("key") in wanted
    if name == "assignee" and raw.strip() == "currentUser()":
        return fields["status"]["name"] != "Done"
    if name == "updated" and operator == ">=":
        stamp = datetime.strptime(str(fields["updated"])[:19], "%Y-%m-%dT%H:%M:%S")
        relative = re.fullmatch(r"-(\d+)([mhdw])", raw.strip())
        if relative:
            unit = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[relative.group(2)]
            return stamp >= datetime.strptime(jira.now, "%Y-%m-%dT%H:%M:%S") - timedelta(**{unit: int(relative.group(1))})
        text = wanted[0]
        return str(fields["updated"])[: len(text)] >= text
    return True


@dataclass
class _Failure:
    prefix: str
    status: int
    times: int
    body: Any = None
    method: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.connections: set[Any] = set()

    def get_request(self) -> Any:
        connection, address = super().get_request()
        self.connections.add(connection)
        return connection, address


class MockJira:
    def __init__(self, *, context_path: str = "/jira", page_cap: int = 2, token: str = "test-token") -> None:
        self.context_path = context_path
        self.page_cap = page_cap  # a server may return fewer results than asked for; the CLI must keep paging
        self.token = token
        self.gzip = True
        self.broken_gzip = False  # answer with a gzip header and a body that is not gzip
        self.issues = default_issues()
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self.permissions = {"PROJ": True, "DEMO": False}
        self.versions = {"PROJ": ["Release 1.0", "Release 2.0"], "DEMO": []}
        self.requests: list[Recorded] = []
        self._failures: list[_Failure] = []
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._next_number = 100

    # -- lifecycle ------------------------------------------------------------------------------

    def __enter__(self) -> "MockJira":
        mock = self

        class Handler(_Handler):
            jira = mock

        self._server = _Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        for connection in list(self._server.connections):
            try:
                connection.close()
            except OSError:
                pass

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}{self.context_path}"

    # -- controls and inspection ----------------------------------------------------------------

    def fail_next(self, prefix: str, status: int, *, times: int = 1, body: Any = None, method: str = "", headers: dict[str, str] | None = None) -> None:
        self._failures.append(_Failure(prefix, status, times, body, method, headers or {}))

    def sent(self, method: str, prefix: str = "") -> list[Recorded]:
        return [item for item in self.requests if item.method == method and item.path.startswith(prefix)]

    def reset_requests(self) -> None:
        self.requests.clear()

    # -- behaviour (called by the handler) ------------------------------------------------------

    def take_failure(self, method: str, path: str) -> _Failure | None:
        with self._lock:
            for failure in self._failures:
                if path.startswith(failure.prefix) and (not failure.method or failure.method == method):
                    failure.times -= 1
                    if failure.times <= 0:
                        self._failures.remove(failure)
                    return failure
        return None

    def new_key(self, project: str) -> str:
        with self._lock:
            self._next_number += 1
            return f"{project}-{self._next_number}"

    def select(self, fields: dict[str, Any], query: dict[str, list[str]]) -> dict[str, Any]:
        wanted = [name for item in query.get("fields", []) for name in item.split(",") if name]
        return {key: value for key, value in fields.items() if key in wanted} if wanted else fields

    def matching(self, jql: str) -> list[str]:
        """The keys a JQL query selects, in the order Jira would answer: relevance for a text search, else by the ORDER BY."""
        condition, order = _split_order(jql)
        keys = [key for key in self.issues if not condition.strip() or _holds(self, key, condition)]
        keys = sorted(keys, key=lambda key: int(key.split("-")[1]))
        if order:
            field, _, direction = order.partition(" ")
            reverse = direction.strip().upper() == "DESC"
            if field.lower() == "created":
                return sorted(keys, key=lambda key: (self.issues[key]["fields"]["created"], int(key.split("-")[1])), reverse=reverse)
            if field.lower() == "updated":
                return sorted(keys, key=lambda key: self.issues[key]["fields"]["updated"], reverse=reverse)
            return sorted(keys, key=lambda key: int(key.split("-")[1]), reverse=reverse)
        terms = _text_terms(condition)
        if terms:
            return sorted(keys, key=lambda key: -_score(self, key, terms))
        return keys

    now = "2026-10-12T00:00:00"  # what "7d ago" is counted from


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    jira: MockJira

    def log_message(self, *args: object) -> None:
        pass

    def reply(self, status: int, payload: Any = None, headers: dict[str, str] | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        broken = self.jira.broken_gzip and payload is not None
        compress = self.jira.gzip and len(body) > 200 and "gzip" in self.headers.get("Accept-Encoding", "")
        if broken:
            body = b"this is not gzip at all"
        elif compress:
            body = gzip.compress(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        if compress or broken:
            self.send_header("Content-Encoding", "gzip")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return raw

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")

    def do_PUT(self) -> None:
        self.route("PUT")

    def do_DELETE(self) -> None:
        self.route("DELETE")

    def route(self, method: str) -> None:
        jira = self.jira
        body = self.read_body()
        url = urlparse(self.path)
        full_path = unquote(url.path)
        path = full_path[len(jira.context_path):] if full_path.startswith(jira.context_path) else full_path
        query = parse_qs(url.query)
        jira.requests.append(Recorded(method, path, query, dict(self.headers), body))
        if self.headers.get("Authorization", "") != f"Bearer {jira.token}":
            return self.reply(401, {"errorMessages": ["You are not authenticated."]})
        failure = jira.take_failure(method, path)
        if failure is not None:
            return self.reply(failure.status, failure.body if failure.body is not None else {"errorMessages": [f"injected {failure.status}"]}, failure.headers)
        if not path.startswith("/rest/"):
            return self.reply(404, {"errorMessages": ["not found"]})
        if path.startswith("/rest/agile/1.0"):
            return self.agile(method, path.removeprefix("/rest/agile/1.0"))
        self.api(method, path.removeprefix("/rest/api/2"), query, body)

    def agile(self, method: str, path: str) -> None:
        if method == "GET" and path == "/board":
            return self.reply(200, {"values": [{"id": 1200, "name": "Demo board", "type": "scrum"}]})
        self.reply(404, {"errorMessages": ["unknown agile route"]})

    def api(self, method: str, path: str, query: dict[str, list[str]], body: Any) -> None:
        jira = self.jira
        if method == "GET" and path == "/myself":
            return self.reply(200, USER)
        if method == "GET" and path == "/serverInfo":
            return self.reply(200, {"version": "9.12.0", "deploymentType": "Server"})
        if method == "GET" and path == "/field":
            fields = [{"id": fid, "name": name, "custom": True} for fid, name in FIELD_NAMES.items()]
            return self.reply(200, fields + [{"id": "summary", "name": "Summary", "custom": False}])
        if method == "GET" and path == "/issueLinkType":
            return self.reply(200, {"issueLinkTypes": LINK_TYPES})
        if method == "POST" and path == "/issueLink":
            return self.create_link(body)
        if method == "GET" and path == "/search":
            return self.search(query)
        if method == "POST" and path == "/issue":
            return self.create_issue(body)
        if method == "GET" and path == "/project":
            return self.reply(200, [{"key": key, "name": f"Project {key}", "projectTypeKey": "software", "lead": USER} for key in jira.versions])
        if method == "GET" and path == "/mypermissions":
            project = query.get("projectKey", [""])[0]
            permission = query.get("permissions", [""])[0]
            return self.reply(200, {"permissions": {permission: {"havePermission": jira.permissions.get(project, False)}}})
        match = re.fullmatch(r"/project/([A-Za-z]+)(/versions)?", path)
        if match and method == "GET":
            key = match.group(1)
            if key not in jira.versions:
                return self.reply(404, {"errorMessages": ["No project could be found with key '%s'." % key]})
            if match.group(2):
                return self.reply(200, [{"name": name} for name in jira.versions[key]])
            return self.reply(200, {"key": key, "name": f"Project {key}", "projectTypeKey": "software", "lead": USER,
                                    "description": "Synthetic project", "issueTypes": ISSUE_TYPES})
        match = re.fullmatch(r"/issue/createmeta/([A-Za-z]+)/issuetypes(?:/(\d+))?", path)
        if match and method == "GET":
            if match.group(1) not in jira.versions:
                return self.reply(404, {"errorMessages": ["No project could be found."]})
            if match.group(2):
                return self.reply(200, {"values": [{"fieldId": "summary", "required": True}], "total": 1})
            return self.reply(200, {"values": ISSUE_TYPES, "total": len(ISSUE_TYPES)})
        match = re.fullmatch(r"/issue/([A-Za-z]+-\d+)(/transitions|/comment)?", path)
        if not match:
            return self.reply(404, {"errorMessages": [f"unknown route {path}"]})
        self.issue(method, match.group(1), match.group(2), query, body)

    def issue(self, method: str, key: str, sub: str | None, query: dict[str, list[str]], body: Any) -> None:
        jira = self.jira
        if key not in jira.issues:
            return self.reply(404, {"errorMessages": [f"Issue Does Not Exist {key}"], "errors": {}})
        issue = jira.issues[key]
        fields = issue["fields"]
        if method == "GET" and sub is None:
            shown = copy.deepcopy(self.jira.select(fields, query))
            if "comment" in shown:
                shown["comment"]["comments"] += jira.comments.get(key, [])
                shown["comment"]["total"] = len(shown["comment"]["comments"])
            return self.reply(200, {"id": issue["id"], "key": key, "fields": shown})
        if method == "GET" and sub == "/transitions":
            return self.reply(200, {"transitions": TRANSITIONS[fields["status"]["name"]]})
        if method == "POST" and sub == "/transitions":
            chosen = body["transition"]["id"]
            options = {item["id"]: item for item in TRANSITIONS[fields["status"]["name"]]}
            if chosen not in options:
                return self.reply(400, {"errorMessages": [f"Transition {chosen} is not valid"], "errors": {}})
            fields["status"] = options[chosen]["to"]
            for item in (body.get("update") or {}).get("comment", []):
                jira.comments.setdefault(key, []).append(self.comment(item["add"]["body"]))
            return self.reply(204)
        if method == "GET" and sub == "/comment":
            comments = fields["comment"]["comments"] + jira.comments.get(key, [])
            return self.reply(200, {"total": len(comments), "comments": comments})
        if method == "POST" and sub == "/comment":
            if "REJECT" in str(body):
                return self.reply(400, {"errorMessages": ["Comment rejected by the mock"], "errors": {}})
            jira.comments.setdefault(key, []).append(self.comment(body["body"]))
            return self.reply(201, jira.comments[key][-1])
        if method == "PUT" and sub is None:
            return self.update(issue, body)
        self.reply(405, {"errorMessages": ["method not allowed"]})

    @staticmethod
    def comment(text: str) -> dict[str, Any]:
        return {"id": "11", "author": USER, "created": "2026-10-15T10:00:00.000+0200", "body": text}

    def update(self, issue: dict[str, Any], body: Any) -> None:
        if not isinstance(body, dict) or ("fields" not in body and "update" not in body):
            return self.reply(400, {"errorMessages": ["bad payload"], "errors": {}})
        fields = issue["fields"]
        for name, value in (body.get("fields") or {}).items():
            if name == "summary" and not value:
                return self.reply(400, {"errorMessages": [], "errors": {"summary": "You must specify a summary of the issue."}})
            fields[name] = value
        for name, operations in (body.get("update") or {}).items():
            for operation in operations:
                if "add" in operation:
                    fields[name] = [*fields.get(name, []), operation["add"]]
        fields["updated"] = "2026-10-20T09:00:00.000+0200"
        self.reply(204)

    def create_link(self, body: Any) -> None:
        jira = self.jira
        kind = next((item for item in LINK_TYPES if item["name"] == body["type"]["name"]), None)
        outward, inward = body["outwardIssue"]["key"], body["inwardIssue"]["key"]
        if kind is None or outward not in jira.issues or inward not in jira.issues:
            return self.reply(404, {"errorMessages": ["link target not found"], "errors": {}})

        def brief(key: str) -> dict[str, Any]:
            data = jira.issues[key]["fields"]
            return {"key": key, "fields": {"summary": data["summary"], "status": data["status"]}}

        jira.issues[outward]["fields"]["issuelinks"].append({"type": kind, "outwardIssue": brief(inward)})
        jira.issues[inward]["fields"]["issuelinks"].append({"type": kind, "inwardIssue": brief(outward)})
        self.reply(201)

    def search(self, query: dict[str, list[str]]) -> None:
        jira = self.jira
        jql = query.get("jql", [""])[0]
        if "BROKEN" in jql:
            return self.reply(400, {"errorMessages": ["Error in the JQL Query: unexpected token BROKEN."], "errors": {}})
        for text in re.findall(r'text ~ ("(?:\\.|[^"\\])*")', jql):
            reason = text_refused(_string(text))
            if reason:
                return self.reply(400, {"errorMessages": [reason], "errors": {}})
        keys = jira.matching(jql)
        start = int(query.get("startAt", ["0"])[0])
        size = min(int(query.get("maxResults", ["50"])[0]), jira.page_cap)
        page = [{"id": jira.issues[key]["id"], "key": key, "fields": jira.select(jira.issues[key]["fields"], query)} for key in keys[start : start + size]]
        self.reply(200, {"startAt": start, "maxResults": size, "total": len(keys), "issues": page})

    def create_issue(self, body: Any) -> None:
        jira = self.jira
        fields = (body or {}).get("fields") or {}
        project = (fields.get("project") or {}).get("key", "")
        if project not in jira.versions:
            return self.reply(400, {"errorMessages": [], "errors": {"project": "valid project is required"}})
        kind = next((item for item in ISSUE_TYPES if item["id"] == (fields.get("issuetype") or {}).get("id")), None)
        if kind is None:
            return self.reply(400, {"errorMessages": [], "errors": {"issuetype": "issue type is required"}})
        if not fields.get("summary"):
            return self.reply(400, {"errorMessages": [], "errors": {"summary": "You must specify a summary of the issue."}})
        key = jira.new_key(project)
        stored = make_issue(key, kind["name"], "To Do", fields["summary"], None, None)
        stored["fields"]["comment"] = {"total": 0, "comments": []}
        stored["fields"].update({name: value for name, value in fields.items() if name not in ("project", "issuetype", "reporter")})
        for name in ("labels",):
            stored["fields"].setdefault(name, [])
        jira.issues[key] = stored
        self.reply(201, {"id": stored["id"], "key": key, "self": f"{jira.base_url}/rest/api/2/issue/{stored['id']}"})
