"""Commands about projects: projects, projects-admin, project, metadata."""
from __future__ import annotations

import argparse
import math
from typing import TYPE_CHECKING, Sequence

from .....domain.errors import UsageError
from ..render import key_values, table
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import pretty

if TYPE_CHECKING:
    from .....domain.model import Project

GROUP = "Projects"


def _lead_name(project: Project) -> str:
    return (project.lead.display_name or project.lead.name) if project.lead else ""


def projects_text(projects: Sequence[Project]) -> str:
    show_lead = any(project.lead for project in projects)
    headers = ["KEY", "NAME", "TYPE"] + (["LEAD"] if show_lead else [])
    rows = [[project.key, project.name, project.project_type] + ([_lead_name(project)] if show_lead else []) for project in projects]
    return table(headers, rows)


def run_projects(ctx: Context, args: argparse.Namespace) -> int:
    projects = ctx.app.list_projects.execute()
    ctx.print_json(projects) if ctx.json_mode else ctx.print(projects_text(projects))
    return 0


def run_projects_admin(ctx: Context, args: argparse.Namespace) -> int:
    if not math.isfinite(args.timeout) or args.timeout < 1:
        raise UsageError("--timeout must be a number of seconds, 1 or more.")
    if args.retry < 0:
        raise UsageError("--retry must be zero or a positive number.")
    result = ctx.app.list_admin_projects.execute(
        timeout=float(args.timeout), retries=args.retry, progress=not args.quiet, fail_fast=args.fail_fast
    )
    if ctx.json_mode:
        ctx.print_json(
            {
                "checked": result.checked,
                "admin": len(result.projects),
                "failed": [{"key": key, "message": message} for key, message in result.failed],
                "projects": result.projects,
            }
        )
    else:
        ctx.print(projects_text(result.projects))
    if not args.quiet:
        ctx.diagnostics.info(f"Finished: checked={result.checked} admin={len(result.projects)} failed={len(result.failed)}")
    return 0


def run_project(ctx: Context, args: argparse.Namespace) -> int:
    project = ctx.app.show_project.execute(args.key or "")
    if ctx.json_mode:
        ctx.print_json(project)
        return 0
    ctx.print(
        key_values(
            [
                ("Key", project.key),
                ("Name", project.name),
                ("Type", project.project_type),
                ("Lead", _lead_name(project)),
                ("Description", project.description),
                ("Issue types", ", ".join(project.issue_types)),
            ]
        )
    )
    return 0


def run_metadata(ctx: Context, args: argparse.Namespace) -> int:
    ctx.print(pretty(ctx.app.show_create_metadata.execute(args.project, args.issue_type_id or "")))
    return 0


COMMANDS = (
    Command(
        name="projects",
        group=GROUP,
        summary="List the projects you can see",
        when="Use to see which projects you can read. For the one you work in, select it once with `use KEY`.",
        description="Key, name and type of every accessible project. The lead is shown unless JIRA_HIDE_USERS=true.",
        examples=(Example("jira projects"),),
        json_output=True,
        output="a list of {key, name, projectType, lead, description, issueTypes}.",
        handler=run_projects,
    ),
    Command(
        name="projects-admin",
        group=GROUP,
        summary="List the projects you administer",
        when="Use to see the projects you administer, for example before a bulk import.",
        description="Checks the Administer Projects permission of every accessible project, one request each, so it takes a while on "
        "a large instance. Progress goes to stderr.",
        args=(
            Arg(("--timeout",), "Seconds to wait for each permission request.", kind="float", default=15.0, metavar="SECONDS"),
            Arg(("--retry",), "Retries of each permission request.", kind="int", default=0, metavar="COUNT"),
            Arg(("--quiet",), "Hide progress messages.", kind="flag"),
            Arg(("--fail-fast",), "Stop at the first failed check.", kind="flag"),
        ),
        examples=(Example("jira projects-admin --quiet"),),
        json_output=True,
        output="checked, admin, failed[{key,message}], projects[].",
        handler=run_projects_admin,
    ),
    Command(
        name="project",
        group=GROUP,
        summary="Show one project",
        when="Use to see the issue types, the lead and the description of one project.",
        description="Key, name, type, lead, description and issue types. Without a key the current project is used (see `jira use`).",
        args=(Arg(("key",), "Project key. Default: the current project.", metavar="PROJECT_KEY"),),
        examples=(Example("jira project PROJ"),),
        json_output=True,
        output="{key, name, projectType, lead, description, issueTypes[]}.",
        handler=run_project,
    ),
    Command(
        name="metadata",
        group=GROUP,
        summary="Show what a project can create",
        when="Use before you create an issue to see which issue types a project accepts and which fields each requires.",
        description="Without an issue type id it lists the issue types the project can create. With one it returns the create-field "
        "metadata of that type (which fields exist and which are required). The answer is Jira's own JSON and is always printed as JSON.",
        args=(
            Arg(("project",), "Project key.", required=True, metavar="PROJECT_KEY"),
            Arg(("issue_type_id",), "Issue type id.", metavar="ISSUE_TYPE_ID"),
        ),
        examples=(Example("jira metadata PROJ"), Example("jira metadata PROJ 10200")),
        json_output=True,
        output="Jira's createmeta document.",
        handler=run_metadata,
    ),
)
