"""Use cases of attachments, reached as `app.attachments`: list them, save them, read them as text, attach files."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from ..domain.attachments import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CHARS,
    KIND_OTHER,
    KIND_TEXT,
    AttachmentLimits,
    UploadFile,
    by_name,
    cut_text,
    guess_mime_type,
    human_size,
    kind_of,
    looks_binary,
    printable,
    select_attachments,
    too_large_many,
    upload_payload,
)
from ..domain.errors import JiraCliError, UsageError, ValidationError
from ..domain.filenames import unique_filenames
from ..domain.model import Attachment, parse_issue_key
from ..domain.multipart import is_sendable_name
from ..domain.ooxml import UnreadableDocument, office_text
from ..domain.payloads import comment_payload
from ..domain.privacy import normalize_newlines, redact_text
from ..domain.textio import decode_text
from .ports import Clock, Diagnostics, Files, JiraGateway
from .ports_attachments import CreatesExclusively, ResolvesPaths
from .settings import Settings

OUT_FOLDER = ("tmp", "jira-attachments")  # the default folder of `download` is tmp/jira-attachments/KEY
_NAMED = 12  # file names listed in a message before "and N more"


def _people_allowed(settings: Settings, with_users: bool) -> None:
    if with_users and settings.hide_users:
        raise ValidationError(
            "JIRA_HIDE_USERS=true: people are not printed, --with-users is not available.",
            hint="Unset JIRA_HIDE_USERS if you need to see people.",
        )


def _positive(value: int, option: str) -> None:
    if value < 1:
        raise UsageError(f"{option} must be a number greater than 0.", hint=f"Example: {option} 1000")


def _choose(gateway: JiraGateway, key: str, identifier: str, names: Sequence[str], everything: bool) -> list[Attachment]:
    return select_attachments(key, gateway.list_attachments(key), identifier=identifier, names=names, everything=everything)


def _named(names: Sequence[str]) -> str:
    """File names for a message, as text for a terminal, the first few and a count of the rest."""
    shown = ", ".join(printable(name) for name in names[:_NAMED])
    return shown + (f" and {len(names) - _NAMED} more" if len(names) > _NAMED else "")


def _place(files: Files, path: str) -> str:
    """One name for the place that `path` is, so that two spellings of one file are equal (see `ResolvesPaths`); the path itself where
    the file system cannot tell."""
    return files.resolve(path) if isinstance(files, ResolvesPaths) else path


def _directory(files: Files, place: str) -> str:
    """The folder of a resolved path with the separator at its end (`Files` knows the last name of a path, which is what is cut off)."""
    return place[: len(place) - len(files.name(place))]


def _create(files: Files, path: str, data: bytes) -> None:
    """The file, only when nothing is there: in one step where the file system can do that, else with a check before the write."""
    if isinstance(files, CreatesExclusively):
        files.create_bytes(path, data)
    elif files.exists(path):
        raise FileExistsError(path)
    else:
        files.write_bytes(path, data)


# -- list ---------------------------------------------------------------------------------------


@dataclass
class AttachmentList:
    key: str
    total: int  # what the issue has; `attachments` holds the ones that were asked for
    attachments: list[Attachment]


class ListAttachments:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, key: str, *, name: str = "", with_users: bool = False) -> AttachmentList:
        key = parse_issue_key(key)
        _people_allowed(self._settings, with_users)
        found = self._gateway.list_attachments(key)
        shown = by_name(found, name) if name.strip() else list(found)
        return AttachmentList(key, len(found), shown if with_users else [replace(item, author=None) for item in shown])


# -- save ---------------------------------------------------------------------------------------


@dataclass
class SavedFile:
    id: str
    filename: str
    mime_type: str
    size: int  # the bytes that were written; before that, what the metadata says
    path: str
    action: str  # written, overwritten; in a dry run would-write, would-overwrite, or exists and too-large (it would be refused)


@dataclass
class SaveResult:
    key: str
    out_dir: str
    dry_run: bool
    files: list[SavedFile]


@dataclass
class _Step:
    item: Attachment
    path: str
    verdict: str  # write, overwrite, or why it is refused: too-large, exists, folder


class DownloadAttachments:
    """Saves attachments as files in a folder: under names that are safe, never over a file that is there without `force` (not even when
    the file appears while the download runs), and never more than `max_bytes` of one file. What is known before the first request (a file
    over the limit, a file that is there) stops the run before it fetches anything. The files that belong to the CLI itself (`protected`:
    its configuration, which holds the token) are never written to or next to, not even with `force`."""

    def __init__(self, gateway: JiraGateway, files: Files, diagnostics: Diagnostics, settings: Settings) -> None:
        self._gateway = gateway
        self._files = files
        self._diagnostics = diagnostics
        self._settings = settings

    def execute(
        self,
        key: str,
        *,
        identifier: str = "",
        names: Sequence[str] = (),
        everything: bool = False,
        out: str = "",
        max_bytes: int = DEFAULT_MAX_BYTES,
        force: bool = False,
        dry_run: bool = False,
        with_users: bool = False,
        protected: Sequence[str] = (),
    ) -> SaveResult:
        key = parse_issue_key(key)
        _people_allowed(self._settings, with_users)  # saving shows no people, but the option is refused under the policy everywhere
        _positive(max_bytes, "--max-bytes")
        chosen = _choose(self._gateway, key, identifier, names, everything)
        folder = out.strip() or self._files.join(*OUT_FOLDER, key)
        if self._files.exists(folder) and not self._files.is_dir(folder):
            raise ValidationError(f"{folder} is a file, not a folder.", hint="Choose another folder with --out.")
        safe = unique_filenames([(item.id, item.filename) for item in chosen])
        steps = [self._step(item, self._files.join(folder, safe[item.id]), max_bytes, force) for item in chosen]
        self._refuse_own_files(steps, folder, protected)  # a dry run and --force are no exception
        self._refuse_folders(steps, folder)  # a folder where a file should go: --force does not replace a folder, a dry run says so too
        if dry_run:
            return SaveResult(key, folder, True, [self._preview(step) for step in steps])
        self._refuse(steps, folder, max_bytes)
        saved: list[SavedFile] = []
        for step in steps:
            try:
                saved.append(self._save(step, max_bytes, force, folder))
            except JiraCliError as error:
                if not saved:
                    raise
                raise self._interrupted(error, step, saved, len(steps), folder) from error
        return SaveResult(key, folder, False, saved)

    def _step(self, item: Attachment, path: str, max_bytes: int, force: bool) -> _Step:
        if self._files.is_dir(path):
            return _Step(item, path, "folder")
        if item.size > max_bytes:
            return _Step(item, path, "too-large")
        if self._files.exists(path):
            return _Step(item, path, "overwrite" if force else "exists")
        return _Step(item, path, "write")

    def _refuse_own_files(self, steps: list[_Step], folder: str, protected: Sequence[str]) -> None:
        """No file is written into the folder that holds the configuration of the CLI, the configuration itself included: an attachment named
        `config` would replace the token and the address that it is sent to. The places are compared, not the spellings, so a relative path,
        a link or another case of the same name is caught too. (Folders below it are no risk: the CLI reads nothing there.)"""
        folders = {_directory(self._files, _place(self._files, path)) for path in protected if path}
        if not folders:
            return
        for step in steps:
            if _directory(self._files, _place(self._files, step.path)) in folders:
                raise UsageError(
                    f"{printable(self._files.name(step.path))} would be written to {printable(folder)}, where the CLI keeps its own configuration "
                    "(with the token): an attachment could replace it.",
                    hint="Choose another folder with --out. This is refused with --force too.",
                )

    def _refuse_folders(self, steps: list[_Step], folder: str) -> None:
        blocked = [self._files.name(step.path) for step in steps if step.verdict == "folder"]
        if blocked:
            what = f"{printable(blocked[0])} is a folder" if len(blocked) == 1 else f"{len(blocked)} of the names ({_named(blocked)}) are folders"
            raise ValidationError(
                f"{what} in {printable(folder)}, where a file would be written.",
                hint="Nothing was written. Remove or rename the folder, or choose another folder with --out; --force does not replace a folder.",
            )

    @staticmethod
    def _preview(step: _Step) -> SavedFile:
        action = f"would-{step.verdict}" if step.verdict in ("write", "overwrite") else step.verdict
        return SavedFile(step.item.id, step.item.filename, step.item.mime_type, step.item.size, step.path, action)

    def _refuse(self, steps: list[_Step], folder: str, max_bytes: int) -> None:
        large = [(step.item.filename, step.item.size) for step in steps if step.verdict == "too-large"]
        if large:
            raise too_large_many(large, max_bytes)
        present = [self._files.name(step.path) for step in steps if step.verdict == "exists"]
        if present:
            what = f"{printable(present[0])} already exists" if len(present) == 1 else f"{len(present)} files already exist ({_named(present)})"
            raise ValidationError(
                f"{what} in {printable(folder)}.",
                hint="Nothing was written. Use --force to overwrite, --out to choose another folder, "
                "or --dry-run to see what would be written.",
            )

    def _save(self, step: _Step, max_bytes: int, force: bool, folder: str) -> SavedFile:
        item = step.item
        data = self._gateway.download_attachment(item, max_bytes)
        if force:
            self._files.write_bytes(step.path, data)
        else:
            try:
                _create(self._files, step.path, data)
            except FileExistsError as error:  # it was not there when the run was planned: somebody made it while the download ran
                raise ValidationError(
                    f"{printable(self._files.name(step.path))} appeared in {printable(folder)} while the download ran. It was left as it is.",
                    hint="Use --force to overwrite it, or --out to choose another folder.",
                ) from error
        if len(data) != item.size:
            self._diagnostics.warn(f"{printable(item.filename)}: Jira lists {item.size} bytes and {len(data)} arrived.")
        action = "overwritten" if step.verdict == "overwrite" else "written"
        return SavedFile(item.id, item.filename, item.mime_type, len(data), step.path, action)

    @staticmethod
    def _interrupted(error: JiraCliError, step: _Step, saved: list[SavedFile], total: int, folder: str) -> JiraCliError:
        """The error of a run that failed after it had written files: it says which, because a new run meets them (`exists`)."""
        written = [{"id": item.id, "filename": item.filename, "path": item.path, "size": item.size} for item in saved]
        advice = "A new run stops at the files that are there: add --force to write them again, or choose the missing ones by ID or --name."
        return type(error)(
            f"{error.message} {len(saved)} of the {total} files were written to {printable(folder)} before that: "
            f"{_named([item.filename for item in saved])}.",
            hint=f"{error.hint} {advice}".strip(),
            details={**error.details, "written": written, "failed": {"id": step.item.id, "filename": step.item.filename}},
        )


# -- read as text -------------------------------------------------------------------------------


@dataclass
class AttachmentText:
    id: str
    filename: str
    mime_type: str
    size: int
    kind: str  # text, docx, xlsx, pptx, or other (it cannot be shown as text)
    action: str  # read, not-text (there is no text to show: see the hint); in a dry run would-read or too-large (it would be refused)
    text: str = ""
    truncated: bool = False
    total_chars: int | None = None  # the length of the whole text before it was cut; None when there is none
    hint: str = ""


@dataclass
class TextResult:
    key: str
    dry_run: bool
    attachments: list[AttachmentText] = field(default_factory=list)


class ReadAttachmentText:
    """The text of attachments instead of a file: text files and Word, Excel and PowerPoint documents. Anything else gets its metadata and
    a hint that points to the plain download; bytes are never shown."""

    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics | None = None) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(
        self,
        key: str,
        *,
        identifier: str = "",
        names: Sequence[str] = (),
        everything: bool = False,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_chars: int = DEFAULT_MAX_CHARS,
        with_users: bool = False,
        dry_run: bool = False,
    ) -> TextResult:
        key = parse_issue_key(key)
        _people_allowed(self._settings, with_users)
        _positive(max_bytes, "--max-bytes")
        _positive(max_chars, "--max-chars")
        chosen = _choose(self._gateway, key, identifier, names, everything)
        kinds = {item.id: kind_of(item.mime_type, item.filename) for item in chosen}
        large = [(item.filename, item.size) for item in chosen if kinds[item.id] != KIND_OTHER and item.size > max_bytes]
        if large and not dry_run:
            raise too_large_many(large, max_bytes)
        result = TextResult(key, dry_run)
        for item in chosen:
            entry = AttachmentText(item.id, item.filename, item.mime_type, item.size, kinds[item.id], "not-text")
            if entry.kind == KIND_OTHER:
                entry.hint = (
                    f"Not shown as text ({item.mime_type or 'unknown type'}). Save it with `jira download {key} {item.id}` "
                    "and open it with a program that knows the type."
                )
            elif dry_run:
                entry.action = "too-large" if item.size > max_bytes else "would-read"
            else:
                self._read(key, item, entry, max_bytes, max_chars, with_users)
            result.attachments.append(entry)
        return result

    def _read(self, key: str, item: Attachment, entry: AttachmentText, max_bytes: int, max_chars: int, with_users: bool) -> None:
        data = self._gateway.download_attachment(item, max_bytes)
        entry.size = len(data)
        try:
            text = self._decode(entry.kind, data)
        except UnreadableDocument as error:
            entry.hint = f"{error.message} Save the file with `jira download {key} {item.id}` instead."
            return
        except Exception as error:  # noqa: BLE001 - one file that breaks the reader must not stop the others (`--all`); the class says enough
            if self._diagnostics is not None:
                self._diagnostics.debug(f"{printable(item.filename)}: the reader failed with {type(error).__name__}")
            entry.hint = f"The content could not be read ({type(error).__name__}). Save the file with `jira download {key} {item.id}` instead."
            return
        entry.action = "read"
        text = printable(text)  # an escape sequence in a file must not reach the terminal of the person who reads the answer
        if not with_users:
            text = redact_text(text)  # before the cut, so that an address cannot be cut in half and slip through
        entry.total_chars = len(text)
        entry.text, entry.truncated = cut_text(text, max_chars)
        if not text.strip():
            entry.hint = "The file holds no text."

    @staticmethod
    def _decode(kind: str, data: bytes) -> str:
        if kind != KIND_TEXT:
            return office_text(data, kind)
        if looks_binary(data):
            raise UnreadableDocument("The content is binary, although the name or the type says text.")
        return normalize_newlines(decode_text(data))


# -- attach -------------------------------------------------------------------------------------


@dataclass
class PlannedFile:
    name: str
    path: str
    size: int
    mime_type: str
    existing: list[str] = field(default_factory=list)  # ids of the attachments of the issue that have this name already


@dataclass
class AttachResult:
    key: str
    dry_run: bool
    files: list[PlannedFile]
    payload: dict[str, Any]
    limits: AttachmentLimits
    warnings: list[str] = field(default_factory=list)
    attached: list[Attachment] = field(default_factory=list)
    comment_id: str = ""


class AttachFiles:
    """Adds files to an issue. The dry run and the real run check the same things first, and a file that Jira would refuse is refused
    before any upload request.

    A file is also refused that must not be published: the configuration of the CLI (`protected`), and any file that holds the token
    (the bytes are looked at, so a copy of the configuration or a log with a header dump is found too). Everyone who can see the issue
    can open an attachment, and the CLI cannot take it back."""

    def __init__(self, gateway: JiraGateway, files: Files, settings: Settings) -> None:
        self._gateway = gateway
        self._files = files
        self._settings = settings

    def execute(
        self, key: str, paths: Sequence[str], *, comment: str | None = None, dry_run: bool = False, protected: Sequence[str] = ()
    ) -> AttachResult:
        key = parse_issue_key(key)
        if not paths:
            raise UsageError("Name at least one file to attach.", hint="Example: jira attach PROJ-123 shot.png --dry-run")
        text = (comment or "").strip()
        if comment is not None and not text:
            raise ValidationError("The comment is empty.", hint="Pass text with --comment, or leave the option out.")
        planned = self._plan(paths)
        self._refuse_own_files(planned, protected)
        existing = self._gateway.list_attachments(key)  # proves that the issue exists, and tells which names it has
        limits = self._gateway.attachment_limits()
        self._check(planned, limits)
        contents = self._read(planned, keep=not dry_run)  # a dry run reads them too: it must refuse what a real run would refuse
        for item in planned:
            item.existing = [other.id for other in by_name(existing, item.name)]
        warnings = self._warnings(key, planned)
        payload = upload_payload(key, [UploadFile(item.name, item.mime_type, item.size) for item in planned], text)
        if dry_run:
            return AttachResult(key, True, planned, payload, limits, warnings)
        created = self._gateway.upload_attachments(
            key, [UploadFile(item.name, item.mime_type, len(data), data) for item, data in zip(planned, contents)]
        )
        return AttachResult(key, False, planned, payload, limits, warnings, created, self._comment(key, text, created) if text else "")

    def _plan(self, paths: Sequence[str]) -> list[PlannedFile]:
        """The files with their names, sizes and types. A file that is missing, a folder, empty or has a name that is no text is an error;
        all of them are named."""
        planned: list[PlannedFile] = []
        problems: list[str] = []
        for path in paths:
            shown = printable(path)
            name = self._files.name(path)
            if self._files.is_dir(path):
                problems.append(f"{shown} is a folder")
                continue
            try:
                size = self._files.file_size(path)
            except ValidationError:  # the size is the one question that is always asked: it also tells that the file is not there
                problems.append(f"{shown} does not exist" if not self._files.exists(path) else f"{shown} cannot be read")
                continue
            if not is_sendable_name(name):
                problems.append(f"{shown} has a name that is no text (damaged characters): Jira cannot be given it")
                continue
            if size == 0:
                problems.append(f"{shown} is empty")
            planned.append(PlannedFile(name, path, size, guess_mime_type(name)))
        if problems:
            raise ValidationError(
                "Cannot attach: " + "; ".join(problems) + ".",
                hint="Nothing was sent. Check the paths (a name with blanks goes in quotes, or after --path; a damaged name needs the file to "
                "be renamed); Jira refuses an empty file.",
            )
        return planned

    def _refuse_own_files(self, planned: list[PlannedFile], protected: Sequence[str]) -> None:
        """The configuration of the CLI is never attached: it holds the token. The places are compared, not the spellings: a link to it, a
        relative path or another case of its name is caught. Only a file of the same size can be that file, so only those are resolved
        (finding out where a link leads costs more than anything else here, and a command line can name a thousand files)."""
        existing = [path for path in protected if path and self._files.exists(path)]
        if not existing:
            return
        sizes = {self._files.file_size(path) for path in existing}
        own = {_place(self._files, path) for path in existing}
        for item in planned:
            if item.size in sizes and _place(self._files, item.path) in own:
                raise UsageError(
                    f"{printable(item.path)} is the configuration file of this CLI. It holds the access token and is never attached.",
                    hint="Nothing was sent. Everyone who can see the issue can open an attachment.",
                )

    def _read(self, planned: list[PlannedFile], *, keep: bool) -> list[bytes]:
        """The bytes of the files, each looked at for the token; kept only for a real run."""
        contents: list[bytes] = []
        for item in planned:
            data = self._files.read_bytes(item.path)
            if _holds_token(data, self._settings.pat):
                raise UsageError(
                    f"{printable(item.path)} holds the access token of this CLI. It would be published to everyone who can see the issue.",
                    hint="Nothing was sent. Attach a file without the token (a copy of the configuration, a shell history or a log "
                    "with the headers of a request are where it usually is), and tell the owner of the token to replace it if it was sent anywhere.",
                )
            if keep:
                contents.append(data)
        return contents

    @staticmethod
    def _check(planned: list[PlannedFile], limits: AttachmentLimits) -> None:
        if not limits.enabled:
            raise ValidationError(
                "Attachments are switched off in this Jira.", hint="Ask a Jira administrator to switch them on. Nothing was sent."
            )
        over = [item for item in planned if limits.upload_limit and item.size > limits.upload_limit]
        if over:
            listed = ", ".join(f"{printable(item.name)} ({human_size(item.size)})" for item in over)
            verb = "is" if len(over) == 1 else "are"
            raise ValidationError(
                f"{listed} {verb} larger than the upload limit of {human_size(limits.upload_limit)} that Jira reports.",
                hint="Nothing was sent. Jira refuses larger files: shrink or split the file, or attach the part that matters.",
            )

    @staticmethod
    def _warnings(key: str, planned: list[PlannedFile]) -> list[str]:
        warnings = [
            f"{printable(item.name)} exists already on {key} (attachment {', '.join(item.existing)}): Jira keeps both files side by side."
            for item in planned
            if item.existing
        ]
        seen: set[str] = set()
        for item in planned:
            if item.name.casefold() in seen:
                warnings.append(f"{printable(item.name)} is given more than once: Jira keeps every copy.")
            seen.add(item.name.casefold())
        return warnings

    def _comment(self, key: str, text: str, created: list[Attachment]) -> str:
        """The comment after the upload. When it fails the files are attached already: the error says so, has the exit code of the
        comment's error and keeps what was uploaded in its details."""
        try:
            return self._gateway.add_comment(key, comment_payload(text)).id
        except JiraCliError as error:
            ids = ", ".join(item.id for item in created) or "ids not reported"
            raise type(error)(
                f"The files were attached to {key} (attachment {ids}), but the comment was not added: {error.message}",
                hint=error.hint or f"Add the comment with `jira comment {key} --text ...`.",
                details={**error.details, "attached": [{"id": item.id, "filename": item.filename, "size": item.size} for item in created]},
            ) from error


def _holds_token(data: bytes, token: str) -> bool:
    """Whether the bytes hold the token, written as UTF-8 or as UTF-16 (a file made by a Windows tool). Never shows it."""
    if not token:
        return False
    return any(token.encode(encoding, "replace") in data for encoding in ("utf-8", "utf-16-le", "utf-16-be"))


@dataclass
class AttachmentsUseCases:
    """One field per use case, reached as `app.attachments.<name>.execute(...)`."""

    list_attachments: ListAttachments
    download: DownloadAttachments
    read_text: ReadAttachmentText
    attach: AttachFiles


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> AttachmentsUseCases:
    return AttachmentsUseCases(
        ListAttachments(gateway, settings),
        DownloadAttachments(gateway, files, diagnostics, settings),
        ReadAttachmentText(gateway, settings, diagnostics),
        AttachFiles(gateway, files, settings),
    )
