"""Commands that act on one issue: transition, comment, link, link-types, update, update-json."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any, Sequence

from .....domain.errors import UsageError
from .....domain.validation import LIST_LIMIT, FieldRule, TransitionScreen, listing
from ..listing import FORMAT, SELECT, print_list
from ..render import table, to_jsonable
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import POWERSHELL_JSON_NOTE, fields_json_option, pretty, replacing_text_option, text_option

if TYPE_CHECKING:
    from .....application.comments import CommentResult, EditResult
    from .....application.linking import LinkResult
    from .....application.validation import TransitionOutcome
    from .....application.writing import WriteResult
    from .....domain.model import Comment

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


def report_warnings(ctx: Context, warnings: Sequence[str]) -> None:
    """What a dry run found that Jira may refuse goes to standard error; the same list is in the JSON result."""
    for warning in warnings:
        ctx.diagnostics.warn(warning)


# -- transition ----------------------------------------------------------------------------------


def _field_json(rule: FieldRule) -> dict[str, Any]:
    names = rule.allowed_names
    return {
        "id": rule.field_id,
        "name": rule.name,
        "required": rule.required,
        "allowed": None if rule.allowed is None else names[:LIST_LIMIT],
        "allowedCount": None if rule.allowed is None else len(names),
    }


def screen_json(screen: TransitionScreen) -> dict[str, Any]:
    transition = screen.transition
    return {
        "id": transition.id,
        "name": transition.name,
        "toStatus": to_jsonable(transition.to_status),
        "fields": [_field_json(rule) for rule in screen.fields],
    }


def screens_text(result: TransitionOutcome) -> str:
    rows = []
    for screen in result.screens:
        transition = screen.transition
        head = (transition.id, transition.name, transition.to_status.name)
        if not screen.fields:
            rows.append((*head, "-", "", ""))
        for index, rule in enumerate(screen.fields):
            values = listing(rule.allowed_names) if rule.allowed is not None else ""
            rows.append((*(head if index == 0 else ("", "", "")), rule.field_id, "required" if rule.required else "", values))
    if not rows:
        return f"{result.key} is in status: {result.current_status}\nNo transitions are available."
    headers = ["ID", "TRANSITION", "TO STATUS", "FIELD", "REQUIRED", "ALLOWED"]
    return f"{result.key} is in status: {result.current_status}\n\n" + table(headers, rows)


def transition_text(result: TransitionOutcome) -> str:
    if result.screens:
        return screens_text(result)
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
    result = ctx.app.validation.transition.execute(
        args.key,
        to=args.to or "",
        comment=comment or "",
        fields=fields_json_option(ctx, args.fields_json),
        resolution=args.resolution or "",
        dry_run=args.dry_run,
        show_fields=args.show_fields,
        if_unchanged_since=args.if_unchanged_since or "",
    )
    report_warnings(ctx, result.warnings)
    if ctx.json_mode:
        ctx.print_json({**to_jsonable(result), "screens": [screen_json(screen) for screen in result.screens]})
    else:
        ctx.print(transition_text(result))
    return 0


# -- comment -------------------------------------------------------------------------------------

COMMENT_COLUMNS = [("ID", "id"), ("CREATED", "created"), ("BY", "authorRef"), ("ME", "byMe"), ("BODY", "body")]


def comment_result_text(result: CommentResult) -> str:
    if result.dry_run:
        return f"Dry run: {result.key} exists; nothing was sent\n{pretty(result.payload)}"
    return f"Commented on {result.key}" + (f" (comment {result.comment_id})" if result.comment_id else "")


def comment_header(comment: Comment) -> str:
    """The line above a comment: its number and time, who wrote it (the author when people are shown, else the pseudonym), whether it is
    yours, who may see it, and when it was last edited."""
    who = (comment.author.display_name or comment.author.name) if comment.author else comment.author_ref
    parts = [
        f"--- #{comment.id} {comment.created}",
        who,
        "(you)" if comment.by_me else "",
        f"[{comment.visibility}]" if comment.visibility else "",
    ]
    if comment.updated and comment.updated != comment.created:
        parts.append(f"edited {comment.updated}")
    return " ".join(part for part in parts if part)


def comment_text(comment: Comment, rendered: bool) -> str:
    """The header and the text: as stored, or with `rendered` as Jira shows it (so that markup Jira does not render stays visible)."""
    text = comment.rendered_body if rendered and comment.rendered_body else comment.body
    return f"{comment_header(comment)}\n{text.strip()}"


def edit_text(result: EditResult) -> str:
    where = f"comment {result.comment_id} of {result.key}"
    if result.action == "unchanged":
        return f"{'Dry run: ' if result.dry_run else ''}{where} already has this text; nothing was sent"
    if not result.dry_run:
        return f"Edited {where}" + (f" (updated {result.updated})" if result.updated else "")
    shown = result.diff or f"Current text:\n{result.current_body}"
    return f"Dry run: {where} would be edited; nothing was sent\n{shown}\n\nWould send:\n{pretty(result.payload)}"


_ONLY = {
    "--rendered": ("--list and --get", ("list", "get")),
    "--since": ("--list", ("list",)),
    "--last": ("--list", ("list",)),
    "--select": ("--list", ("list",)),
    "--format": ("--list", ("list",)),
    "--if-unchanged-since": ("--edit", ("edit",)),
    "--visible-to": ("adding a comment", ("add",)),
}


def _comment_mode(args: argparse.Namespace, body: str | None) -> str:
    """add, list, get or edit; a usage error for options that do not go together."""
    chosen = [flag for flag, on in (("--list", args.list), ("--get", args.get is not None), ("--edit", args.edit is not None)) if on]
    if len(chosen) > 1:
        raise UsageError(f"{' and '.join(chosen)} cannot be combined.", hint="Use one of them at a time.")
    mode = chosen[0].lstrip("-") if chosen else "add"
    if mode in ("list", "get") and body is not None:
        raise UsageError(f"--{mode} cannot be combined with --text or --file.")
    if body is None and mode in ("add", "edit"):
        hint = "Pass --text or --file to add a comment, --list or --get ID to read, --edit ID with the new text to change one."
        raise UsageError("Nothing to do." if mode == "add" else "--edit needs the new text.", hint=hint)
    given = {
        "--rendered": args.rendered,
        "--since": args.since,
        "--last": args.last is not None,
        "--select": args.select,
        "--format": args.format != "table",
        "--if-unchanged-since": args.if_unchanged_since,
        "--visible-to": args.visible_to,
    }
    for flag, (applies, modes) in _ONLY.items():
        if given[flag] and mode not in modes:
            raise UsageError(f"{flag} applies to {applies}.", hint=f"Leave {flag} out, or use {applies}.")
    if args.last is not None and args.last < 1:
        raise UsageError("--last must be at least 1.", hint="--last 3 lists the three newest comments.")
    return mode


def _list_comments(ctx: Context, args: argparse.Namespace) -> None:
    found = ctx.app.comments.find.execute(
        args.key, with_users=args.with_users, rendered=args.rendered, since=args.since or "", last=args.last or 0
    )
    rows = [to_jsonable(comment) for comment in found.comments]
    payload = {"key": found.key, "total": found.total, "count": len(rows), "since": found.since or None, "comments": rows}

    def plain() -> str:
        return "\n".join(comment_text(comment, args.rendered) for comment in found.comments) if found.comments else "No comments."

    print_list(ctx, args, payload=payload, rows=rows, list_key="comments", columns=COMMENT_COLUMNS, plain=plain)


def _get_comment(ctx: Context, args: argparse.Namespace) -> None:
    comment = ctx.app.comments.get.execute(args.key, args.get, with_users=args.with_users, rendered=args.rendered)
    if ctx.json_mode:
        ctx.print_json({"key": args.key.strip().upper(), **to_jsonable(comment)})
    else:
        ctx.print(comment_text(comment, args.rendered))


def _edit_comment(ctx: Context, args: argparse.Namespace, body: str) -> None:
    text = _markdown(ctx, body, args.wiki) or ""
    since = args.if_unchanged_since or ""
    result = ctx.app.comments.edit.execute(args.key, args.edit, text, dry_run=args.dry_run, if_unchanged_since=since)
    report_warnings(ctx, result.warnings)
    ctx.print_json(result) if ctx.json_mode else ctx.print(edit_text(result))


def run_comment(ctx: Context, args: argparse.Namespace) -> int:
    body = text_option(ctx, args.text, args.file, "comment")
    mode = _comment_mode(args, body)
    if mode == "list":
        _list_comments(ctx, args)
    elif mode == "get":
        _get_comment(ctx, args)
    elif mode == "edit":
        _edit_comment(ctx, args, body or "")
    else:
        text = _markdown(ctx, body, args.wiki) or ""
        result = ctx.app.add_comment.execute(args.key, text, dry_run=args.dry_run, visible_to=args.visible_to or "")
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

    description = _markdown(ctx, replacing_text_option(ctx, args.description, args.description_file, "description"), args.wiki)
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
    report_warnings(ctx, result.warnings)
    ctx.print_json(result) if ctx.json_mode else ctx.print(write_text(result, since))
    return 0


def run_update_json(ctx: Context, args: argparse.Namespace) -> int:
    from .....domain.drafts import parse_json_object

    payload = parse_json_object(ctx.read_input(args.file), args.file)
    since = args.if_unchanged_since or ""
    result = ctx.app.update_issue_from_payload.execute(args.key, payload, dry_run=args.dry_run, if_unchanged_since=since)
    report_warnings(ctx, result.warnings)
    ctx.print_json(result) if ctx.json_mode else ctx.print(write_text(result, since))
    return 0


COMMANDS = (
    Command(
        name="transition",
        group=GROUP,
        summary="Move an issue to another status",
        when="Use to move an issue to another status, or without --to to see which statuses are possible from here. When the screen of a "
        "transition asks for fields (a resolution), --show-fields lists them. Run it with --dry-run first and show the result to the "
        "person who asked for the change.",
        description="Without --to it lists the transitions that are possible from the current status. --to is resolved as a "
        "transition id, then as the target status name, then as the transition name (case-insensitive). Transition names can "
        "differ from status names (\"Reopen\" leads to \"To Do\"), so prefer the target status. --resolution and --fields-json send "
        "the fields of the transition screen; the dry run checks them against that screen.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("--to",), "Target status, transition name or transition id.", metavar="STATUS"),
            Arg(("--comment",), "Add this comment in the same step.", metavar="TEXT"),
            Arg(("--comment-file",), "Read the comment from a file (`-` is standard input).", metavar="FILE"),
            WIKI,
            Arg(("--fields-json",), "Fields of the transition screen as JSON, or @FILE (a custom field, a version).", metavar="JSON|@FILE"),
            Arg(("--resolution",), "Set the resolution, for example Fixed (the same as a resolution in --fields-json).", metavar="NAME"),
            Arg(("--show-fields",), "List the fields on the screen of each possible transition, with the values they allow.", kind="flag"),
            Arg(("--dry-run",), "Show what would happen and send nothing.", kind="flag"),
            SINCE,
        ),
        examples=(
            Example("jira transition PROJ-86", "what is possible from here"),
            Example("jira transition PROJ-86 --to 'In Review' --comment 'Ready for review.'"),
            Example("jira transition PROJ-86 --to Done --dry-run"),
            Example("jira transition PROJ-86 --to Done --show-fields", "what the screen of that transition asks for"),
            Example("jira transition PROJ-86 --to Done --resolution Fixed --dry-run"),
            Example("jira transition PROJ-86 --to Done --resolution Fixed --if-unchanged-since 2026-05-01T08:00:00Z"),
        ),
        notes=(
            "A transition whose screen requires fields is refused by Jira unless --resolution or --fields-json sends them; the dry run "
            "warns about a required field that is missing, and refuses a value that the screen does not allow (exit code 6).",
            POWERSHELL_JSON_NOTE,
        ),
        writes=True,
        json_output=True,
        output="key, currentStatus, options[{id,name,toStatus}], chosen, dryRun, executed, payload, warnings, "
        "screens[{id,name,toStatus,fields[{id,name,required,allowed,allowedCount}]}] (with --show-fields).",
        handler=run_transition,
    ),
    Command(
        name="comment",
        group=GROUP,
        summary="Add, read or edit a comment",
        when="Use to add a comment, to read comments (--list, --get) or to correct one you wrote (--edit). Everyone who sees the issue "
        "sees a comment: write or edit one only when asked, dry-run it first, and keep people's names out of it.",
        description="Adds a comment; --list shows the comments of an issue (--since and --last pick the recent ones), --get ID one, "
        "--edit ID replaces its text and keeps who may see it. --rendered shows the text as Jira renders it. Authors are hidden unless "
        "--with-users (refused when JIRA_HIDE_USERS=true); every comment has byMe and a stable authorRef instead.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("--text",), "The comment (with --edit: the new text).", metavar="TEXT"),
            Arg(("--file",), "Read the text from a file (`-` is standard input).", metavar="FILE"),
            WIKI,
            Arg(("--list",), "List the comments instead of adding one.", kind="flag"),
            Arg(("--with-users",), "With --list or --get: show the authors.", kind="flag"),
            DRY_RUN,
            Arg(("--get",), "Show one comment.", metavar="ID"),
            Arg(("--edit",), "Replace the text of this comment (with --text or --file); who may see it stays.", metavar="ID"),
            Arg(("--rendered",), "With --list or --get: the text as Jira renders it, as plain text.", kind="flag"),
            Arg(
                ("--since",),
                "With --list: only comments of the last 2d, 12h or 1w, or since a date (2026-10-15) or an ISO time.",
                metavar="WHEN",
            ),
            Arg(("--last",), "With --list: only the N newest comments (of those that --since lets through).", kind="int", metavar="N"),
            Arg(("--visible-to",), "When adding: let only role:NAME or group:NAME see the comment.", metavar="RESTRICTION"),
            Arg(
                ("--if-unchanged-since",),
                "With --edit: refuse (exit code 3) when the comment changed after this time (ISO 8601). A check before the write.",
                metavar="ISO",
            ),
            SELECT,
            FORMAT,
        ),
        examples=(
            Example("jira comment PROJ-86 --text 'Fixed in the next build.'"),
            Example("jira comment PROJ-86 --file notes.md --wiki"),
            Example("jira comment PROJ-86 --list --json"),
            Example("jira comment PROJ-86 --list --since 2d --last 3", "the newest three of the last two days"),
            Example("jira comment PROJ-86 --get 10100 --rendered", "how Jira shows it"),
            Example("jira comment PROJ-86 --edit 10100 --file new.md --wiki --dry-run"),
        ),
        writes=True,
        json_output=True,
        output="adding: key, payload, dryRun, commentId; --list: key, total, count, since, comments[{id,created,updated,body,renderedBody,"
        "author,byMe,authorRef,visibility}]; --get: key and the keys of one comment; --edit: key, commentId, action, dryRun, payload, "
        "updated, byMe, currentBody, diff, warnings.",
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
        "To add one label without replacing the others use `jira update-json` with Jira's `update` syntax. The dry run checks the "
        "priority, components and fix versions against what the project allows (exit code 6, and the allowed values, when one is not).",
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
            Arg(
                ("--assignee",),
                "Assignee: a user name, display name or e-mail address (one user: ambiguity is refused), me, unassigned or automatic.",
                metavar="USER",
            ),
            Arg(("--fields-json",), "Any other field as JSON, or @FILE. For a custom field put {\"customfield_10001\": 5} in a file and pass '@fields.json'. A field that has its own option cannot be set here too.", metavar="JSON|@FILE"),
            DRY_RUN,
            SINCE,
        ),
        examples=(
            Example("jira update PROJ-86 --summary 'Clearer title' --labels backend,api"),
            Example("jira update PROJ-86 --description-file body.md --wiki --dry-run"),
            Example("jira update PROJ-86 --fields-json '@fields.json' --dry-run"),
            Example("jira update PROJ-86 --priority High --if-unchanged-since 2026-05-01T08:00:00Z"),
            Example("jira update PROJ-86 --assignee 'Jane Doe' --dry-run", "the payload shows the user name it resolved to"),
        ),
        notes=(POWERSHELL_JSON_NOTE,),
        writes=True,
        json_output=True,
        output="action, key, payload, dryRun, warnings.",
        handler=run_update,
    ),
    Command(
        name="update-json",
        group=GROUP,
        summary="Update an issue from a complete JSON payload",
        when="Use to send a complete JSON payload to one issue when `update` has no option for the field (custom fields). Dry-run "
        "first, and give --if-unchanged-since.",
        description="Sends a payload with \"fields\" and/or \"update\" as it is. Use it for what `jira update` cannot express, "
        "for example adding one label (`{\"update\": {\"labels\": [{\"add\": \"x\"}]}}`). The dry run checks the priority, components "
        "and fix versions that the payload names.",
        args=(
            Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),
            Arg(("file",), "JSON file (`-` is standard input).", required=True, metavar="PAYLOAD.json"),
            Arg(("--dry-run",), "Validate the payload, check that the issue exists, send nothing.", kind="flag"),
            SINCE,
        ),
        examples=(Example("jira update-json PROJ-86 payload.json --dry-run"),),
        writes=True,
        json_output=True,
        output="action, key, payload, dryRun, warnings.",
        handler=run_update_json,
    ),
)
