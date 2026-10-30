"""The attachments calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Sequence

from ...domain.attachments import AttachmentLimits, UploadFile, printable, too_large
from ...domain.errors import AuthError, ConfigError, JiraCliError, ValidationError
from ...domain.model import Attachment
from ...domain.multipart import FilePart, encode_multipart
from . import map_attachments as mapping
from .http_transport import HttpResponse, RedirectRefused, ResponseTooLarge

_ID = re.compile(r"[0-9A-Za-z_-]{1,40}")  # checked with `fullmatch`: `$` would let a trailing line feed through
_HOPS = 3  # redirects that a download follows, and only within the scheme, host and port of JIRA_BASE_URL
_PAGES = ("text/html", "application/xhtml+xml")
_DOWNLOAD_HINTS = {
    403: "Downloading needs the permission to browse the project of the issue, and a token that may read it. Ask a Jira administrator.",
    404: "The attachment may have been deleted, or you may not see it. `jira attachments KEY` lists the ones that exist now.",
}
_UPLOAD_HINTS = {
    403: "Attaching needs the 'Create Attachments' permission in the project, and attachments must be switched on in Jira: "
    "`jira attach KEY FILE --dry-run` checks the switch and the limit.",
    404: "The issue does not exist or you cannot see it, or a file is larger than the upload limit of Jira (it answers 404 for that): "
    "`jira attach KEY FILE --dry-run` shows the limit.",
    413: "A proxy in front of Jira may limit the size of an upload. `jira attach KEY FILE --dry-run` shows the limit of Jira itself.",
}


def _refused(error: RedirectRefused) -> ConfigError:
    """The error for a redirect that the transport did not follow. The address is what Jira (or a proxy) sent: it is shown as text only."""
    if error.reason == "elsewhere":
        return ConfigError(
            f"Jira redirected the download to another address ({printable(error.target)}). "
            "The token is sent only to JIRA_BASE_URL: nothing was sent there.",
            hint="Use the address that Jira serves its files from as JIRA_BASE_URL (a proxy may show another one), "
            "or download the file in the web interface.",
        )
    if error.reason == "too many":
        return ConfigError(
            f"Jira redirected the download more than {_HOPS} times.",
            hint="Check JIRA_BASE_URL and any proxy in front of Jira: the address may lead in a circle.",
        )
    return ConfigError(
        "Jira redirected the download to an address that cannot be read.", hint="Check JIRA_BASE_URL and any proxy in front of Jira."
    )


class RestAttachments:
    """Implements `AttachmentsPort` (application/ports_attachments.py) over Jira Data Center's REST API."""

    def list_attachments(self, key: str) -> list[Attachment]:
        return mapping.attachments_of_issue(self._call("GET", f"/issue/{urllib.parse.quote(key, safe='')}?fields=attachment"))

    def attachment_limits(self) -> AttachmentLimits:
        return mapping.limits_from(self._call("GET", "/attachment/meta"))

    def download_attachment(self, attachment: Attachment, max_bytes: int) -> bytes:
        """The bytes of an attachment. The address is built here from the id and the file name below the configured base address, never
        taken from the `content` field of the metadata: that field comes from Jira, or from a proxy in front of it, and may name another
        host, which would then get the token."""
        if not _ID.fullmatch(attachment.id):
            raise ValidationError("Jira gave an attachment id of a form that is not used to build a request.")
        path = f"/secure/attachment/{attachment.id}/{urllib.parse.quote(attachment.filename, safe='', errors='replace')}"
        try:
            response = self._transport.request(
                "GET", path, headers={"Accept": "*/*", "Accept-Encoding": "identity"}, max_bytes=max_bytes, redirects=_HOPS
            )
        except ResponseTooLarge as error:
            if self._is_page(error.content_type, attachment):  # the limit is smaller than the login page that came instead of the file
                raise self._page_error(error.status, error.content_type, attachment, path) from error
            raise too_large(attachment.filename, max_bytes, error.announced) from error
        except RedirectRefused as error:
            raise _refused(error) from error
        if not 200 <= response.status < 300:
            self._answer(response, "GET", path, _DOWNLOAD_HINTS)
        if self._is_page(response.headers.get("content-type", ""), attachment):
            raise self._page_error(response.status, response.headers.get("content-type", ""), attachment, path)
        return response.body

    def upload_attachments(self, key: str, files: Sequence[UploadFile]) -> list[Attachment]:
        body, content_type = encode_multipart([FilePart(item.name, item.data, item.mime_type) for item in files])
        path = f"/issue/{urllib.parse.quote(key, safe='')}/attachments"
        response = self._transport.request(
            "POST", self._path(path), body=body, headers={"Content-Type": content_type, "X-Atlassian-Token": "no-check"}
        )
        return mapping.attachments_of_upload(self._answer(response, "POST", path, _UPLOAD_HINTS))

    def _answer(self, response: HttpResponse, method: str, path: str, hints: dict[int, str]) -> Any:
        """The decoded answer, or the error for its status with the hint that fits this call."""
        decoded = self._decode(response)
        try:
            self._raise_for_status(response.status, decoded, method, path)
        except JiraCliError as error:
            hint = hints.get(response.status)
            if hint:
                raise type(error)(error.message, hint=hint, details=error.details) from error
            raise
        return decoded

    @staticmethod
    def _is_page(content_type: str, attachment: Attachment) -> bool:
        """Whether an answer of this content type is a web page where a file was asked for, and the attachment is no web page itself."""
        kind = content_type.split(";")[0].strip().lower()
        meant = "html" in attachment.mime_type.lower() or attachment.filename.lower().endswith((".html", ".htm", ".xhtml"))
        return kind in _PAGES and not meant

    @staticmethod
    def _page_error(status: int, content_type: str, attachment: Attachment, path: str) -> AuthError:
        return AuthError(
            f"Jira sent a web page instead of {printable(attachment.filename)} (HTTP {status}, {printable(content_type)}).",
            hint="It is most likely the login page: the token was not accepted for downloads, or a proxy stepped in. "
            "`jira ping` checks the token; the file can also be downloaded in the web interface.",
            details={"status": status, "request": f"GET {path}"},
        )
