"""The parts of a long text: an outline of its headings, one section, and a cut that does not split a paragraph.

A description can be long and the answer short. These functions let a reader (a person, a program, an agent) look at the headings
first, read only the part it needs, and cut a text at the end of a paragraph. Headings are the Jira wiki kind (`h2. Title`) and the
Markdown kind (`## Title`). A code block (`{code}`, `{noformat}`, a fenced block) holds no heading, and a cut does not split it: the cut
falls before the block, or, when the text opens with a block that does not fit, at the end of one of its lines, with the block closed there.

In wiki markup `# item` is a numbered list, not a heading. So in a text that has wiki headings, a `#` line that stands next to another
list line is read as a list item; in a text without wiki headings every `#` line is a Markdown heading. Written for fields of Jira's
size: every pattern is bounded and every pass is linear, so a hostile text costs time in proportion to its length.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import ValidationError
from .privacy import normalize_newlines
from .unwiki import wiki_to_text

_WIKI_HEADING = re.compile(r"^h([1-6])\.[ \t]+(.*)$")
_MARKDOWN_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
_LIST_LINE = re.compile(r"^[ \t]*[#*]+[ \t]")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
_CODE_MACRO = re.compile(r"\{(code|noformat)(?::[^{}\n]*)?\}")
_NUMBER = re.compile(r"[0-9]{1,6}")  # ASCII digits only: `isdigit()` takes `²`, which `int()` refuses, and `int()` refuses 4,300 digits
LISTED = 30  # headings that an error names before it says "and more"


@dataclass(frozen=True)
class Heading:
    index: int  # 1-based position in the outline
    level: int  # 1 to 6
    title: str  # as plain text
    line: int  # 0-based line of the heading
    end: int  # 0-based line after the end of its section (the next heading of the same or a higher level)
    chars: int  # size of the section's text, with its sub-sections


@dataclass(frozen=True)
class Cut:
    text: str  # what is shown
    truncated: bool  # whether something was cut off
    total_chars: int  # the size of the whole text (a line end counts as one character)
    closed: str = ""  # the end of a code block that the cut added to the text (`{code}`, a fence), when it fell inside the block


class _Code:
    """Whether a line is inside a code block, fed line by line: a fenced block of Markdown, or `{code}` and `{noformat}` of the wiki."""

    def __init__(self) -> None:
        self._fence: tuple[str, int] | None = None
        self._macro = ""

    @property
    def inside(self) -> bool:
        return self._fence is not None or bool(self._macro)

    @property
    def closing(self) -> str:
        """The line that ends the block that is open: nothing when no block is."""
        if self._fence is not None:
            return self._fence[0] * self._fence[1]
        return "{" + self._macro + "}" if self._macro else ""

    def feed(self, line: str) -> None:
        if self._fence is not None:
            closing = _FENCE_CLOSE.match(line)
            if closing and closing.group(1)[0] == self._fence[0] and len(closing.group(1)) >= self._fence[1]:
                self._fence = None
            return
        if not self._macro:
            opening = _FENCE.match(line)
            if opening:
                self._fence = (opening.group(1)[0], len(opening.group(1)))
                return
        for marker in _CODE_MACRO.finditer(line):
            if not self._macro:
                self._macro = marker.group(1)
            elif marker.group(1) == self._macro:
                self._macro = ""


def _closing_hashes_off(title: str) -> str:
    """`Title ##` is `Title` in Markdown. Plain string work: a pattern for the blanks before the hashes is quadratic on a long run of them."""
    text = title.strip()
    stripped = text.rstrip("#")
    return stripped.rstrip() if stripped != text and stripped[-1:] in (" ", "\t") else text


def _plain_title(raw: str) -> str:
    return " ".join(wiki_to_text(raw).split())


def _scan(lines: list[str]) -> list[tuple[int, int, str]]:
    """(line, level, title) of every heading outside code blocks, in the order of the text."""
    wiki: list[tuple[int, int, str]] = []
    markdown: list[tuple[int, int, str, bool]] = []
    code = _Code()
    for number, line in enumerate(lines):
        inside = code.inside
        code.feed(line)
        if inside:
            continue
        found = _WIKI_HEADING.match(line)
        if found:
            wiki.append((number, int(found.group(1)), _plain_title(found.group(2))))
            continue
        found = _MARKDOWN_HEADING.match(line)
        title = _closing_hashes_off(found.group(2)) if found else ""
        if found and title:
            beside = (number > 0 and bool(_LIST_LINE.match(lines[number - 1]))) or (number + 1 < len(lines) and bool(_LIST_LINE.match(lines[number + 1])))
            markdown.append((number, len(found.group(1)), _plain_title(title), beside))
    kept = [(number, level, title) for number, level, title, beside in markdown if not (wiki and beside)]
    return sorted([*wiki, *kept])


def outline(text: str) -> list[Heading]:
    """The headings of a text with the size of each section."""
    lines = normalize_newlines(text).split("\n")
    found = [item for item in _scan(lines) if item[2]]
    ends = [len(lines)] * len(found)
    open_levels: list[int] = []  # positions of the headings whose section has not ended yet; their levels rise from the bottom
    for position, (number, level, _) in enumerate(found):
        while open_levels and found[open_levels[-1]][1] >= level:
            ends[open_levels.pop()] = number
        open_levels.append(position)
    return [
        Heading(position + 1, level, title, number, ends[position], len("\n".join(lines[number : ends[position]]).rstrip()))
        for position, (number, level, title) in enumerate(found)
    ]


def _norm(text: str) -> str:
    return " ".join(_plain_title(text).casefold().split()).rstrip(":").strip()


def _listing(headings: list[Heading]) -> str:
    shown = " | ".join(f"{heading.index}. {heading.title}" for heading in headings[:LISTED])
    return shown + ("" if len(headings) <= LISTED else f" | and {len(headings) - LISTED} more")


def find_section(text: str, selector: str) -> tuple[str, Heading]:
    """The section under one heading, from the heading to the next one of the same or a higher level.

    `selector` is the text of the heading in any case (all of it; when no heading has exactly that text, a part of it that only one
    heading holds), or the number that `outline` gave the heading. No match, or several, is an error that lists the headings."""
    headings = outline(text)
    wanted = _norm(selector)
    if not headings:
        raise ValidationError("The text has no headings, so it has no sections.", hint="Read it whole, or cut it with --max-chars.")
    if not wanted:
        raise ValidationError("--section needs the text of a heading or its number.", hint="The headings: " + _listing(headings) + ".")
    number = int(wanted) if _NUMBER.fullmatch(wanted) else 0
    if 1 <= number <= len(headings) and not any(_norm(heading.title) == wanted for heading in headings):
        chosen = [headings[number - 1]]
    else:
        chosen = [heading for heading in headings if _norm(heading.title) == wanted] or [heading for heading in headings if wanted in _norm(heading.title)]
    if not chosen:
        raise ValidationError(f"No heading matches {selector!r}.", hint="The headings: " + _listing(headings) + ".")
    if len(chosen) > 1:
        raise ValidationError(f"{len(chosen)} headings match {selector!r}.", hint="Say which one by its number: " + _listing(chosen) + ".")
    heading = chosen[0]
    lines = normalize_newlines(text).split("\n")
    return "\n".join(lines[heading.line : heading.end]).rstrip(), heading


def _is_heading(line: str) -> bool:
    return bool(_WIKI_HEADING.match(line) or _MARKDOWN_HEADING.match(line))


def _without_trailing_headings(lines: list[str]) -> list[str]:
    """The lines without a heading, or blank line, at the end: a cut must not leave a title with nothing under it."""
    kept = list(lines)
    while kept and (not kept[-1].strip() or _is_heading(kept[-1])):
        kept.pop()
    return kept


def truncate(text: str, max_chars: int) -> Cut:
    """The text cut to at most `max_chars`, at the end of a paragraph if it can be, never inside a code block that something precedes
    and never below a heading that has nothing under it. When nothing can be shown before the code block that opens the text, the block
    is cut at the end of its last line that fits and closed there (`Cut.closed`). Failing that (the first line is longer than the
    limit) at the end of a word."""
    flat = normalize_newlines(text)
    total = len(flat)
    if max_chars < 1 or total <= max_chars:
        return Cut(flat, False, total)
    lines = flat.split("\n")
    code = _Code()
    size = 0
    best_paragraph = best_line = best_in_code = 0
    closing = ""
    for number, line in enumerate(lines):
        size += (1 if number else 0) + len(line)
        if size > max_chars:
            break
        was_inside = code.inside
        code.feed(line)
        if code.inside:
            if was_inside and line.strip() and size + 1 + len(code.closing) <= max_chars:  # a line of the block, and room to close it
                best_in_code, closing = number + 1, code.closing
        elif line.strip():
            best_line = number + 1
            if number + 1 < len(lines) and not lines[number + 1].strip():
                best_paragraph = number + 1
    cut = best_paragraph or best_line
    kept = _without_trailing_headings(lines[:cut]) if cut else []
    if kept:
        return Cut("\n".join(kept).rstrip(), True, total)
    if best_in_code:
        return Cut("\n".join([*lines[:best_in_code], closing]), True, total, closing)
    hard = flat[:max_chars]
    if not flat[max_chars].isspace():  # the cut falls inside a word: back to the end of the word before
        space = max(hard.rfind(" "), hard.rfind("\n"), hard.rfind("\t"))
        if space > 0 and hard[:space].strip():
            hard = hard[:space]
    return Cut(hard.rstrip(), True, total)
