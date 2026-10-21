"""The passage of an issue around the words that were searched for, so that a hit can be judged without opening the issue."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Iterator, Sequence

from .searchterms import Term

WIDTH = 220
LEAD = 200
_MAX_POSITIONS = 400  # in all, shared by the terms
_WORD = re.compile(r"\w+")
_WORD_WITH_WILDCARDS = re.compile(r"[\w*?]+")


@dataclass(frozen=True)
class Passages:
    text: str  # the passages, joined by an ellipsis; empty when no term occurs
    matched: tuple[str, ...]  # the terms that occur in the text, as they were given


def _stem(word: str) -> str:
    """A rough stem, so that `groups` finds `group` and `requesting` finds `request`: only for showing the passage."""
    lowered = word.casefold()
    if "*" in lowered or "?" in lowered:
        return lowered
    for suffix, minimum in (("ing", 6), ("ed", 5), ("s", 4)):
        if len(lowered) >= minimum and lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def _pattern(term: Term) -> re.Pattern[str]:
    """The term as a regular expression: a phrase, or a word that may continue (`group` finds `groups`). The wildcards of a word
    that holds punctuation are left out here; a word with wildcards is matched by `_starts` without a regular expression."""
    if term.phrase:
        return re.compile(r"\s+".join(re.escape(part) for part in term.text.split()), re.IGNORECASE)
    stem = _stem(term.text).replace("\\", "").replace("*", "").replace("?", "")
    return re.compile(r"(?<!\w)" + re.escape(stem) + r"\w*", re.IGNORECASE)


def _wildcard(term: Term) -> re.Pattern[str] | None:
    """A matcher for a whole word given with `*` or `?`, or None when the term is not such a word. `fnmatch` builds expressions
    that do not backtrack without end, as a chain of `\\w*` would on a hostile text."""
    text = term.text.replace("\\", "")
    if term.phrase or not ("*" in text or "?" in text) or not _WORD_WITH_WILDCARDS.fullmatch(text):
        return None
    return re.compile(fnmatch.translate(re.sub(r"\*+", "*", text) + "*"), re.IGNORECASE)  # a word may continue after the last letter


def _starts(term: Term, text: str) -> Iterator[int]:
    """Where the term begins in the text, in order."""
    glob = _wildcard(term)
    if glob is not None:
        return (word.start() for word in _WORD.finditer(text) if glob.match(word.group()))
    return (match.start() for match in _pattern(term).finditer(text))


def matched_terms(text: str, terms: Sequence[Term]) -> tuple[str, ...]:
    """The terms that occur in the text."""
    return tuple(term.display for term in terms if next(_starts(term, text or ""), None) is not None)


def lead(text: str, width: int = LEAD) -> str:
    """The beginning of a text, cut at a word."""
    flat = " ".join((text or "").split())
    if len(flat) <= width:
        return flat
    cut = flat.rfind(" ", 0, width)
    return flat[: cut if cut > width // 2 else width].rstrip() + " …"


def find_passages(text: str, terms: Sequence[Term], *, width: int = WIDTH, count: int = 2) -> Passages:
    """Up to `count` windows of about `width` characters that hold the most different terms."""
    flat = " ".join((text or "").split())
    if not flat or not terms:
        return Passages("", ())
    positions: list[tuple[int, int]] = []  # (where, which term)
    matched: list[str] = []
    per_term = max(1, _MAX_POSITIONS // len(terms))
    for index, term in enumerate(terms):
        found = list(_starts(term, flat))
        if found:
            matched.append(term.display)
            if len(found) > per_term:  # spread over the text, so that a word that occurs often cannot crowd out one that is rare
                found = found[:: -(-len(found) // per_term)]
            positions.extend((where, index) for where in found)
    if not positions:
        return Passages("", ())
    positions.sort()

    def window(where: int) -> tuple[int, int]:
        start = max(0, where - width // 3)
        end = min(len(flat), start + width)
        start = max(0, end - width) if end == len(flat) else start
        if start:
            space = flat.find(" ", start, start + 24)
            start = space + 1 if space != -1 else start
        if end < len(flat):
            space = flat.rfind(" ", end - 24, end)
            end = space if space != -1 else end
        return start, end

    candidates = []
    for where, _ in positions:
        start, end = window(where)
        covered = {which for place, which in positions if start <= place < end}
        candidates.append((-len(covered), start, end, covered))
    candidates.sort()
    chosen: list[tuple[int, int]] = []
    covered_total: set[int] = set()
    for negative, start, end, covered in candidates:
        if len(chosen) >= count:
            break
        if any(start < other_end and other_start < end for other_start, other_end in chosen):
            continue
        if chosen and not (covered - covered_total):
            continue  # a second window must show something the first one does not
        chosen.append((start, end))
        covered_total |= covered
    chosen.sort()
    pieces = [flat[start:end].strip() for start, end in chosen]
    prefix = "… " if chosen[0][0] > 0 else ""
    suffix = " …" if chosen[-1][1] < len(flat) else ""
    return Passages(prefix + " … ".join(pieces) + suffix, tuple(matched))
