"""The documentation and skill that ship with the CLI, read from the installation or the source tree."""
from __future__ import annotations

import os
from pathlib import Path

from ...application.ports import DocTopic, Docs
from ...domain.errors import NotFoundError

SKILL_PATH = Path("skills") / "jira-dc-cli" / "SKILL.md"


def find_root() -> Path:
    """The folder that holds VERSION and the docs: the repository, or the installation folder."""
    override = os.environ.get("JIRA_CLI_HOME")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "VERSION").is_file() and ((parent / "docs").is_dir() or (parent / "README.md").is_file()):
            return parent
    return here.parents[3]


def _title(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return path.stem


class LocalDocs(Docs):
    def __init__(self, root: Path | None = None) -> None:
        self._root = root or find_root()

    def _read(self, relative: str | Path) -> str:
        path = self._root / relative
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise NotFoundError(
                f"The documentation file {relative} was not found under {self._root}.",
                hint="Reinstall the CLI (python install.py) or set JIRA_CLI_HOME to its folder.",
            ) from error

    def _topic_files(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for folder in ("how-to", "reference"):
            directory = self._root / "docs" / folder
            if directory.is_dir():
                for entry in sorted(directory.glob("*.md")):
                    found[entry.stem] = entry
        architecture = self._root / "docs" / "architecture.md"
        if architecture.is_file():
            found["architecture"] = architecture
        return found

    def topics(self) -> list[DocTopic]:
        return [DocTopic(name, _title(path)) for name, path in sorted(self._topic_files().items())]

    def topic(self, name: str) -> str:
        files = self._topic_files()
        wanted = name.strip().casefold().removesuffix(".md")
        for stem, path in files.items():
            if stem.casefold() == wanted:
                return path.read_text(encoding="utf-8")
        raise NotFoundError(f"No guide named '{name}'.", hint="List them with `jira how-to`.")

    def readme(self) -> str:
        return self._read("README.md")

    def changelog(self) -> str:
        return self._read("CHANGELOG.md")

    def skill(self) -> str:
        return self._read(SKILL_PATH)
