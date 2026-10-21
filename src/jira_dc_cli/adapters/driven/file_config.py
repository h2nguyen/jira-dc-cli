"""The settings file: `KEY=value` lines, quoted the way a shell quotes them.

The file is parsed as data and never executed. Quoting follows the shell (`'a b'`, `"a b"`, `a\\ b`).
What a shell would expand (`$(...)`, backticks, `$'...'`, `$VAR`) is refused instead of being guessed,
unless it sits inside single quotes or is escaped, where the shell takes it literally too.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from ...application.ports import ConfigStore
from ...domain.errors import ConfigError
from .atomic import write_text_atomically

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_EXPANSION = re.compile(r"\$[(\'{A-Za-z0-9_@*#?!$-]")


def _expands(text: str) -> bool:
    """True when a shell would substitute something in this value."""
    in_single = in_double = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_single:
            in_single = char != "'"
        elif char == "\\":
            index += 1  # the next character is literal
        elif char == "#" and not in_double and (index == 0 or text[index - 1].isspace()):
            break  # the rest is a comment
        elif char == "~" and not in_double and (index == 0 or text[index - 1] == ":"):
            return True  # the home folder
        elif char == "`" or (char == "$" and _EXPANSION.match(text, index)):
            return True
        elif char == "'" and not in_double:
            in_single = True
        elif char == '"':
            in_double = not in_double
        index += 1
    return False


def parse_value(name: str, text: str) -> str:
    if _expands(text):
        raise ConfigError(
            f"{name} uses shell syntax that the CLI does not execute.",
            hint="Write the value itself in the file (in single quotes if it holds a $ or a ~), or set it as an environment variable.",
        )
    try:
        tokens = shlex.split(text, comments=True, posix=True)
    except ValueError as error:
        raise ConfigError(f"{name} has unbalanced quotes in the config file.", hint="Fix the line or run `jira config set`.") from error
    if len(tokens) > 1:
        raise ConfigError(f"{name} has several words without quotes in the config file.", hint="Put the value in quotes.")
    return tokens[0] if tokens else ""


class FileConfigStore(ConfigStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> str:
        return str(self._path)

    def load(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in self._lines():
            match = _ASSIGNMENT.match(line)
            if match:
                values[match.group(1)] = parse_value(match.group(1), match.group(2))
        return values

    def set(self, key: str, value: str) -> None:
        lines = [line for line in self._lines() if not self._assigns(line, key)]
        lines.append(f"{key}={shlex.quote(value)}")
        self._write(lines)

    def unset(self, key: str) -> bool:
        lines = self._lines()
        kept = [line for line in lines if not self._assigns(line, key)]
        if len(kept) == len(lines):
            return False
        self._write(kept)
        return True

    @staticmethod
    def _assigns(line: str, key: str) -> bool:
        match = _ASSIGNMENT.match(line)
        return bool(match and match.group(1) == key)

    def _lines(self) -> list[str]:
        try:
            return self._path.read_text(encoding="utf-8-sig").splitlines()
        except FileNotFoundError:
            return []
        except OSError as error:
            raise ConfigError(f"Cannot read the config file {self._path}: {error}") from error

    def _write(self, lines: list[str]) -> None:
        text = "\n".join(lines) + ("\n" if lines else "")
        try:
            write_text_atomically(self._path, text, private=True)
        except OSError as error:
            raise ConfigError(f"Cannot write the config file {self._path}: {error}") from error
