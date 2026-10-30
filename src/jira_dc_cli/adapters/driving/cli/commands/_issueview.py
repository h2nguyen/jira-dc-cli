"""How `jira issue` prints what the use case read: the text view (the whole issue, or only the parts asked for with `--only`) and the JSON."""
from __future__ import annotations

from typing import Any

from .....application.reading import IssueReading, MissingIssue, ReadResult
from .....domain.attachments import printable
from ..render import key_values, number, to_jsonable

# The keys of the JSON that belong to each part of `--only`. A key that an option adds (`outline`, `renderedDescription`) goes with its part.
PART_KEYS = {
    "summary": ("summary",),
    "status": ("status",),
    "priority": ("priority",),
    "labels": ("labels",),
    "components": ("components",),
    "fixVersions": ("fixVersions",),
    "epic": ("epicKey", "parentKey"),
    "points": ("storyPoints",),
    "sprints": ("sprints",),
    "dates": ("created", "updated"),
    "description": ("description", "outline", "section", "renderedDescription"),
    "acceptance": ("acceptance", "renderedAcceptance"),
    "comments": ("comments", "commentTotal"),
    "links": ("links",),
    "subtasks": ("subtasks",),
    "attachments": ("attachments", "attachmentTotal"),
}
OPTION_KEYS = ("history", "historyComplete", "truncated", "totalChars")  # there when their option was given
PEOPLE_KEYS = ("assignee", "reporter")
SEPARATOR = "\n\n=====\n\n"
NONE = "(none)"


def issue_json(reading: IssueReading, *, cuts_asked: bool) -> dict[str, Any]:
    """The issue as `--json` prints it: the model, and the keys that the options add (only when they were given)."""
    data = to_jsonable(reading.issue)
    if reading.outline is not None:
        data["description"] = None  # the headings are shown instead of the text
        data["outline"] = [{"index": item.index, "level": item.level, "title": item.title, "chars": item.chars} for item in reading.outline]
    if reading.section is not None:
        data["section"] = {"index": reading.section.index, "level": reading.section.level, "title": reading.section.title}
    if reading.rendered_description is not None:
        data["renderedDescription"] = reading.rendered_description
    if reading.rendered_acceptance is not None:
        data["renderedAcceptance"] = reading.rendered_acceptance
    if reading.history is not None:
        data["history"] = [{"created": item.created, "from": item.from_status, "to": item.to_status} for item in reading.history]
        data["historyComplete"] = reading.history_complete
    if cuts_asked:
        data["truncated"] = {name: cut.truncated for name, cut in reading.cuts.items()}
        data["totalChars"] = {name: cut.total_chars for name, cut in reading.cuts.items()}
    if not reading.parts:
        return data
    keep = {"key", *OPTION_KEYS, *(PEOPLE_KEYS if reading.view.users_shown else ())}
    for part in reading.parts:
        keep.update(PART_KEYS[part])
    return {name: value for name, value in data.items() if name in keep}


def result_json(result: ReadResult, *, cuts_asked: bool) -> dict[str, Any]:
    """Several keys: the issues that were found, in the order of the keys, and one entry for each key that was not."""
    return {
        "count": len(result.issues),
        "issues": [issue_json(reading, cuts_asked=cuts_asked) for reading in result.issues],
        "errors": [{"key": item.key, "code": item.code, "message": item.message} for item in result.errors],
    }


# -- text ----------------------------------------------------------------------------------------


def _trailer(reading: IssueReading, name: str, shown: str, *, outline_help: bool = False) -> list[str]:
    """One line under a text that was cut: how much of it is shown, and what to do to read more."""
    cut = reading.cuts.get(name)
    if cut is None or not cut.truncated:
        return []
    more = "--outline lists the headings, --section TITLE reads one part, --max-chars N shows more" if outline_help else "--max-chars N shows more"
    closed = "; the code block is closed at the cut" if cut.closed else ""
    return [f"(cut: {len(shown.strip())} of {cut.total_chars} characters{closed}; {more})"]


def _text_block(reading: IssueReading, title: str, name: str, text: str, *, whole: bool, outline_help: bool = False) -> list[str]:
    """A long text under its title. In the whole view an empty text is left out, in a view of parts it says that it is empty."""
    if not text.strip():
        return [] if whole else [f"{title}: {NONE}"]
    return [title + ":", text.strip(), *_trailer(reading, name, text, outline_help=outline_help)]


def _default_pairs(reading: IssueReading) -> list[tuple[str, Any]]:
    issue = reading.issue
    pairs: list[tuple[str, Any]] = []
    if reading.view.users_shown:
        pairs += [("Assignee", issue.assignee.display_name if issue.assignee else ""), ("Reporter", issue.reporter.display_name if issue.reporter else "")]
    return pairs + [
        ("Labels", ", ".join(issue.labels)),
        ("Components", ", ".join(issue.components)),
        ("Fix versions", ", ".join(issue.fix_versions)),
        ("Epic", issue.epic_key or issue.parent_key),
        ("Story points", "" if issue.story_points is None else number(issue.story_points)),
        ("Sprint", ", ".join(f"{sprint.name} ({sprint.state})" for sprint in issue.sprints)),
        ("Created", issue.created),
        ("Updated", issue.updated),
    ]


def _part_pairs(reading: IssueReading) -> list[tuple[str, Any]]:
    """The short facts that `--only` asked for, one line each. A fact that is empty says so."""
    issue = reading.issue
    rows = [
        ("summary", "Summary", issue.summary),
        ("status", "Status", issue.status.name if issue.status else ""),
        ("priority", "Priority", issue.priority),
        ("labels", "Labels", ", ".join(issue.labels)),
        ("components", "Components", ", ".join(issue.components)),
        ("fixVersions", "Fix versions", ", ".join(issue.fix_versions)),
        ("epic", "Epic", issue.epic_key or issue.parent_key),
        ("points", "Story points", "" if issue.story_points is None else number(issue.story_points)),
        ("sprints", "Sprint", ", ".join(f"{sprint.name} ({sprint.state})" for sprint in issue.sprints)),
        ("dates", "Created", issue.created),
        ("dates", "Updated", issue.updated),
    ]
    pairs: list[tuple[str, Any]] = []
    if reading.view.users_shown:
        pairs += [("Assignee", issue.assignee.display_name if issue.assignee else NONE), ("Reporter", issue.reporter.display_name if issue.reporter else NONE)]
    return pairs + [(label, value or NONE) for part, label, value in rows if reading.shows(part)]


def _list_blocks(reading: IssueReading, *, whole: bool) -> list[str]:
    """Links, subtasks and attachments. In the whole view a block that is empty is left out."""
    issue = reading.issue
    lines: list[str] = []
    if reading.shows("links") and (issue.links or not whole):
        lines += ["", "Links:" if issue.links else f"Links: {NONE}"]
        lines += [f"  {link.phrase} {link.other.key} ({link.other.status}) {link.other.summary}" for link in issue.links]
    if reading.shows("subtasks") and (issue.subtasks or not whole):
        lines += ["", "Subtasks:" if issue.subtasks else f"Subtasks: {NONE}"]
        lines += [f"  {task.key} ({task.status}) {task.summary}" for task in issue.subtasks]
    if reading.shows("attachments") and (issue.attachments or not whole):
        lines += ["", f"Attachments: {len(issue.attachments)}" if issue.attachments else f"Attachments: {NONE}"]
        lines += [f"  {printable(attachment.filename)}" for attachment in issue.attachments]
    return lines


def _description_blocks(reading: IssueReading, *, whole: bool) -> list[str]:
    issue = reading.issue
    lines: list[str] = []
    if reading.outline is not None:
        lines += ["", "Outline of the description (--section TITLE or NUMBER reads one part):"]
        lines += [f"{item.index:>4}. h{item.level} {'  ' * (item.level - 1)}{item.title}  ({item.chars} characters)" for item in reading.outline]
        if not reading.outline:
            lines.append("      (no headings: read the description whole, or cut it with --max-chars)")
    elif reading.shows("description"):
        title = "Description" if reading.section is None else f"Description (section {reading.section.index}, {reading.section.title})"
        block = _text_block(reading, title, "description", issue.description, whole=whole, outline_help=reading.section is None)
        lines += ["", *block] if block else []
    if reading.rendered_description is not None:
        block = _text_block(reading, "Description as Jira renders it", "renderedDescription", reading.rendered_description, whole=True, outline_help=True)
        lines += ["", *block] if block else []
    return lines


def _acceptance_blocks(reading: IssueReading, *, whole: bool) -> list[str]:
    lines: list[str] = []
    if reading.shows("acceptance"):
        block = _text_block(reading, "Acceptance criteria", "acceptance", reading.issue.acceptance, whole=whole)
        lines += ["", *block] if block else []
    if reading.rendered_acceptance is not None:
        block = _text_block(reading, "Acceptance criteria as Jira renders them", "renderedAcceptance", reading.rendered_acceptance, whole=True)
        lines += ["", *block] if block else []
    return lines


def _comment_blocks(reading: IssueReading) -> list[str]:
    if not reading.shows("comments"):
        return []
    issue = reading.issue
    lines = ["", f"Comments: {issue.comment_total}" + ("" if reading.view.comments_shown else " (show with --comments)")]
    if reading.view.comments_shown:
        for comment in issue.comments:
            who = f" {comment.author.display_name}" if comment.author else ""
            lines += [f"--- #{comment.id} {comment.created}{who}", comment.body.strip(), *_trailer(reading, f"comments.{comment.id}", comment.body)]
    return lines


def _history_blocks(reading: IssueReading) -> list[str]:
    if reading.history is None:
        return []
    if not reading.history:
        lines = ["", "History: no status changes."]
    else:
        lines = ["", "History (status changes, oldest first):"]
        lines += [f"  {item.created}  {item.from_status or '-'} -> {item.to_status or '-'}" for item in reading.history]
    if not reading.history_complete:
        lines.append("  (Jira sent only part of the history, and Data Center cannot page it: older or newer changes may be missing.)")
    return lines


def issue_text(reading: IssueReading) -> str:
    """The whole view of an issue, or with `--only` the key and the parts asked for."""
    issue = reading.issue
    whole = not reading.parts
    if whole:
        status = issue.status.name if issue.status else ""
        lines = [f"{issue.key}  [{issue.issue_type} | {status} | {issue.priority}]", issue.summary, "", key_values(_default_pairs(reading))]
    else:
        lines = [issue.key]
        facts = key_values(_part_pairs(reading))
        if facts:
            lines.append(facts)
    lines += _list_blocks(reading, whole=whole)
    lines += _description_blocks(reading, whole=whole)
    lines += _acceptance_blocks(reading, whole=whole)
    lines += _comment_blocks(reading)
    lines += _history_blocks(reading)
    return "\n".join(lines)


def result_text(result: ReadResult) -> str:
    """One issue as its text; several, in the order of the keys, with a line for each key that was not found."""
    blocks = [issue_text(item) if isinstance(item, IssueReading) else _missing_text(item) for item in result.entries]
    return SEPARATOR.join(blocks)


def _missing_text(item: MissingIssue) -> str:
    return f"{item.key}  [{item.code}] {item.message}"
