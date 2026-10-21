"""Running the command line in-process, with fakes behind it."""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Mapping

from jira_dc_cli.adapters.driving.cli.main import run
from jira_dc_cli.application.app import Application
from jira_dc_cli.application.ports import Diagnostics, Docs
from jira_dc_cli.application.settings import Settings

from .fakes import FakeGateway, MemoryConfigStore, MemoryFiles, build_app


@dataclass
class Result:
    code: int
    out: str
    err: str
    app: Application | None = None

    def json(self) -> Any:
        return json.loads(self.out)

    def error(self) -> dict[str, Any]:
        return json.loads(self.err.strip().splitlines()[-1])["error"]


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def run_cli(
    *argv: str,
    gateway: FakeGateway | None = None,
    files: MemoryFiles | None = None,
    store: MemoryConfigStore | None = None,
    settings: Settings | None = None,
    docs: Docs | None = None,
    stdin: str = "",
    stdin_tty: bool = False,
    environ: Mapping[str, str] | None = None,
) -> Result:
    out, err = io.StringIO(), io.StringIO()
    built: list[Application] = []

    def factory(diagnostics: Diagnostics) -> Application:
        app = build_app(gateway=gateway, files=files, store=store, settings=settings, docs=docs, diagnostics=diagnostics, environ=environ)
        built.append(app)
        return app

    source = _Tty(stdin) if stdin_tty else io.StringIO(stdin)
    code = run(list(argv), app_factory=factory, environ=environ or {}, stdout=out, stderr=err, stdin=source)
    return Result(code, out.getvalue(), err.getvalue(), built[0] if built else None)
