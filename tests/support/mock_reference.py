"""The reference routes of the mock Jira: priorities, resolutions, issue types, statuses, the components, versions and roles of a project,
the counts of a version, permissions, dashboards, and the information and configuration of the server. They behave as the 11.3 reference
says (a 404 for an unknown project, paging fields where the answer has them); the switches below let a test change what is true.

    reference(jira).denied.add("EDIT_ISSUES")        # the token's user lacks a permission, everywhere
    reference(jira).configuration["votingEnabled"] = False
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

PREFIX = "/rest/api/2"
PERMISSIONS = (  # key, name, type: some of the permissions of Jira, with the keys and names it uses
    ("ADMINISTER", "Administer Jira", "GLOBAL"),
    ("ADMINISTER_PROJECTS", "Administer Projects", "PROJECT"),
    ("ADD_COMMENTS", "Add Comments", "PROJECT"),
    ("ASSIGN_ISSUES", "Assign Issues", "PROJECT"),
    ("BROWSE_PROJECTS", "Browse Projects", "PROJECT"),
    ("COMMENT_EDIT_ALL", "Edit All Comments", "PROJECT"),
    ("CREATE_ISSUES", "Create Issues", "PROJECT"),
    ("EDIT_ISSUES", "Edit Issues", "PROJECT"),
    ("LINK_ISSUES", "Link Issues", "PROJECT"),
    ("MANAGE_WATCHERS", "Manage Watchers", "PROJECT"),
    ("TRANSITION_ISSUES", "Transition Issues", "PROJECT"),
    ("VIEW_VOTERS_AND_WATCHERS", "View Voters and Watchers", "PROJECT"),
    ("WORK_ON_ISSUES", "Work On Issues", "PROJECT"),
)
PRIORITIES = (  # id, name, description
    ("1", "Highest", "This problem will block progress."),
    ("2", "High", "Serious problem that could block progress."),
    ("3", "Medium", "Has the potential to affect progress."),
    ("4", "Low", "Minor problem or easily worked around."),
)
RESOLUTIONS = (
    ("10000", "Done", "Work has been completed on this issue."),
    ("10001", "Won't Do", "This issue won't be actioned."),
    ("10002", "Duplicate", "The problem is a duplicate of an existing issue."),
)
ISSUE_TYPES = (  # id, name, description, subtask
    ("10000", "Epic", "A big user story that needs to be broken down.", False),
    ("10200", "Story", "A user story.", False),
    ("10201", "Bug", "A problem which impairs or prevents the functions of the product.", False),
    ("10202", "Sub-task", "The sub-task of the issue.", True),
)
STATUS_ROWS = (  # id, name, category key, category id, description
    ("1", "To Do", "new", 2, "The issue is waiting."),
    ("3", "In Progress", "indeterminate", 4, "The issue is being worked on."),
    ("5", "Done", "done", 3, "The work is finished."),
    ("6", "Closed", "done", 3, "The issue is closed."),
)
CATEGORY_NAMES = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
PROJECT_IDS = {"PROJ": 10000, "DEMO": 10001}
ROLES = {"Administrators": (10002, ["asmith"]), "Developers": (10001, ["jdoe", "jira-developers"])}
DASHBOARDS = (("10000", "System Dashboard"), ("10001", "Team Dashboard"), ("10002", "Release Dashboard"))
DISPLAY_NAMES = {"asmith": "Alex Smith", "jdoe": "Jane Doe"}
NO_PROJECT = "No project could be found with key '{key}'."


def reference(jira: "MockJira") -> "ReferenceRoutes":
    """The routes object of a mock Jira, to read and change what it answers."""
    return next(item for item in jira.extensions if isinstance(item, ReferenceRoutes))


def _error(*messages: str) -> dict[str, Any]:
    return {"errorMessages": list(messages), "errors": {}}


class ReferenceRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.denied: set[str] = set()  # permission keys that the token's user lacks, in every project and on every issue
        self.issue_types_for_administrators_only = False  # `GET /issuetype` answers 403, as the reference says it may
        self.dashboard_page_cap = 2  # a server may return fewer dashboards than asked for
        self.configuration: dict[str, Any] = {
            "attachmentsEnabled": True,
            "issueLinkingEnabled": True,
            "subTasksEnabled": True,
            "timeTrackingEnabled": True,
            "timeTrackingConfiguration": {"workingHoursPerDay": 8.0, "workingDaysPerWeek": 5.0, "timeFormat": "pretty", "defaultUnit": "minute"},
            "unassignedIssuesAllowed": False,
            "votingEnabled": True,
            "watchingEnabled": True,
        }
        self.server_info: dict[str, Any] = {
            "baseUrl": "https://jira.example.com/jira",  # what the server thinks of itself: the CLI shows the address it is configured with
            "version": "11.3.12",
            "versionNumbers": [11, 3, 12],
            "deploymentType": "Server",
            "buildNumber": 11003012,
            "serverTitle": "Jira",
        }
        self.components: dict[str, list[dict[str, Any]]] = {}
        self.version_facts: dict[tuple[str, str], dict[str, Any]] = {}
        self.version_counts: dict[str, dict[str, int]] = {}
        self._next_version_id = 10300
        self.routes: list[tuple["re.Pattern[str]", Callable[..., bool]]] = [
            (re.compile(pattern), route)
            for pattern, route in (
                (r"/priority", self.priorities),
                (r"/resolution", self.resolutions),
                (r"/issuetype", self.issue_types),
                (r"/status", self.statuses),
                (r"/project/([^/]+)/statuses", self.project_statuses),
                (r"/project/([^/]+)/components", self.project_components),
                (r"/project/([^/]+)/versions", self.project_versions),
                (r"/project/([^/]+)/role", self.project_roles),
                (r"/project/([^/]+)/role/([^/]+)", self.project_role),
                (r"/version/([^/]+)/relatedIssueCounts", self.related_counts),
                (r"/version/([^/]+)/unresolvedIssueCount", self.unresolved_count),
                (r"/mypermissions", self.my_permissions),
                (r"/dashboard", self.dashboards),
                (r"/serverInfo", self.server),
                (r"/configuration", self.configure),
            )
        ]

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area.

        `path` is below the context path and keeps its `/rest/...` prefix; `query` holds the decoded parameters.
        """
        if method != "GET" or not path.startswith(PREFIX + "/"):
            return False
        for pattern, route in self.routes:
            match = pattern.fullmatch(path[len(PREFIX) :])
            if match:
                return route(handler, query, *match.groups())
        return False

    def has(self, permission: str) -> bool:
        return permission not in self.denied

    def _url(self, *parts: str) -> str:
        return "/".join([f"{self.jira.base_url}/rest/api/2", *parts])

    def _known(self, handler: "_Handler", project: str) -> bool:
        """False, after answering 404, for a project that the mock does not know."""
        if project not in self.jira.versions:
            handler.reply(404, _error(NO_PROJECT.format(key=project)))
            return False
        return True

    # -- the lists of the whole Jira -------------------------------------------------------------------

    def priorities(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        handler.reply(200, [
            {"self": self._url("priority", number), "statusColor": "#d04437", "description": text, "iconUrl": "", "name": name, "id": number}
            for number, name, text in PRIORITIES
        ])
        return True

    def resolutions(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        handler.reply(200, [
            {"self": self._url("resolution", number), "id": number, "description": text, "name": name} for number, name, text in RESOLUTIONS
        ])
        return True

    def _issue_type(self, row: tuple[Any, ...]) -> dict[str, Any]:
        number, name, text, subtask = row
        return {"self": self._url("issuetype", number), "id": number, "description": text, "iconUrl": "", "name": name, "subtask": subtask, "avatarId": 10300}

    def issue_types(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        if self.issue_types_for_administrators_only:
            handler.reply(403, _error("You do not have the administrator permission."))
            return True
        handler.reply(200, [self._issue_type(row) for row in ISSUE_TYPES])
        return True

    def _status(self, row: tuple[Any, ...]) -> dict[str, Any]:
        number, name, key, category_id, text = row
        category = {"self": self._url("statuscategory", str(category_id)), "id": category_id, "key": key, "colorName": "blue-gray", "name": CATEGORY_NAMES[key]}
        return {"self": self._url("status", number), "description": text, "iconUrl": "", "name": name, "id": number, "statusCategory": category}

    def statuses(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        handler.reply(200, [self._status(row) for row in STATUS_ROWS])
        return True

    # -- what belongs to a project ---------------------------------------------------------------------

    def project_statuses(self, handler: "_Handler", query: dict[str, list[str]], project: str) -> bool:
        if self._known(handler, project):
            types = (("10200", "Story", STATUS_ROWS[:3]), ("10201", "Bug", STATUS_ROWS)) if project == "PROJ" else ()
            handler.reply(200, [
                {"self": self._url("issuetype", number), "id": number, "name": name, "subtask": False, "statuses": [self._status(row) for row in rows]}
                for number, name, rows in types
            ])
        return True

    def components_of(self, project: str) -> list[dict[str, Any]]:
        from .mock_jira import USER

        if project not in self.components:
            backend = {
                "self": self._url("component", "10100"), "id": "10100", "name": "Backend", "description": "Server side.", "lead": USER,
                "leadUserName": "jdoe", "assigneeType": "PROJECT_DEFAULT", "project": project,
            }
            frontend = {"self": self._url("component", "10101"), "id": "10101", "name": "Frontend", "assigneeType": "PROJECT_DEFAULT", "project": project}
            self.components[project] = [backend, frontend] if project == "PROJ" else []
        return self.components[project]

    def project_components(self, handler: "_Handler", query: dict[str, list[str]], project: str) -> bool:
        if self._known(handler, project):
            handler.reply(200, self.components_of(project))
        return True

    def versions_of(self, project: str) -> list[dict[str, Any]]:
        """The versions of a project: the names the mock Jira keeps in `versions` (which `create` and `import` read), with what a version has."""
        found = []
        for name in self.jira.versions.get(project, []):
            if (project, name) not in self.version_facts:
                self._next_version_id += 1
                identifier = str(self._next_version_id)
                released = name == "Release 1.0"
                self.version_facts[(project, name)] = {
                    "self": self._url("version", identifier),
                    "id": identifier,
                    "name": name,
                    "archived": False,
                    "released": released,
                    "releaseDate": "2026-06-01" if released else "2026-12-01",
                    "overdue": False,
                    "projectId": PROJECT_IDS.get(project, 10099),
                }
                self.version_counts[identifier] = {"fixed": 12, "affected": 3, "unresolved": 0} if released else {"fixed": 5, "affected": 1, "unresolved": 4}
            found.append(self.version_facts[(project, name)])
        return found

    def project_versions(self, handler: "_Handler", query: dict[str, list[str]], project: str) -> bool:
        if self._known(handler, project):
            handler.reply(200, self.versions_of(project))
        return True

    def _version(self, handler: "_Handler", version_id: str) -> dict[str, Any] | None:
        for project in self.jira.versions:
            for version in self.versions_of(project):
                if version["id"] == version_id:
                    return version
        handler.reply(404, _error(f"The version with id '{version_id}' does not exist."))
        return None

    def related_counts(self, handler: "_Handler", query: dict[str, list[str]], version_id: str) -> bool:
        version = self._version(handler, version_id)
        if version is not None:
            counts = self.version_counts[version_id]
            handler.reply(200, {
                "self": version["self"], "issuesFixedCount": counts["fixed"], "issuesAffectedCount": counts["affected"],
                "issueCountWithCustomFieldsShowingVersion": 0, "customFieldNames": [],
            })
        return True

    def unresolved_count(self, handler: "_Handler", query: dict[str, list[str]], version_id: str) -> bool:
        version = self._version(handler, version_id)
        if version is not None:
            counts = self.version_counts[version_id]
            handler.reply(200, {"self": version["self"], "issuesUnresolvedCount": counts["unresolved"], "issuesCount": counts["fixed"]})
        return True

    def _role_url(self, project: str, role_id: str) -> str:
        return self._url("project", str(PROJECT_IDS.get(project, 10099)), "role", role_id)

    def project_roles(self, handler: "_Handler", query: dict[str, list[str]], project: str) -> bool:
        if self._known(handler, project):
            handler.reply(200, {name: self._role_url(project, str(role[0])) for name, role in ROLES.items()})
        return True

    def _actor(self, index: int, name: str) -> dict[str, Any]:
        group = "-" in name  # a group name has a hyphen here, a user name has none
        return {
            "id": 10100 + index, "displayName": DISPLAY_NAMES.get(name, name), "name": name, "avatarUrl": "https://jira.example.com/avatar.png",
            "type": "atlassian-group-role-actor" if group else "atlassian-user-role-actor",
        }

    def project_role(self, handler: "_Handler", query: dict[str, list[str]], project: str, role_id: str) -> bool:
        if not self._known(handler, project):
            return True
        for name, (identifier, actors) in ROLES.items():
            if str(identifier) == role_id:
                handler.reply(200, {
                    "self": self._role_url(project, role_id), "name": name, "id": identifier, "description": "A role.",
                    "actors": [self._actor(index, actor) for index, actor in enumerate(actors)],
                })
                return True
        handler.reply(404, _error("The project role does not exist."))
        return True

    # -- permissions, dashboards, the server ----------------------------------------------------------------

    def my_permissions(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        if "permissions" in query:
            return False  # the question `projects-admin` asks, answered by the mock's own route as before
        project = query.get("projectKey", [""])[0]
        issue = query.get("issueKey", [""])[0]
        if issue:
            if issue not in self.jira.issues:
                handler.reply(404, _error("Issue Does Not Exist"))
                return True
            project = issue.rsplit("-", 1)[0]
        elif project and project not in self.jira.versions:
            handler.reply(404, _error(NO_PROJECT.format(key=project)))
            return True
        found: dict[str, Any] = {}
        for key, name, kind in PERMISSIONS:
            granted = self.jira.permissions.get(project, False) if key == "ADMINISTER_PROJECTS" and project else True
            found[key] = {
                "id": str(10 + len(found)), "key": key, "name": name, "type": kind, "description": f"Ability to {name.lower()}.",
                "havePermission": granted and self.has(key),
            }
        handler.reply(200, {"permissions": found})
        return True

    def dashboards(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        start_text = query.get("startAt", ["0"])[0]
        if not start_text.isdigit():
            handler.reply(400, _error("The startAt parameter is invalid."))
            return True
        wanted = {"my": {"10002"}, "favourite": {"10001"}}.get(query.get("filter", [""])[0])
        rows = [row for row in DASHBOARDS if wanted is None or row[0] in wanted]
        start = int(start_text)
        size = min(int(query.get("maxResults", ["20"])[0]), self.dashboard_page_cap)
        shown = [
            {"id": number, "name": name, "self": self._url("dashboard", number), "view": f"{self.jira.base_url}/secure/Dashboard.jspa?selectPageId={number}"}
            for number, name in rows[start : start + size]
        ]
        answer: dict[str, Any] = {"startAt": start, "maxResults": size, "total": len(rows), "dashboards": shown}
        if start + size < len(rows):
            answer["next"] = self._url("dashboard") + f"?startAt={start + size}"
        if start > 0:
            answer["prev"] = self._url("dashboard") + f"?startAt={max(start - size, 0)}"
        handler.reply(200, answer)
        return True

    def server(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        handler.reply(200, self.server_info)
        return True

    def configure(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        handler.reply(200, self.configuration)
        return True
