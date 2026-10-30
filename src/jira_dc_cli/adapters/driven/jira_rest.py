"""The Jira port implemented over Jira Data Center's REST API."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from typing import Any, Sequence

from ...application.ports import Cache, JiraGateway
from ...application.settings import Settings
from ...domain.errors import (
    AuthError,
    ConfigError,
    NotFoundError,
    RejectedError,
    UnavailableError,
)
from ...domain.fields import LOGICAL_FIELDS, resolve_field_ids
from ...domain.model import (
    Comment,
    CreatedIssue,
    FieldInfo,
    Issue,
    IssueTypeInfo,
    LinkType,
    Project,
    SearchPage,
    Transition,
    User,
)
from ...domain.privacy import redact_json, strip_users
from . import jira_mapper as mapper
from .http_transport import HttpResponse, HttpTransport
from .rest_agile import RestAgile
from .rest_attachments import RestAttachments
from .rest_collab import RestCollab
from .rest_comments import RestComments
from .rest_reading import RestReading
from .rest_reference import RestReference
from .rest_users import RestUsers
from .rest_validation import RestValidation

_FIELDS_TTL = 24 * 3600.0
_ISSUE_TYPES_TTL = 7 * 24 * 3600.0
_LONG_QUERY = 3500  # characters of an encoded search query above which the search is sent as a POST: a long `key in (...)` does not fit into a URL


def _project_and_type(path: str, request: Any) -> tuple[str, str]:
    """The project key and issue type id that a rejected request was about, as far as the path and the payload say."""
    match = re.match(r"^/issue/([A-Za-z][A-Za-z0-9_]*)-\d+", path)
    fields = request.get("fields") if isinstance(request, dict) else None
    fields = fields if isinstance(fields, dict) else {}
    project, kind = fields.get("project"), fields.get("issuetype")  # a payload that is wrong may hold a text where an object belongs
    key = match.group(1).upper() if match else (str(project.get("key") or "") if isinstance(project, dict) else "")
    return key, str(kind.get("id") or "") if isinstance(kind, dict) else ""


_GENERIC_HINT = "Jira's message above says what it did not accept; `--dry-run` shows the request that would be sent."


def _rejected_hint(method: str, path: str, request: Any, response: Any) -> str:
    """What to do after Jira refused a request: never empty, and never an error of its own (the refusal is what matters). For an issue it
    names the field and points to `jira metadata`, which lists the values the project allows."""
    try:
        errors = response.get("errors") if isinstance(response, dict) else None
        fields = [str(name) for name in (errors or {})] if isinstance(errors, dict) else []
        if path.startswith("/issue"):
            project, type_id = _project_and_type(path, request)
            where = f"jira metadata {project} {type_id}".strip() if project else "jira metadata PROJECT ISSUE_TYPE_ID"
            if fields:
                which = "that field" if len(fields) == 1 else "those fields"
                return f"Jira names {', '.join(fields)}: check the value of {which} against what the project allows, which `{where}` lists. `--dry-run` checks the standard fields before anything is sent."
            return f"See what the project accepts with `{where}`; `--dry-run` checks the standard fields before anything is sent."
        if path.startswith("/search"):
            return "Check the JQL. `jira fields` lists the field names. Windows PowerShell 5.1 removes double quotes inside an argument: pass the query as --jql '@query.jql'."
    except (AttributeError, TypeError, ValueError):
        pass
    return _GENERIC_HINT


def _messages(body: Any) -> str:
    """Jira's own explanation of a refusal: errorMessages, field errors and `message`."""
    if not isinstance(body, dict):
        return ""
    parts = [str(item) for item in body.get("errorMessages") or []]
    parts += [f"{name}: {text}" for name, text in (body.get("errors") or {}).items()]
    if body.get("message"):
        parts.append(str(body["message"]))
    return "; ".join(parts)


def _without_token(value: Any, token: str) -> Any:
    """The value with every occurrence of the token replaced, in strings at any depth."""
    if len(token) < 6:  # too short to be a token; replacing it would only damage the text
        return value
    if isinstance(value, str):
        return value.replace(token, "********")
    if isinstance(value, dict):
        return {key: _without_token(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_token(item, token) for item in value]
    return value


class RestJiraGateway(
    RestAttachments, RestAgile, RestComments, RestUsers, RestValidation, RestReading, RestReference, RestCollab, JiraGateway
):
    """The Jira port over Jira Data Center's REST API. The calls of a feature area live in the `rest_<area>.py` mixin of that
    area; they use `_call`, `_transport` and `_settings` of this class."""

    def __init__(self, settings: Settings, transport: HttpTransport, cache: Cache) -> None:
        self._settings = settings
        self._transport = transport
        self._cache = cache
        self._ids: dict[str, str | None] | None = None
        self._origin = hashlib.sha1(settings.base_url.encode("utf-8")).hexdigest()[:12]

    # -- plumbing ---------------------------------------------------------------------------

    def _path(self, path: str) -> str:
        return path if path.startswith("/rest/") else self._settings.api_path.rstrip("/") + path

    def _call(
        self, method: str, path: str, body: Any = None, *, timeout: float | None = None, retries: int | None = None, idempotent: bool = False
    ) -> Any:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        response = self._transport.request(method, self._path(path), body=payload, timeout=timeout, retries=retries, idempotent=idempotent)
        decoded = self._decode(response)
        self._raise_for_status(response.status, decoded, method, path, body)
        return decoded

    @staticmethod
    def _decode(response: HttpResponse) -> Any:
        if not response.body.strip():
            return {}
        try:
            return json.loads(response.body.decode("utf-8"))
        except ValueError:
            return {"_text": response.body.decode("utf-8", "replace")[:500]}

    def _scrub(self, value: Any) -> Any:
        """Jira's explanation of a refusal ends up in our error output. Take the token out in case something
        in between echoed it, and the personal data out of its text: mentions, addresses and personal links always,
        the people themselves under the privacy policy."""
        value = strip_users(value) if self._settings.hide_users else redact_json(value)
        return _without_token(value, self._settings.pat)

    def _raise_for_status(self, status: int, body: Any, method: str, path: str, request: Any = None) -> None:
        if 200 <= status < 300:
            return
        body = self._scrub(body)
        reason = _messages(body)
        shown = path.split("?")[0]
        details = {"status": status, "request": f"{method} {shown}"}
        if 300 <= status < 400:
            raise ConfigError(
                f"Jira answered with a redirect (HTTP {status}) instead of data.",
                hint="Check JIRA_BASE_URL: the address or the context path is probably wrong (or http instead of https).",
                details=details,
            )
        if status == 401:
            raise AuthError(
                "Jira did not accept the token (HTTP 401).",
                hint="Create a new personal access token in your Jira profile and run `jira setup-pat`.",
                details=details,
            )
        if status == 403:
            raise AuthError(
                f"Jira refused this request (HTTP 403){': ' + reason if reason else ''}.",
                hint="Your account lacks the permission, or the token is limited. Ask a Jira administrator.",
                details=details,
            )
        if status == 404:
            raise NotFoundError(f"Not found: {method} {shown}{' - ' + reason if reason else ''}", details=details)
        if status == 429:
            raise UnavailableError(
                "Jira is limiting the rate of requests (HTTP 429).",
                hint="Wait a moment and try again; fewer requests (for example --limit instead of --all) help.",
                details=details,
            )
        if status >= 500:
            raise UnavailableError(
                f"Jira answered with a server error (HTTP {status}){': ' + reason if reason else ''}.",
                hint="Try again in a moment; if it stays, tell your Jira administrator.",
                details=details,
            )
        raise RejectedError(
            f"Jira refused the request (HTTP {status}): {reason or 'no explanation'}",
            hint=_rejected_hint(method, shown, request, body),
            details={**details, "jira": body},
        )

    def _field_ids_param(self, names: Sequence[str]) -> tuple[str, list[str]]:
        """Jira's `fields` parameter for logical and standard names, and the extra names asked for."""
        ids = self.field_ids()
        requested: list[str] = []
        extras: list[str] = []
        for name in names:
            if name in LOGICAL_FIELDS:
                if ids.get(name):
                    requested.append(ids[name])  # type: ignore[arg-type]
            else:
                requested.append(name)
                if name not in mapper.MODELED:
                    extras.append(name)
        return ",".join(requested), extras

    # -- discovery --------------------------------------------------------------------------

    def current_user(self) -> User:
        return mapper.user_from(self._call("GET", "/myself")) or User()

    def server_info(self) -> dict[str, Any]:
        return dict(self._call("GET", "/serverInfo"))

    def list_fields(self, *, refresh: bool = False) -> list[FieldInfo]:
        key = self._origin
        cached = None if refresh or self._settings.no_cache else self._cache.get("fields", key, _FIELDS_TTL)
        if cached:
            try:
                return [mapper.field_from(item) for item in json.loads(cached)]
            except ValueError:
                pass
        data = self._call("GET", "/field")
        if not self._settings.no_cache:
            self._cache.put("fields", key, json.dumps(data))
        return [mapper.field_from(item) for item in data]

    def field_ids(self) -> dict[str, str | None]:
        if self._ids is None:
            self._ids = resolve_field_ids(self.list_fields(), self._settings.field_overrides())
        return self._ids

    # -- issues -----------------------------------------------------------------------------

    def get_issue(self, key: str, *, fields: Sequence[str] | None = None) -> Issue:
        quoted = urllib.parse.quote(key, safe="")
        if fields is None:
            data = self._call("GET", f"/issue/{quoted}")
            return mapper.issue_from(data, self.field_ids())
        param, extras = self._field_ids_param(fields)
        data = self._call("GET", f"/issue/{quoted}?fields={urllib.parse.quote(param, safe=',')}")
        return mapper.issue_from(data, self.field_ids(), extras)

    def search(self, jql: str, *, fields: Sequence[str], start_at: int = 0, max_results: int = 50) -> SearchPage:
        param, extras = self._field_ids_param(fields)
        query = urllib.parse.urlencode(
            {"jql": jql, "startAt": start_at, "maxResults": max_results, "fields": param}, quote_via=urllib.parse.quote
        )
        if len(query) > _LONG_QUERY:
            request: dict[str, Any] = {"jql": jql, "startAt": start_at, "maxResults": max_results}
            if param:
                request["fields"] = param.split(",")
            data = self._call("POST", "/search", request, idempotent=True)  # it only reads: retried like the GET form
        else:
            data = self._call("GET", f"/search?{query}")
        ids = self.field_ids()
        raw = list(data.get("issues") or [])
        return SearchPage(
            issues=[mapper.issue_from(item, ids, extras) for item in raw],
            total=int(data.get("total", len(raw))),
            start_at=int(data.get("startAt", start_at)),
            max_results=int(data.get("maxResults", max_results)),
            raw_issues=raw,
        )

    def list_transitions(self, key: str) -> list[Transition]:
        data = self._call("GET", f"/issue/{urllib.parse.quote(key, safe='')}/transitions")
        return [mapper.transition_from(item) for item in data.get("transitions") or []]

    def transition_issue(self, key: str, payload: dict[str, Any]) -> None:
        self._call("POST", f"/issue/{urllib.parse.quote(key, safe='')}/transitions", payload)

    def list_comments(self, key: str) -> list[Comment]:
        data = self._call("GET", f"/issue/{urllib.parse.quote(key, safe='')}/comment?maxResults=1000")
        return [mapper.comment_from(item) for item in data.get("comments") or []]

    def add_comment(self, key: str, payload: dict[str, Any]) -> Comment:
        data = self._call("POST", f"/issue/{urllib.parse.quote(key, safe='')}/comment", payload)
        return mapper.comment_from(data) if isinstance(data, dict) and data.get("id") else Comment("", "", str(payload.get("body", "")))

    def list_link_types(self) -> list[LinkType]:
        data = self._call("GET", "/issueLinkType")
        return [mapper.link_type_from(item) for item in data.get("issueLinkTypes") or []]

    def create_link(self, payload: dict[str, Any]) -> None:
        self._call("POST", "/issueLink", payload)

    def create_issue(self, payload: dict[str, Any]) -> CreatedIssue:
        data = self._call("POST", "/issue", payload)
        key = str(data.get("key", ""))
        url = f"{self._settings.base_url.rstrip('/')}/browse/{key}" if key else ""
        return CreatedIssue(str(data.get("id", "")), key, url)

    def update_issue(self, key: str, payload: dict[str, Any]) -> None:
        self._call("PUT", f"/issue/{urllib.parse.quote(key, safe='')}", payload)

    # -- projects ---------------------------------------------------------------------------

    def list_projects(self) -> list[Project]:
        return [mapper.project_from(item) for item in self._call("GET", "/project?expand=lead")]

    def get_project(self, key: str) -> Project:
        expand = "description,lead,issueTypes,url,projectKeys"
        return mapper.project_from(self._call("GET", f"/project/{urllib.parse.quote(key, safe='')}?expand={expand}"))

    def list_versions(self, project_key: str) -> list[str]:
        data = self._call("GET", f"/project/{urllib.parse.quote(project_key, safe='')}/versions")
        return [str(item.get("name", "")) for item in data if isinstance(item, dict)]

    def has_permission(
        self, project_key: str, permission: str, *, timeout: float | None = None, retries: int | None = None
    ) -> bool:
        query = urllib.parse.urlencode({"projectKey": project_key, "permissions": permission})
        data = self._call("GET", f"/mypermissions?{query}", timeout=timeout, retries=retries)
        return bool(((data.get("permissions") or {}).get(permission) or {}).get("havePermission"))

    def list_issue_types(self, project_key: str, *, refresh: bool = False) -> list[IssueTypeInfo]:
        key = f"{self._origin}-{project_key}"
        cached = None if refresh or self._settings.no_cache else self._cache.get("issue-types", key, _ISSUE_TYPES_TTL)
        if cached:
            try:
                return mapper.issue_types_from(json.loads(cached))
            except ValueError:
                pass
        data = self.create_metadata(project_key)
        if not self._settings.no_cache:
            self._cache.put("issue-types", key, json.dumps(data))
        return mapper.issue_types_from(data)

    def create_metadata(self, project_key: str, issue_type_id: str = "") -> dict[str, Any]:
        base = f"/issue/createmeta/{urllib.parse.quote(project_key, safe='')}/issuetypes"
        if issue_type_id:
            return self._call("GET", f"{base}/{urllib.parse.quote(issue_type_id, safe='')}?maxResults=1000")
        return self._call("GET", f"{base}?maxResults=100")

    # -- raw --------------------------------------------------------------------------------

    def request(self, method: str, path: str, body: Any = None) -> Any:
        return self._call(method, path, body)
