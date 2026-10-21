"""Markdown to Jira wiki markup.

A conservative, dependency-free converter for the common constructs. It protects generated Jira
markup from later passes, and it never touches front matter: that is import metadata.
"""
from __future__ import annotations

import re

from .errors import ValidationError


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
    value = sub(r"`([^`\n]+)`", lambda m: "{{" + m.group(1) + "}}", value)
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
