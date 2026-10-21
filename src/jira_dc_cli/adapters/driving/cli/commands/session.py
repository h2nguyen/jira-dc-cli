"""Commands for the setup and the connection: setup-pat, remove-pat, use, current, clear-project, config, ping, doctor."""
from __future__ import annotations

import argparse
import getpass

from .....domain.errors import ConfigError, UsageError
from ..render import table
from ..runtime import Context
from ..spec import Arg, Command, Example

GROUP = "Setup and information"

OK, WARN, FAIL = "ok", "warn", "fail"  # the statuses of a doctor check
_MARK = {OK: "[ok]  ", WARN: "[warn]", FAIL: "[FAIL]"}


# -- token ---------------------------------------------------------------------------------------


def _warn_if_overridden(ctx: Context, name: str) -> None:
    """The environment wins over the file: a change to the file does nothing while the variable is set."""
    if ctx.environ.get(name):
        ctx.diagnostics.warn(f"{name} is also set in the environment, which overrides the configuration file; the change has no effect while it is set.")


def run_setup_pat(ctx: Context, args: argparse.Namespace) -> int:
    if args.stdin:
        token = ctx.read_stdin()
    elif ctx.stdin.isatty():
        token = getpass.getpass("Enter Jira PAT. Input will not be displayed: ")
    else:
        ctx.diagnostics.warn(
            "Standard input is not a terminal, so the token may be visible while you type. "
            "Hide it with `winpty jira setup-pat`, or pipe it in with --stdin."
        )
        token = ctx.stdin.readline()
    ctx.app.config.save_pat(token)
    _warn_if_overridden(ctx, "JIRA_PAT")
    path = ctx.app.config.path
    ctx.print_json({"saved": True, "file": path}) if ctx.json_mode else ctx.print(f"Token saved to {path}")
    return 0


def run_remove_pat(ctx: Context, args: argparse.Namespace) -> int:
    removed = ctx.app.config.remove_pat()
    _warn_if_overridden(ctx, "JIRA_PAT")
    path = ctx.app.config.path
    if ctx.json_mode:
        ctx.print_json({"removed": removed, "file": path})
    else:
        ctx.print(f"Token removed from {path}" if removed else "No token was stored.")
    return 0


# -- project -------------------------------------------------------------------------------------


def run_use(ctx: Context, args: argparse.Namespace) -> int:
    project = ctx.app.config.select_project(args.key)
    _warn_if_overridden(ctx, "JIRA_PROJECT")
    if ctx.json_mode:
        ctx.print_json({"project": project.key, "name": project.name})
    else:
        ctx.print(f"Current project: {project.key}" + (f" ({project.name})" if project.name else ""))
    return 0


def run_current(ctx: Context, args: argparse.Namespace) -> int:
    key = ctx.app.config.current_project()
    if ctx.json_mode:
        ctx.print_json({"project": key or None})
        return 0 if key else 1
    if not key:
        ctx.diagnostics.info("No current project. Run `jira use PROJECT_KEY`.")
        return 1
    ctx.print(key)
    return 0


def run_clear_project(ctx: Context, args: argparse.Namespace) -> int:
    cleared = ctx.app.config.clear_project()
    _warn_if_overridden(ctx, "JIRA_PROJECT")
    if ctx.json_mode:
        ctx.print_json({"cleared": cleared})
    else:
        ctx.print("The current project was cleared." if cleared else "No current project was set.")
    return 0


# -- config --------------------------------------------------------------------------------------


def run_config(ctx: Context, args: argparse.Namespace) -> int:
    action = args.action or "show"
    service = ctx.app.config
    if action == "path":
        ctx.print(service.path)
        return 0
    if action == "show":
        entries = service.entries()
        if ctx.json_mode:
            ctx.print_json({"file": service.path, "settings": entries})
        else:
            ctx.print(f"Configuration file: {service.path}\n")
            ctx.print(table(["SETTING", "VALUE", "SOURCE"], [(entry.name, entry.value, entry.source) for entry in entries]))
        return 0
    if not args.name:
        raise UsageError(f"`jira config {action}` needs a setting name.", hint="List the names with `jira config show`.")
    if action == "set":
        if args.value is None:
            raise UsageError(f"`jira config set {args.name}` needs a value.")
        service.set_value(args.name, args.value)
        _warn_if_overridden(ctx, args.name)
        ctx.print(f"{args.name} saved to {service.path}")
    else:
        removed = service.unset_value(args.name)
        _warn_if_overridden(ctx, args.name)
        ctx.print(f"{args.name} removed from {service.path}" if removed else f"{args.name} was not set in {service.path}")
    return 0


# -- ping and doctor -----------------------------------------------------------------------------


def run_ping(ctx: Context, args: argparse.Namespace) -> int:
    result = ctx.app.ping.execute(quiet=args.quiet)
    base_url = ctx.app.settings.base_url
    if ctx.json_mode:
        ctx.print_json({"ok": result.ok, "baseUrl": base_url, "user": result.user})
    elif args.quiet:
        ctx.print("OK")  # a script can compare the output with OK
    elif result.user is None:
        ctx.print(f"OK: connected to {base_url}")
    else:
        who = result.user.display_name or result.user.name
        ctx.print(f"OK: connected to {base_url} as {who}" + (f" ({result.user.name})" if result.user.name and result.user.name != who else ""))
    return 0


def run_doctor(ctx: Context, args: argparse.Namespace) -> int:
    try:
        checks = ctx.app.doctor.execute()
    except ConfigError as error:  # the settings cannot even be read: that is the finding
        broken = [{"name": "configuration", "status": FAIL, "detail": error.message, "hint": error.hint}]
        if ctx.explicit_json:
            ctx.print_json({"ok": False, "checks": broken})
        else:
            ctx.print(f"{_MARK[FAIL]} configuration  {error.message}" + (f"\n{' ' * 15}Hint: {error.hint}" if error.hint else ""))
        return 1
    failed = any(check.status == FAIL for check in checks)
    if ctx.json_mode:
        ctx.print_json({"ok": not failed, "checks": checks})
    else:
        width = max(len(check.name) for check in checks)
        for check in checks:
            ctx.print(f"{_MARK[check.status]} {check.name.ljust(width)}  {check.detail}")
            if check.hint and check.status != OK:
                ctx.print(f"{' ' * (len(_MARK[check.status]) + 1 + width + 2)}Hint: {check.hint}")
    return 1 if failed else 0


COMMANDS = (
    Command(
        name="setup-pat",
        group=GROUP,
        summary="Save the personal access token",
        when="For the person only: it asks for the token. Never run it for someone, and never pass a token through a chat.",
        description="Asks for the Jira personal access token without showing it and saves it in the configuration file "
        "(~/.config/jira-dc-cli/config, or the file named by JIRA_CONFIG_FILE) with restricted permissions. "
        "The token is never printed and never accepted as a command line argument, so it does not end up in the shell history.",
        args=(Arg(("--stdin",), "Read the token from standard input (for scripts and agents).", kind="flag"),),
        examples=(
            Example("jira setup-pat"),
            Example("printf '%s' \"$TOKEN\" | jira setup-pat --stdin", "non-interactive"),
        ),
        writes=True,
        json_output=True,
        output="saved, file.",
        handler=run_setup_pat,
    ),
    Command(
        name="remove-pat",
        group=GROUP,
        summary="Remove the stored token",
        when="For the person only: it removes the stored token.",
        description="Removes the token from the configuration file. Other settings stay.",
        examples=(Example("jira remove-pat"),),
        writes=True,
        json_output=True,
        output="removed, file.",
        handler=run_remove_pat,
    ),
    Command(
        name="use",
        group=GROUP,
        summary="Select the current project",
        when="Use once to select the project that the commands use when --project is left out.",
        description="Checks that you can access the project and saves it as the current project (JIRA_PROJECT), "
        "so that --project can be left out.",
        args=(Arg(("key",), "Project key.", required=True, metavar="PROJECT_KEY"),),
        examples=(Example("jira use PROJ"),),
        writes=True,
        json_output=True,
        output="project, name.",
        handler=run_use,
    ),
    Command(
        name="current",
        group=GROUP,
        summary="Print the current project",
        when="Use to see which project is selected.",
        description="Prints the project key set with `jira use`. Exit code 1 and no output when none is set.",
        examples=(Example("jira current"),),
        json_output=True,
        output="project (null when none).",
        handler=run_current,
    ),
    Command(
        name="clear-project",
        group=GROUP,
        summary="Forget the current project",
        when="Use to forget the selected project.",
        examples=(Example("jira clear-project"),),
        writes=True,
        json_output=True,
        output="cleared.",
        handler=run_clear_project,
    ),
    Command(
        name="config",
        group=GROUP,
        summary="Show or change the settings",
        when="Use to read the settings (the token is masked), or to change one when the person asks for it.",
        description="`show` lists every setting with its value and where it comes from (environment, config file or default); secrets "
        "are masked. `set NAME VALUE` and `unset NAME` change the configuration file. The environment overrides the file. "
        "The token is set with `jira setup-pat` only.",
        args=(
            Arg(("action",), "show (default), path, set or unset.", kind="choice", choices=("show", "path", "set", "unset")),
            Arg(("name",), "Setting name, for example JIRA_BASE_URL."),
            Arg(("value",), "New value for `set`."),
        ),
        examples=(
            Example("jira config show"),
            Example("jira config set JIRA_BASE_URL https://jira.example.com/jira"),
            Example("jira config set JIRA_HIDE_USERS true"),
            Example("jira config unset JIRA_CA_BUNDLE"),
        ),
        writes=True,
        json_output=True,
        output="file, settings[{name,value,source,description,secret}].",
        handler=run_config,
    ),
    Command(
        name="ping",
        group=GROUP,
        summary="Check the connection and the token",
        when="Use to check quickly that Jira is reachable and the token is accepted (--quiet prints only the result). For everything "
        "that can be wrong use `doctor`.",
        description="Calls Jira once. Prints who the token belongs to, unless --quiet (which prints just OK) or JIRA_HIDE_USERS=true.",
        args=(Arg(("--quiet",), "Print only OK, not who you are.", kind="flag"),),
        examples=(Example("jira ping --quiet"),),
        json_output=True,
        output="ok, baseUrl, user (null when hidden).",
        handler=run_ping,
    ),
    Command(
        name="doctor",
        group=GROUP,
        summary="Check the setup and say how to fix it",
        when="Use first in a session: it checks the connection, the token, the custom fields and the project, and says how to fix "
        "what fails. When it fails, show the checks to the person and stop.",
        description="Checks the configuration, the token, TLS, the connection, the server version, the custom fields and the current "
        "project, and says how to fix what is wrong. Exit code 1 when a check failed.",
        examples=(Example("jira doctor"), Example("jira doctor --json")),
        json_output=True,
        output="ok and checks[{name,status,detail,hint}] with status ok, warn or fail.",
        handler=run_doctor,
    ),
)
