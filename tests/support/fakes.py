"""In-memory stand-ins for the ports: the use cases and the command line run without HTTP or files."""
from __future__ import annotations

import dataclasses
import fnmatch
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from jira_dc_cli.application.app import Application
from jira_dc_cli.application.ports import Cache, Clock, ConfigStore, Diagnostics, DocTopic, Docs, Files, JiraGateway
from jira_dc_cli.application.settings import Settings
from jira_dc_cli.domain.errors import JiraCliError, NotFoundError, ValidationError
from jira_dc_cli.domain.model import (
    Comment,
    CreatedIssue,
    FieldInfo,
    Issue,
    IssueLink,
    IssueRef,
    IssueTypeInfo,
    LinkType,
    Project,
    SearchPage,
    Sprint,
    Status,
    Transition,
    User,
)

from .fakes_agile import FakeAgile
from .fakes_attachments import FakeAttachments
from .fakes_collab import FakeCollab
from .fakes_comments import FakeComments
from .fakes_reading import FakeReading
from .fakes_reference import FakeReference
from .fakes_users import FakeUsers
from .fakes_validation import FakeValidation

JANE = User("jdoe", "Jane Doe", "jane.doe@example.com")
STATUS = {
    "To Do": Status("To Do", "new"),
    "In Progress": Status("In Progress", "indeterminate"),
    "Done": Status("Done", "done"),
}


def sample_issue(
    key: str,
    kind: str = "Story",
    state: str = "To Do",
    summary: str = "A summary",
    points: float | None = None,
    epic: str = "",
    **overrides: Any,
) -> Issue:
    values: dict[str, Any] = dict(
        key=key,
        summary=summary,
        issue_type=kind,
        status=STATUS[state],
        priority="High",
        labels=["backend"],
        components=["Demo"],
        fix_versions=["Release 1.0"],
        epic_key=epic,
        story_points=points,
        sprints=[Sprint("Demo Sprint 3", "ACTIVE", 7)],
        description="Ask [~jdoe] or jane.doe@example.com\nSecond line",
        acceptance="1. GIVEN a, WHEN b, THEN c.",
        created="2026-10-01T08:00:00.000+0200",
        updated="2026-10-10T09:00:00.000+0200",
        assignee=JANE,
        reporter=JANE,
        comments=[Comment("10", "2026-10-02T10:00:00.000+0200", "Looks fine, [~jdoe].", JANE)],
        comment_total=1,
        raw={
            "key": key,
            "fields": {
                "summary": summary,
                "assignee": {"name": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com"},
            },
        },
    )
    values.update(overrides)
    return Issue(**values)


def default_issues() -> dict[str, Issue]:
    issues = [
        sample_issue("PROJ-1", "Story", "To Do", "Export the report", 5.0, "PROJ-9"),
        sample_issue("PROJ-2", "Bug", "In Progress", "Fix the thing", 3.0, "PROJ-9"),
        sample_issue("PROJ-3", "Story", "Done", "Finished story", 8.0, "PROJ-8"),
        sample_issue("PROJ-4", "Story", "To Do", "No points yet", None, ""),
        sample_issue("PROJ-8", "Epic", "To Do", "Epic eight", None, ""),
        sample_issue("PROJ-9", "Epic", "In Progress", "Epic nine", None, ""),
    ]
    return {issue.key: issue for issue in issues}


class FakeGateway(FakeAttachments, FakeAgile, FakeComments, FakeUsers, FakeValidation, FakeReading, FakeReference, FakeCollab, JiraGateway):
    """A Jira in memory. Every call is recorded in `calls`; `errors` makes a method fail. The calls of a feature area live in
    the `fakes_<area>.py` mixin of that area, whose state is set up by `_init_<area>` at the end of `__init__`."""

    def __init__(self) -> None:
        self.user = JANE
        self.issues = default_issues()
        self.transitions = {
            "To Do": [Transition("21", "Start", STATUS["In Progress"])],
            "In Progress": [Transition("111", "Reopen", STATUS["To Do"]), Transition("81", "Finish", STATUS["Done"])],
            "Done": [Transition("111", "Reopen", STATUS["To Do"])],
        }
        self.link_types = [
            LinkType("1", "Blocks", "blocks", "is blocked by"),
            LinkType("2", "Has to be done after", "is required by", "requires"),
            LinkType("3", "Pre-requisite", "requires", "is a requisite of"),
        ]
        self.projects = {
            "PROJ": Project("PROJ", "Project PROJ", "software", JANE, "Synthetic", ["Epic", "Story", "Bug"]),
            "DEMO": Project("DEMO", "Project DEMO", "software", JANE),
        }
        self.versions = {"PROJ": ["Release 1.0", "Release 2.0"], "DEMO": []}
        self.admin_projects = {"PROJ"}
        self.issue_types = [IssueTypeInfo("10000", "Epic"), IssueTypeInfo("10200", "Story"), IssueTypeInfo("10201", "Bug")]
        self.fields = [
            FieldInfo("customfield_10001", "Story Points", True),
            FieldInfo("customfield_10002", "Epic Link", True),
            FieldInfo("customfield_10003", "Sprint", True),
            FieldInfo("customfield_10004", "Acceptance criteria (DoD)", True),
            FieldInfo("summary", "Summary", False),
        ]
        self.responses: dict[tuple[str, str], Any] = {}
        self.errors: dict[str, JiraCliError] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.page_cap = 2
        self.next_number = 100
        self.search_handler: Callable[[str], Sequence[Issue]] | None = None  # answers a search itself, to test the JQL that was sent
        for area in (
            self._init_attachments,
            self._init_agile,
            self._init_comments,
            self._init_users,
            self._init_validation,
            self._init_reading,
            self._init_reference,
            self._init_collab,
        ):
            area()

    # -- helpers -----------------------------------------------------------------------------

    def _enter(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        if name in self.errors:
            raise self.errors[name]

    def called(self, name: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == name]

    def _issue(self, key: str) -> Issue:
        if key not in self.issues:
            raise NotFoundError(f"Not found: GET /issue/{key} - Issue Does Not Exist")
        return self.issues[key]

    # -- JiraGateway -------------------------------------------------------------------------

    def current_user(self) -> User:
        self._enter("current_user")
        return self.user

    def server_info(self) -> dict[str, Any]:
        self._enter("server_info")
        return {"version": "11.3.12", "deploymentType": "Server"}

    def list_fields(self, *, refresh: bool = False) -> list[FieldInfo]:
        self._enter("list_fields", refresh)
        return list(self.fields)

    def field_ids(self) -> dict[str, str | None]:
        self._enter("field_ids")
        return {
            "story_points": "customfield_10001",
            "epic_link": "customfield_10002",
            "sprint": "customfield_10003",
            "acceptance": "customfield_10004",
        }

    def get_issue(self, key: str, *, fields: Sequence[str] | None = None) -> Issue:
        self._enter("get_issue", key, None if fields is None else tuple(fields))
        return self._issue(key)

    def search(self, jql: str, *, fields: Sequence[str], start_at: int = 0, max_results: int = 50) -> SearchPage:
        self._enter("search", jql, tuple(fields), start_at, max_results)
        if self.search_handler is not None:
            found = list(self.search_handler(jql))
            size = min(max_results, self.page_cap)
            chosen = found[start_at : start_at + size]
            raw = [{"key": issue.key, "fields": (issue.raw or {}).get("fields", {})} for issue in chosen]
            return SearchPage(chosen, len(found), start_at, size, raw)
        keys = sorted(self.issues, key=lambda key: int(key.split("-")[1]))
        if "issuetype = Epic" in jql:
            keys = [key for key in keys if self.issues[key].issue_type == "Epic"]
        size = min(max_results, self.page_cap)
        chosen = [self.issues[key] for key in keys[start_at : start_at + size]]
        raw = [{"key": issue.key, "fields": (issue.raw or {}).get("fields", {})} for issue in chosen]
        return SearchPage(chosen, len(keys), start_at, size, raw)

    def list_transitions(self, key: str) -> list[Transition]:
        self._enter("list_transitions", key)
        return list(self.transitions[self._issue(key).status.name])  # type: ignore[union-attr]

    def transition_issue(self, key: str, payload: dict[str, Any]) -> None:
        self._enter("transition_issue", key, payload)
        issue = self._issue(key)
        transition_id = payload["transition"]["id"]
        chosen = next(item for item in self.transitions[issue.status.name] if item.id == transition_id)  # type: ignore[union-attr]
        self.issues[key] = dataclasses.replace(issue, status=chosen.to_status)

    def list_comments(self, key: str) -> list[Comment]:
        self._enter("list_comments", key)
        return list(self._issue(key).comments)

    def add_comment(self, key: str, payload: dict[str, Any]) -> Comment:
        self._enter("add_comment", key, payload)
        issue = self._issue(key)
        comment = Comment("11", "2026-10-15T10:00:00.000+0200", payload["body"], JANE)
        self.issues[key] = dataclasses.replace(issue, comments=[*issue.comments, comment], comment_total=issue.comment_total + 1)
        return comment

    def list_link_types(self) -> list[LinkType]:
        self._enter("list_link_types")
        return list(self.link_types)

    def create_link(self, payload: dict[str, Any]) -> None:
        self._enter("create_link", payload)
        kind = next(item for item in self.link_types if item.name == payload["type"]["name"])
        outward_key, inward_key = payload["outwardIssue"]["key"], payload["inwardIssue"]["key"]
        outward, inward = self._issue(outward_key), self._issue(inward_key)

        def ref(issue: Issue) -> IssueRef:
            return IssueRef(issue.key, issue.summary, issue.status.name if issue.status else "")

        self.issues[outward_key] = dataclasses.replace(outward, links=[*outward.links, IssueLink(kind.name, kind.outward, "outward", ref(inward))])
        self.issues[inward_key] = dataclasses.replace(inward, links=[*inward.links, IssueLink(kind.name, kind.inward, "inward", ref(outward))])

    def create_issue(self, payload: dict[str, Any]) -> CreatedIssue:
        self._enter("create_issue", payload)
        fields = payload["fields"]
        self.next_number += 1
        key = f"{fields['project']['key']}-{self.next_number}"
        kind = next(item.name for item in self.issue_types if item.id == fields["issuetype"]["id"])
        self.issues[key] = sample_issue(key, kind, "To Do", fields["summary"], comments=[], comment_total=0)
        return CreatedIssue(str(self.next_number), key, f"https://jira.example.com/browse/{key}")

    def update_issue(self, key: str, payload: dict[str, Any]) -> None:
        self._enter("update_issue", key, payload)
        issue = self._issue(key)
        fields = payload.get("fields") or {}
        changes: dict[str, Any] = {}
        if "summary" in fields:
            changes["summary"] = fields["summary"] or issue.summary
        if "description" in fields:
            changes["description"] = fields["description"] or ""
        if "labels" in fields:
            changes["labels"] = list(fields["labels"])
        if "priority" in fields:
            changes["priority"] = fields["priority"]["name"]
        if "components" in fields:
            changes["components"] = [item["name"] for item in fields["components"]]
        if "fixVersions" in fields:
            changes["fix_versions"] = [item["name"] for item in fields["fixVersions"]]
        if "customfield_10001" in fields:
            changes["story_points"] = fields["customfield_10001"]
        if "customfield_10004" in fields:
            changes["acceptance"] = fields["customfield_10004"] or ""
        self.issues[key] = dataclasses.replace(issue, updated="2026-10-20T09:00:00.000+0200", **changes)

    def list_projects(self) -> list[Project]:
        self._enter("list_projects")
        return list(self.projects.values())

    def get_project(self, key: str) -> Project:
        self._enter("get_project", key)
        if key not in self.projects:
            raise NotFoundError(f"Not found: GET /project/{key}")
        return self.projects[key]

    def list_versions(self, project_key: str) -> list[str]:
        self._enter("list_versions", project_key)
        return list(self.versions.get(project_key, []))

    def has_permission(self, project_key: str, permission: str, *, timeout: float | None = None, retries: int | None = None) -> bool:
        self._enter("has_permission", project_key, permission, timeout, retries)
        return project_key in self.admin_projects

    def list_issue_types(self, project_key: str, *, refresh: bool = False) -> list[IssueTypeInfo]:
        self._enter("list_issue_types", project_key, refresh)
        return list(self.issue_types)

    def create_metadata(self, project_key: str, issue_type_id: str = "") -> dict[str, Any]:
        self._enter("create_metadata", project_key, issue_type_id)
        return {"values": [{"id": item.id, "name": item.name} for item in self.issue_types]}

    def request(self, method: str, path: str, body: Any = None) -> Any:
        self._enter("request", method, path, body)
        return self.responses.get((method, path), {"ok": True, "user": {"displayName": "Jane Doe", "name": "jdoe"}})


class MemoryFiles(Files):
    """Files in memory: text in `files`, binary content in `blobs` (a file is in one of them)."""

    def __init__(self, files: Mapping[str, str] | None = None) -> None:
        self.files = dict(files or {})
        self.blobs: dict[str, bytes] = {}

    def _blobs(self) -> dict[str, bytes]:
        return self.__dict__.setdefault("blobs", {})  # a test subclass may not call __init__

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        if path not in self.files and path in self._blobs():
            try:
                return self._blobs()[path].decode(encoding)
            except UnicodeDecodeError as error:
                raise ValidationError(f"{path} is not valid {encoding} text.") from error
        if path not in self.files:
            raise ValidationError(f"Cannot read {path}: No such file")
        text = self.files[path]
        return text.lstrip("\ufeff") if encoding == "utf-8-sig" else text

    def write_text(self, path: str, text: str) -> None:
        self._blobs().pop(path, None)
        self.files[path] = text

    def read_bytes(self, path: str) -> bytes:
        if path in self._blobs():
            return self._blobs()[path]
        if path in self.files:
            return self.files[path].encode("utf-8")
        raise ValidationError(f"Cannot read {path}: No such file")

    def write_bytes(self, path: str, data: bytes) -> None:
        self.files.pop(path, None)
        self._blobs()[path] = bytes(data)

    def file_size(self, path: str) -> int:
        return len(self.read_bytes(path))

    def append_text(self, path: str, text: str) -> None:
        self.files[path] = self.files.get(path, "") + text

    def exists(self, path: str) -> bool:
        return path in self.files or path in self._blobs() or self.is_dir(path)

    def is_dir(self, path: str) -> bool:
        return any(name.startswith(path.rstrip("/") + "/") for name in [*self.files, *self._blobs()])

    def list_matching(self, directory: str, pattern: str) -> list[str]:
        prefix = directory.rstrip("/") + "/"
        names = [name for name in [*self.files, *self._blobs()] if name.startswith(prefix) and "/" not in name[len(prefix) :]]
        return sorted(name for name in names if fnmatch.fnmatch(name[len(prefix) :], pattern))

    def join(self, *parts: str) -> str:
        return "/".join(part.strip("/") if index else part.rstrip("/") for index, part in enumerate(parts))

    def name(self, path: str) -> str:
        return path.rsplit("/", 1)[-1]


class MemoryCache(Cache):
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, namespace: str, key: str, max_age: float) -> str | None:
        return self.values.get((namespace, key))

    def put(self, namespace: str, key: str, value: str) -> None:
        self.values[(namespace, key)] = value


class MemoryConfigStore(ConfigStore):
    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values = dict(values or {})

    @property
    def path(self) -> str:
        return "memory:/config"

    def load(self) -> dict[str, str]:
        return dict(self.values)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def unset(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class FixedClock(Clock):
    def now(self) -> datetime:
        return datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


class RecordingDiagnostics(Diagnostics):
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.debugs: list[str] = []

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        self.debugs.append(message)


class FakeDocs(Docs):
    def __init__(self) -> None:
        self.pages = {"daily-workflow": "# Daily workflow\n\nText.\n", "troubleshooting": "# Troubleshooting\n"}

    def topics(self) -> list[DocTopic]:
        return [DocTopic(name, text.splitlines()[0][2:]) for name, text in sorted(self.pages.items())]

    def topic(self, name: str) -> str:
        if name not in self.pages:
            raise NotFoundError(f"No guide named '{name}'.")
        return self.pages[name]

    def readme(self) -> str:
        return "# README\n"

    def changelog(self) -> str:
        return "# Changelog\n\n## 1.0.0\n"

    def skill(self) -> str:
        return "---\nname: jira-dc-cli\ndescription: test skill\n---\n# Skill\n"


DEFAULT_SETTINGS = Settings(base_url="https://jira.example.com", pat="secret-token", project="PROJ")


def build_app(
    *,
    gateway: FakeGateway | None = None,
    files: MemoryFiles | None = None,
    store: MemoryConfigStore | None = None,
    settings: Settings | None = None,
    docs: Docs | None = None,
    diagnostics: Diagnostics | None = None,
    environ: Mapping[str, str] | None = None,
    cache: Cache | None = None,
    runtime: Mapping[str, str] | None = None,
) -> Application:
    return Application.create(
        settings=settings or DEFAULT_SETTINGS,
        gateway=gateway or FakeGateway(),
        store=store or MemoryConfigStore(),
        files=files or MemoryFiles(),
        clock=FixedClock(),
        docs=docs or FakeDocs(),
        diagnostics=diagnostics or RecordingDiagnostics(),
        environ=environ or {},
        runtime=runtime or {"version": "1.0.0", "python": "3.13.0"},
        cache=cache,
    )
