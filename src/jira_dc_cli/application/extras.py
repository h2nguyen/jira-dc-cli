"""Use cases around the shipped documentation, the skill, Markdown conversion and raw API calls."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..domain.errors import UsageError, ValidationError
from ..domain.privacy import strip_users
from ..domain.wiki import check_markdown, markdown_to_wiki
from .ports import DocTopic, Docs, Files, JiraGateway
from .settings import Settings

SKILL_FOLDER = "jira-dc-cli"
SKILL_FILE = "SKILL.md"


class ReadDocs:
    def __init__(self, docs: Docs) -> None:
        self._docs = docs

    def topics(self) -> list[DocTopic]:
        return self._docs.topics()

    def topic(self, name: str) -> str:
        return self._docs.topic(name)

    def readme(self) -> str:
        return self._docs.readme()

    def changelog(self) -> str:
        return self._docs.changelog()

    def skill(self) -> str:
        return self._docs.skill()


@dataclass(frozen=True)
class SkillInstall:
    path: str
    dry_run: bool
    action: str  # installed | replaced | unchanged


class InstallSkill:
    """Copies the skill that ships with the CLI into a folder of agent skills."""

    def __init__(self, docs: Docs, files: Files) -> None:
        self._docs = docs
        self._files = files

    def execute(self, directory: str, *, dry_run: bool = False, force: bool = False) -> SkillInstall:
        target = self._files.join(directory, SKILL_FOLDER, SKILL_FILE)
        skill = self._docs.skill()
        existing = self._files.read_text(target) if self._files.exists(target) else None
        if existing == skill:
            return SkillInstall(target, dry_run, "unchanged")
        if existing is not None and not force:
            raise ValidationError(
                f"{target} exists and differs from the skill of this version (an older one, or one you changed).",
                hint="Use --force to replace it.",
            )
        if not dry_run:
            self._files.write_text(target, skill)
        return SkillInstall(target, dry_run, "installed" if existing is None else "replaced")


class ConvertMarkdown:
    def execute(self, text: str) -> str:
        return markdown_to_wiki(text).rstrip()

    def check(self, text: str) -> list[tuple[int, str]]:
        """What Jira is known not to render as the Markdown meant it: (line number, what)."""
        return check_markdown(text)


@dataclass(frozen=True)
class RawResult:
    method: str
    path: str
    dry_run: bool
    body: Any = None
    response: Any = None


class RawApi:
    """An escape hatch for what no command covers yet. GET is free; writing must be asked for."""

    METHODS = ("GET", "POST", "PUT")

    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, method: str, path: str, body_text: str = "", *, write: bool = False, dry_run: bool = False) -> RawResult:
        method = method.upper()
        if method not in self.METHODS:
            raise UsageError(f"Method {method} is not supported.", hint="Use GET, POST or PUT. DELETE is deliberately not offered.")
        if method != "GET" and not write:
            raise UsageError(f"{method} changes Jira.", hint="Add --write to confirm, and preview with --dry-run first.")
        if not path.strip():
            raise UsageError("A path is required.", hint="Example: issue/PROJ-1 or rest/agile/1.0/board")
        path = path if path.startswith("/") else "/" + path
        body: Any = None
        if body_text.strip():
            try:
                body = json.loads(body_text)
            except ValueError as error:
                raise ValidationError(
                    f"The request body is not valid JSON: {error}",
                    hint="Windows PowerShell 5.1 removes the double quotes inside an argument. Put the body in a file and pass --data-file FILE.",
                ) from error
        if method != "GET" and body is None:
            raise UsageError(f"{method} needs a body.", hint="Pass --data JSON or --data-file FILE.")
        if dry_run:
            return RawResult(method, path, True, body)
        response = self._gateway.request(method, path, body)
        return RawResult(method, path, False, body, strip_users(response) if self._settings.hide_users else response)
