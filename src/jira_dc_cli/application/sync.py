"""A folder of Markdown tickets kept in step with Jira: look (diff), bring Jira in (pull), send what you edited (push).

Jira leads. Nothing is ever deleted, no issue is created in Jira (`import-md` does that; a push creates links between issues, and never removes
one), and a field that was edited here is not overwritten by a pull, nor a field that changed in Jira by a push, unless the person says so with
--force. Each field is judged for itself: an edit of one field and a change of another in Jira are no conflict.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..domain import jql
from ..domain.branching import slugify
from ..domain.errors import AuthError, ConflictError, JiraCliError, NotFoundError, UnavailableError, ValidationError
from ..domain.fields import ACCEPTANCE, EPIC_LINK, STORY_POINTS
from ..domain.mirror import (
    BACK,
    CONFLICT,
    DIFFERS,
    IGNORE_FILE,
    JIRA_CHANGED,
    LINKS,
    LOCAL_CHANGED,
    MIXED,
    NO_SUCH_FIELD,
    SAME,
    Managed,
    MirrorFile,
    Verdict,
    agreement,
    apply_remote,
    carries,
    classify,
    file_links,
    local_managed,
    new_file,
    placeholders_in,
    remote_managed,
    skipped_fields,
    stamp,
    update_payload,
)
from ..domain.mirror_links import entries_of, new_link, plan_links, resolve_type
from ..domain.model import Issue, parse_issue_key
from ..domain.privacy import hide_users
from .issues import fetch_all, require_project
from .ports import Clock, Files, JiraGateway
from .settings import Settings
from .writing import UpdateIssueFromPayload

SYNC_FIELDS = ("summary", "issuetype", "priority", "labels", "components", "fixVersions", "description", "updated", EPIC_LINK, STORY_POINTS, ACCEPTANCE, "issuelinks")
ONLY_JIRA, ONLY_LOCAL, DUPLICATE, UNREADABLE = "only-jira", "only-local", "duplicate", "unreadable"
STATES = (SAME, DIFFERS, JIRA_CHANGED, LOCAL_CHANGED, MIXED, CONFLICT, ONLY_JIRA, ONLY_LOCAL, DUPLICATE, UNREADABLE)
# What `pull` says to a person whose edit of nothing but the spacing is neither sent nor released: both ways end the edit.
SPACING_WAY_OUT = "put the spacing back, or delete the `jira_base` line of the file and the next pull notes the file as it is"


@dataclass
class Entry:
    key: str
    state: str
    fields: list[str] = field(default_factory=list)  # the fields that differ
    file: str = ""  # the name of the file
    message: str = ""
    skipped: dict[str, str] = field(default_factory=dict)  # fields that do not take part for this file, and why
    local_fields: list[str] = field(default_factory=list)  # of those: edited here since the last agreement
    jira_fields: list[str] = field(default_factory=list)  # changed in Jira since then
    conflicts: list[str] = field(default_factory=list)  # both
    unknown: list[str] = field(default_factory=list)  # no record of an agreement


@dataclass
class Plan:
    scope: str
    fetched_at: str
    files: int
    issues: int
    entries: list[Entry]
    counts: dict[str, int]
    left_out: list[dict[str, str]]  # files and keys that were not looked at, and why


@dataclass
class _Work:
    entry: Entry
    verdict: Verdict
    path: str
    file: MirrorFile
    issue: Issue
    local: Managed
    remote: Managed
    skipped: dict[str, str]


@dataclass
class Loaded:
    plan: Plan
    works: list[_Work]
    only_jira: list[Issue]
    ids: dict[str, str | None]
    directory: str
    key_line: str
    sources: dict[str, str]  # the `source_id` of a file by its key, to name the epic of a new file


class SyncEngine:
    """Reads Jira and the folder, and says per issue where the two stand."""

    def __init__(self, gateway: JiraGateway, settings: Settings, files: Files, clock: Clock) -> None:
        self._gateway = gateway
        self._settings = settings
        self._files = files
        self._clock = clock

    def now(self) -> str:
        return self._clock.now().isoformat(timespec="seconds")

    def _ignored(self, directory: str) -> set[str]:
        path = self._files.join(directory, IGNORE_FILE)
        if not self._files.exists(path):
            return set()
        lines = self._files.read_text(path, "utf-8-sig").splitlines()
        return {line.split()[0].upper() for line in lines if line.strip() and not line.lstrip().startswith("#")}

    def load(self, directory: str, *, project: str = "", keys: Sequence[str] = ()) -> Loaded:
        if self._files.exists(directory) and not self._files.is_dir(directory):
            raise ValidationError(f"{directory} is not a folder.", hint="Name the folder that holds the Markdown tickets.")
        wanted = [parse_issue_key(key) for key in keys]
        chosen = "" if wanted else require_project(project, self._settings)
        query = f"key in ({', '.join(wanted)})" if wanted else jql.backlog_search(chosen)
        issues, total, _ = fetch_all(self._gateway, query, SYNC_FIELDS)
        if len(issues) != total:
            raise UnavailableError(f"Jira reports {total} issues but {len(issues)} were read; nothing was compared.", hint="Run the command again.")
        by_key = {issue.key: hide_users(issue) for issue in issues}
        ids = dict(self._gateway.field_ids())
        left_out: list[dict[str, str]] = []
        in_folder = self._files.is_dir(directory)
        ignored = self._ignored(directory) if in_folder else set()
        found: dict[str, list[tuple[str, MirrorFile]]] = {}
        count = 0
        for path in self._files.list_matching(directory, "*.md") if in_folder else []:
            count += 1
            name = self._files.name(path)
            try:
                mirror = MirrorFile.parse(self._files.read_text(path))
            except JiraCliError as error:
                left_out.append({"file": name, "why": f"cannot be read: {error.message}"})
                continue
            key = mirror.key
            if not key:
                left_out.append({"file": name, "why": "has no jira_key or issue_key line"})
            elif wanted and key not in wanted:
                continue
            elif not wanted and not key.startswith(chosen + "-"):
                left_out.append({"file": name, "key": key, "why": f"belongs to another project than {chosen}"})
            elif key in ignored:
                left_out.append({"file": name, "key": key, "why": f"is listed in {IGNORE_FILE}"})
            else:
                found.setdefault(key, []).append((path, mirror))
        entries: list[Entry] = []
        works: list[_Work] = []
        for key, holders in found.items():
            if len(holders) > 1:
                names = ", ".join(self._files.name(path) for path, _ in holders)
                entries.append(Entry(key, DUPLICATE, file=self._files.name(holders[0][0]), message=f"{len(holders)} files hold this key: {names}"))
                continue
            path, mirror = holders[0]
            name = self._files.name(path)
            issue = by_key.get(key)
            if issue is None:
                entries.append(Entry(key, ONLY_LOCAL, file=name, message="Jira has no such issue (moved, deleted, or a typo in the file)"))
                continue
            skipped = skipped_fields(mirror, ids, issue)
            try:
                local = local_managed(mirror, ids, skipped)
            except JiraCliError as error:
                entries.append(Entry(key, UNREADABLE, file=name, message=error.message))
                continue
            remote = remote_managed(issue, skipped)
            verdict = classify(local, remote, mirror.base)
            entry = Entry(key, verdict.state, verdict.fields, name, skipped=skipped, local_fields=verdict.local, jira_fields=verdict.jira, conflicts=verdict.conflicts, unknown=verdict.unknown)
            entries.append(entry)
            works.append(_Work(entry, verdict, path, mirror, issue, local, remote, skipped))
        only_jira: list[Issue] = []
        for key, issue in by_key.items():
            if key in found:
                continue
            if key in ignored:
                left_out.append({"key": key, "why": f"is listed in {IGNORE_FILE}"})
                continue
            only_jira.append(issue)
            entries.append(Entry(key, ONLY_JIRA, message="there is no file for it"))
        entries.sort(key=lambda entry: (int(entry.key.rsplit("-", 1)[1]), entry.key))
        counts = {state: sum(1 for entry in entries if entry.state == state) for state in STATES}
        sources = {key: holders[0][1].get("source_id") for key, holders in found.items() if len(holders) == 1 and holders[0][1].get("source_id")}
        lines = Counter(mirror.key_line for holders in found.values() for _, mirror in holders if mirror.key_line)
        key_line = lines.most_common(1)[0][0] if lines else "issue_key"
        scope = f"{', '.join(wanted)}" if wanted else f"project {chosen}"
        plan = Plan(scope, self.now(), count, len(by_key), entries, counts, left_out)
        return Loaded(plan, works, only_jira, ids, directory, key_line, sources)


class DiffMirror:
    def __init__(self, engine: SyncEngine) -> None:
        self._engine = engine

    def execute(self, directory: str, *, project: str = "", keys: Sequence[str] = ()) -> Plan:
        return self._engine.load(directory, project=project, keys=keys).plan


@dataclass
class PullOutcome:
    key: str
    action: str  # created | updated | adopted | unchanged | skipped | conflict
    file: str = ""
    fields: list[str] = field(default_factory=list)
    message: str = ""
    dry_run: bool = False
    lossy: list[str] = field(default_factory=list)  # what the Markdown of the description could not carry


@dataclass
class PullResult:
    scope: str
    outcomes: list[PullOutcome]
    left_out: list[dict[str, str]]
    only_local: list[str]
    first_error: JiraCliError | None = None
    skipped: dict[str, dict[str, str]] = field(default_factory=dict)  # per key: the fields that were not looked at, and why

    def count(self, action: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.action == action)


class PullMirror:
    """Jira into the folder. Never changes Jira."""

    def __init__(self, engine: SyncEngine, files: Files) -> None:
        self._engine = engine
        self._files = files

    def execute(
        self,
        directory: str,
        *,
        project: str = "",
        keys: Sequence[str] = (),
        update: bool = False,
        force: bool = False,
        new_files: bool = True,
        dry_run: bool = False,
    ) -> PullResult:
        loaded = self._engine.load(directory, project=project, keys=keys)
        now = self._engine.now()
        outcomes: list[PullOutcome] = []
        conflicts: list[str] = []
        for work in loaded.works:
            entry, verdict = work.entry, work.verdict
            state = verdict.state
            if state == SAME:
                record = work.file.base or {}
                own, there, current = work.local.digests(), work.remote.digests(), agreement(work.local, work.remote)
                # A field whose text was edited here in its spacing only agrees with Jira's but remains an edit: noting it as agreed would let a later
                # pull take Jira's change over it without a word. When Jira's spacing moved too, only Jira's digest is noted; the edit stays the edit.
                # It is no edit any more once the file holds exactly what Jira holds now, or what Jira held when the two last agreed.
                held = [name for name, (mine, noted) in record.items() if name in own and own[name] not in (mine, noted, there.get(name))]
                todo = [name for name in current if record.get(name) != current[name] and not (name in held and record[name][1] == there[name])]
                stale = [name for name in record if name not in current]  # a record of a field that no longer takes part
                if todo or stale:
                    self._adopt(work, loaded, todo, held, now, dry_run)
                    said = "it holds what Jira holds: noted as the state both agree on"
                    if held:
                        said += f"; your edit of the spacing in {', '.join(held)} is not sent and stays an edit ({SPACING_WAY_OUT})"
                    outcomes.append(PullOutcome(entry.key, "adopted", entry.file, held, said, dry_run))
                elif held:
                    outcomes.append(PullOutcome(entry.key, "skipped", entry.file, held, f"your edit of the spacing in {', '.join(held)} is not sent: the texts agree ({SPACING_WAY_OUT})"))
                else:
                    outcomes.append(PullOutcome(entry.key, "unchanged", entry.file))
            elif state == LOCAL_CHANGED:
                outcomes.append(PullOutcome(entry.key, "skipped", entry.file, verdict.local, "edited here and not sent yet: `jira push` sends it"))
            elif state in (JIRA_CHANGED, MIXED):
                if not update:
                    outcomes.append(PullOutcome(entry.key, "skipped", entry.file, verdict.jira, "Jira changed it: --update brings the change in"))
                    continue
                dropped = self._record(work, loaded, verdict.jira, now, dry_run)
                kept = f"your edit of {', '.join(verdict.local)} stays" if verdict.local else ""
                outcomes.append(PullOutcome(entry.key, "updated", entry.file, verdict.jira, kept, dry_run, list(dropped)))
            elif update and force:  # a field that differs without a record, or on both sides: Jira wins; the fields only edited here stay
                taken = [name for name in verdict.fields if name not in verdict.local]
                dropped = self._record(work, loaded, taken, now, dry_run)
                kept = f"your edit of {', '.join(verdict.local)} stays" if verdict.local else ""
                outcomes.append(PullOutcome(entry.key, "updated", entry.file, taken, kept, dry_run, list(dropped)))
            elif state == DIFFERS:
                outcomes.append(PullOutcome(entry.key, "skipped", entry.file, verdict.unknown, "never synced and different: --update --force lets Jira win, `jira push --force` lets the file win"))
            else:
                conflicts.append(entry.key)
                outcomes.append(PullOutcome(entry.key, "conflict", entry.file, verdict.conflicts, "Jira changed it and the file was edited too: nothing was overwritten"))
        for entry in loaded.plan.entries:  # a file that cannot be read or a key that two files hold is left alone, and said so
            if entry.state in (DUPLICATE, UNREADABLE):
                outcomes.append(PullOutcome(entry.key, "skipped", entry.file, message=entry.message))
        if new_files:
            for issue in loaded.only_jira:
                outcomes.append(self._create(issue, loaded, now, dry_run))
        else:
            outcomes.extend(PullOutcome(issue.key, "skipped", message="there is no file for it (--no-new)") for issue in loaded.only_jira)
        error = None
        if conflicts:
            error = ConflictError(
                f"{', '.join(conflicts)} changed in Jira after the last sync and was edited here too; nothing of it was overwritten.",
                hint="Read both with `jira diff`, then `jira push KEY --force` lets the file win, or `jira pull --update --force --issue KEY` lets Jira win.",
                details={"keys": conflicts},
            )
        only_local = [entry.key for entry in loaded.plan.entries if entry.state == ONLY_LOCAL]
        skipped = {entry.key: entry.skipped for entry in loaded.plan.entries if entry.skipped}
        return PullResult(loaded.plan.scope, outcomes, loaded.plan.left_out, only_local, error, skipped)

    def _record(self, work: _Work, loaded: Loaded, fields: Sequence[str], now: str, dry_run: bool) -> tuple[str, ...]:
        """Take Jira's values for `fields` into the file, note the agreement on them, and write the file."""
        dropped = apply_remote(work.file, work.issue, loaded.ids, fields)
        skipped = skipped_fields(work.file, loaded.ids, work.issue)
        stamp(work.file, local_managed(work.file, loaded.ids, skipped), remote_managed(work.issue, skipped), now, fields)
        if not dry_run:
            self._files.write_text(work.path, work.file.render())
        return dropped

    def _adopt(self, work: _Work, loaded: Loaded, names: Sequence[str], keep: Sequence[str], now: str, dry_run: bool) -> None:
        """The file holds what Jira holds for `names`: note the agreement on them (and drop the records of fields that no longer take part).
        Of the fields in `keep` only Jira's side is noted, because the file's text of them stays an edit. Nothing else in the file changes."""
        skipped = skipped_fields(work.file, loaded.ids, work.issue)
        stamp(work.file, local_managed(work.file, loaded.ids, skipped), remote_managed(work.issue, skipped), now, names, keep)
        if not dry_run:
            self._files.write_text(work.path, work.file.render())

    def _create(self, issue: Issue, loaded: Loaded, now: str, dry_run: bool) -> PullOutcome:
        slug = slugify(issue.summary)
        name = f"{issue.key}-{slug}.md" if slug else f"{issue.key}.md"
        path = self._files.join(loaded.directory, name)
        if self._files.exists(path):
            return PullOutcome(issue.key, "skipped", name, message="a file of that name exists but holds another key")
        project = issue.key.rsplit("-", 1)[0]
        file, dropped = new_file(issue, loaded.ids, key_line=loaded.key_line, project=project, parent_source=loaded.sources.get(issue.epic_key, ""))
        skipped = skipped_fields(file, loaded.ids, issue)
        stamp(file, local_managed(file, loaded.ids, skipped), remote_managed(issue, skipped), now)
        if not dry_run:
            self._files.write_text(path, file.render())
        return PullOutcome(issue.key, "created", name, dry_run=dry_run, lossy=list(dropped))


@dataclass
class LinkAction:
    """A link that a push creates (a dry run: would create): as the file writes it, as Jira shows it, and the request that creates it."""

    entry: str
    relation: str
    type_name: str
    payload: dict[str, Any]


@dataclass
class _LinkWork:
    actions: list[LinkAction] = field(default_factory=list)
    jira_only: list[str] = field(default_factory=list)  # links that Jira holds and the file does not list

    @property
    def note(self) -> str:
        return f"{', '.join(self.jira_only)}: jira has it; remove it in Jira (the CLI deletes no link)" if self.jira_only else ""


@dataclass
class _Sending:
    """What a push is about to do for one file: the fields it sends (`links` among them when the links differ), the request that changes the
    issue, the links to create and what to tell the person."""

    fields: list[str]
    payload: dict[str, Any] | None
    links: _LinkWork
    said: str


@dataclass
class PushOutcome:
    key: str
    action: str  # pushed | unchanged | refused
    file: str = ""
    fields: list[str] = field(default_factory=list)
    payload: dict[str, Any] | None = None
    dry_run: bool = False
    message: str = ""
    links: list[LinkAction] = field(default_factory=list)  # the links created (a dry run: that would be created)
    jira_only_links: list[str] = field(default_factory=list)  # links that Jira holds and the file does not list: the CLI never removes one
    error: JiraCliError | None = None  # a write that failed after another one went through: the run ends with this error


@dataclass
class PushResult:
    outcomes: list[PushOutcome]
    first_error: JiraCliError | None = None

    def count(self, action: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.action == action)


class PushMirror:
    """The edits of a file into Jira, field by field, for the issues that are named. One at a time; the first refusal ends the run."""

    def __init__(self, engine: SyncEngine, gateway: JiraGateway, files: Files, update: UpdateIssueFromPayload) -> None:
        self._engine = engine
        self._gateway = gateway
        self._files = files
        self._update = update

    def _select(self, target: str, keys: Sequence[str]) -> list[tuple[str, MirrorFile]]:
        wanted = [parse_issue_key(key) for key in keys]
        if self._files.is_dir(target):
            if not wanted:
                raise ValidationError("A folder does not say which issue to change.", hint="Name the issue with --issue KEY (several: KEY,KEY), or name the file.")
            found: dict[str, list[tuple[str, MirrorFile]]] = {}
            for path in self._files.list_matching(target, "*.md"):
                try:
                    mirror = MirrorFile.parse(self._files.read_text(path))
                except JiraCliError:
                    continue
                if mirror.key in wanted:
                    found.setdefault(mirror.key, []).append((path, mirror))
            for key in wanted:
                if key not in found:
                    raise NotFoundError(f"No file in {target} holds {key}.")
                if len(found[key]) > 1:
                    raise ValidationError(f"{len(found[key])} files in {target} hold {key}: {', '.join(self._files.name(p) for p, _ in found[key])}.")
            return [found[key][0] for key in wanted]
        if not self._files.exists(target):
            raise NotFoundError(f"No such file or folder: {target}")
        mirror = MirrorFile.parse(self._files.read_text(target))
        if not mirror.key:
            raise ValidationError(
                f"{self._files.name(target)} has no jira_key or issue_key line, so it belongs to no issue.",
                hint="`jira import-md` creates an issue from a file that has none.",
            )
        if wanted and wanted != [mirror.key]:
            raise ValidationError(f"{self._files.name(target)} holds {mirror.key}, not {', '.join(wanted)}.")
        return [(target, mirror)]

    def execute(self, target: str, *, keys: Sequence[str] = (), force: bool = False, dry_run: bool = False) -> PushResult:
        selected = self._select(target, keys)
        ids = dict(self._gateway.field_ids())
        outcomes: list[PushOutcome] = []
        for path, mirror in selected:
            name = self._files.name(path)
            try:
                outcome = self._one(path, name, mirror, ids, force, dry_run)
            except JiraCliError as error:
                outcomes.append(PushOutcome(mirror.key, "refused", name, message=error.message, dry_run=dry_run))
                return PushResult(outcomes, error)
            outcomes.append(outcome)
            if outcome.error is not None:
                return PushResult(outcomes, outcome.error)
        return PushResult(outcomes)

    def _one(self, path: str, name: str, mirror: MirrorFile, ids: dict[str, str | None], force: bool, dry_run: bool) -> PushOutcome:
        key = mirror.key
        issue = hide_users(self._gateway.get_issue(key, fields=SYNC_FIELDS))
        skipped = skipped_fields(mirror, ids, issue)
        local = local_managed(mirror, ids, skipped)
        remote = remote_managed(issue, skipped)
        verdict = classify(local, remote, mirror.base)
        state = verdict.state
        # What a person may think was sent and was not looked at. A field that this Jira does not have is no news: the file cannot send it anywhere.
        named = [name for name, why in skipped.items() if not why.startswith(NO_SUCH_FIELD)]
        left_out = f"not compared: {', '.join(named)}" if named else ""
        meanwhile = f"Jira changed {', '.join(verdict.jira)} meanwhile: `jira pull --update` brings it in" if verdict.jira else ""
        if state == SAME:
            held = f" ({left_out})" if left_out else ""
            return PushOutcome(key, "unchanged", name, message="the file holds what Jira holds" + held, dry_run=dry_run)
        if state == JIRA_CHANGED:
            return PushOutcome(key, "unchanged", name, verdict.jira, message="Jira changed it and the file was not edited: `jira pull --update` brings the change in", dry_run=dry_run)
        if state == CONFLICT and not force:
            raise ConflictError(
                f"{key} changed in Jira after the last sync and {name} was edited too (the same {', '.join(verdict.conflicts)}): nothing was sent.",
                hint="Read both with `jira diff`. --force sends the file's values; `jira pull --update --force` takes Jira's instead.",
                details={"key": key, "fields": verdict.conflicts},
            )
        if state == DIFFERS and not force:
            raise ConflictError(
                f"{name} and {key} differ in {', '.join(verdict.unknown)} and were never synced, so nobody can say which one is newer: nothing was sent.",
                hint="`jira pull` notes the files that are equal; --force sends the file's values anyway.",
                details={"key": key, "fields": verdict.unknown},
            )
        sending = [name_ for name_ in verdict.fields if name_ not in verdict.jira]  # what Jira alone changed is not reverted by the old value in the file
        marked = placeholders_in(sending, local)
        if marked:
            raise ValidationError(
                f"{name}: {', '.join(marked)} holds a placeholder ([~user], [personal link] or [email]) where Jira holds personal data; sending it would overwrite the real text.",
                hint="Take the field out of the file's changes, or write the text again without the placeholder.",
            )
        if "description" in sending and not force:
            lost = carries(issue.description)
            if lost:
                named = [item for item in lost if item != BACK] + (["text that changes on the way back"] if BACK in lost else [])
                raise ValidationError(
                    f"The description of {key} holds {', '.join(named)}, which Markdown cannot carry unchanged: sending the file's text would change more than you edited.",
                    hint="Change that description in Jira, or --force to send the file's text anyway.",
                )
        fields_sent = [name_ for name_ in sending if name_ != LINKS]
        payload = update_payload(fields_sent, local, ids) if fields_sent else None
        links = self._links_to_create(mirror, issue, name) if LINKS in sending else _LinkWork()
        said = "; ".join(part for part in (meanwhile, left_out, links.note) if part)
        if payload is None and not links.actions:  # all that is left is a link that only Jira holds, and nothing can be done about it from here
            return PushOutcome(key, "unchanged", name, [LINKS], message=said, dry_run=dry_run, jira_only_links=links.jira_only)
        if dry_run:
            shown = fields_sent + ([LINKS] if links.actions else [])
            return PushOutcome(key, "pushed", name, shown, payload, True, said, links.actions, links.jira_only)
        return self._send(path, name, mirror, issue, local, skipped, _Sending(sending, payload, links, said))

    def _links_to_create(self, mirror: MirrorFile, issue: Issue, name: str) -> _LinkWork:
        """What the push does about the links, checked before anything is sent: a link that only the file lists is created if its type exists and
        the other issue exists (and not if Jira holds it already); a link that only Jira holds is named, never removed."""
        plan = plan_links(file_links(mirror), entries_of(issue))
        jira_only = [entry.text() for entry in plan.jira_only]
        if not plan.create:
            return _LinkWork([], jira_only)
        try:
            types = self._gateway.list_link_types()
        except NotFoundError:
            raise NotFoundError(
                f"{name}: Jira has no link types (HTTP 404): issue linking seems to be disabled, so no link can be created.",
                hint="A Jira administrator turns issue linking on in the administration.",
            ) from None
        checked: set[str] = set()
        actions: list[LinkAction] = []
        for entry in plan.create:
            try:
                link_type = resolve_type(types, entry.type_name)
            except ValidationError as error:
                raise ValidationError(f"{name}: {entry.text()}: {error.message}", hint=error.hint) from None
            if entry.key not in checked:
                self._check_exists(entry.key, f"{name}: {entry.text()}")
                checked.add(entry.key)
            new = new_link(mirror.key, entry, link_type)
            actions.append(LinkAction(entry.text(), new.relation, new.type_name, new.payload))
        return _LinkWork(actions, jira_only)

    def _check_exists(self, key: str, where: str) -> None:
        try:
            self._gateway.get_issue(key, fields=["summary"])
        except NotFoundError:
            raise NotFoundError(f"{where}: {key} does not exist in Jira, or you may not see it: nothing was sent.", hint="Check the key in the links line.") from None

    def _send(self, path: str, name: str, mirror: MirrorFile, issue: Issue, local: Managed, skipped: dict[str, str], todo: _Sending) -> PushOutcome:
        """Write the fields first, guarded against a change of the issue since it was read (a link changes the issue too), then the links one by
        one, and note in the file what the two agree on now."""
        key, said = mirror.key, todo.said
        if todo.payload is not None:
            self._update.execute(key, todo.payload, dry_run=False, if_unchanged_since=issue.updated)
        created: list[LinkAction] = []
        failure: JiraCliError | None = None
        for action in todo.links.actions:
            try:
                self._gateway.create_link(action.payload)
            except JiraCliError as error:
                failure = _link_refused(error, action)
                break
            created.append(action)
        if failure is not None and todo.payload is None and not created:
            raise failure  # nothing went through: a refusal like any other
        if failure is not None:
            left = ", ".join(action.entry for action in todo.links.actions[len(created) :])
            said = "; ".join(part for part in (f"links not created ({left}): {failure.message}", said) if part)
        fields = [name_ for name_ in todo.fields if name_ != LINKS]
        try:
            fresh = remote_managed(hide_users(self._gateway.get_issue(key, fields=SYNC_FIELDS)), skipped)
            settled, edit = list(fields), []
            if LINKS in todo.fields and failure is None and local.values.get(LINKS) == fresh.values.get(LINKS):
                settled.append(LINKS)
            elif created:
                # The links that were made are Jira's now, but what is left to do is still an edit made here: it must not turn into a conflict.
                settled.append(LINKS)
                edit.append(LINKS)
            if settled:
                stamp(mirror, local, fresh, self._engine.now(), settled, edit)
                self._files.write_text(path, mirror.render())
        except (JiraCliError, OSError) as error:  # Jira holds the change by now: it is not "refused"; the next pull notes the state
            reason = getattr(error, "message", "") or str(error)
            failed = f"Jira holds the change, but the file could not be brought up to date ({reason}): `jira pull` notes the new state"
            said = "; ".join(part for part in (failed, said) if part)
        sent = fields + ([LINKS] if created else [])
        return PushOutcome(key, "pushed", name, sent, todo.payload, message=said, links=created, jira_only_links=todo.links.jira_only, error=failure)


def _link_refused(error: JiraCliError, action: LinkAction) -> JiraCliError:
    """Say what a refusal to create a link means. The 11.3 reference answers 401 (and in practice 403) when the Link Issues permission is missing
    and 404 when linking is disabled or an issue is not visible; the token was accepted for the reads of this very push, so it is not the token."""
    if isinstance(error, AuthError):
        return AuthError(
            f"{action.relation}: {error.message}",
            hint="Creating a link needs the 'Link Issues' permission in the project of this issue, and Browse Projects for the other one; Jira answers HTTP 401 or 403 "
            "when it is missing (the token was accepted for the reads before). Ask a Jira administrator.",
            details={**error.details, "link": action.entry},
        )
    if isinstance(error, NotFoundError):
        return NotFoundError(
            f"{action.relation}: {error.message}",
            hint="Issue linking may be disabled in this Jira, or one of the issues is not visible to you. `jira link-types` lists the link types.",
            details={**error.details, "link": action.entry},
        )
    return error
