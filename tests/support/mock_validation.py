"""The validation routes of the mock Jira: the edit metadata of an issue, the create metadata of an issue type, and the transitions of an
issue with the fields of their screens (`expand=transitions.fields`).

By default a project asks for nothing but the summary, offers a few priorities, components, fix versions and resolutions, and no
transition has a screen. A test makes the project stricter with `routes(jira).required_to_create` (issue type id -> extra field
metadata) and `routes(jira).screens` (transition id -> the fields of its screen, by field id).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
COMPONENTS = {"PROJ": ("Demo", "Backend"), "DEMO": ()}
RESOLUTIONS = ("Fixed", "Won't Fix", "Duplicate")
EDIT_META = re.compile(r"^/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/editmeta$")
CREATE_META = re.compile(r"^/rest/api/2/issue/createmeta/([A-Za-z][A-Za-z0-9_]*)/issuetypes/(\d+)$")
TRANSITIONS = re.compile(r"^/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/transitions$")


def named(*texts: str) -> list[dict[str, str]]:
    """Allowed values as Jira lists them: an id (the position) and a name."""
    return [{"id": str(number), "name": text} for number, text in enumerate(texts, 1)]


def field_meta(
    field_id: str, name: str, *, required: bool = False, default: bool = False, allowed: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "required": required,
        "name": name,
        "key": field_id,
        "hasDefaultValue": default,
        "operations": ["set"],
        "schema": {"type": "string"},
    }
    if allowed is not None:
        meta["allowedValues"] = allowed
    return meta


def resolution_field(*, required: bool = True) -> dict[str, Any]:
    """The resolution as the screen of a transition to Done holds it."""
    return field_meta("resolution", "Resolution", required=required, allowed=named(*RESOLUTIONS))


class ValidationRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.required_to_create: dict[str, dict[str, dict[str, Any]]] = {}
        self.screens: dict[str, dict[str, dict[str, Any]]] = {}
        self.components = {project: tuple(names) for project, names in COMPONENTS.items()}  # a test adds one: `components["PROJ"] += ("New",)`
        self.page_size: int | None = None  # the most fields that the create metadata answers per page (a real server caps it); None: no cap

    def _fields(self, project: str) -> dict[str, dict[str, Any]]:
        """What the screens of a project hold: the summary, the priority, the components, the fix versions, labels and description."""
        versions = named(*self.jira.versions.get(project, []))
        return {
            "summary": field_meta("summary", "Summary", required=True),
            "description": field_meta("description", "Description"),
            "priority": field_meta("priority", "Priority", default=True, allowed=named(*PRIORITIES)),
            "components": field_meta("components", "Component/s", allowed=named(*self.components.get(project, ()))),
            "fixVersions": field_meta("fixVersions", "Fix Version/s", allowed=versions),
            "labels": field_meta("labels", "Labels"),
        }

    @staticmethod
    def _issue_type_ids() -> set[str]:
        """The ids of the issue types that the mock lists now (a test may add one)."""
        from .mock_jira import ISSUE_TYPES

        return {str(item["id"]) for item in ISSUE_TYPES}

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area.

        `path` is below the context path and keeps its `/rest/...` prefix; `query` holds the decoded parameters.
        """
        if method != "GET":
            return False
        edit = EDIT_META.match(path)
        if edit:
            return self._reply_for_issue(handler, edit.group(1), lambda key: {"fields": self._fields(key.split("-")[0])})
        transitions = TRANSITIONS.match(path)
        if transitions and "transitions.fields" in ",".join(query.get("expand", [])):
            return self._reply_for_issue(handler, transitions.group(1), self._transitions)
        create = CREATE_META.match(path)
        if create and create.group(1) in self.jira.versions and create.group(2) in self._issue_type_ids():
            fields = {**self._fields(create.group(1)), **self.required_to_create.get(create.group(2), {})}
            values = [{**meta, "fieldId": field_id} for field_id, meta in fields.items()]
            start = int(query.get("startAt", ["0"])[0])
            size = min(int(query.get("maxResults", ["1000"])[0]), self.page_size or 1000)
            page = values[start : start + size]
            last = start + len(page) >= len(values)
            handler.reply(200, {"startAt": start, "maxResults": size, "total": len(values), "isLast": last, "values": page})
            return True
        return False

    def _reply_for_issue(self, handler: "_Handler", key: str, build: Any) -> bool:
        if key not in self.jira.issues:
            handler.reply(404, {"errorMessages": [f"Issue Does Not Exist {key}"], "errors": {}})
        else:
            handler.reply(200, build(key))
        return True

    def _transitions(self, key: str) -> dict[str, Any]:
        from .mock_jira import TRANSITIONS as POSSIBLE

        status = self.jira.issues[key]["fields"]["status"]["name"]
        shown = []
        for transition in POSSIBLE[status]:
            fields = self.screens.get(transition["id"], {})
            shown.append({**transition, "hasScreen": bool(fields), "isAvailable": True, "fields": fields})
        return {"expand": "transitions", "transitions": shown}


def routes(jira: "MockJira") -> ValidationRoutes:
    """The validation routes of this mock Jira: a test fills `required_to_create` and `screens`."""
    return next(item for item in jira.extensions if isinstance(item, ValidationRoutes))
