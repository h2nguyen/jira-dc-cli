"""Use case: find issues by words, the way a person or an agent asks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from ..domain import jql as jql_builder
from ..domain.errors import JiraCliError, UsageError
from ..domain.fields import ACCEPTANCE, EPIC_LINK, SPRINT, STORY_POINTS
from ..domain.model import Issue, parse_issue_key, parse_project_key
from ..domain.privacy import hide_users, redact_text
from ..domain.searchterms import Term, parse_terms, terms_from_jql
from ..domain.snippets import find_passages, lead, matched_terms
from ..domain.unwiki import wiki_to_text
from .ports import JiraGateway
from .settings import Settings

PAGE_SIZE = 100
MAX_HITS = 2000
MAX_SNIPPETS = 25
MATCH_MODES = ("all", "any", "auto")
# Fewer issues than this holding every word of a question do not make an answer: a long question holds all its words in an
# issue by accident, and the issue that answers it, which lacks one of them, would be hidden. Measured on a real backlog:
# looking only for "none at all" found the issue in 6 of 8 questions, this rule in 7 (the eighth was in another language).
RELAX_BELOW = 5
# People are not asked for: no assignee, no reporter. The text fields come only when a passage is wanted.
SEARCH_FIELDS = ("summary", "status", "issuetype", "priority", "labels", "updated", "parent", EPIC_LINK, STORY_POINTS, SPRINT)
SNIPPET_FIELDS = ("description", ACCEPTANCE)
_KEY = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")


@dataclass
class Hit:
    issue: Issue  # without people, text redacted
    epic_summary: str = ""
    snippet: str = ""  # the passage around the words (with snippets), else empty
    matched: tuple[str, ...] = ()  # which of the words the issue holds (with snippets)
    url: str = ""
    all_words: bool | None = None  # True: it holds every word. None: not known (a search for any of the words)


@dataclass
class FindResult:
    hits: list[Hit]
    total: int
    jql: str
    scope: str
    terms: tuple[str, ...] = ()  # what was searched for, as it went into the query
    ignored: tuple[str, ...] = ()  # filler words that were left out of the query
    match: str = "all"  # all | any
    relaxed: bool = False  # fewer than RELAX_BELOW issues held every word, so the issues that hold some of them are shown
    notes: list[str] = field(default_factory=list)  # for the person at the terminal
    start_at: int = 0
    truncated: bool = False


@dataclass
class _Found:
    issues: list[Issue]
    total: int
    truncated: bool


def browse_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/browse/{key}" if base_url else ""


class FindIssues:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(
        self,
        *,
        text: str = "",
        project: str = "",
        all_projects: bool = False,
        issue_types: Sequence[str] = (),
        statuses: Sequence[str] = (),
        labels: Sequence[str] = (),
        under: str = "",
        sprint: str = "",
        fix_version: str = "",
        since: str = "",
        extra_jql: str = "",
        limit: int = 10,
        start_at: int = 0,
        fetch_everything: bool = False,
        match: str = "auto",
        snippets: int = 0,
    ) -> FindResult:
        if match not in MATCH_MODES:
            raise UsageError(f"Unknown match mode: {match}", hint=f"Use one of: {', '.join(MATCH_MODES)}.")
        if not 0 <= snippets <= MAX_SNIPPETS:
            raise UsageError(f"--snippets must be between 0 and {MAX_SNIPPETS}.")
        if limit < 1 or start_at < 0:
            raise UsageError("--limit must be at least 1 and --start-at must not be negative.")
        chosen = self._project(project, all_projects)
        scope = f"project {chosen}" if chosen else "every project"
        parsed = parse_terms(text)
        options: dict[str, Any] = dict(
            project=chosen,
            issue_types=[item for item in issue_types if item.strip()],
            statuses=[item for item in statuses if item.strip()],
            labels=[item for item in labels if item.strip()],
            under=parse_issue_key(under) if under else "",
            epic_field=self._epic_field() if under else '"Epic Link"',
            sprint=sprint.strip(),
            fix_version=fix_version.strip(),
            since=since.strip(),
            extra=extra_jql.strip(),
        )
        fields = [*SEARCH_FIELDS, *(SNIPPET_FIELDS if snippets else ())]
        mode = "any" if match == "any" else "all"
        query = jql_builder.search_query(terms=parsed, match=mode, **options)
        may_relax = match == "auto" and len(parsed.used) > 1
        # When the search may be loosened, the few issues that hold every word are fetched too: they are marked in the loosened list.
        found = self._find(query, fields, limit=max(limit, RELAX_BELOW) if may_relax else limit, start_at=start_at, everything=fetch_everything)
        relaxed = False
        strict: list[Issue] = []
        if may_relax and found.total < RELAX_BELOW:
            mode, relaxed = "any", True
            strict = found.issues if start_at == 0 else self._find(query, fields, limit=RELAX_BELOW, start_at=0, everything=False).issues
            query = jql_builder.search_query(terms=parsed, match="any", **options)
            found = self._find(query, fields, limit=limit, start_at=start_at, everything=fetch_everything)
        elif not fetch_everything:
            found = _Found(found.issues[:limit], found.total, found.truncated)
        strict_keys = {issue.key for issue in strict}
        shown = tuple(term.display for term in parsed.used)
        notes: list[str] = []
        if text.strip() and not parsed:
            notes.append("The text held no word to search for (only punctuation), so only the filters apply.")
        if relaxed:
            holding = f"Only {len(strict_keys)} issue{'s' if len(strict_keys) != 1 else ''} hold{'' if len(strict_keys) != 1 else 's'} all"
            notes.append(
                (f"No issue holds all of these words ({', '.join(shown)})" if not strict_keys else f"{holding} of these words ({', '.join(shown)})")
                + "; the issues that hold some of them are shown, the best match first"
                + (" (`allWords` marks the ones that hold every word)" if strict_keys else "")
                + ". `--match all` makes the search strict again."
            )
        if parsed.ignored:
            notes.append(f"Left out as filler: {', '.join(parsed.ignored)}. Put a word in quotes to keep it.")
        if found.truncated:
            notes.append(f"Stopped after {MAX_HITS} issues; narrow the search with a filter.")
        if found.total == 0:
            notes.append(
                f"Nothing found in {scope}. The issues may be written in another language than your words (the search does not "
                "translate): try the words of the issues, fewer or shorter words, a wildcard such as rol*, or `--match any`. "
                "`jira how-to find-information` says more."
            )
        issues = [hide_users(issue) for issue in found.issues]
        everything_held: bool | None = True if mode == "all" else None
        hits = [
            Hit(issue, url=browse_url(self._settings.base_url, issue.key), all_words=(issue.key in strict_keys) if relaxed else everything_held)
            for issue in issues
        ]
        hits = self._add_epic_names(hits, notes)
        if snippets and hits:
            hits = self._add_snippets(hits, parsed.used or terms_from_jql(extra_jql), snippets)
        return FindResult(hits, found.total, query, scope, shown, parsed.ignored, mode, relaxed, notes, start_at, found.truncated)

    def _project(self, project: str, all_projects: bool) -> str:
        if project and all_projects:
            raise UsageError("--project and --all-projects exclude each other.")
        if all_projects:
            return ""
        chosen = project or self._settings.project
        if not chosen:
            raise UsageError("No project selected.", hint="Run `jira use KEY`, pass --project KEY, or search every project with --all-projects.")
        return parse_project_key(chosen)

    def _epic_field(self) -> str:
        """How JQL names the Epic Link field: by its id when the instance's field was found, else by the name Jira gives it."""
        identifier = self._gateway.field_ids().get(EPIC_LINK) or ""
        number = identifier.removeprefix("customfield_")
        return f"cf[{number}]" if number.isdigit() else '"Epic Link"'

    def _find(self, query: str, fields: Sequence[str], *, limit: int, start_at: int, everything: bool) -> _Found:
        """The hits from `start_at` on, as many as `limit` (a server may answer with fewer per request: the next page is asked for)."""
        issues: list[Issue] = []
        total, position, truncated = 0, start_at if not everything else 0, False
        wanted = MAX_HITS if everything else limit
        while True:
            page = self._gateway.search(query, fields=fields, start_at=position, max_results=min(PAGE_SIZE, max(wanted - len(issues), 1)))
            total = page.total
            issues.extend(page.issues)
            position += len(page.issues)
            if not page.issues or position >= total or len(issues) >= wanted:
                break
        if everything and position < total and len(issues) >= MAX_HITS:
            truncated = True
        return _Found(issues, total, truncated)

    def _add_epic_names(self, hits: list[Hit], notes: list[str]) -> list[Hit]:
        """The name of the epic of each hit, from the hits themselves or from one more request that asks for the other epics."""
        names = {hit.issue.key: hit.issue.summary for hit in hits}
        missing = sorted({hit.issue.epic_key for hit in hits if _KEY.fullmatch(hit.issue.epic_key or "") and hit.issue.epic_key not in names})
        if missing:
            try:
                page = self._gateway.search(
                    "key in (" + ", ".join(missing) + ")", fields=["summary"], start_at=0, max_results=min(len(missing), PAGE_SIZE)
                )
                names.update({issue.key: redact_text(issue.summary) for issue in page.issues})
            except JiraCliError as error:
                notes.append(f"The names of the epics could not be read: {error.message}")
        return [replace(hit, epic_summary=names.get(hit.issue.epic_key, "")) if hit.issue.epic_key else hit for hit in hits]

    def _add_snippets(self, hits: list[Hit], terms: Sequence[Term], count: int) -> list[Hit]:
        """The passage around the words for the first `count` hits. The text came with the search, so no request is needed, and
        it is already redacted: a mention is `[~user]`, an e-mail address `[email]`."""
        result: list[Hit] = []
        for index, hit in enumerate(hits):
            if index >= count:
                result.append(hit)
                continue
            parts = [wiki_to_text(hit.issue.description), wiki_to_text(hit.issue.acceptance)]
            body = "\n".join(part for part in parts if part)
            passages = find_passages(body, terms)
            seen = set(passages.matched) | set(matched_terms(hit.issue.summary, terms))
            result.append(
                replace(hit, snippet=passages.text or lead(body), matched=tuple(term.display for term in terms if term.display in seen))
            )
        return result
