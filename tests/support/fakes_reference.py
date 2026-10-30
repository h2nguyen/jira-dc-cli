"""The reference calls of `FakeGateway`, in memory."""
from __future__ import annotations

from jira_dc_cli.domain.errors import NotFoundError
from jira_dc_cli.domain.model import User
from jira_dc_cli.domain.refdata import (
    ComponentInfo,
    DashboardInfo,
    DashboardPage,
    IssueTypeDetail,
    PermissionInfo,
    ProjectRole,
    RefItem,
    RoleActor,
    RoleRef,
    ServerSettings,
    StatusDetail,
    TimeTracking,
    TypeStatuses,
    VersionCounts,
    VersionInfo,
)

JANE = User("jdoe", "Jane Doe", "jane.doe@example.com")
ALEX = User("asmith", "Alex Smith", "alex.smith@example.com")
PERMISSIONS = (  # key, name, type: a few of the permissions Jira has, with the keys and names it uses
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


class FakeReference:
    """Implements `ReferencePort` for the tests; its state lives on the gateway (every name starts with `ref_`, so that no area
    of the gateway meets another's) and is set up in `_init_reference`."""

    def _init_reference(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.ref_priorities = [
            RefItem("1", "Highest", "This problem will block progress."),
            RefItem("2", "High", "Serious problem that could block progress."),
            RefItem("3", "Medium", "Has the potential to affect progress."),
            RefItem("4", "Low", "Minor problem or easily worked around."),
        ]
        self.ref_resolutions = [
            RefItem("10000", "Done", "Work has been completed on this issue."),
            RefItem("10001", "Won't Do", "This issue won't be actioned."),
            RefItem("10002", "Duplicate", "The problem is a duplicate of an existing issue."),
        ]
        epic = IssueTypeDetail("10000", "Epic", "A big user story that needs to be broken down.", False)
        story = IssueTypeDetail("10200", "Story", "A user story.", False)
        bug = IssueTypeDetail("10201", "Bug", "A problem which impairs or prevents the functions of the product.", False)
        subtask = IssueTypeDetail("10202", "Sub-task", "The sub-task of the issue.", True)
        self.ref_all_issue_types = [epic, story, bug, subtask]
        self.ref_project_issue_types = {"PROJ": [epic, story, bug], "DEMO": [story, bug, subtask]}
        todo = StatusDetail("1", "To Do", "new", "The issue is waiting.")
        progress = StatusDetail("3", "In Progress", "indeterminate", "The issue is being worked on.")
        done = StatusDetail("5", "Done", "done", "The work is finished.")
        closed = StatusDetail("6", "Closed", "done", "The issue is closed.")
        self.ref_all_statuses = [todo, progress, done, closed]
        story = TypeStatuses("10200", "Story", False, [todo, progress, done])
        bug = TypeStatuses("10201", "Bug", False, [todo, progress, done, closed])
        self.ref_project_statuses = {"PROJ": [story, bug], "DEMO": []}
        self.ref_components = {
            "PROJ": [ComponentInfo("10100", "Backend", "Server side.", JANE), ComponentInfo("10101", "Frontend", "", None)],
            "DEMO": [],
        }
        self.ref_versions = {
            "PROJ": [
                VersionInfo("10300", "Release 1.0", "The first release.", True, False, "2026-06-01"),
                VersionInfo("10301", "Release 2.0", "", False, False, "2026-12-01"),
            ],
            "DEMO": [],
        }
        self.ref_version_counts = {"10300": VersionCounts(12, 3, 0), "10301": VersionCounts(5, 1, 4)}
        self.ref_denied: set[str] = set()  # permission keys that the token's user lacks
        self.ref_roles = {
            "PROJ": [
                ProjectRole("10001", "Developers", [RoleActor("jdoe", "Jane Doe", "user"), RoleActor("jira-developers", "", "group")]),
                ProjectRole("10002", "Administrators", [RoleActor("asmith", "Alex Smith", "user")]),
            ],
            "DEMO": [],
        }
        self.ref_dashboards = [
            DashboardInfo("10000", "System Dashboard", "", "https://jira.example.com/secure/Dashboard.jspa?selectPageId=10000"),
            DashboardInfo("10001", "Team Dashboard", "What the team looks at.", "https://jira.example.com/secure/Dashboard.jspa?selectPageId=10001"),
            DashboardInfo("10002", "Release Dashboard", "", "https://jira.example.com/secure/Dashboard.jspa?selectPageId=10002"),
        ]
        self.ref_favourite_dashboards = {"10001"}
        self.ref_my_dashboards = {"10002"}
        self.ref_dashboard_page_cap = 2  # a server may return fewer than asked for: the CLI keeps asking
        self.ref_server_settings = ServerSettings(
            attachments_enabled=True,
            sub_tasks_enabled=True,
            issue_linking_enabled=True,
            voting_enabled=True,
            watching_enabled=True,
            unassigned_issues_allowed=False,
            time_tracking_enabled=True,
            time_tracking=TimeTracking(8.0, 5.0, "pretty", "minute"),
        )
        self.ref_attachment_upload_limit: int | None = 10485760

    def _ref_project(self, key: str) -> str:
        if key not in self.projects:
            raise NotFoundError(f"Not found: GET /project/{key} - No project could be found with key '{key}'.")
        return key

    def list_priorities(self) -> list[RefItem]:
        self._enter("list_priorities")
        return list(self.ref_priorities)

    def list_resolutions(self) -> list[RefItem]:
        self._enter("list_resolutions")
        return list(self.ref_resolutions)

    def list_all_issue_types(self) -> list[IssueTypeDetail]:
        self._enter("list_all_issue_types")
        return list(self.ref_all_issue_types)

    def list_project_issue_types(self, project_key: str) -> list[IssueTypeDetail]:
        self._enter("list_project_issue_types", project_key)
        return list(self.ref_project_issue_types.get(self._ref_project(project_key), []))

    def list_all_statuses(self) -> list[StatusDetail]:
        self._enter("list_all_statuses")
        return list(self.ref_all_statuses)

    def list_project_statuses(self, project_key: str) -> list[TypeStatuses]:
        self._enter("list_project_statuses", project_key)
        return list(self.ref_project_statuses.get(self._ref_project(project_key), []))

    def list_project_components(self, project_key: str) -> list[ComponentInfo]:
        self._enter("list_project_components", project_key)
        return list(self.ref_components.get(self._ref_project(project_key), []))

    def list_project_versions(self, project_key: str) -> list[VersionInfo]:
        self._enter("list_project_versions", project_key)
        return list(self.ref_versions.get(self._ref_project(project_key), []))

    def get_version_counts(self, version_id: str) -> VersionCounts:
        self._enter("get_version_counts", version_id)
        if version_id not in self.ref_version_counts:
            raise NotFoundError(f"Not found: GET /version/{version_id}/relatedIssueCounts - The version does not exist.")
        return self.ref_version_counts[version_id]

    def get_my_permissions(self, *, project_key: str = "", issue_key: str = "") -> list[PermissionInfo]:
        self._enter("get_my_permissions", project_key, issue_key)
        if project_key:
            self._ref_project(project_key)
        if issue_key:
            self._issue(issue_key)
        return [PermissionInfo(key, name, kind, key not in self.ref_denied) for key, name, kind in PERMISSIONS]

    def list_project_roles(self, project_key: str) -> list[RoleRef]:
        self._enter("list_project_roles", project_key)
        return [RoleRef(role.id, role.name) for role in self.ref_roles.get(self._ref_project(project_key), [])]

    def get_project_role(self, project_key: str, role_id: str) -> ProjectRole:
        self._enter("get_project_role", project_key, role_id)
        for role in self.ref_roles.get(self._ref_project(project_key), []):
            if role.id == role_id:
                return role
        raise NotFoundError(f"Not found: GET /project/{project_key}/role/{role_id} - The project role does not exist.")

    def list_dashboards(self, *, filter_name: str = "", start_at: int = 0, max_results: int = 50) -> DashboardPage:
        self._enter("list_dashboards", filter_name, start_at, max_results)
        wanted = {"favourite": self.ref_favourite_dashboards, "my": self.ref_my_dashboards}.get(filter_name)
        found = [item for item in self.ref_dashboards if wanted is None or item.id in wanted]
        size = min(max_results, self.ref_dashboard_page_cap)
        return DashboardPage(len(found), found[start_at : start_at + size])

    def get_server_settings(self) -> ServerSettings:
        self._enter("get_server_settings")
        return self.ref_server_settings

    def get_attachment_upload_limit(self) -> int | None:
        self._enter("get_attachment_upload_limit")
        return self.ref_attachment_upload_limit
