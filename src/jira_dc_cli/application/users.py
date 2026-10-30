"""Use cases of users, reached as `app.users`: find the user name that `--assignee` needs, list people, assign an issue."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..domain.errors import AuthError, NotFoundError, UsageError, ValidationError
from ..domain.model import parse_issue_key, parse_project_key
from ..domain.timeutil import ensure_unchanged_since
from ..domain.users import AUTOMATIC, AUTOMATIC_NAME, ME, UNASSIGNED, Person, Resolution, keyword, resolve_person
from .ports import Clock, Diagnostics, Files, JiraGateway
from .settings import Settings

MOST_CHARACTERS = 256  # a user name, a display name or an address is never longer: more would only make a request that Jira refuses


def _refuse_people(settings: Settings, what: str, hint: str) -> None:
    if settings.hide_users:
        raise ValidationError(f"JIRA_HIDE_USERS=true: people are not printed, {what} is not available.", hint=hint)


def _scope(project: str, issue: str) -> tuple[str, str]:
    """The project key and the issue key that narrow a search to the users who can be assigned there (at most one of them)."""
    if project.strip() and issue.strip():
        raise UsageError(
            "--project and --issue cannot be combined.", hint="Use --issue for an issue that exists, --project for one yet to be created."
        )
    return (parse_project_key(project) if project.strip() else ""), (parse_issue_key(issue) if issue.strip() else "")


class UserResolver:
    """Finds the user that a user name, a display name or an e-mail address stands for, and turns what a person typed after `--assignee`
    into the value of the assignee field. Jira is asked once for each query and scope of a run; a name that fits nobody, or several, is
    looked for once more before it is refused, because a user can be created while the program runs (`jira mcp` runs for hours)."""

    def __init__(self, gateway: JiraGateway) -> None:
        self._gateway = gateway
        self._asked: dict[tuple[str, str, str, bool], list[Person]] = {}

    def people(
        self, query: str, *, project_key: str = "", issue_key: str = "", include_inactive: bool = False, refresh: bool = False
    ) -> list[Person]:
        text = query.strip()
        if not text:
            raise ValidationError("A user is needed.", hint="Give a user name, a display name or an e-mail address, or `me`.")
        if len(text) > MOST_CHARACTERS:
            raise ValidationError(
                f"'{text[:20]}...' is too long for a user name, a display name or an address.",
                hint=f"At most {MOST_CHARACTERS} characters.",
            )
        asked = (text.casefold(), project_key, issue_key, include_inactive)
        if refresh or asked not in self._asked:
            found = self._gateway.find_users(text, project_key=project_key, issue_key=issue_key, include_inactive=include_inactive)
            self._asked[asked] = found
        return self._asked[asked]

    def resolve(self, query: str, *, project_key: str = "", issue_key: str = "", include_inactive: bool = False) -> Resolution:
        asked = (query.strip().casefold(), project_key, issue_key, include_inactive)
        remembered = asked in self._asked
        people = self.people(query, project_key=project_key, issue_key=issue_key, include_inactive=include_inactive)
        found = resolve_person(query, people)
        if found.person is None and remembered:  # the answer of an earlier call: Jira may know the user by now
            people = self.people(query, project_key=project_key, issue_key=issue_key, include_inactive=include_inactive, refresh=True)
            found = resolve_person(query, people)
        return found

    def me(self) -> Person:
        user = self._gateway.current_user()
        return Person(user.name, user.display_name, user.email)

    def assignee(self, text: str, *, project_key: str = "", issue_key: str = "") -> dict[str, Any]:
        """The value of the assignee field for `text`: `unassigned`, `automatic`, `me`, or the one assignable user that it names. An
        unknown user is refused, and so is a name that fits several users: with how many, and not with who they are."""
        if not text.strip():
            raise ValidationError(
                "The assignee is empty.",
                hint="Say `unassigned` to remove the assignee, `automatic` for the default assignee, `me`, or a user.",
            )
        word = keyword(text)
        if word == UNASSIGNED:
            return {"name": None}
        if word == AUTOMATIC:
            return {"name": AUTOMATIC_NAME}
        if word == ME:
            return {"name": self.me().name}
        found = self.resolve(text, project_key=project_key, issue_key=issue_key)
        if found.person is not None:
            return {"name": found.person.name}
        where = f"in {project_key}" if project_key else f"for {issue_key}" if issue_key else "in Jira"
        if found.matches == 0:
            raise NotFoundError(
                f"No user who can be assigned {where} matches '{text.strip()}'.",
                hint="Check the spelling. `jira users resolve NAME --issue KEY` tells whether a name fits; inactive users and users who "
                "may not be assigned issues there are not found.",
                details={"query": text.strip(), "matches": 0},
            )
        raise ValidationError(
            f"'{text.strip()}' matches {found.matches} users, so it does not say which one is meant.",
            hint="Give the exact user name. `jira users resolve NAME` says how many users a name fits, and prints no people.",
            details={"query": text.strip(), "matches": found.matches},
        )


@dataclass
class UserLookup:
    query: str
    matches: int
    person: Person | None = None  # the user, when exactly one fits; without display name and address unless people may be shown


def _query_text(query: str) -> str:
    text = query.strip()
    if not text:
        raise UsageError(
            "Nothing to look up.", hint="Give a user name, a display name or an e-mail address, or `me`: `jira users resolve jdoe`."
        )
    return text


class ResolveUser:
    def __init__(self, resolver: UserResolver, settings: Settings) -> None:
        self._resolver = resolver
        self._settings = settings

    def execute(self, query: str, *, project: str = "", issue: str = "", with_users: bool = False) -> UserLookup:
        text = _query_text(query)
        if with_users:
            _refuse_people(self._settings, "--with-users", "Unset JIRA_HIDE_USERS if you need to see people.")
        project_key, issue_key = _scope(project, issue)
        if keyword(text) == ME:
            found = Resolution(1, self._resolver.me())
        else:
            found = self._resolver.resolve(text, project_key=project_key, issue_key=issue_key, include_inactive=True)
        person = found.person
        if person is not None and not with_users:
            person = replace(person, display_name="", email="")
        return UserLookup(text, found.matches, person)


class SearchUsers:
    """Lists people. It is the one use case that does nothing without `with_users`, and nothing at all under the privacy policy."""

    def __init__(self, resolver: UserResolver, settings: Settings) -> None:
        self._resolver = resolver
        self._settings = settings

    def execute(self, query: str, *, project: str = "", issue: str = "", with_users: bool = False) -> list[Person]:
        _refuse_people(self._settings, "`jira users search`", "`jira users resolve NAME` finds a user name without printing people.")
        if not with_users:
            raise UsageError(
                "`jira users search` lists people.",
                hint="Add --with-users to show them; `jira users resolve NAME` prints no people (how many fit, and which user name).",
            )
        text = _query_text(query)
        project_key, issue_key = _scope(project, issue)
        people = self._resolver.people(text, project_key=project_key, issue_key=issue_key, include_inactive=True)
        return sorted(people, key=lambda person: person.name.casefold())


@dataclass
class AssignResult:
    key: str
    action: str  # "assigned", "unassigned" or "automatic" (the default assignee of the project)
    payload: dict[str, Any]
    dry_run: bool
    assignee: str | None = None  # the user name Jira holds after the write; in a dry run the one that would be sent (None: left to Jira)


def _denied(error: AuthError, key: str) -> AuthError:
    """Jira answers 401, not 403, when the token's user may not assign issues: say what it means."""
    status = error.details.get("status")
    return AuthError(
        f"Jira refused to assign {key} (HTTP {status or 403}): the token's user may not assign issues in this project, "
        "or the token is not accepted.",
        hint="Run `jira ping` to check the token. If it works, ask a Jira administrator for the 'Assign Issues' permission in the "
        "project, and check that the user can be assigned issues there.",
        details=error.details,
    )


class AssignIssue:
    def __init__(self, gateway: JiraGateway, resolver: UserResolver) -> None:
        self._gateway = gateway
        self._resolver = resolver

    def execute(self, key: str, assignee: str, *, dry_run: bool = False, if_unchanged_since: str = "") -> AssignResult:
        key = parse_issue_key(key)
        if not assignee.strip():
            raise ValidationError("An assignee is needed.", hint="Pass a user name, a display name, `me`, `unassigned` or `automatic`.")
        if dry_run or if_unchanged_since:
            current = self._gateway.get_issue(key, fields=["summary", "updated"])  # the issue exists, and is unchanged
            if if_unchanged_since:
                ensure_unchanged_since(key, current.updated, if_unchanged_since)
        payload = self._resolver.assignee(assignee, issue_key=key)
        name = payload["name"]
        action = "unassigned" if name is None else "automatic" if name == AUTOMATIC_NAME else "assigned"
        if dry_run:
            return AssignResult(key, action, payload, True, name if action == "assigned" else None)
        try:
            self._gateway.assign_issue(key, name)
        except AuthError as error:
            if error.details.get("status") not in (None, 401, 403):
                raise
            raise _denied(error, key) from error
        stored = self._gateway.get_issue(key, fields=["assignee"])  # read back: what Jira holds now
        return AssignResult(key, action, payload, False, stored.assignee.name if stored.assignee else None)


@dataclass
class UsersUseCases:
    """The use cases of `jira users` and `jira assign`, one field each. `UserResolver` is not one: `update` and `create` use it too."""

    resolve: ResolveUser
    search: SearchUsers
    assign: AssignIssue


def build(gateway: JiraGateway, settings: Settings, files: Files, clock: Clock, diagnostics: Diagnostics) -> UsersUseCases:
    resolver = UserResolver(gateway)
    return UsersUseCases(
        resolve=ResolveUser(resolver, settings), search=SearchUsers(resolver, settings), assign=AssignIssue(gateway, resolver)
    )
