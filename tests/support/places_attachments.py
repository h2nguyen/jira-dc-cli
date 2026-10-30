"""Files and a configuration store for the tests of what must never be written over, into or attached: places, links and a race."""
from __future__ import annotations

from typing import Mapping

from .fakes import MemoryConfigStore, MemoryFiles


class PlacesFiles(MemoryFiles):
    """Files in memory that know where a path leads, as the real ones do (`ResolvesPaths`).

    `a/../b` and `./b` are one place, a relative path is one below `/work`, `links` maps a path or a folder to what it points to, and
    with `ignore_case` `B` and `b` are one file. Every operation goes by the place, so a file can be reached under another spelling:
    that is what a link or a relative path is."""

    def __init__(self, files: Mapping[str, str] | None = None, *, links: Mapping[str, str] | None = None, ignore_case: bool = False) -> None:
        super().__init__(files)
        self.links = dict(links or {})
        self.ignore_case = ignore_case

    def resolve(self, path: str) -> str:
        for link, target in self.links.items():
            if path == link or path.startswith(link.rstrip("/") + "/"):
                path = target + path[len(link) :]
                break
        if not path.startswith("/"):
            path = "/work/" + path
        parts: list[str] = []
        for part in path.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        place = "/" + "/".join(parts)
        return place.lower() if self.ignore_case else place

    def _stored(self) -> list[str]:
        return [*self.files, *self._blobs()]

    def _key(self, path: str) -> str:
        """The name under which a file at this place is stored, else the path as it is."""
        place = self.resolve(path)
        return next((key for key in self._stored() if self.resolve(key) == place), path)

    def put(self, path: str, data: bytes) -> None:
        """A file that is there before the test starts, not written by the code under test."""
        MemoryFiles.write_bytes(self, self._key(path), data)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return super().read_text(self._key(path), encoding)

    def write_text(self, path: str, text: str) -> None:
        super().write_text(self._key(path), text)

    def read_bytes(self, path: str) -> bytes:
        return super().read_bytes(self._key(path))

    def write_bytes(self, path: str, data: bytes) -> None:
        super().write_bytes(self._key(path), data)

    def file_size(self, path: str) -> int:
        return super().file_size(self._key(path))

    def exists(self, path: str) -> bool:
        place = self.resolve(path)
        return any(self.resolve(key) == place for key in self._stored()) or self.is_dir(path)

    def is_dir(self, path: str) -> bool:
        place = self.resolve(path).rstrip("/") + "/"
        return any(self.resolve(key).startswith(place) for key in self._stored())


class ExclusiveFiles(PlacesFiles):
    """The same, and a file can also be created only when nothing is there (`CreatesExclusively`), as the real ones can.

    `arrivals` maps a path to the bytes that somebody else writes there at the moment the code under test creates it: the race that a check
    before a write cannot see. `created` and `replaced` are the paths that were created exclusively and written over."""

    def __init__(self, files: Mapping[str, str] | None = None, **options: object) -> None:
        super().__init__(files, **options)  # type: ignore[arg-type]
        self.arrivals: dict[str, bytes] = {}
        self.created: list[str] = []
        self.replaced: list[str] = []

    def create_bytes(self, path: str, data: bytes) -> None:
        if path in self.arrivals:
            self.put(path, self.arrivals.pop(path))  # somebody else was faster
        if self.exists(path):
            raise FileExistsError(path)
        self.created.append(path)
        PlacesFiles.write_bytes(self, path, data)

    def write_bytes(self, path: str, data: bytes) -> None:
        self.replaced.append(path)
        super().write_bytes(path, data)


class ConfigStoreAt(MemoryConfigStore):
    """A configuration store that lives at the path that the test chooses."""

    def __init__(self, path: str, values: Mapping[str, str] | None = None) -> None:
        super().__init__(values)
        self._path = path

    @property
    def path(self) -> str:
        return self._path
