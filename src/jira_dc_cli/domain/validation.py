"""Checking the values of an issue payload against what Jira's metadata allows, before anything is sent."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

from .comments import placeholders_added
from .errors import RejectedError, ValidationError
from .model import Issue, Transition
from .privacy import PLACEHOLDERS

LIST_LIMIT = 30  # names in a hint or a listing: the rest is counted ("and 12 more")

# What the texts, and the fields of names, that can hold a mention or an address are called. The fields of names (labels, components, fix
# versions) are the names that people give: `jira issue` redacts them like a text, and an item of such a list is compared one by one.
NAME_FIELDS = ("labels", "components", "fixVersions")
COMPARED = ("summary", "description", "acceptance", "labels", "components", "fixVersions")  # what is compared with the issue in Jira
TEXT_NAMES = {
    "summary": "summary",
    "description": "description",
    "acceptance": "acceptance criteria",
    "labels": "labels",
    "components": "components",
    "fixVersions": "fix versions",
}

# The standard fields whose values a dry run checks, and what one value of each is called (singular, plural).
CHECKED: dict[str, tuple[str, str]] = {
    "priority": ("priority", "priorities"),
    "resolution": ("resolution", "resolutions"),
    "components": ("component", "components"),
    "fixVersions": ("fix version", "fix versions"),
    "versions": ("affected version", "affected versions"),
}


@dataclass(frozen=True)
class Allowed:
    """One value that a field accepts."""

    id: str = ""
    name: str = ""

    @property
    def text(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class FieldRule:
    """What the metadata says about one field."""

    field_id: str
    name: str = ""
    required: bool = False
    has_default: bool = False
    allowed: tuple[Allowed, ...] | None = None  # None: the metadata lists no values (free text, a person, a number)

    @property
    def label(self) -> str:
        return self.name or self.field_id

    @property
    def allowed_names(self) -> list[str]:
        return [item.text for item in self.allowed or ()]


FieldRules = dict[str, FieldRule]  # by field id


@dataclass(frozen=True)
class TransitionScreen:
    """A transition that is possible now, and the fields of its screen."""

    transition: Transition
    fields: tuple[FieldRule, ...] = ()


@dataclass(frozen=True)
class Reference:
    """How a payload names one value of a field: by id, by name or by both."""

    id: str = ""
    name: str = ""

    @property
    def text(self) -> str:
        return self.name or self.id

    def matches(self, allowed: Allowed) -> bool:
        """By id, or by name without regard to case (Jira may be stricter, never more lenient)."""
        return bool(self.id and self.id == allowed.id) or bool(self.name and self.name.casefold() == allowed.name.casefold())


@dataclass(frozen=True)
class Problem:
    field_id: str
    kind: str  # "value": not among the allowed values; "required": cleared although required; "screen": the field is not on the screen
    value: str = ""
    allowed: tuple[str, ...] = ()


def listing(names: Sequence[str], limit: int = LIST_LIMIT) -> str:
    """The names for a hint, at most `limit`, then how many more."""
    shown = list(names[:limit])
    rest = len(names) - len(shown)
    text = ", ".join(shown) if shown else "none"
    return f"{text} and {rest} more" if rest > 0 else text


def _references(value: Any) -> tuple[list[Reference], bool]:
    """The values that a field value names, and whether it is empty (which clears the field). A value that names nothing, an empty name or
    id, stays in the list as an empty reference: it is no value that the field allows, and Jira refuses it. A shape without `id`, `name`
    or `value` is not one that the metadata can judge, and is left alone."""
    if value is None or value in ("", [], {}):
        return [], True
    found: list[Reference] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, Mapping):
            if any(key in item for key in ("id", "name", "value")):
                found.append(Reference(str(item.get("id") or ""), str(item.get("name") or item.get("value") or "")))
        elif isinstance(item, (str, int)) and not isinstance(item, bool):
            found.append(Reference(name=str(item)))
    return found, False


def stated(payload: Mapping[str, Any], field_id: str) -> tuple[list[Reference], bool]:
    """The values that a payload gives a field in `fields` and in the `add` and `set` of `update`, and whether it clears the field."""
    found: list[Reference] = []
    cleared = False
    fields = payload.get("fields")
    if isinstance(fields, Mapping) and field_id in fields:
        values, empty = _references(fields[field_id])
        found, cleared = found + values, cleared or empty
    update = payload.get("update")
    operations = update.get(field_id) if isinstance(update, Mapping) else None
    for operation in operations if isinstance(operations, list) else []:
        if not isinstance(operation, Mapping):
            continue
        for verb in ("add", "set"):
            if verb in operation:
                values, empty = _references(operation[verb])
                found, cleared = found + values, cleared or (verb == "set" and empty)
    return found, cleared


def check_values(payload: Mapping[str, Any], rules: Mapping[str, FieldRule]) -> list[Problem]:
    """What Jira would refuse in the standard fields of the payload: a value that is not among the allowed ones, a required field that is
    cleared, and a field that the metadata (the screen) does not have, which Jira refuses too. Custom fields, and fields whose metadata
    lists no values, are not judged."""
    problems: list[Problem] = []
    for field_id in CHECKED:
        references, cleared = stated(payload, field_id)
        if not references and not cleared:
            continue
        rule = rules.get(field_id)
        if rule is None:
            problems.append(Problem(field_id, "screen"))
            continue
        if cleared and rule.required:
            problems.append(Problem(field_id, "required"))
        if rule.allowed is None:
            continue
        for reference in references:
            if not any(reference.matches(allowed) for allowed in rule.allowed):
                problems.append(Problem(field_id, "value", reference.text, tuple(rule.allowed_names)))
    return problems


def missing_required(payload: Mapping[str, Any], rules: Mapping[str, FieldRule]) -> list[FieldRule]:
    """The fields that the metadata requires, that have no default, and that the payload does not mention."""
    fields, update = payload.get("fields"), payload.get("update")
    mentioned = {*(fields if isinstance(fields, Mapping) else ()), *(update if isinstance(update, Mapping) else ())}
    return [rule for rule in rules.values() if rule.required and not rule.has_default and rule.field_id not in mentioned]


def required_warning(rule: FieldRule, where: str) -> str:
    values = f" (allowed: {listing(rule.allowed_names, 8)})" if rule.allowed else ""
    return f"{rule.label} ({rule.field_id}) is required {where}, and the payload does not set it{values}."


def _describe(problem: Problem, where: str) -> str:
    singular = CHECKED[problem.field_id][0]
    if problem.kind == "value":
        if not problem.value:
            return f"{singular} is empty {where}: it needs a name or an id"
        return f"{singular} '{problem.value}' is not available {where}"
    if problem.kind == "required":
        return f"{singular} is required {where} and cannot be cleared"
    return f"{singular} cannot be set {where}: it is not on the screen"


def _short(problem: Problem) -> str:
    singular = CHECKED[problem.field_id][0]
    shown = {"value": f"{singular} '{problem.value}'" if problem.value else f"empty {singular}", "required": f"{singular} (cleared)"}
    return shown.get(problem.kind, f"{singular} (not on the screen)")


def refusal(problems: Sequence[Problem], where: str, *, screen_hint: str = "") -> RejectedError:
    """The error of a dry run that found values Jira would refuse: it names them and the hint lists what is allowed (`where` is for
    example `for PROJ-1` or `for the transition 'Finish' (81) of PROJ-2`)."""
    if len(problems) == 1:
        message = _describe(problems[0], where) + "."
    else:
        message = f"{len(problems)} values would be refused {where}: " + "; ".join(_short(item) for item in problems) + "."
    hints: list[str] = []
    for field_id in dict.fromkeys(item.field_id for item in problems):
        mine = [item for item in problems if item.field_id == field_id]
        wrong = next((item for item in mine if item.kind == "value"), None)
        if wrong is not None:
            hints.append(f"Allowed {CHECKED[field_id][1]}: {listing(wrong.allowed)}.")
        if any(item.kind == "required" for item in mine):
            hints.append(f"Give the {CHECKED[field_id][0]} a value instead of clearing it.")
        if screen_hint and any(item.kind == "screen" for item in mine):
            hints.append(screen_hint)
    details = {
        "problems": [
            {"field": item.field_id, "kind": item.kind, "value": item.value, "allowed": list(item.allowed[:LIST_LIMIT])}
            for item in problems
        ]
    }
    return RejectedError(message, hint=" ".join(dict.fromkeys(hints)), details=details)


def create_target(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    """The project key and the issue type id that a create payload names; None when it names them in another way (by id, by name)."""
    fields = payload.get("fields")
    if not isinstance(fields, Mapping):
        return None
    project, kind = fields.get("project"), fields.get("issuetype")
    key = project.get("key") if isinstance(project, Mapping) else None
    type_id = kind.get("id") if isinstance(kind, Mapping) else None
    if isinstance(key, str) and key.strip() and type_id not in (None, "") and not isinstance(type_id, (dict, list)):
        return key.strip(), str(type_id)
    return None


# -- texts and names that hold a stand-in for personal data -------------------------------------------------------------------------------
# `jira issue` shows `[~user]`, `[email]` and `[personal link]` where Jira holds a mention, an address or a link, in the texts (summary,
# description, acceptance criteria) and in the names of labels, components and fix versions. A text or a list that an agent edited from
# that reading and sends back would overwrite the real one: `comment --edit`, `push` and the imports refuse it, and so do `update` and
# `update-json` (with the rules below), which compare with what Jira holds now. Other custom text fields are not judged.


def _written(payload: Mapping[str, Any]) -> Iterator[tuple[str, Any, str]]:
    """(field id, value, how) for each value that a payload writes: an entry of `fields`, or the `add` or the `set` of an operation in
    `update`. A `remove` writes nothing."""
    fields, update = payload.get("fields"), payload.get("update")
    for field_id, value in fields.items() if isinstance(fields, Mapping) else ():
        yield str(field_id), value, "fields"
    for field_id, operations in update.items() if isinstance(update, Mapping) else ():
        for operation in operations if isinstance(operations, list) else ():
            if isinstance(operation, Mapping):
                for how in ("add", "set"):
                    if how in operation:
                        yield str(field_id), operation[how], how


def _set_texts(payload: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """(field id, text) for each text that a payload sets: a string in `fields`, or the `set` of an operation in `update`."""
    for field_id, value, how in _written(payload):
        if isinstance(value, str) and how != "add":
            yield field_id, value


def custom_texts(payload: Mapping[str, Any]) -> list[str]:
    """The texts that a payload sets in custom fields: one of them may be the acceptance criteria, whose id is Jira's to say."""
    return [text for field_id, text in _set_texts(payload) if field_id.startswith("customfield_")]


def text_changes(payload: Mapping[str, Any], acceptance_id: str = "") -> list[tuple[str, str]]:
    """The texts of a payload in the fields that can hold a mention or an address, as (`summary`, `description` or `acceptance`, text).
    The acceptance criteria are a custom field: they count only when their id is given."""
    names = {"summary": "summary", "description": "description"}
    if acceptance_id:
        names[acceptance_id] = "acceptance"
    return [(names[field_id], text) for field_id, text in _set_texts(payload) if field_id in names]


def _names_of(value: Any) -> list[str]:
    """The names that the value of a list field gives: the strings of a list of labels, the `name` of each component or version."""
    names = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def name_changes(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The names that a payload gives to labels, components and fix versions, as (`labels`, `components` or `fixVersions`, name): the items
    of a list in `fields`, and what the `add` and the `set` of an operation in `update` bring."""
    return [(field_id, name) for field_id, value, _ in _written(payload) if field_id in NAME_FIELDS for name in _names_of(value)]


def new_stand_ins(changes: Sequence[tuple[str, str]], current: Mapping[str, str | Sequence[str] | None]) -> dict[str, list[str]]:
    """By name of the text or the list, the stand-ins that a new text or a new item holds and Jira does not hold: they come from a reading
    that hid a mention or an address, and sending them back would overwrite the real one. A text that holds one in Jira already (somebody
    wrote the literal `[email]`) may keep it, and so may an item that the current list holds verbatim."""
    found: dict[str, list[str]] = {}
    for name, text in changes:
        held = current.get(name) or ""
        if isinstance(held, str):
            marks = placeholders_added(held, text)
        else:  # a list of names: an item that Jira holds verbatim may stay
            marks = [] if text in held else placeholders_added("", text)
        for mark in marks:
            if mark not in found.setdefault(name, []):
                found[name].append(mark)
    return {name: marks for name, marks in found.items() if marks}


def current_values(issue: Issue) -> dict[str, str | list[str]]:
    """What the issue in Jira holds now, by the names of `COMPARED`: the values that `new_stand_ins` compares a new text or name with. An
    issue that was read without a field has its default there: only the fields that were asked for count."""
    return {
        "summary": issue.summary,
        "description": issue.description,
        "acceptance": issue.acceptance,
        "labels": issue.labels,
        "components": issue.components,
        "fixVersions": issue.fix_versions,
    }


def _called(names: Sequence[str]) -> str:
    labels = [TEXT_NAMES[name] for name in names]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _holds(names: Sequence[str]) -> str:
    """`holds` for one text, `hold` for several things or for a list of names."""
    return "holds" if len(names) == 1 and names[0] not in NAME_FIELDS else "hold"


def _marks(found: Mapping[str, Sequence[str]]) -> list[str]:
    held = {mark for marks in found.values() for mark in marks}
    return [mark for mark in PLACEHOLDERS if mark in held]


def _way_out(key: str, transition: bool) -> str:
    """The sentence of the hint for a person who really wants to write the stand-in: the raw call of `jira api`, which has no guard."""
    lead = "If the stand-in is really what you want to write, send it with "
    if transition:
        return (
            f"{lead}`jira api POST issue/{key}/transitions --data-file FILE --write`, a file with "
            '{"transition": {"id": "ID"}, "fields": {...}} ' + f"(`jira transition {key}` lists the ids; the raw call has no guard)."
        )
    return f"{lead}`jira api PUT issue/{key} --data-file FILE --write` (the raw call has no guard)."


def stand_in_refusal(found: Mapping[str, Sequence[str]], key: str = "", *, transition: bool = False) -> ValidationError:
    """The error of an update, or of a transition with fields, whose new text or names would overwrite the real mentions and addresses of
    the current ones with stand-ins. Its wording is that of `comment --edit`, which refuses the same thing (exit code 1). The hint names the
    way out: the raw call of `jira api` (`key` is the issue of the command; a transition is a POST to its `transitions`, not a PUT)."""
    names = list(found)
    return ValidationError(
        f"The new {_called(names)} {_holds(names)} {', '.join(_marks(found))}, which stands in place of a mention or an address that the "
        "CLI hides: sending it would overwrite the real one.",
        hint="The CLI shows [~user], [email] and [personal link] in place of real mentions, addresses and links. Write the real mention "
        "([~username], see `jira users resolve`) or leave that part out; a text that holds people is best edited in Jira itself. "
        + _way_out(key or "KEY", transition),
        details={"fields": names, "placeholders": _marks(found)},
    )


def stand_in_note(found: Mapping[str, Sequence[str]]) -> str:
    """What a dry run of `create` says about a new text or name that holds stand-ins: nothing is overwritten, so it is no refusal."""
    names = list(found)
    return (
        f"The {_called(names)} of the new issue {_holds(names)} {', '.join(_marks(found))}, which stands in place of a mention or an "
        "address that the CLI hides: Jira would keep it as plain text, not as the real person or address."
    )
