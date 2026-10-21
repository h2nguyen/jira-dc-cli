"""Where the CLI keeps its files, the same way in Git Bash, PowerShell and cmd.

Git Bash sets HOME (for example to H:\\); PowerShell and cmd do not, and Python's own idea of the home folder
is the Windows profile. Without HOME, Git for Windows takes the home drive and path of the user
(`HOMEDRIVE` + `HOMEPATH`), so those come next and the profile last. The settings file is
`$HOME/.config/jira-dc-cli/config`: an existing file is found in whichever of these folders holds it, and a new
file goes into the first one. That way a configuration made in Git Bash is the one PowerShell uses, and the cache
lives next to the configuration in use.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

APP = "jira-dc-cli"


def _windows_home(environ: Mapping[str, str]) -> Path | None:
    """The home drive and path of Windows (`H:` and `\\`), when both look like that and the folder exists."""
    drive, path = environ.get("HOMEDRIVE", ""), environ.get("HOMEPATH", "")
    if not (len(drive) == 2 and drive[0].isalpha() and drive[1] == ":" and path[:1] in ("\\", "/")):
        return None
    folder = Path(drive + path)
    return folder if folder.is_dir() else None


def home_directories(environ: Mapping[str, str]) -> list[Path]:
    """The folders that can be the home folder of the user, most specific first, each once.

    `HOME` is trusted as it is, and then nothing else on a network drive is touched. Without it the home drive
    and path of Windows counts when its folder is there (a missing one is skipped), then the profile."""
    found: list[Path] = []
    if environ.get("HOME"):
        found.append(Path(environ["HOME"]))
    else:
        mapped = _windows_home(environ)
        if mapped is not None:
            found.append(mapped)
    found.append(Path.home())
    unique: list[Path] = []
    seen: set[str] = set()
    for folder in found:
        key = os.path.normcase(os.path.normpath(str(folder)))
        if key not in seen:
            seen.add(key)
            unique.append(folder)
    return unique


def _config_in(home: Path) -> Path:
    return home / ".config" / APP / "config"


def active_home(environ: Mapping[str, str]) -> Path:
    """The home folder in use: the first that holds a configuration, else the first one (a new file goes there)."""
    homes = home_directories(environ)
    return next((home for home in homes if _config_in(home).is_file()), homes[0])


def config_file(environ: Mapping[str, str]) -> Path:
    """`JIRA_CONFIG_FILE`, else the first existing candidate (XDG, then the home folders), else the first candidate."""
    explicit = environ.get("JIRA_CONFIG_FILE")
    if explicit:
        return Path(explicit)
    xdg = Path(environ["XDG_CONFIG_HOME"]) / APP / "config" if environ.get("XDG_CONFIG_HOME") else None
    if xdg is not None and xdg.is_file():
        return xdg
    in_home = _config_in(active_home(environ))
    return xdg if xdg is not None and not in_home.is_file() else in_home


def cache_directory(environ: Mapping[str, str]) -> Path:
    """Next to the configuration in use, so the cache is not put on a network drive when the configuration is local."""
    if environ.get("XDG_CACHE_HOME"):
        return Path(environ["XDG_CACHE_HOME"]) / APP
    return active_home(environ) / ".cache" / APP
