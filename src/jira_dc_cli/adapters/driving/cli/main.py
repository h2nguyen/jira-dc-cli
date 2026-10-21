"""The command line entry point: from arguments to an exit code."""
from __future__ import annotations

import errno
import json
import os
import sys
from typing import Mapping, Sequence, TextIO

from ....domain.errors import JiraCliError, UsageError
from ....version import __version__
from .help import render_command_help, render_overview
from .registry import all_commands, find_command, suggestions
from .runtime import AppFactory, Context

_CLOSED_PIPE = (errno.EPIPE, errno.EINVAL)


def _wants_help(arguments: Sequence[str]) -> bool:
    return any(item in ("-h", "--help") for item in arguments)


def _error_text(error: JiraCliError) -> str:
    lines = [f"ERROR: {error.message}"]
    if error.hint:
        lines.append(f"Hint: {error.hint}")
    return "\n".join(lines)


def _error_json(error: JiraCliError) -> str:
    body = {
        "code": error.code,
        "message": error.message,
        "hint": error.hint,
        "exitCode": error.exit_code,
        "details": error.details,
    }
    return json.dumps({"error": body}, ensure_ascii=False, default=str, separators=(",", ":"))


def _wants_json(arguments: Sequence[str], environ: Mapping[str, str]) -> bool:
    return "--json" in arguments or environ.get("JIRA_OUTPUT", "").strip().lower() == "json"


def run(
    argv: Sequence[str],
    *,
    app_factory: AppFactory,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    out, err, source = stdout or sys.stdout, stderr or sys.stderr, stdin or sys.stdin
    env = os.environ if environ is None else environ
    arguments = list(argv)
    try:
        return _dispatch(arguments, app_factory, env, out, err, source)
    except JiraCliError as error:
        err.write((_error_json(error) if _wants_json(arguments, env) else _error_text(error)) + "\n")
        return error.exit_code
    except BrokenPipeError:
        return 141  # the reader of our output went away, as in `jira issues | head`
    except KeyboardInterrupt:
        err.write("Interrupted.\n")
        return 130
    except Exception as error:  # a bug: say so plainly, with the traceback only on request
        if isinstance(error, OSError) and error.errno in _CLOSED_PIPE:
            return 141  # Windows reports a closed pipe as EINVAL
        err.write(f"ERROR: unexpected {type(error).__name__}: {error}\n")
        if "-v" in arguments or "--verbose" in arguments:
            import traceback

            err.write(traceback.format_exc())
        else:
            err.write("Hint: run again with --verbose for details and report it if it persists.\n")
        return 1


def _dispatch(
    arguments: list[str], app_factory: AppFactory, env: Mapping[str, str], out: TextIO, err: TextIO, source: TextIO
) -> int:
    commands = all_commands()
    if not arguments or arguments[0] in ("-h", "--help"):
        out.write(render_overview(commands, __version__) + "\n")
        return 0
    if arguments[0] in ("-V", "--version"):
        arguments = ["version", *arguments[1:]]
    name, rest = arguments[0], arguments[1:]
    command = find_command(name)
    if command is None:
        close = suggestions(name)
        raise UsageError(
            f"Unknown command: {name}" + (f". Did you mean: {', '.join(close)}?" if close else "."),
            hint="Run `jira help` for the list of commands.",
        )
    if _wants_help(rest):
        out.write(render_command_help(command) + "\n")
        return 0
    from .parser import build_parser  # argparse is only needed to run a command, not to print help

    namespace = build_parser(command).parse_args(rest)
    context = Context(
        command=command,
        app_factory=app_factory,
        out=out,
        err=err,
        stdin=source,
        json_flag=bool(getattr(namespace, "json", False)),
        verbose=bool(getattr(namespace, "verbose", False)),
        environ=env,
    )
    try:
        return command.handler(context, namespace) or 0
    finally:
        context.close()
