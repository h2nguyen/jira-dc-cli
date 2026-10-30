"""Commands of the collab area: what people do together on an issue (watchers, votes, work logged, web links)."""
from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any, Sequence

from .....domain.worktime import format_seconds
from ..listing import FORMAT, SELECT, Column, print_list, structured_text
from ..render import table, to_jsonable
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import pretty

if TYPE_CHECKING:
    from .....application.collab import LogWorkResult, RemoteLinkResult, VoteResult, WatchResult
    from .....domain.collab import Watchers, Worklog

READ = "Read"
WORK = "Work on an issue"

ISSUE_KEY = Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY")
DRY_RUN = Arg(("--dry-run",), "Show what would be sent and check that the issue exists. Sends nothing.", kind="flag")
UNCHANGED_SINCE = Arg(
    ("--if-unchanged-since",),
    "Refuse (exit code 3) when the issue changed in Jira after this time (ISO 8601). A check just before the write, not a lock.",
    metavar="ISO",
)
WITH_USERS = "Show {what} (people). Refused when JIRA_HIDE_USERS=true."

WORKLOG_COLUMNS: list[Column] = [
    ("ID", "id"),
    ("STARTED", "started"),
    ("TIME SPENT", "timeSpent"),
    ("SECONDS", "timeSpentSeconds"),
    ("COMMENT", "comment"),
]
LINK_COLUMNS: list[Column] = [
    ("ID", "id"),
    ("TITLE", "title"),
    ("URL", "url"),
    ("RELATIONSHIP", "relationship"),
    ("APPLICATION", "application"),
    ("GLOBAL ID", "globalId"),
]
OPTIONAL_LINK_COLUMNS = ("application", "globalId")  # left out of the text when no link has them


def _count(number: int, word: str) -> str:
    return f"{number} {word}" + ("" if number == 1 else "s")


def _key(args: argparse.Namespace) -> str:
    return args.key.strip().upper()


def _dry_run_payload(payload: Any, query: dict[str, str]) -> str:
    """What a dry run says is sent: the body, when there is one, and the query."""
    lines = [pretty(payload)] if payload is not None else []
    if query:
        lines.append("Query: " + json.dumps(query, ensure_ascii=False))
    return "\n".join(lines)


# -- watchers ---------------------------------------------------------------------------------------------


def watchers_text(key: str, found: Watchers, with_users: bool) -> str:
    head = f"{key}: {_count(found.count, 'watcher')}, you are {'' if found.watching else 'not '}watching"
    if found.users:
        return head + "\n" + table(["NAME", "DISPLAY NAME"], [(user.name, user.display_name) for user in found.users])
    if with_users and found.count:
        return head + "\nJira lists the watchers only to people who may see them (permission View Voters and Watchers)."
    return head + ("\nWho they are: add --with-users." if found.count and not with_users else "")


def run_watchers(ctx: Context, args: argparse.Namespace) -> int:
    found = ctx.app.collab.watchers.execute(args.key, with_users=args.with_users)
    if ctx.json_mode:
        ctx.print_json({"key": _key(args), "count": found.count, "watching": found.watching, "watchers": found.users})
    else:
        ctx.print(watchers_text(_key(args), found, args.with_users))
    return 0


def watch_text(result: WatchResult) -> str:
    who = result.user or "you"
    adding = result.action == "added"
    if result.dry_run:
        change = f"{who} would be {'added to' if adding else 'removed from'} the watchers of {result.key}"
        state = f"you {'watch' if result.watching else 'do not watch'} it now"
        return f"Dry run: {change} ({state}); nothing was sent\n{_dry_run_payload(result.payload, result.query)}"
    change = f"{'Added' if adding else 'Removed'} {who} {'to' if adding else 'from'} the watchers of {result.key}"
    return change + ("." if result.watch_count is None else f" ({_count(result.watch_count, 'watcher')} now).")


def run_watch(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.collab.watch.execute(args.key, user=args.user or "", remove=args.remove, dry_run=args.dry_run)
    ctx.print_json(result) if ctx.json_mode else ctx.print(watch_text(result))
    return 0


# -- votes ------------------------------------------------------------------------------------------------


def run_votes(ctx: Context, args: argparse.Namespace) -> int:
    votes = ctx.app.collab.votes.execute(args.key)
    if ctx.json_mode:
        ctx.print_json({"key": _key(args), "count": votes.count, "hasVoted": votes.has_voted})
    else:
        ctx.print(f"{_key(args)}: {_count(votes.count, 'vote')}, you have {'' if votes.has_voted else 'not '}voted")
    return 0


def vote_text(result: VoteResult) -> str:
    adding = result.action == "added"
    if not result.changed:
        said = f"You have voted for {result.key} already" if adding else f"You have no vote for {result.key} to withdraw"
        return f"{said}; nothing was sent."
    if result.dry_run:
        what = f"{'vote for' if adding else 'withdraw your vote for'} {result.key}"
        return f"Dry run: you would {what} ({'POST' if adding else 'DELETE'} /issue/{result.key}/votes, no body); nothing was sent"
    done = f"{'Voted for' if adding else 'Withdrew your vote for'} {result.key}"
    return done + ("." if result.count is None else f" ({_count(result.count, 'vote')} now).")


def run_vote(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.collab.vote.execute(args.key, remove=args.remove, dry_run=args.dry_run)
    ctx.print_json(result) if ctx.json_mode else ctx.print(vote_text(result))
    return 0


# -- worklogs ---------------------------------------------------------------------------------------------


def _worklog_cells(entry: Worklog, with_users: bool) -> list[str]:
    cells = [entry.id, entry.started, entry.time_spent, entry.comment]
    if with_users:
        cells.append((entry.author.display_name or entry.author.name) if entry.author else "")
    return cells


def run_worklogs(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.collab.worklogs.execute(args.key, since=args.since or "", limit=args.limit, with_users=args.with_users)
    rows = [to_jsonable(entry) for entry in result.worklogs]
    columns = WORKLOG_COLUMNS + ([("AUTHOR", "author.displayName")] if args.with_users else [])
    spent = format_seconds(result.seconds)
    document = {
        "key": result.key,
        "total": result.total,
        "count": len(rows),
        "timeSpentSeconds": result.seconds,
        "timeSpent": spent,
        "worklogs": rows,
    }

    def plain() -> str:
        if not rows:
            return f"No worklogs on {result.key}" + (" in that period." if args.since else ".")
        header = ["ID", "STARTED", "TIME", "COMMENT"] + (["AUTHOR"] if args.with_users else [])
        body = [_worklog_cells(entry, args.with_users) for entry in result.worklogs]
        return f"{table(header, body)}\nTotal: {len(rows)} of {_count(result.total, 'worklog')}, {spent} ({result.seconds} s)."

    print_list(ctx, args, payload=document, rows=rows, list_key="worklogs", columns=columns, plain=plain)
    return 0


def log_work_text(result: LogWorkResult) -> str:
    spent = result.payload["timeSpent"]
    if result.dry_run:
        length = f" ({result.seconds} s)" if result.seconds is not None else ""
        shown = _dry_run_payload(result.payload, result.query)
        return f"Dry run: {result.key} exists; would log {spent}{length}; nothing was sent\n{shown}"
    entry = result.worklog
    return f"Logged {spent} on {result.key}" + (f" (worklog {entry.id}, {entry.time_spent_seconds} s)" if entry and entry.id else "")


def run_log_work(ctx: Context, args: argparse.Namespace) -> int:
    comment = ctx.app.convert_markdown.execute(args.comment) if args.wiki and args.comment else args.comment
    result = ctx.app.collab.log_work.execute(
        args.key,
        args.time,
        comment=comment or "",
        started=args.started or "",
        adjust=args.adjust_estimate or "",
        new_estimate=args.new_estimate or "",
        reduce_by=args.reduce_by or "",
        dry_run=args.dry_run,
        if_unchanged_since=args.if_unchanged_since or "",
    )
    ctx.print_json(result) if ctx.json_mode else ctx.print(log_work_text(result))
    return 0


# -- web links --------------------------------------------------------------------------------------------


def links_text(rows: Sequence[dict[str, Any]], key: str) -> str:
    if not rows:
        return f"No web links on {key}."
    shown = [column for column in LINK_COLUMNS if column[1] not in OPTIONAL_LINK_COLUMNS or any(row[column[1]] for row in rows)]
    return structured_text(rows, shown, None, "table")


def run_remote_links(ctx: Context, args: argparse.Namespace) -> int:
    rows = [to_jsonable(link) for link in ctx.app.collab.remote_links.execute(args.key)]
    print_list(ctx, args, payload=rows, rows=rows, list_key=None, columns=LINK_COLUMNS, plain=lambda: links_text(rows, _key(args)))
    return 0


def remote_link_text(result: RemoteLinkResult) -> str:
    target = result.payload["object"]
    if result.dry_run:
        what = f"update the web link with global id {result.payload['globalId']}" if result.action == "updated" else "add a web link"
        return f"Dry run: {result.key} exists; would {what}; nothing was sent\n{pretty(result.payload)}"
    link = result.link
    done = "Updated" if result.action == "updated" else "Added"
    return f"{done} web link{' ' + link.id if link and link.id else ''} on {result.key}: {target['title']} ({target['url']})"


def run_remote_link(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.collab.remote_link.execute(
        args.key,
        args.url,
        args.title,
        relationship=args.relationship or "",
        global_id=args.global_id or "",
        dry_run=args.dry_run,
        if_unchanged_since=args.if_unchanged_since or "",
    )
    ctx.print_json(result) if ctx.json_mode else ctx.print(remote_link_text(result))
    return 0


COMMANDS = (
    Command(
        name="watchers",
        group=READ,
        summary="Show who watches an issue",
        when="Use to see how many people watch an issue and whether you do. Names are shown only with --with-users. "
        "To start or stop watching use `watch`.",
        description="Jira lists the watchers only to people who may see them.",
        args=(ISSUE_KEY, Arg(("--with-users",), WITH_USERS.format(what="who the watchers are"), kind="flag")),
        examples=(Example("jira watchers PROJ-1"), Example("jira watchers PROJ-1 --with-users --json")),
        json_output=True,
        output="key, count, watching, watchers; watchers is null unless --with-users, then [{name, displayName, email}].",
        handler=run_watchers,
    ),
    Command(
        name="watch",
        group=WORK,
        summary="Start or stop watching an issue",
        when="Use to watch an issue (you, or with --user another person) or with --remove to stop watching yourself. "
        "It changes only the watcher list. Dry-run first.",
        description="--user needs the permission Manage Watchers. --remove takes only your own watch off: another person is removed in Jira. "
        "The list is read back.",
        args=(
            ISSUE_KEY,
            Arg(("--user",), "Login name of the person to add; `me` is you. Default: you. With --remove only you.", metavar="NAME"),
            Arg(("--remove",), "Stop watching instead of starting. Only for yourself: nothing is sent for another person.", kind="flag"),
            DRY_RUN,
        ),
        examples=(Example("jira watch PROJ-1 --dry-run"), Example("jira watch PROJ-1 --remove"), Example("jira watch PROJ-1 --user jdoe")),
        writes=True,
        json_output=True,
        output="key, action (added|removed), user, payload, query, dryRun, watchCount, watching, confirmed, redacted, message; "
        "watchCount, watching and confirmed are null when the list could not be read back, and then message says the change was made.",
        handler=run_watch,
    ),
    Command(
        name="votes",
        group=READ,
        summary="Show the votes of an issue",
        when="Use to see how many votes an issue has and whether you voted. Who voted is never shown. To vote use `vote`.",
        description="The number of votes and whether you have voted.",
        args=(ISSUE_KEY,),
        examples=(Example("jira votes PROJ-1"), Example("jira votes PROJ-1 --json")),
        json_output=True,
        output="key, count, hasVoted.",
        handler=run_votes,
    ),
    Command(
        name="vote",
        group=WORK,
        summary="Vote for an issue, or withdraw your vote",
        when="Use only when asked to support an issue: a vote is visible to others. Only your own vote can be given or withdrawn; "
        "Jira refuses a vote for an issue you reported. Dry-run first.",
        description="Nothing is sent when the vote is as asked already. The count is read back.",
        args=(ISSUE_KEY, Arg(("--remove",), "Withdraw your vote instead of giving it.", kind="flag"), DRY_RUN),
        examples=(Example("jira vote PROJ-1 --dry-run"), Example("jira vote PROJ-1 --remove")),
        writes=True,
        json_output=True,
        output="key, action (added|removed), dryRun, changed, count, hasVoted, message; count and hasVoted are null when the votes could not be read "
        "back, and then message says the vote was made.",
        handler=run_vote,
    ),
    Command(
        name="worklogs",
        group=READ,
        summary="List the work logged on an issue",
        when="Use to see how much time was logged on an issue and when, newest first. Authors are shown only with --with-users. "
        "To log time use `log-work`.",
        description="--since keeps the work that started after 30m, 12h, 2d or 1w ago (clock time) or an ISO date; --limit keeps the newest. "
        "Comments have mentions and e-mail addresses redacted.",
        args=(
            ISSUE_KEY,
            Arg(("--since",), "Only work that started after this: 2d, 12h, 1w, 30m ago, or an ISO date or time.", metavar="ISO|2d"),
            Arg(("--limit",), "Most entries to show, newest first. Default: all.", kind="int", default=0, metavar="N"),
            Arg(("--with-users",), WITH_USERS.format(what="who logged each entry"), kind="flag"),
            SELECT,
            FORMAT,
        ),
        examples=(Example("jira worklogs PROJ-1"), Example("jira worklogs PROJ-1 --since 7d --limit 5 --json")),
        json_output=True,
        output="key, total, count, timeSpentSeconds, timeSpent, worklogs[{id, started, timeSpent, timeSpentSeconds, comment, author, created, "
        "updated, visibility}]; author is null unless --with-users.",
        handler=run_worklogs,
    ),
    Command(
        name="log-work",
        group=WORK,
        summary="Log work on an issue",
        when="Use to record time spent after the person asked for it: everyone sees it and it changes the time tracking. Dry-run first.",
        description="TIME is as in Jira: 1h 30m, 2d, 1w 2d. Jira converts days and weeks with its own working time (see `jira server`). "
        "By default it reduces the remaining estimate (--adjust-estimate). The new entry is read back.",
        args=(
            ISSUE_KEY,
            Arg(("time",), "Time spent: 1h 30m, 2d, 1w 2d.", required=True, metavar="TIME"),
            Arg(("--comment",), "What was done.", metavar="TEXT"),
            Arg(("--wiki",), "Convert the comment from Markdown to Jira wiki markup first.", kind="flag"),
            Arg(("--started",), "When the work was done, ISO 8601, for example 2026-10-15T08:30:00+02:00. Default: now.", metavar="ISO"),
            Arg(
                ("--adjust-estimate",),
                "What Jira does with the remaining estimate: auto (the default: reduces it), leave, new or manual.",
                kind="choice",
                choices=("auto", "leave", "new", "manual"),
                metavar="auto|leave|new|manual",
            ),
            Arg(("--new-estimate",), "With --adjust-estimate new: the new remaining estimate, such as 2d.", metavar="TIME"),
            Arg(("--reduce-by",), "With --adjust-estimate manual: how much to reduce the remaining estimate by.", metavar="TIME"),
            DRY_RUN,
            UNCHANGED_SINCE,
        ),
        examples=(
            Example("jira log-work PROJ-1 '1h 30m' --comment 'Reviewed the export.' --dry-run"),
            Example("jira log-work PROJ-1 2d --started 2026-10-14T08:00:00+02:00"),
            Example("jira log-work PROJ-1 30m --adjust-estimate new --new-estimate 2h"),
        ),
        writes=True,
        json_output=True,
        output="key, payload, query, seconds, dryRun, worklog{id, started, timeSpent, timeSpentSeconds, comment}, confirmed, message; "
        "seconds is set for hours and minutes only. confirmed is null when the entry could not be read back: message then says the work WAS "
        "logged and must not be logged again.",
        handler=run_log_work,
    ),
    Command(
        name="remote-links",
        group=READ,
        summary="List the web links of an issue",
        when="Use to see the external links (web pages, documents) of an issue, not the links between issues (`link`).",
        description="Titles and addresses have personal data redacted.",
        args=(ISSUE_KEY, SELECT, FORMAT),
        examples=(Example("jira remote-links PROJ-1"), Example("jira remote-links PROJ-1 --json")),
        json_output=True,
        output="a list of {id, title, url, relationship, application, globalId}.",
        handler=run_remote_links,
    ),
    Command(
        name="remote-link",
        group=WORK,
        summary="Add a web link to an issue",
        when="Use to attach an address (document, pull request, page) to an issue after the person asked for it; for another issue use `link`. "
        "Only http and https. Dry-run first.",
        description="With --global-id Jira updates the link that has that id and otherwise adds one, so a repeated call does not duplicate it. "
        "The link is read back.",
        args=(
            ISSUE_KEY,
            Arg(("--url",), "The address: http or https, without credentials.", required=True, metavar="URL"),
            Arg(("--title",), "The text of the link.", required=True, metavar="TEXT"),
            Arg(("--relationship",), "How the issue relates to the page: documents, is caused by, ... Default: Jira's own text.", metavar="TEXT"),
            Arg(("--global-id",), "A stable id of your own: the same id updates the same link.", metavar="ID"),
            DRY_RUN,
            UNCHANGED_SINCE,
        ),
        examples=(
            Example("jira remote-link PROJ-1 --url 'https://wiki.example.com/runbook' --title 'Runbook' --dry-run"),
            Example("jira remote-link PROJ-1 --url 'https://example.com/pr/7' --title 'Pull request 7' --global-id pr-7"),
        ),
        writes=True,
        json_output=True,
        output="key, action (created|updated), payload, dryRun, link{id, title, url, relationship, application, globalId}, confirmed, message; "
        "confirmed is null when the link could not be read back: message then says it WAS saved.",
        handler=run_remote_link,
    ),
)
