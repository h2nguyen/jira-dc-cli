"""Fields: the names the application uses, and finding the custom fields of a Jira instance."""
from __future__ import annotations

from typing import Iterable, Mapping

from .model import FieldInfo

# Logical names. The adapter maps them to the custom field ids of the instance; callers never see ids.
STORY_POINTS = "story_points"
EPIC_LINK = "epic_link"
SPRINT = "sprint"
ACCEPTANCE = "acceptance"
LOGICAL_FIELDS = (STORY_POINTS, EPIC_LINK, SPRINT, ACCEPTANCE)

FIELD_LABELS = {
    STORY_POINTS: "Story Points",
    EPIC_LINK: "Epic Link",
    SPRINT: "Sprint",
    ACCEPTANCE: "Acceptance criteria (DoD)",
}

# Names the dump and the JSON output use for the logical fields.
EXPORT_KEYS = {STORY_POINTS: "storyPoints", EPIC_LINK: "epicLink", SPRINT: "sprint", ACCEPTANCE: "acceptance"}

LIST_FIELDS = ("summary", "status", "issuetype", "assignee", "priority", "created", "updated")
REPORT_FIELDS = ("summary", "status", "issuetype", STORY_POINTS, EPIC_LINK)
BACKLOG_FIELDS = (
    "summary",
    "issuetype",
    "status",
    "priority",
    "labels",
    "components",
    "fixVersions",
    "description",
    EPIC_LINK,
    STORY_POINTS,
    ACCEPTANCE,
    SPRINT,
)


def resolve_field_ids(fields: Iterable[FieldInfo], overrides: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """The id of every logical field. Settings win; then the exact label; then, for the
    acceptance criteria, the first field whose name starts with "acceptance"."""
    catalog = list(fields)
    by_name = {info.name.casefold(): info.id for info in catalog}
    result: dict[str, str | None] = {}
    for logical in LOGICAL_FIELDS:
        override = (overrides or {}).get(logical, "")
        found = override or by_name.get(FIELD_LABELS[logical].casefold())
        if not found and logical == ACCEPTANCE:
            found = next((info.id for info in catalog if info.name.casefold().startswith("acceptance")), None)
        result[logical] = found or None
    return result
