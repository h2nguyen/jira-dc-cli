"""Use cases of validation, reached as `app.validation`: the check of what a dry run would send, and the transition with its fields."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..domain.drafts import FRESH_LIST
from ..domain.errors import RejectedError, UsageError, ValidationError
from ..domain.fields import ACCEPTANCE
from ..domain.model import IssueTypeInfo, Transition, parse_issue_key
from ..domain.payloads import transition_payload
from ..domain.privacy import holds_placeholder
from ..domain.timeutil import ensure_unchanged_since
from ..domain.transitions import pick_transition
from ..domain.validation import (
    CHECKED,
    COMPARED,
    FieldRules,
    TransitionScreen,
    check_values,
    current_values,
    custom_texts,
    listing,
    missing_required,
    name_changes,
    new_stand_ins,
    refusal,
    required_warning,
    stand_in_refusal,
    text_changes,
)
from .ports import Clock, Diagnostics, Files, JiraGateway
from .settings import Settings


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def stand_ins_of(gateway: JiraGateway, payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The texts of the payload (summary, description, acceptance criteria) and the names that it gives to labels, components and fix
    versions that hold a stand-in for personal data (`[~user]`, `[email]`, `[personal link]`), as (name, text). Jira is asked for the id of
    the acceptance criteria field only when a custom field of the payload holds one: in the usual case nothing is asked. Other custom text
    fields are not judged. `update`, `update-json` and the fields of a `transition` are judged with it."""
    acceptance = ""
    if any(holds_placeholder(text) for text in custom_texts(payload)):
        acceptance = gateway.field_ids().get(ACCEPTANCE) or ""
    held = [*text_changes(payload, acceptance), *name_changes(payload)]
    return [(name, text) for name, text in held if holds_placeholder(text)]


class IssueValidator:
    """Checks what a dry run of `create` or `update` would send against the metadata of Jira, so that a value that Jira would refuse fails
    the dry run (exit code 6) with the allowed values in the hint, and not the live run. Jira is asked once for each project and issue
    type, and for each issue, in a run; a value that is refused, and an issue type that is not found, are looked for once more in what Jira
    says now before they are refused, because what the project offers can change while the program runs (`jira mcp` runs for hours) and
    the list of issue types is kept for a week. Custom fields are not judged; a live run is not checked."""

    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway
        self._create: dict[tuple[str, str], FieldRules] = {}
        self._edit: dict[str, FieldRules] = {}

    def _issue_type(
        self, project_key: str, issue_type_id: str, issue_types: Sequence[IssueTypeInfo] | None, fresh: bool
    ) -> IssueTypeInfo:
        """The issue type of the id, from the list that was given or asked for. A miss in a list that may come from the cache sends one more
        request that bypasses it, before the type is refused."""
        types = list(issue_types) if issue_types is not None else self._gateway.list_issue_types(project_key, refresh=fresh)
        named = next((item for item in types if item.id == issue_type_id), None)
        if named is None and not fresh:
            types = self._gateway.list_issue_types(project_key, refresh=True)
            named = next((item for item in types if item.id == issue_type_id), None)
        if named is None:
            available = ", ".join(f"{item.name} ({item.id})" for item in sorted(types, key=lambda item: item.name))
            raise RejectedError(
                f"Issue type {issue_type_id} is not available in {project_key}.",
                hint=f"Available: {available or 'none'}. {FRESH_LIST}",
                details={"issueTypes": [item.id for item in types]},
            )
        return named

    def check_create(
        self,
        project_key: str,
        issue_type_id: str,
        payload: Mapping[str, Any],
        *,
        issue_types: Sequence[IssueTypeInfo] | None = None,
        refresh: bool = False,
    ) -> list[str]:
        """The warnings for a create payload: fields that the project requires and the payload leaves out, and fields that its create
        screen lacks. A value that is not allowed, or an issue type that the project does not have, raises `RejectedError`. `refresh` says
        that the issue types (`issue_types`, or the list that is asked for) were read from Jira just now and not from the cache."""
        named = self._issue_type(project_key, issue_type_id, issue_types, refresh)
        key = (project_key, issue_type_id)
        where = f"in {project_key} for {named.name} ({named.id})"
        remembered = key in self._create
        if not remembered:
            self._create[key] = self._gateway.get_create_fields(*key)
        try:
            warnings = self._off_screen(payload, self._create[key], where, "create")
        except RejectedError:
            if not remembered:
                raise
            self._create[key] = self._gateway.get_create_fields(*key)  # what the project offers may have changed since it was asked
            warnings = self._off_screen(payload, self._create[key], where, "create")
        return warnings + [_capital(required_warning(rule, where)) for rule in missing_required(payload, self._create[key])]

    def check_update(self, key: str, payload: Mapping[str, Any]) -> list[str]:
        """The warnings for an update payload: fields that the edit screen lacks. A value that is not allowed raises `RejectedError`."""
        remembered = key in self._edit
        if not remembered:
            self._edit[key] = self._gateway.get_edit_metadata(key)
        try:
            return self._off_screen(payload, self._edit[key], f"for {key}", "edit")
        except RejectedError:
            if not remembered:
                raise
            self._edit[key] = self._gateway.get_edit_metadata(key)  # what the issue allows may have changed since it was asked
            return self._off_screen(payload, self._edit[key], f"for {key}", "edit")

    @staticmethod
    def _off_screen(payload: Mapping[str, Any], rules: FieldRules, where: str, screen: str) -> list[str]:
        problems = check_values(payload, rules)
        wrong = [item for item in problems if item.kind != "screen"]
        if wrong:
            raise refusal(wrong, where)
        return [
            _capital(f"{CHECKED[item.field_id][0]} is not on the {screen} screen {where}: Jira will probably refuse it.")
            for item in problems
        ]


@dataclass
class TransitionOutcome:
    """What `jira transition` found or did: the transitions that are possible, the one it chose, and whether it sent that one or only
    showed what it would send."""

    key: str
    current_status: str
    options: list[Transition] = field(default_factory=list)
    chosen: Transition | None = None
    dry_run: bool = False
    executed: bool = False
    payload: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)  # a dry run: what Jira may refuse (a field that the screen requires, and is not set)
    screens: list[TransitionScreen] = field(default_factory=list)  # with --show-fields: the screens of the transitions and their fields


class TransitionWithFields:
    """`jira transition`: moves an issue and sends the fields of the transition screen (`--fields-json`, `--resolution`), or lists what
    the screens ask for (`--show-fields`). The dry run checks the fields against the screen of the transition; a live run does not. The
    texts and the names of those fields (summary, description, acceptance criteria, labels, components, fix versions) are judged first,
    as in `update`: one that holds a stand-in for personal data which Jira does not hold is refused, before anything is sent, with the
    read that every call makes (the comment of a transition is a new text: there is nothing to overwrite)."""

    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    @staticmethod
    def _fields(fields: Mapping[str, Any] | None, resolution: str) -> dict[str, Any]:
        sent = dict(fields or {})
        if resolution.strip():
            if "resolution" in sent:
                raise ValidationError(
                    "resolution is set twice: by --resolution and in --fields-json.",
                    hint="Set it once: take it out of --fields-json, or leave --resolution out.",
                )
            sent["resolution"] = {"name": resolution.strip()}
        return sent

    @staticmethod
    def _check_options(
        *, target: str, comment: str, fields: Mapping[str, Any] | None, resolution: str, since: str, dry_run: bool, show_fields: bool
    ) -> None:
        """What does not go together: listing with writing, and what travels with a transition without a transition."""
        if show_fields and (comment.strip() or fields or resolution.strip() or since or dry_run):
            raise UsageError(
                "--show-fields only lists: it cannot be combined with --comment, --fields-json, --resolution, --if-unchanged-since "
                "or --dry-run.",
                hint="List the fields first, then run the transition with what its screen asks for.",
            )
        asked = (
            ("--comment", comment.strip()),
            ("--fields-json", fields),
            ("--resolution", resolution.strip()),
            ("--if-unchanged-since", since),
        )
        given = [name for name, value in asked if value]
        if given and not target:
            verb, pronoun = ("needs", "it is") if len(given) == 1 else ("need", "they are")
            raise ValidationError(
                f"{' and '.join(given)} {verb} --to: {pronoun} used together with a transition.",
                hint="Say which transition with --to STATUS; without it the command only lists what is possible.",
            )

    def _possible(self, key: str, with_screens: bool) -> tuple[list[Transition], list[TransitionScreen]]:
        """The transitions that are possible now and, when the screens are wanted, the fields of each (one request either way)."""
        if with_screens:
            screens = self._gateway.list_transition_screens(key)
            return [item.transition for item in screens], screens
        return self._gateway.list_transitions(key), []

    def execute(
        self,
        key: str,
        *,
        to: str = "",
        comment: str = "",
        fields: Mapping[str, Any] | None = None,
        resolution: str = "",
        dry_run: bool = False,
        show_fields: bool = False,
        if_unchanged_since: str = "",
    ) -> TransitionOutcome:
        """Without `to` it only lists what is possible from the current status; with `show_fields` also what each screen holds."""
        key = parse_issue_key(key)
        target = to.strip()
        self._check_options(
            target=target,
            comment=comment,
            fields=fields,
            resolution=resolution,
            since=if_unchanged_since,
            dry_run=dry_run,
            show_fields=show_fields,
        )
        sent = self._fields(fields, resolution)
        stand_ins = stand_ins_of(self._gateway, {"fields": sent}) if sent else []
        asked = {name for name, _ in stand_ins}
        # The one read that every call makes (the status, and `updated` for the guard) also holds what a stand-in is compared with.
        issue = self._gateway.get_issue(key, fields=["status", "updated", *(name for name in COMPARED if name in asked)])
        found = new_stand_ins(stand_ins, current_values(issue))
        if found:
            raise stand_in_refusal(found, key, transition=True)
        if if_unchanged_since:
            ensure_unchanged_since(key, issue.updated, if_unchanged_since)
        current = issue.status.name if issue.status else ""
        options, screens = self._possible(key, show_fields or (dry_run and bool(target)))
        if not target:
            return TransitionOutcome(key, current, options, screens=screens)
        chosen = pick_transition(options, target)
        if show_fields:
            return TransitionOutcome(key, current, options, screens=[item for item in screens if item.transition.id == chosen.id])
        payload = transition_payload(chosen.id, comment, sent)
        if dry_run:
            warnings = self._check(key, chosen, payload, screens)
            return TransitionOutcome(key, current, options, chosen, dry_run=True, payload=payload, warnings=warnings)
        self._gateway.transition_issue(key, payload)
        return TransitionOutcome(key, current, options, chosen, executed=True, payload=payload)

    @staticmethod
    def _check(key: str, chosen: Transition, payload: Mapping[str, Any], screens: Sequence[TransitionScreen]) -> list[str]:
        """What a dry run says about the fields of the payload: a value that the screen does not allow, or a field it does not have, is
        refused (Jira refuses it too); a field that the screen requires and the payload lacks is a warning."""
        screen = next((item for item in screens if item.transition.id == chosen.id), None)
        fields = screen.fields if screen else ()
        rules = {rule.field_id: rule for rule in fields}
        where = f"for the transition '{chosen.name}' ({chosen.id}) of {key}"
        problems = check_values(payload, rules)
        if problems:
            raise refusal(
                problems,
                where,
                screen_hint=f"The screen has these fields: {listing([rule.field_id for rule in fields])}. "
                f"`jira transition {key} --to {chosen.id} --show-fields` lists them with their values.",
            )
        return [_capital(required_warning(rule, where)) for rule in missing_required(payload, rules)]


@dataclass
class ValidationUseCases:
    """The use cases of validation, one field each. `IssueValidator` is not one: `create` and `update` use it in their dry runs."""

    transition: TransitionWithFields


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> ValidationUseCases:
    return ValidationUseCases(transition=TransitionWithFields(gateway))
