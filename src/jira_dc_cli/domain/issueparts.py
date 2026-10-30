"""The parts of an issue that a reader can ask for (`jira issue --only`), and an issue as read with Jira's expansions."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import UsageError
from .history import Changelog
from .model import Issue

PARTS = (
    "summary",
    "status",
    "priority",
    "labels",
    "components",
    "fixVersions",
    "epic",
    "points",
    "sprints",
    "dates",
    "description",
    "acceptance",
    "comments",
    "links",
    "subtasks",
    "attachments",
)
_BY_SPELLING = {name.casefold(): name for name in PARTS}


@dataclass(frozen=True)
class ExpandedIssue:
    """An issue with what `expand=renderedFields` and `expand=changelog` add to it. A text that was not asked for is empty, a changelog
    that was not asked for is None."""

    issue: Issue
    rendered_description: str = ""  # HTML, as Jira renders the description
    rendered_acceptance: str = ""  # HTML, as Jira renders the acceptance criteria
    changelog: Changelog | None = None


def parse_parts(text: str) -> tuple[str, ...]:
    """The parts named in `--only a,b,c`, in the order of `PARTS`. The case and `-` or `_` inside a name do not matter
    (`fix-versions`). An unknown name is a usage error that lists the valid ones; no name at all is one as well."""
    names = [name.strip() for name in (text or "").split(",") if name.strip()]
    if not names:
        raise UsageError("--only needs at least one name.", hint="Valid names: " + ", ".join(PARTS) + ".")
    chosen: set[str] = set()
    unknown: list[str] = []
    for name in names:
        part = _BY_SPELLING.get(name.replace("-", "").replace("_", "").casefold())
        if part:
            chosen.add(part)
        else:
            unknown.append(name)
    if unknown:
        raise UsageError(f"Unknown name in --only: {', '.join(unknown)}.", hint="Valid names: " + ", ".join(PARTS) + ".")
    return tuple(part for part in PARTS if part in chosen)


def split_keys(text: str) -> list[str]:
    """The issue keys in what was typed: one word, several words, or a list separated by commas (`PROJ-1,PROJ-2`)."""
    return (text or "").replace(",", " ").split()
