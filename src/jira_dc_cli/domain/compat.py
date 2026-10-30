"""Which Jira Data Center this CLI is written for, and what to say about the one it meets.

The CLI is written and tested against the Jira Data Center 11.3 REST API (`/rest/api/2` and `/rest/agile/1.0`). The tests check
every call it sends against the 11.3 reference (see `tests/support/conformance.py`). Another version is not refused: REST API 2
stays the same within a major release and changes little between releases; `assess` says how far from 11.3 the server is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_MAJOR, TARGET_MINOR = 11, 3
TARGET = f"{TARGET_MAJOR}.{TARGET_MINOR}.x"  # how the documentation names it
REFERENCE_URL = "https://developer.atlassian.com/server/jira/platform/rest/v11003/intro/"

OK, WARN = "ok", "warn"

# At most 9 ASCII digits a part: the server's answer must never reach int() as a string of thousands of digits.
_NUMBER = r"([0-9]{1,9})(?![0-9])"
_VERSION = re.compile(rf"^\s*{_NUMBER}(?:\.{_NUMBER})?(?:\.{_NUMBER})?")


@dataclass(frozen=True)
class Compatibility:
    status: str  # ok | warn
    detail: str
    hint: str = ""


def parse_version(text: str) -> tuple[int, int, int] | None:
    """`11.3.12` as (11, 3, 12); `9.12` as (9, 12, 0); None when the text has no leading number (or one that no release has)."""
    match = _VERSION.match(text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0)


def shown(text: object) -> str:
    """What a server called itself (its version, its kind of deployment), as it may be printed: one line of at most 64 characters."""
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", str(text or "")).strip()
    return cleaned if len(cleaned) <= 64 else cleaned[:63] + "…"


def assess(version: str, deployment: str = "") -> Compatibility:
    """Whether the server runs the release line that the CLI is written for, with what to expect when it does not."""
    version = shown(version)
    shown_version = version or "unknown version"
    if shown(deployment).casefold() == "cloud":
        return Compatibility(
            WARN,
            f"Jira Cloud {shown_version}: this CLI is written for Jira Data Center {TARGET}",
            "Jira Cloud has other authentication and other endpoints; use a Data Center instance.",
        )
    parsed = parse_version(version)
    if parsed is None:
        return Compatibility(WARN, f"Jira {shown_version}: the version is not a number, so compatibility with {TARGET} is unknown")
    major, minor, _ = parsed
    if (major, minor) == (TARGET_MAJOR, TARGET_MINOR):
        return Compatibility(OK, f"Jira {version} is on the {TARGET} release line this CLI is written and tested for")
    if major == TARGET_MAJOR:
        side = "newer" if minor > TARGET_MINOR else "older"
        return Compatibility(
            OK,
            f"Jira {version} is {side} than {TARGET} in the same major release: the REST API 2 is stable within it, only the newest features may differ",
        )
    if major > TARGET_MAJOR:
        return Compatibility(
            WARN,
            f"Jira {version} is newer than {TARGET}, which this CLI was tested with",
            "REST API 2 usually stays compatible; report what does not work.",
        )
    if major >= 9:
        return Compatibility(
            WARN,
            f"Jira {version} is older than {TARGET}, which this CLI was tested with: most commands are expected to work, some newer endpoints or fields may be missing",
            f"The CLI is written for {TARGET}; a command that fails with 404 may need a newer Jira.",
        )
    return Compatibility(
        WARN,
        f"Jira {version} is much older than {TARGET}: the metadata endpoints that `create` uses exist only from 9.0",
        f"Use Jira {TARGET_MAJOR - 2}.0 or newer, ideally {TARGET}.",
    )
