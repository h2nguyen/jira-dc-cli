"""From the tool call of an MCP client to a command line, and from what the command printed to a tool result.

A tool is one entry of `help.mcp_tools(...)`: its `inputSchema` says what a call may hold and `_meta.jira` how the properties become a
command line. A call is untrusted: a model wrote it, perhaps after reading text that a stranger put into a ticket. So it is checked before
it runs and written so that a value stays a value. It cannot turn into an option of the command, cannot read a file of this computer
through `@FILE`, and cannot ask for people unless the server was started to allow that. Which properties name a path, accept `@FILE` or
overwrite is written down below, by command and argument, and a test over the real commands fails for an argument that looks like a path
and is in no table: nothing is guessed from the English of a help text.

A path property that a call leaves out can still decide where a command works: `download` without `out` writes below `tmp/`, and an import
without a map file adds to a file beside its Markdown. Those places are written down too (`DEFAULT_PLACES`), so that they are judged like a
path that the call gave, and the command is given the place that was judged.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ....application.attachments import OUT_FOLDER
from ....application.imports import MAP_FILE_NAME
from ....domain.errors import JiraCliError
from ....domain.model import parse_issue_key
from .help import mcp_property_names
from .spec import Command

MAX_RESULT_CHARS = 100_000
WITH_USERS = "--with-users"
_TYPE_NAMES = {"string": "a string", "integer": "an integer", "number": "a number", "boolean": "true or false"}

# -- what a property does with its value: named once, by command and argument (its first name) -------------------------

READS_FILE = "reads a file"
READS_FILES = "reads files"  # several, separated by blanks
READS_FOLDER = "reads a folder"
READS_FILE_OR_FOLDER = "reads a file or a folder"
WRITES_FILE = "writes a file"  # a new one
WRITES_FOLDER = "writes into a folder"
WRITES_BELOW_FOLDER = "writes into a folder below the working folder"  # files whose names a stranger chose: not into the folder itself
UPDATES_FILE = "reads a file and adds to it"  # the map file of an import: it may exist
PATH_KINDS = (READS_FILE, READS_FILES, READS_FOLDER, READS_FILE_OR_FOLDER, WRITES_FILE, WRITES_FOLDER, WRITES_BELOW_FOLDER, UPDATES_FILE)

PATHS: dict[tuple[str, str], str] = {
    ("comment", "--file"): READS_FILE,
    ("transition", "--comment-file"): READS_FILE,
    ("update", "--description-file"): READS_FILE,
    ("create", "--description-file"): READS_FILE,
    ("update-json", "file"): READS_FILE,
    ("create-json", "file"): READS_FILE,
    ("import-md", "file"): READS_FILE,
    ("import-csv", "file"): READS_FILE,
    ("attach", "files"): READS_FILES,
    ("attach", "--path"): READS_FILE,  # one file, exactly as written
    ("import-md-dir", "directory"): READS_FOLDER,  # the map file lies beside its Markdown files, under a name of the CLI: see DEFAULT_PLACES
    ("import-md-dir", "--map-file"): UPDATES_FILE,
    ("diff", "directory"): READS_FOLDER,
    ("push", "target"): READS_FILE_OR_FOLDER,  # the sync record goes back into the file that was sent
    ("export", "--out"): WRITES_FILE,
    ("download", "--out"): WRITES_BELOW_FOLDER,  # the names and the bytes of the files come from the author of the ticket
    ("pull", "directory"): WRITES_FOLDER,
}
# `@FILE` as the value reads a file of this computer (`-` reads standard input, which is empty here).
AT_FILE: frozenset[tuple[str, str]] = frozenset(
    {
        ("issues", "--jql"),
        ("search", "text"),
        ("search", "--jql"),
        ("users", "query"),
        ("transition", "--fields-json"),
        ("update", "--fields-json"),
        ("create", "--fields-json"),
    }
)
# A flag that replaces what exists: a file, or a change that somebody else made. The user decides that, not a model.
OVERWRITES: frozenset[tuple[str, str]] = frozenset({("download", "--force"), ("pull", "--force"), ("push", "--force")})
# What looks like a path to a reader of the tables, and is none: the reason is part of the entry.
NOT_A_PATH: dict[tuple[str, str], str] = {
    ("attachments", "--name"): "a file name to look for among the attachments of the issue",
    ("download", "--name"): "a file name to look for among the attachments of the issue",
    ("comment", "--edit"): "the id of a comment",
    ("diff", "--issue"): "the keys of the issues to compare",
    ("pull", "--issue"): "the keys of the issues to pull",
    ("push", "--issue"): "the keys of the issues to push",
}


@dataclass(frozen=True)
class DefaultPlace:
    """Where a command works when a call leaves a path property out, and how that place is worked out. A place that the policy did not judge
    would be an open door: a link at `tmp`, or a stray file under the name of the map, takes the command anywhere (a download without `out`
    wrote a stranger's files outside the working folder, an import without a map file added a row to a README). So the place is worked out
    here, judged as a path that the call gave is, and handed to the command as the value of the property."""

    made_of: tuple[str, ...]  # the arguments (first names) that the place is made of: one that the call lacks leaves nothing to work out
    build: Callable[[Mapping[str, Any]], str]  # from the values of those arguments, as the command works it out
    unused_with: str = ""  # a flag with which the command works at no such place and takes no value for it (`download --text` writes no file, and refuses `--out`)


def _download_folder(values: Mapping[str, Any]) -> str:
    return os.path.join(*OUT_FOLDER, parse_issue_key(str(values["key"])))


def _map_beside(values: Mapping[str, Any]) -> str:
    return os.path.join(str(values["directory"]), MAP_FILE_NAME)


# The path properties that have a default place. A test over the real commands holds this table, `NO_DEFAULT_PLACE` and the real behaviour
# of the commands together: an optional property that names a path is in one of them, and the place is the one that the command uses.
DEFAULT_PLACES: dict[tuple[str, str], DefaultPlace] = {
    ("download", "--out"): DefaultPlace(("key",), _download_folder, unused_with="--text"),
    ("import-md-dir", "--map-file"): DefaultPlace(("directory",), _map_beside),
}
# The optional path properties that have none: what the command does without them, so that it touches no file of its own choosing.
NO_DEFAULT_PLACE: dict[tuple[str, str], str] = {
    ("attach", "files"): "the files are those of `path`",
    ("attach", "--path"): "the files are those of `files`",
    ("comment", "--file"): "the text comes from the other options",
    ("create", "--description-file"): "the description comes from the other options, or there is none",
    ("update", "--description-file"): "the description is left as it is, or comes from the other options",
    ("transition", "--comment-file"): "no comment is read",
    ("export", "--out"): "the dump is printed and no file is written",
}


@dataclass(frozen=True)
class Default:
    """A default place as the policy of the paths gets it: the place, and the property of the tool that holds each argument that it needs."""

    place: DefaultPlace
    properties: tuple[tuple[str, str], ...]  # (argument, property) for what the place is made of and for the flag that makes it unused


@dataclass(frozen=True)
class Access:
    """What a property does with its value."""

    path: str = ""  # one of PATH_KINDS, or nothing
    at_file: bool = False
    overwrites: bool = False
    default: Default | None = None  # a path property: where the command works when the call leaves it out


def access_of(command: Command) -> dict[str, Access]:
    """The properties of the tool of `command` that name a path, accept `@FILE` or overwrite, by property name."""
    names = mcp_property_names(command)
    found: dict[str, Access] = {}
    for arg in command.args:
        key = (command.name, arg.names[0])
        place = DEFAULT_PLACES.get(key)
        wanted = () if place is None else (*place.made_of, *((place.unused_with,) if place.unused_with else ()))
        default = None if place is None else Default(place, tuple((first, names[first]) for first in wanted))
        access = Access(PATHS.get(key, ""), key in AT_FILE, key in OVERWRITES, default)
        if access != Access():
            found[names[arg.names[0]]] = access
    return found


@dataclass(frozen=True)
class CommandResult:
    """What a command did: its exit code and everything it printed."""

    code: int
    out: str
    err: str


def _describe(value: Any) -> str:
    """What a JSON value is, for a message."""
    if value is None:
        return "null"
    if isinstance(value, bool):  # a bool is an int in Python, and not a number in JSON
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    return "an array" if isinstance(value, list) else "an object"


def _fits(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return kind == "number" or isinstance(value, int) or value.is_integer()  # JSON Schema counts 5.0 as an integer


def _text_problem(text: str) -> str:
    if "\x00" in text:
        return "must not contain a NUL character"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "must be valid text (it holds a lone surrogate)"
    return ""


def _value_problems(name: str, value: Any, spec: Mapping[str, Any]) -> list[str]:
    kind = spec["type"]
    if not _fits(value, kind):
        return [f"'{name}' must be {_TYPE_NAMES[kind]}, not {_describe(value)}"]
    if kind == "string":
        problem = _text_problem(value)
        if problem:
            return [f"'{name}' {problem}"]
    if "enum" in spec and value not in spec["enum"]:
        return [f"'{name}' must be one of {', '.join(spec['enum'])}"]
    return []


def _meaning_problems(tool: Mapping[str, Any], arguments: Mapping[str, Any], show_users: bool, access: Mapping[str, Access]) -> list[str]:
    """What a value would mean to the command, apart from its type: an option, a file, people, a positional argument out of order."""
    found: list[str] = []
    skipped = ""
    for item in tool["_meta"]["jira"]["arguments"]:
        name, value = item["property"], arguments.get(item["property"])
        if value is None:
            skipped = skipped or (name if item["positional"] else "")
            continue
        if item["positional"]:
            if str(value).startswith("-"):
                found.append(f"'{name}' is a positional argument and must not start with '-' (the command would read it as an option)")
            if skipped:
                found.append(f"'{name}' cannot be given without '{skipped}': positional arguments come in order")
        if item["flag"] == WITH_USERS and value is True and not show_users:
            found.append(f"'{name}' is refused: people stay out unless the server is started with --show-users")
        if isinstance(value, str) and value.startswith("@") and access.get(name, Access()).at_file:
            found.append(f"'{name}' must not start with '@': the command would read a file of this computer. Give the text itself")
    return found


def _quoted(names: Sequence[str], limit: int = 10) -> str:
    shown = ", ".join(f"'{name}'" for name in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def problems(tool: Mapping[str, Any], arguments: Mapping[str, Any], *, show_users: bool, access: Mapping[str, Access]) -> list[str]:
    """What is wrong with a call, a sentence each; nothing when it may run. `access` is what the properties of the tool do with a
    value (`access_of`), with no default so that a caller cannot forget it: a value that starts with `@` is refused where it would read
    a file."""
    schema = tool["inputSchema"]
    properties = schema["properties"]
    unknown = [name for name in arguments if name not in properties]
    found = []
    if unknown:
        noun = "property" if len(unknown) == 1 else "properties"
        found.append(f"unknown {noun} {_quoted(unknown)} (the properties are {', '.join(properties) or 'none'})")
    found += [f"'{name}' is required" for name in schema["required"] if name not in arguments]
    for name, value in arguments.items():
        if name in properties:
            found += _value_problems(name, value, properties[name])
    return found + _meaning_problems(tool, arguments, show_users, access)


def problem_summary(found: Sequence[str], limit: int = 8) -> str:
    """The problems as one message: a model that sent many wrong properties needs the first ones, not a page."""
    shown = "; ".join(found[:limit])
    return shown + (f"; and {len(found) - limit} more" if len(found) > limit else "")


def _text(value: Any, kind: str) -> str:
    return str(int(value)) if kind == "integer" else str(value)


def _texts(value: Any, kind: str) -> list[str]:
    """The arguments that a value becomes: one, or one for each element of a list (the files that the policy of the paths approved)."""
    if isinstance(value, (list, tuple)):
        return [_text(item, kind) for item in value]
    return [_text(value, kind)]


def command_line(tool: Mapping[str, Any], arguments: Mapping[str, Any], approved: Mapping[str, Any] | None = None) -> list[str]:
    """What `tests/cli/test_agent_tools.py::argv_from` builds, for a call that `problems` accepted: the positional arguments in order, then
    the flags (a boolean only when true), then what is always appended. Every option is written `--flag=value`, so that a value that
    starts with `-` stays the value of its option.

    `approved` holds, for each property that names a path, the value that the policy of the paths approved (`mcp_paths.PathPolicy.check`):
    the path in full, links followed. The command is given that value and never the text that the model wrote, because a command may take a
    text apart in its own way (take the blanks off its ends, split it at the blanks) and then use another path than the one that was
    checked. The files of `attach` are approved as a list and become one argument each, so that no command can split one at a blank."""
    meta = tool["_meta"]["jira"]
    items = meta["arguments"]
    argv = list(meta["command"][1:])
    given = {**arguments, **(approved or {})}
    for item in items:
        value = given.get(item["property"])
        if item["positional"] and value is not None:
            argv.extend(_texts(value, item["type"]))
    for item in items:
        value = given.get(item["property"])
        if item["positional"] or value is None or value is False:
            continue
        if item["type"] == "boolean":
            argv.append(item["flag"])
        else:
            argv.extend(f"{item['flag']}={text}" for text in _texts(value, item["type"]))
    return argv + list(meta["alwaysAppend"])


def split_error(err: str) -> tuple[str, str]:
    """The error object that the command printed as the last line of standard error (JSON, compact), and the lines before it. Lines end
    at `\\n` only: `str.splitlines` would also cut a message at a character such as U+2028 that Jira may have put into it."""
    lines = err.rstrip("\n").split("\n")
    try:
        printed = json.loads(lines[-1])
    except ValueError:
        printed = None
    if isinstance(printed, dict) and isinstance(printed.get("error"), dict):
        return lines[-1], "\n".join(lines[:-1])
    return "", err.rstrip("\n")


def _error_object(result: CommandResult) -> str:
    line, rest = split_error(result.err)
    if line:
        return line
    # a bug in a command: the CLI prints it as text, not as the error object
    message = next((text[len("ERROR: ") :] for text in rest.split("\n") if text.startswith("ERROR: ")), "")
    error = {
        "code": "ERROR" if message else "EXIT",
        "message": message or f"The command finished with exit code {result.code}.",
        "hint": "",
        "exitCode": result.code,
        "details": {},
    }
    return json.dumps({"error": error}, ensure_ascii=False, separators=(",", ":"))


def _cut(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return (
        text[:MAX_RESULT_CHARS]
        + f"\n[Cut: the result has {len(text):,} characters and only the first {MAX_RESULT_CHARS:,} are shown. "
        "Ask for less: fewer fields, a smaller page, a narrower query.]"
    )


def _block(text: str) -> dict[str, str]:
    return {"type": "text", "text": text}


def tool_result(result: CommandResult) -> dict[str, Any]:
    """The answer to `tools/call`: what the command printed or, when it failed, the error object that it printed on standard error, so that
    the caller sees the hint. A command that answered and still failed (`doctor`) adds what it printed. The rest of standard error is
    not part of the result."""
    if result.code == 0:
        return {"content": [_block(_cut(result.out))], "isError": False}
    content = [_block(_error_object(result))]
    if result.out.strip():
        content.append(_block(_cut(result.out)))
    return {"content": content, "isError": True}


def error_result(error: JiraCliError) -> CommandResult:
    """What a command that failed with `error` leaves: the error object on standard error, as `main.run` prints it for `--json`. A call
    that the server refuses by its own policy (a path outside the working folder) is answered like a command that failed."""
    body = {"code": error.code, "message": error.message, "hint": error.hint, "exitCode": error.exit_code, "details": error.details}
    return CommandResult(error.exit_code, "", json.dumps({"error": body}, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")
