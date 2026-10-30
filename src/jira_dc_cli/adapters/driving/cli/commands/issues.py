"""Commands that read issues: issue, issues, search, report, branch, export, fields."""
from __future__ import annotations

import argparse
import json
import re
from typing import TYPE_CHECKING, Any

from .....application.reading import ReadOptions
from .....domain.drafts import split_list
from .....domain.errors import UsageError
from .....domain.issueparts import PARTS, split_keys
from ..listing import FORMAT, SELECT, print_list
from ..parser import usage_hint
from ..render import number, one_line, table
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import QUERY_NOTE, pretty, query_option
from ._issueview import issue_json, result_json, result_text

if TYPE_CHECKING:
    from .....application.issues import SearchResult
    from .....application.search import FindResult, Hit
    from .....domain.model import Issue, User
    from .....domain.reporting import Report

GROUP = "Read"


# -- issue ---------------------------------------------------------------------------------------


def _user_json(user: User | None) -> dict[str, str] | None:
    return None if user is None else {"name": user.name, "displayName": user.display_name, "email": user.email}


def run_issue(ctx: Context, args: argparse.Namespace) -> int:
    keys = split_keys(args.keys)
    if not keys:
        message = "the following arguments are required: ISSUE_KEY"
        raise UsageError(message, hint=usage_hint(ctx.command, message))
    result = ctx.app.reading.read_issues.execute(
        keys,
        ReadOptions(
            with_comments=args.comments,
            with_users=args.with_users,
            want_raw=args.raw,
            only=args.only or "",
            max_chars=args.max_chars,
            outline=args.outline,
            section=args.section or "",
            rendered=args.rendered,
            history=args.history,
        ),
    )
    if args.raw:
        ctx.print(pretty(result.issues[0].view.raw))
    elif ctx.json_mode:
        cuts_asked = args.max_chars is not None
        ctx.print_json(result_json(result, cuts_asked=cuts_asked) if result.many else issue_json(result.issues[0], cuts_asked=cuts_asked))
    else:
        ctx.print(result_text(result))
    for reading in result.issues:
        if reading.history is not None and not reading.history_complete:
            ctx.diagnostics.info(
                f"Jira sent only part of the history of {reading.issue.key}: Data Center cannot page it, so some status changes may be missing."
            )
    return 4 if result.errors else 0


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
    if args.raw and (args.select or args.format != "table"):
        raise UsageError("--raw prints Jira's own JSON, which has no columns.", hint="Leave out --select and --format.")
    query = query_option(ctx, args.jql, "JQL")
    if query and not args.filter and not re.search(r"\bproject\b", query, re.IGNORECASE):
        ctx.diagnostics.info("--jql replaces the project query, so this searches every project. Add `project = KEY AND` to stay in one, or use `jira search`.")
    result = ctx.app.search_issues.execute(
        project=args.project or "",
        query=query or "",
        mine=args.mine,
        limit=args.limit,
        start_at=args.start_at,
        fetch_everything=args.all,
        extra_fields=extra,
        hide_assignee=args.no_assignee,
        filter_id=args.filter or "",
    )
    if args.raw:
        ctx.print(pretty({"startAt": 0, "maxResults": len(result.raw_issues), "total": result.total, "issues": result.raw_issues}))
        return 0
    listed = ["summary", "issuetype", "status", "priority", "assignee", "created", "updated", *extra]
    rows = [issue_row_json(issue, listed, result.assignee_hidden) for issue in result.issues]
    columns = [("KEY", "key"), ("TYPE", "issueType"), ("STATUS", "status.name")]
    if not result.assignee_hidden:
        columns.append(("ASSIGNEE", "assignee.displayName"))
    columns.append(("SUMMARY", "summary"))
    document = {"total": result.total, "count": len(rows), "jql": result.jql, "issues": rows}
    print_list(ctx, args, payload=document, rows=rows, list_key="issues", columns=columns, plain=lambda: issues_text(result))
    if not ctx.json_mode and len(result.issues) < result.total and not args.all:
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
        text=query_option(ctx, args.text, "search text") or "",
        project=args.project or "",
        all_projects=args.all_projects,
        issue_types=split_list(args.type or ""),
        statuses=split_list(args.status or ""),
        labels=split_list(args.label or ""),
        under=args.under or "",
        sprint=args.sprint or "",
        fix_version=args.fix_version or "",
        since=args.since or "",
        extra_jql=query_option(ctx, args.jql, "JQL") or "",
        limit=args.limit,
        start_at=args.start_at,
        fetch_everything=args.all,
        match=args.match,
        snippets=args.snippets,
        phrases=args.phrase or (),
        filter_id=args.filter or "",
    )
    for note in result.notes:
        ctx.diagnostics.info(note)
    rows = [hit_json(hit, with_words=bool(result.terms)) for hit in result.hits]
    document = {
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
        "hits": rows,
    }
    columns = [("KEY", "key"), ("TYPE", "issueType"), ("STATUS", "status.name"), ("EPIC", "epicKey"), ("SUMMARY", "summary")]
    text = search_text(result, with_passages=args.snippets > 0)
    if text or ctx.json_mode or args.select or args.format != "table":
        print_list(ctx, args, payload=document, rows=rows, list_key="hits", columns=columns, plain=lambda: text)
    if not ctx.json_mode and len(result.hits) < result.total and not args.all:
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
    rows = [{"id": item.id, "name": item.name, "custom": item.custom} for item in fields]

    def plain() -> str:
        return table(["ID", "NAME", "CUSTOM"], [(item.id, one_line(item.name), "yes" if item.custom else "no") for item in fields])

    print_list(ctx, args, payload=rows, rows=rows, list_key=None, columns=[("ID", "id"), ("NAME", "name"), ("CUSTOM", "custom")], plain=plain)
    return 0


COMMANDS = (
    Command(
        name="issue",
        group=GROUP,
        summary="Show one or several issues",
        when="Use to read issues whose keys you know. Give several keys to read them with one request. To find the issue first use `search`. "
        "A long issue is read in parts: --only, --max-chars, --outline, --section. Add --comments when the discussion matters, "
        "--with-users only when you need the people.",
        description="Shows type, status, priority, summary, labels, components, fix versions, epic, story points, sprint, acceptance criteria, links, "
        "subtasks, attachments and the number of comments. Custom fields are found by name. People and mentions are hidden unless you add "
        "--with-users. A key that does not exist is reported on its own; the other issues are still shown.",
        args=(
            Arg(("keys",), "Issue key such as PROJ-86; several keys (blanks or commas) are read with one request.", kind="words", required=True, metavar="ISSUE_KEY"),
            Arg(("--comments",), "Also show the comments.", kind="flag"),
            Arg(("--with-users",), "Show people (assignee, reporter, comment authors). Refused when JIRA_HIDE_USERS=true.", kind="flag"),
            Arg(("--raw",), "Print Jira's own JSON for the issue (people removed unless --with-users).", kind="flag"),
            Arg(("--only",), "Only these parts, comma separated: " + ", ".join(PARTS) + ".", metavar="a,b,c"),
            Arg(("--max-chars",), "Cut each long text after about N characters, at the end of a paragraph, and say so.", kind="int", metavar="N"),
            Arg(("--outline",), "Show the headings of the description with their size, not its text.", kind="flag"),
            Arg(("--section",), "Show only the part of the description under this heading (its text, or its number from --outline).", metavar="TITLE|N"),
            Arg(("--rendered",), "Also show the description and the acceptance criteria as Jira renders them.", kind="flag"),
            Arg(("--history",), "Also show the status changes with their time, without people.", kind="flag"),
        ),
        examples=(
            Example("jira issue PROJ-86", "the issue without comments"),
            Example("jira issue PROJ-86 --comments --json", "everything as JSON, for a script or an agent"),
            Example("jira issue PROJ-86 PROJ-87 PROJ-90 --only summary,status --json", "three issues with one request"),
            Example("jira issue PROJ-86 --outline", "the headings of a long description; then --section TITLE reads one part"),
            Example("jira issue PROJ-86 --history", "when the status changed"),
        ),
        notes=(
            "Several keys are read with one search and come back in the order given. A key that does not exist, or that you may not see, "
            "gets its own entry (`errors` in the JSON, a line in the text) and the exit code is 4; the issues that were found are still shown. "
            "One key prints the issue itself, several print {count, issues, errors}; the number of keys given decides, not how many of them "
            "differ (`PROJ-1 PROJ-1` is a list of one issue).",
            "--max-chars cuts every long text at the end of a paragraph, never in a word and without splitting a code block (a text that opens "
            "with one is cut at a line and the block is closed): the JSON then has truncated and "
            "totalChars by field (a comment is comments.ID), the text a line under the text that was cut. --only comments shows the comments, as --comments does.",
            "--outline and --section read the description of one issue. A heading is `h1.` to `h6.` or Markdown `#`; in a text that has wiki "
            "headings a `#` line beside other list lines is a numbered list.",
            "--history reads the changelog with the issue (`expand=changelog`, no extra request). Jira Data Center may send only part of a long "
            "history and cannot page it: historyComplete says so.",
            "--rendered shows the text as Jira renders it, so that markup that Jira renders wrongly is visible. A mention is [~user] unless --with-users.",
        ),
        json_output=True,
        output="the issue: key, summary, issueType, status{name,category}, priority, labels, components, fixVersions, epicKey, "
        "parentKey, storyPoints, sprints[], description, acceptance, created, updated, assignee/reporter (null unless --with-users), "
        "links[], subtasks[], comments[], commentTotal, attachments[], attachmentTotal. Several keys: {count, issues[], errors[{key,code,message}]}. "
        "With --only: key and those keys. Options add truncated{} and totalChars{} (--max-chars), outline[{index,level,title,chars}] "
        "(--outline, description is then null), section{} (--section), renderedDescription and renderedAcceptance (--rendered), "
        "history[{created,from,to}] and historyComplete (--history).",
        handler=run_issue,
    ),
    Command(
        name="issues",
        group=GROUP,
        summary="Search and list issues",
        when="Use to list issues by a JQL query that you write yourself, by a saved filter (--filter), or your own open issues with --mine. A JQL "
        "without `project = KEY` searches every project. To find issues by words or by a question use `search`.",
        description="Lists the issues of the current project (newest first), your own open issues with --mine, a saved filter, or the result of any JQL query.",
        args=(
            Arg(("--project",), "Project key. Default: the current project (`jira use`).", metavar="KEY"),
            Arg(("--jql",), "A JQL query that replaces the project query. `@FILE` reads it from a file, `-` from standard input.", metavar="JQL"),
            Arg(("--filter",), "A saved filter by its id (`jira filters` lists yours); --jql narrows it.", metavar="ID"),
            Arg(("--mine",), "Your unresolved issues: assignee = currentUser() AND resolution = Unresolved.", kind="flag"),
            Arg(("--limit",), "Issues per page.", kind="int", default=50, metavar="N"),
            Arg(("--start-at",), "Index of the first issue.", kind="int", default=0, metavar="N"),
            Arg(("--all",), "Fetch every page instead of one (ignores --limit and --start-at).", kind="flag"),
            Arg(("--fields",), "More fields to load, comma separated: labels, fixVersions, components, description, reporter, parent, a customfield_ id, or story_points, epic_link, sprint, acceptance.", metavar="a,b,c"),
            Arg(("--no-assignee",), "Leave the assignee out. Always on when JIRA_HIDE_USERS=true.", kind="flag"),
            Arg(("--raw",), "Print Jira's own JSON.", kind="flag"),
            SELECT,
            FORMAT,
        ),
        examples=(
            Example("jira issues --project PROJ --limit 20"),
            Example("jira issues --mine"),
            Example("jira issues --jql 'project = PROJ AND status != Done ORDER BY updated DESC' --all --json"),
            Example("jira issues --jql '@query.jql' --all --json", "the query in a file: the form that survives Windows PowerShell 5.1"),
            Example("jira issues --filter 10000 --format md", "run a saved filter"),
            Example("jira issues --all --json --select key,status.name,summary", "only some keys of every issue"),
        ),
        notes=(QUERY_NOTE,),
        json_output=True,
        output="total, count, jql and issues[]: each with key, summary, issueType, status, priority, assignee (omitted when hidden), "
        "created, updated and the fields asked for with --fields; with --select only those (dotted) keys of every issue.",
        handler=run_issues,
    ),
    Command(
        name="search",
        group=GROUP,
        summary="Find issues by words, also a whole question",
        description="Turns what you type into a Jira text search: words and \"quoted phrases\"; filler words (English and German) and punctuation that Jira "
        "cannot parse are left out, and the answer says which. Stems match (roles finds role) and * is a wildcard. Every word must occur; when fewer "
        "than five issues hold all of them, those that hold some are shown too, the best match first (--match any or all forces one way). The scope is "
        "the current project, or --project, or the whole site with --all-projects. A hit has its epic, labels, status, story points and sprint; "
        "--snippets N adds the passage around your words.",
        when="Use first when you do not know which issue holds the information: a question, a topic, a requirement, a role, a component. Each hit says "
        "where it belongs and, with --snippets, shows the passage around your words, so that you can choose without reading issues. Read a hit with "
        "`issue`. To list by status, sprint, epic or saved filter without words use --status, --sprint, --under or --filter; to run your own JQL use `issues`.",
        args=(
            Arg(("text",), "Words or a question; several words need no quotes. \"Quotes\" keep a phrase together (single quotes around the whole text then); a word in quotes is kept even when it is filler. `@FILE` reads the text from a file, `-` from standard input.", kind="words", metavar="TEXT"),
            Arg(("--project",), "Project key. Default: the current project (`jira use`).", metavar="KEY"),
            Arg(("--phrase",), "An exact phrase, without double quotes (PowerShell 5.1 removes them); repeat for more.", kind="repeat", metavar="TEXT"),
            Arg(("--all-projects",), "Search every project of the site, not only one.", kind="flag"),
            Arg(("--type",), "Only these issue types, comma separated: Story, Bug, Epic ...", metavar="TYPE"),
            Arg(("--status",), "Only these statuses, comma separated: \"To Do\", \"In Progress\" ...", metavar="STATUS"),
            Arg(("--label",), "Only issues with one of these labels, comma separated.", metavar="LABEL"),
            Arg(("--under",), "Only the issues of this epic (its stories) or of this story (its subtasks).", metavar="ISSUE_KEY"),
            Arg(("--sprint",), "`active` for the open sprints, or an exact sprint name.", metavar="active|NAME"),
            Arg(("--fix-version",), "Only one fix version.", metavar="NAME"),
            Arg(("--since",), "Only issues changed since a date (YYYY-MM-DD) or a time ago such as 7d, 2w, 12h.", metavar="DATE"),
            Arg(("--filter",), "Only the issues of this saved filter, by id (`jira filters` lists yours).", metavar="ID"),
            Arg(("--jql",), "A condition of your own in JQL, added with AND (an ORDER BY in it is kept); `@FILE` or `-` reads it from a file or standard input.", metavar="JQL"),
            Arg(("--match",), "`all`: every word must occur. `any`: one is enough, best match first. `auto`: all, and any as well when fewer than five issues hold all.", kind="choice", choices=("auto", "all", "any"), default="auto"),
            Arg(("--snippets",), "Show the passage around the words for the first N hits (up to 25); no extra request.", kind="int", default=0, metavar="N"),
            Arg(("--limit",), "Hits to show.", kind="int", default=10, metavar="N"),
            Arg(("--start-at",), "Index of the first hit.", kind="int", default=0, metavar="N"),
            Arg(("--all",), "Show every hit instead of one page (ignores --limit and --start-at).", kind="flag"),
            SELECT,
            FORMAT,
        ),
        examples=(
            Example("jira search 'how do I export the monthly report for ACME?' --snippets 3 --json", "a question works: filler words are dropped and reported"),
            Example("jira search 'audit trail' --status 'To Do' --type Story"),
            Example("jira search --under PROJ-5", "the stories of an epic, newest change first"),
            Example("jira search roles --all-projects --limit 20"),
            Example("jira search '@question.txt' --snippets 3", "the text in a file: the form that survives Windows PowerShell 5.1"),
            Example("jira search --phrase 'audit trail' --phrase 'append only'", "exact phrases without double quotes"),
        ),
        notes=(
            "Jira does not translate: words in another language than the issues find nothing (the answer says what to try).",
            "The hits come in Jira's order of relevance when you give words, and newest change first when you only filter.",
            "People are never asked for: no assignee, reporter or comment author appears, and mentions and e-mail addresses in the text are redacted.",
            "--filter adds a saved filter like any other condition: with a project in force (the current one, or --project) the filter is searched inside it only; add --all-projects for all of it.",
            QUERY_NOTE,
        ),
        json_output=True,
        output="total, count, startAt, jql, scope, match, relaxed, terms[], ignored[], notes[], truncated and hits[]: key, issueType, status{name,category}, "
        "priority, summary, epicKey, epicSummary, parentKey, labels[], sprints[], storyPoints, updated, url, allWords, snippet, matched[]. relaxed: fewer "
        "than five issues held every word, so those that hold some are shown too; allWords: the issue holds every word (null: not known); snippet and "
        "matched[] only with --snippets. --select keeps only those (dotted) keys of every hit.",
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
        description="Builds TYPE/KEY-slug from the issue summary as `jira issue` shows it: mentions and e-mail addresses are left out. The type is "
        "bugfix for a Bug and feature for everything else. "
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
            SELECT,
            FORMAT,
        ),
        examples=(Example("jira fields --search 'story points'"), Example("jira fields --custom --format tsv --select id,name")),
        json_output=True,
        output="a list of {id, name, custom}; with --select only those keys of every field.",
        handler=run_fields,
    ),
)
