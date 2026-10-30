#!/usr/bin/env python3
"""Writes the reference documentation from the command descriptions, so the docs cannot drift.

    python scripts/generate_docs.py            write docs/reference/*.md and the command table in README.md
    python scripts/generate_docs.py --check    fail when a generated file is out of date (CI, release check)

The command table in README.md sits between the markers `<!-- commands:start -->` and
`<!-- commands:end -->`; everything else in the README is written by hand.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jira_dc_cli.adapters.driving.cli.help import commands_markdown, exit_codes_markdown, render_compact_reference, settings_markdown  # noqa: E402
from jira_dc_cli.adapters.driving.cli.registry import all_commands  # noqa: E402
from jira_dc_cli.adapters.driving.cli.spec import GROUP_ORDER  # noqa: E402
from jira_dc_cli.version import __version__  # noqa: E402

START, END = "<!-- commands:start -->", "<!-- commands:end -->"
SKILL = ROOT / "skills" / "jira-dc-cli" / "SKILL.md"
REFERENCE_START, REFERENCE_END = "<!-- reference:start -->", "<!-- reference:end -->"
SKILL_REFERENCE = ("comment", "api", "create", "update", "pull", "diff")  # the verbs whose flags an agent used `help` for most


def command_table() -> str:
    lines: list[str] = []
    for group in GROUP_ORDER:
        members = [command for command in all_commands() if command.group == group]
        if not members:
            continue
        lines += [f"**{group}**", "", "| Command | What it does |", "| --- | --- |"]
        lines += [f"| `jira {command.name}` | {command.summary} |" for command in members]
        lines.append("")
    return "\n".join(lines).rstrip()


def with_command_table(readme: str) -> str:
    if START not in readme or END not in readme:
        raise SystemExit(f"README.md needs the markers {START} and {END} around the command table.")
    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n\n{command_table()}\n\n{END}{tail}"


def with_compact_reference(skill: str) -> str:
    """The skill carries the arguments and all options of the verbs an agent looked up most, between its markers."""
    if REFERENCE_START not in skill or REFERENCE_END not in skill:
        raise SystemExit(f"{SKILL.relative_to(ROOT)} needs the markers {REFERENCE_START} and {REFERENCE_END} around the compact reference.")
    head, rest = skill.split(REFERENCE_START, 1)
    _, tail = rest.split(REFERENCE_END, 1)
    block = render_compact_reference(all_commands(), SKILL_REFERENCE)
    return f"{head}{REFERENCE_START}\n\n```text\n{block}\n```\n\n{REFERENCE_END}{tail}"


def generated() -> dict[Path, str]:
    commands = all_commands()
    files = {
        ROOT / "docs" / "reference" / "commands.md": commands_markdown(commands, __version__),
        ROOT / "docs" / "reference" / "configuration.md": settings_markdown(),
        ROOT / "docs" / "reference" / "exit-codes.md": exit_codes_markdown(),
    }
    readme = ROOT / "README.md"
    files[readme] = with_command_table(readme.read_text(encoding="utf-8"))
    files[SKILL] = with_compact_reference(SKILL.read_text(encoding="utf-8"))
    return files


def out_of_date() -> list[Path]:
    stale = []
    for path, text in generated().items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            stale.append(path)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only check; exit 1 when something is out of date")
    args = parser.parse_args(argv)
    if args.check:
        stale = out_of_date()
        for path in stale:
            print(f"out of date: {path.relative_to(ROOT)}", file=sys.stderr)
        if stale:
            print("Run: python scripts/generate_docs.py", file=sys.stderr)
        return 1 if stale else 0
    for path, text in generated().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
