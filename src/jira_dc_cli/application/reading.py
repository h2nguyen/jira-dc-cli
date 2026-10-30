"""Use cases of reading, reached as `app.reading`: issues read in batches and in parts, and saved filters."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

from ..domain.errors import NotFoundError, RejectedError, UsageError
from ..domain.history import Changelog, StatusChange, status_changes
from ..domain.issueparts import PARTS, ExpandedIssue, parse_parts
from ..domain.model import Issue, parse_issue_key
from ..domain.outline import Cut, Heading, find_section, truncate
from ..domain.outline import outline as outline_of
from ..domain.rendered import rendered_text
from ..domain.savedfilters import SavedFilter, parse_filter_id, without_people
from .issues import IssueView, ViewIssue, check_users
from .ports import Clock, Diagnostics, Files, JiraGateway
from .settings import Settings


@dataclass(frozen=True)
class ReadOptions:
    """What a reader asks for besides the keys: the options of `jira issue`."""

    with_comments: bool = False
    with_users: bool = False
    want_raw: bool = False
    only: str = ""  # the parts to show, comma separated (see `domain.issueparts.PARTS`); empty: the whole view
    max_chars: int | None = None  # cut each long text at the end of a paragraph after about this many characters
    outline: bool = False  # the headings of the description instead of its text
    section: str = ""  # the part of the description under this heading
    rendered: bool = False  # the description and the acceptance criteria as Jira renders them
    history: bool = False  # the status changes


@dataclass
class IssueReading:
    """One issue as it was asked for: the issue (people and comments as the policy and the options say, long texts cut), and what
    the options add to it."""

    view: IssueView
    parts: tuple[str, ...] = ()  # the parts that are shown; empty: the whole view
    cuts: dict[str, Cut] = field(default_factory=dict)  # by text (`description`, `acceptance`, `comments.ID`, ...); only with `max_chars`
    rendered_description: str | None = None  # None: `rendered` was not asked for
    rendered_acceptance: str | None = None
    outline: list[Heading] | None = None
    section: Heading | None = None
    history: list[StatusChange] | None = None
    history_complete: bool = True  # False: Jira holds more history than it sent

    @property
    def issue(self) -> Issue:
        return self.view.issue

    def shows(self, part: str) -> bool:
        return not self.parts or part in self.parts


@dataclass(frozen=True)
class MissingIssue:
    key: str
    code: str
    message: str


@dataclass
class ReadResult:
    entries: list[IssueReading | MissingIssue]  # in the order of the keys, each key once
    many: bool = False  # several keys were given, the same one twice included: the answer is a list, whatever was found

    @property
    def issues(self) -> list[IssueReading]:
        return [item for item in self.entries if isinstance(item, IssueReading)]

    @property
    def errors(self) -> list[MissingIssue]:
        return [item for item in self.entries if isinstance(item, MissingIssue)]


class ReadIssues:
    """Issues by key: one, or several with one request, and long ones in parts (only some of their parts, cut at the end of a
    paragraph, the headings of the description or one section of it), with the rendered text and the status changes if asked."""

    def __init__(self, gateway: JiraGateway, settings: Settings, view: ViewIssue) -> None:
        self._gateway = gateway
        self._settings = settings
        self._view = view

    def execute(self, keys: Sequence[str], options: ReadOptions = ReadOptions()) -> ReadResult:
        given = [parse_issue_key(key) for key in keys]
        wanted = list(dict.fromkeys(given))
        if not wanted:
            raise UsageError("No issue key given.", hint="Give at least one issue key, such as PROJ-86.")
        parts = self._check(len(given), options)
        comments = options.with_comments or "comments" in parts
        expand = [name for name, asked in (("renderedFields", options.rendered), ("changelog", options.history)) if asked]
        if len(wanted) == 1:
            try:
                first = self._gateway.get_issue_with(wanted[0], expand) if expand else ExpandedIssue(self._gateway.get_issue(wanted[0]))
            except NotFoundError as error:
                if len(given) == 1:  # one key, given once: an error, as it always was
                    raise
                first = MissingIssue(wanted[0], error.code, error.message)  # the same key twice is a list, whose entry says what is wrong
            found: list[ExpandedIssue | MissingIssue] = [first]
        else:
            found = self._read_many(wanted, expand)
        return ReadResult(
            [item if isinstance(item, MissingIssue) else self._reading(item, options, parts, comments) for item in found],
            many=len(given) > 1,
        )

    def _check(self, given: int, options: ReadOptions) -> tuple[str, ...]:
        """Refuse a combination of options that makes no sense, before anything is asked of Jira. `given` is the number of keys that
        were given, the same key twice counted twice: what the answer looks like must not depend on which keys are alike. Returns the
        parts to show."""
        check_users(self._settings, options.with_users)
        if options.max_chars is not None and options.max_chars < 1:
            raise UsageError("--max-chars must be at least 1.")
        if options.want_raw:
            if given > 1:
                raise UsageError("--raw prints Jira's own JSON of one issue.", hint="Give one key, or leave out --raw.")
            if options.only or options.max_chars is not None or options.outline or options.section or options.rendered or options.history:
                raise UsageError(
                    "--raw prints Jira's own JSON, which has no parts.",
                    hint="Leave out --only, --max-chars, --outline, --section, --rendered and --history.",
                )
        if (options.outline or options.section) and given > 1:
            raise UsageError("--outline and --section read the description of one issue.", hint="Give one key.")
        if options.outline and options.section:
            raise UsageError("--outline lists the headings and shows no text.", hint="Read a part with --section, without --outline.")
        parts = parse_parts(options.only) if options.only else ()
        if parts:
            if (options.outline or options.section) and "description" not in parts:
                raise UsageError("--outline and --section read the description.", hint="Add description to --only.")
            if options.rendered and not {"description", "acceptance"} & set(parts):
                raise UsageError("--rendered shows the description and the acceptance criteria.", hint="Add description or acceptance to --only.")
            if options.with_comments:
                return tuple(part for part in PARTS if part in {*parts, "comments"})
        return parts

    def _read_many(self, keys: list[str], expand: list[str]) -> list[ExpandedIssue | MissingIssue]:
        """The issues in the order of the keys. One search asks for all of them; Jira answers HTTP 400 for the whole query when
        one key does not exist, so then (and for a key that the search left out) each key is asked for on its own."""
        found: dict[str, ExpandedIssue] = {}
        try:
            found = {item.issue.key.upper(): item for item in self._gateway.get_issues_by_key(keys, expand)}
        except RejectedError as error:
            if error.details.get("status") != 400:
                raise
        entries: list[ExpandedIssue | MissingIssue] = []
        for key in keys:
            if key in found:
                entries.append(found[key])
                continue
            try:
                entries.append(self._gateway.get_issue_with(key, expand))
            except NotFoundError as error:
                entries.append(MissingIssue(key, error.code, error.message))
        return entries

    def _reading(self, expanded: ExpandedIssue, options: ReadOptions, parts: tuple[str, ...], comments: bool) -> IssueReading:
        view = self._view.present(expanded.issue, with_comments=comments, with_users=options.with_users, want_raw=options.want_raw)
        issue = view.issue

        def shows(part: str) -> bool:
            return not parts or part in parts

        description = issue.description
        headings: list[Heading] | None = None
        section: Heading | None = None
        if options.outline:
            headings, description = outline_of(description), ""
        elif options.section:
            description, section = find_section(description, options.section)
        texts: dict[str, str] = {}
        if shows("description") and not options.outline:
            texts["description"] = description
        if shows("acceptance"):
            texts["acceptance"] = issue.acceptance
        if options.rendered:
            if shows("description"):
                texts["renderedDescription"] = rendered_text(expanded.rendered_description, people_shown=options.with_users)
            if shows("acceptance"):
                texts["renderedAcceptance"] = rendered_text(expanded.rendered_acceptance, people_shown=options.with_users)
        texts.update({f"comments.{comment.id}": comment.body for comment in issue.comments})
        cuts = {name: truncate(text, options.max_chars) for name, text in texts.items()} if options.max_chars else {}
        shown = {name: cuts[name].text if name in cuts else text for name, text in texts.items()}
        issue = replace(
            issue,
            description=shown.get("description", description),
            acceptance=shown.get("acceptance", issue.acceptance),
            comments=[replace(comment, body=shown[f"comments.{comment.id}"]) for comment in issue.comments],
        )
        changes: list[StatusChange] | None = None
        complete = True
        if options.history:
            changelog = expanded.changelog or Changelog()
            changes, complete = status_changes(changelog), changelog.complete
        return IssueReading(
            replace(view, issue=issue),
            parts,
            cuts,
            shown.get("renderedDescription"),
            shown.get("renderedAcceptance"),
            headings,
            section,
            changes,
            complete,
        )


class ListFilters:
    """The saved filters of the user that are marked as favourite: Data Center has no call that lists all of them. The owner and the people
    that a JQL names are shown only when asked for (`with_users`, which the privacy policy refuses)."""

    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, *, with_users: bool = False) -> list[SavedFilter]:
        check_users(self._settings, with_users)
        return [item if with_users else without_people(item) for item in self._gateway.list_filters()]


class ShowFilter:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, filter_id: str, *, with_users: bool = False) -> SavedFilter:
        filter_id = parse_filter_id(filter_id)
        check_users(self._settings, with_users)
        try:
            found = self._gateway.get_filter(filter_id)
        except NotFoundError as error:
            raise NotFoundError(
                error.message,
                hint="`jira filters` lists your favourite filters. A filter that you may not see is reported as not found.",
                details=error.details,
            ) from error
        return found if with_users else without_people(found)


@dataclass
class ReadingUseCases:
    """One field per use case; build each in `build`."""

    read_issues: ReadIssues
    list_filters: ListFilters
    show_filter: ShowFilter


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> ReadingUseCases:
    return ReadingUseCases(
        read_issues=ReadIssues(gateway, settings, ViewIssue(gateway, settings)),
        list_filters=ListFilters(gateway, settings),
        show_filter=ShowFilter(gateway, settings),
    )
