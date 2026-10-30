"""The body of a `multipart/form-data` request (RFC 7578), built as a pure function so that a test can read it back.

Jira takes an attachment as one part per file, all named `file`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

_BOUNDARY = re.compile(r"^[0-9A-Za-z'()+_,./:=?-]{1,70}$")  # what RFC 2046 allows in a boundary, without the blank
_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")
FALLBACK_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class FilePart:
    filename: str
    data: bytes
    content_type: str = FALLBACK_TYPE
    field: str = "file"


def _quoted(text: str) -> str:
    """A value inside the double quotes of a header, as a browser writes it (WHATWG): `"` and line breaks are percent-encoded, every
    other character, a non-ASCII one too, stays as UTF-8, which is what Jira reads."""
    return text.replace("\r", "%0D").replace("\n", "%0A").replace('"', "%22")


def wire_filename(name: str) -> str:
    """The file name as it is sent: the text between the double quotes of `filename="..."`. What Jira shows is this, not the name on disk."""
    return _quoted(name)


def is_sendable_name(name: str) -> bool:
    """Whether a file name is text that can be sent. A name with a lone surrogate (what bytes that are no text become in a Python string)
    is not: it has no UTF-8 form."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _boundary_for(parts: Sequence[FilePart]) -> str:
    """A boundary that stands in none of the parts: made from their content, and made again while it is found in one of them."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.filename.encode("utf-8", "replace"))
        digest.update(part.data)
    seed = digest.hexdigest()
    while True:
        boundary = f"jira-dc-cli-{seed[:32]}"
        needle = boundary.encode("ascii")
        if not any(needle in part.data or needle in part.filename.encode("utf-8", "replace") for part in parts):
            return boundary
        seed = hashlib.sha256(seed.encode("ascii")).hexdigest()


def encode_multipart(parts: Sequence[FilePart], boundary: str | None = None) -> tuple[bytes, str]:
    """`(body, value of the Content-Type header)` for the files. The boundary is made from the content unless one is given, and a
    boundary that stands in a part is refused."""
    if not parts:
        raise ValueError("a multipart body needs at least one part")
    if boundary is None:
        boundary = _boundary_for(parts)
    elif not _BOUNDARY.match(boundary):
        raise ValueError("not a valid multipart boundary")
    marker = boundary.encode("ascii")
    pieces: list[bytes] = []
    for part in parts:
        if marker in part.data:
            raise ValueError("the boundary stands in the content of a part")
        kind = part.content_type if _TYPE.match(part.content_type or "") else FALLBACK_TYPE  # a header value must hold no line break
        disposition = f'Content-Disposition: form-data; name="{_quoted(part.field)}"; filename="{_quoted(part.filename)}"'
        head = f"{disposition}\r\nContent-Type: {kind}\r\n\r\n"
        pieces += [b"--" + marker + b"\r\n", head.encode("utf-8", "replace"), part.data, b"\r\n"]  # a lone surrogate becomes `?`: it has no UTF-8 form
    pieces.append(b"--" + marker + b"--\r\n")
    return b"".join(pieces), f"multipart/form-data; boundary={boundary}"
