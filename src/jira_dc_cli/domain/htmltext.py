"""The HTML that Jira renders (`renderedFields`, `renderedBody`) as plain text that still shows its structure.

Lists keep their bullets and numbers, code keeps its fences or backticks, headings keep their level. It is for reading what a
person will see, so that a text that Jira renders wrongly (raw `{{` in place of monospace) is visible. Standard library only; the
work is linear in the size of the input.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCKS = {"p", "div", "section", "article", "blockquote", "table", "tr", "ul", "ol", "dl", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
_HEADINGS = {f"h{level}": level for level in range(1, 7)}
_SKIP = {"script", "style", "head"}
MAX_NESTING = 20
MAX_INPUT = 2_000_000  # characters; what is more is not read
_MARKER = re.compile(r"(?:^|\n)[ ]*(?:-|\d+\.) $")  # a list marker with nothing after it yet
# A `<` that does not open a tag, a closing tag or a comment within reach. The parser of the standard library takes a very long time over
# a document made of unterminated tags, so such a `<` is made text before the parser sees it.
_STRAY = re.compile(r"<(?![A-Za-z/!][^<>]{0,2000}>)")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[list[int]] = []  # [number of the next item or 0 for a bullet list]
        self.pre = 0
        self.skip = 0
        self.href: list[str] = []
        self.cell = 0  # the number of cells of this row so far
        self.in_cell = 0  # inside a td or th: the row stays on one line
        self.compact = 0  # inside a list item: a break between paragraphs is a line break

    def _break(self, count: int = 1) -> None:
        if self.in_cell:
            return
        text = "".join(self.parts[-3:])
        if _MARKER.search(text):
            return  # a paragraph inside a list item stays on the line of its marker
        count = 1 if self.compact else count
        have = len(text) - len(text.rstrip("\n"))
        if self.parts and have < count:
            self.parts.append("\n" * (count - have))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in _SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag in ("ul", "ol"):
            self._break()
            if len(self.lists) < MAX_NESTING:
                self.lists.append([1 if tag == "ol" else 0])
        elif tag == "li":
            self._break()
            depth = max(len(self.lists) - 1, 0)
            marker = "-"
            if self.lists and self.lists[-1][0]:
                marker = f"{self.lists[-1][0]}."
                self.lists[-1][0] += 1
            self.parts.append("  " * depth + marker + " ")
            self.compact += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "pre":
            self._break(2)
            self.pre += 1
            self.parts.append("```\n")
        elif tag in ("code", "tt") and not self.pre:
            self.parts.append("`")
        elif tag in _HEADINGS:
            self._break(2)
            self.parts.append("#" * _HEADINGS[tag] + " ")
        elif tag == "a":
            self.href.append(values.get("href") or "")
        elif tag == "img":
            alt = values.get("alt") or values.get("title") or ""
            self.parts.append(f"[image: {alt}]" if alt else "[image]")
        elif tag in ("td", "th"):
            if self.cell:
                self.parts.append(" | ")
            self.cell += 1
            self.in_cell += 1
        elif tag == "tr":
            self.in_cell = 0
            self._break()
            self.cell = 0
        elif tag in _BLOCKS:
            self._break(2 if tag in ("p", "blockquote", "table") else 1)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(self.skip - 1, 0)
        elif self.skip:
            return
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._break(2 if not self.lists else 1)
        elif tag == "li":
            self.compact = max(self.compact - 1, 0)
            self._break()
        elif tag in ("td", "th"):
            self.in_cell = max(self.in_cell - 1, 0)
        elif tag == "pre":
            self._break()
            self.parts.append("```")
            self.pre = max(self.pre - 1, 0)
            self._break(2)
        elif tag in ("code", "tt") and not self.pre:
            self.parts.append("`")
        elif tag in _HEADINGS:
            self._break(2)
        elif tag == "a":
            href = self.href.pop() if self.href else ""
            if href and not href.startswith("#"):
                self.parts.append(f" ({href})")
        elif tag in ("tr", "table"):
            self.in_cell = 0  # a cell that was not closed does not swallow the breaks that follow the row
            self._break(2 if tag == "table" else 1)
        elif tag in ("p", "blockquote"):
            self._break(2)
        elif tag in _BLOCKS:
            self._break()

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self.parts.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        written = "".join(self.parts[-2:])
        if not written or written[-1] in "\n ":
            text = text.lstrip()  # a blank at the start of a line, or after a bullet, is only the indentation of the HTML
        self.parts.append(text)


def html_to_text(html: str) -> str:
    """Plain text with the structure of the HTML: bullets, numbers, fences, heading marks and table cells separated by ` | `."""
    parser = _Text()
    parser.feed(_STRAY.sub("&lt;", (html or "")[:MAX_INPUT]))
    parser.close()
    lines = [line.rstrip() for line in "".join(parser.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
