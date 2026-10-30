"""Use cases of agile, reached as `app.agile`: boards, sprints, the review and the velocity, the backlog, epics, and the writes that
move issues, rank them and create or change sprints."""
from __future__ import annotations

import functools
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Mapping, Sequence

from ..domain import agile as rules
from ..domain import sprints as review_rules
from ..domain.agile import Board, Epic, SprintInfo
from ..domain.errors import AuthError, JiraCliError, NotFoundError, RejectedError, UsageError, ValidationError
from ..domain.model import Issue, parse_issue_key, parse_project_key
from ..domain.privacy import hide_users, redact_text
from ..domain.sprints import SprintReview, VelocityRow
from ..domain.timeutil import ensure_unchanged_since, parse_timestamp
from .ports import Clock, Diagnostics, Files, JiraGateway
from .settings import Settings

MAX_VELOCITY = 20
LIST_LIMIT = 1000
_ISSUE_FIELDS = ["summary", "issuetype", "status", "updated", "epic_link", "sprint"]


def _safe_board(board: Board) -> Board:
    return replace(board, name=redact_text(board.name))


def _safe_sprint(sprint: SprintInfo) -> SprintInfo:
    return replace(sprint, name=redact_text(sprint.name), goal=redact_text(sprint.goal))


def _safe_epic(epic: Epic) -> Epic:
    return replace(epic, name=redact_text(epic.name), summary=redact_text(epic.summary))


def _check_limit(limit: int, top: int = LIST_LIMIT) -> None:
    if not 1 <= limit <= top:
        raise UsageError(f"--limit must be between 1 and {top}.")


@contextmanager
def _needs(what: str, permission: str) -> Iterator[None]:
    """A refusal (HTTP 403) inside the block says which permission the action needs; a rejected token (401) keeps its own hint."""
    try:
        yield
    except AuthError as error:
        if error.details.get("status") != 403:
            raise
        raise AuthError(
            error.message,
            hint=f"{what} needs a Jira Software licence and the project permission '{permission}'. Ask a Jira administrator.",
            details=error.details,
        ) from error


def _browsing(method: Callable[..., Any]) -> Callable[..., Any]:
    """A use case that reads boards, sprints or epics: its 403 names the permission."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _needs("Reading boards, sprints and epics", "Browse Projects"):
            return method(*args, **kwargs)

    return wrapper


def _after_partial(error: JiraCliError, done: Sequence[str], total: int) -> JiraCliError:
    """The error of a later batch, with what the earlier ones already changed: a write is never retried, so the person must know."""
    if not done:
        return error
    message = f"{error.message} {len(done)} of {total} issues were already changed: {', '.join(done)}."
    return type(error)(message, hint=error.hint, details={**error.details, "changed": list(done)})


class Resolver:
    """Finds the board and the sprint that a command means: from an id, from the name of a sprint, or from the project."""

    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def board(self, board: int | None, project: str, *, sprints: bool) -> Board:
        """The board given by --board, or the one board of the project (the current one by default); `sprints`: one that has sprints."""
        if board is not None:
            found = self._gateway.get_board(rules.parse_board_id(board))
            return rules.require_sprints(found) if sprints else found
        chosen = project or self._settings.project
        if not chosen:
            raise UsageError("No board and no project selected.", hint="Pass --board ID or --project KEY, or run `jira use KEY`.")
        key = parse_project_key(chosen)
        if not sprints:
            return rules.choose_board(self._gateway.list_boards(project=key), key, kind="")
        scrum = self._gateway.list_boards(project=key, board_type="scrum")
        if not scrum:
            raise rules.no_sprint_board(key, self._gateway.list_boards(project=key))
        return rules.choose_board(scrum, key, kind="scrum")

    def sprint(self, reference: str, board: int | None, project: str) -> SprintInfo:
        """A sprint by id, or by name among the sprints of the board."""
        parsed = rules.parse_sprint_ref(reference)
        if isinstance(parsed, int):
            return self._gateway.get_sprint(parsed)
        found = self.board(board, project, sprints=True)
        return rules.resolve_sprint(self._gateway.list_sprints(found.id), parsed)


# -- reading -------------------------------------------------------------------------------------


@dataclass
class BoardList:
    boards: list[Board]
    total: int


class ListBoards:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    @_browsing
    def execute(self, *, project: str = "", board_type: str = "", name: str = "", limit: int = 50) -> BoardList:
        _check_limit(limit)
        found = self._gateway.list_boards(
            project=parse_project_key(project) if project else "", board_type=rules.parse_board_type(board_type), name=name.strip()
        )
        shown = sorted(found, key=lambda board: (board.name.casefold(), board.id))[:limit]
        return BoardList([replace(_safe_board(board), projects=tuple(self._projects(board.id))) for board in shown], len(found))

    def _projects(self, board_id: int) -> list[str]:
        try:
            return self._gateway.board_projects(board_id)
        except (NotFoundError, AuthError):
            return []  # a board whose projects you may not see is still a board


@dataclass
class SprintList:
    board: Board
    sprints: list[SprintInfo]
    total: int


class ListSprints:
    def __init__(self, gateway: JiraGateway, resolver: Resolver) -> None:
        self._gateway = gateway
        self._resolver = resolver

    @_browsing
    def execute(self, *, board: int | None = None, project: str = "", states: str = "", limit: int = 50) -> SprintList:
        _check_limit(limit)
        wanted = rules.parse_states(states)
        found = self._resolver.board(board, project, sprints=True)
        sprints = self._gateway.list_sprints(found.id, states=wanted)
        return SprintList(_safe_board(found), [_safe_sprint(sprint) for sprint in rules.order_sprints(sprints)[:limit]], len(sprints))


class Reviewer:
    """The review of one sprint: the issues that are or were in it, with their history, sorted by the rules of `domain.sprints`."""

    def __init__(self, gateway: JiraGateway, clock: Clock) -> None:
        self._gateway = gateway
        self._clock = clock

    def categories(self) -> dict[str, str]:
        return review_rules.status_categories(self._gateway.status_category_list())

    def review(self, sprint: SprintInfo, categories: Mapping[str, str], *, with_subtasks: bool = False) -> SprintReview:
        if sprint.state == "future":
            raise ValidationError(
                f"Sprint {rules.sprint_line(sprint)} has not started: there is nothing to review.",
                hint="Review an active or a closed sprint; `jira sprint ID --issues` lists what is planned in this one.",
                details={"sprintId": sprint.id, "state": sprint.state},
            )
        started = sprint.start_date or sprint.activated_date
        if not started:
            raise ValidationError(
                f"Sprint {rules.sprint_line(sprint)} has no start date: it cannot be reviewed.",
                hint="Jira holds no start date for it: `jira sprint ID` shows what it holds.",
                details={"sprintId": sprint.id, "state": sprint.state},
            )
        if sprint.state == "closed":
            finished = sprint.complete_date or sprint.end_date
            if not finished:
                raise ValidationError(f"Sprint {rules.sprint_line(sprint)} is closed but Jira gives no date for it: it cannot be reviewed.")
            end, ended = parse_timestamp(finished), True
        else:
            end, ended = self._clock.now(), False
        members = self._gateway.search_with_history(review_rules.members_jql(sprint.id))
        try:
            left = self._gateway.search_with_history(review_rules.removed_jql(sprint.id))
            searched = True
        except RejectedError:
            left, searched = [], False  # the review goes on without them and says what that costs
        return review_rules.review_sprint(
            sprint.id,
            [*members, *left],
            start=parse_timestamp(started),
            end=end,
            members={issue.key for issue in members},
            categories=categories,
            ended=ended,
            with_subtasks=with_subtasks,
            leavers_searched=searched,
        )


@dataclass
class SprintView:
    sprint: SprintInfo
    issues: list[Issue] | None = None
    review: SprintReview | None = None


class ShowSprint:
    def __init__(self, gateway: JiraGateway, resolver: Resolver, reviewer: Reviewer) -> None:
        self._gateway = gateway
        self._resolver = resolver
        self._reviewer = reviewer

    @_browsing
    def execute(
        self,
        sprint: str,
        *,
        board: int | None = None,
        project: str = "",
        issues: bool = False,
        review: bool = False,
        with_subtasks: bool = False,
    ) -> SprintView:
        found = self._resolver.sprint(sprint, board, project)
        listed = [hide_users(issue) for issue in self._gateway.list_sprint_issues(found.id)] if issues else None
        reviewed = self._reviewer.review(found, self._reviewer.categories(), with_subtasks=with_subtasks) if review else None
        return SprintView(_safe_sprint(found), listed, reviewed)


@dataclass
class VelocityResult:
    board: Board
    rows: list[VelocityRow]
    average_completed: float | None
    notes: list[str] = field(default_factory=list)
    left_out: list[int] = field(default_factory=list)  # the closed sprints that could not be reviewed, by id


class Velocity:
    def __init__(self, gateway: JiraGateway, resolver: Resolver, reviewer: Reviewer) -> None:
        self._gateway = gateway
        self._resolver = resolver
        self._reviewer = reviewer

    @_browsing
    def execute(self, *, board: int | None = None, project: str = "", last: int = 5) -> VelocityResult:
        if not 1 <= last <= MAX_VELOCITY:
            raise UsageError(f"--last must be between 1 and {MAX_VELOCITY}.", hint="Each sprint is one review: a long history is slow.")
        found = self._resolver.board(board, project, sprints=True)
        closed = rules.order_sprints(self._gateway.list_sprints(found.id, states=["closed"]))[:last]
        categories = self._reviewer.categories() if closed else {}
        rows: list[VelocityRow] = []
        left_out: list[int] = []
        notes = list(review_rules.DERIVATION_NOTES)
        for sprint in closed:
            shown = _safe_sprint(sprint)
            try:
                result = self._reviewer.review(sprint, categories)
            except ValidationError as error:
                notes.append(f"{shown.name}: left out. {error.message}")
                left_out.append(sprint.id)
                continue
            rows.append(review_rules.velocity_row(shown, result))
            notes.extend(f"{shown.name}: {warning}" for warning in result.warnings)
        average = review_rules.average([row.completed_points for row in rows])
        return VelocityResult(_safe_board(found), rows, average, notes, left_out)


@dataclass
class BacklogView:
    board: Board
    issues: list[Issue]
    total: int


class ShowBacklog:
    def __init__(self, gateway: JiraGateway, resolver: Resolver) -> None:
        self._gateway = gateway
        self._resolver = resolver

    @_browsing
    def execute(self, *, board: int | None = None, project: str = "", limit: int = 50) -> BacklogView:
        _check_limit(limit)
        found = self._resolver.board(board, project, sprints=False)
        issues, total = self._gateway.board_backlog(found.id, limit=limit)
        return BacklogView(_safe_board(found), [hide_users(issue) for issue in issues], total)


@dataclass
class EpicList:
    board: Board
    epics: list[Epic]


class ListEpics:
    def __init__(self, gateway: JiraGateway, resolver: Resolver) -> None:
        self._gateway = gateway
        self._resolver = resolver

    @_browsing
    def execute(self, *, board: int | None = None, project: str = "", include_done: bool = False) -> EpicList:
        found = self._resolver.board(board, project, sprints=False)
        return EpicList(_safe_board(found), [_safe_epic(epic) for epic in self._gateway.list_epics(found.id, include_done=include_done)])


# -- writing -------------------------------------------------------------------------------------


def read_issues(gateway: JiraGateway, keys: Sequence[str], since: str = "") -> list[Issue]:
    """Reads every issue that a write will touch, so that an unknown key fails before anything is sent; with `since` it refuses an
    issue that changed after that time."""
    found = []
    for key in keys:
        issue = gateway.get_issue(key, fields=_ISSUE_FIELDS)
        if since:
            ensure_unchanged_since(key, issue.updated, since)
        found.append(issue)
    return found


def current_issues(typed: Sequence[str], read: Sequence[Issue]) -> tuple[dict[str, Issue], list[str]]:
    """The issues that were read, under the key that Jira holds for each, in the order given and each once, and a note for every key that
    Jira knows under another name. An old key of an issue that moved to another project reads as its new key; what a write sends and what
    its read-back is compared with must be what Jira calls the issue."""
    found: dict[str, Issue] = {}
    notes: list[str] = []
    for key, issue in zip(typed, read):
        current = issue.key or key
        if current != key:
            notes.append(f"{key} is now {current}.")
        found.setdefault(current, issue)
    return found, notes


@dataclass
class MoveResult:
    """What a move of issues into a sprint or an epic did, or with a dry run would do."""

    target: str
    name: str
    keys: list[str]
    skipped: list[str]
    payload: dict[str, Any]
    requests: int
    dry_run: bool
    confirmed: list[str] | None = None  # the keys sent that the read-back found in the sprint or the epic; None without a read-back
    missing: list[str] | None = None  # the keys sent that it did not find there; None without a read-back
    notes: list[str] = field(default_factory=list)
    read_back: bool = False  # whether the sprint or the epic was read after the move; False for a dry run, nothing to send, a failed read


@dataclass(kw_only=True)
class SprintMoveResult(MoveResult):
    sprint: SprintInfo


def _send_in_batches(send: Callable[[dict[str, Any]], None], keys: Sequence[str]) -> None:
    done: list[str] = []
    try:
        for part in rules.batches(keys):
            send(rules.assign_payload(part))
            done.extend(part)
    except JiraCliError as error:
        raise _after_partial(error, done, len(keys)) from error


def _confirm(result: MoveResult, read: Callable[[], list[Issue]]) -> None:
    """Reads what a move did into the result. A read-back that fails must not hide that the move was made."""
    try:
        found = [issue.key for issue in read()]
    except JiraCliError as error:
        result.notes.append(f"The issues were moved, but they could not be read back: {error.message}")
        return
    result.confirmed = found
    result.missing = [key for key in result.keys if key not in found]
    result.read_back = True


def _leaving_sprints(issues: Sequence[Issue], target: SprintInfo) -> list[str]:
    """A note for each issue that is in another sprint that is not closed: moving it takes it out of there (a scope change)."""
    return [
        f"{issue.key} leaves {redact_text(item.name)} ({item.state.lower()})."
        for issue in issues
        for item in issue.sprints
        if item.id != target.id and item.state.upper() != "CLOSED"
    ]


class AddToSprint:
    def __init__(self, gateway: JiraGateway, resolver: Resolver) -> None:
        self._gateway = gateway
        self._resolver = resolver

    def execute(
        self,
        keys: str,
        sprint: str,
        *,
        board: int | None = None,
        project: str = "",
        if_state: str = "",
        if_unchanged_since: str = "",
        dry_run: bool = False,
    ) -> SprintMoveResult:
        wanted = rules.split_keys(keys)
        target = self._resolver.sprint(sprint, board, project)
        rules.check_state(target, rules.parse_states(if_state))
        rules.require_open(target)
        issues, renamed = current_issues(wanted, read_issues(self._gateway, wanted, if_unchanged_since))
        already = [key for key, issue in issues.items() if any(item.id == target.id for item in issue.sprints)]
        to_send = [key for key in issues if key not in already]
        notes = [*renamed, *_leaving_sprints([issues[key] for key in to_send], target)]
        if not to_send:
            notes.append("Nothing to send: every issue is already in the sprint.")
        payload = rules.assign_payload(to_send)
        requests = len(rules.batches(to_send))
        result = SprintMoveResult(
            str(target.id), redact_text(target.name), to_send, already, payload, requests, dry_run, notes=notes, sprint=_safe_sprint(target)
        )
        if dry_run or not to_send:
            return result
        with _needs("Moving issues into a sprint", "Schedule Issues"):
            _send_in_batches(lambda body: self._gateway.move_issues_to_sprint(target.id, body), to_send)
        _confirm(result, lambda: self._gateway.list_sprint_issues(target.id, keys=to_send))
        return result


@dataclass
class RankResult:
    keys: list[str]
    position: str  # before or after
    anchor: str
    payload: dict[str, Any]
    dry_run: bool
    order: list[str] | None = None  # the moved issues and the anchor in the order of the rank; None when the order was not read
    confirmed: bool | None = None  # whether they stand where they were asked to; None when the order was not read
    notes: list[str] = field(default_factory=list)
    read_back: bool = False  # whether the order was read after the rank; False for a dry run and for a failed read


class RankIssues:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, keys: str, *, before: str = "", after: str = "", if_unchanged_since: str = "", dry_run: bool = False) -> RankResult:
        wanted = rules.split_keys(keys)
        rules.rank_payload(wanted, before=before, after=after)  # a request that cannot be right is refused before anything is read
        typed = [*wanted, parse_issue_key(before or after)]
        read = read_issues(self._gateway, typed, if_unchanged_since)
        current = [issue.key or key for key, issue in zip(typed, read)]
        moved, anchor = list(dict.fromkeys(current[:-1])), current[-1]
        payload = rules.rank_payload(moved, before=anchor if before else "", after="" if before else anchor)
        position = "before" if "rankBeforeIssue" in payload else "after"
        renamed = [f"{key} is now {now}." for key, now in zip(typed, current) if key != now]
        result = RankResult(moved, position, anchor, payload, dry_run, notes=renamed)
        if dry_run:
            return result
        with _needs("Ranking issues", "Schedule Issues"):
            self._gateway.rank_issues(payload)
        try:
            order = self._gateway.rank_order([*moved, anchor])
        except JiraCliError as error:
            result.notes.append(f"The issues were ranked, but their order could not be read back: {error.message}")
            return result
        result.order, result.read_back = order, True
        result.confirmed = rules.ranked_as_asked(order, moved, anchor, before=position == "before")
        if not result.confirmed:
            result.notes.append("Read back in the order of the rank, the issues do not stand where they were asked to: check the board.")
        return result


class AddToEpic:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, keys: str, epic: str, *, if_unchanged_since: str = "", dry_run: bool = False) -> MoveResult:
        wanted = rules.split_keys(keys)
        text = (epic or "").strip()
        if not text:
            raise UsageError("No epic given.", hint="Pass --epic EPIC_KEY, or --epic none to take the issues out of their epic.")
        target = None if text.lower() == "none" else self._gateway.get_epic(parse_issue_key(text))
        issues, renamed = current_issues(wanted, read_issues(self._gateway, wanted, if_unchanged_since))
        _refuse_epics(list(issues.values()), target)
        keyed = bool(self._gateway.field_ids().get("epic_link"))  # without the field, no issue shows its epic
        label = target.key if target else "none"
        already = [key for key, issue in issues.items() if keyed and issue.epic_key == (target.key if target else "")]
        to_send = [key for key in issues if key not in already]
        leaving = [issues[key] for key in to_send if issues[key].epic_key and issues[key].epic_key != label]
        notes = [*renamed, *(f"{issue.key} leaves epic {issue.epic_key}." for issue in leaving)]
        if target is not None and target.done:
            notes.append(f"Epic {target.key} is done.")
        if not to_send:
            notes.append("Nothing to send: every issue is already there.")
        name = redact_text(target.name or target.summary) if target else ""
        result = MoveResult(label, name, to_send, already, rules.assign_payload(to_send), len(rules.batches(to_send)), dry_run, notes=notes)
        if dry_run or not to_send:
            return result
        with _needs("Changing the epic of issues", "Edit Issues"):
            _send_in_batches(lambda body: self._gateway.move_issues_to_epic(label, body), to_send)
        _confirm(result, lambda: self._gateway.list_epic_issues(label, keys=to_send))
        return result


def _refuse_epics(issues: Sequence[Issue], target: Epic | None) -> None:
    """An epic cannot be put into an epic, and no issue into itself."""
    for issue in issues:
        if issue.issue_type.casefold() == "epic" or (target is not None and issue.key == target.key):
            raise ValidationError(
                f"{issue.key} is an epic or the epic itself: an epic cannot be added to an epic.", hint="Give the keys of stories, tasks or bugs."
            )


@dataclass
class SprintWrite:
    """A sprint that was created or changed, or with a dry run would be."""

    payload: dict[str, Any]
    dry_run: bool
    sprint: SprintInfo | None = None  # as Jira holds it after the write (None only for a dry run); see `read_back`
    before: SprintInfo | None = None  # as it was; None for a new sprint
    notes: list[str] = field(default_factory=list)
    read_back: bool = False  # False when `sprint` is what the write itself answered, because the sprint could not be read afterwards


def _written(
    payload: dict[str, Any], answer: SprintInfo, read: Callable[[], SprintInfo], before: SprintInfo | None, notes: list[str], what: str
) -> SprintWrite:
    """The result of a sprint that was created or changed, as Jira holds it now. The write is done, so a read-back that fails (a server
    error, the network) is no error of the command: the answer of the write itself stands in for it and a note says so."""
    try:
        return SprintWrite(payload, False, _safe_sprint(read()), before, notes, read_back=True)
    except JiraCliError as error:
        sprint_id = before.id if before else answer.id
        notes = [
            *notes,
            f"The sprint was {what} (id {sprint_id}), but it could not be read back: {error.message.rstrip('.')}. "
            f"Do not send the command again; look at the sprint with `jira sprint {sprint_id}`.",
        ]
        return SprintWrite(payload, False, _safe_sprint(answer), before, notes)


class CreateSprint:
    def __init__(self, gateway: JiraGateway, resolver: Resolver, clock: Clock) -> None:
        self._gateway = gateway
        self._resolver = resolver
        self._clock = clock

    def execute(self, name: str, board: int, *, goal: str = "", start: str = "", end: str = "", dry_run: bool = False) -> SprintWrite:
        if not name.strip():
            raise UsageError("No sprint name given.", hint="Give the name, for example `jira sprint-create Sprint 14 --board 1200`.")
        found = self._resolver.board(board, "", sprints=True)
        now = self._clock.now()
        payload = rules.sprint_create_payload(
            name, found.id, goal=goal, start=rules.optional_moment(start, now), end=rules.optional_moment(end, now)
        )
        open_sprints = self._gateway.list_sprints(found.id, states=["future", "active"])
        if any(rules.same_name(sprint.name, payload["name"]) for sprint in open_sprints):
            raise ValidationError(
                f"Board {rules.board_line(found)} already has a future or active sprint named '{payload['name']}'.",
                hint="A second one would be a duplicate. Pick another name, or change that sprint with `jira sprint-update`.",
            )
        if dry_run:
            return SprintWrite(payload, True)
        with _needs("Creating a sprint", "Manage Sprints"):
            created = self._gateway.create_sprint(payload)
        return _written(payload, created, lambda: self._gateway.get_sprint(created.id), None, [], "created")


class UpdateSprint:
    def __init__(self, gateway: JiraGateway, resolver: Resolver, clock: Clock) -> None:
        self._gateway = gateway
        self._resolver = resolver
        self._clock = clock

    def execute(
        self,
        sprint: str,
        *,
        board: int | None = None,
        project: str = "",
        name: str | None = None,
        goal: str | None = None,
        start: str = "",
        end: str = "",
        state: str = "",
        if_state: str = "",
        dry_run: bool = False,
    ) -> SprintWrite:
        target = self._resolver.sprint(sprint, board, project)
        rules.check_state(target, rules.parse_states(if_state))
        now = self._clock.now()
        payload = rules.sprint_update_payload(
            target,
            name=name,
            goal=goal,
            start=rules.optional_moment(start, now),
            end=rules.optional_moment(end, now),
            state=state.strip().lower(),
        )
        notes = []
        if payload.get("state") == "closed":
            notes.append("This command does not move the open issues of the sprint: decide where they go (`jira sprint-add`).")
        if dry_run:
            return SprintWrite(payload, True, None, _safe_sprint(target), notes)
        with _needs("Changing a sprint", "Manage Sprints"):
            answer = self._gateway.update_sprint(target.id, payload)
        return _written(payload, answer, lambda: self._gateway.get_sprint(target.id), _safe_sprint(target), notes, "changed")


@dataclass
class AgileUseCases:
    """One field per use case, reached as `app.agile.<use_case>.execute(...)`."""

    list_boards: ListBoards
    list_sprints: ListSprints
    show_sprint: ShowSprint
    velocity: Velocity
    show_backlog: ShowBacklog
    list_epics: ListEpics
    add_to_sprint: AddToSprint
    rank_issues: RankIssues
    add_to_epic: AddToEpic
    create_sprint: CreateSprint
    update_sprint: UpdateSprint


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> AgileUseCases:
    resolver = Resolver(gateway, settings)
    reviewer = Reviewer(gateway, clock)
    return AgileUseCases(
        list_boards=ListBoards(gateway),
        list_sprints=ListSprints(gateway, resolver),
        show_sprint=ShowSprint(gateway, resolver, reviewer),
        velocity=Velocity(gateway, resolver, reviewer),
        show_backlog=ShowBacklog(gateway, resolver),
        list_epics=ListEpics(gateway, resolver),
        add_to_sprint=AddToSprint(gateway, resolver),
        rank_issues=RankIssues(gateway),
        add_to_epic=AddToEpic(gateway),
        create_sprint=CreateSprint(gateway, resolver, clock),
        update_sprint=UpdateSprint(gateway, resolver, clock),
    )
