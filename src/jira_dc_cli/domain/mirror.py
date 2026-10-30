"""A Markdown ticket of a mirror folder, and the part of it that is kept in step with Jira.

The file is the one `import-md` reads: front matter lines `key: value`, then the description as Markdown. It belongs to an issue by
its `jira_key` or `issue_key` line (a mirror may use either). Lines that are not managed here, the order of the lines and the text
outside what is rewritten stay as they are; a file that is rewritten ends up with plain line feeds and without a byte order mark.

What is kept in step: the summary, the priority, the labels, the components, the fix versions, the story points and the acceptance
criteria (both in `fields_json`, where the importer reads them), the issue links (the `links:` line, see `mirror_links`) and the description
(the Markdown body). Not the assignee, the status, the sprint or the comments: people are not mirrored, and the rest changes too often to be a
copy that stays true.

To tell which side changed since the two last agreed, the file carries `jira_base`: for every field `name=LOCAL/REMOTE`, a short digest
of what the file held and of what Jira held when the two agreed on that field. A field of the file that no longer matches its LOCAL digest
was edited here; a field of Jira that no longer matches its REMOTE digest was changed there. Two digests, because the text does not always
come back the same from Markdown. Per field, because a field edited here and another changed in Jira are no conflict: each side can take
over the other's change without touching its own.

A file from before the links were kept in step has no record for them. It never listed any, so the record is taken to be "none on either side":
the links that Jira holds are news to the file (`jira changed`), the ones that were written into it are an edit (`edited here`), and no file
turns `differs` because of a field that did not exist when it was synced.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .drafts import parse_json_object, split_list
from .errors import ValidationError
from .fields import ACCEPTANCE, STORY_POINTS
from .mirror_links import LinkEntry, entries_of, forms, parse_line, render_line, unwritable_types
from .model import Issue
from .privacy import holds_placeholder, redact_text
from .unwiki import wiki_to_markdown
from .wiki import markdown_to_wiki

KEY_LINES = ("jira_key", "issue_key")
BASE_LINE = "jira_base"
SYNCED_LINE = "jira_synced"
IGNORE_FILE = "jira-sync-ignore.txt"
LINKS = "links"  # the field, and the front matter line that lists the issue links

SAME, LOCAL_CHANGED, JIRA_CHANGED, MIXED, CONFLICT, DIFFERS = "same", "local-changed", "jira-changed", "mixed", "conflict", "differs"
LISTS = {"labels": "labels", "components": "components", "fixVersions": "fix_versions"}  # field -> front matter line
TEXTS = ("summary", "priority", "acceptance", "description")
BACK = "changes on the way back"  # what `lossy:` says of a description that Markdown does not carry unchanged, although it names nothing
NO_SUCH_FIELD = "this Jira has no"  # how the reason of a field that this Jira does not have begins (`skipped_fields`)
LINKS_AFTER = ("fix_versions", "components", "labels", "priority", "summary")  # a `links:` line that is added comes after the first of these lines that exists

_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")
_BASE_ENTRY = re.compile(r"([A-Za-z]+)=([0-9a-f]{6})/([0-9a-f]{6})")
# A link whose target is neither an address, an anchor nor an issue key: the importer of a planning mirror turns it into text for Jira.
# An image is no link (`![](shot.png)` is how an attachment looks); it counts only when its target is a path (`../docs/img/a.png`).
_RELATIVE_LINK = re.compile(r"(?<!!)\[[^\]\n]{0,200}\]\((?!https?:|mailto:|#|<|[A-Za-z][A-Za-z0-9_]*-[0-9]+\))[^)\s]{1,300}\)")
_RELATIVE_IMAGE = re.compile(r"!\[[^\]\n]{0,200}\]\((?!https?:|data:|<)[^)\s/\\]{0,300}[/\\][^)\s]{0,300}\)")
_DASH_LIST = re.compile(r"^([*#-]+)([ \t]+\S.*)$", re.MULTILINE)  # `-` makes a bullet in Jira's markup, written back as `*`
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# JSON leaves these as they are; `str.splitlines`, which the importer reads the front matter with, breaks a line at each of them.
_LINE_BREAKS = str.maketrans({"\u0085": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"})

Base = dict[str, tuple[str, str]]  # field -> (digest of the file's value, digest of Jira's value) at the last agreement


def normalize(text: Any) -> str:
    """Text as it is compared: without people, and without caring about line breaks and spacing. A backslash and an n, which a
    spreadsheet or a script leaves where a line break was, count as the line break."""
    return re.sub(r"\s+", " ", redact_text(("" if text is None else str(text)).replace("\\n", "\n"))).strip()


def one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def protect_quotes(text: str) -> str:
    """A value that begins and ends with the same quote loses that pair when it is read (the importer does the same): write one more pair."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[0] + text + text[0]
    return text


def line_of(name: str, value: Any) -> str:
    text = protect_quotes(one_line(value))
    return f"{name}: {text}" if text else f"{name}:"


def without_code(text: str) -> str:
    """The text without its fenced code blocks and inline code: `handlers[0](event)` in code is no link."""
    kept: list[str] = []
    fence = ""
    for line in text.split("\n"):
        found = _FENCE.match(line)
        if fence:
            bare = line.strip()
            if found and bare[:1] == fence[0] and len(bare) >= len(fence) and bare == bare[0] * len(bare):
                fence = ""
            continue
        if found:
            fence = found.group(1)
            continue
        kept.append(_INLINE_CODE.sub("", line))
    return "\n".join(kept)


def light(value: Any) -> Any:
    """A value as it is recorded: text with plain line feeds and no trailing spaces, but with its blank lines and its indentation (a
    changed indentation of a code sample is an edit); a list without its order."""
    if isinstance(value, str):
        return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    if isinstance(value, (list, tuple)):
        return sorted(value)
    return value


def _digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(light(value), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:6]


NO_LINKS = (_digest([]), _digest([]))  # the record of the links of a file that was synced before they were kept in step: none on either side


def carries(description: str) -> tuple[str, ...]:
    """What Markdown cannot carry of a Jira description. Sending the Markdown back would then change more than the person edited.

    What the converter names (a table, a panel, a macro, ...). Where it names nothing, `changes on the way back` when the way to Markdown and
    back changes the text all the same (a code parameter, a literal star, a mark inside a mark). A bullet made with `-` comes back as `*`:
    the words are the same, only Jira may draw the two bullets differently, so that is not counted as a change."""
    converted = wiki_to_markdown(description)
    if converted.dropped:
        return tuple(converted.dropped)
    if _stars(light(markdown_to_wiki("\n" + converted.text))) != _stars(light(description)):
        return (BACK,)
    return ()


def _stars(text: str) -> str:
    return _DASH_LIST.sub(lambda match: match.group(1).replace("-", "*") + match.group(2), text)


class MirrorFile:
    """The front matter lines as they are, and the text after them."""

    def __init__(self, head: list[str], rest: str) -> None:
        self.head = head
        self.rest = rest

    @classmethod
    def parse(cls, text: str) -> "MirrorFile":
        lines = str(text).replace("\r\n", "\n").lstrip("\ufeff").split("\n")
        if not lines or lines[0].rstrip() != "---":
            raise ValidationError("The file has no front matter: it must start with a --- line.")
        for index in range(1, len(lines)):
            if lines[index].rstrip() == "---":
                return cls(lines[1:index], "\n".join(lines[index + 1 :]))
        raise ValidationError("missing closing --- in front matter")

    def render(self) -> str:
        return "\n".join(["---", *self.head, "---"]) + "\n" + self.rest

    def _index(self, name: str) -> int:
        pattern = re.compile(re.escape(name) + r"\s*:")
        for index, line in enumerate(self.head):
            if pattern.match(line):
                return index
        return -1

    def has(self, name: str) -> bool:
        return self._index(name) >= 0

    def get(self, name: str) -> str:
        index = self._index(name)
        if index < 0:
            return ""
        value = self.head[index].split(":", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value

    def set(self, name: str, value: Any, *, after: Sequence[str] = ()) -> None:
        """Write the line. A line that is not there yet is added at the end, or after the first of the lines in `after` that exists."""
        line = line_of(name, value)
        index = self._index(name)
        if index >= 0:
            self.head[index] = line
            return
        for anchor in after:
            place = self._index(anchor)
            if place >= 0:
                self.head.insert(place + 1, line)
                return
        self.head.append(line)

    def remove(self, name: str) -> None:
        index = self._index(name)
        if index >= 0:
            del self.head[index]

    @property
    def key(self) -> str:
        for name in KEY_LINES:
            value = self.get(name)
            if _KEY.fullmatch(value):
                return value.upper()
        return ""

    @property
    def key_line(self) -> str:
        for name in KEY_LINES:
            if _KEY.fullmatch(self.get(name)):
                return name
        return ""

    def fields(self) -> dict[str, Any]:
        text = self.get("fields_json")
        return parse_json_object(text, "fields_json") if text else {}

    def set_fields(self, updates: Mapping[str, Any]) -> None:
        """Change entries of `fields_json`; a value of None removes the entry. The other entries and their order stay. The line is written as
        JSON, whole: the blanks inside a text (an acceptance criterion) are not collapsed as they are in the other lines."""
        current = self.fields()
        for name, value in updates.items():
            if value is None:
                current.pop(name, None)
            else:
                current[name] = value
        line = "fields_json: " + json.dumps(current, ensure_ascii=False, separators=(",", ":")).translate(_LINE_BREAKS)
        index = self._index("fields_json")
        if index >= 0:
            self.head[index] = line
        else:
            self.head.append(line)

    @property
    def base(self) -> Base | None:
        found = {name: (local, remote) for name, local, remote in _BASE_ENTRY.findall(self.get(BASE_LINE))}
        return found or None


@dataclass(frozen=True)
class Managed:
    """What is kept in step: `values` normalized (compared and digested), `raw` as it would be written or sent, and the fields that are
    left out of the comparison with the reason."""

    values: Mapping[str, Any]
    raw: Mapping[str, Any]
    skipped: Mapping[str, str] = field(default_factory=dict)

    def digests(self) -> dict[str, str]:
        """A short digest of each field as it stands (see `light`): a whitespace-only edit changes it, the order of a list does not."""
        return {name: _digest(self.raw[name]) for name in self.values}


def _points(value: Any) -> float | None:
    """The story points of `fields_json`: a number, or a text that holds one. Anything else is an error, because reading it as `no points`
    would let a push clear the field."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValidationError(f"The story points in fields_json are not a number: {value!r}.", hint="Write a number, or take the entry out.")
    try:
        number = float(value)
    except (ValueError, OverflowError):  # a text that is no number, an integer too large for a float
        raise ValidationError(f"The story points in fields_json are not a number: {value!r}.", hint="Write a number, or take the entry out.") from None
    if not math.isfinite(number):
        raise ValidationError(f"The story points in fields_json are not a number: {value!r}.", hint="Write a number, or take the entry out.")
    return number


def skipped_fields(file: MirrorFile, ids: Mapping[str, str | None], issue: Issue | None = None) -> dict[str, str]:
    """The fields that do not take part for this file, and why. With the issue, also the lists that cannot be written as a line and the links of
    an issue with a link type whose name the `links:` line cannot hold."""
    left: dict[str, str] = {}
    if not ids.get(STORY_POINTS):
        left["storyPoints"] = f"{NO_SUCH_FIELD} story points field"
    if not ids.get(ACCEPTANCE):
        left["acceptance"] = f"{NO_SUCH_FIELD} acceptance criteria field"
    if file.get("mirror").strip().lower() == "sanitized":
        left["description"] = "the file says mirror: sanitized, its text is not Jira's"
    else:
        plain = without_code(file.rest)
        if _RELATIVE_LINK.search(plain) or _RELATIVE_IMAGE.search(plain):
            left["description"] = "the text holds a relative link, which Jira cannot show the same way, so the description is not compared"
    if issue is not None:
        for name, values in (("labels", issue.labels), ("components", issue.components), ("fixVersions", issue.fix_versions)):
            if any("," in value for value in values):
                left[name] = "a name with a comma cannot be written in a line of comma separated names"
        if unwritable_types(issue):
            left[LINKS] = "a link type with a comma, < or > in its name cannot be written in the links line"
    return left


def _build(raw: dict[str, Any], skipped: Mapping[str, str]) -> Managed:
    values: dict[str, Any] = {}
    for name, value in raw.items():
        if name in LISTS or name == LINKS:
            values[name] = sorted(value)
        elif name == "storyPoints":
            values[name] = value
        else:
            values[name] = normalize(value)
    return Managed(values, raw, dict(skipped))


def file_links(file: MirrorFile) -> list[LinkEntry]:
    """The links the `links:` line of the file lists. An error when the line cannot be read, or when it links the issue to itself."""
    return parse_line(file.get(LINKS), file.key)


def local_managed(file: MirrorFile, ids: Mapping[str, str | None], skipped: Mapping[str, str]) -> Managed:
    raw: dict[str, Any] = {"summary": file.get("summary").strip(), "priority": file.get("priority").strip()}
    for name, line in LISTS.items():
        if name not in skipped:
            raw[name] = split_list(file.get(line))
    entries = file.fields()
    if "storyPoints" not in skipped:
        raw["storyPoints"] = _points(entries.get(ids.get(STORY_POINTS) or ""))
    if "acceptance" not in skipped:
        raw["acceptance"] = str(entries.get(ids.get(ACCEPTANCE) or "") or "")
    if "description" not in skipped:
        raw["description"] = markdown_to_wiki("\n" + file.rest).strip()
    if LINKS not in skipped:
        raw[LINKS] = forms(file_links(file))
    return _build(raw, skipped)


def remote_managed(issue: Issue, skipped: Mapping[str, str]) -> Managed:
    """The same fields from an issue (already without people). The fields left out for the file are left out here too."""
    raw: dict[str, Any] = {"summary": issue.summary.strip(), "priority": issue.priority}
    for name, values in (("labels", issue.labels), ("components", issue.components), ("fixVersions", issue.fix_versions)):
        if name not in skipped:
            raw[name] = list(values)
    if "storyPoints" not in skipped:
        raw["storyPoints"] = issue.story_points
    if "acceptance" not in skipped:
        raw["acceptance"] = issue.acceptance
    if "description" not in skipped:
        raw["description"] = issue.description
    if LINKS not in skipped:
        raw[LINKS] = forms(entries_of(issue))
    return _build(raw, skipped)


def differing(local: Managed, remote: Managed) -> list[str]:
    return [name for name in local.values if local.values[name] != remote.values.get(name)]


@dataclass(frozen=True)
class Verdict:
    """Where a file stands against Jira, field by field.

    `fields`: every field that differs. Of those, `local` were edited here since the last agreement, `jira` changed in Jira, `conflicts`
    are both, and `unknown` have no record of an agreement, so nobody can say which side is newer. `state` sums it up."""

    state: str
    fields: list[str] = field(default_factory=list)
    local: list[str] = field(default_factory=list)
    jira: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def classify(local: Managed, remote: Managed, base: Base | None) -> Verdict:
    """Compare per field with what the two last agreed on.

    A field that differs and was never agreed on is `unknown`. Otherwise it was edited here (`local`), changed in Jira (`jira`), or both
    (`conflicts`); a field that differs but is what it was at the last agreement on both sides, as a text that does not come back the same
    from Markdown, is no difference. The state: `conflict` when there is a conflicting field, `differs` when there is an unknown one,
    `mixed` when some fields were edited here and others changed in Jira, else `local-changed`, `jira-changed` or `same`.

    The links are the one field that is never `unknown`: a record that has none (a file from before they were kept in step) counts as `NO_LINKS`."""
    fields = differing(local, remote)
    own, theirs = local.digests(), remote.digests()
    edited: list[str] = []
    changed: list[str] = []
    conflicts: list[str] = []
    unknown: list[str] = []
    real: list[str] = []
    for name in fields:
        record = (base or {}).get(name) or (NO_LINKS if name == LINKS else None)
        if record is None:
            unknown.append(name)
            real.append(name)
            continue
        here, there = own[name] != record[0], theirs[name] != record[1]
        if here and there:
            conflicts.append(name)
        elif here:
            edited.append(name)
        elif there:
            changed.append(name)
        else:
            continue
        real.append(name)
    if not real:
        return Verdict(SAME)
    if conflicts:
        state = CONFLICT
    elif unknown:
        state = DIFFERS
    elif edited and changed:
        state = MIXED
    else:
        state = LOCAL_CHANGED if edited else JIRA_CHANGED
    return Verdict(state, real, edited, changed, conflicts, unknown)


def agreement(local: Managed, remote: Managed) -> Base:
    """What a file and Jira agree on right now, for every field that is compared."""
    own, theirs = local.digests(), remote.digests()
    return {name: (own[name], theirs[name]) for name in own}


def stamp(file: MirrorFile, local: Managed, remote: Managed, now: str, fields: Sequence[str] | None = None, keep: Sequence[str] = ()) -> None:
    """Record the agreement on `fields` (all of them by default): the digests of both sides and the time. The records of the other fields stay,
    so that what is still waiting to be pulled or pushed keeps waiting. Of a field in `keep` only Jira's digest is recorded: what the file held
    when the two last agreed stays the reference, so that an edit made here stays an edit (for the links of a file that has no record yet, the
    reference is `NO_LINKS`; any other field without a record gets both digests)."""
    current = agreement(local, remote)
    kept = {name: pair for name, pair in (file.base or {}).items() if name in current}
    for name in current if fields is None else [name for name in fields if name in current]:
        reference = kept.get(name) or (NO_LINKS if name == LINKS else None)
        kept[name] = (reference[0], current[name][1]) if name in keep and reference else current[name]
    order = list(local.values)
    file.set(BASE_LINE, ",".join(f"{name}={kept[name][0]}/{kept[name][1]}" for name in order if name in kept))
    file.set(SYNCED_LINE, now)


def apply_remote(file: MirrorFile, issue: Issue, ids: Mapping[str, str | None], fields: Sequence[str]) -> tuple[str, ...]:
    """Write what Jira holds for `fields` into the file. Returns what the Markdown of the description could not carry. The issue type and the
    epic of a file are written when the file is made and never changed afterwards."""
    dropped: tuple[str, ...] = ()
    for name in fields:
        if name == "summary":
            file.set("summary", one_line(issue.summary))
        elif name == "priority":
            file.set("priority", issue.priority)
        elif name in LISTS:
            file.set(LISTS[name], ",".join({"labels": issue.labels, "components": issue.components, "fixVersions": issue.fix_versions}[name]))
        elif name == "storyPoints":
            file.set_fields({ids.get(STORY_POINTS) or "": issue.story_points})
        elif name == "acceptance":
            file.set_fields({ids.get(ACCEPTANCE) or "": issue.acceptance or None})
        elif name == LINKS:
            file.set(LINKS, render_line(entries_of(issue)), after=LINKS_AFTER)
        elif name == "description":
            file.rest = "\n" + wiki_to_markdown(issue.description).text
            dropped = carries(issue.description)
            if dropped:
                file.set("lossy", ", ".join(dropped))
            else:
                file.remove("lossy")
    return dropped


def new_file(issue: Issue, ids: Mapping[str, str | None], *, key_line: str, project: str, parent_source: str = "") -> tuple[MirrorFile, tuple[str, ...]]:
    """A file for an issue that has none, in the format the importer reads. People are left out: `assignee:` stays empty."""
    head = [
        line_of(key_line, issue.key),
        line_of("project", project),
        line_of("issuetype", issue.issue_type),
        line_of("summary", issue.summary),
        line_of("priority", issue.priority),
        line_of("labels", ",".join(issue.labels)),
        line_of("components", ",".join(issue.components)),
        line_of("fix_versions", ",".join(issue.fix_versions)),
    ]
    listed = [] if unwritable_types(issue) else entries_of(issue)  # a file of an issue without links has no line for them; the field is left out when a type cannot be written
    if listed:
        head.append(line_of(LINKS, render_line(listed)))
    if issue.epic_key:
        head.append(line_of("epic_key", issue.epic_key))
    if parent_source:
        head.append(line_of("parent_id", parent_source))
    head.append("assignee:")
    file = MirrorFile(head, "")
    entries: dict[str, Any] = {}
    if ids.get(STORY_POINTS) and issue.story_points is not None:
        entries[ids.get(STORY_POINTS) or ""] = issue.story_points
    if ids.get(ACCEPTANCE) and issue.acceptance:
        entries[ids.get(ACCEPTANCE) or ""] = issue.acceptance
    file.set_fields(entries)
    dropped = apply_remote(file, issue, ids, ["description"])
    return file, dropped


def update_payload(changed: Sequence[str], local: Managed, ids: Mapping[str, str | None]) -> dict[str, Any]:
    """The request that makes Jira hold what the file holds, for the fields that differ and for no others. The links are no field of the issue:
    each one is a request of its own (`mirror_links.new_link`), so they are not part of this payload."""
    fields: dict[str, Any] = {}
    for name in changed:
        if name == LINKS:
            continue
        value = local.raw[name]
        for text in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(text, str) and "\x00" in text:
                raise ValidationError(f"The {name} of the file holds a NUL character, which is no text: nothing was sent.", hint="Take the character out of the file.")
        if name == "summary":
            if not value:
                raise ValidationError("The summary of the file is empty, and Jira needs one.")
            fields["summary"] = value
        elif name == "priority":
            if not value:
                raise ValidationError("The priority of the file is empty: take the line out, or write a priority.")
            fields["priority"] = {"name": value}
        elif name == "labels":
            fields["labels"] = list(value)
        elif name in ("components", "fixVersions"):
            fields[name] = [{"name": item} for item in value]
        elif name == "storyPoints":
            fields[ids.get(STORY_POINTS) or ""] = value
        elif name == "acceptance":
            fields[ids.get(ACCEPTANCE) or ""] = value
        elif name == "description":
            fields["description"] = value
    return {"fields": fields}


def placeholders_in(changed: Sequence[str], local: Managed) -> list[str]:
    """The fields to be sent that hold a placeholder, in a text or in a name of a list: it stands where Jira holds personal data, so sending it
    would overwrite the real text."""
    marked = []
    for name in changed:
        value = local.raw.get(name)
        if name == LINKS:
            continue  # the name of a link type is no personal data
        if isinstance(value, (list, tuple)):
            if any(holds_placeholder(item) for item in value):
                marked.append(name)
        elif name in ("summary", "description", "acceptance") and holds_placeholder(value):
            marked.append(name)
    return marked
