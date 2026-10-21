"""Writing a file so that it is either the old content or the complete new content, never half of it."""
from __future__ import annotations

import os
from pathlib import Path


def write_text_atomically(path: Path, text: str, *, private: bool = False) -> None:
    """UTF-8 with plain `\\n` line ends, missing folders created. A temporary file in the same folder is
    written, flushed to disk and renamed over the target; it is removed again when anything fails.
    `private` keeps the file readable for its owner only (a file that holds the token). Raises OSError."""
    import tempfile  # only a write needs it

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
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
