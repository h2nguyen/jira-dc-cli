"""JQL building: quoting and the searches the CLI needs."""
from __future__ import annotations

import re
from typing import Sequence

from .errors import UsageError, ValidationError
from .savedfilters import parse_filter_id
from .searchterms import Term, Terms

MATCH_MODES = ("all", "any")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?: [0-9]{2}:[0-9]{2})?")
_RELATIVE = re.compile(r"-?([0-9]+)([mhdw])")
_ORDER_BY = re.compile(r"order\s+by\b", re.IGNORECASE)
_ISSUE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+")
_EPIC_FIELD = re.compile(r'cf\[[0-9]+\]|"[A-Za-z][A-Za-z0-9 ]*"')


def quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def project_clause(project: str) -> str:
    return f"project = {quote(project)}"


def filter_clause(filter_id: str) -> str:
    """`filter = 10000`: the issues of a saved filter. The id is checked, so that no text can add a clause."""
    return f"filter = {parse_filter_id(filter_id)}"


def issue_search(*, project: str = "", jql: str = "", mine: bool = False, filter_id: str = "") -> str:
    if filter_id:
        return _filter_search(filter_id, jql, mine)
    if mine:
        if jql:
            raise ValidationError("--mine cannot be combined with --jql")
        base = "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC"
        return f"{project_clause(project)} AND {base}" if project else base
    if jql:
        return jql
    if not project:
        raise UsageError(
            "No project selected.",
            hint="Run `jira use KEY`, pass --project KEY, or give a query with --jql.",
        )
    return f"{project_clause(project)} ORDER BY created DESC"


def _filter_search(filter_id: str, jql: str, mine: bool) -> str:
    """A saved filter replaces the project query, as `--jql` does. A `--jql` that goes with it narrows the filter (AND); its ORDER BY is kept."""
    if mine:
        raise ValidationError("--mine cannot be combined with --filter")
    clause = filter_clause(filter_id)
    condition, order = split_order_by(jql)
    if condition and not _closes(condition):
        raise ValidationError(
            "--jql has a bracket or a quote that does not close.",
            hint="The filter would not stay in force. Balance the brackets and quotes of your condition.",
        )
    parts = [f"{clause} AND ({condition})" if condition else clause, order]
    return " ".join(part for part in parts if part)


def report_search(project: str, *, sprint: str = "", fix_version: str = "") -> tuple[str, str]:
    """The JQL of a progress report and a short description of its scope."""
    jql = project_clause(project)
    scope = f"project {project}"
    if sprint == "active":
        jql += " AND sprint in openSprints()"
        scope += ", open sprint(s)"
    elif sprint:
        jql += f" AND sprint = {quote(sprint)}"
        scope += f', sprint "{sprint}"'
    if fix_version:
        jql += f" AND fixVersion = {quote(fix_version)}"
        scope += f', fix version "{fix_version}"'
    return f"{jql} ORDER BY key ASC", scope


def epics_search(project: str) -> str:
    return f"{project_clause(project)} AND issuetype = Epic ORDER BY key ASC"


def backlog_search(project: str) -> str:
    return f"{project_clause(project)} ORDER BY key ASC"


# -- the search of `jira search` ---------------------------------------------------------------------


def _text_value(term: Term) -> str:
    return f'"{term.text}"' if term.phrase else term.text


def text_clause(terms: Sequence[Term], match: str = "all") -> str:
    """The clause for the words and phrases of a search.

    `all`: every word and phrase must occur (`text ~ "a b"`; each phrase is a clause of its own). `any`: one is enough
    (`(text ~ "a" OR text ~ "b")`). Without an ORDER BY Jira ranks by relevance, so with `any` the issues that hold most of
    them come first."""
    if match not in MATCH_MODES:
        raise ValidationError(f"Unknown match mode: {match}", hint=f"Use one of: {', '.join(MATCH_MODES)}.")
    if not terms:
        return ""
    if match == "any" and len(terms) > 1:
        return "(" + " OR ".join(f"text ~ {quote(_text_value(term))}" for term in terms) + ")"
    words = [term.text for term in terms if not term.phrase]
    parts = [f"text ~ {quote(' '.join(words))}"] if words else []
    parts += [f"text ~ {quote(_text_value(term))}" for term in terms if term.phrase]
    return " AND ".join(parts)


def split_order_by(query: str) -> tuple[str, str]:
    """A JQL text without its ORDER BY clause, and the clause: an ORDER BY inside quotes is not one."""
    quote_char = ""
    escaped = False
    for index, char in enumerate(query):
        if escaped:
            escaped = False
        elif quote_char:
            if char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = ""
        elif char in "\"'":
            quote_char = char
        elif char in "oO" and (index == 0 or not (query[index - 1].isalnum() or query[index - 1] == "_")) and _ORDER_BY.match(query, index):
            return query[:index].strip(), query[index:].strip()
    return query.strip(), ""


def _one_or_in(field: str, values: Sequence[str]) -> str:
    cleaned = [_CONTROL.sub(" ", value).strip() for value in values if value.strip()]
    if len(cleaned) == 1:
        return f"{field} = {quote(cleaned[0])}"
    return f"{field} in ({', '.join(quote(value) for value in cleaned)})"


def _since_clause(since: str) -> str:
    text = since.strip()
    relative = _RELATIVE.fullmatch(text)
    if relative:
        return f"updated >= -{relative.group(1)}{relative.group(2)}"
    if _DATE.fullmatch(text):
        return f"updated >= {quote(text)}"
    raise ValidationError(f"Not a date: {since}", hint="Write YYYY-MM-DD (or YYYY-MM-DD HH:MM), or a time ago such as 7d, 2w or 12h.")


def _closes(text: str) -> bool:
    """Whether the brackets and the quotes of a JQL condition close. One that does not could end the brackets that keep the other filters in force."""
    depth = 0
    quote_char = ""
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif quote_char:
            if char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = ""
        elif char in "\"'":
            quote_char = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not quote_char


def search_query(
    *,
    terms: Terms | None = None,
    match: str = "all",
    project: str = "",
    issue_types: Sequence[str] = (),
    statuses: Sequence[str] = (),
    labels: Sequence[str] = (),
    under: str = "",
    epic_field: str = '"Epic Link"',
    sprint: str = "",
    fix_version: str = "",
    since: str = "",
    extra: str = "",
    filter_id: str = "",
) -> str:
    """The query of `jira search`: the project (when one is chosen), the words and the filters, ANDed.

    `extra` is a condition of your own that is added with AND; an ORDER BY in it is kept. `filter_id` adds a saved filter. Without
    words the newest issues come first, with words Jira's relevance order stays."""
    condition, order = split_order_by(extra)
    if condition and not _closes(condition):
        raise ValidationError("--jql has a bracket or a quote that does not close.", hint="The other filters would not stay in force. Balance the brackets and quotes of your condition.")
    if under and not _ISSUE_KEY.fullmatch(under):
        raise ValidationError(f"Not an issue key: {under}", hint="--under takes the key of an epic or of a story, such as PROJ-5.")
    if not _EPIC_FIELD.fullmatch(epic_field):
        raise ValidationError(f"Not a field reference: {epic_field}", hint='Write "Epic Link" or cf[12345].')
    clauses: list[str] = []
    if project:
        clauses.append(project_clause(project))
    if filter_id:
        clauses.append(filter_clause(filter_id))
    if terms:
        clauses.append(text_clause(terms.used, match))
    if issue_types:
        clauses.append(_one_or_in("issuetype", issue_types))
    if statuses:
        clauses.append(_one_or_in("status", statuses))
    if labels:
        clauses.append(_one_or_in("labels", labels))
    if under:
        clauses.append(f"({epic_field} = {under} OR parent = {under})")
    if sprint:
        clauses.append("sprint in openSprints()" if sprint.casefold() == "active" else f"sprint = {quote(_CONTROL.sub(' ', sprint))}")
    if fix_version:
        clauses.append(f"fixVersion = {quote(_CONTROL.sub(' ', fix_version))}")
    if since:
        clauses.append(_since_clause(since))
    if condition:
        clauses.append(f"({condition})")
    if not clauses or clauses == [project_clause(project)] and not (terms or condition):
        raise UsageError("Nothing to search for.", hint="Give a text, or a filter such as --type, --status, --label, --under, --sprint, --fix-version, --since or --jql.")
    query = " AND ".join(clauses)
    if order:
        return f"{query} {order}"
    return query if terms else f"{query} ORDER BY updated DESC"
