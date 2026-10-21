"""A small file cache for lists that rarely change (fields, issue types). Best effort: errors are ignored."""
from __future__ import annotations

import re
import time
from pathlib import Path

from ...application.ports import Cache


class FileCache(Cache):
    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def _file(self, namespace: str, key: str) -> Path:
        return self._directory / (re.sub(r"[^A-Za-z0-9_.-]", "_", f"{namespace}-{key}") + ".json")

    def get(self, namespace: str, key: str, max_age: float) -> str | None:
        path = self._file(namespace, key)
        try:
            if time.time() - path.stat().st_mtime > max_age:
                return None
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def put(self, namespace: str, key: str, value: str) -> None:
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            self._file(namespace, key).write_text(value, encoding="utf-8")
        except OSError:
            pass
