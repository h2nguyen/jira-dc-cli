"""Commands that read issues: issue, issues, report, branch, export, fields."""
from __future__ import annotations

import argparse
import json
import re
from typing import TYPE_CHECKING, Any

from .....domain.drafts import split_list
from .....domain.errors import UsageError
from ..render import key_values, number, one_line, table
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import pretty

if TYPE_CHECKING:
    from .....application.issues import IssueView, SearchResult
    from .....application.search import FindResult, Hit
    from .....domain.model import Issue, User
    from .....domain.reporting import Report

GROUP = "Read"


# -- issue ---------------------------------------------------------------------------------------


def _user_json(user: User | None) -> dict[str, str] | None:
    return None if user is None else {"name": user.name, "displayName": user.display_name, "email": user.email}


def issue_text(view: IssueView) -> str:
    issue = view.issue
    status = issue.status.name if issue.status else ""
    pairs: list[tuple[str, Any]] = []
    if view.users_shown:
        pairs += [("Assignee", issue.assignee.display_name if issue.assignee else ""), ("Reporter", issue.reporter.display_name if issue.reporter else "")]
    pairs += [
        ("Labels", ", ".join(issue.labels)),
        ("Components", ", ".join(issue.components)),
        ("Fix versions", ", ".join(issue.fix_versions)),
        ("Epic", issue.epic_key or issue.parent_key),
        ("Story points", "" if issue.story_points is None else number(issue.story_points)),
        ("Sprint", ", ".join(f"{sprint.name} ({sprint.state})" for sprint in issue.sprints)),
        ("Created", issue.created),
        ("Updated", issue.updated),
    ]
    lines = [f"{issue.key}  [{issue.issue_type} | {status} | {issue.priority}]", issue.summary, "", key_values(pairs)]
    if issue.links:
        lines += ["", "Links:"]
        lines += [f"  {link.phrase} {link.other.key} ({link.other.status}) {link.other.summary}" for link in issue.links]
    if issue.subtasks:
        lines += ["", "Subtasks:"]
        lines += [f"  {task.key} ({task.status}) {task.summary}" for task in issue.subtasks]
    if issue.description.strip():
        lines += ["", "Description:", issue.description.strip()]
    if issue.acceptance.strip():
        lines += ["", "Acceptance criteria:", issue.acceptance.strip()]
    lines += ["", f"Comments: {issue.comment_total}" + ("" if view.comments_shown else " (show with --comments)")]
    if view.comments_shown:
        for comment in issue.comments:
            who = f" {comment.author.display_name}" if comment.author else ""
            lines += [f"--- #{comment.id} {comment.created}{who}", comment.body.strip()]
    return "\n".join(lines)


def run_issue(ctx: Context, args: argparse.Namespace) -> int:
    view = ctx.app.view_issue.execute(args.key, with_comments=args.comments, with_users=args.with_users, want_raw=args.raw)
    if args.raw:
        ctx.print(pretty(view.raw))
    elif ctx.json_mode:
        ctx.print_json(view.issue)
    else:
        ctx.print(issue_text(view))
    return 0


# -- issues --------------------------------------------------------------------------------------

_JSON_FIELDS = {
    "summary": ("summary", lambda i: i.summary),
    "issuetype": ("issueType", lambda i: i.issue_type),
    "status": ("status", lambda i: {"name": i.status.name, "category": i.status.category} if i.status else None),
    "priority": ("priority", lambda i: i.priority),
    "assignee": ("assignee", lambda i: _user_json(i.assignee)),
    "reporter": ("reporter", lambda i: _user_json(i.reporter)),
    "created": ("created", lambda i: i.created),
    "updated": ("updated", lambda i: i.updated),
    "labels": ("labels", lambda i: i.labels),
    "components": ("components", lambda i: i.components),
    "fixVersions": ("fixVersions", lambda i: i.fix_versions),
    "description": ("description", lambda i: i.description),
    "parent": ("parentKey", lambda i: i.parent_key),
    "story_points": ("storyPoints", lambda i: i.story_points),
    "epic_link": ("epicLink", lambda i: i.epic_key),
    "sprint": ("sprints", lambda i: [{"name": s.name, "state": s.state} for s in i.sprints]),
    "acceptance": ("acceptance", lambda i: i.acceptance),
}


def issue_row_json(issue: Issue, fields: list[str], hide_assignee: bool) -> dict[str, Any]:
    row: dict[str, Any] = {"key": issue.key}
    for name in fields:
        if name == "assignee" and hide_assignee:
            continue
        if name in _JSON_FIELDS:
            label, getter = _JSON_FIELDS[name]
            row[label] = getter(issue)
        elif name in issue.extra:
            row[name] = issue.extra[name]
    return row


def issues_text(result: SearchResult) -> str:
    headers = ["KEY", "TYPE", "STATUS"] + ([] if result.assignee_hidden else ["ASSIGNEE"]) + ["SUMMARY"]
    rows = []
    for issue in result.issues:
        cells = [issue.key, issue.issue_type, issue.status.name if issue.status else ""]
        if not result.assignee_hidden:
            cells.append(issue.assignee.display_name if issue.assignee else "")
        rows.append(cells + [issue.summary])
    return table(headers, rows)


def run_issues(ctx: Context, args: argparse.Namespace) -> int:
    extra = [item for item in (args.fields or "").split(",") if item]
    if args.fields and not re.fullmatch(r"[A-Za-z0-9_,]+", args.fields):
        raise UsageError("--fields takes a comma separated list of field names or ids.")
    if args.limit < 1 or args.start_at < 0:
        raise UsageError("--limit must be at least 1 and --start-at must not be negative.")
    if args.jql and not re.search(r"\bproject\b", args.jql, re.IGNORECASE):
        ctx.diagnostics.info("--jql replaces the project query, so this searches every project. Add `project = KEY AND` to stay in one, or use `jira search`.")
    result = ctx.app.search_issues.execute(
        project=args.project or "",
        query=args.jql or "",
        mine=args.mine,
        limit=args.limit,
        start_at=args.start_at,
        fetch_everything=args.all,
        extra_fields=extra,
        hide_assignee=args.no_assignee,
    )
    if args.raw:
        ctx.print(pretty({"startAt": 0, "maxResults": len(result.raw_issues), "total": result.total, "issues": result.raw_issues}))
        return 0
    if ctx.json_mode:
        listed = ["summary", "issuetype", "status", "priority", "assignee", "created", "updated", *extra]
        rows = [issue_row_json(issue, listed, result.assignee_hidden) for issue in result.issues]
        ctx.print_json({"total": result.total, "count": len(rows), "jql": result.jql, "issues": rows})
        return 0
    ctx.print(issues_text(result))
    if len(result.issues) < result.total and not args.all:
        ctx.diagnostics.info(f"Showing {len(result.issues)} of {result.total}. Use --all, or --start-at to page.")
    return 0


# -- search --------------------------------------------------------------------------------------


def hit_json(hit: Hit, *, with_words: bool = True) -> dict[str, Any]:
    issue = hit.issue
    return {
        "key": issue.key,
        "issueType": issue.issue_type,
        "status": {"name": issue.status.name, "category": issue.status.category} if issue.status else None,
        "priority": issue.priority,
        "summary": issue.summary,
        "epicKey": issue.epic_key or None,
        "epicSummary": hit.epic_summary or None,
        "parentKey": issue.parent_key or None,
        "labels": issue.labels,
        "sprints": [{"name": sprint.name, "state": sprint.state} for sprint in issue.sprints],
        "storyPoints": issue.story_points,
        "updated": issue.updated,
        "url": hit.url or None,
        "allWords": hit.all_words if with_words else None,
        "snippet": hit.snippet or None,
        "matched": list(hit.matched),
    }


def _context(hit: Hit) -> str:
    """Where a hit belongs, in one line: the epic, the labels, the points, the sprint."""
    issue = hit.issue
    parts = []
    if issue.epic_key:
        parts.append(f"epic {issue.epic_key}" + (f" {hit.epic_summary}" if hit.epic_summary else ""))
    elif issue.parent_key:
        parts.append(f"subtask of {issue.parent_key}")
    if issue.labels:
        parts.append("labels " + ", ".join(issue.labels))
    if issue.story_points is not None:
        parts.append(f"{number(issue.story_points)} points")
    if issue.sprints:
        parts.append("sprint " + ", ".join(sprint.name for sprint in issue.sprints))
    return " | ".join(parts)


def search_text(result: FindResult, *, with_passages: bool) -> str:
    if not result.hits:
        return ""
    if not with_passages:
        return table(
            ["KEY", "TYPE", "STATUS", "EPIC", "SUMMARY"],
            [(h.issue.key, h.issue.issue_type, h.issue.status.name if h.issue.status else "", h.issue.epic_key or h.issue.parent_key, h.issue.summary) for h in result.hits],
        )
    blocks = []
    for hit in result.hits:
        issue = hit.issue
        lines = [f"{issue.key}  {issue.issue_type} | {issue.status.name if issue.status else ''} | {issue.priority}  {one_line(issue.summary)}"]
        if _context(hit):
            lines.append(f"    {_context(hit)}")
        if hit.snippet:
            lines.append(f"    {hit.snippet}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def run_search(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.find_issues.execute(
        text=args.text or "",
        project=args.project or "",
        all_projects=args.all_projects,
        issue_types=split_list(args.type or ""),
        statuses=split_list(args.status or ""),
        labels=split_list(args.label or ""),
        under=args.under or "",
        sprint=args.sprint or "",
        fix_version=args.fix_version or "",
        since=args.since or "",
        extra_jql=args.jql or "",
        limit=args.limit,
        start_at=args.start_at,
        fetch_everything=args.all,
        match=args.match,
        snippets=args.snippets,
    )
    for note in result.notes:
        ctx.diagnostics.info(note)
    if ctx.json_mode:
        ctx.print_json(
            {
                "total": result.total,
                "count": len(result.hits),
                "startAt": result.start_at,
                "jql": result.jql,
                "scope": result.scope,
                "match": result.match,
                "relaxed": result.relaxed,
                "terms": list(result.terms),
                "ignored": list(result.ignored),
                "notes": result.notes,
                "truncated": result.truncated,
                "hits": [hit_json(hit, with_words=bool(result.terms)) for hit in result.hits],
            }
        )
        return 0
    text = search_text(result, with_passages=args.snippets > 0)
    if text:
        ctx.print(text)
    if len(result.hits) < result.total and not args.all:
        ctx.diagnostics.info(f"Showing {len(result.hits)} of {result.total}, the best match first. Use --limit, --start-at or --all.")
    return 0


# -- report --------------------------------------------------------------------------------------


def report_text(report: Report) -> str:
    def percent(part: float, whole: float) -> str:
        return f"{100 * part / whole:.0f}%" if whole else "-"

    lines = [
        f"Scope: {report.scope}",
        f"Issues (without epics): {report.issues} | done {report.done} ({percent(report.done, report.issues)})",
        f"Story points: {number(report.points)} | done {number(report.done_points)} ({percent(report.done_points, report.points)}) | open {number(report.open_points)}",
        f"Issues without story points: {report.without_points}",
        "",
        table(["STATUS", "ISSUES", "POINTS"], [(row.status, row.issues, number(row.points)) for row in report.by_status]),
    ]
    if report.by_epic:
        lines += [
            "",
            table(
                ["EPIC", "SUMMARY", "ISSUES DONE/TOTAL", "POINTS DONE/TOTAL"],
                [(row.key, row.summary, f"{row.done}/{row.issues}", f"{number(row.done_points)}/{number(row.points)}") for row in report.by_epic],
            ),
        ]
    return "\n".join(lines)


def report_json(report: Report) -> dict[str, Any]:
    """Objects keyed by status name and by epic key, with the `category` and `summary` of each."""
    return {
        "scope": report.scope,
        "issues": report.issues,
        "done": report.done,
        "points": report.points,
        "donePoints": report.done_points,
        "withoutPoints": report.without_points,
        "byStatus": {row.status: {"category": row.category, "issues": row.issues, "points": row.points} for row in report.by_status},
        "byEpic": {
            row.key: {"summary": row.summary, "issues": row.issues, "done": row.done, "points": row.points, "donePoints": row.done_points}
            for row in report.by_epic
        },
    }


def run_report(ctx: Context, args: argparse.Namespace) -> int:
    report = ctx.app.build_report.execute(
        project=args.project or "", sprint=args.sprint or "", fix_version=args.fix_version or "", per_epic=args.epics
    )
    if ctx.json_mode:
        ctx.print_json(report_json(report))
    else:
        ctx.print(report_text(report))
    return 0


# -- branch, export, fields ----------------------------------------------------------------------


def run_branch(ctx: Context, args: argparse.Namespace) -> int:
    name = ctx.app.suggest_branch.execute(args.key, branch_type=args.type or "")
    ctx.print_json({"key": args.key.upper(), "branch": name}) if ctx.json_mode else ctx.print(name)
    return 0


def run_export(ctx: Context, args: argparse.Namespace) -> int:
    dump = ctx.app.export_backlog.execute(project=args.project or "")
    if not args.out:
        ctx.print_json(dump)
        return 0
    ctx.app.files.write_text(args.out, json.dumps(dump, ensure_ascii=False, indent=1) + "\n")
    count = len(dump["issues"])
    if ctx.json_mode:
        ctx.print_json({"file": args.out, "issues": count, "fetchedAt": dump["fetchedAt"]})
    else:
        ctx.print(f"{count} issues written to {args.out}")
    return 0


def run_fields(ctx: Context, args: argparse.Namespace) -> int:
    fields = ctx.app.list_fields.execute(search=args.search or "", custom_only=args.custom, refresh=args.refresh)
    if ctx.json_mode:
        ctx.print_json(fields)
    else:
        ctx.print(table(["ID", "NAME", "CUSTOM"], [(item.id, one_line(item.name), "yes" if item.custom else "no") for item in fields]))
    return 0


COMMANDS = (
    Command(
        name="issue",
        group=GROUP,
        summary="Show one issue",
        when="Use to read one issue whose key you know: status, epic, labels, story points, sprint, acceptance criteria, links and "
        "subtasks. To find the issue first use `search`; add --comments to read the comments, and --with-users only when you "
        "need the people.",
        description="Shows type, status, priority, summary, labels, components, fix versions, epic, story points, sprint, "
        "acceptance criteria, links, subtasks and the number of comments. Custom fields are found by name. "
        "People and mentions are hidden unless you add --with-users.",
        args=(
            Arg(("key",), "Issue key such as PROJ-86.", required=True, metavar="ISSUE_KEY"),
            Arg(("--comments",), "Also show the comments.", kind="flag"),
            Arg(("--with-users",), "Show people (assignee, reporter, comment authors). Refused when JIRA_HIDE_USERS=true.", kind="flag"),
            Arg(("--raw",), "Print Jira's own JSON for the issue (people removed unless --with-users).", kind="flag"),
        ),
        examples=(
            Example("jira issue PROJ-86", "the issue without comments"),
            Example("jira issue PROJ-86 --comments --json", "everything as JSON, for a script or an agent"),
        ),
        json_output=True,
        output="the issue: key, summary, issueType, status{name,category}, priority, labels, components, fixVersions, epicKey, "
        "parentKey, storyPoints, sprints[], description, acceptance, created, updated, assignee/reporter (null unless --with-users), "
        "links[], subtasks[], comments[], commentTotal.",
        handler=run_issue,
    ),
    Command(
        name="issues",
        group=GROUP,
        summary="Search and list issues",
        when="Use to list issues by a JQL query that you write yourself, or your own open issues with --mine. A JQL without `project "
        "= KEY` searches every project. To find issues by words or by a question use `search`.",
        description="Lists the issues of the current project (newest first), your own open issues with --mine, or the result of any JQL query.",
        args=(
            Arg(("--project",), "Project key. Default: the current project (`jira use`).", metavar="KEY"),
            Arg(("--jql",), "A JQL query. Replaces the project query.", metavar="JQL"),
            Arg(("--mine",), "Your unresolved issues: assignee = currentUser() AND resolution = Unresolved.", kind="flag"),
            Arg(("--limit",), "Issues per page.", kind="int", default=50, metavar="N"),
            Arg(("--start-at",), "Index of the first issue.", kind="int", default=0, metavar="N"),
            Arg(("--all",), "Fetch every page instead of one (ignores --limit and --start-at).", kind="flag"),
            Arg(("--fields",), "More fields to load, comma separated: labels, fixVersions, components, description, reporter, parent, a customfield_ id, or story_points, epic_link, sprint, acceptance.", metavar="a,b,c"),
            Arg(("--no-assignee",), "Leave the assignee out. Always on when JIRA_HIDE_USERS=true.", kind="flag"),
            Arg(("--raw",), "Print Jira's own JSON.", kind="flag"),
        ),
        examples=(
            Example("jira issues --project PROJ --limit 20"),
            Example("jira issues --mine"),
            Example("jira issues --jql 'project = PROJ AND status != Done ORDER BY updated DESC' --all --json"),
        ),
        json_output=True,
        output="total, count, jql and issues[]: each with key, summary, issueType, status, priority, assignee (omitted when hidden), "
        "created, updated and the fields asked for with --fields.",
        handler=run_issues,
    ),
    Command(
        name="search",
        group=GROUP,
        summary="Find issues by words, also a whole question",
        description="Turns what you type into a Jira text search: words and \"quoted phrases\"; filler words in English and German (how, do, the, wie, ich ...) "
        "and the punctuation that Jira cannot parse are left out, and the answer says which. Case does not matter, stems match (roles finds role) and * is "
        "a wildcard. By default every word must occur; when fewer than five issues hold all of them, the issues that hold some of them are shown "
        "instead, the best match first, and the answer says so and marks the ones that hold every word (--match any asks for the best match directly, "
        "--match all never loosens). It searches the current project, or the "
        "one named with --project, never the whole site unless you add --all-projects. Each hit has its epic, labels, status, story points and sprint. "
        "--snippets N shows the passage around your words for the first N hits, so that you need not open an issue to judge it.",
        when="Use first when you do not know which issue holds the information: a question, a topic, a requirement, a role, a component. It returns for "
        "each hit where it belongs and, with --snippets, the passage around your words, so that you can choose without reading issues. Read a hit with "
        "`issue`. To list by status, sprint or epic without words, use --status, --sprint or --under; to run your own JQL use `issues`.",
        args=(
            Arg(("text",), "Words or a question; several words need no quotes. \"Quotes\" keep a phrase together (put the whole text in single quotes then); a word in quotes is kept even when it is filler.", kind="words", metavar="TEXT"),
            Arg(("--project",), "Project key. Default: the current project (`jira use`).", metavar="KEY"),
            Arg(("--all-projects",), "Search every project of the site, not only one.", kind="flag"),
            Arg(("--type",), "Only these issue types, comma separated: Story, Bug, Epic ...", metavar="TYPE"),
            Arg(("--status",), "Only these statuses, comma separated: \"To Do\", \"In Progress\" ...", metavar="STATUS"),
            Arg(("--label",), "Only issues with one of these labels, comma separated.", metavar="LABEL"),
            Arg(("--under",), "Only the issues of this epic (its stories) or of this story (its subtasks).", metavar="ISSUE_KEY"),
            Arg(("--sprint",), "`active` for the open sprints, or an exact sprint name.", metavar="active|NAME"),
            Arg(("--fix-version",), "Only one fix version.", metavar="NAME"),
            Arg(("--since",), "Only issues changed since a date (YYYY-MM-DD) or a time ago such as 7d, 2w, 12h.", metavar="DATE"),
            Arg(("--jql",), "A condition of your own in JQL, added with AND (an ORDER BY in it is kept).", metavar="JQL"),
            Arg(("--match",), "`all`: every word must occur. `any`: one is enough, best match first. `auto`: all, and when fewer than five issues hold all, any as well.", kind="choice", choices=("auto", "all", "any"), default="auto"),
            Arg(("--snippets",), "Show the passage around the words for the first N hits (up to 25). The text comes with the search: no extra request.", kind="int", default=0, metavar="N"),
            Arg(("--limit",), "Hits to show.", kind="int", default=10, metavar="N"),
            Arg(("--start-at",), "Index of the first hit.", kind="int", default=0, metavar="N"),
            Arg(("--all",), "Show every hit instead of one page (ignores --limit and --start-at).", kind="flag"),
        ),
        examples=(
            Example("jira search 'how do I export the monthly report for ACME?' --snippets 3 --json", "a question works: filler words are dropped and reported"),
            Example("jira search 'audit trail' --status 'To Do' --type Story"),
            Example("jira search --under PROJ-5", "the stories of an epic, newest change first"),
            Example("jira search roles --all-projects --limit 20"),
        ),
        notes=(
            "Jira does not translate: words in another language than the issues find nothing (the answer says what to try).",
            "The hits come in Jira's order of relevance when you give words, and newest change first when you only filter.",
            "People are never asked for: no assignee, reporter or comment author appears, and mentions and e-mail addresses in the text are redacted.",
        ),
        json_output=True,
        output="total, count, startAt, jql, scope, match (all or any), relaxed (true when fewer than five issues held every word, so the issues that hold "
        "some of them are shown too), terms[] and ignored[] (what was searched "
        "for and what was left out), notes[] (what the person at the terminal is told), truncated and hits[]: key, issueType, status{name,category}, "
        "priority, summary, epicKey, epicSummary, parentKey, labels[], sprints[], storyPoints, updated, url, allWords (true when the issue holds every "
        "word; null when that is not known), snippet and matched[] (with --snippets: the passage around the words, and which words the issue holds).",
        handler=run_search,
    ),
    Command(
        name="report",
        group=GROUP,
        summary="Progress by status, sprint, fix version and epic",
        when="Use for the progress of a sprint, a fix version or the whole project: issues and story points by status, and per epic "
        "with --epics. It counts, it does not list: use `search` or `issues` to see the issues.",
        description="Counts issues and story points by status, optionally per epic. Epics are not counted themselves. "
        "\"Done\" means the status category done, so it follows your workflow, not a status name.",
        args=(
            Arg(("--project",), "Project key. Default: the current project.", metavar="KEY"),
            Arg(("--sprint",), "`active` for the open sprints, or an exact sprint name.", metavar="active|NAME"),
            Arg(("--fix-version",), "Only one fix version.", metavar="NAME"),
            Arg(("--epics",), "Add one row per epic (issues and points done/total).", kind="flag"),
        ),
        examples=(Example("jira report --sprint active --epics"), Example("jira report --fix-version 'Release 1.0' --json")),
        json_output=True,
        output="scope, issues, done, points, donePoints, withoutPoints, byStatus{STATUS: {category,issues,points}}, "
        "byEpic{EPIC_KEY: {summary,issues,done,points,donePoints}}.",
        handler=run_report,
    ),
    Command(
        name="branch",
        group=GROUP,
        summary="Print a branch name for an issue",
        when="Use when you start work on an issue: it prints the branch name that the working agreement asks for "
        "(feature/PROJ-86-slug). Nothing is created.",
        description="Builds TYPE/KEY-slug from the issue summary. The type is bugfix for a Bug and feature for everything else. "
        "Nothing is created: use it as in `git switch -c \"$(jira branch PROJ-86)\"`.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("--type",), "Override the type.", kind="choice", choices=("feature", "bugfix", "hotfix", "chore")),
        ),
        examples=(Example("jira branch PROJ-86"), Example("jira branch PROJ-86 --type hotfix")),
        json_output=True,
        output="key and branch.",
        handler=run_branch,
    ),
    Command(
        name="export",
        group=GROUP,
        summary="Export a project's backlog as JSON",
        when="Use to read the whole backlog in one call as a JSON dump without personal data (it is also what the mirror comparison "
        "reads), or to keep a copy to search offline. For a few issues use `search`; to work on Markdown files use `pull`.",
        description="Writes every issue of a project in the stable \"backlog dump\" format, without any personal data: summary, type, "
        "status, priority, labels, components, fix versions, description, epic link, story points, acceptance criteria and sprints. "
        "It refuses to write a partial dump. Other tools (for example a markdown mirror comparison) read it.",
        args=(
            Arg(("--project",), "Project key. Default: the current project.", metavar="KEY"),
            Arg(("--out",), "Write the dump to this file instead of printing it.", metavar="FILE"),
        ),
        examples=(Example("jira export --project PROJ --out tmp/jira-sync/PROJ.json"),),
        json_output=True,
        output="without --out the dump itself: fetchedAt, fieldIds{} and issues{KEY: ...}; with --out file, issues and fetchedAt.",
        handler=run_export,
    ),
    Command(
        name="fields",
        group=GROUP,
        summary="List Jira fields and their ids",
        when="Use to find the id of a custom field, to ask for it in `issues` or to put it into a JIRA_*_FIELD setting. Story points, "
        "epic link, sprint and acceptance criteria are found by name: you do not need it for them.",
        description="Use it to find a custom field id, for example for the --fields option of `issues` or for a JIRA_*_FIELD setting. The list is cached for a day.",
        args=(
            Arg(("--search",), "Only fields whose name or id contains this text.", metavar="TEXT"),
            Arg(("--custom",), "Only custom fields.", kind="flag"),
            Arg(("--refresh",), "Ignore the cache and ask Jira again.", kind="flag"),
        ),
        examples=(Example("jira fields --search 'story points'"),),
        json_output=True,
        output="a list of {id, name, custom}.",
        handler=run_fields,
    ),
)
