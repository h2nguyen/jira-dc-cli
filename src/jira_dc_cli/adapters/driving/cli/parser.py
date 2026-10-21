"""Argument parsing on `argparse`, driven by the command descriptions."""
from __future__ import annotations

import argparse
from typing import NoReturn

from ....domain.errors import UsageError
from .spec import Arg, Command


class _Parser(argparse.ArgumentParser):
    def __init__(self, command: Command) -> None:
        super().__init__(prog=f"jira {command.name}", add_help=False, allow_abbrev=False)
        self._command = command

    def error(self, message: str) -> NoReturn:
        raise UsageError(message, hint=f"Run `jira help {self._command.name}` for the usage and examples.")


class _Words(argparse.Action):
    """The words that stand there, as one text: `jira search audit trail` is the text `audit trail`."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, " ".join(values) if isinstance(values, list) else values)


def _add(parser: argparse.ArgumentParser, arg: Arg) -> None:
    if arg.positional and arg.kind == "words":
        parser.add_argument(arg.dest, nargs="*", action=_Words, metavar=arg.label, default=arg.default, help=arg.help)
        return
    if arg.positional:
        parser.add_argument(
            arg.dest,
            nargs=None if arg.required else "?",
            metavar=arg.label,
            choices=arg.choices or None,
            default=arg.default,
            help=arg.help,
        )
        return
    if arg.kind == "flag":
        parser.add_argument(*arg.names, dest=arg.dest, action="store_true", help=arg.help)
        return
    kind = {"int": int, "float": float}.get(arg.kind, str)
    parser.add_argument(
        *arg.names,
        dest=arg.dest,
        type=kind,
        metavar=arg.label,
        choices=arg.choices or None,
        default=arg.default,
        required=arg.required,
        help=arg.help,
    )


def build_parser(command: Command) -> argparse.ArgumentParser:
    parser = _Parser(command)
    for arg in command.args:
        _add(parser, arg)
    if command.json_output:
        parser.add_argument("--json", dest="json", action="store_true")
    parser.add_argument("-v", "--verbose", dest="verbose", action="store_true")
    return parser
