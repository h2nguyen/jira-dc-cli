#!/usr/bin/env python3
"""Installs jira-dc-cli for the current user. Standard library only; nothing else to install.

    python install.py                  install or update
    python install.py --dry-run        show what would happen
    python install.py --uninstall      remove the program (the settings stay)

It copies the program to the local profile (%LOCALAPPDATA%\\jira-dc-cli on Windows, ~/.local/share/jira-dc-cli
elsewhere) and writes the launchers `jira` (Git Bash, Linux, macOS) and `jira.cmd` (cmd and PowerShell) to
~/bin. `~` is HOME in Git Bash; PowerShell and cmd have no HOME, so the home drive and path of Windows is
used, then the profile, the same way as the program itself finds its configuration. The launchers start the
Python that ran this script, so run it with the Python you want to use. The configuration
(~/.config/jira-dc-cli) is never touched unless you ask with --purge.

The installer only replaces or removes what it recognises as its own: an installation folder of this
program and launchers it wrote. Anything else is left alone.
"""
from __future__ import annotations

import argparse
import compileall
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path

APP = "jira-dc-cli"
MINIMUM_PYTHON = (3, 10)
SOURCE = Path(os.path.abspath(__file__)).parent
COPY_DIRECTORIES = ("docs", "skills", "examples")
COPY_FILES = ("jira.py", "install.py", "VERSION", "LICENSE", "README.md", "CHANGELOG.md")
SIGNATURE = f"{APP} launcher, written by install.py"  # the second line of every launcher this installer writes


def absolute(path: str | Path) -> Path:
    """An absolute path as it was written. `Path.resolve()` would turn a mapped network drive (H:\\) into its
    \\\\server\\share form, which then ends up in the launchers."""
    return Path(os.path.abspath(path))


def home_directories() -> list[Path]:
    """The folders that can be the home folder, most specific first. Keep in step with `adapters/driven/paths.py`.

    Git Bash sets HOME (for example H:\\), and then nothing on a network drive is touched. PowerShell and cmd do
    not: Git Bash itself then falls back to the home drive and path of Windows (`H:` and `\\`), which counts when
    both look like that and the folder is there. The profile comes last."""
    found: list[Path] = []
    if os.environ.get("HOME"):
        found.append(Path(os.environ["HOME"]))
    else:
        drive, path = os.environ.get("HOMEDRIVE", ""), os.environ.get("HOMEPATH", "")
        if len(drive) == 2 and drive[0].isalpha() and drive[1] == ":" and path[:1] in ("\\", "/") and Path(drive + path).is_dir():
            found.append(Path(drive + path))
    found.append(Path.home())
    unique: list[Path] = []
    for folder in found:
        if not any(os.path.normcase(os.path.normpath(str(folder))) == os.path.normcase(os.path.normpath(str(seen))) for seen in unique):
            unique.append(folder)
    return unique


def home_directory() -> Path:
    return home_directories()[0]


def default_install_dir() -> Path:
    """Where the program goes. On Windows the local profile (`%LOCALAPPDATA%`): Python reads about a hundred files
    every time it starts, which is three to four times slower on a network home drive such as H:."""
    if os.environ.get("JIRA_CLI_INSTALL_DIR"):
        return Path(os.environ["JIRA_CLI_INSTALL_DIR"])
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / APP
    return home_directory() / ".local" / "share" / APP


def default_bin_dir() -> Path:
    return Path(os.environ["JIRA_CLI_BIN_DIR"]) if os.environ.get("JIRA_CLI_BIN_DIR") else home_directory() / "bin"


def say(message: str = "") -> None:
    print(message)


def fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def source_package() -> Path:
    for candidate in (SOURCE / "src" / "jira_dc_cli", SOURCE / "jira_dc_cli"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("the jira_dc_cli package was not found next to install.py")


def shell_path(path: Path) -> str:
    """A path the shell and Windows both accept: forward slashes."""
    return str(path).replace("\\", "/")


def launchers(python: Path, install_dir: Path, bin_dir: Path, windows: bool) -> dict[Path, tuple[str, str]]:
    """Launcher files: path -> (text, newline). `-I` keeps a stray PYTHONPATH out; `-X utf8` fixes the console encoding."""
    entry = install_dir / "jira.py"
    files: dict[Path, tuple[str, str]] = {
        bin_dir
        / "jira": (
            "#!/bin/sh\n"
            f"# {SIGNATURE}\n"
            f'exec "{shell_path(python)}" -I -X utf8 "{shell_path(entry)}" "$@"\n',
            "\n",
        )
    }
    if windows:
        files[bin_dir / "jira.cmd"] = (
            "@echo off\n"
            f"rem {SIGNATURE}\n"
            f'"{python}" -I -X utf8 "{entry}" %*\n'
            "exit /b %ERRORLEVEL%\n",
            "\r\n",
        )
    return files


def is_installation(path: Path) -> bool:
    """A folder made by this installer."""
    return (path / "jira.py").is_file() and (path / "jira_dc_cli" / "__init__.py").is_file()


def is_ours(path: Path) -> bool:
    """A launcher written by this installer."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return SIGNATURE in text


def points_to(launcher: Path, install_dir: Path) -> bool:
    """Whether a launcher starts the program in `install_dir`."""
    try:
        text = os.path.normcase(launcher.read_text(encoding="utf-8", errors="replace").replace("\\", "/"))
    except OSError:
        return False
    return os.path.normcase(shell_path(install_dir)) in text


def on_path(directory: Path) -> bool:
    wanted = os.path.normcase(os.path.normpath(str(directory)))
    return any(os.path.normcase(os.path.normpath(entry)) == wanted for entry in os.environ.get("PATH", "").split(os.pathsep) if entry)


def sibling_launcher_folder() -> Path | None:
    """A folder on the PATH that holds the launcher of the sibling CLI (`conflu`) and that this user can really write to (a file is created in
    it and removed again: on Windows `os.access` says yes to every folder). The launchers of the two CLIs belong side by side, and a folder
    that a shell already searches for one is searched for the other."""
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        folder = Path(entry) if entry else None
        if folder and any((folder / name).is_file() for name in ("conflu", "conflu.cmd")) and can_write(folder):
            return absolute(folder)
    return None


def can_write(folder: Path) -> bool:
    import tempfile  # only the note at the end of an installation needs it

    try:
        with tempfile.TemporaryFile(dir=str(folder)):
            return True
    except OSError:
        return False


def windows_path_folder() -> Path | None:
    """A folder of the user that is on the PATH of every Windows shell without any setting: where the app
    execution aliases live. Offered as a home for the launchers when `~/bin` is on nobody's PATH but Git Bash's."""
    local = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not local:
        return None
    folder = absolute(Path(local) / "Microsoft" / "WindowsApps")
    return folder if folder.is_dir() and on_path(folder) else None


def unique_sibling(target: Path, label: str) -> Path:
    """A name next to `target` that nobody has: the installer only ever deletes folders it made itself."""
    return target.with_name(f"{target.name}.{label}-{uuid.uuid4().hex[:8]}")


def make_writable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except OSError:
        pass


def remove_file(path: Path) -> None:
    make_writable(path)  # Windows refuses to delete a read-only file
    path.unlink()


def remove_tree(path: Path) -> None:
    """Delete a folder, also when files or folders in it are read-only (Windows refuses otherwise)."""

    def clear_and_retry(function, name, *_unused):
        target = Path(name)
        make_writable(target)
        if path in target.parents:
            make_writable(target.parent)  # on Unix a read-only folder blocks deleting what lies in it; never the folder above `path`
        function(name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=clear_and_retry)
    else:
        shutil.rmtree(path, onerror=clear_and_retry)


def copy_tree(source: Path, target: Path) -> None:
    """Copy the files only: no permissions or attributes (a read-only folder in a synced source must not follow)."""
    target.mkdir(parents=True, exist_ok=True)
    for entry in sorted(source.iterdir()):
        if entry.name == "__pycache__" or entry.suffix == ".pyc":
            continue
        if entry.is_dir():
            copy_tree(entry, target / entry.name)
        else:
            shutil.copyfile(entry, target / entry.name)


def stage(target: Path) -> Path:
    """Build the new installation in a new folder next to the old one, so a failure cannot leave a half-copied program."""
    staging = unique_sibling(target, "new")
    staging.mkdir(parents=True)
    try:
        copy_tree(source_package(), staging / "jira_dc_cli")
        for name in COPY_DIRECTORIES:
            if (SOURCE / name).is_dir():
                copy_tree(SOURCE / name, staging / name)
        for name in COPY_FILES:
            if (SOURCE / name).is_file():
                shutil.copyfile(SOURCE / name, staging / name)
    except BaseException:
        remove_tree(staging)
        raise
    return staging


def swap(staging: Path, target: Path, keep_previous: bool) -> Path | None:
    """Put `staging` in place of `target`. The old content is deleted, unless it is not ours (`keep_previous`):
    then it stays in a folder next to the target, whose path is returned."""
    previous = unique_sibling(target, "old")
    if target.exists():
        target.rename(previous)
    try:
        staging.rename(target)
    except OSError:
        if previous.exists():
            previous.rename(target)  # put the old installation back
        raise
    if not previous.exists():
        return None
    if keep_previous:
        return previous
    remove_tree(previous)
    return None


def check_launchers(files: dict[Path, tuple[str, str]], force: bool) -> None:
    """Refuse before anything changes when a launcher's place is taken by something else."""
    for path in files:
        if path.exists() and not is_ours(path) and not force:
            raise FileExistsError(f"{path} exists and is not a {APP} launcher; use --force to replace it")


def write_launcher(path: Path, text: str, newline: str) -> None:
    """Write next to the launcher and rename over it, so it is never half written, also when the old one is read-only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = unique_sibling(path, "tmp")
    with open(temporary, "w", encoding="utf-8", newline=newline) as stream:
        stream.write(text)
    try:
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    if path.exists():
        make_writable(path)
    os.replace(temporary, path)


def installed_version(python: Path, install_dir: Path) -> str:
    done = subprocess.run([str(python), "-I", "-X", "utf8", str(install_dir / "jira.py"), "version"], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip() or f"exit code {done.returncode}")
    return done.stdout.strip()


def install(args: argparse.Namespace) -> int:
    if sys.version_info < MINIMUM_PYTHON:
        return fail(f"Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer is needed; this is {sys.version.split()[0]}.")
    python = absolute(args.python or sys.executable)
    install_dir = absolute(args.prefix) if args.prefix else absolute(default_install_dir())
    bin_dir = absolute(args.bin_dir) if args.bin_dir else absolute(default_bin_dir())
    windows = os.name == "nt"
    try:
        version = (SOURCE / "VERSION").read_text(encoding="utf-8").strip()
        source_package()
    except OSError as error:
        return fail(f"this does not look like a source checkout of {APP}: {error}")
    if install_dir == SOURCE:
        return fail("the installation folder is the source folder; choose another with --prefix.")
    foreign = install_dir.exists() and any(install_dir.iterdir()) and not is_installation(install_dir)
    if foreign and not args.force:
        return fail(f"{install_dir} holds something that is not an installation of {APP}; choose another folder with --prefix, or use --force.")
    files = launchers(python, install_dir, bin_dir, windows)
    try:
        check_launchers(files, args.force)
    except FileExistsError as error:
        return fail(str(error))

    say(f"{APP} {version}")
    say(f"  Python:      {python} ({sys.version.split()[0]})")
    say(f"  Install to:  {install_dir}")
    say(f"  Launchers:   {', '.join(str(path) for path in files)}")
    if foreign:
        say("  Moves aside: what is in that folder now; it is kept next to it, not deleted")
    if args.dry_run:
        say("Dry run: nothing was changed.")
        return 0

    try:
        staging = stage(install_dir)
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        kept = swap(staging, install_dir, keep_previous=foreign)
        compileall.compile_dir(str(install_dir / "jira_dc_cli"), quiet=1, force=True)
        for path, (text, newline) in files.items():
            write_launcher(path, text, newline)
        found = installed_version(python, install_dir)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        return fail(f"the installation failed: {error}")
    if found != f"{APP} {version}":
        return fail(f"the installed program reports '{found}', expected '{APP} {version}'.")

    say(f"Installed {found}.")
    if kept:
        say(f"The previous content of the folder was kept in {kept}.")
    if not on_path(bin_dir):
        say(f"Note: {bin_dir} is not on the PATH of this shell, so a shell that does not search it will not find `jira`.")
        say("  Call the launcher by its full path, or put the folder on your PATH:")
        say('  Git Bash:   echo \'export PATH="$HOME/bin:$PATH"\' >> ~/.bashrc')
        say("  PowerShell: add the folder under Settings > System > About > Advanced system settings > Environment Variables")
        shared_folders = [(sibling_launcher_folder(), "the folder that holds the launcher of conflu"), (windows_path_folder(), "a folder that every Windows shell searches")]
        shown: list[Path] = []
        for folder, why in shared_folders:
            if folder and folder != bin_dir and folder not in shown:
                shown.append(folder)
                say(f"  Or install the launchers into {why}:")
                say(f'    python install.py --bin-dir "{folder}"')
    say("Next: `jira setup-pat`, then `jira config set JIRA_BASE_URL https://jira.example.com/jira`, then `jira doctor`.")
    return 0


def purge_targets() -> tuple[list[Path], list[Path]]:
    """What `--purge` removes: the files, and the folders to try afterwards.

    The files are the configuration file and the cache files (`*.json`) of this program, in every place it looks
    (see `home_directories`). The folders are the standard ones named `jira-dc-cli` that nothing else lives in once
    those files are gone; they are removed with `rmdir`, so a file that appears meanwhile keeps its folder. The
    folder of a `JIRA_CONFIG_FILE` kept elsewhere is never touched, whatever it is called."""
    homes = home_directories()
    explicit = os.environ.get("JIRA_CONFIG_FILE")
    if explicit:
        config_files, config_folders = [Path(explicit)], []
    else:
        config_files = [Path(os.environ["XDG_CONFIG_HOME"]) / APP / "config"] if os.environ.get("XDG_CONFIG_HOME") else []
        config_files += [home / ".config" / APP / "config" for home in homes]
        config_folders = [path.parent for path in config_files]
    cache_folders = [Path(os.environ["XDG_CACHE_HOME"]) / APP] if os.environ.get("XDG_CACHE_HOME") else []
    cache_folders += [home / ".cache" / APP for home in homes]

    files = list(dict.fromkeys(path for path in config_files if path.is_file()))
    for folder in cache_folders:
        if folder.name == APP and folder.is_dir():
            files += [entry for entry in sorted(folder.iterdir()) if entry.is_file() and entry.suffix == ".json" and entry not in files]
    candidates = dict.fromkeys(folder for folder in (*config_folders, *cache_folders) if folder.name == APP and folder.is_dir())
    return files, [folder for folder in candidates if all(entry in files for entry in folder.iterdir())]


def uninstall(args: argparse.Namespace) -> int:
    install_dir = absolute(args.prefix) if args.prefix else absolute(default_install_dir())
    bin_dir = absolute(args.bin_dir) if args.bin_dir else absolute(default_bin_dir())
    files = [path for path in (bin_dir / "jira", bin_dir / "jira.cmd") if path.is_file() and is_ours(path) and points_to(path, install_dir)]
    program = install_dir if install_dir.is_dir() and is_installation(install_dir) else None
    if program is None and install_dir.exists():
        say(f"Not removing {install_dir}: it does not look like an installation of {APP}.")
    folders: list[Path] = []
    if args.purge:
        purge_files, folders = purge_targets()
        files += purge_files
    if program is not None:  # what lies inside the program folder goes with it
        files = [path for path in files if program not in path.parents]
        folders = [folder for folder in folders if folder != program and program not in folder.parents]
    if not files and not folders and program is None:
        say("Nothing to remove.")
        return 0
    verb = "Would remove" if args.dry_run else "Removing"
    for path in files:
        say(f"{verb} {path}")
        if not args.dry_run and os.path.lexists(path):
            remove_file(path)
    if program is not None:
        say(f"{verb} {program}")
        if not args.dry_run:
            remove_tree(program)
    for folder in folders:
        say(f"{verb} {folder}")
        if not args.dry_run:
            try:
                folder.rmdir()
            except OSError:
                pass  # something arrived in it meanwhile: it stays
    if not args.purge:
        say("The configuration was kept. Add --purge to delete it, with the cache.")
    return 0


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install or remove jira-dc-cli for the current user.")
    parser.add_argument("--prefix", help="installation folder (default: JIRA_CLI_INSTALL_DIR, else %%LOCALAPPDATA%%\\jira-dc-cli on Windows, else ~/.local/share/jira-dc-cli)")
    parser.add_argument("--bin-dir", help="folder for the launchers (default: JIRA_CLI_BIN_DIR, else bin in the home folder: HOME, else the Windows home drive and path, else the profile)")
    parser.add_argument("--python", help="interpreter the launchers start (default: the one running this script)")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen and change nothing")
    parser.add_argument("--force", action="store_true", help="replace a launcher that is not from this program, and move a foreign folder aside")
    parser.add_argument("--uninstall", action="store_true", help="remove the program and its launchers")
    parser.add_argument("--purge", action="store_true", help="with --uninstall: also delete the configuration and the cache")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    return uninstall(args) if args.uninstall else install(args)


if __name__ == "__main__":
    raise SystemExit(main())
