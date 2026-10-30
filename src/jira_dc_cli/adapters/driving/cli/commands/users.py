"""Commands of the users area: `users` (find the user name that `--assignee` needs, list people) and `assign`."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from .....domain.errors import UsageError
from ..listing import FORMAT, SELECT, print_list
from ..render import key_values, table, to_jsonable
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import pretty, query_option
from .workflow import DRY_RUN, SINCE

if TYPE_CHECKING:
    from .....application.users import AssignResult, UserLookup

GROUP = "Work on an issue"
USER_COLUMNS = [("NAME", "name"), ("DISPLAY NAME", "displayName"), ("EMAIL", "email"), ("ACTIVE", "active")]


# -- users ---------------------------------------------------------------------------------------


def lookup_json(found: UserLookup, with_users: bool) -> dict[str, Any]:
    """`matches` says how many users fit and `name` is the user name when exactly one does; the person only with `--with-users`."""
    person = found.person
    document: dict[str, Any] = {
        "query": found.query,
        "matches": found.matches,
        "name": person.name if person else None,
        "active": person.active if person else None,
    }
    if with_users:
        document.update({"displayName": person.display_name if person else None, "email": person.email if person else None})
    return document


def lookup_text(found: UserLookup, with_users: bool) -> str:
    person = found.person
    pairs: list[tuple[str, Any]] = [("Query", found.query), ("Matches", found.matches)]
    if person is not None:
        pairs += [("Name", person.name), ("Active", "yes" if person.active else "no")]
        if with_users:
            pairs += [("Display name", person.display_name), ("Email", person.email)]
    return key_values(pairs)


def _resolve(ctx: Context, args: argparse.Namespace, query: str) -> None:
    if args.select or args.format != "table":
        raise UsageError("--select and --format apply to `users search`.", hint="`users resolve` prints one answer.")
    found = ctx.app.users.resolve.execute(query, project=args.project or "", issue=args.issue or "", with_users=args.with_users)
    if ctx.json_mode:
        ctx.print_json(lookup_json(found, args.with_users))
        return
    ctx.print(lookup_text(found, args.with_users))
    if found.matches == 0:
        ctx.diagnostics.info(f"No user fits '{found.query}'. Check the spelling; with --project or --issue only assignable users count.")
    elif found.matches > 1:
        ctx.diagnostics.info(f"{found.matches} users fit '{found.query}': give the exact user name, or add --issue or --project.")


def _search(ctx: Context, args: argparse.Namespace, query: str) -> None:
    people = ctx.app.users.search.execute(query, project=args.project or "", issue=args.issue or "", with_users=args.with_users)
    rows = [to_jsonable(person) for person in people]

    def plain() -> str:
        if not people:
            return "No users."
        shown = [(person.name, person.display_name, person.email, "yes" if person.active else "no") for person in people]
        return table([header for header, _ in USER_COLUMNS], shown)

    payload = {"query": query, "count": len(rows), "users": rows}
    print_list(ctx, args, payload=payload, rows=rows, list_key="users", columns=USER_COLUMNS, plain=plain)


def run_users(ctx: Context, args: argparse.Namespace) -> int:
    query = query_option(ctx, args.query, "user query") or ""
    if args.action == "resolve":
        _resolve(ctx, args, query)
    else:
        _search(ctx, args, query)
    return 0


# -- assign --------------------------------------------------------------------------------------


def assign_text(result: AssignResult, since: str = "") -> str:
    if result.dry_run:
        target = {"assigned": f"to {result.assignee}", "unassigned": "to nobody", "automatic": "to the default assignee"}[result.action]
        unchanged = f" and is unchanged since {since}" if since else ""
        return f"Dry run: {result.key} exists{unchanged}; it would be assigned {target}; nothing was sent\n{pretty(result.payload)}"
    if result.assignee is None:
        return f"{result.key} is now unassigned"
    return f"Assigned {result.key} to {result.assignee}" + (" (the default assignee)" if result.action == "automatic" else "")


def run_assign(ctx: Context, args: argparse.Namespace) -> int:
    since = args.if_unchanged_since or ""
    result = ctx.app.users.assign.execute(args.key, args.assignee, dry_run=args.dry_run, if_unchanged_since=since)
    ctx.print_json(result) if ctx.json_mode else ctx.print(assign_text(result, since))
    return 0


COMMANDS: tuple[Command, ...] = (
    Command(
        name="users",
        group=GROUP,
        summary="Find the user name for an assignee",
        when="Use `resolve` to get the user name that `--assignee` needs from a display name or an address: it says how many users fit "
        "and which, and prints no people. `search` lists people: it needs --with-users and is refused when JIRA_HIDE_USERS=true.",
        description="`resolve NAME` finds the user whose user name, display name or e-mail address is NAME: an exact user name wins, then "
        "an exact display name, then an exact address, then one partial match; `me` is the token's user. It prints `matches`, `name` "
        "(null unless exactly one user fits) and `active`. With --project or --issue only users who can be assigned there count.",
        args=(
            Arg(
                ("action",),
                "resolve (the user name, no people printed) or search (lists people).",
                kind="choice",
                choices=("resolve", "search"),
                required=True,
            ),
            Arg(
                ("query",),
                "A user name, display name or e-mail address; `me` is the token's user. `@FILE` or `-` reads it from a file or stdin.",
                kind="words",
                required=True,
                metavar="NAME",
            ),
            Arg(("--project",), "Only users who can be assigned issues of this project (for an issue yet to be created).", metavar="KEY"),
            Arg(("--issue",), "Only users who can be assigned this issue.", metavar="ISSUE_KEY"),
            Arg(
                ("--with-users",),
                "Also show display names and e-mail addresses; `search` needs it. Refused if JIRA_HIDE_USERS=true.",
                kind="flag",
            ),
            SELECT,
            FORMAT,
        ),
        examples=(
            Example("jira users resolve 'Jane Doe'"),
            Example("jira users resolve jane --issue PROJ-86 --json", "among the users who can be assigned PROJ-86"),
            Example("jira users search Jane --with-users", "people: only when they may be shown"),
        ),
        json_output=True,
        output="resolve: query, matches, name, active (and displayName, email with --with-users); "
        "search: query, count, users[{name,displayName,email,active}].",
        handler=run_users,
    ),
    Command(
        name="assign",
        group=GROUP,
        summary="Assign an issue to a user",
        when="Use to assign one issue, or to unassign it, after the person has asked for it. Say who by user name, display name, e-mail "
        "address, me, unassigned or automatic: a name that fits several users is refused with their number. Dry-run first.",
        description="Assigns one issue and reads the assignee back. The name is looked up among the users who can be assigned that issue; "
        "`automatic` gives the issue to the default assignee of its project. The result holds the user name and no other personal data.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(
                ("assignee",),
                "A user name, display name or e-mail address, or me, unassigned or automatic.",
                required=True,
                metavar="ASSIGNEE",
            ),
            DRY_RUN,
            SINCE,
        ),
        examples=(
            Example("jira assign PROJ-86 jdoe"),
            Example("jira assign PROJ-86 'Jane Doe' --dry-run", "the payload shows the user name it resolved to"),
            Example("jira assign PROJ-86 unassigned --if-unchanged-since 2026-05-01T08:00:00Z"),
        ),
        writes=True,
        json_output=True,
        output="key, action, payload, dryRun, assignee.",
        handler=run_assign,
    ),
)
