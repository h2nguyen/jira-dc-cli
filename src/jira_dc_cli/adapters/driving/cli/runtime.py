"""What a running command has at hand: the application, the streams and the output mode."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping, TextIO

from ....application.ports import Diagnostics
from .render import dumps_json, to_jsonable
from .spec import Command

if TYPE_CHECKING:
    from ....application.app import Application

AppFactory = Callable[[Diagnostics], "Application"]


class StderrDiagnostics(Diagnostics):
    """Messages for the person (or agent) at the terminal. They go to stderr, never into the result."""

    def __init__(self, stream: TextIO, verbose: bool = False) -> None:
        self._stream = stream
        self._verbose = verbose

    def info(self, message: str) -> None:
        self._stream.write(f"[jira] {message}\n")

    def warn(self, message: str) -> None:
        self._stream.write(f"[jira] WARNING: {message}\n")

    def debug(self, message: str) -> None:
        if self._verbose:
            self._stream.write(f"[jira] debug: {message}\n")


class Context:
    def __init__(
        self,
        *,
        command: Command,
        app_factory: AppFactory,
        out: TextIO,
        err: TextIO,
        stdin: TextIO,
        json_flag: bool,
        verbose: bool,
        environ: Mapping[str, str],
    ) -> None:
        self.command = command
        self.out = out
        self.err = err
        self.stdin = stdin
        self.diagnostics = StderrDiagnostics(err, verbose)
        self._app_factory = app_factory
        self._app: Application | None = None
        self._json_flag = json_flag
        self.environ = environ

    @property
    def app(self) -> Application:
        if self._app is None:
            self._app = self._app_factory(self.diagnostics)
        return self._app

    @property
    def explicit_json(self) -> bool:
        """JSON was asked for on the command line or in the environment; no settings need to be read for that."""
        return self._json_flag or self.environ.get("JIRA_OUTPUT", "").strip().lower() == "json"

    @property
    def json_mode(self) -> bool:
        if not self.command.json_output:
            return False
        if self.explicit_json:
            return True
        return self.command.needs_app and self.app.settings.json_by_default

    def close(self) -> None:
        if self._app is not None:
            self._app.close()

    def print(self, text: str = "") -> None:
        self.out.write(text + "\n")

    def print_json(self, value: Any) -> None:
        """Pretty on a terminal, compact when piped (agents and scripts)."""
        pretty = bool(getattr(self.out, "isatty", lambda: False)())
        self.print(dumps_json(to_jsonable(value), pretty=pretty))

    def read_stdin(self) -> str:
        return self.stdin.read()

    def read_input(self, path: str) -> str:
        """The text of a file, or of standard input when the path is `-`."""
        return self.read_stdin() if path == "-" else self.app.files.read_text(path)
