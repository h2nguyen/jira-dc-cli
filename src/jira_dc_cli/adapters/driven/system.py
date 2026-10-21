"""The file system and the clock."""
from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path

from ...application.ports import Clock, Files
from ...domain.errors import ValidationError
from .atomic import write_text_atomically


class LocalFiles(Files):
    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        try:
            return Path(path).read_text(encoding=encoding)
        except OSError as error:
            raise ValidationError(f"Cannot read {path}: {error.strerror or error}") from error
        except UnicodeDecodeError as error:
            raise ValidationError(f"{path} is not valid {encoding} text.") from error

    def write_text(self, path: str, text: str) -> None:
        try:
            write_text_atomically(Path(path), text)
        except OSError as error:
            raise ValidationError(f"Cannot write {path}: {error.strerror or error}") from error

    def append_text(self, path: str, text: str) -> None:
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
        except OSError as error:
            raise ValidationError(f"Cannot write {path}: {error.strerror or error}") from error

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def list_matching(self, directory: str, pattern: str) -> list[str]:
        return [
            str(entry)
            for entry in sorted(Path(directory).iterdir(), key=lambda entry: entry.name)
            if entry.is_file() and fnmatch.fnmatch(entry.name, pattern)
        ]

    def join(self, *parts: str) -> str:
        return os.path.join(*parts)

    def name(self, path: str) -> str:
        return os.path.basename(path)


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
