"""Commands of the reference area: read-only lists and facts about the Jira (priorities, statuses, versions, permissions, roles, ...)."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, Callable, Sequence

from ..listing import FORMAT, SELECT, Column, print_list, structured_text
from ..render import key_values, table, to_jsonable
from ..runtime import Context
from ..spec import Arg, Command, Example

if TYPE_CHECKING:
    from .....domain.refdata import ServerReport, ServerSettings

GROUP = "Reference"

PROJECT = Arg(("project",), "Project key. Default: the current project (`jira use`).", metavar="PROJECT_KEY")
ONLY_PROJECT = Arg(("--project",), "Only this project. Default: the whole Jira.", metavar="KEY")
WITH_USERS = "Show {what} (people). Refused when JIRA_HIDE_USERS=true."

ITEM_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name"), ("DESCRIPTION", "description")]
TYPE_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name"), ("SUBTASK", "subtask"), ("DESCRIPTION", "description")]
STATUS_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name"), ("CATEGORY", "category"), ("DESCRIPTION", "description")]
PROJECT_STATUS_COLUMNS: list[Column] = [("ISSUE TYPE", "name"), ("STATUSES", "statuses.name"), ("CATEGORIES", "statuses.category")]
COMPONENT_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name"), ("DESCRIPTION", "description")]
VERSION_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name"), ("RELEASED", "released"), ("ARCHIVED", "archived"), ("RELEASE DATE", "releaseDate")]
COUNT_COLUMNS: list[Column] = [("FIXED", "counts.fixed"), ("AFFECTED", "counts.affected"), ("UNRESOLVED", "counts.unresolved")]
PERMISSION_COLUMNS: list[Column] = [("KEY", "key"), ("NAME", "name"), ("TYPE", "type"), ("GRANTED", "havePermission")]
ROLE_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name")]
DASHBOARD_COLUMNS: list[Column] = [("ID", "id"), ("NAME", "name"), ("DESCRIPTION", "description"), ("VIEW", "view")]


def _rows(items: Sequence[Any]) -> list[dict[str, Any]]:
    return [to_jsonable(item) for item in items]


def _show(
    ctx: Context,
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    columns: Sequence[Column],
    empty: str,
    *,
    payload: Any = None,
    list_key: str | None = None,
    plain: Callable[[], str] | None = None,
) -> None:
    """Print rows as JSON (the list itself, or `payload` that holds it under `list_key`), as `--format` or `--select` ask, or as a table."""
    text = plain or (lambda: structured_text(rows, columns, None, "table") if rows else empty)
    print_list(ctx, args, payload=rows if payload is None else payload, rows=rows, list_key=list_key, columns=columns, plain=text)


# -- priorities, resolutions, issue types ---------------------------------------------------------------


def run_priorities(ctx: Context, args: argparse.Namespace) -> int:
    _show(ctx, args, _rows(ctx.app.reference.priorities.execute()), ITEM_COLUMNS, "No priorities.")
    return 0


def run_resolutions(ctx: Context, args: argparse.Namespace) -> int:
    _show(ctx, args, _rows(ctx.app.reference.resolutions.execute()), ITEM_COLUMNS, "No resolutions.")
    return 0


def run_issue_types(ctx: Context, args: argparse.Namespace) -> int:
    _show(ctx, args, _rows(ctx.app.reference.issue_types.execute(args.project or "")), TYPE_COLUMNS, "No issue types.")
    return 0


# -- statuses ---------------------------------------------------------------------------------------------


def run_statuses(ctx: Context, args: argparse.Namespace) -> int:
    overview = ctx.app.reference.statuses.execute(args.project or "")
    key, columns = ("issueTypes", PROJECT_STATUS_COLUMNS) if overview.project else ("statuses", STATUS_COLUMNS)
    rows = _rows(overview.issue_types if overview.project else overview.statuses)
    document = {"project": overview.project or None, key: rows, "doneStatuses": overview.done}

    def plain() -> str:
        text = structured_text(rows, columns, None, "table") if rows else "No statuses."
        return f"{text}\nDone means: {', '.join(overview.done)}." if overview.done else text

    _show(ctx, args, rows, columns, "No statuses.", payload=document, list_key=key, plain=plain)
    return 0


# -- components, versions ---------------------------------------------------------------------------------


def run_components(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.reference.components.execute(args.project or "", with_users=args.with_users)
    columns = COMPONENT_COLUMNS + ([("LEAD", "lead.displayName")] if args.with_users else [])
    _show(ctx, args, _rows(result.components), columns, f"No components in {result.project}.")
    return 0


def run_fix_versions(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.reference.versions.execute(args.project or "", released=args.released, unreleased=args.unreleased, counts=args.counts)
    columns = VERSION_COLUMNS + (COUNT_COLUMNS if args.counts else [])
    _show(ctx, args, _rows(result.versions), columns, f"No such versions in {result.project}.")
    return 0


# -- permissions, roles, dashboards -----------------------------------------------------------------------


def run_permissions(ctx: Context, args: argparse.Namespace) -> int:
    found = ctx.app.reference.permissions.execute(project=args.project or "", issue=args.issue or "", only=args.only or "")

    def plain() -> str:
        return table(["KEY", "GRANTED", "NAME"], [(item.key, "yes" if item.have_permission else "no", item.name) for item in found])

    _show(ctx, args, _rows(found), PERMISSION_COLUMNS, "No permissions.", plain=plain)
    return 0


def run_roles(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.reference.roles.execute(args.project or "", with_users=args.with_users)
    columns = ROLE_COLUMNS + ([("ACTORS", "actors.name")] if args.with_users else [])
    _show(ctx, args, _rows(result.roles), columns, f"No roles in {result.project}.")
    return 0


def run_dashboards(ctx: Context, args: argparse.Namespace) -> int:
    page = ctx.app.reference.dashboards.execute(mine=args.mine, favourite=args.favourite, limit=args.limit)
    rows = _rows(page.dashboards)
    columns = [column for column in DASHBOARD_COLUMNS if column[1] != "description" or any(row["description"] for row in rows)]

    def plain() -> str:
        text = structured_text(rows, columns, None, "table") if rows else "No dashboards."
        return f"{text}\nShowing {len(rows)} of {page.total}." if rows and page.total > len(rows) else text

    _show(ctx, args, rows, columns, "No dashboards.", payload={"total": page.total, "dashboards": rows}, list_key="dashboards", plain=plain)
    return 0


# -- server -----------------------------------------------------------------------------------------------


def _on(flag: bool | None) -> str:
    return "unknown" if flag is None else "on" if flag else "off"


def _size(number: int) -> str:
    """A number of bytes as a person reads it: 10485760 is `10 MiB`."""
    value = float(number)
    for unit in ("bytes", "KiB", "MiB"):
        if value < 1024:
            return f"{value:g} {unit}"
        value /= 1024
    return f"{value:g} GiB"


def _attachments(settings: ServerSettings) -> str:
    limit = settings.attachment_upload_limit
    return _on(settings.attachments_enabled) + (f", upload limit {_size(limit)}" if limit is not None else "")


def _time_tracking(settings: ServerSettings) -> str:
    tracking = settings.time_tracking
    if not settings.time_tracking_enabled or tracking is None:
        return _on(settings.time_tracking_enabled)
    parts = []
    if tracking.working_hours_per_day is not None:
        parts.append(f"{tracking.working_hours_per_day:g} hours a day")
    if tracking.working_days_per_week is not None:
        parts.append(f"{tracking.working_days_per_week:g} days a week")
    if tracking.time_format:
        parts.append(f"format {tracking.time_format}")
    if tracking.default_unit:
        parts.append(f"default unit {tracking.default_unit}")
    return f"on ({', '.join(parts)})" if parts else "on"


def server_text(report: ServerReport) -> str:
    settings = report.settings
    build = f", build {report.build_number}" if report.build_number is not None else ""
    unassigned = {None: "unknown", True: "allowed", False: "not allowed"}[settings.unassigned_issues_allowed]
    return key_values(
        [
            ("Jira", f"{report.version or 'unknown version'} ({report.deployment_type or 'unknown deployment'}{build})"),
            ("Base URL", report.base_url),
            ("Compatibility", f"{report.compatibility.status}: {report.compatibility.detail}"),
            ("Hint", report.compatibility.hint),
            ("Attachments", _attachments(settings)),
            ("Sub-tasks", _on(settings.sub_tasks_enabled)),
            ("Issue linking", _on(settings.issue_linking_enabled)),
            ("Voting", _on(settings.voting_enabled)),
            ("Watching", _on(settings.watching_enabled)),
            ("Unassigned", unassigned),
            ("Time tracking", _time_tracking(settings)),
        ]
    )


def run_server(ctx: Context, args: argparse.Namespace) -> int:
    report = ctx.app.reference.server.execute()
    ctx.print_json(report) if ctx.json_mode else ctx.print(server_text(report))
    return 0


COMMANDS = (
    Command(
        name="priorities",
        group=GROUP,
        summary="List the issue priorities",
        when="Use to see the priority names of this Jira, highest first, before you set one with `update --priority` or ask for one in a search.",
        description="Id, name and description of every priority, in the order Jira keeps them.",
        args=(SELECT, FORMAT),
        examples=(Example("jira priorities"), Example("jira priorities --json --select name")),
        json_output=True,
        output="a list of {id, name, description}.",
        handler=run_priorities,
    ),
    Command(
        name="resolutions",
        group=GROUP,
        summary="List the issue resolutions",
        when="Use to see the resolution names (Done, Won't Do, ...) of this Jira. A resolution is not a status: `statuses` says what counts as done.",
        description="Id, name and description of every resolution.",
        args=(SELECT, FORMAT),
        examples=(Example("jira resolutions"), Example("jira resolutions --format md")),
        json_output=True,
        output="a list of {id, name, description}.",
        handler=run_resolutions,
    ),
    Command(
        name="issue-types",
        group=GROUP,
        summary="List the issue types",
        when="Use to see the issue types of this Jira, or with --project the ones that project uses, before `create --type` or a search by type. "
        "`metadata` adds the fields that each type requires.",
        description="Without --project every type of the Jira.",
        args=(ONLY_PROJECT, SELECT, FORMAT),
        examples=(Example("jira issue-types"), Example("jira issue-types --project PROJ --format tsv")),
        json_output=True,
        output="a list of {id, name, description, subtask}.",
        handler=run_issue_types,
    ),
    Command(
        name="statuses",
        group=GROUP,
        summary="List the statuses and what counts as done",
        when="Use to see the status names and their category (new, indeterminate, done): the category decides whether an issue is done. "
        "With --project the statuses of each issue type of that project. To move an issue use `transition`.",
        description="`doneStatuses` names the statuses of the category done.",
        args=(ONLY_PROJECT, SELECT, FORMAT),
        examples=(
            Example("jira statuses"),
            Example("jira statuses --project PROJ"),
            Example("jira statuses --project PROJ --json --select name,statuses.name"),
        ),
        json_output=True,
        output="project, statuses[{id,name,category,description}] (whole Jira) or issueTypes[{id,name,subtask,statuses[{id,name,category,"
        "description}]}] (--project), doneStatuses[].",
        handler=run_statuses,
    ),
    Command(
        name="components",
        group=GROUP,
        summary="List the components of a project",
        when="Use to see the component names that `update --components` and a search by component accept. The lead is a person: "
        "shown only with --with-users.",
        description="Without a key the current project is used.",
        args=(PROJECT, Arg(("--with-users",), WITH_USERS.format(what="the lead of every component"), kind="flag"), SELECT, FORMAT),
        examples=(Example("jira components PROJ"), Example("jira components PROJ --with-users --json")),
        json_output=True,
        output="a list of {id, name, description, lead}; lead is null unless --with-users.",
        handler=run_components,
    ),
    Command(
        name="fix-versions",
        group=GROUP,
        summary="List the versions of a project",
        when="Use to see the versions (releases) of a project and whether they are released, before you set a fix version or search by one.",
        description="--counts adds the issues per version: two requests each, so at most 25 versions are counted "
        "(narrow the list with --released or --unreleased).",
        args=(
            PROJECT,
            Arg(("--released",), "Only the released versions.", kind="flag"),
            Arg(("--unreleased",), "Only the versions that are not released yet.", kind="flag"),
            Arg(("--counts",), "Add the issues per version: fixed, affected, unresolved.", kind="flag"),
            SELECT,
            FORMAT,
        ),
        examples=(Example("jira fix-versions PROJ --unreleased"), Example("jira fix-versions PROJ --released --counts --json")),
        json_output=True,
        output="a list of {id, name, description, released, archived, releaseDate, counts{fixed,affected,unresolved}}; "
        "counts is null unless --counts.",
        handler=run_fix_versions,
    ),
    Command(
        name="permissions",
        group=GROUP,
        summary="Show what your account may do",
        when="Use before a write to see whether the token's user may do it (EDIT_ISSUES, TRANSITION_ISSUES, ...), instead of reading "
        "the refusal. It answers for your own user only.",
        description="In a project (--project), on an issue (--issue) or in general. --only names the ones you ask about, by key or by name; "
        "an unknown name lists the valid ones.",
        args=(
            Arg(("--project",), "Answer for this project.", metavar="KEY"),
            Arg(("--issue",), "Answer for this issue.", metavar="ISSUE_KEY"),
            Arg(("--only",), "Only these permissions, comma separated: EDIT_ISSUES,TRANSITION_ISSUES.", metavar="NAME,NAME"),
            SELECT,
            FORMAT,
        ),
        examples=(
            Example("jira permissions --issue PROJ-1 --only EDIT_ISSUES,TRANSITION_ISSUES"),
            Example("jira permissions --project PROJ --json"),
        ),
        json_output=True,
        output="a list of {key, name, type, havePermission}.",
        handler=run_permissions,
    ),
    Command(
        name="roles",
        group=GROUP,
        summary="List the roles of a project",
        when="Use to see which project roles exist (Administrators, Developers, ...). The people in a role are shown only with --with-users.",
        description="With --with-users one more request per role lists its actors (people and groups).",
        args=(PROJECT, Arg(("--with-users",), WITH_USERS.format(what="the actors of every role"), kind="flag"), SELECT, FORMAT),
        examples=(Example("jira roles PROJ"), Example("jira roles PROJ --with-users --json")),
        json_output=True,
        output="a list of {id, name, actors}; actors is null unless --with-users, then [{name, displayName, kind}].",
        handler=run_roles,
    ),
    Command(
        name="dashboards",
        group=GROUP,
        summary="List dashboards",
        when="Use to find a dashboard of yours or a favourite one and its address. It holds no issues: use `search` or `issues` for those.",
        description="--mine or --favourite narrows the list; not both. Without either: every dashboard you can see.",
        args=(
            Arg(("--mine",), "Only your own dashboards.", kind="flag"),
            Arg(("--favourite",), "Only your favourite dashboards.", kind="flag"),
            Arg(("--limit",), "Most dashboards to show.", kind="int", default=50, metavar="N"),
            SELECT,
            FORMAT,
        ),
        examples=(Example("jira dashboards"), Example("jira dashboards --favourite --json")),
        json_output=True,
        output="total, dashboards[{id, name, description, view}]; Jira states no description for a dashboard, so it is usually empty.",
        handler=run_dashboards,
    ),
    Command(
        name="server",
        group="Setup and information",
        summary="Show the Jira version and its settings",
        when="Use to see which Jira the CLI talks to: whether it is the 11.3 release line the CLI is written for, and whether attachments, "
        "voting, watching and time tracking are on. `doctor` checks your own setup.",
        description="The base URL is the one you configured.",
        examples=(Example("jira server"), Example("jira server --json")),
        json_output=True,
        output="baseUrl, version, deploymentType, buildNumber, compatibility{status,detail,hint}, settings{attachmentsEnabled, "
        "attachmentUploadLimit, subTasksEnabled, issueLinkingEnabled, votingEnabled, watchingEnabled, unassignedIssuesAllowed, "
        "timeTrackingEnabled, timeTracking{workingHoursPerDay, workingDaysPerWeek, timeFormat, defaultUnit}}.",
        handler=run_server,
    ),
)
