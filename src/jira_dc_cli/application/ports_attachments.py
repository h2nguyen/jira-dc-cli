"""Port operations for attachments: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, Sequence, runtime_checkable

from ..domain.attachments import AttachmentLimits, UploadFile
from ..domain.model import Attachment


@runtime_checkable
class CreatesExclusively(Protocol):
    """What the `Files` of the real file system can do besides what `Files` says, and a file system in memory need not: create a file
    only when nothing is there, in one step. A check followed by a write leaves room for another run that writes the same name in
    between."""

    def create_bytes(self, path: str, data: bytes) -> None:
        """The file with `data`, missing folders created. `FileExistsError` when something is at `path`, also when it appeared a moment
        ago; what is there is then left as it is. The file is never seen half written."""


@runtime_checkable
class ResolvesPaths(Protocol):
    """The other ability: one name for a place, so that two spellings of one file can be told to be one (a relative and an absolute path,
    a path through a link, `..`, the case on Windows)."""

    def resolve(self, path: str) -> str:
        """The path made absolute with links followed and `..` folded, and on a system that ignores case, in one case."""


class AttachmentsPort(ABC):
    """What the application asks of Jira for attachments."""

    @abstractmethod
    def list_attachments(self, key: str) -> list[Attachment]:
        """The attachments of an issue, as `GET /issue/{key}?fields=attachment` reports them. NotFoundError for an unknown issue."""

    @abstractmethod
    def attachment_limits(self) -> AttachmentLimits:
        """Whether attachments are switched on and the largest upload (`GET /attachment/meta`)."""

    @abstractmethod
    def download_attachment(self, attachment: Attachment, max_bytes: int) -> bytes:
        """The content of one attachment, fetched from the configured Jira only (the address is built from the id and the file name, not
        taken from the metadata). ValidationError when it is longer than `max_bytes`; the reading stops there."""

    @abstractmethod
    def upload_attachments(self, key: str, files: Sequence[UploadFile]) -> list[Attachment]:
        """Adds the files to an issue (`POST /issue/{key}/attachments`, one multipart part per file) and returns the new attachments.
        Never retried."""
