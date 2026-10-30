"""Safe file names for what comes from outside, such as the name of an attachment.

A name from Jira is text that somebody typed or a program made. Written to a disk as it is, it could leave the folder (`..\\..\\x`),
name a device (`CON`), open a hidden stream (`a.txt:b`) or fail on another system (a trailing dot, 300 characters).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Sequence

MAX_CHARS = 150  # a name is kept within this, extension included: a folder path has to fit with it
MAX_BYTES = 240  # the file systems count bytes (255): 150 characters of Chinese are 450 bytes in UTF-8
_MAX_EXTENSION = 16  # a longer tail after the last dot is no extension

_SEPARATORS = re.compile(r"[\\/]")
_DRIVE = re.compile(r"^[A-Za-z]:")
_REFUSED = frozenset('<>:"|?*')  # not allowed in a Windows file name; ":" also opens an alternate data stream (`a.txt:b`)
_DEVICES = frozenset(
    ["con", "prn", "aux", "nul", "conin$", "conout$"]
    + [f"{device}{digit}" for device in ("com", "lpt") for digit in "123456789\u00b9\u00b2\u00b3"]
)


def _clean(char: str) -> str:
    """A character as it may stand in a file name: invisible ones are dropped, control and refused ones become `_`."""
    if char in _REFUSED:
        return "_"
    category = unicodedata.category(char)
    if category in ("Cf", "Cs"):  # format characters (bidi overrides, zero width) and lone surrogates, which no file system takes
        return ""
    if category in ("Cc", "Zl", "Zp"):
        return "_"
    return char


def _split_extension(name: str) -> tuple[str, str]:
    dot = name.rfind(".")
    if dot <= 0 or len(name) - dot > _MAX_EXTENSION + 1:  # no dot, a hidden file (`.bashrc`) or a tail that is no extension
        return name, ""
    return name[:dot], name[dot:]


def _fit(stem: str, extension: str, spare: int = 0) -> str:
    """The stem, shortened so that it, the extension and `spare` more characters stay within the limits."""
    stem = stem[: max(MAX_CHARS - len(extension) - spare, 1)]
    while len(stem) > 1 and len((stem + extension).encode("utf-8")) + spare > MAX_BYTES:
        stem = stem[:-1]
    return stem


def _tag(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "", text)


def safe_filename(name: str, identifier: str = "") -> str:
    """A name that can be created in any folder on Windows, macOS and Linux.

    It has no folder and no drive (what stands after the last `/` or `\\` is the name), no control character, no character that
    Windows refuses, no trailing dot or blank, a prefix `_` when it would name a device (`CON`, `NUL.txt`, `COM1`, `LPT3` ...), and
    at most `MAX_CHARS` characters with its extension kept. A name that is nothing after that becomes `attachment-<identifier>`."""
    last = _DRIVE.sub("", _SEPARATORS.split(name or "")[-1])
    cleaned = "".join(_clean(char) for char in last).strip().rstrip(". ")
    if cleaned.split(".", 1)[0].rstrip(" ").casefold() in _DEVICES:
        cleaned = "_" + cleaned
    stem, extension = _split_extension(cleaned)
    if not stem.strip(". "):
        stem = f"attachment-{_tag(identifier)}" if _tag(identifier) else "attachment"
    return _fit(stem, extension).rstrip(". ") + extension


def _with_tag(name: str, tag: str) -> str:
    """The name with `-tag` before its extension, the stem shortened where the limits ask for it."""
    stem, extension = _split_extension(name)
    return _fit(stem, extension, len(tag) + 1).rstrip(". ") + "-" + tag + extension


def unique_filenames(entries: Sequence[tuple[str, str]]) -> dict[str, str]:
    """A safe name for each `(identifier, name)`, and no two of them equal, case aside (Windows and macOS see `A.txt` and `a.txt` as
    one file). Every name that would clash gets `-<identifier>` before its extension, so the result does not depend on the order."""
    safe = {identifier: safe_filename(name, identifier) for identifier, name in entries}
    clashes = Counter(name.casefold() for name in safe.values())
    result: dict[str, str] = {}
    taken: set[str] = set()
    for identifier, name in safe.items():
        if clashes[name.casefold()] > 1:
            name = _with_tag(name, _tag(identifier) or "file")
        candidate, count = name, 1
        while candidate.casefold() in taken:  # a name that was there already, `a-10002.txt` among the files
            count += 1
            candidate = _with_tag(name, str(count))
        taken.add(candidate.casefold())
        result[identifier] = candidate
    return result
