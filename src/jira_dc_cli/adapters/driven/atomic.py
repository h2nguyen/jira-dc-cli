"""Writing a file so that it is either the old content or the complete new content, never half of it."""
from __future__ import annotations

import os
from pathlib import Path


def write_text_atomically(path: Path, text: str, *, private: bool = False) -> None:
    """UTF-8 with plain `\\n` line ends, missing folders created. See `write_bytes_atomically`."""
    write_bytes_atomically(path, text.encode("utf-8"), private=private)


def write_bytes_atomically(path: Path, data: bytes, *, private: bool = False) -> None:
    """Missing folders created. A temporary file in the same folder is written, flushed to disk and renamed over the
    target; it is removed again when anything fails. `private` keeps the file readable for its owner only (a file that
    holds the token). Raises OSError."""
    import tempfile  # only a write needs it

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_name, 0o600 if private else 0o666 & ~_umask())  # mkstemp creates 0600; a normal file follows the umask
        except OSError:
            pass  # not every file system has Unix permissions
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def write_bytes_exclusively(path: Path, data: bytes) -> None:
    """The file with all of `data`, created only when nothing is at `path`: `FileExistsError` when something is there, also when it
    appeared a moment ago (a second run into the same folder), and then what is there is left as it was. The data is written to a
    temporary file in the same folder first and linked to the target, so the target is never seen half written, and never replaced
    (`write_bytes_atomically` replaces). Where the file system has no hard links the file is created in place with `x`, which is just
    as exclusive. Missing folders are created. Raises OSError."""
    import errno
    import tempfile  # only a write needs it

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError as error:  # a file where a folder should be: that is not the target that exists
        raise NotADirectoryError(errno.ENOTDIR, "Not a directory", str(path.parent)) from error
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temp_name, 0o666 & ~_umask())
        except OSError:
            pass  # not every file system has Unix permissions
        try:
            os.link(temp_name, path)
        except FileExistsError:
            raise
        except OSError:  # hard links are not available here (FAT, some network drives)
            _create_in_place(path, data)
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass


def _create_in_place(path: Path, data: bytes) -> None:
    """The exclusive creation without a hard link: a write that fails removes what was created."""
    stream = open(path, "xb")  # FileExistsError, before anything is created
    try:
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
