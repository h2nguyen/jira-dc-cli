"""The attachments routes of the mock Jira: the metadata calls, the upload, and the address that the `content` of an attachment points to.

Attachments are kept where Jira keeps them, in `fields["attachment"]` of the issue, in Jira's shape (`id`, `filename`, `mimeType`, `size`,
`created`, `author`, `content`); their bytes are in `AttachmentsRoutes.data`. No issue has one until a test adds it:

    routes = attachments_routes(jira)
    add_attachment(jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
    routes.mode = "redirect"        # how the download answers: see MODES

The download address is no REST call: it is the one call outside the Jira Data Center 11.3 reference that the mock accepts.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

UPLOADER = {"name": "jdoe", "key": "jdoe", "displayName": "Jane Doe", "emailAddress": "jane.doe@example.com", "self": "x"}
CREATED = "2026-10-05T09:30:00.000+0200"
MODES = (
    "normal",  # 200 with the bytes
    "redirect",  # 302 to the same address on this host (absolute), which then answers
    "redirect-relative",  # the same with a relative Location
    "redirect-away",  # 302 to another host: `routes.elsewhere` (a base URL set by the test)
    "redirect-loop",  # 302 for ever
    "chunked",  # 200 without a Content-Length
    "endless",  # 200 without a Content-Length, and a body that does not end until the client hangs up
    "big-length",  # a Content-Length of 1 GiB, with only the real bytes behind it
    "short-body",  # a Content-Length of a thousand bytes more than the body, which is cut off there
    "html",  # 200 with a web page, as a login page would be
)
_ISSUE = re.compile(r"/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)/attachments")


def parse_multipart(content_type: str, raw: bytes) -> list[dict[str, Any]]:
    """The parts of a multipart/form-data body, read by hand (the `cgi` module is gone): `name`, `filename`, `type` and `data` of each."""
    found = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type)
    if not found:
        raise ValueError("no boundary in " + content_type)
    delimiter = b"\r\n--" + (found.group(1) or found.group(2)).encode("ascii")
    chunks = (b"\r\n" + raw).split(delimiter)
    parts = []
    for chunk in chunks[1:]:
        if chunk.startswith(b"--"):  # the closing delimiter
            break
        head, separator, data = chunk.partition(b"\r\n\r\n")
        if not separator or not head.startswith(b"\r\n"):
            raise ValueError("a part without headers")
        headers = {}
        for line in head.decode("utf-8").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        name = re.search(r'\bname="([^"]*)"', disposition)
        filename = re.search(r'filename="([^"]*)"', disposition)
        parts.append(
            {
                "name": name.group(1) if name else "",
                "filename": filename.group(1) if filename else None,
                "type": headers.get("content-type", ""),
                "data": data,
            }
        )
    return parts


class AttachmentsRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.data: dict[str, bytes] = {}  # the bytes by attachment id
        self.enabled = True  # the switch of Jira: when off, an upload is refused (403) and nothing can be fetched (404)
        self.upload_limit = 10 * 1024 * 1024
        self.limit_status = 404  # what an upload over the limit is answered with: the reference says 404, a proxy may say 413
        self.mode = "normal"
        self.elsewhere = ""  # the base URL of another host, for the mode `redirect-away`
        self.next_id = 10000

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area."""
        if method == "GET" and path.startswith("/secure/attachment/"):
            self._download(handler, path[len("/secure/attachment/") :], query)
            return True
        if method == "GET" and path == "/rest/api/2/attachment/meta":
            handler.reply(200, {"enabled": self.enabled, "uploadLimit": self.upload_limit})
            return True
        found = re.fullmatch(r"/rest/api/2/attachment/([^/]+)", path)
        if method == "GET" and found:
            self._metadata(handler, found.group(1))
            return True
        found = _ISSUE.fullmatch(path)
        if method == "POST" and found:
            self._upload(handler, found.group(1))
            return True
        return False

    # -- state --------------------------------------------------------------------------------

    def find(self, attachment_id: str) -> dict[str, Any] | None:
        for issue in self.jira.issues.values():
            for item in issue["fields"].get("attachment") or []:
                if item["id"] == attachment_id:
                    return item
        return None

    def add(self, key: str, filename: str, data: bytes, mime_type: str, size: int | None = None) -> dict[str, Any]:
        """Store an attachment on an issue as Jira reports it. `size` is what the metadata says when it should not be the length of `data`."""
        self.next_id += 1
        identifier = str(self.next_id)
        item = {
            "self": f"{self.jira.base_url}/rest/api/2/attachment/{identifier}",
            "id": identifier,
            "filename": filename,
            "author": dict(UPLOADER),
            "created": CREATED,
            "size": len(data) if size is None else size,
            "mimeType": mime_type,
            "content": f"{self.jira.base_url}/secure/attachment/{identifier}/{quote(filename, safe='')}",
        }
        self.data[identifier] = data
        self.jira.issues[key]["fields"].setdefault("attachment", []).append(item)
        return item

    # -- the routes ---------------------------------------------------------------------------

    def _metadata(self, handler: "_Handler", identifier: str) -> None:
        item = self.find(identifier) if self.enabled else None
        if item is None:
            reason = f"The attachment with id '{identifier}' does not exist" if self.enabled else "Attachments are disabled"
            handler.reply(404, {"errorMessages": [reason], "errors": {}})
            return
        handler.reply(200, item)

    def _upload(self, handler: "_Handler", key: str) -> None:
        if handler.headers.get("X-Atlassian-Token", "").lower() != "no-check":
            handler.reply(403, {"errorMessages": ["XSRF check failed"], "errors": {}})
            return
        if key not in self.jira.issues:
            handler.reply(404, {"errorMessages": [f"Issue Does Not Exist {key}"], "errors": {}})
            return
        if not self.enabled:
            handler.reply(403, {"errorMessages": ["Attachments are disabled"], "errors": {}})
            return
        try:
            parts = [part for part in parse_multipart(handler.headers.get("Content-Type", ""), handler.raw_body) if part["name"] == "file"]
        except ValueError as error:
            handler.reply(400, {"errorMessages": [f"The request is no multipart upload: {error}"], "errors": {}})
            return
        if not parts or any(part["filename"] is None for part in parts):
            handler.reply(400, {"errorMessages": ["No file was sent: a multipart part named `file` is required"], "errors": {}})
            return
        if any(len(part["data"]) > self.upload_limit for part in parts):
            handler.reply(self.limit_status, {"errorMessages": ["The attachment exceeds the maximum attachment size"], "errors": {}})
            return
        added = [self.add(key, part["filename"], part["data"], part["type"] or "application/octet-stream") for part in parts]
        handler.reply(200, added)

    def _download(self, handler: "_Handler", rest: str, query: dict[str, list[str]]) -> None:
        identifier, _, filename = rest.partition("/")
        item = self.find(identifier) if self.enabled else None
        if item is None or identifier not in self.data or item["filename"] != filename:
            handler.reply(404, {"errorMessages": ["The attachment could not be found"], "errors": {}})
            return
        data, kind = self.data[identifier], item["mimeType"]
        address = f"/secure/attachment/{identifier}/{quote(filename, safe='')}"
        hop = int((query.get("hop") or ["0"])[0])
        if self.mode == "redirect" and not hop:
            handler.reply(302, None, {"Location": f"{self.jira.base_url}{address}?hop=1"})
        elif self.mode == "redirect-relative" and not hop:
            handler.reply(302, None, {"Location": f"{self.jira.context_path}{address}?hop=1"})
        elif self.mode == "redirect-away":
            handler.reply(302, None, {"Location": f"{self.elsewhere}{address}"})
        elif self.mode == "redirect-loop":
            handler.reply(302, None, {"Location": f"{self.jira.base_url}{address}?hop={hop + 1}"})
        elif self.mode == "html":
            handler.reply_bytes(200, b"<html><body>Log in to Jira</body></html>", "text/html;charset=UTF-8")
        elif self.mode in ("chunked", "endless"):
            self._chunked(handler, data, kind, endless=self.mode == "endless")
        elif self.mode == "big-length":
            self._lying(handler, data, kind, 1024**3)
        elif self.mode == "short-body":
            self._lying(handler, data, kind, len(data) + 1000)
        else:
            fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "_")  # a header is Latin-1: the real name goes in the other form
            disposition = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
            handler.reply_bytes(200, data, kind, {"Content-Disposition": disposition})

    @staticmethod
    def _lying(handler: "_Handler", data: bytes, kind: str, announced: int) -> None:
        """A Content-Length that is not the length of the body: the real bytes follow, and then the connection ends."""
        handler.send_response(200)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(announced))
        handler.end_headers()
        try:
            handler.wfile.write(data)
        except OSError:
            pass  # the client has seen the length and hung up
        handler.close_connection = True

    @staticmethod
    def _chunked(handler: "_Handler", data: bytes, kind: str, *, endless: bool) -> None:
        """A body without a Content-Length: the real bytes, then for `endless` more of them until the client has had enough."""
        handler.send_response(200)
        handler.send_header("Content-Type", kind)
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()

        def chunk(piece: bytes) -> None:
            handler.wfile.write(f"{len(piece):x}\r\n".encode("ascii") + piece + b"\r\n")

        try:
            for start in range(0, len(data), 8192):
                chunk(data[start : start + 8192])
            for _ in range(4096 if endless else 0):  # 256 MiB at most: a test that forgot its limit ends
                chunk(b"x" * 65536)
            handler.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass  # the client hung up, which is what the test wants
        handler.close_connection = True


def attachments_routes(jira: "MockJira") -> AttachmentsRoutes:
    """The attachments routes of a running mock, to set modes and limits."""
    return next(item for item in jira.extensions if isinstance(item, AttachmentsRoutes))


def add_attachment(jira: "MockJira", key: str, filename: str, data: bytes = b"", mime_type: str = "application/octet-stream", *, size: int | None = None) -> dict[str, Any]:
    """Give an issue of the mock an attachment, in the shape Jira reports it; returns that shape."""
    return attachments_routes(jira).add(key, filename, data, mime_type, size)
