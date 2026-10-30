"""Markdown to Jira wiki markup.

A conservative, dependency-free converter for the common constructs. It protects generated Jira
markup from later passes, and it never touches front matter: that is import metadata.
"""
from __future__ import annotations

import re

from .errors import ValidationError


def _code_span(match: re.Match[str]) -> str:
    """`code` as `{{code}}`. Jira does not read `{{` or `}}` with a blank next to them as monospace, so a blank inside the backticks
    goes outside the braces, a span of blanks is only blanks, and a brace in the code is escaped."""
    inner = match.group(1)
    core = inner.strip()
    if not core:
        return inner
    lead, trail = inner[: len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()) :]
    return lead + "{{" + core.replace("{", "\\{").replace("}", "\\}") + "}}" + trail


def _inline(value: str) -> str:
    """Convert inline Markdown without letting one conversion affect another."""
    tokens: list[str] = []

    def protect(rendered: str) -> str:
        token = f"\x00JIRA{len(tokens)}\x00"
        tokens.append(rendered)
        return token

    def sub(pattern: str, renderer, source: str) -> str:
        return re.sub(pattern, lambda match: protect(renderer(match)), source)

    # Every pattern below is anchored or bounded: a hostile line of 32,000 characters must not make one of them quadratic or worse.
    value = sub(r"`([^`\n]+)`", _code_span, value)
    # Images and links are protected before emphasis is converted.
    value = sub(
        r"!\[([^\]]{0,500})\]\(([^)\s]{1,1000})(?:\s+[\"'][^\"']*[\"'])?\)",
        lambda m: "!" + m.group(2) + "!",
        value,
    )
    value = sub(
        r"\[([^\]]{1,500})\]\(([^)\s]{1,1000})(?:\s+[\"'][^\"']*[\"'])?\)",
        lambda m: "[" + m.group(1) + "|" + m.group(2) + "]",
        value,
    )
    # Strong text before emphasis, so generated *bold* is not read as Markdown italic.
    value = sub(r"\*\*([^*\n]+)\*\*", lambda m: "*" + m.group(1) + "*", value)
    value = sub(r"__([^_\n]+)__", lambda m: "*" + m.group(1) + "*", value)
    value = sub(r"~~([^~\n]+)~~", lambda m: "-" + m.group(1) + "-", value)
    value = sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: "_" + m.group(1) + "_", value)
    value = sub(r"(?<!_)_([^_\n]+)_(?!_)", lambda m: "_" + m.group(1) + "_", value)

    # A later token can hold an earlier one (bold around code): the last is put back first, so that none is left.
    for index in range(len(tokens) - 1, -1, -1):
        value = value.replace(f"\x00JIRA{index}\x00", tokens[index])
    return value


def _heading(line: str) -> tuple[int, str] | None:
    """`# Title ##` as (level, title), or None. The same lines as `^(#{1,6})\\s+(.+?)\\s*#*\\s*$`, without its backtracking on a long run of blanks."""
    level = len(line) - len(line.lstrip("#"))
    rest = line[level:]
    if not 1 <= level <= 6 or not rest or not rest[0].isspace():
        return None
    body = rest.strip()
    if not body:
        return (level, rest[-1]) if len(rest) >= 2 else None  # the pattern reads the last blank as the title
    title = body.rstrip("#").rstrip()
    return level, title if title else body[:1]


def markdown_to_wiki(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\x00", " ")  # a NUL would be taken for a protected piece of text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ValidationError("missing closing --- in front matter")
        text = text[end + 5 :]

    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        fence = re.match(r"^\s*```(.*)$", line)
        if fence and "`" not in fence.group(1):
            if not in_fence:
                language = fence.group(1).strip()
                out.append("{code" + (":" + language if language else "") + "}")
                in_fence = True
            else:
                out.append("{code}")
                in_fence = False
            continue
        if in_fence:
            out.append(line)
            continue

        heading = _heading(line)
        if heading:
            out.append(f"h{heading[0]}. {_inline(heading[1])}")
            continue
        if re.match(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", line):
            out.append("----")
            continue
        bullet = re.match(r"^(\s*)[-+*]\s+(.*)$", line)
        if bullet:
            level = max(1, len(bullet.group(1).expandtabs(2)) // 2 + 1)
            out.append("*" * level + " " + _inline(bullet.group(2)))
            continue
        numbered = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
        if numbered:
            level = max(1, len(numbered.group(1).expandtabs(2)) // 2 + 1)
            out.append("#" * level + " " + _inline(numbered.group(2)))
            continue
        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            out.append("bq. " + _inline(quote.group(1)))
            continue
        out.append(_inline(line))

    if in_fence:  # be forgiving about an unclosed fence
        out.append("{code}")
    return "\n".join(out).rstrip() + "\n"


_CHECKS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"^\s*\|.*\|\s*$"), "a Markdown table is not converted: Jira reads `| a | b |` as a table row, but its header row is written `|| a || b ||`, and the `---` row becomes a row of dashes"),
    (re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s"), "a task list item is not converted: Jira shows the brackets as text, there is no check box"),
    (re.compile(r"^\s*(?:=+|-+)\s*$"), ""),  # a setext underline: decided in the loop, because it needs the line before it
    (re.compile(r"\[[^\]\n]{1,300}\]\[[^\]\n]{0,100}\]|^\[[^\]\n]{1,300}\]:\s*\S"), "a reference-style link is not converted: write the link in the form [text](address)"),
    (re.compile(r"\[\^[^\]\n]{1,100}\]"), "a footnote is not converted and shows as text"),
    (re.compile(r"</?(?:a|b|br|code|details|div|em|font|h[1-6]|hr|i|img|li|ol|p|pre|span|strong|sub|summary|sup|table|td|th|tr|u|ul)\b[^<>\n]*>", re.IGNORECASE), "an HTML tag is not rendered: Jira shows it as text"),
    (re.compile(r"^\s*>\s*>"), "a nested block quote is not converted: Jira has one level of `bq.`"),
)
_BRACE = re.compile(r"(?<!\\)[{}]")
_FENCE_LINE = re.compile(r"^\s*```([^`]*)$")
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_MAX_CHECKED_LINE = 5000  # a longer line is no prose: checking it costs more than it tells, and a hostile one must not make a pattern slow


def check_markdown(text: str) -> list[tuple[int, str]]:
    """What Jira is known not to render the way the Markdown meant it, as (line number, what), for `jira wiki --check`.
    Code (fences and backtick spans) and front matter (import metadata, which the conversion drops) are left alone; a brace outside
    code can start a Jira macro (`{code}`, `{color}`)."""
    normalized = text.replace("\r\n", "\n")
    front_matter = 0
    if normalized.startswith("---\n") and normalized.find("\n---\n", 4) >= 0:
        front_matter = normalized[: normalized.find("\n---\n", 4) + 5].count("\n")
    problems: list[tuple[int, str]] = []
    in_fence = False
    previous = ""
    for number, line in enumerate(normalized.split("\n"), start=1):
        if number <= front_matter:
            continue
        if _FENCE_LINE.match(line):
            in_fence = not in_fence
            previous = ""  # a rule after a fence is no underline of the fence
            continue
        if in_fence:
            continue
        if len(line) > _MAX_CHECKED_LINE:
            problems.append((number, f"the line is too long to check ({len(line)} characters)"))
            previous = ""
            continue
        prose = _CODE_SPAN.sub("", line)
        for pattern, message in _CHECKS:
            if not pattern.search(prose):
                continue
            if message == "":  # a setext underline: only when a line of text stands above it
                if previous.strip() and not previous.lstrip().startswith(("-", "*", "+", "#", ">", "|")) and len(line.strip()) >= 2:
                    problems.append((number, "an underlined heading is not converted: write it as `# Title`"))
            else:
                problems.append((number, message))
        if _BRACE.search(prose):
            problems.append((number, "a brace outside code can start a Jira macro: put the text in a code span, or write `\\{`"))
        previous = line
    return problems
