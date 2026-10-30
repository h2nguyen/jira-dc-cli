# Extend the CLI

The program is organised so that most changes touch a few files in a predictable order. Read
[the architecture](../architecture.md) first for the layers and the one rule that matters: **dependencies point
inwards**. The domain knows nothing, the application knows the domain, adapters know the application.

## Where does my change go

| I want to ... | Touch |
| --- | --- |
| Add a command or an option | A module in `adapters/driving/cli/commands/`, and the use case it calls. |
| Change how an issue is read or written | `domain/` (rules, payloads) and the use case in `application/`. |
| Ask Jira for something new | A method on the port of its feature area (`application/ports_<area>.py`), its implementation in the `adapters/driven/rest_<area>.py` mixin, the fake (`tests/support/fakes_<area>.py`) and the mock route (`tests/support/mock_<area>.py`). **Only calls that the Jira Data Center 11.3 REST API reference documents**: the mock answers 404 to any other. See "Feature areas" below. |
| Add a setting | One `SettingDef` in `application/settings.py` and a field in `Settings`. |
| Change what Jira's JSON means | `adapters/driven/jira_mapper.py`. |
| Support another backend (Jira Cloud) | A new class implementing `JiraGateway`, wired in `bootstrap.py`. |
| Add another front end (a small web service, like the MCP server) | A second driving adapter that calls `Application`, wired in `bootstrap.py`. `jira mcp` is one (`adapters/driving/cli/mcp_server.py`): it lives in the cli package and runs commands through `main.run`. |
| Change the text of a message or the exit code of an error | `domain/errors.py` or where the error is raised. |

## Worked example: a read-only command `jira watchers KEY`

An illustration of the order, with a command that the program has meanwhile gained: the real `jira watchers` belongs to the feature area `collab` (see
"Feature areas" below), so its files are `ports_collab.py`, `rest_collab.py`, `collab.py` and `commands/collab.py`, and the names below are simplified.
Every step has a test.

**1. The port.** In `application/ports.py` add to `JiraGateway`:

```python
    @abstractmethod
    def list_watchers(self, key: str) -> list[User]: ...
```

The test double `FakeGateway` now fails to instantiate (an abstract method is missing), which is the reminder
to implement it there, in `tests/support/fakes.py`, and in the HTTP mock (`tests/support/mock_jira.py`).

**2. The adapter.** In `adapters/driven/jira_rest.py`:

```python
    def list_watchers(self, key: str) -> list[User]:
        data = self._call("GET", f"/issue/{urllib.parse.quote(key, safe='')}/watchers")
        return [user for user in map(mapper.user_from, data.get("watchers") or []) if user]
```

`_call` already turns HTTP statuses into the right errors. Test it against the mock in
`tests/integration/test_http_and_gateway.py`.

**3. The use case.** In `application/issues.py` (or a new module). It validates, applies the privacy policy and
calls the port; it never prints and never touches HTTP:

```python
class ListWatchers:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, key: str) -> list[User]:
        if self._settings.hide_users:
            raise ValidationError("JIRA_HIDE_USERS=true: people are not printed.")
        return self._gateway.list_watchers(parse_issue_key(key))
```

Test it with `FakeGateway` in `tests/unit/`: no HTTP, no files.

**4. Wire it.** In `application/app.py` add the field `list_watchers: ListWatchers` to `Application` and
`list_watchers=ListWatchers(gateway, settings)` to `Application.create`.

**5. The command.** In `adapters/driving/cli/commands/issues.py` add a handler and a `Command` to `COMMANDS`:

```python
def run_watchers(ctx: Context, args: argparse.Namespace) -> int:
    watchers = ctx.app.list_watchers.execute(args.key)
    if ctx.json_mode:
        ctx.print_json(watchers)
    else:
        ctx.print(table(["NAME", "DISPLAY NAME"], [(user.name, user.display_name) for user in watchers]))
    return 0


    Command(
        name="watchers",
        group=GROUP,
        summary="List who watches an issue",
        when="Use to see who is notified about an issue. It reads only; to change the watchers use the web interface.",
        description="Shows the watchers of an issue. Refused when JIRA_HIDE_USERS=true.",
        args=(Arg(("key",), "Issue key.", required=True, metavar="ISSUE_KEY"),),
        examples=(Example("jira watchers PROJ-123"),),
        json_output=True,
        output="a list of {name, displayName, email}.",
        handler=run_watchers,
    ),
```

A new module needs one more line, in `commands/__init__.py`: add it to `MODULES`.

**6. The checks.** Write a test in `tests/cli/test_commands.py` (`run_cli("watchers", "PROJ-1", gateway=...)`).
`tests/cli/test_framework.py` already checks the new command: it has an example, the example parses, every
argument has help, the JSON output is documented. Then regenerate the documentation and run everything:

```bash
python scripts/generate_docs.py          # docs/reference/commands.md and the table in README.md
python -m unittest discover -s tests -t .
python scripts/check_release.py          # the skill must mention the new command: add it to skills/jira-dc-cli/SKILL.md
```

Add a line to `## [Unreleased]` in `CHANGELOG.md`.

## The rules every command follows

These are checked by tests where a test can check them.

- **A command that changes Jira has `--dry-run`**, which shows the payload and sends nothing. Build the payload in
  one place (`domain/payloads.py`, `domain/drafts.py`) so the preview is exactly what is sent.
- **A command that returns data supports `--json`** and documents the shape in `output=`. Add keys, never rename
  them inside a major version.
- **Every command has examples**, and they must be valid usage.
- **Every command says when to use it** (`when=`): one or two sentences that tell a person or an agent when this is the
  command to reach for and when another one is better. The text feeds `jira help`, the catalog (`whenToUse`) and the
  tool descriptions of `jira commands --format mcp`; a command that is not for an agent (the token, the settings) is
  listed in `MCP_EXCLUDED` in `adapters/driving/cli/help.py`, one that never asks Jira in `MCP_LOCAL`, and one that
  changes Jira in a way that is not only additive in `MCP_DESTRUCTIVE` (and `MCP_IDEMPOTENT` when repeating it changes nothing more).
- **A command with an argument that names a path, accepts `@FILE` or overwrites** is classified in `adapters/driving/cli/mcp_calls.py` (`PATHS`: what it
  does with the value; `AT_FILE`; `OVERWRITES`; or `NOT_A_PATH` with the reason), which is what `jira mcp` checks a call against;
  `tests/cli/test_mcp_server.py::ClassificationTest` fails for an argument that looks like a path (its name, metavar or help says file, path, directory
  or folder, it is `--out`, or it says `@FILE`) and is in no table. An optional path argument that the command works out a place for when it is left
  out is in `DEFAULT_PLACES` (with the function that works the place out like the command; `tests/cli/test_mcp_server.py::DefaultPlacesTest` checks it
  against the real command), or in `NO_DEFAULT_PLACE` with the reason why it has none: that test fails for an optional property that names a path and is in
  neither. A command that writes files and not Jira belongs in `MCP_FILES` of `help.py`: it is of
  `--only write` and `jira mcp` offers it only with `--allow-files`.
- **Errors are `JiraCliError` subclasses** with a message that says what happened and a `hint` that says what to
  do. Never `print` an error from a use case, never `sys.exit` below `main`.
- **A command that prints a list** builds its rows as dicts (the shape of its JSON), offers `listing.SELECT` and `listing.FORMAT`
  (`--select a,b.c`, `--format table|tsv|md`) and prints through `listing.print_list`, so that every list verb behaves the same.
- **A query that people type** (JQL, search text) goes through `_common.query_option`: `@FILE` or `-` reads it from a file or standard
  input, because Windows PowerShell 5.1 removes the double quotes inside an argument.
- **A command that talks on standard input and output itself** (a server such as `jira mcp`) sets `streams=True`, so that
  `--json-envelope` does not capture its output.
- **No I/O in `domain/` or `application/`.** Files, the clock, HTTP and the console are ports or adapters.
- **People stay out of output by default.** Use `strip_users`, `hide_users` and `redact_text` from
  `domain/privacy.py`, and honour `settings.hide_users`.
- **Standard library only.** `tests/test_architecture.py` fails on any other import.
- **Results to stdout, messages to stderr**: `ctx.print`, `ctx.print_json`, `ctx.diagnostics`.
- **No secrets in output**, including `-v`.

## Feature areas

`JiraGateway` is composed of **area ports**, one file per feature area, so that no file grows with every command and so that areas can be
built side by side. For an area `X` (attachments, agile, comments, users, validation, reading, reference, collab):

| File | Holds |
| --- | --- |
| `application/ports_X.py` | `XPort`, the abstract Jira operations of the area; `JiraGateway` inherits it. |
| `adapters/driven/rest_X.py` | `RestX`, a mixin of `RestJiraGateway` that implements them with `self._call(...)` and, for raw bytes, `self._transport`. |
| `application/X.py` | `XUseCases` (reached as `app.X.<use_case>`) and `build(...)`, which `Application.create` calls. |
| `adapters/driving/cli/commands/X.py` | The `COMMANDS` of the area. |
| `tests/support/fakes_X.py`, `tests/support/mock_X.py` | `FakeX`, a mixin of `FakeGateway` (state set up in `_init_X`), and `XRoutes`, the routes of the mock Jira. |

Order of work: write the REST method and the fake method first, declare the abstract method in the port last; `JiraGateway` cannot be
instantiated while one of the implementations lacks a declared method.

**The target is Jira Data Center 11.3.x, and the tests hold you to it.** `tests/support/jira_dc_11_3_endpoints.txt` lists the calls of the
11.3 reference (method and path); the mock Jira answers 404 to a call that is not in it and records it in `jira.unknown_endpoints`. Every
test of a command that talks to the mock asserts that this list is empty (`tests/integration/test_conformance.py` sweeps the commands).
Jira Cloud endpoints (such as `/issue/{key}/changelog`) and internal ones (`rest/greenhopper/*`) are not in the list: use the documented way
(`expand=changelog`, the Agile API). The one address outside the list is the content of an attachment, `GET /secure/attachment/{id}/{name}`: it is no REST call
(`OUTSIDE_THE_REFERENCE` in `tests/support/conformance.py`). See [compatibility](../reference/compatibility.md).

## Add a setting

One entry in `SETTING_DEFS` (`application/settings.py`) with name, attribute, type, default, description and an
example, and the attribute on the `Settings` dataclass. Parsing, `jira config show`, `jira doctor` (if you add a
check) and `docs/reference/configuration.md` (after `generate_docs.py`) follow from that.

## Add an adapter

An adapter implements a port from `application/ports.py` and is wired in `bootstrap.create_application`.
Translate the failures of your technology into domain errors at the boundary. Test it on its own with the real
technology (a temporary folder, the mock server) and keep the use cases unchanged: if you find yourself editing a
use case to suit an adapter, the port is wrong.

A second Jira backend is the clearest case: implement all of `JiraGateway`, return domain objects, map the
logical field names, and select it in `bootstrap.py` from a setting.

## The test helpers

| Helper | Where | Use |
| --- | --- | --- |
| `FakeGateway`, `MemoryFiles`, `MemoryConfigStore`, `MemoryCache`, `FixedClock`, `FakeDocs`, `RecordingDiagnostics`, `build_app` | `tests/support/fakes.py` | Use cases and commands without I/O. `FakeGateway.calls` records every call; `.errors[name]` makes a method fail. |
| `run_cli(*argv, gateway=..., files=..., stdin=...)` | `tests/support/cli.py` | Run the command line in-process; returns `code`, `out`, `err`, `json()`, `error()`. |
| `MockJira` | `tests/support/mock_jira.py` | A Jira Data Center in a thread with synthetic data. `fail_next(prefix, status, times=...)` injects errors; `.requests` records everything; `.unknown_endpoints` lists the calls that the 11.3 reference does not document (the mock answers 404 to them). The routes of a feature area live in `tests/support/mock_<area>.py`. |
| `EndToEnd` | `tests/integration/test_end_to_end.py` | The base of whole-program tests: the real adapters against `MockJira`; `self.cli(...)`, `self.jira.sent(...)`. |

## Before you merge

1. `python -m unittest discover -s tests -t .` is green.
2. `python scripts/generate_docs.py` produced no unexpected change; `python scripts/check_release.py` is happy.
3. A line in `CHANGELOG.md` under `## [Unreleased]`.
4. For a release, follow [release a new version](release-process.md).
