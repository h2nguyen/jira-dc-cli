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
        raise UsageError(message, hint=usage_hint(self._command, message))


def _has_flag(command: Command, flag: str) -> bool:
    return any(flag in arg.names for arg in command.args)


def usage_hint(command: Command, message: str) -> str:
    """What to do after a usage error. For `unrecognized arguments` more: the command that has an option that this one lacks
    (`issues` has no --type, `search` has), and what to do when the text of a query lost its double quotes."""
    hint = f"Run `jira help {command.name}` for the usage and examples."
    if not message.startswith("unrecognized arguments"):
        return hint
    from .registry import all_commands

    leftovers = message.partition(":")[2].split()
    flags = [token for token in leftovers if token.startswith("--") and len(token) > 2]
    for flag in flags:
        owners = [other.name for other in all_commands() if other.name != command.name and _has_flag(other, flag)]
        if owners:
            hint += f" `{flag}` is not an option of `jira {command.name}`; " + " and ".join(f"`jira {name}`" for name in owners[:3]) + " has it."
    if not flags and any(not token.startswith("-") for token in leftovers):
        if _has_flag(command, "--jql"):
            how = "put the query in a file and pass it in single quotes, --jql '@query.jql' (or `-` for standard input)"
        elif _has_flag(command, "--phrase"):
            how = "put the text in a file and pass it in single quotes, '@question.txt', or give each phrase with --phrase 'exact words'"
        else:
            how = ""  # a command that takes no query: a stray word is only a stray word
        if how:
            hint += (
                " If a query or text held double quotes, Windows PowerShell 5.1 removed them and split it into several arguments: "
                f"{how}, or write each double quote as \\\"."
            )
    return hint


class _Words(argparse.Action):
    """The words that stand there, as one text: `jira search audit trail` is the text `audit trail`. The words as they came (the
    boundaries of the arguments) stay in `<name>_words`, for a command that must tell `"a b"` from `a b` (a path with a blank)."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, " ".join(values) if isinstance(values, list) else values)
        if isinstance(values, list):
            setattr(namespace, f"{self.dest}_words", list(values))


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
    if arg.kind == "repeat":
        parser.add_argument(*arg.names, dest=arg.dest, action="append", metavar=arg.label, default=arg.default, required=arg.required, help=arg.help)
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
    parser.add_argument("--json-envelope", dest="json_envelope", action="store_true")
    return parser
