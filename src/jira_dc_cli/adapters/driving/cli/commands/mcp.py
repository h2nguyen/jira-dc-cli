"""Commands that run the CLI for another program: `jira mcp`. Add a `Command` to COMMANDS: the module is already registered."""
from __future__ import annotations

import argparse

from ..runtime import Context
from ..spec import Arg, Command, Example

GROUP = "Tools"


def run_mcp(ctx: Context, args: argparse.Namespace) -> int:
    from ..mcp_server import ServerOptions, serve  # the server imports the registry, which imports this module

    options = ServerOptions(
        allow_write=args.allow_write, allow_files=args.allow_files, show_users=args.show_users, groups=tuple(args.group or ())
    )
    return serve(ctx, options)


COMMANDS = (
    Command(
        name="mcp",
        group=GROUP,
        summary="Run a local MCP server for AI tools",
        when="Use to give an MCP client such as VS Code the read-only commands as tools, with no shell and no new process per call; "
        "the client starts it from its MCP configuration. An agent that has a shell runs the commands directly instead.",
        description="Starts an MCP server on standard input and output: the commands of `jira commands --format mcp` become tools that "
        "run inside this process, so a call needs no shell, and each call builds its own application, as a run of `jira` would. "
        "It speaks JSON-RPC, one message per line, "
        "and writes nothing else to standard output; its own messages go to standard error, and the end of the input stops it. "
        "Tools that change Jira are not offered unless --allow-write, tools that write files (download, pull, export) not unless "
        "--allow-files, and people stay out unless --show-users. Every path in a call must lie below the folder the server was "
        "started in; the settings file and the cache folder are never reachable.",
        args=(
            Arg(
                ("--allow-write",),
                "Also offer the commands that change Jira (each has dryRun). Without it they are not offered and a call of one is refused. "
                "It does not offer the tools that write files.",
                kind="flag",
            ),
            Arg(
                ("--allow-files",),
                "Also offer the commands that write local files: download, pull and export. A call may write only below the folder the "
                "server was started in, never into a hidden folder, never over an existing export file, download only into a folder "
                "below it, and there is no force. It does not offer the tools that change Jira.",
                kind="flag",
            ),
            Arg(
                ("--show-users",),
                "Allow the option that shows people (withUsers). Without it a call that sets it is refused; "
                "JIRA_HIDE_USERS=true refuses it anyway.",
                kind="flag",
            ),
            Arg(
                ("--group",),
                "Offer only the commands of this group of `jira help` (Read, Work on an issue, ...); the case does not matter. "
                "Repeat it for several groups.",
                kind="repeat",
                metavar="NAME",
            ),
        ),
        examples=(
            Example("jira mcp", "what an MCP client starts: the read-only tools"),
            Example("jira mcp --allow-write", "also the tools that change Jira"),
            Example("jira mcp --allow-files", "also download, pull and export, writing below the working folder"),
            Example("jira mcp --group Read --group Agile", "only the commands of two groups"),
        ),
        notes=(
            'VS Code, in .vscode/mcp.json: {"servers": {"jira": {"type": "stdio", "command": "jira", "args": ["mcp"]}}}. '
            'Add "--allow-write", "--allow-files" or "--group", "Read" to args as needed.',
            'Another client: the same command and arguments in its MCP settings, often under a top-level "mcpServers" object: '
            '{"mcpServers": {"jira": {"command": "jira", "args": ["mcp"]}}}.',
            "If the client does not find `jira` on its PATH, start the program itself. "
            'In a console: python "%LOCALAPPDATA%\\jira-dc-cli\\jira.py" mcp. '
            'In the settings of the client: "command": "python" and "args": [the same path, "mcp"]; '
            "VS Code writes ${env:LOCALAPPDATA} for the folder.",
            "The working folder is the boundary of every path in a call (file, descriptionFile, out, directory, ...): start the server in "
            'the project folder ("cwd": "${workspaceFolder}" in VS Code). Paths outside it, `..` escapes, network and device paths, '
            "the settings file and the cache folder are refused with an error that says what is allowed.",
            "The settings file and the token are read again for each call: `jira config set` works at the next call. The environment of "
            "the server is read once when it starts: restart it after changing JIRA_BASE_URL, JIRA_PROFILE or another variable. "
            "`JIRA_PROFILE=agent` in the environment of the server keeps people out of the answers (the exceptions: `jira how-to use-with-ai-agents`). More: `jira how-to mcp-server`.",
        ),
        needs_app=False,
        streams=True,
        handler=run_mcp,
    ),
)
