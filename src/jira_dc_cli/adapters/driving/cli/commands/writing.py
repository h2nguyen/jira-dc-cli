"""Commands that create issues: create, create-json, import-md, import-md-dir, import-csv."""
from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from .....domain.errors import UsageError
from ..runtime import Context
from ..spec import Arg, Command, Example
from ._common import POWERSHELL_JSON_NOTE, fields_json_option, pretty, text_option
from .workflow import DRY_RUN, WIKI, report_warnings, write_text

if TYPE_CHECKING:
    from .....application.imports import BatchImport, ImportOutcome
    from .....application.writing import WriteResult

GROUP = "Create and import"


def _created_text(result: WriteResult) -> str:
    if result.dry_run:
        return f"Dry run: nothing was sent\n{pretty(result.payload)}"
    return write_text(result)


# -- create --------------------------------------------------------------------------------------


def run_create(ctx: Context, args: argparse.Namespace) -> int:
    from .....domain.drafts import IssueDraft, split_list

    if args.type and args.type_id:
        raise UsageError("Use either --type or --type-id, not both.")
    description = text_option(ctx, args.description, args.description_file, "description")
    if args.wiki and description:
        description = ctx.app.convert_markdown.execute(description)
    draft = IssueDraft(
        project=args.project or "",
        issue_type=args.type or args.type_id or "",
        summary=args.summary or "",
        description=description or "",
        labels=split_list(args.labels or ""),
        priority=args.priority or "",
        assignee=args.assignee or "",
        components=split_list(args.components or ""),
        fix_versions=split_list(args.fix_versions or ""),
        reporter=args.reporter or "",
        fields=fields_json_option(ctx, args.fields_json),
    )
    result = ctx.app.create_issue.execute(draft, dry_run=args.dry_run, refresh_metadata=args.refresh_metadata)
    report_warnings(ctx, result.warnings)
    ctx.print_json(result) if ctx.json_mode else ctx.print(_created_text(result))
    return 0


def run_create_json(ctx: Context, args: argparse.Namespace) -> int:
    from .....domain.drafts import parse_json_object

    payload = parse_json_object(ctx.read_input(args.file), args.file)
    result = ctx.app.create_issue_from_payload.execute(payload, dry_run=args.dry_run)
    report_warnings(ctx, result.warnings)
    ctx.print_json(result) if ctx.json_mode else ctx.print(_created_text(result))
    return 0


# -- imports -------------------------------------------------------------------------------------


def _outcome_line(outcome: ImportOutcome) -> str:
    if outcome.action == "failed":
        return f"FAILED        {outcome.file}: {outcome.message}"
    if outcome.action == "skipped":
        return f"skipped       {outcome.file} (already {outcome.key})"
    verb = {"created": "create", "updated": "update"}.get(outcome.action, outcome.action)
    label = f"would {verb}" if outcome.dry_run else outcome.action
    return f"{label:<14}{outcome.key + ' ' if outcome.key else ''}{outcome.file}"


def _batch_text(batch: BatchImport, dry_run: bool) -> str:
    lines = [_outcome_line(outcome) for outcome in batch.outcomes]
    created, updated = batch.count("created"), batch.count("updated")
    summary = f"Done: files={len(batch.outcomes)} created={created} updated={updated} skipped={batch.count('skipped')} failed={batch.count('failed')}"
    if dry_run:
        summary += " (dry run, nothing was sent)"
    return "\n".join([*lines, summary])


def _finish_batch(ctx: Context, batch: BatchImport, dry_run: bool) -> int:
    if ctx.json_mode:
        ctx.print_json(
            {
                "dryRun": dry_run,
                "mapFile": batch.map_file or None,
                "created": batch.count("created"),
                "updated": batch.count("updated"),
                "skipped": batch.count("skipped"),
                "failed": batch.count("failed"),
                "outcomes": batch.outcomes,
            }
        )
    else:
        ctx.print(_batch_text(batch, dry_run))
    if batch.first_error is not None:
        raise batch.first_error
    return 0


def run_import_md(ctx: Context, args: argparse.Namespace) -> int:
    outcome = ctx.app.import_markdown.execute(
        args.file, wiki=args.wiki, dry_run=args.dry_run, skip_invalid_fix_version=args.skip_invalid_fix_version
    )
    if ctx.json_mode:
        ctx.print_json(outcome)
    elif args.dry_run:
        verb = "create an issue" if outcome.action == "created" else f"update {outcome.key}"
        ctx.print(f"Dry run: would {verb}; nothing was sent\n{pretty(outcome.payload)}")
    else:
        ctx.print(f"{outcome.action.capitalize()} {outcome.key}")
    return 0


def run_import_md_dir(ctx: Context, args: argparse.Namespace) -> int:
    batch = ctx.app.import_markdown_directory.execute(
        args.directory,
        wiki=args.wiki,
        dry_run=args.dry_run,
        continue_on_error=args.continue_on_error,
        map_file=args.map_file or "",
        skip_invalid_fix_version=args.skip_invalid_fix_version,
    )
    return _finish_batch(ctx, batch, args.dry_run)


def run_import_csv(ctx: Context, args: argparse.Namespace) -> int:
    batch = ctx.app.import_csv.execute(args.file, dry_run=args.dry_run, continue_on_error=args.continue_on_error)
    return _finish_batch(ctx, batch, args.dry_run)


_WIKI_IMPORT = Arg(("--wiki", "--jira-wiki"), "Convert the Markdown body to Jira wiki markup.", kind="flag")
_SKIP_VERSION = Arg(
    ("--skip-invalid-fix-version",),
    "Create the issue without a fix version that does not exist in the project (default: refuse).",
    kind="flag",
)
_CONTINUE = Arg(("--continue-on-error",), "Carry on with the next item when one fails; the exit code still reports the first failure.", kind="flag")

COMMANDS = (
    Command(
        name="create",
        group=GROUP,
        summary="Create one issue",
        when="Use to create one issue after the person has asked for it. Dry-run first, show the payload, and record the key that "
        "Jira returns: never guess a key.",
        description="The project defaults to the current project (`jira use`). The issue type is resolved by name (case-insensitive) or "
        "id against the types the project can create. The reporter defaults to JIRA_REPORTER, else to the token's user. The dry run "
        "checks the priority, components and fix versions against what the project allows (exit code 6, and the allowed values, when "
        "one is not) and warns about a required field that the options leave out.",
        args=(
            Arg(("--project",), "Project key.", metavar="KEY"),
            Arg(("--type",), "Issue type name or id, for example Story.", metavar="NAME|ID"),
            Arg(("--type-id",), "Issue type id (instead of --type).", metavar="ID"),
            Arg(("--summary",), "Summary (required).", metavar="TEXT", required=True),
            Arg(("--description",), "Description.", metavar="TEXT"),
            Arg(("--description-file",), "Description from a file (`-` is standard input).", metavar="FILE"),
            WIKI,
            Arg(("--labels",), "Labels.", metavar="a,b"),
            Arg(("--priority",), "Priority, for example High.", metavar="NAME"),
            Arg(
                ("--assignee",),
                "Assignee: a user name, display name or e-mail address (one user: ambiguity is refused), me, unassigned or automatic.",
                metavar="USER",
            ),
            Arg(("--components",), "Components (they must exist).", metavar="a,b"),
            Arg(("--fix-versions",), "Fix versions (they must exist).", metavar="a,b"),
            Arg(("--fields-json",), "Any other field as JSON, or @FILE, for example custom fields. A field that has its own option (project, type, summary, reporter, ...) cannot be set here too.", metavar="JSON|@FILE"),
            Arg(("--reporter",), "Reporter user name. Default: JIRA_REPORTER or the token's user.", metavar="USER"),
            Arg(("--refresh-metadata",), "Reload the cached issue types of the project.", kind="flag"),
            Arg(("--dry-run",), "Print the payload and send nothing.", kind="flag"),
        ),
        examples=(
            Example("jira create --type Story --summary 'Export the backlog' --labels api --dry-run"),
            Example("jira create --project PROJ --type Bug --summary 'Wrong total' --description-file bug.md --wiki"),
        ),
        notes=(POWERSHELL_JSON_NOTE,),
        writes=True,
        json_output=True,
        output="action, key, payload, dryRun, created{id,key,url}, warnings.",
        handler=run_create,
    ),
    Command(
        name="create-json",
        group=GROUP,
        summary="Create one issue from a JSON payload",
        when="Use to create one issue from a JSON payload when `create` has no option for a field. Dry-run first.",
        description="Sends a complete Jira payload: an object with a \"fields\" object (project, issuetype, summary, ...). "
        "Use `jira create --dry-run` to see what a payload looks like. The dry run checks the standard fields against what the project "
        "allows when the payload names the project by key and the issue type by id.",
        args=(
            Arg(("file",), "JSON file (`-` is standard input).", required=True, metavar="PAYLOAD.json"),
            Arg(("--dry-run",), "Validate and print the payload, send nothing.", kind="flag"),
        ),
        examples=(Example("jira create-json payload.json --dry-run"),),
        writes=True,
        json_output=True,
        output="action, key, payload, dryRun, created{id,key,url}, warnings.",
        handler=run_create_json,
    ),
    Command(
        name="import-md",
        group=GROUP,
        summary="Create or update one issue from a Markdown file",
        when="Use to create one issue from a Markdown ticket. With `issue_key` in the front matter it rewrites every field of that "
        "issue from the file, so do not use it on an issue that exists: `push` sends only what you changed.",
        description="The front matter holds the fields (summary, issuetype, priority, labels, ...) and the Markdown body becomes the "
        "description. With `issue_key` in the front matter the issue is updated (every field present is rewritten), otherwise created. "
        "Front matter fields and examples: `jira how-to import-tickets`.",
        args=(Arg(("file",), "Markdown file with front matter.", required=True, metavar="FILE.md"), _WIKI_IMPORT, _SKIP_VERSION, DRY_RUN),
        examples=(Example("jira import-md examples/ticket-create.md --dry-run --wiki"),),
        notes=("Do not use it to change one field of an existing issue: it rewrites every field of the front matter.",),
        writes=True,
        json_output=True,
        output="file, action, key, dryRun, message, payload.",
        handler=run_import_md,
    ),
    Command(
        name="import-md-dir",
        group=GROUP,
        summary="Import all Markdown tickets of a folder",
        when="Use to create many issues from a folder of Markdown tickets (`NNN-*.md`); the map file makes it resumable. It needs "
        "its own explicit request and a dry run, and it is not for issues that exist.",
        description="Imports every NNN-*.md file in name order. `source_id` and `parent_id` in the front matter link a story to its epic "
        "(through the Epic Link field). A map file (default DIRECTORY/jira-import-map.csv) remembers what was created, so a run can be "
        "repeated or resumed without duplicates.",
        args=(
            Arg(("directory",), "Folder with the Markdown files.", required=True, metavar="DIRECTORY"),
            _WIKI_IMPORT,
            Arg(("--map-file",), "Map file to use instead of DIRECTORY/jira-import-map.csv.", metavar="FILE"),
            _SKIP_VERSION,
            _CONTINUE,
            Arg(("--dry-run",), "Print what would happen and send nothing.", kind="flag"),
        ),
        examples=(Example("jira import-md-dir docs/tickets --dry-run"), Example("jira import-md-dir docs/tickets --wiki --continue-on-error")),
        writes=True,
        json_output=True,
        output="dryRun, mapFile, created, updated, skipped, failed, outcomes[{file,action,key,dryRun,message}].",
        handler=run_import_md_dir,
    ),
    Command(
        name="import-csv",
        group=GROUP,
        summary="Create or update issues from a CSV file",
        when="Use to create or update many issues from a spreadsheet. It needs its own explicit request and a dry run; a row with "
        "`issue_key` rewrites the columns it names.",
        description="One issue per row. A row with an `issue_key` updates that issue, a row without one creates an issue. "
        "Columns and examples: `jira how-to import-tickets` and examples/issues.csv.",
        args=(Arg(("file",), "CSV file with a header row.", required=True, metavar="FILE.csv"), _CONTINUE, Arg(("--dry-run",), "Print every payload and send nothing.", kind="flag")),
        examples=(Example("jira import-csv examples/tickets-create.csv --dry-run"),),
        writes=True,
        json_output=True,
        output="dryRun, created, updated, skipped, failed, outcomes[{file,action,key,dryRun,message}].",
        handler=run_import_csv,
    ),
)
