"""Importing tickets from Markdown files (one file or a folder) and from CSV."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

from ..domain.drafts import IssueChanges, IssueDraft, parse_json_object, split_list
from ..domain.errors import ConfigError, JiraCliError, ValidationError
from ..domain.fields import EPIC_LINK
from ..domain.frontmatter import CsvRow, parse_csv, parse_markdown_ticket
from ..domain.privacy import holds_placeholder
from ..domain.wiki import markdown_to_wiki
from .ports import Diagnostics, Files, JiraGateway
from .settings import Settings
from .writing import CreateIssue, UpdateIssue

MAP_HEADER = ["source_id", "source_key", "jira_key", "issue_type", "status", "file"]
MAP_FILE_NAME = "jira-import-map.csv"
MARKDOWN_PATTERN = "[0-9][0-9][0-9]-*.md"


@dataclass
class ImportOutcome:
    file: str
    action: str  # created | updated | skipped | failed
    key: str = ""
    dry_run: bool = False
    message: str = ""
    payload: dict[str, Any] | None = None


@dataclass
class BatchImport:
    outcomes: list[ImportOutcome] = field(default_factory=list)
    map_file: str = ""
    first_error: JiraCliError | None = None

    def count(self, action: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.action == action)


def _optional(value: str) -> str | None:
    return value if value else None


def _optional_list(value: str) -> list[str] | None:
    return split_list(value) if value else None


def _refuse_placeholders(where: str, texts: dict[str, Any]) -> None:
    """An update from a text that holds what `redact_text` writes in place of personal data would overwrite the real text in Jira with it."""
    marked = [name for name, value in texts.items() if isinstance(value, str) and holds_placeholder(value)]
    if marked:
        raise ValidationError(
            f"{where}: {', '.join(marked)} holds a placeholder ([~user], [personal link] or [email]) where Jira holds personal data; updating the issue from it would overwrite the real text.",
            hint="Use `jira push` for a file that `jira pull` wrote: it leaves such a field alone. `jira update` changes a field on purpose.",
        )


class ImportMarkdown:
    """One Markdown file: with `issue_key` in the front matter it updates, otherwise it creates."""

    def __init__(
        self,
        files: Files,
        gateway: JiraGateway,
        settings: Settings,
        create: CreateIssue,
        update: UpdateIssue,
        diagnostics: Diagnostics,
    ) -> None:
        self._files = files
        self._gateway = gateway
        self._settings = settings
        self._create = create
        self._update = update
        self._diagnostics = diagnostics

    def execute(
        self,
        path: str,
        *,
        wiki: bool = False,
        dry_run: bool = False,
        skip_invalid_fix_version: bool = False,
        extra_fields: dict[str, Any] | None = None,
    ) -> ImportOutcome:
        if not self._files.exists(path):
            raise ValidationError(f"File not found: {path}")
        text = self._files.read_text(path)
        ticket = parse_markdown_ticket(text)
        meta = ticket.meta
        description = markdown_to_wiki(text).rstrip() if wiki else ticket.description
        fields = parse_json_object(meta.get("fields_json") or "{}", "fields_json")
        fields.update(extra_fields or {})

        if meta.get("issue_key"):
            _refuse_placeholders(
                path,
                {
                    "summary": meta.get("summary", ""),
                    "description": description,
                    "labels": meta.get("labels", ""),
                    "components": meta.get("components", ""),
                    "fix_versions": meta.get("fix_versions", ""),
                    **fields,
                },
            )
            changes = IssueChanges(
                summary=_optional(meta.get("summary", "")),
                description=_optional(description),
                labels=_optional_list(meta.get("labels", "")),
                priority=_optional(meta.get("priority", "")),
                assignee=_optional(meta.get("assignee", "")),
                components=_optional_list(meta.get("components", "")),
                fix_versions=_optional_list(meta.get("fix_versions", "")),
                fields=fields,
            )
            result = self._update.execute(meta["issue_key"], changes, dry_run=dry_run)
            return ImportOutcome(path, "updated", result.key, dry_run, payload=result.payload)

        project = meta.get("project") or self._settings.project
        if not project:
            raise ValidationError("The Markdown has no project and no current project is selected.", hint="Add `project:` to the front matter or run `jira use KEY`.")
        if not meta.get("issuetype"):
            raise ValidationError("The Markdown front matter requires `issuetype`.")
        if not meta.get("summary"):
            raise ValidationError("The Markdown front matter requires `summary`.")
        draft = IssueDraft(
            project=project,
            issue_type=meta["issuetype"],
            summary=meta["summary"],
            description=description,
            labels=split_list(meta.get("labels", "")),
            priority=meta.get("priority", ""),
            assignee=meta.get("assignee", ""),
            components=split_list(meta.get("components", "")),
            fix_versions=self._existing_versions(project, split_list(meta.get("fix_versions", "")), skip_invalid_fix_version),
            fields=fields,
        )
        result = self._create.execute(draft, dry_run=dry_run)
        return ImportOutcome(path, "created", result.key, dry_run, payload=result.payload)

    def _existing_versions(self, project: str, requested: list[str], skip_invalid: bool) -> list[str]:
        if not requested:
            return []
        known = {name.casefold() for name in self._gateway.list_versions(project.upper())}
        valid: list[str] = []
        for name in requested:
            if name.casefold() in known:
                valid.append(name)
            elif skip_invalid:
                self._diagnostics.warn(f"Skipping invalid Fix Version '{name}' for project {project}.")
            else:
                raise ValidationError(
                    f"Fix Version '{name}' does not exist in project {project}.",
                    hint="Create it in Project settings > Versions, or use --skip-invalid-fix-version.",
                )
        return valid


class ImportMarkdownDirectory:
    """All `NNN-*.md` files of a folder in name order. A map file remembers what was created, so a
    run can be repeated or resumed, and `parent_id` can be turned into an Epic Link."""

    def __init__(
        self, files: Files, gateway: JiraGateway, settings: Settings, import_one: ImportMarkdown, diagnostics: Diagnostics
    ) -> None:
        self._files = files
        self._gateway = gateway
        self._settings = settings
        self._import_one = import_one
        self._diagnostics = diagnostics

    def execute(
        self,
        directory: str,
        *,
        wiki: bool = False,
        dry_run: bool = False,
        continue_on_error: bool = False,
        map_file: str = "",
        skip_invalid_fix_version: bool = False,
    ) -> BatchImport:
        if not self._files.is_dir(directory):
            raise ValidationError(f"Directory not found: {directory}")
        map_path = map_file or self._files.join(directory, MAP_FILE_NAME)
        files = self._files.list_matching(directory, MARKDOWN_PATTERN)
        self._reject_duplicate_source_ids(files)
        if not dry_run:
            self._prepare_map(map_path)
        created = self._read_map(map_path)
        planned: set[str] = set()  # source ids a dry run would have created before the current file
        batch = BatchImport(map_file=map_path)
        for index, path in enumerate(files, start=1):
            name = self._files.name(path)
            self._diagnostics.info(f"Markdown {index}: {name}")
            try:
                meta = parse_markdown_ticket(self._files.read_text(path)).meta
                source_id, parent_id = meta.get("source_id", ""), meta.get("parent_id", "")
                if source_id and source_id in created:
                    self._diagnostics.info(f"Skipping source_id={source_id}; already created as {created[source_id]}")
                    batch.outcomes.append(ImportOutcome(path, "skipped", created[source_id], dry_run))
                    continue
                outcome = self._import_one.execute(
                    path,
                    wiki=wiki,
                    dry_run=dry_run,
                    skip_invalid_fix_version=skip_invalid_fix_version,
                    extra_fields=self._parent_link(parent_id, created, planned if dry_run else None) if parent_id else None,
                )
            except JiraCliError as error:
                self._diagnostics.warn(f"Failed: {name}: {error.message}")
                batch.outcomes.append(ImportOutcome(path, "failed", message=error.message))
                batch.first_error = batch.first_error or error
                if not continue_on_error:
                    break
                continue
            batch.outcomes.append(outcome)
            if dry_run:
                if source_id:
                    planned.add(source_id)
            elif source_id and outcome.action == "created" and outcome.key:
                try:
                    self._append_map(map_path, [source_id, meta.get("source_key", ""), outcome.key, meta.get("issuetype", ""), "Created", name])
                except JiraCliError as error:
                    # The issue exists but is not recorded: a repeated run would create it again. Stop and say so.
                    message = f"created {outcome.key}, but the map file could not be updated: {error.message}. Add the row by hand before you import again."
                    self._diagnostics.warn(message)
                    batch.outcomes.append(ImportOutcome(path, "failed", outcome.key, message=message))
                    batch.first_error = batch.first_error or error
                    break
                created[source_id] = outcome.key
        return batch

    def _reject_duplicate_source_ids(self, files: list[str]) -> None:
        """Two files with one source_id: the second would be skipped as "already created", silently."""
        seen: dict[str, str] = {}
        for path in files:
            try:
                source_id = parse_markdown_ticket(self._files.read_text(path)).meta.get("source_id", "")
            except JiraCliError:
                continue  # an unreadable file is reported when its turn comes
            if source_id in seen:
                raise ValidationError(
                    f"source_id {source_id} is used by two files: {self._files.name(seen[source_id])} and {self._files.name(path)}.",
                    hint="Give every ticket its own source_id; the map file identifies a ticket by it.",
                )
            if source_id:
                seen[source_id] = path

    def _prepare_map(self, path: str) -> None:
        """Create the map file (and its folders) before the first issue is created: a created issue must never go unrecorded."""
        if self._files.exists(path) and self._files.read_text(path).strip():
            return
        try:
            self._files.write_text(path, ",".join(MAP_HEADER) + "\n")
        except JiraCliError as error:
            raise ValidationError(f"The map file cannot be written: {error.message}", hint="Fix the path of --map-file; nothing was created.") from error

    def _parent_link(self, parent_id: str, created: dict[str, str], planned: set[str] | None = None) -> dict[str, Any]:
        parent_key = created.get(parent_id)
        if not parent_key and planned is not None and parent_id in planned:
            parent_key = f"<key of {parent_id}, assigned when it is created>"  # a dry run cannot know it
        if not parent_key:
            raise ValidationError(f"Parent reference {parent_id} has not been imported.", hint="Import the parent first; it must be in the map file.")
        field_id = self._settings.epic_link_field or self._gateway.field_ids().get(EPIC_LINK)
        if not field_id:
            raise ConfigError(f"Cannot link parent {parent_id}: the Epic Link field was not found.", hint="Set JIRA_EPIC_LINK_FIELD to its id (see `jira fields --search epic`).")
        return {field_id: parent_key}

    def _read_map(self, path: str) -> dict[str, str]:
        if not self._files.exists(path):
            return {}
        rows = csv.DictReader(io.StringIO(self._files.read_text(path), newline=""))
        return {
            row["source_id"]: row["jira_key"]
            for row in rows
            if row.get("status") == "Created" and row.get("source_id") and row.get("jira_key")
        }

    def _append_map(self, path: str, row: list[str]) -> None:
        existing = self._files.read_text(path) if self._files.exists(path) else ""
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        if not existing.strip():
            writer.writerow(MAP_HEADER)
        writer.writerow(row)
        # A hand-edited map may not end with a newline: every row must start on its own line.
        prefix = "\n" if existing and not existing.endswith("\n") else ""
        self._files.append_text(path, prefix + buffer.getvalue())


class ImportCsv:
    """One issue per row: with `issue_key` the row updates, otherwise it creates."""

    def __init__(self, files: Files, settings: Settings, create: CreateIssue, update: UpdateIssue) -> None:
        self._files = files
        self._settings = settings
        self._create = create
        self._update = update

    def execute(self, path: str, *, dry_run: bool = False, continue_on_error: bool = False) -> BatchImport:
        if not self._files.exists(path):
            raise ValidationError(f"File not found: {path}")
        batch = BatchImport()
        for row in parse_csv(self._files.read_text(path, "utf-8-sig")):
            try:
                batch.outcomes.append(self._import_row(row, dry_run))
            except JiraCliError as error:
                batch.outcomes.append(ImportOutcome(path, "failed", message=f"line {row.line}: {error.message}"))
                batch.first_error = batch.first_error or error
                if not continue_on_error:
                    break
        return batch

    def _import_row(self, row: CsvRow, dry_run: bool) -> ImportOutcome:
        fields = parse_json_object(row.get("fields_json") or "{}", f"fields_json in line {row.line}")
        issue_key = row.get("issue_key")
        if issue_key:
            _refuse_placeholders(
                f"line {row.line}",
                {
                    "summary": row.get("summary"),
                    "description": row.get("description"),
                    "labels": row.get("labels"),
                    "components": row.get("components"),
                    "fix_versions": row.get("fix_versions", "fixVersions"),
                    **fields,
                },
            )
            changes = IssueChanges(
                summary=_optional(row.get("summary")),
                description=_optional(row.get("description")),
                labels=_optional_list(row.get("labels")),
                priority=_optional(row.get("priority")),
                assignee=_optional(row.get("assignee")),
                components=_optional_list(row.get("components")),
                fix_versions=_optional_list(row.get("fix_versions", "fixVersions")),
                fields=fields,
            )
            result = self._update.execute(issue_key, changes, dry_run=dry_run)
            return ImportOutcome(f"line {row.line}", "updated", result.key, dry_run, payload=result.payload)
        draft = IssueDraft(
            project=row.get("project") or self._settings.project,
            issue_type=row.get("issuetype", "type"),
            summary=row.get("summary"),
            description=row.get("description"),
            labels=split_list(row.get("labels")),
            priority=row.get("priority"),
            assignee=row.get("assignee"),
            components=split_list(row.get("components")),
            fix_versions=split_list(row.get("fix_versions", "fixVersions")),
            fields=fields,
        )
        result = self._create.execute(draft, dry_run=dry_run)
        return ImportOutcome(f"line {row.line}", "created", result.key, dry_run, payload=result.payload)
