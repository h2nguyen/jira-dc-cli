#!/usr/bin/env python3
"""Checks that a release is consistent. Run it before tagging a version.

    python scripts/check_release.py

It checks that VERSION, the code, the changelog and the README agree, that the generated reference is
current, that the community files (LICENSE, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY) exist and the README names them,
that the skill names every command, and that every link in the documentation leads somewhere.
Exit code 0: ready. 1: it lists what to fix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_docs  # noqa: E402
from jira_dc_cli.adapters.driving.cli.registry import all_commands  # noqa: E402
from jira_dc_cli.version import __version__  # noqa: E402

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
COMMUNITY_FILES = ("LICENSE", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md")  # at the root, where GitHub looks for them


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def documents() -> list[Path]:
    """Every Markdown document that has to be right: the README, the changelog, the community files, the skill and the guides."""
    community = [ROOT / name for name in COMMUNITY_FILES if name.endswith(".md")]
    return [ROOT / "README.md", ROOT / "CHANGELOG.md", *community, ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md", ROOT / "skills" / "jira-dc-cli" / "SKILL.md", *sorted((ROOT / "docs").rglob("*.md"))]


def version_problems() -> list[str]:
    problems = []
    declared = read(ROOT / "VERSION").strip()
    if not VERSION_PATTERN.match(declared):
        problems.append(f"VERSION must look like 1.2.3, not '{declared}'")
    if declared != __version__:
        problems.append(f"VERSION says {declared}, src/jira_dc_cli/version.py says {__version__}")
    return problems


def changelog_problems() -> list[str]:
    text = read(ROOT / "CHANGELOG.md")
    entries = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", text, re.MULTILINE)
    if not entries:
        return ["CHANGELOG.md has no '## [x.y.z] - YYYY-MM-DD' entry"]
    problems = []
    if entries[0][0] != __version__:
        problems.append(f"the newest CHANGELOG.md entry is {entries[0][0]}, the version is {__version__}")
    if "## [Unreleased]" not in text:
        problems.append("CHANGELOG.md lost its '## [Unreleased]' section")
    versions = [tuple(int(part) for part in version.split(".")) for version, _ in entries]
    if versions != sorted(versions, reverse=True):
        problems.append("CHANGELOG.md entries are not in descending version order")
    return problems


def generated_problems() -> list[str]:
    return [f"{path.relative_to(ROOT)} is out of date: run python scripts/generate_docs.py" for path in generate_docs.out_of_date()]


def skill_problems() -> list[str]:
    path = ROOT / "skills" / "jira-dc-cli" / "SKILL.md"
    if not path.is_file():
        return ["skills/jira-dc-cli/SKILL.md is missing"]
    text = read(path)
    problems = []
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return ["SKILL.md has no front matter"]
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    if fields.get("name", "").strip() != "jira-dc-cli":
        problems.append("SKILL.md front matter needs `name: jira-dc-cli`")
    if len(fields.get("description", "").strip()) < 40:
        problems.append("SKILL.md front matter needs a useful `description`")
    body = text[match.end():]
    problems += [f"SKILL.md does not mention `jira {command.name}`" for command in all_commands() if f"jira {command.name}" not in body]
    return problems


def link_problems() -> list[str]:
    problems = []
    for path in documents():
        if not path.is_file():
            continue  # a missing document is reported by community_problems
        for target in LINK.findall(read(path)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            location = target.split("#", 1)[0]
            if location and not (path.parent / location).exists():
                problems.append(f"{path.relative_to(ROOT)} links to {target}, which does not exist")
    return problems


def community_problems() -> list[str]:
    return [f"{name} is missing at the root of the repository" for name in COMMUNITY_FILES if not (ROOT / name).is_file()]


def readme_problems() -> list[str]:
    text = read(ROOT / "README.md")
    needles = ("python install.py", "jira doctor", "docs/how-to", *COMMUNITY_FILES)
    return [f"README.md does not mention `{needle}`" for needle in needles if needle not in text]


def problems() -> list[str]:
    found: list[str] = []
    for check in (version_problems, changelog_problems, generated_problems, community_problems, readme_problems, skill_problems, link_problems):
        found += check()
    return found


def main() -> int:
    found = problems()
    for problem in found:
        print(f"- {problem}", file=sys.stderr)
    if found:
        print(f"{len(found)} problem(s). Not ready to release.", file=sys.stderr)
        return 1
    print(f"jira-dc-cli {__version__}: consistent, ready to release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
