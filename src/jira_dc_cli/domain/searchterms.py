"""What a person or an agent types into a search, turned into search terms.

Jira matches every word of `text ~ "a b c"` (an AND) and does not skip filler words: a whole question such as
"how do I export the monthly report for ACME?" finds nothing although the issue exists, and so does a word that carries its
question mark ("ACME?" is a wildcard that wants one more letter). Some characters are refused outright with HTTP 400:
a leading `~`, unbalanced parentheses, `[`, `]`, `{`, `}`, a trailing `^`, a lone `!`, `-` or `+`. Here a text becomes
words and phrases, without the punctuation, the filler words and those characters, and what was left out is reported,
so that nothing disappears unnoticed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .errors import UsageError

MAX_TERMS = 12

# Words that carry no meaning in a search, English and German. Short on purpose: a word that can be a subject
# (`bin`, `list`, `show`, `war`, `man`) is not here.
STOP_WORDS = frozenset(
    (
        "a an the this that these those there here it its i me my we us our you your he she they them his her their "
        "of in on at to for from by with about into over after before between as is are was were be been being am "
        "do does did done have has had can could will would shall should may might must "
        "how what which who whom whose when where why whether please "
        "der die das den dem des ein eine einen einem einer eines "
        "aber kein keine keinen wie was welche welcher welches welchen wer wem wen wann wo wohin woher warum wieso weshalb "
        "ich mich mir wir uns ihr euch du dich dir er sie es ihn ihm "
        "ist sind sein wird werden wurde wurden hat haben hatte kann kannst koennen können muss müssen soll sollen darf dürfen "
        "mit für fuer von vom zu zum zur im auf bei aus nach über ueber unter um durch gegen ohne bitte gibt"
    ).split()
)
# Operators are never terms, whatever their spelling: `AND` in a question is not a word to look for.
OPERATORS = frozenset({"and", "or", "not", "und", "oder", "nicht"})

_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"'})
_QUOTE_MARKS = re.compile('["\u201c\u201d\u201e\u201f\u00ab\u00bb]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
_EDGE = ".,;:!?()[]{}<>'\u2018\u2019\u201a"
# What Jira's text search refuses or reads as an operator inside a word: each becomes a space.
_UNSAFE = re.compile(r"[~^\[\]{}()!\\]")
_JQL_TEXT = re.compile(r'\b(?:text|summary|description|comment|environment)\s*~\s*"((?:\\.|[^"\\])*)"', re.IGNORECASE)


@dataclass(frozen=True)
class Term:
    text: str
    phrase: bool = False  # several words that must stand together, in this order

    @property
    def display(self) -> str:
        return f'"{self.text}"' if self.phrase else self.text


@dataclass(frozen=True)
class Terms:
    used: tuple[Term, ...] = ()
    ignored: tuple[str, ...] = ()  # filler words, and terms beyond the limit, that were left out

    def __bool__(self) -> bool:
        return bool(self.used)


def _is_filler(word: str) -> bool:
    lowered = word.casefold()
    if lowered in OPERATORS:
        return True
    # `IT` and `US` are abbreviations, `it` and `us` are pronouns; a single capital (`I`, `A`) is the pronoun or the article.
    return lowered in STOP_WORDS and not (len(word) > 1 and word.isupper())


def _plain_words(word: str) -> list[str]:
    """The words inside one token: no edge punctuation, no leading + or - (Jira reads them as operators), and a word that
    held a character the search refuses becomes two words."""
    pieces = _UNSAFE.sub(" ", word.strip('"').strip(_EDGE)).split()
    words = []
    for piece in pieces:
        value = piece.strip(_EDGE).lstrip("+-")
        if re.search(r"\w", value):  # a lone `=` or `-` is not something to look for
            words.append(value)
    return words


def parse_terms(text: str) -> Terms:
    """Words and quoted phrases of `text`. A word in quotes is kept even when it is a filler word."""
    cleaned = _CONTROL.sub(" ", str(text or "")).translate(_QUOTES)
    candidates: list[tuple[str, bool, bool]] = []  # text, phrase, quoted
    for match in _TOKEN.finditer(cleaned):
        quoted, word = match.group(1), match.group(2)
        if quoted is not None:
            value = " ".join(_UNSAFE.sub(" ", quoted).split())
            if re.search(r"\w", value):
                candidates.append((value, " " in value, True))
            continue
        for value in _plain_words(word):
            candidates.append((value, False, False))
    seen: set[str] = set()
    unique = []
    for value, phrase, quoted in candidates:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append((value, phrase, quoted))
    meaningful = any(quoted or not _is_filler(value) for value, _, quoted in unique)
    used: list[Term] = []
    ignored: list[str] = []
    for value, phrase, quoted in unique:
        if meaningful and not quoted and _is_filler(value):
            ignored.append(value)
        elif len(used) >= MAX_TERMS:
            ignored.append(value)
        else:
            used.append(Term(value, phrase))
    return Terms(tuple(used), tuple(ignored))


def with_phrases(text: str, phrases: Sequence[str]) -> str:
    """The search text with exact phrases added. A phrase is given on its own (`--phrase 'audit trail'`), so that its words stay together
    without double quotes, which Windows PowerShell 5.1 removes inside an argument. The phrases come first: an unbalanced quote in the
    text then cannot swallow them, and they are the last terms to be left out at the limit."""
    quoted: list[str] = []
    for phrase in phrases:
        words = " ".join(_QUOTE_MARKS.sub(" ", str(phrase or "")).split())
        if not words:
            raise UsageError("--phrase needs the words of the phrase.", hint="For example --phrase 'audit trail'.")
        quoted.append(f'"{words}"')
    return " ".join(part for part in (*quoted, str(text or "").strip()) if part)


def terms_from_jql(jql: str) -> tuple[Term, ...]:
    """The words and phrases that a JQL query searches for in the text, to look for them in an issue."""
    found: list[Term] = []
    for match in _JQL_TEXT.finditer(jql or ""):
        value = re.sub(r"\\(.)", r"\1", match.group(1))
        if len(value) > 2 and value.startswith('"') and value.endswith('"'):
            inner = " ".join(value[1:-1].split())
            if inner:
                found.append(Term(inner, " " in inner))
            continue
        for word in value.split():
            word = word.strip(_EDGE + '"')
            if re.search(r"\w", word):
                found.append(Term(word))
    unique: dict[str, Term] = {}
    for term in found:
        unique.setdefault(term.text.casefold(), term)
    return tuple(unique.values())
