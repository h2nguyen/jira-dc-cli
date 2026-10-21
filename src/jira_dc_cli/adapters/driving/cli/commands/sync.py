"""Commands that keep a folder of Markdown tickets in step with Jira: diff, pull, push."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from .....domain.drafts import split_list
from .....domain.mirror import NO_SUCH_FIELD
from ..render import table
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import pretty

if TYPE_CHECKING:
    from .....application.sync import Entry, Plan, PullOutcome, PushOutcome

GROUP = "Sync with files"

_STATE_LABEL = {
    "same": "in step",
    "differs": "differs",
    "jira-changed": "jira changed",
    "local-changed": "edited here",
    "mixed": "both sides",
    "conflict": "conflict",
    "only-jira": "only in Jira",
    "only-local": "only in folder",
    "duplicate": "duplicate",
    "unreadable": "unreadable",
}


def entry_json(entry: Entry) -> dict[str, Any]:
    return {
        "key": entry.key,
        "state": entry.state,
        "fields": entry.fields,
        "editedHere": entry.local_fields,
        "changedInJira": entry.jira_fields,
        "conflicting": entry.conflicts,
        "unknown": entry.unknown,
        "file": entry.file or None,
        "message": entry.message or None,
        "skipped": entry.skipped,
    }


def _detail(entry: Entry) -> str:
    """What differs, and on which side it was changed."""
    parts = []
    if entry.local_fields:
        parts.append("here: " + ", ".join(entry.local_fields))
    if entry.jira_fields:
        parts.append("Jira: " + ", ".join(entry.jira_fields))
    if entry.conflicts:
        parts.append("both: " + ", ".join(entry.conflicts))
    if entry.unknown:
        parts.append("no record: " + ", ".join(entry.unknown))
    return "; ".join(parts) or entry.message


def plan_summary(plan: Plan) -> str:
    counts = " | ".join(f"{_STATE_LABEL[state]} {number}" for state, number in plan.counts.items() if number)
    return f"{plan.scope}: {plan.files} files, {plan.issues} issues in Jira | {counts or 'nothing to compare'}"


def skipped_note(skipped: dict[str, dict[str, str]]) -> str:
    """Which fields were left out of the comparison, so that nobody reads `in step` as more than it says. Empty when nothing was left out.
    A field that this Jira does not have is left out for every file: one phrase says so, not a list of all keys."""
    by_field: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    for key, fields in skipped.items():
        for name, why in fields.items():
            by_field.setdefault(name, []).append(key)
            reasons[name] = why
    parts = []
    for name, keys in by_field.items():
        if reasons[name].startswith(NO_SUCH_FIELD):
            parts.append(f"{name} (this Jira has no such field)")
        else:
            parts.append(f"{name} of {', '.join(keys[:5])}" + (f" and {len(keys) - 5} more" if len(keys) > 5 else ""))
    return ("Not compared (`--json` says why): " + "; ".join(parts) + ".") if parts else ""


def run_diff(ctx: Context, args: argparse.Namespace) -> int:
    plan = ctx.app.diff_mirror.execute(args.directory, project=args.project or "", keys=split_list(args.issue or ""))
    shown = plan.entries if args.show_same else [entry for entry in plan.entries if entry.state != "same"]
    if ctx.json_mode:
        ctx.print_json(
            {
                "scope": plan.scope,
                "fetchedAt": plan.fetched_at,
                "files": plan.files,
                "issues": plan.issues,
                "counts": plan.counts,
                "entries": [entry_json(entry) for entry in shown],
                "leftOut": plan.left_out,
            }
        )
        return 0
    lines = [plan_summary(plan)]
    if shown:
        lines += ["", table(["KEY", "STATE", "DETAIL", "FILE"], [(e.key, _STATE_LABEL[e.state], _detail(e), e.file) for e in shown])]
    ctx.print("\n".join(lines))
    counts = plan.counts
    note = skipped_note({entry.key: entry.skipped for entry in plan.entries if entry.skipped})
    if note:
        ctx.diagnostics.info(note)
    if counts["jira-changed"]:
        ctx.diagnostics.info("`jira pull DIRECTORY --update` brings in what Jira changed.")
    if counts["local-changed"]:
        ctx.diagnostics.info("`jira push FILE --dry-run` shows what would be sent of what you edited.")
    if counts["mixed"]:
        ctx.diagnostics.info("Both sides changed, but different fields: `jira pull --update` takes Jira's, `jira push` sends yours, and neither touches the other's.")
    if counts["conflict"]:
        ctx.diagnostics.info("A conflict is a field that Jira changed and the file was edited too: neither side is overwritten until you choose (see `jira help pull` and `jira help push`).")
    if counts["differs"]:
        ctx.diagnostics.info("A file that differs and was never synced has no known direction: `jira pull DIRECTORY` notes the ones that are equal; --force on `pull --update` or `push` picks a side.")
    if counts["only-jira"]:
        ctx.diagnostics.info("`jira pull DIRECTORY` writes a file for each issue that has none.")
    for item in plan.left_out:
        ctx.diagnostics.debug(f"left out: {item}")
    return 0


def _pull_line(outcome: PullOutcome) -> str:
    verb = {"created": "create", "updated": "update", "adopted": "note"}.get(outcome.action, outcome.action)
    label = f"would {verb}" if outcome.dry_run and outcome.action in ("created", "updated", "adopted") else outcome.action
    detail = f" ({', '.join(outcome.fields)})" if outcome.fields and outcome.action in ("updated", "skipped", "conflict") else ""
    note = f": {outcome.message}" if outcome.message else ""
    lossy = f" [Markdown cannot show: {', '.join(outcome.lossy)}]" if outcome.lossy else ""
    return f"{label:<14}{outcome.key:<11}{outcome.file}{detail}{note}{lossy}"


def run_pull(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.pull_mirror.execute(
        args.directory,
        project=args.project or "",
        keys=split_list(args.issue or ""),
        update=args.update,
        force=args.force,
        new_files=not args.no_new,
        dry_run=args.dry_run,
    )
    counts = {action: result.count(action) for action in ("created", "updated", "adopted", "unchanged", "skipped", "conflict")}
    if ctx.json_mode:
        ctx.print_json(
            {
                "scope": result.scope,
                "dryRun": args.dry_run,
                "counts": counts,
                "outcomes": [
                    {"key": o.key, "action": o.action, "file": o.file or None, "fields": o.fields, "message": o.message or None, "lossy": o.lossy}
                    for o in result.outcomes
                    if o.action != "unchanged"
                ],
                "onlyLocal": result.only_local,
                "skipped": result.skipped,
                "leftOut": result.left_out,
            }
        )
    else:
        lines = [_pull_line(outcome) for outcome in result.outcomes if outcome.action != "unchanged"]
        summary = "Done: " + " ".join(f"{name}={number}" for name, number in counts.items()) + (" (dry run, nothing was written)" if args.dry_run else "")
        ctx.print("\n".join([*lines, summary]))
    if result.only_local:
        ctx.diagnostics.info(f"In the folder but not in Jira: {', '.join(result.only_local)}. Nothing was deleted.")
    note = skipped_note(result.skipped)
    if note:
        ctx.diagnostics.info(note)
    if result.first_error is not None:
        raise result.first_error
    return 0


def _push_text(outcome: PushOutcome) -> str:
    fields = f" ({', '.join(outcome.fields)})" if outcome.fields else ""
    if outcome.action == "pushed":
        label = "would push" if outcome.dry_run else "pushed"
        text = f"{label} {outcome.key}{fields} from {outcome.file}"
        if outcome.dry_run:
            return f"{text}\n{pretty(outcome.payload)}" + (f"\n{outcome.message}" if outcome.message else "")
        return f"{text}: {outcome.message}" if outcome.message else text
    if outcome.action == "refused":
        return f"REFUSED {outcome.key} ({outcome.file}): {outcome.message}"
    return f"unchanged {outcome.key} ({outcome.file}): {outcome.message}"


def run_push(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.push_mirror.execute(args.target, keys=split_list(args.issue or ""), force=args.force, dry_run=args.dry_run)
    if ctx.json_mode:
        ctx.print_json(
            {
                "dryRun": args.dry_run,
                "pushed": result.count("pushed"),
                "unchanged": result.count("unchanged"),
                "refused": result.count("refused"),
                "outcomes": [
                    {"key": o.key, "action": o.action, "file": o.file or None, "fields": o.fields, "payload": o.payload, "message": o.message or None}
                    for o in result.outcomes
                ],
            }
        )
    else:
        lines = [_push_text(outcome) for outcome in result.outcomes]
        if args.dry_run:
            lines.append("Dry run: nothing was sent.")
        ctx.print("\n".join(lines))
    if result.first_error is not None:
        raise result.first_error
    return 0


_PROJECT = Arg(("--project",), "Project key. Default: the current project (`jira use`).", metavar="KEY")
_ISSUE = Arg(("--issue",), "Only these issues, comma separated (KEY or KEY,KEY): the files that hold them and the issues themselves.", metavar="KEYS")

COMMANDS = (
    Command(
        name="diff",
        group=GROUP,
        summary="Compare a folder of Markdown tickets with Jira",
        when="Use before you pull or push, or to see whether a folder of Markdown tickets is in step with Jira: it reads Jira and the files and changes "
        "nothing. It says per issue, and per field, whether Jira changed it, you edited the file, both did, or the file was never synced.",
        description="Reads every issue of the project (or the ones named with --issue) and every `*.md` file of the folder that holds a `jira_key` or "
        "`issue_key` line, and compares what is kept in step: summary, priority, labels, components, fix versions, story points, acceptance criteria and "
        "the description. Every field is compared with what the file and Jira last agreed on, which the `jira_base` line records (`pull` and `push` write it): "
        "a field that Jira changed is `jira changed`, one that you edited is `edited here`, an edit of one field and a change of another make `both sides` "
        "(no conflict), the same field changed on both sides is a `conflict`, and a field that differs where nothing was ever agreed is `differs`. "
        "Files and issues listed in `jira-sync-ignore.txt`, files of another project and files without a key are left out and said so with --json.",
        args=(
            Arg(("directory",), "The folder of the Markdown tickets.", required=True, metavar="DIRECTORY"),
            _PROJECT,
            _ISSUE,
            Arg(("--show-same",), "Also list the issues that are in step (default: only the ones that need a decision).", kind="flag"),
        ),
        examples=(
            Example("jira diff docs/tickets", "what differs between the mirror and Jira"),
            Example("jira diff tmp/jira --issue PROJ-33 --json"),
        ),
        notes=(
            "Not compared on purpose: assignee, status, sprint and comments (people are not mirrored, and the rest changes too often).",
            "A file that says `mirror: sanitized`, or whose text holds a relative link (outside code; a link to an issue key, and an attached image (`!shot.png!` in Jira's markup), are no relative link), is compared without its description. "
            "A list that holds a name with a comma (a component `Data, Quality`) is not compared either: a line of comma separated names cannot write it. The ones left out are named on standard error and with --json.",
            "Blanks and line breaks do not make two texts differ, so an edit of nothing but the spacing is not sent. It still counts as an edit: `pull` does not note it as agreed, and a later change of Jira's text is a conflict, not an overwrite.",
        ),
        json_output=True,
        output="scope, fetchedAt, files, issues, counts{same, differs, jira-changed, local-changed, mixed, conflict, only-jira, only-local, duplicate, unreadable}, "
        "entries[] (key, state, fields[], editedHere[], changedInJira[], conflicting[], unknown[], file, message, skipped{}; without the ones in step unless "
        "--show-same) and leftOut[].",
        handler=run_diff,
    ),
    Command(
        name="pull",
        group=GROUP,
        summary="Bring Jira into a folder of Markdown tickets",
        when="Use to get Jira into a folder of Markdown files: as a copy to read and search offline together with the code, or to keep a mirror current. "
        "New issues become files, a file that Jira changed is rewritten with --update, and a file you edited is not overwritten without --force. It never changes Jira.",
        description="Writes a file `KEY-slug.md` for every issue that has none (the format `import-md` reads: front matter, then the description as "
        "Markdown), notes the files that already hold what Jira holds (two lines, `jira_base` and `jira_synced`), and with --update rewrites the fields "
        "that Jira changed and that you did not edit since; your edit of another field stays. The same field changed on both sides is a conflict: nothing "
        "of that file is overwritten and the exit code is 3. A field that differs but was never synced is left alone unless you add --force, which lets Jira win. "
        "Nothing is deleted. People are left out: `assignee:` stays empty and mentions, e-mail addresses and personal links are redacted.",
        args=(
            Arg(("directory",), "The folder of the Markdown tickets. It is created when it is missing.", required=True, metavar="DIRECTORY"),
            _PROJECT,
            _ISSUE,
            Arg(("--update",), "Also rewrite the fields that Jira changed (and, with --force, the ones that differ or conflict).", kind="flag"),
            Arg(("--force",), "With --update: let Jira win for a field that differs without a record or conflicts: your edit of that field is lost.", kind="flag"),
            Arg(("--no-new",), "Do not write files for issues that have none.", kind="flag"),
            Arg(("--dry-run",), "Say what would be written and write nothing.", kind="flag"),
        ),
        examples=(
            Example("jira pull tmp/jira --dry-run", "what would be written"),
            Example("jira pull tmp/jira", "a copy of the project: one file per issue"),
            Example("jira pull docs/tickets --update --issue PROJ-33", "bring one change into the mirror"),
        ),
        notes=(
            "The first pull of an existing folder adds `jira_base` and `jira_synced` to every file that is in step: that is what lets the next pull and push tell who changed. Nothing else in such a file changes.",
            "A description that holds something Markdown cannot carry unchanged (a table, a panel, a colour, a macro, a code parameter, a literal star) is written as far as it goes, the file says `lossy:` with what is missing, and `push` will not send that description back.",
            "A file that is rewritten ends up with plain line feeds and without a byte order mark. The issue type and the epic of a file are written when it is made and never changed.",
            "A text that you edited in its spacing only agrees with Jira's: `pull` leaves it an edit (`skipped`, with the field in `fields`), so that a later change of Jira's text is a conflict and nothing is overwritten. It says how to end such an edit: put the spacing back, or delete the file's `jira_base` line.",
            "A file that cannot be read, or a key that two files hold, is named in the answer and left alone.",
            "Only the files of the folder are written; Jira is only read.",
        ),
        json_output=True,
        output="scope, dryRun, counts{created, updated, adopted, unchanged, skipped, conflict}, outcomes[] (key, action, file, fields[], message, lossy[]; "
        "without the unchanged), onlyLocal[], skipped{} (per key the fields that were not looked at, and why) and leftOut[].",
        handler=run_pull,
    ),
    Command(
        name="push",
        group=GROUP,
        summary="Send what you edited in a Markdown ticket to Jira",
        when="Use to send what you edited in one Markdown file to Jira, after the person has asked for it. It sends only the fields that you changed, refuses "
        "when Jira changed the same field meanwhile (exit code 3), and shows the payload with --dry-run first. Push one issue at a time.",
        description="Compares the file with the issue and sends the fields that you edited since the last sync: summary, priority, labels, components, fix "
        "versions, story points, acceptance criteria and description (Markdown converted to Jira wiki markup). A field that only Jira changed is not "
        "reverted. It refuses when the same field changed in Jira (a conflict, exit code 3), when the file was never synced and differs, when a field to be "
        "sent holds a placeholder such as [~user] where Jira "
        "holds personal data, and when the description in Jira does not come back from Markdown unchanged (a table, a panel, a code parameter, a literal star): "
        "sending it would change more than you edited. Just before the write it checks once more that "
        "the issue did not change. Afterwards it reads the issue back and notes the new state in the file. It creates no issue (`import-md` does) and "
        "never deletes anything.",
        args=(
            Arg(("target",), "One Markdown file, or a folder together with --issue.", required=True, metavar="FILE|DIRECTORY"),
            _ISSUE,
            Arg(("--force",), "Send the file's values for a field that Jira changed too, or that was never synced; a description that Markdown does not carry unchanged is sent too.", kind="flag"),
            Arg(("--dry-run",), "Show the payload and send nothing.", kind="flag"),
        ),
        examples=(
            Example("jira push docs/tickets/PROJ-33-audit-trail.md --dry-run", "what would be sent"),
            Example("jira push tmp/jira --issue PROJ-33", "the file of PROJ-33 in that folder"),
        ),
        notes=(
            "A placeholder is never sent, not even with --force: it stands where Jira holds personal data, so sending it would overwrite the real text. This holds for names in a list (a label) too.",
            "A bullet made with `-` comes back as `*`: the words are the same, but Jira may draw the two bullets differently. That does not stop a description from being sent.",
            "A file that says `mirror: sanitized`, or whose text holds a relative link, does not send its description, and a list with a name that holds a comma is not sent either: the answer names what was left out (`not compared: description`). A file named here is pushed even when `jira-sync-ignore.txt` lists its issue: you chose it.",
            "When Jira holds the change but the file could not be brought up to date, the answer says `pushed` with the reason: the next `pull` notes the new state.",
        ),
        writes=True,
        json_output=True,
        output="dryRun, pushed, unchanged, refused, outcomes[]: key, action (pushed, unchanged, refused), file, fields[], payload, message.",
        handler=run_push,
    ),
)
