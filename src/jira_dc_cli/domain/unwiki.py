"""Jira wiki markup to Markdown, the other direction of `wiki.markdown_to_wiki`.

A conservative converter for the constructs people write in a description: headings, lists, bold, italic, strike-through,
monospace, links, images, code and quote blocks, tables, rules. `dropped` names what the Markdown copy cannot carry back to
Jira unchanged (a table, a panel, a colour, a macro, ...): a text that holds such a construct must not replace the Jira text
from the copy.

Written for fields of Jira's size (32,767 characters by default): every pattern is bounded, so a hostile text costs time in
proportion to its length. A text beyond `MAX_INLINE_CHARS` keeps its blocks but gets no inline conversion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_INLINE_CHARS = 300_000
_SPAN = 400  # the longest bold, italic or code span that is converted

_HEADING = re.compile(r"^h([1-6])\.[ \t]+(.*)$")  # the trailing blanks are taken off in code: `(.*?)[ \t]*$` is quadratic on a long run of blanks
_RULE = re.compile(r"^-{4,}[ \t]*$")
_LIST = re.compile(r"^([*#-]+)[ \t]+(\S.*)$")
_BQ = re.compile(r"^bq\.[ \t]+(.*)$")
_TABLE_ROW = re.compile(r"^\|.*\|[ \t]*$")
_CODE_OPEN = re.compile(r"^\{code(?::([^}]*))?\}(.*)$")
_NOFORMAT_OPEN = re.compile(r"^\{noformat(?::[^}]*)?\}(.*)$")
_QUOTE_MACRO = re.compile(r"^\{quote\}[ \t]*$")
_PANEL_OPEN = re.compile(r"^\{panel(?::([^}]*))?\}[ \t]*$")
_PANEL_CLOSE = re.compile(r"^\{panel\}[ \t]*$")

_CODE = re.compile(r"\{\{([^{}\n]{1,%d})\}\}" % _SPAN)
_LINK = re.compile(r"\[([^\[\]|\n]{1,%d})\|([^\[\]|\n]{1,%d})(\|[^\[\]\n]{0,%d})?\]" % (_SPAN, _SPAN, _SPAN))
_IMAGE = re.compile(r"(?<![\w!])!([^\s!|]{1,%d}\.[A-Za-z0-9]{2,5}|https?://[^\s!|]{1,%d})(\|[^!\n]{0,%d})?!(?![\w!])" % (_SPAN, _SPAN, _SPAN))
_COLOR = re.compile(r"\{color(?::[^}\n]{0,40})?\}([^\n]{0,%d}?)\{color\}" % _SPAN)
_ANCHOR = re.compile(r"\{anchor(?::[^}\n]{0,80})?\}")
_MACRO = re.compile(r"\{([A-Za-z][\w-]{0,40})(?::[^{}\n]{0,200})?\}")
_BOLD = re.compile(r"(?<![\w*\\])\*([^\s*](?:[^*\n]{0,%d}[^\s*])?)\*(?![\w*])" % _SPAN)
_ITALIC = re.compile(r"(?<![\w_\\])_([^\s_](?:[^_\n]{0,%d}[^\s_])?)_(?![\w_])" % _SPAN)
_STRIKE = re.compile(r"(?<![\w\-\\])-([^\s\-](?:[^\-\n]{0,%d}[^\s\-])?)-(?![\w\-])" % _SPAN)
_UNDERLINE = re.compile(r"(?<![\w+\\])\+([^\s+](?:[^+\n]{0,%d}[^\s+])?)\+(?![\w+])" % _SPAN)
_CITATION = re.compile(r"\?\?([^\s?](?:[^?\n]{0,%d}[^\s?])?)\?\?" % _SPAN)
_SUPERSCRIPT = re.compile(r"\^([^\s^\\]{1,40})\^")
_SUBSCRIPT = re.compile(r"~([^\s~\\]{1,40})~")
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


@dataclass(frozen=True)
class Converted:
    text: str
    dropped: tuple[str, ...] = ()


class _Inline:
    """Inline conversion of one line. A converted piece is put aside, so that a later pass cannot read it again."""

    def __init__(self, dropped: set[str]) -> None:
        self._dropped = dropped
        self._parts: list[str] = []

    def _keep(self, rendered: str) -> str:
        self._parts.append(rendered)
        return f"\x00{len(self._parts) - 1}\x00"

    def _plain(self, name: str):
        def render(match: re.Match[str]) -> str:
            self._dropped.add(name)
            return match.group(1)

        return render

    def _marks(self, value: str, depth: int) -> str:
        """Bold, italic and the other marks of a text; the text inside a mark is converted too, down to three levels."""

        def inner(text: str) -> str:
            return self._marks(text, depth + 1) if depth < 3 else text

        value = _BOLD.sub(lambda m: self._keep("**" + inner(m.group(1)) + "**"), value)
        value = _ITALIC.sub(lambda m: self._keep("*" + inner(m.group(1)) + "*"), value)
        value = _STRIKE.sub(lambda m: self._keep("~~" + inner(m.group(1)) + "~~"), value)
        value = _UNDERLINE.sub(self._plain("underline"), value)
        value = _CITATION.sub(self._plain("citation"), value)
        value = _SUPERSCRIPT.sub(self._plain("superscript"), value)
        return _SUBSCRIPT.sub(self._plain("subscript"), value)

    def convert(self, line: str) -> str:
        value = line.replace("\x00", " ")
        value = _CODE.sub(lambda m: self._keep("`" + m.group(1) + "`"), value)

        def link(match: re.Match[str]) -> str:
            target = match.group(2).strip()
            if match.group(3):
                self._dropped.add("link title")
            if re.search(r"[()\s]", target):
                self._dropped.add("link")
                return match.group(0)
            return self._keep(f"[{match.group(1).strip()}]({target})")

        value = _LINK.sub(link, value)

        def image(match: re.Match[str]) -> str:
            if match.group(2):
                self._dropped.add("image options")
            return self._keep(f"![]({match.group(1)})")

        value = _IMAGE.sub(image, value)
        value = _COLOR.sub(self._plain("color"), value)
        value = _ANCHOR.sub(lambda m: (self._dropped.add("anchor"), "")[1], value)
        value = _MACRO.sub(lambda m: (self._dropped.add(f"macro {m.group(1)}"), "")[1], value)
        value = self._marks(value, 0)
        for _ in range(6):  # a kept piece can hold another one (bold around a link, italic inside bold)
            if "\x00" not in value:
                break
            value = _PLACEHOLDER.sub(lambda m: self._parts[int(m.group(1))], value)
        return value


def _split_cells(row: str) -> tuple[bool, list[str]]:
    """The cells of a table row, and whether it is a header row (`||a||b||`). A `|` inside `[...]` or `{...}` is no separator."""
    body = row.strip()
    header = body.startswith("||")
    pieces: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char in "[{":
            depth += 1
        elif char in "]}" and depth:
            depth -= 1
        if char == "|" and depth == 0:
            pieces.append("".join(current))
            current = []
            index += 1
            while index < len(body) and body[index] == "|":
                index += 1
            continue
        current.append(char)
        index += 1
    pieces.append("".join(current))
    inner = pieces[1:-1] if len(pieces) >= 2 else pieces  # a row begins and ends with a separator
    return header, [cell.strip() for cell in inner]


def wiki_to_markdown(text: str) -> Converted:
    """The Markdown of a Jira wiki text, and what it could not carry (`dropped`)."""
    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    dropped: set[str] = set()
    inline = _Inline(dropped).convert if len(source) <= MAX_INLINE_CHARS else (lambda line: line)
    if len(source) > MAX_INLINE_CHARS:
        dropped.add("large text")
    out: list[str] = []
    lines = source.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # the line break that ends the text is no line of its own
    counters: dict[int, int] = {}
    index = 0

    def end_list() -> None:
        counters.clear()

    while index < len(lines):
        line = lines[index]
        index += 1
        code = _CODE_OPEN.match(line)
        plain_block = _NOFORMAT_OPEN.match(line)
        if code or plain_block:
            end_list()
            language = ""
            if plain_block:
                dropped.add("noformat")
            if code and code.group(1):
                params = code.group(1)
                found = re.search(r"(?:^|\|)language=([A-Za-z0-9+#_-]+)", params)
                language = found.group(1) if found else (params if re.fullmatch(r"[A-Za-z0-9+#_-]+", params) else "")
                if "title=" in params:
                    dropped.add("code title")
            first = (code or plain_block).group(2 if code else 1)
            body: list[str] = [first] if first.strip() and not re.fullmatch(r"\{(?:code|noformat)\}", first.strip()) else []
            closed = bool(first.strip()) and bool(re.search(r"\{(?:code|noformat)\}[ \t]*$", first))
            if closed:
                body = [re.sub(r"\{(?:code|noformat)\}[ \t]*$", "", first)]
            while not closed and index < len(lines):
                current = lines[index]
                index += 1
                if re.fullmatch(r"\{(?:code|noformat)\}[ \t]*", current):
                    closed = True
                    break
                body.append(current)
            longest = max((len(run) for item in body for run in re.findall(r"`+", item)), default=0)
            fence = "`" * max(3, longest + 1)  # longer than any run of backticks inside, found in one pass
            out += [fence + language, *body, fence]
            continue
        if _QUOTE_MACRO.match(line):
            end_list()
            dropped.add("quote block")
            while index < len(lines) and not _QUOTE_MACRO.match(lines[index]):
                out.append("> " + inline(lines[index]) if lines[index].strip() else ">")
                index += 1
            index += 1
            continue
        panel = _PANEL_OPEN.match(line)
        if panel:
            end_list()
            dropped.add("panel")
            title = re.search(r"(?:^|\|)title=([^|]+)", panel.group(1) or "")
            if title:
                out.append("> **" + title.group(1).strip() + "**")
            while index < len(lines) and not _PANEL_CLOSE.match(lines[index]):
                out.append("> " + inline(lines[index]) if lines[index].strip() else ">")
                index += 1
            index += 1
            continue
        if _TABLE_ROW.match(line):
            end_list()
            dropped.add("table")
            rows = [line]
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                rows.append(lines[index])
                index += 1
            parsed = [_split_cells(row) for row in rows]
            width = max((len(cells) for _, cells in parsed), default=0)
            header_cells = parsed[0][1] if parsed[0][0] else [""] * width
            body_rows = parsed[1:] if parsed[0][0] else parsed

            def row_text(cells: list[str]) -> str:
                padded = cells + [""] * (width - len(cells))
                return "| " + " | ".join(inline(cell).replace("|", "\\|") for cell in padded) + " |"

            out += [row_text(header_cells), "| " + " | ".join(["---"] * width) + " |"]
            out += [row_text(cells) for _, cells in body_rows]
            continue
        heading = _HEADING.match(line)
        if heading:
            end_list()
            out.append("#" * int(heading.group(1)) + " " + inline(heading.group(2).rstrip(" \t")))
            continue
        if _RULE.match(line):
            end_list()
            out.append("---")
            continue
        item = _LIST.match(line)
        if item:
            marks, content = item.group(1), item.group(2)
            depth = len(marks)
            if "#" in marks and ("*" in marks or "-" in marks):
                dropped.add("mixed list")
            for deeper in [level for level in counters if level > depth]:
                del counters[deeper]
            indent = "  " * (depth - 1)
            if marks[-1] == "#":
                counters[depth] = counters.get(depth, 0) + 1
                out.append(f"{indent}{counters[depth]}. {inline(content)}")
            else:
                counters.pop(depth, None)
                out.append(f"{indent}- {inline(content)}")
            continue
        end_list()
        quote = _BQ.match(line)
        if quote:
            out.append("> " + inline(quote.group(1)))
            continue
        out.append(inline(line))
    return Converted("\n".join(out).rstrip() + "\n" if out else "", tuple(sorted(dropped)))


_PLAIN_RULES = (
    (re.compile(r"^```[^\n]*$", re.MULTILINE), ""),
    (re.compile(r"^[ \t]*\|(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*$", re.MULTILINE), ""),
    (re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE), ""),
    (re.compile(r"^[ \t]*(?:[-*]|\d{1,9}\.)[ \t]+", re.MULTILINE), ""),
    (re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE), ""),
    (re.compile(r"!\[[^\]\n]{0,%d}\]\([^)\n]{0,%d}\)" % (_SPAN, _SPAN)), ""),
    (re.compile(r"\[([^\]\n]{0,%d})\]\([^)\n]{0,%d}\)" % (_SPAN, _SPAN)), r"\1"),
    (re.compile(r"\*\*|~~|`"), ""),
    (re.compile(r"(?<![\w*])\*([^\s*][^*\n]{0,%d}?)\*(?![\w*])" % _SPAN), r"\1"),
    (re.compile(r"\|"), " "),
    (re.compile(r"[ \t]{2,}"), " "),  # after the pipes: a run of blanks is one blank (a pattern `[ \t]*\|[ \t]*` is quadratic on a long run)
)


def wiki_to_text(text: str) -> str:
    """The words of a Jira wiki text without its markup, for a short passage or a search: one line per paragraph, no marks."""
    flat = wiki_to_markdown(text).text
    for pattern, replacement in _PLAIN_RULES:
        flat = pattern.sub(replacement, flat)
    return "\n".join(line.strip() for line in flat.split("\n") if line.strip())
