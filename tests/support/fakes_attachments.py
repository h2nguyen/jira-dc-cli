"""The attachments calls of `FakeGateway`, in memory."""
from __future__ import annotations

import dataclasses
from typing import Sequence

from jira_dc_cli.domain.attachments import AttachmentLimits, UploadFile, too_large
from jira_dc_cli.domain.errors import AuthError, NotFoundError
from jira_dc_cli.domain.model import Attachment, User

UPLOADER = User("jdoe", "Jane Doe", "jane.doe@example.com")


class FakeAttachments:
    """Implements `AttachmentsPort` for the tests; its state lives on the gateway and is set up in `_init_attachments`.

    The attachments of an issue are `Issue.attachments`; their bytes are in `attachment_data` by id. `add_attachment` sets both up."""

    def _init_attachments(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.attachment_data: dict[str, bytes] = {}
        self.attachments_enabled = True
        self.attachment_upload_limit = 10 * 1024 * 1024
        self.next_attachment_id = 20000

    def add_attachment(
        self,
        key: str,
        filename: str,
        data: bytes = b"",
        mime_type: str = "application/octet-stream",
        *,
        attachment_id: str = "",
        author: User | None = UPLOADER,
        size: int | None = None,
    ) -> Attachment:
        """Give an issue an attachment. `size` is what the metadata says when it should differ from the bytes (a metadata that lies)."""
        issue = self._issue(key)
        if not attachment_id:
            self.next_attachment_id += 1
            attachment_id = str(self.next_attachment_id)
        attachment = Attachment(
            attachment_id,
            filename,
            mime_type,
            len(data) if size is None else size,
            "2026-10-05T09:30:00.000+0200",
            author,
            content_url=f"https://jira.example.com/secure/attachment/{attachment_id}/{filename}",
        )
        self.attachment_data[attachment_id] = data
        self.issues[key] = dataclasses.replace(
            issue, attachments=[*issue.attachments, attachment], attachment_total=issue.attachment_total + 1
        )
        return attachment

    def list_attachments(self, key: str) -> list[Attachment]:
        self._enter("list_attachments", key)
        return list(self._issue(key).attachments)

    def attachment_limits(self) -> AttachmentLimits:
        self._enter("attachment_limits")
        return AttachmentLimits(self.attachments_enabled, self.attachment_upload_limit)

    def download_attachment(self, attachment: Attachment, max_bytes: int) -> bytes:
        self._enter("download_attachment", attachment.id, max_bytes)
        if attachment.id not in self.attachment_data:
            raise NotFoundError(f"Not found: GET /secure/attachment/{attachment.id}/{attachment.filename}")
        data = self.attachment_data[attachment.id]
        if len(data) > max_bytes:
            raise too_large(attachment.filename, max_bytes)
        return data

    def upload_attachments(self, key: str, files: Sequence[UploadFile]) -> list[Attachment]:
        self._enter("upload_attachments", key, list(files))
        self._issue(key)
        if not self.attachments_enabled:
            raise AuthError("Jira refused this request (HTTP 403): attachments are disabled.", details={"status": 403})
        return [self.add_attachment(key, item.name, item.data, item.mime_type) for item in files]
