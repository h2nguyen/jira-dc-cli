"""Use cases of collab, reached as `app.collab`: what people do together on an issue (watching, voting, work logged, web links)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from ..domain.collab import RemoteLink, Votes, Watchers, Worklog, user_name, watcher_payload, watcher_query
from ..domain.errors import AuthError, JiraCliError, NotFoundError, RejectedError, UsageError, ValidationError
from ..domain.model import parse_issue_key
from ..domain.privacy import redact_text
from ..domain.remotelinks import remote_link_payload
from ..domain.timeutil import ensure_unchanged_since, parse_timestamp
from ..domain.users import ME, keyword
from ..domain.worktime import parse_since, worklog_request
from .people_policy import ensure_people_allowed
from .ports import Clock, Diagnostics, Files, JiraGateway
from .settings import Settings


def _with_hint(error: JiraCliError, hint: str) -> JiraCliError:
    """The same error (its kind, so its exit code, its message and its details) with a hint that fits what was being done."""
    return type(error)(error.message, hint=hint, details=error.details)


def _reason(error: JiraCliError) -> str:
    """Jira's own words in an error that the adapter made of an answer: what follows `... - ` in its message."""
    return error.message.partition(" - ")[2] or error.message


def _said(error: JiraCliError) -> str:
    """What the error said, without its closing full stop, to put it in the middle of a sentence."""
    return error.message.rstrip(". ") or type(error).__name__


def _moment(text: str) -> datetime:
    try:
        return parse_timestamp(text)
    except ValidationError:
        return datetime.min.replace(tzinfo=timezone.utc)  # an entry whose time cannot be read sorts as the oldest one


_UNKNOWN_USER_HINT = "Jira knows no user of that name, or you may not see the issue. Give the login name, as with --assignee."
_REMOVE_HINT = "Remove another person in Jira itself (Manage Watchers): this CLI only takes your own watch off."
YOU = "[you]"  # what a preview says in place of the token's own user name when the policy keeps people out of output
_VOTE_HINT = (
    "Jira refuses a vote for an issue you reported, when you may not browse the project, and when voting is switched off (`jira server`)."
)
_WORKLOG_HINT = (
    "Jira's message above says what it did not accept. The time is sent as written: check that time tracking is on (`jira server`), "
    "that --started is a date and time, and that --adjust-estimate has what it needs."
)
_LINK_HINT = "Jira's message above says what it did not accept: the address must be a full http or https address, and the title must not be empty."


# -- watchers ----------------------------------------------------------------------------------------


class ListWatchers:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, key: str, *, with_users: bool = False) -> Watchers:
        """The number of watchers and whether the token's user is one; who they are only with `with_users`."""
        key = parse_issue_key(key)
        if with_users:
            ensure_people_allowed(self._settings)
        found = self._gateway.get_watchers(key)
        return found if with_users else replace(found, users=None)


@dataclass(frozen=True)
class WatchResult:
    key: str
    action: str  # added | removed
    user: str | None  # the name given with --user; None for the token's user
    payload: str | None  # what adding sends: a JSON string with the user name; None for removing, and for a call that was made under the policy
    query: dict[str, str] | None  # what removing sends (a DELETE has no body); empty for adding; None under the policy, as payload
    dry_run: bool
    watch_count: int | None  # watchers of the issue: before the call on a dry run, after it on a real one; None if the list was not readable
    watching: bool | None  # whether the token's user watches the issue, at the same moment
    # read back: Jira's list shows what was asked; None on a dry run, when the list was not readable, or when Jira lists nobody for another user
    confirmed: bool | None
    # the policy keeps the token's own name out: a preview says `[you]` for it, and a call that was made does not say what it sent
    redacted: bool = False
    message: str = ""  # what the person must know when the call was made but is not confirmed; empty otherwise


def _confirmed(after: Watchers, target: str, own: bool, remove: bool) -> bool | None:
    if own:
        return after.watching != remove
    if after.users is None:
        return None
    listed = any(user.name.casefold() == target.casefold() for user in after.users)
    return listed != remove


def _typed_user(user: str) -> str:
    """The login name that the caller typed with --user; empty for no name, and for `me`, which is the caller."""
    if not user.strip() or keyword(user) == ME:
        return ""
    return user_name(user)


class WatchIssue:
    def __init__(self, gateway: JiraGateway, settings: Settings, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._diagnostics = diagnostics

    def execute(self, key: str, *, user: str = "", remove: bool = False, dry_run: bool = False) -> WatchResult:
        """Adds the token's user (or `user`) to the watchers of the issue, or with `remove` takes the token's user off again.

        Removing is the inverse of what the same person did for themselves: it is refused for any other person, before anything is sent.
        Under JIRA_HIDE_USERS=true the token's own name shows as `[you]` in a preview, and a call that was made does not say what it sent.
        When the call was made but the list cannot be read afterwards, the result says so (`confirmed` is None) and still succeeds."""
        key = parse_issue_key(key)
        typed = _typed_user(user)  # checked before anything is asked
        me = self._gateway.current_user().name
        if not typed and not me:
            raise ValidationError("Jira did not say who you are.", hint="Pass --user NAME.")
        own = not typed or typed.casefold() == me.casefold()
        if remove and not own:
            raise UsageError(
                f"--remove takes only your own watch off {key}, and '{typed}' is not you: nothing was sent.", hint=_REMOVE_HINT
            )
        target = me if own else typed  # for oneself the login as Jira spells it
        hidden = self._settings.hide_users and not typed
        shown = YOU if hidden else target
        action = "removed" if remove else "added"
        who = typed or None
        if dry_run:
            current = self._gateway.get_watchers(key)  # proves that the issue exists and that watching is on
            payload = None if remove else watcher_payload(shown)
            query = watcher_query(shown) if remove else {}
            return WatchResult(key, action, who, payload, query, True, current.count, current.watching, None, hidden)
        try:
            if remove:
                self._gateway.remove_watcher(key, watcher_query(target))
            else:
                self._gateway.add_watcher(key, watcher_payload(target))
        except AuthError as error:
            raise _with_hint(error, self._refused(key, own)) from error
        except NotFoundError as error:
            if own:
                raise
            raise _with_hint(error, _UNKNOWN_USER_HINT) from error
        # From here on the change is made: whatever goes wrong now must not read as a failure, or somebody repeats it.
        payload = None if hidden or remove else watcher_payload(target)
        query = None if hidden else (watcher_query(target) if remove else {})
        try:
            after = self._gateway.get_watchers(key)
        except JiraCliError as error:
            message = (
                f"{key}: the watcher was {'taken off' if remove else 'added'}, but reading the watcher list afterwards failed: "
                f"{_said(error)}. Check it with `jira watchers {key}`."
            )
            self._diagnostics.warn(message)
            return WatchResult(key, action, who, payload, query, False, None, None, None, hidden, message)
        confirmed = _confirmed(after, target, own, remove)
        message = ""
        if confirmed is False:
            message = f"{key}: Jira accepted the change, but its watcher list does not show it yet. Check it with `jira watchers {key}`."
            self._diagnostics.warn(message)
        return WatchResult(key, action, who, payload, query, False, after.count, after.watching, confirmed, hidden, message)

    @staticmethod
    def _refused(key: str, own: bool) -> str:
        missing = "Jira answers HTTP 401 or 403 for a missing permission."
        if own:
            return f"Check that you may see {key} and that watching is switched on (`jira server`). {missing}"
        return (
            "Changing what another person watches needs the permission Manage Watchers (MANAGE_WATCHERS): check it with "
            f"`jira permissions --issue {key} --only MANAGE_WATCHERS`. {missing}"
        )


# -- votes -------------------------------------------------------------------------------------------


class ShowVotes:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, key: str) -> Votes:
        """The number of votes and whether the token's user voted. Who voted is not asked for."""
        return self._gateway.get_votes(parse_issue_key(key))


@dataclass(frozen=True)
class VoteResult:
    key: str
    action: str  # added | removed
    dry_run: bool
    changed: bool  # False: the vote was as asked already, so nothing is sent; on a dry run, whether a call would be made
    count: int | None  # None when the votes could not be read after the call
    has_voted: bool | None  # of the token's user: before the call on a dry run, after it on a real one
    message: str = ""  # what the person must know when the call was made but could not be read back; empty otherwise


class VoteIssue:
    def __init__(self, gateway: JiraGateway, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._diagnostics = diagnostics

    def execute(self, key: str, *, remove: bool = False, dry_run: bool = False) -> VoteResult:
        """Votes for the issue as the token's user, or with `remove` withdraws that vote. Nobody else's vote can be touched.

        When the call was made but the votes cannot be read afterwards, the result says so (no count) and still succeeds."""
        key = parse_issue_key(key)
        action = "removed" if remove else "added"
        before = self._gateway.get_votes(key)  # proves that the issue exists and that voting is on
        if before.has_voted == (not remove):
            return VoteResult(key, action, dry_run, False, before.count, before.has_voted)
        if dry_run:
            return VoteResult(key, action, True, True, before.count, before.has_voted)
        try:
            if remove:
                self._gateway.remove_vote(key)
            else:
                self._gateway.add_vote(key)
        except NotFoundError as error:  # Jira answers 404 for every reason why it will not take or withdraw a vote
            what = "withdraw your vote for" if remove else "take a vote for"
            raise RejectedError(
                f"Jira will not {what} {key}: {_reason(error)}", hint=_VOTE_HINT, details=error.details
            ) from error
        try:
            after = self._gateway.get_votes(key)
        except JiraCliError as error:  # the vote is made: say so, or somebody repeats it
            message = (
                f"{key}: your vote was {'withdrawn' if remove else 'added'}, but reading the votes afterwards failed: "
                f"{_said(error)}. Check it with `jira votes {key}`."
            )
            self._diagnostics.warn(message)
            return VoteResult(key, action, False, True, None, None, message)
        return VoteResult(key, action, False, True, after.count, after.has_voted)


# -- worklogs ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WorklogList:
    key: str
    total: int  # the entries that the issue holds
    worklogs: list[Worklog]  # the ones shown, newest first
    seconds: int  # the time of the ones shown


class ListWorklogs:
    def __init__(self, gateway: JiraGateway, settings: Settings, clock: Clock, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._clock = clock
        self._diagnostics = diagnostics

    def execute(self, key: str, *, since: str = "", limit: int = 0, with_users: bool = False) -> WorklogList:
        """The work logged on an issue, newest first, from `since` on, at most `limit` entries (0: all). Authors only with `with_users`."""
        key = parse_issue_key(key)
        if with_users:
            ensure_people_allowed(self._settings)
        if limit < 0:
            raise UsageError("--limit must be 0 or more.")
        earliest = parse_since(since, self._clock.now()) if since.strip() else None
        page = self._gateway.list_worklogs(key)
        if page.total > len(page.worklogs):
            self._diagnostics.warn(f"Jira returned {len(page.worklogs)} of the {page.total} worklogs of {key}.")
        found = [entry for entry in page.worklogs if earliest is None or _moment(entry.started) >= earliest]
        found.sort(key=lambda entry: _moment(entry.started), reverse=True)
        if limit:
            found = found[:limit]
        shown = [replace(entry, comment=redact_text(entry.comment), author=entry.author if with_users else None) for entry in found]
        return WorklogList(key, page.total, shown, sum(entry.time_spent_seconds for entry in shown))


@dataclass(frozen=True)
class LogWorkResult:
    key: str
    payload: dict[str, Any]  # the body a real run sends
    query: dict[str, str]  # and its query: how the remaining estimate is adjusted
    seconds: int | None  # the length, when the time was given in hours and minutes only (Jira converts days and weeks itself)
    dry_run: bool
    worklog: Worklog | None = None  # what Jira stored, read back; what Jira answered to the write when the list could not be read
    confirmed: bool | None = None  # whether the read-back list holds the new entry; None on a dry run or when the list could not be read
    message: str = ""  # what the person must know when the work was logged but is not confirmed; empty otherwise


def _clean_worklog(entry: Worklog) -> Worklog:
    return replace(entry, comment=redact_text(entry.comment), author=None)


class LogWork:
    def __init__(self, gateway: JiraGateway, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._diagnostics = diagnostics

    def execute(
        self,
        key: str,
        time_spent: str,
        *,
        comment: str = "",
        started: str = "",
        adjust: str = "",
        new_estimate: str = "",
        reduce_by: str = "",
        dry_run: bool = False,
        if_unchanged_since: str = "",
    ) -> LogWorkResult:
        """Logs `time_spent` on the issue as the token's user. The time goes to Jira in its own words; Jira converts days and weeks.

        When the work was logged but the worklogs cannot be read afterwards, the result says so (`confirmed` is None) and still
        succeeds: a worklog cannot be deleted with this CLI, so a failure here would invite a repeat that logs the work twice."""
        key = parse_issue_key(key)
        request = worklog_request(
            time_spent, comment=comment, started=started, adjust=adjust, new_estimate=new_estimate, reduce_by=reduce_by
        )
        if dry_run or if_unchanged_since:
            current = self._gateway.get_issue(key, fields=["summary", "updated"])  # exists, and is unchanged
            if if_unchanged_since:
                ensure_unchanged_since(key, current.updated, if_unchanged_since)
        if dry_run:
            return LogWorkResult(key, request.body, request.query, request.seconds, True)
        try:
            created = self._gateway.add_worklog(key, request)
        except AuthError as error:
            raise _with_hint(
                error,
                "Logging work needs the permission Work On Issues (WORK_ON_ISSUES) and time tracking switched on: check them with "
                f"`jira permissions --issue {key} --only WORK_ON_ISSUES` and `jira server`.",
            ) from error
        except RejectedError as error:
            raise _with_hint(error, _WORKLOG_HINT) from error
        label = f" (worklog {created.id})" if created.id else ""
        try:
            listed = self._gateway.list_worklogs(key).worklogs
        except JiraCliError as error:  # the work is logged: say so, or somebody repeats it
            message = (
                f"The work WAS logged on {key}{label}, but reading the worklogs afterwards failed: {_said(error)}. "
                f"Do not run the command again: that would log the work twice. Look at `jira worklogs {key}`."
            )
            self._diagnostics.warn(message)
            return LogWorkResult(key, request.body, request.query, request.seconds, False, _clean_worklog(created), None, message)
        stored = next((entry for entry in listed if created.id and entry.id == created.id), None)
        message = ""
        if stored is None:
            message = (
                f"Jira answered that it logged the work on {key}{label}, but its list of worklogs does not hold the entry. "
                f"Do not run the command again: that would log the work twice. Look at `jira worklogs {key}`."
            )
            self._diagnostics.warn(message)
        shown = _clean_worklog(stored or created)
        return LogWorkResult(key, request.body, request.query, request.seconds, False, shown, stored is not None, message)


# -- web links ---------------------------------------------------------------------------------------


def _clean_link(link: RemoteLink) -> RemoteLink:
    return replace(
        link,
        title=redact_text(link.title),
        url=redact_text(link.url),
        relationship=redact_text(link.relationship),
        application=redact_text(link.application),
    )


def _find_link(links: list[RemoteLink], link_id: str, global_id: str) -> RemoteLink | None:
    """The link that Jira answered with, by its id, or else the one that has the global id that was sent."""
    for link in links:
        if (link_id and link.id == link_id) or (global_id and link.global_id == global_id):
            return link
    return None


class ListRemoteLinks:
    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway

    def execute(self, key: str) -> list[RemoteLink]:
        """The web links of an issue (not the links to other issues)."""
        return [_clean_link(link) for link in self._gateway.list_remote_links(parse_issue_key(key))]


@dataclass(frozen=True)
class RemoteLinkResult:
    key: str
    action: str  # created | updated (a link with the same global id exists)
    payload: dict[str, Any]
    dry_run: bool
    link: RemoteLink | None = None  # what Jira stored, read back; None on a dry run and when the list could not be read
    confirmed: bool | None = None  # None on a dry run or when the list could not be read
    message: str = ""  # what the person must know when the link was saved but is not confirmed; empty otherwise


class SaveRemoteLink:
    def __init__(self, gateway: JiraGateway, diagnostics: Diagnostics) -> None:
        self._gateway = gateway
        self._diagnostics = diagnostics

    def execute(
        self,
        key: str,
        url: str,
        title: str,
        *,
        relationship: str = "",
        global_id: str = "",
        dry_run: bool = False,
        if_unchanged_since: str = "",
    ) -> RemoteLinkResult:
        """Adds a web link to the issue; with a `global_id` that the issue holds already, updates that link instead."""
        key = parse_issue_key(key)
        payload = remote_link_payload(url, title, relationship=relationship, global_id=global_id)
        if dry_run or if_unchanged_since:
            current = self._gateway.get_issue(key, fields=["summary", "updated"])  # exists, and is unchanged
            if if_unchanged_since:
                ensure_unchanged_since(key, current.updated, if_unchanged_since)
        existing = self._gateway.list_remote_links(key)  # also tells a new link from the update of one
        wanted_id = payload.get("globalId", "")
        action = "updated" if wanted_id and any(link.global_id == wanted_id for link in existing) else "created"
        same_address = [link for link in existing if link.url == payload["object"]["url"] and link.global_id != wanted_id]
        if same_address and action == "created":
            self._diagnostics.warn(
                f"{key} has a web link to this address already (id {same_address[0].id}). A second one is added: "
                "pass --global-id to update one link instead."
            )
        if dry_run:
            return RemoteLinkResult(key, action, payload, True)
        try:
            ref = self._gateway.save_remote_link(key, payload)
        except AuthError as error:
            raise _with_hint(
                error,
                f"A web link needs the permission Link Issues (LINK_ISSUES) and issue linking switched on: check them with "
                f"`jira permissions --issue {key} --only LINK_ISSUES` and `jira server`.",
            ) from error
        except RejectedError as error:
            raise _with_hint(error, _LINK_HINT) from error
        # From here on the link is saved: whatever goes wrong now must not read as a failure, or somebody repeats it.
        label = f" (id {ref.id})" if ref.id else ""
        done = "updated" if action == "updated" else "added"
        again = (
            "Running the command again with the same --global-id only updates that link."
            if wanted_id
            else "Do not run the command again: that would add a second link."
        )
        try:
            listed = self._gateway.list_remote_links(key)
        except JiraCliError as error:
            message = (
                f"The web link WAS {done} on {key}{label}, but reading the links afterwards failed: {_said(error)}. "
                f"{again} Look at `jira remote-links {key}`."
            )
            self._diagnostics.warn(message)
            return RemoteLinkResult(key, action, payload, False, None, None, message)
        stored = _find_link(listed, ref.id, wanted_id)
        message = ""
        if stored is None:
            message = (
                f"Jira answered that it {done} the web link on {key}{label}, but its list of web links does not hold it. "
                f"{again} Look at `jira remote-links {key}`."
            )
            self._diagnostics.warn(message)
        return RemoteLinkResult(key, action, payload, False, _clean_link(stored) if stored else None, stored is not None, message)


@dataclass
class CollabUseCases:
    """One field per use case; built in `build`."""

    watchers: ListWatchers
    watch: WatchIssue
    votes: ShowVotes
    vote: VoteIssue
    worklogs: ListWorklogs
    log_work: LogWork
    remote_links: ListRemoteLinks
    remote_link: SaveRemoteLink


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> CollabUseCases:
    return CollabUseCases(
        watchers=ListWatchers(gateway, settings),
        watch=WatchIssue(gateway, settings, diagnostics),
        votes=ShowVotes(gateway),
        vote=VoteIssue(gateway, diagnostics),
        worklogs=ListWorklogs(gateway, settings, clock, diagnostics),
        log_work=LogWork(gateway, diagnostics),
        remote_links=ListRemoteLinks(gateway),
        remote_link=SaveRemoteLink(gateway, diagnostics),
    )
