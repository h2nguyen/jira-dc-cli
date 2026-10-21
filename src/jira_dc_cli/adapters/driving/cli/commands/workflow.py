"""Commands that act on one issue: transition, comment, link, link-types, update, update-json."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from .....domain.errors import UsageError
from ..render import table
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import POWERSHELL_JSON_NOTE, fields_json_option, pretty, text_option

if TYPE_CHECKING:
    from .....application.comments import CommentResult
    from .....application.linking import LinkResult
    from .....application.workflow import TransitionResult
    from .....application.writing import WriteResult

GROUP = "Work on an issue"

WIKI = Arg(("--wiki",), "Convert the text from Markdown to Jira wiki markup first.", kind="flag")
DRY_RUN = Arg(("--dry-run",), "Show what would be sent and check that the issue exists. Sends nothing.", kind="flag")
SINCE = Arg(
    ("--if-unchanged-since",),
    "Refuse (exit code 3) when the issue changed in Jira after this time (ISO 8601, for example the fetch time of your last export). "
    "It is a check just before the write, not a lock: Jira has no atomic compare-and-set, so a change in those few milliseconds is not caught.",
    metavar="ISO",
)


def _markdown(ctx: Context, text: str | None, wiki: bool) -> str | None:
    return ctx.app.convert_markdown.execute(text) if wiki and text else text


# -- transition ----------------------------------------------------------------------------------


def transition_text(result: TransitionResult) -> str:
    if result.chosen is None:
        if not result.options:
            return f"{result.key} is in status: {result.current_status}\nNo transitions are available."
        rows = [(item.id, item.name, item.to_status.name) for item in result.options]
        return f"{result.key} is in status: {result.current_status}\n\n" + table(["ID", "TRANSITION", "TO STATUS"], rows)
    chosen = result.chosen
    if result.dry_run:
        return (
            f"Dry run: {result.key} would move from '{result.current_status}' to '{chosen.to_status.name}' "
            f"via transition '{chosen.name}' ({chosen.id}); nothing was sent\n{pretty(result.payload)}"
        )
    return f"Moved {result.key} from '{result.current_status}' to '{chosen.to_status.name}' via transition '{chosen.name}'"


def run_transition(ctx: Context, args: argparse.Namespace) -> int:
    comment = _markdown(ctx, text_option(ctx, args.comment, args.comment_file, "comment"), args.wiki)
    result = ctx.app.transition_issue.execute(args.key, to=args.to or "", comment=comment or "", dry_run=args.dry_run)
    ctx.print_json(result) if ctx.json_mode else ctx.print(transition_text(result))
    return 0


# -- comment -------------------------------------------------------------------------------------


def comment_result_text(result: CommentResult) -> str:
    if result.dry_run:
        return f"Dry run: {result.key} exists; nothing was sent\n{pretty(result.payload)}"
    return f"Commented on {result.key}" + (f" (comment {result.comment_id})" if result.comment_id else "")


def run_comment(ctx: Context, args: argparse.Namespace) -> int:
    body = text_option(ctx, args.text, args.file, "comment")
    if args.list:
        if body is not None:
            raise UsageError("--list cannot be combined with --text or --file.")
        comments = ctx.app.list_comments.execute(args.key, with_users=args.with_users)
        if ctx.json_mode:
            ctx.print_json({"key": args.key.upper(), "total": len(comments), "comments": comments})
            return 0
        if not comments:
            ctx.print("No comments.")
        for comment in comments:
            who = f" {comment.author.display_name}" if comment.author else ""
            ctx.print(f"--- #{comment.id} {comment.created}{who}")
            ctx.print(comment.body.strip())
        return 0
    if body is None:
        raise UsageError("Nothing to do.", hint="Pass --text or --file to add a comment, or --list to read the comments.")
    result = ctx.app.add_comment.execute(args.key, _markdown(ctx, body, args.wiki) or "", dry_run=args.dry_run)
    ctx.print_json(result) if ctx.json_mode else ctx.print(comment_result_text(result))
    return 0


# -- link ----------------------------------------------------------------------------------------


def link_text(result: LinkResult) -> str:
    if result.dry_run:
        return f"Dry run: would link  {result.relation}  (type '{result.type_name}'); nothing was sent\n{pretty(result.payload)}"
    if result.confirmed:
        return f"Linked: {result.confirmed}"
    return f"Link created, but it could not be read back from {result.key}; check it with: jira issue {result.key}"


def run_link(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.link_issues.execute(args.key, args.to, args.type, inverse=args.inverse, dry_run=args.dry_run)
    ctx.print_json(result) if ctx.json_mode else ctx.print(link_text(result))
    return 0


def run_link_types(ctx: Context, args: argparse.Namespace) -> int:
    types = ctx.app.list_link_types.execute()
    if ctx.json_mode:
        ctx.print_json(types)
    else:
        ctx.print(table(["NAME", "OUTWARD (this issue ...)", "INWARD (this issue ...)"], [(t.name, t.outward, t.inward) for t in types]))
    return 0


# -- update --------------------------------------------------------------------------------------


def write_text(result: WriteResult, since: str = "") -> str:
    if result.dry_run:
        what = f"{result.key} exists{' and is unchanged since ' + since if since else ''}" if result.action == "updated" else "the payload is valid"
        return f"Dry run: {what}; nothing was sent\n{pretty(result.payload)}"
    if result.action == "updated":
        return f"Updated {result.key}"
    created = result.created
    return f"Created {result.key}" + (f"\n{created.url}" if created and created.url else "")


def _optional_list(value: str | None) -> list[str] | None:
    from .....domain.drafts import split_list

    return None if value is None else split_list(value)


def run_update(ctx: Context, args: argparse.Namespace) -> int:
    from .....domain.drafts import IssueChanges

    description = _markdown(ctx, text_option(ctx, args.description, args.description_file, "description"), args.wiki)
    changes = IssueChanges(
        summary=args.summary,
        description=description,
        labels=_optional_list(args.labels),
        components=_optional_list(args.components),
        fix_versions=_optional_list(args.fix_versions),
        priority=args.priority,
        assignee=args.assignee,
        fields=fields_json_option(ctx, args.fields_json),
    )
    since = args.if_unchanged_since or ""
    result = ctx.app.update_issue.execute(args.key, changes, dry_run=args.dry_run, if_unchanged_since=since)
    ctx.print_json(result) if ctx.json_mode else ctx.print(write_text(result, since))
    return 0


def run_update_json(ctx: Context, args: argparse.Namespace) -> int:
    from .....domain.drafts import parse_json_object

    payload = parse_json_object(ctx.read_input(args.file), args.file)
    since = args.if_unchanged_since or ""
    result = ctx.app.update_issue_from_payload.execute(args.key, payload, dry_run=args.dry_run, if_unchanged_since=since)
    ctx.print_json(result) if ctx.json_mode else ctx.print(write_text(result, since))
    return 0


COMMANDS = (
    Command(
        name="transition",
        group=GROUP,
        summary="Move an issue to another status",
        when="Use to move an issue to another status, or without --to to see which statuses are possible from here. Run it with "
        "--dry-run first and show the result to the person who asked for the change.",
        description="Without --to it lists the transitions that are possible from the current status. --to is resolved as a "
        "transition id, then as the target status name, then as the transition name (case-insensitive). Transition names can "
        "differ from status names (\"Reopen\" leads to \"To Do\"), so prefer the target status.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("--to",), "Target status, transition name or transition id.", metavar="STATUS"),
            Arg(("--comment",), "Add this comment in the same step.", metavar="TEXT"),
            Arg(("--comment-file",), "Read the comment from a file (`-` is standard input).", metavar="FILE"),
            WIKI,
            Arg(("--dry-run",), "Show what would happen and send nothing.", kind="flag"),
        ),
        examples=(
            Example("jira transition PROJ-86", "what is possible from here"),
            Example("jira transition PROJ-86 --to 'In Review' --comment 'Ready for review.'"),
            Example("jira transition PROJ-86 --to Done --dry-run"),
        ),
        notes=("Jira refuses a transition whose screen requires fields this command does not send; Jira's message is shown as it is.",),
        writes=True,
        json_output=True,
        output="key, currentStatus, options[{id,name,toStatus}], chosen, dryRun, executed, payload.",
        handler=run_transition,
    ),
    Command(
        name="comment",
        group=GROUP,
        summary="Add a comment, or list the comments",
        when="Use to add a comment to an issue, or with --list to read the comments. Everyone who sees the issue sees the comment: "
        "write one only when asked, dry-run it first, and keep people's names out of it.",
        description="Adds a comment, or with --list shows the comments of an issue. Authors and mentions are hidden in the list "
        "unless you add --with-users; JIRA_HIDE_USERS=true removes --with-users altogether.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("--text",), "The comment.", metavar="TEXT"),
            Arg(("--file",), "Read the comment from a file (`-` is standard input).", metavar="FILE"),
            WIKI,
            Arg(("--list",), "List the comments instead of adding one.", kind="flag"),
            Arg(("--with-users",), "With --list: show the authors.", kind="flag"),
            DRY_RUN,
        ),
        examples=(
            Example("jira comment PROJ-86 --text 'Fixed in the next build.'"),
            Example("jira comment PROJ-86 --file notes.md --wiki"),
            Example("jira comment PROJ-86 --list --json"),
        ),
        writes=True,
        json_output=True,
        output="adding: key, payload, dryRun, commentId; with --list: key, total, comments[{id,created,body,author}].",
        handler=run_comment,
    ),
    Command(
        name="link",
        group=GROUP,
        summary="Link two issues",
        when="Use to link two issues (blocks, relates to, ...) after the person has asked for it. See the link types with "
        "`link-types`; dry-run first.",
        description="Creates an issue link. The link type is the exact name from `jira link-types` (case-insensitive); phrases such as "
        "\"requires\" occur in several types and are not accepted. Without --inverse the first issue gets the OUTWARD text of the type "
        "(`jira link A-1 --type Blocks --to A-2` means A-1 blocks A-2); with --inverse it gets the INWARD text "
        "(A-1 is blocked by A-2). After creating, the relation is read back from Jira and printed, so a wrong direction is visible.",
        args=(
            Arg(("key",), "The issue that gets the relation.", required=True, metavar="ISSUE_KEY"),
            Arg(("--type",), "Link type name, see `jira link-types`.", required=True, metavar="LINK_TYPE_NAME"),
            Arg(("--to",), "The other issue.", required=True, metavar="OTHER_KEY"),
            Arg(("--inverse",), "Use the inward text: ISSUE_KEY is blocked by / depends on OTHER_KEY.", kind="flag"),
            Arg(("--dry-run",), "Print the relation in words and the payload, check both issues, send nothing.", kind="flag"),
        ),
        examples=(
            Example("jira link PROJ-1 --type Blocks --to PROJ-2", "PROJ-1 blocks PROJ-2"),
            Example("jira link PROJ-1 --type Blocks --to PROJ-2 --inverse", "PROJ-1 is blocked by PROJ-2 (\"depends on\")"),
        ),
        writes=True,
        json_output=True,
        output="key, other, typeName, phrase, relation, payload, dryRun, confirmed.",
        handler=run_link,
    ),
    Command(
        name="link-types",
        group=GROUP,
        summary="List the link types",
        when="Use before `link` to see the link types of this Jira and how each reads.",
        description="The names to use with `jira link --type`, with the outward and inward text of each.",
        examples=(Example("jira link-types"),),
        json_output=True,
        output="a list of {id, name, outward, inward}.",
        handler=run_link_types,
    ),
    Command(
        name="update",
        group=GROUP,
        summary="Change fields of an issue",
        when="Use to change named fields of one issue (summary, description, labels, priority ...). Dry-run first, give "
        "--if-unchanged-since with the time you read the issue, and change one issue per command. To change an issue from a "
        "Markdown file use `push`.",
        description="Updates only the fields you pass. Lists replace the current values (an empty value clears them). "
        "To add one label without replacing the others use `jira update-json` with Jira's `update` syntax.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("--summary",), "New summary.", metavar="TEXT"),
            Arg(("--description",), "New description.", metavar="TEXT"),
            Arg(("--description-file",), "New description from a file (`-` is standard input).", metavar="FILE"),
            WIKI,
            Arg(("--labels",), "Replaces the labels.", metavar="a,b"),
            Arg(("--components",), "Replaces the components (they must exist).", metavar="a,b"),
            Arg(("--fix-versions",), "Replaces the fix versions (they must exist).", metavar="a,b"),
            Arg(("--priority",), "Priority, for example High.", metavar="NAME"),
            Arg(("--assignee",), "Assignee user name.", metavar="USER"),
            Arg(("--fields-json",), "Any other field as JSON, or @FILE. For a custom field put {\"customfield_10001\": 5} in a file and pass '@fields.json'. A field that has its own option cannot be set here too.", metavar="JSON|@FILE"),
            DRY_RUN,
            SINCE,
        ),
        examples=(
            Example("jira update PROJ-86 --summary 'Clearer title' --labels backend,api"),
            Example("jira update PROJ-86 --description-file body.md --wiki --dry-run"),
            Example("jira update PROJ-86 --fields-json '@fields.json' --dry-run"),
            Example("jira update PROJ-86 --priority High --if-unchanged-since 2026-05-01T08:00:00Z"),
        ),
        notes=(POWERSHELL_JSON_NOTE,),
        writes=True,
        json_output=True,
        output="action, key, payload, dryRun.",
        handler=run_update,
    ),
    Command(
        name="update-json",
        group=GROUP,
        summary="Update an issue from a complete JSON payload",
        when="Use to send a complete JSON payload to one issue when `update` has no option for the field (custom fields). Dry-run "
        "first, and give --if-unchanged-since.",
        description="Sends a payload with \"fields\" and/or \"update\" as it is. Use it for what `jira update` cannot express, "
        "for example adding one label (`{\"update\": {\"labels\": [{\"add\": \"x\"}]}}`).",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("file",), "JSON file (`-` is standard input).", required=True, metavar="PAYLOAD.json"),
            Arg(("--dry-run",), "Validate the payload, check that the issue exists, send nothing.", kind="flag"),
            SINCE,
        ),
        examples=(Example("jira update-json PROJ-86 payload.json --dry-run"),),
        writes=True,
        json_output=True,
        output="action, key, payload, dryRun.",
        handler=run_update_json,
    ),
)
