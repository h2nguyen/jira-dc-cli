"""The composition root: the one place that knows the real adapters and wires them to the application.

Nothing else imports from here. The command line adapter gets an `app_factory` and never learns
which adapters are behind it, so tests hand it a factory with fakes instead.

The adapters are imported when the application is created, not when this module is loaded: a command
that needs no application (`help`, `version`) then starts without loading files, HTTP or TLS code.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .adapters.driving.cli.main import run
from .application.app import Application
from .application.ports import Diagnostics
from .application.settings import SETTING_DEFS, Settings
from .version import __version__


def effective_values(stored: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """The config file overlaid with the environment: a variable set in the shell wins. An empty variable counts as
    not set, so `export JIRA_HIDE_USERS=` cannot switch off a policy that the file sets."""
    values = dict(stored)
    values.update({definition.name: environ[definition.name] for definition in SETTING_DEFS if environ.get(definition.name)})
    return values


def create_application(diagnostics: Diagnostics, environ: Mapping[str, str] | None = None) -> Application:
    from .adapters.driven.file_cache import FileCache
    from .adapters.driven.file_config import FileConfigStore
    from .adapters.driven.http_transport import HttpTransport
    from .adapters.driven.jira_rest import RestJiraGateway
    from .adapters.driven.local_docs import LocalDocs, find_root
    from .adapters.driven.paths import cache_directory, config_file
    from .adapters.driven.system import LocalFiles, SystemClock

    env = os.environ if environ is None else environ
    store = FileConfigStore(config_file(env))
    settings = Settings.from_mapping(effective_values(store.load(), env))
    transport = HttpTransport(settings, debug=diagnostics.debug)
    cache = FileCache(cache_directory(env))
    gateway = RestJiraGateway(settings, transport, cache)
    return Application.create(
        settings=settings,
        gateway=gateway,
        store=store,
        files=LocalFiles(),
        clock=SystemClock(),
        docs=LocalDocs(),
        diagnostics=diagnostics,
        environ=env,
        runtime=runtime_facts(env, find_root()),
        closers=(transport.close,),
        cache=cache,
    )


def runtime_facts(environ: Mapping[str, str], root: Path) -> dict[str, str]:
    """What `jira doctor` says about the program itself: the version, the Python, whether `jira` is found by name on the PATH
    (and from where this run was started), and the installer that can put it there. The installer is named only in a source checkout:
    the copy inside an installation refuses to install over itself."""
    launcher = shutil.which("jira", path=environ.get("PATH")) or ""
    installer = root / "install.py"
    source_checkout = (root / "src" / "jira_dc_cli").is_dir()
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "launcher": launcher,
        "program": sys.argv[0] if sys.argv else "",
        "install_script": str(installer) if source_checkout and installer.is_file() else "",
    }


def _use_utf8(stream: TextIO, **options: str) -> None:
    """UTF-8 and plain `\\n` on every platform, so `$(jira branch KEY)` has no stray carriage return."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace", **options)


def _silence_stdout() -> None:
    """Point stdout at the null device, so the flush at exit cannot fail on a closed pipe."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _flush_quietly() -> int:
    """Flush at the end; a reader that closed early (`| head`) must not end in a traceback."""
    try:
        sys.stdout.flush()
        return 0
    except OSError:
        _silence_stdout()
        return 141


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8(sys.stdout, newline="\n")
    _use_utf8(sys.stderr, newline="\n")
    _use_utf8(sys.stdin)
    code = run(sys.argv[1:] if argv is None else argv, app_factory=create_application)
    if code == 141:
        _silence_stdout()
        return code
    return code or _flush_quietly()
