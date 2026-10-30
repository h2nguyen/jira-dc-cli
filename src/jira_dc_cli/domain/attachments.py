"""Rules for attachments: which ones a command means, which can be read as text, how a long text is cut, what an upload looks like.

All pure: the bytes, the file system and Jira are the business of the ports.
"""
from __future__ import annotations

import codecs
import mimetypes
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .errors import NotFoundError, UsageError, ValidationError
from .model import Attachment
from .multipart import wire_filename
from .payloads import comment_payload

DEFAULT_MAX_BYTES = 26_214_400  # 25 MiB: what `download` accepts of one file unless told otherwise
DEFAULT_MAX_CHARS = 20_000  # what `download --text` shows of one file unless told otherwise
GENERIC_TYPE = "application/octet-stream"
KIND_TEXT, KIND_OTHER = "text", "other"  # the kinds of Office Open XML files are `docx`, `xlsx` and `pptx`

_LISTED = 12  # attachments named in a hint before "and N more"
_PATH_WORDS = 8  # words that one path with blanks may be made of, when a command line arrives with its words joined
_MEMO_LIMIT = 100_000  # characters that `printable` remembers the answer for
_ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
_JOINABLE = r"[^\x00-\x7f\s\u200c\u200d]"  # a character that a zero width joiner can be part of the text next to: not ASCII, no blank, no joiner
_STRAY_JOINER = re.compile(rf"[\u200c\u200d](?:(?!{_JOINABLE})|(?<!{_JOINABLE}[\u200c\u200d]))")  # a joiner that lacks one on a side


class _Screen(dict[int, "int | str | None"]):
    """The table of `str.translate` for `printable`, filled as the characters come: a control character becomes U+FFFD (tab and line feed
    stay), a lone surrogate too (it cannot be written to a terminal), and a format character is dropped (category Cf: zero width
    characters, the bidirectional controls, the byte order mark, the tag characters U+E0000 to U+E007F that spell text nobody sees).
    The table lives as long as the process, so it stops growing at `_MEMO_LIMIT` entries: a text that uses every code point of Unicode
    must not make it large, it only has the rest looked up one by one."""

    def __init__(self) -> None:
        super().__init__({code: "\ufffd" for code in [*range(32), *range(127, 160)] if code not in (9, 10)})
        self[0x200C] = 0x200C  # the joiners that belong to the text are chosen by `_STRAY_JOINER` before the table is applied
        self[0x200D] = 0x200D

    def __missing__(self, code: int) -> int | str | None:
        category = unicodedata.category(chr(code))
        value = None if category == "Cf" else "\ufffd" if category == "Cs" else code
        if len(self) < _MEMO_LIMIT:
            self[code] = value
        return value


_FOR_THE_SCREEN = _Screen()
_TEXT_TYPES = frozenset(
    {
        "application/json", "application/xml", "application/yaml", "application/x-yaml", "application/toml", "application/javascript",
        "application/x-javascript", "application/ecmascript", "application/sql", "application/x-sql", "application/x-sh",
        "application/x-shellscript", "application/x-ndjson", "application/ndjson", "application/csv", "application/x-csv",
        "application/graphql", "application/x-httpd-php", "application/x-powershell",
    }
)
_TEXT_SUFFIXES = ("+json", "+xml", "+yaml")
_BINARY_FAMILIES = ("image/", "audio/", "video/", "font/")
_TEXT_EXTENSIONS = frozenset(
    (
        "txt log md markdown rst adoc csv tsv json jsonl ndjson har xml xsd xsl xslt wsdl xaml html htm xhtml svg yaml yml toml ini "
        "cfg conf config properties sql sh bash zsh ps1 psm1 bat cmd py pyi js mjs cjs ts tsx jsx java kt kts scala groovy gradle cs vb "
        "fs c h cc cpp cxx hpp hh go rs rb php pl pm lua swift r jl dart css scss sass less vue svelte tf tfvars hcl diff patch tex bib "
        "srt vtt ics graphql gql proto csproj props targets sln rss atom"
    ).split()
)
_TEXT_NAMES = frozenset({"dockerfile", "makefile", "readme", "license", "notice", "changelog"})
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_OFFICE_TYPES = {_DOCX: "docx", _XLSX: "xlsx", _PPTX: "pptx"}
_OFFICE_EXTENSIONS = {"docx": "docx", "docm": "docx", "xlsx": "xlsx", "xlsm": "xlsx", "pptx": "pptx", "pptm": "pptx"}

# What a person means by an extension, whatever the registry of this computer says (Windows maps `.md` and `.log` as it likes).
_KNOWN_TYPES = {
    "txt": "text/plain", "log": "text/plain", "md": "text/markdown", "markdown": "text/markdown", "csv": "text/csv",
    "tsv": "text/tab-separated-values", "json": "application/json", "har": "application/json", "xml": "application/xml",
    "html": "text/html", "htm": "text/html", "yaml": "application/yaml", "yml": "application/yaml",
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "bmp": "image/bmp", "pdf": "application/pdf", "zip": "application/zip", "gz": "application/gzip", "7z": "application/x-7z-compressed",
    "mp4": "video/mp4", "docx": _DOCX, "xlsx": _XLSX, "pptx": _PPTX,
}
_ENCODED_TYPES = {
    "gzip": "application/gzip", "bzip2": "application/x-bzip2", "xz": "application/x-xz", "compress": "application/x-compress",
}


@dataclass(frozen=True)
class AttachmentLimits:
    """What Jira says about attachments (`GET /attachment/meta`): whether they are switched on, and the largest upload in bytes
    (0: it did not say)."""

    enabled: bool = True
    upload_limit: int = 0


@dataclass(frozen=True)
class UploadFile:
    """A file to attach. A dry run knows the size only; a real run carries the bytes too."""

    name: str
    mime_type: str
    size: int
    data: bytes = field(default=b"", repr=False)


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    return f"{size} B"  # pragma: no cover


def printable(text: str) -> str:
    """Text as it may reach a terminal. What comes from outside (the content of an attachment, a file name) can hold escape sequences
    that move the cursor, clear the screen or rewrite what was printed before. They are removed. Other control characters become U+FFFD.
    Format characters (category Cf) are dropped: the controls that turn text around on the screen (a name that reads `exe.png` for
    `png.exe`), zero width characters, the byte order mark and the tag characters that spell text that nobody sees. The two joiners
    (zero width joiner and non-joiner) are the exception where they belong to the text: one that stands between two characters that are
    neither ASCII nor blanks nor joiners themselves, as in an emoji sequence (`👨‍👩‍👧`) or a Persian word (`می‌خواهم`), where dropping it
    changes the text. A joiner anywhere else, and every one of a run of them, is dropped like the rest. Tab and line feed stay."""
    text = _ESCAPES.sub("", text)
    if "\u200c" in text or "\u200d" in text:
        text = _STRAY_JOINER.sub("", text)
    return text.translate(_FOR_THE_SCREEN)


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def guess_mime_type(filename: str) -> str:
    """The MIME type for a file name: what a person means by its extension, else what the system knows, else a generic type."""
    known = _KNOWN_TYPES.get(_extension(filename))
    if known:
        return known
    kind, encoding = mimetypes.guess_type(filename, strict=False)
    return _ENCODED_TYPES.get(encoding or "") or kind or GENERIC_TYPE


def describe(attachments: Sequence[Attachment]) -> str:
    """`10001 shot.png (12.3 KB); 10002 notes.txt (1 B)`, for a hint."""
    shown = [printable(f"{item.id} {item.filename} ({human_size(item.size)})") for item in attachments[:_LISTED]]
    more = f"; and {len(attachments) - _LISTED} more" if len(attachments) > _LISTED else ""
    return "; ".join(shown) + more


def by_name(attachments: Sequence[Attachment], name: str) -> list[Attachment]:
    """The attachments with this file name: exact, case aside."""
    wanted = name.strip().casefold()
    return [item for item in attachments if item.filename.casefold() == wanted]


def select_attachments(
    key: str, attachments: Sequence[Attachment], *, identifier: str = "", names: Sequence[str] = (), everything: bool = False
) -> list[Attachment]:
    """The attachments that an ID, file names or `--all` choose, each once, in the order of the issue. A choice that picks nothing, or
    that cannot be told from another, is an error that says what the issue has."""
    identifier = identifier.strip()
    names = [name for name in names if name.strip()]
    have = f"{key} has: {describe(attachments)}." if attachments else f"{key} has no attachments."
    if everything:
        if identifier or names:
            raise UsageError("--all cannot be combined with an ID or --name.", hint="Give --all alone, or choose with an ID and --name.")
        return list(attachments)
    if not identifier and not names:
        raise UsageError(f"Say which attachment of {key} you mean.", hint=f"Give an ID or --name FILE, or --all. {have}")
    chosen: set[str] = set()
    if identifier:
        found = [item for item in attachments if item.id == identifier]
        if not found:
            same_name = f" '{identifier}' is a file name: use --name '{identifier}'." if by_name(attachments, identifier) else ""
            raise NotFoundError(f"{key} has no attachment with the ID {identifier}.", hint=f"{have}{same_name}")
        chosen.add(found[0].id)
    for name in names:
        found = by_name(attachments, name)
        if not found:
            raise NotFoundError(f"{key} has no attachment named '{name}'.", hint=have)
        if len(found) > 1:
            ids = ", ".join(item.id for item in found)
            raise UsageError(
                f"{len(found)} attachments of {key} are named '{name}': {ids}.",
                hint=f"Choose one by its ID, for example `jira download {key} {found[0].id}`.",
            )
        chosen.add(found[0].id)
    return [item for item in attachments if item.id in chosen]


# -- the limit on what is fetched ------------------------------------------------------------------


def too_large(filename: str, limit: int, size: int | None = None) -> ValidationError:
    """The error for a file over the limit: `size` is known from the metadata, or not when the download was stopped while reading."""
    what = f"is {human_size(size)}" if size is not None else "is larger"
    stopped = "" if size is not None else " The download was stopped."
    return ValidationError(
        f"{printable(filename)} {what}, more than the limit of {human_size(limit)} ({limit} bytes).{stopped}",
        hint="Raise the limit with --max-bytes N (a number of bytes) if you need the file, or download it in the web interface.",
    )


def too_large_many(items: Sequence[tuple[str, int]], limit: int) -> ValidationError:
    """The same for several files at once: they are all named, so that one run tells everything."""
    if len(items) == 1:
        return too_large(items[0][0], limit, items[0][1])
    listed = ", ".join(f"{printable(name)} ({human_size(size)})" for name, size in items)
    return ValidationError(
        f"{len(items)} attachments are larger than the limit of {human_size(limit)} ({limit} bytes): {listed}.",
        hint="Raise the limit with --max-bytes N (a number of bytes), or choose the others by ID or --name.",
    )


# -- reading as text ----------------------------------------------------------------------------


def _is_text_type(mime: str) -> bool:
    return mime.startswith("text/") or mime in _TEXT_TYPES or mime.endswith(_TEXT_SUFFIXES)


def kind_of(mime_type: str, filename: str) -> str:
    """How the content can be read: `docx`, `xlsx` or `pptx` (Office Open XML), `text`, or `other` (not as text). The MIME type and the
    extension both count: Jira often says `application/octet-stream`, and a browser on Windows says `application/vnd.ms-excel` for a
    `.csv`. An extension of text does not make an image, a sound or a video text."""
    mime = (mime_type or "").split(";")[0].strip().lower()
    extension = _extension(filename)
    if mime in _OFFICE_TYPES:
        return _OFFICE_TYPES[mime]
    if extension in _OFFICE_EXTENSIONS:
        return _OFFICE_EXTENSIONS[extension]
    if _is_text_type(mime):
        return KIND_TEXT
    if (extension in _TEXT_EXTENSIONS or filename.strip().lower() in _TEXT_NAMES) and not mime.startswith(_BINARY_FAMILIES):
        return KIND_TEXT
    return KIND_OTHER


def looks_binary(data: bytes) -> bool:
    """Whether bytes that are said to be text are none: a zero byte in the first 8 KiB, unless a byte order mark says UTF-16 or UTF-32."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return False
    return b"\x00" in data[:8192]


def cut_text(text: str, limit: int) -> tuple[str, bool]:
    """`(text, truncated)`: the text as it is, or cut to at most `limit` characters at the end of a paragraph, else of a line, else of a
    word, taking the last of them that still leaves the reader half of the room (a cut that throws most away is no cut)."""
    if len(text) <= limit:
        return text, False
    for separator in ("\n\n", "\n", " "):
        at = text.rfind(separator, 0, limit + len(separator))  # a separator that starts at the limit ends a complete piece
        if at >= limit // 2 and at > 0:
            return text[:at].rstrip(), True
    return text[:limit], True


# -- uploading ----------------------------------------------------------------------------------


def split_paths(text: str, exists: Callable[[str], bool]) -> list[str]:
    """The file paths in the words of a command line that arrived joined by blanks (`jira attach KEY a.png "my shot.png"` is `a.png my
    shot.png` by then, and the program cannot tell a blank in a name from a blank between two names). For a caller that has only that
    text; a caller that has the arguments as they came uses `argument_paths`. See `choose_paths` for how the words become paths."""
    return choose_paths(text.split(" "), exists)


def argument_paths(arguments: Sequence[str], exists: Callable[[str], bool]) -> list[str]:
    """The file paths in the arguments of a command line as the program received them: each element of `argv` is one argument, so a quoted
    `"x y"` is one argument and `x y` without quotes are two.

    An argument that names a file stays whole, blanks and all: `"x y"` is the file `x y` even when the files `x` and `y` exist too, and `x`
    `y` are the files `x` and `y`. An argument that names no file and holds blanks is split at them: it is the text of a program that joined
    its words (the files of an MCP call come as one string). Then `choose_paths` decides on the words: one that is a file is a path, one
    that is not is joined with the ones after it (eight in all), and words that lead to nothing stay together, so that the error names
    them. Every path is asked for once, and the work is a few `exists` calls per argument, whatever the number of arguments."""
    known: dict[str, bool] = {}

    def names_a_file(path: str) -> bool:
        if path not in known:
            known[path] = _exists_quietly(exists, path)
        return known[path]

    words: list[str] = []
    for argument in arguments:
        if " " in argument and not names_a_file(argument):
            words.extend(argument.split(" "))
        else:
            words.append(argument)
    return choose_paths(words, names_a_file)


def choose_paths(arguments: Sequence[str], exists: Callable[[str], bool]) -> list[str]:
    """The file paths in the arguments of a command line, each argument being one path when it names a file.

    An argument that is a file by itself is a path: a longer join never takes its place, so `x` and `y` are the files `x` and `y` even when
    a file `x y` exists too. An argument that is none is joined with the arguments after it, up to eight in all, and the first (shortest)
    join that names an existing file is the path. Arguments that lead to nothing stay together as one path that does not exist, so that
    the error can name it. The work is a few `exists` calls per argument, whatever the number of arguments."""
    count = len(arguments)
    paths: list[str] = []
    missing: list[str] = []
    index = 0
    while index < count:
        if not arguments[index]:  # a blank that was typed twice
            index += 1
            continue
        taken = _arguments_that_exist(arguments, index, exists)
        if taken == 0:
            missing.append(arguments[index])
            index += 1
            continue
        if missing:
            paths.append(" ".join(missing))
            missing = []
        paths.append(" ".join(arguments[index : index + taken]))
        index += taken
    if missing:
        paths.append(" ".join(missing))
    return paths


def _exists_quietly(exists: Callable[[str], bool], path: str) -> bool:
    """Whether `path` exists. A name that the file system refuses to look at (too long, a zero byte in it) is no file, and no failure: the
    text of a call is whatever a program wrote."""
    try:
        return exists(path)
    except (OSError, ValueError):
        return False


def _arguments_that_exist(arguments: Sequence[str], start: int, exists: Callable[[str], bool]) -> int:
    """How many arguments from `start` name an existing path: one when the argument alone does, else the fewest more that do (up to
    `_PATH_WORDS` in all); 0 when none does."""
    if _exists_quietly(exists, arguments[start]):
        return 1
    for length in range(2, min(_PATH_WORDS, len(arguments) - start) + 1):
        if _exists_quietly(exists, " ".join(arguments[start : start + length])):
            return length
    return 0


def upload_payload(key: str, files: Sequence[UploadFile], comment: str = "") -> dict[str, Any]:
    """What an upload sends, described: the one place that a dry run and a real run take it from. The file name is the one that goes on
    the wire (`"` and line breaks percent-encoded, as a browser writes them), which is what Jira will then show."""
    payload: dict[str, Any] = {
        "request": f"POST /issue/{key}/attachments",
        "headers": {"X-Atlassian-Token": "no-check"},
        "parts": [{"field": "file", "filename": wire_filename(item.name), "contentType": item.mime_type, "size": item.size} for item in files],
    }
    if comment.strip():
        payload["comment"] = comment_payload(comment)
    return payload
