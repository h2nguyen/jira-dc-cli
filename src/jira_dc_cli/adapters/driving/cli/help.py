"""Help for people and for programs, and the generated reference. All from the command descriptions."""
from __future__ import annotations

import re
import textwrap
from typing import Any, Sequence

from ....domain.errors import EXIT_CODES
from .spec import GROUP_ORDER, Arg, Command

_COMMON_OPTIONS = (
    ("--json", "Print the result as JSON (stable keys for scripts and agents)."),
    ("-v, --verbose", "Log every request to stderr (never the token)."),
    ("--json-envelope", "Print exactly one JSON object on stdout, {\"ok\": true, \"data\": ...} or {\"ok\": false, \"error\": {...}}, whatever happens. The exit code and stderr stay as they are. Also JIRA_JSON_ENVELOPE=1."),
    ("-h, --help", "Show this help. Help never calls Jira and never changes anything."),
)


def _wrap(text: str, indent: str = "") -> str:
    return "\n".join(
        textwrap.fill(paragraph, width=96, initial_indent=indent, subsequent_indent=indent)
        for paragraph in text.strip().split("\n\n")
    )


def _option_label(arg: Arg) -> str:
    if arg.positional:
        return arg.label
    names = ", ".join(arg.names)
    return names if arg.kind == "flag" else f"{names} {arg.label}"


def _arg_lines(command: Command) -> list[tuple[str, str]]:
    rows = [(_option_label(arg), arg.help + (f" Default: {arg.default}." if arg.default not in (None, "") else "")) for arg in command.args]
    if command.json_output:
        rows.append(_COMMON_OPTIONS[0])
    rows.extend(_COMMON_OPTIONS[1:])
    return rows


def render_overview(commands: Sequence[Command], version: str) -> str:
    lines = [
        f"Jira Data Center command line helper (jira-dc-cli {version})",
        "",
        "Usage: jira COMMAND [options]",
        "       jira help COMMAND",
        "",
    ]
    width = max(len(command.name) for command in commands) + 2
    for group in GROUP_ORDER:
        members = [command for command in commands if command.group == group]
        if not members:
            continue
        lines.append(group)
        lines.extend(f"  {command.name.ljust(width)}{command.summary}" for command in members)
        lines.append("")
    lines += [
        "Every command answers `jira COMMAND --help` (or `jira help COMMAND`) with its options and examples.",
        "Help never calls Jira and never changes anything.",
        "",
        "For scripts and AI agents: add --json (or set JIRA_OUTPUT=json). Errors then come as JSON on",
        "stderr. Exit codes and JSON shapes: `jira how-to exit-codes`, `jira how-to json-output`.",
        "Settings: `jira config show`. Guides: `jira how-to`. Setup check: `jira doctor`.",
    ]
    return "\n".join(lines)


BRIEF_BUDGET = 4096
_BRIEF_HEADER = (
    "jira COMMAND ARGS [--options]   [x] optional, x... repeats, * changes data: use --dry-run first where the command has it, ... more options: jira help COMMAND",
    "Every command also takes -v and --json-envelope; a command that returns data takes --json.",
)


def _brief_line(command: Command, options: int | None) -> str:
    """`name ARGS --required [--option --option ...]`; a star after the name says that the command changes data. An option that must
    be given stands outside the brackets and is never cut."""
    positionals = [(arg.label + ("..." if arg.kind == "words" else "")) if arg.required else f"[{arg.label}{'...' if arg.kind == 'words' else ''}]" for arg in command.args if arg.positional]
    flags = [arg for arg in command.args if not arg.positional and not (command.writes and arg.names[0] == "--dry-run")]  # the star says it
    required = [arg.names[0] for arg in flags if arg.required]
    optional = [arg.names[0] for arg in flags if not arg.required]
    shown = optional if options is None else optional[:options]
    text = " ".join([command.name + ("*" if command.writes else ""), *positionals, *required])
    if shown:
        text += " [" + " ".join(shown) + (" ..." if len(shown) < len(optional) else "") + "]"
    elif optional:
        text += " [...]"
    return text


def render_brief(commands: Sequence[Command], *, budget: int | None = BRIEF_BUDGET, names: Sequence[str] | None = None) -> str:
    """One line per command with its arguments and options: the compact reference for an agent (`jira help --brief`).

    With a `budget` (characters) the options of every command are cut, step by step, until the text fits; `names` keeps only those
    commands; with `budget=None` nothing is cut."""
    chosen = [command for command in commands if names is None or command.name in names]

    def build(options: int | None) -> str:
        lines = list(_BRIEF_HEADER)
        for group in GROUP_ORDER:
            members = [command for command in chosen if command.group == group]
            if members:
                lines.append(f"{group}:")
                lines.extend("  " + _brief_line(command, options) for command in members)
        return "\n".join(lines)

    text = build(None)
    for options in (8, 6, 5, 4, 3, 2, 1, 0):
        if budget is None or len(text) <= budget:
            break
        text = build(options)
    return text


def render_compact_reference(commands: Sequence[Command], names: Sequence[str]) -> str:
    """For the skill: the arguments and all options of some commands, each with its first example."""
    by_name = {command.name: command for command in commands}
    lines: list[str] = []
    for name in names:
        command = by_name[name]
        lines.append(_brief_line(command, None))
        if command.examples:
            example = command.examples[0]
            lines.append("    " + example.command + (f"    # {example.note}" if example.note else ""))
    return "\n".join(lines)


def _changes(command: Command) -> str:
    """What the help says about writing: yes or no, and whether a preview exists."""
    if not command.writes:
        return "no"
    return "yes, preview with --dry-run" if any("--dry-run" in arg.names for arg in command.args) else "yes"


def render_command_help(command: Command) -> str:
    lines = [f"jira {command.name} - {command.summary}", "", f"Usage: {command.usage()}", ""]
    if command.description:
        lines += [_wrap(command.description), ""]
    if command.when:
        lines += [_wrap("When to use: " + command.when), ""]
    rows = _arg_lines(command)
    width = min(max(len(label) for label, _ in rows), 34) + 2
    positional = [(l, h) for (l, h), a in zip(rows, command.args) if a.positional]
    options = [row for row in rows if row not in positional]
    for title, block in (("Arguments", positional), ("Options", options)):
        if not block:
            continue
        lines.append(f"{title}:")
        for label, text in block:
            lead = f"  {label.ljust(width)}"
            wrapped = textwrap.wrap(text, width=96 - len(lead)) or [""]
            lines.append(lead + wrapped[0])
            lines.extend(" " * len(lead) + extra for extra in wrapped[1:])
        lines.append("")
    if command.examples:
        lines.append("Examples:")
        for example in command.examples:
            lines.append(f"  {example.command}")
            if example.note:
                lines.append(f"      {example.note}")
        lines.append("")
    if command.notes:
        lines.append("Notes:")
        lines.extend(_wrap("- " + note, "  ").replace("\n  ", "\n    ") for note in command.notes)
        lines.append("")
    facts = [f"Changes data: {_changes(command)}"]
    if command.output:
        facts.append(f"JSON output: {command.output}")
    lines.extend(textwrap.fill(fact, width=96, subsequent_indent="  ") for fact in facts)
    return "\n".join(lines).rstrip()


def _arg_catalog(arg: Arg) -> dict[str, Any]:
    return {
        "names": list(arg.names),
        "positional": arg.positional,
        "kind": arg.kind,
        "required": arg.required,
        "default": arg.default,
        "choices": list(arg.choices),
        "help": arg.help,
    }


def catalog(commands: Sequence[Command], version: str) -> dict[str, Any]:
    """Everything a program needs to know about the CLI: `jira commands --json`."""
    from ....application.settings import SETTING_DEFS

    return {
        "version": version,
        "commands": [
            {
                "name": command.name,
                "group": command.group,
                "summary": command.summary,
                "description": command.description,
                "whenToUse": command.when,
                "usage": command.usage(),
                "arguments": [_arg_catalog(arg) for arg in command.args],
                "supportsJson": command.json_output,
                "changesData": command.writes,
                "jsonOutput": command.output,
                "examples": [{"command": item.command, "note": item.note} for item in command.examples],
                "notes": list(command.notes),
                "aliases": list(command.aliases),
            }
            for command in commands
        ],
        "exitCodes": [{"code": code, "names": names, "meaning": meaning} for code, names, meaning in EXIT_CODES],
        "settings": [
            {
                "name": item.name,
                "kind": item.kind,
                "default": item.default,
                "secret": item.secret,
                "description": item.description,
            }
            for item in SETTING_DEFS
        ],
    }


# -- tool descriptions for agents (the shape of an MCP `tools/list` answer) ------------------------------------------

# Commands that are for the person, not for an agent: they hold or change credentials and settings, or describe the CLI itself.
# `api` is a raw escape hatch whose writes no description can bound; an agent that needs it asks the person.
MCP_EXCLUDED = frozenset({"setup-pat", "remove-pat", "config", "use", "clear-project", "help", "commands", "skill", "changelog", "api", "wiki", "mcp"})
MCP_LOCAL = frozenset({"version", "how-to", "current"})  # they never ask Jira
MCP_DESTRUCTIVE = frozenset(
    {
        "update", "update-json", "transition", "import-md", "import-md-dir", "import-csv", "push",  # they overwrite or move
        "sprint-add", "rank", "epic-add", "sprint-update",  # they move issues between sprints and epics, change the rank, start and complete sprints
        "watch", "vote", "remote-link",  # they change who is notified, a vote, a link that may be updated
        "comment",  # it adds a comment, but with --edit it overwrites the text of one
        "assign",  # it replaces the assignee
    }
)
MCP_IDEMPOTENT = frozenset({"update", "update-json", "push", "sprint-add", "rank", "epic-add", "sprint-update", "watch", "vote", "assign"})  # the same call again changes nothing more
MCP_FILES = frozenset({"pull", "export", "download"})  # they change no issue but write local files (`pull`, `export --out`, `download`): not read-only, so they are of the write set, and `jira mcp` offers them only with --allow-files
# What a tool that writes files says about itself: in the catalogue (`jira commands --format mcp`, for a program that runs `jira NAME` in a shell, where `--force` works) and
# as `jira mcp` offers it (which refuses `force`, never writes over a file of the export and the download, and keeps to the working folder).
MCP_FILES_NOTE = "Does not change Jira. Writes files in a folder and can overwrite them: ask before you use it on a folder that holds work."
MCP_FILES_NOTE_OVER_MCP = (
    "Does not change Jira. Writes files below the working folder of the server. Over MCP it never replaces a file or an edit of yours: "
    "`force` is refused, `export` refuses a file that exists, `download` stops at one, and `pull` only adds files and brings in what Jira changed."
)
MCP_REFUSED_NOTE = "Refused over MCP: replacing a file, or a change that somebody else made, is for the user, not for a call."
MCP_ALWAYS = {"issues": ("--no-assignee",), "ping": ("--quiet",)}  # what keeps the people out of an answer that would show them
MCP_ONLY = ("read", "write", "all")
_TYPES = {"flag": "boolean", "int": "integer", "float": "number"}


def _camel(text: str) -> str:
    head, *tail = [part for part in re.split(r"[^A-Za-z0-9]+", text) if part]
    return head.lower() + "".join(part[:1].upper() + part[1:].lower() for part in tail)


def mcp_property_names(command: Command) -> dict[str, str]:
    """The property of the input schema for each argument (by its first name): `--if-unchanged-since` is `ifUnchangedSince`, ISSUE_KEY is `issueKey`."""
    names: dict[str, str] = {}
    taken: set[str] = set()
    for arg in command.args:
        base = _camel(arg.metavar) if arg.positional and arg.metavar else _camel(arg.dest)
        name = base if base not in taken else base + "Option"
        taken.add(name)
        names[arg.names[0]] = name
    return names


def _schema(arg: Arg) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": _TYPES.get(arg.kind, "string")}
    if arg.choices:
        schema["enum"] = list(arg.choices)
    schema["description"] = arg.help.strip()
    if arg.default not in (None, "", False):
        schema["default"] = arg.default
    return schema


def _in_tool_terms(text: str, command: Command, names: dict[str, str], tool_names: dict[str, str]) -> str:
    """The text as a reader of the tool description needs it: `--dry-run` is the property `dryRun`, `issue` is the tool `jira_issue`."""
    for cli_name, tool_name in tool_names.items():  # first: afterwards `use` could be a property or a command
        text = text.replace(f"`{cli_name}`", f"`{tool_name}`")
    for arg in command.args:
        if arg.positional:
            continue
        name = names[arg.names[0]]
        for flag in sorted(arg.names, key=len, reverse=True):
            text = text.replace(f"`{flag}`", f"`{name}`")
            text = re.sub(r"(?<![\w`-])" + re.escape(flag) + r"(?![\w-])", f"`{name}`", text)
    return text


def mcp_tool_name(command: Command) -> str:
    """The name of the tool of a command: `import-md` is `jira_import_md`."""
    return "jira_" + command.name.replace("-", "_")


def mcp_tool(command: Command, tool_names: dict[str, str] | None = None, *, over_mcp: bool = False) -> dict[str, Any]:
    """The description of one command as a tool. `over_mcp` describes it as `jira mcp` offers it: the server refuses `force` and never writes over
    a file of an export or a download, which the catalogue (for a shell, where the flag works) does not say."""
    names = mcp_property_names(command)
    tool_names = tool_names or {}

    def say(text: str) -> str:
        return _in_tool_terms(text, command, names, tool_names)

    properties = {names[arg.names[0]]: {**_schema(arg), "description": say(_schema(arg)["description"])} for arg in command.args}
    forcing = [names[arg.names[0]] for arg in command.args if arg.kind == "flag" and arg.names[0] == "--force"]
    if over_mcp:
        for name in forcing:
            properties[name]["description"] = f"{MCP_REFUSED_NOTE} {properties[name]['description']}"
    required = [names[arg.names[0]] for arg in command.args if arg.required]
    parts = [command.summary + "."]
    if command.when:
        parts.append(say(f"When to use: {command.when}"))
    if command.description:
        parts.append(say(" ".join(command.description.split())))
    if command.output:
        parts.append(say(f"Returns JSON: {command.output}"))
    if command.writes:
        parts.append("Changes Jira." + (" Run it with dryRun first and show the result." if any("--dry-run" in a.names for a in command.args) else ""))
    elif command.name in MCP_FILES:
        parts.append(MCP_FILES_NOTE_OVER_MCP if over_mcp else MCP_FILES_NOTE)
    if over_mcp and forcing:
        parts.append(f"Over MCP `{forcing[0]}` is refused: do not set it.")
    writes_files = command.name in MCP_FILES
    annotations: dict[str, Any] = {"title": command.summary, "readOnlyHint": not command.writes and not writes_files, "openWorldHint": command.name not in MCP_LOCAL}
    if command.writes:
        annotations["destructiveHint"] = command.name in MCP_DESTRUCTIVE
        annotations["idempotentHint"] = command.name in MCP_IDEMPOTENT
    elif writes_files:
        annotations["destructiveHint"] = True
        annotations["idempotentHint"] = True
    return {
        "name": mcp_tool_name(command),
        "title": command.summary,
        "description": "\n\n".join(parts),
        "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        "annotations": annotations,
        "_meta": {
            "jira": {
                "command": ["jira", command.name],
                "arguments": [
                    {
                        "property": names[arg.names[0]],
                        "positional": arg.positional,
                        "flag": None if arg.positional else arg.names[0],
                        "type": _TYPES.get(arg.kind, "string"),
                    }
                    for arg in command.args
                ],
                "alwaysAppend": (["--json"] if command.json_output else []) + list(MCP_ALWAYS.get(command.name, ())),
                "examples": [item.command for item in command.examples],
            }
        },
    }


def mcp_tools(commands: Sequence[Command], version: str, *, only: str = "read", over_mcp: bool = False) -> dict[str, Any]:
    """The commands an agent may use, as MCP tool descriptions: name, description, JSON Schema for the input, annotations
    (read-only, destructive, idempotent), and under `_meta.jira` how the properties become a command line.

    `only` is `read` (the default: nothing that changes Jira and nothing that writes local files), `write` (the commands that change Jira, and
    `MCP_FILES`, which write local files) or `all`. `over_mcp` is how `jira mcp` offers them: the same tools, and the texts that differ say
    what the server does (it refuses `force`, and never writes over a file of an export or a download); the catalogue describes the
    commands as a shell runs them."""
    if only not in MCP_ONLY:
        raise ValueError(f"only must be one of {MCP_ONLY}")
    chosen = [
        command
        for command in commands
        if command.name not in MCP_EXCLUDED and (only == "all" or (command.writes or command.name in MCP_FILES) == (only == "write"))
    ]
    tool_names = {command.name: mcp_tool_name(command) for command in chosen}
    return {
        "tools": [mcp_tool(command, tool_names, over_mcp=over_mcp) for command in chosen],
        "_meta": {
            "jira": {
                "version": version,
                "only": only,
                "invoke": (
                    "Run the command: `jira NAME`, then the positional arguments in the order of `arguments`, then each other property as its flag "
                    "(a boolean only when true), then the always-appended flags. A result is JSON on standard output; "
                    "a failure is a non-zero exit code and JSON on standard error: {\"error\": {\"code\", \"message\", \"hint\", \"exitCode\"}}. "
                    "People stay out of the answers when the process that runs the commands has JIRA_HIDE_USERS=true: set it for an agent "
                    "(the lead of a project is shown otherwise; your own login still appears where a command sets you as reporter or assignee)."
                ),
                "exitCodes": [{"code": code, "names": names, "meaning": meaning} for code, names, meaning in EXIT_CODES],
            }
        },
    }


# -- generated reference (docs/reference/*.md) ----------------------------------------------------

_GENERATED = "<!-- Generated by scripts/generate_docs.py from the command descriptions. Do not edit by hand. -->"


def commands_markdown(commands: Sequence[Command], version: str) -> str:
    out = [_GENERATED, "", "# Command reference", "", f"jira-dc-cli {version}. The same text is available offline with `jira help COMMAND`.", ""]
    for group in GROUP_ORDER:
        members = [command for command in commands if command.group == group]
        if not members:
            continue
        out += [f"## {group}", ""]
        for command in members:
            out += [f"### `jira {command.name}`", "", command.summary + ".", "", "```text", command.usage(), "```", ""]
            if command.description:
                out += [command.description.strip(), ""]
            if command.when:
                out += [f"**When to use:** {command.when}", ""]
            rows = _arg_lines(command)
            out += ["| Argument or option | Meaning |", "| --- | --- |"]
            out += [f"| `{label}` | {text.replace('|', chr(92) + '|')} |" for label, text in rows]
            out.append("")
            if command.examples:
                out += ["```bash"]
                for example in command.examples:
                    out.append(f"{example.command}" + (f"    # {example.note}" if example.note else ""))
                out += ["```", ""]
            for note in command.notes:
                out.append(f"- {note}")
            if command.notes:
                out.append("")
            out.append(f"Changes data: **{_changes(command)}**." if command.writes else "Changes data: no.")
            if command.output:
                out.append("")
                out.append(f"JSON output: {command.output}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _default_text(item: Any) -> str:
    if item.secret:
        return "secret"
    if item.kind == "bool":
        return f"`{str(item.default).lower()}`"
    if item.kind == "float":
        return f"`{item.default:g}`"
    return f"`{item.default}`" if item.default not in ("", None) else "(empty)"


def settings_markdown() -> str:
    from ....application.settings import SETTING_DEFS

    out = [_GENERATED, "", "# Configuration reference", "",
           "Settings come from the environment, then from the config file (`jira config path`), then from the defaults.", "",
           "| Setting | Default | Meaning |", "| --- | --- | --- |"]
    for item in SETTING_DEFS:
        example = f" Example: `{item.example}`." if item.example else ""
        out.append(f"| `{item.name}` | {_default_text(item)} | {item.description}{example} |")
    return "\n".join(out) + "\n"


def exit_codes_markdown() -> str:
    out = [_GENERATED, "", "# Exit codes", "",
           "A script or an agent can decide what to do from the exit code alone.", "",
           "| Code | Error codes | Meaning |", "| --- | --- | --- |"]
    out += [f"| {code} | {names} | {meaning} |" for code, names, meaning in EXIT_CODES]
    out += ["", "With `--json` (or `JIRA_OUTPUT=json`) an error is printed to **stderr** as one JSON object:", "",
            "```json", '{"error": {"code": "NOT_FOUND", "message": "...", "hint": "...", "exitCode": 4, "details": {}}}', "```", ""]
    return "\n".join(out)
