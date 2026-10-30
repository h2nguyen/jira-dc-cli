"""The people that a JQL query names, left out of what is shown.

A saved filter holds a JQL that somebody wrote, and `assignee = jdoe` names a person as plainly as a mention does. `redact_jql` puts
`[user]` in place of every literal value that a clause of a user field holds: `assignee`, `reporter`, `creator`, `watcher`, `voter`,
`commentedBy`, `approver` and `"Request participants"`, in any case and with the field name quoted or not, after `=`, `!=`, `~`, `!~`,
`is`, `in`, `not in`, `was`, `was in`, `was not`, `was not in` and `changed from` / `to`, as one value or as a list in brackets. It
does the same for the value after `by` in a `was` or `changed` clause of any field (`status changed by jdoe`) and for the user of
`updatedBy()`, `projectsLeadByUser()` and `componentsLeadByUser()`. Functions (`currentUser()`, `membersOf("group")`) and the words
`EMPTY`, `NULL` and `unassigned` stay: a group, or nobody, is no person.

What it does not recognise stays as it is: a user field that has a name of its own (`"Reviewer" = jdoe`, `cf[10100] = jdoe`), a name that
is written in a text (`text ~ "jane doe"`), and a JQL that Jira would refuse. It is no parser of JQL. It reads words, quoted strings,
brackets and operators in one pass, and every token is looked at a bounded number of times, so that a hostile text of 200 KB costs time
in proportion to its length.
"""
from __future__ import annotations

import re

PLACEHOLDER = "[user]"
USER_FIELDS = frozenset({"assignee", "reporter", "creator", "watcher", "voter", "commentedby", "approver", "request participants"})
USER_FUNCTIONS = frozenset({"updatedby", "projectsleadbyuser", "componentsleadbyuser"})  # their first argument is a user name
_NOT_PEOPLE = frozenset({"empty", "null", "unassigned"})
_COMPARISONS = frozenset({"=", "==", "!=", "~", "!~", "<", "<=", ">", ">="})
_CLAUSE_WORDS = frozenset({"is", "not", "in", "was", "changed"})
_WHO = frozenset({"from", "to", "by"})  # predicates of a `was` or `changed` clause that hold people (`from` and `to` of a user field only)
_WHEN = frozenset({"after", "before", "on", "during"})  # and those that hold dates
_NAME_LIMIT = 64  # no field name of people is longer; a longer text is not looked at

# A quoted string that is not closed runs to the end of the text, so that no retry can make the pattern quadratic: after the loop the
# next character is a quote or the end, and the pattern always matches. Whatever is no token is white space.
_TOKEN = re.compile(
    r"""
    (?P<string>"[^"\\]*(?:\\[\s\S]?[^"\\]*)*(?:"|\Z)|'[^'\\]*(?:\\[\s\S]?[^'\\]*)*(?:'|\Z))
    |(?P<open>\()
    |(?P<close>\))
    |(?P<comma>,)
    |(?P<operator>[=!~<>]+)
    |(?P<connector>[&|]+)
    |(?P<word>[^\s"'(),=!~<>&|]+)
    """,
    re.VERBOSE,
)


class _Redactor:
    def __init__(self, text: str) -> None:
        self._text = text
        self._tokens = [(match.lastgroup, match.start(), match.end()) for match in _TOKEN.finditer(text)]
        self._spans: list[tuple[int, int]] = []

    def run(self) -> str:
        index = 0
        while index < len(self._tokens):
            index = self._visit(index)
        if not self._spans:
            return self._text
        parts: list[str] = []
        last = 0
        for start, end in self._spans:
            if start >= last:
                parts += [self._text[last:start], PLACEHOLDER]
                last = end
        parts.append(self._text[last:])
        return "".join(parts)

    # -- the tokens ------------------------------------------------------------------------------

    def _kind(self, index: int) -> str:
        return self._tokens[index][0] if 0 <= index < len(self._tokens) else ""

    def _word(self, index: int) -> str:
        """The text of an unquoted word in lower case; nothing for any other token."""
        if self._kind(index) != "word":
            return ""
        _, start, end = self._tokens[index]
        return self._text[start:end].lower()

    def _is_call(self, index: int) -> bool:
        return self._kind(index) == "word" and self._kind(index + 1) == "open"

    def _is_comparison(self, index: int) -> bool:
        if self._kind(index) != "operator":
            return False
        _, start, end = self._tokens[index]
        return end - start <= 2 and self._text[start:end] in _COMPARISONS

    def _name(self, index: int) -> str:
        """The name of a field as written at the start of a clause: a word, or a string without its quotes; lower case, blanks as one."""
        _, start, end = self._tokens[index]
        if self._kind(index) == "string":
            start += 1
            end = end - 1 if end - start >= 1 and self._text[end - 1] == self._text[start - 1] else end
        return " ".join(self._text[start:end].lower().split()) if end - start <= _NAME_LIMIT else ""

    # -- the clauses -----------------------------------------------------------------------------

    def _visit(self, index: int) -> int:
        """Look at the token where a clause may begin. Returns the index to go on from: always beyond `index`."""
        if self._kind(index) not in ("word", "string"):
            return index + 1
        if self._is_call(index):
            if self._word(index) in USER_FUNCTIONS:
                self._person(index + 2)
            return index + 1
        after = index + 1
        comparison = self._is_comparison(after)
        word = self._word(after)
        if not comparison and word not in _CLAUSE_WORDS:
            return index + 1
        people = self._name(index) in USER_FIELDS
        if word == "was":
            return self._history(after + 1, people, was=True)
        if word == "changed":
            return self._history(after + 1, people, was=False)
        if not people:
            return index + 1
        if comparison or word == "in":
            return self._value(after + 1, True)
        if word == "is":
            return self._value(after + 2 if self._word(after + 1) == "not" else after + 1, True)
        return self._value(after + 2, True) if self._word(after + 1) == "in" else index + 1  # `not in`

    def _history(self, index: int, people: bool, *, was: bool) -> int:
        """The rest of a `was ...` or `changed ...` clause. The people in it are the value of `was`, what follows `from` and `to` when
        the field holds people, and what follows `by` for every field; what follows `after`, `before`, `on` and `during` is a date."""
        if was:
            index += 1 if self._word(index) == "not" else 0
            index += 1 if self._word(index) == "in" else 0
            index = self._value(index, people)
        while True:
            word = self._word(index)
            if word in _WHO:
                index = self._value(index + 1, people or word == "by")
            elif word in _WHEN:
                index = self._value(index + 1, False)
            else:
                return index

    def _value(self, index: int, redact: bool) -> int:
        """Read the value at `index`: one literal, a list in brackets or a function call. Returns the index after it, or `index` if there is none."""
        kind = self._kind(index)
        if kind == "open":
            return self._list(index, redact)
        if self._is_call(index):
            return self._skip(index + 1)
        if kind in ("word", "string"):
            if redact:
                self._person(index)
            return index + 1
        return index

    def _list(self, index: int, redact: bool) -> int:
        depth = 0
        while index < len(self._tokens):
            kind = self._kind(index)
            if kind == "open":
                depth += 1
            elif kind == "close":
                depth -= 1
                if depth == 0:
                    return index + 1
            elif self._is_call(index):
                index = self._skip(index + 1)
                continue
            elif redact:
                self._person(index)
            index += 1
        return index

    def _skip(self, index: int) -> int:
        """The index after the brackets that open at `index`; the end of the text when they are not closed."""
        depth = 0
        while index < len(self._tokens):
            kind = self._kind(index)
            if kind == "open":
                depth += 1
            elif kind == "close":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return index

    def _person(self, index: int) -> None:
        """Mark the literal at `index` as a person, unless it is a function, EMPTY or the like."""
        if self._kind(index) in ("word", "string") and not self._is_call(index) and self._word(index) not in _NOT_PEOPLE:
            _, start, end = self._tokens[index]
            self._spans.append((start, end))


def redact_jql(jql: str) -> str:
    """The JQL with `[user]` in place of the people that it names (see the module). A JQL that names nobody comes back as it is."""
    return _Redactor(str(jql or "")).run()
