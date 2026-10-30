# Architecture

jira-dc-cli follows the hexagonal (ports and adapters) pattern. The business rules sit in the middle and know
nothing about HTTP, files or the terminal; everything that touches the outside world is an adapter that plugs
into a port. That is what makes the program easy to extend and to test, and tests enforce the rules.

```text
                    driving side                                         driven side
        (who asks the application to act)                  (what the application needs from outside)

   +--------------------------+                                   +-----------------------------+
   |  command line adapter    |                                   |  RestJiraGateway  (HTTP)    |
   |  adapters/driving/cli    |                                   |  FileConfigStore  (file)    |
   |  parse, call, print      |                                   |  FileCache, LocalFiles      |
   +------------+-------------+                                   |  LocalDocs, SystemClock     |
                |                                                 +--------------+--------------+
                | calls use cases                                                ^ implements ports
                v                                                                |
        +----------------------------------------------------------------------------------+
        |  application   use cases (ViewIssue, TransitionWithFields, ImportMarkdown, ...)   |
        |                ports: JiraGateway, ConfigStore, Cache, Files, Clock, Docs, ...     |
        |  +------------------------------------------------------------------------------+ |
        |  |  domain   Issue, Transition, JQL, reporting, privacy, wiki markup, payloads    | |
        |  +------------------------------------------------------------------------------+ |
        +----------------------------------------------------------------------------------+

   bootstrap.py is the composition root: the only module that knows both sides. It reads the
   settings, builds the real adapters and hands the application to the command line.
```

## The layers and what they may import

| Layer | Folder | Holds | May import |
| --- | --- | --- | --- |
| Domain | `src/jira_dc_cli/domain/` | Pure rules and data: the model, JQL, branch names, reports, privacy, Markdown to wiki conversion and back (`unwiki`), search terms and snippets, the Markdown ticket file with its sync records (`mirror`) and its `links:` line (`mirror_links`), payload builders, the error types with their exit codes. | The domain and the standard library, but nothing that does I/O. |
| Application | `src/jira_dc_cli/application/` | The use cases, the **ports** (abstract interfaces), the settings definitions. | The domain and the application. |
| Driven adapters | `src/jira_dc_cli/adapters/driven/` | Implementations of the ports: HTTP transport and Jira REST gateway, config file, cache, files, documentation, clock. | Domain, application, `version`. Never a driving adapter. |
| Driving adapter | `src/jira_dc_cli/adapters/driving/cli/` | The command line: command descriptions, parser, help, output formatting, `main`; and the MCP server (`mcp_server.py`, `mcp_calls.py`, `mcp_paths.py`). | Domain, application, `version`. Never a driven adapter. |
| Composition root | `src/jira_dc_cli/bootstrap.py` | Wiring: settings from file and environment, real adapters, `main()`. | Everything. Nothing imports it except the entry points. |

Everything uses the standard library only. `tests/test_architecture.py` reads the imports of every module
and fails on a violation: an inner layer reaching outwards, I/O in the domain or application, a third-party
import, a command module that is not registered, a port without a real adapter or a test double.

## Ports

| Port | What the application needs | Real adapter | Test double |
| --- | --- | --- | --- |
| `JiraGateway` | Everything Jira: users, fields, issues, search, transitions, comments, links, create, update, projects, raw requests; and, in the `ports_<area>.py` files it inherits from, attachments, boards and sprints, users, validation metadata, saved filters, reference lists and collaboration (watchers, votes, worklogs, remote links). Only calls that the Jira Data Center 11.3 REST API reference documents. | `RestJiraGateway` over `HttpTransport` | `FakeGateway`; also `MockJira`, an HTTP server |
| `ConfigStore` | Persistent `KEY=value` settings. | `FileConfigStore` | `MemoryConfigStore` |
| `Cache` | Lists that rarely change (fields, issue types). | `FileCache` | `MemoryCache` |
| `Files` | Read, write, list, join. `LocalFiles` can also create a file only when nothing is there and give one name to a place (`create_bytes`, `resolve`); the use cases of attachments ask for these through the optional protocols of `application/ports_attachments.py`, and a file system in memory may not have them. | `LocalFiles` | `MemoryFiles` |
| `Clock` | The current time. | `SystemClock` | `FixedClock` |
| `Docs` | The documentation and skill that ship with the CLI. | `LocalDocs` | `FakeDocs` |
| `Diagnostics` | Progress and warnings for the person at the terminal. | `StderrDiagnostics` (in the command line adapter) | `RecordingDiagnostics` |

`JiraGateway` speaks in domain objects and in **logical field names** (`story_points`, `epic_link`, `sprint`,
`acceptance`). Only the REST adapter knows that these are `customfield_10001` and so on in one Jira and
something else in another.

## One command, from the keyboard to Jira

`jira transition PROJ-1 --to Done --comment "ok"`:

1. `main.run` reads the arguments, finds the command in the registry and builds an argparse parser from its
   description (`spec.Command`). `--help` is answered before parsing, so help never needs valid arguments.
2. The handler `run_transition` in `commands/workflow.py` gets a `Context`: the streams, the output mode and a lazy
   `Application`. It converts the text options and calls `ctx.app.validation.transition.execute(...)`.
3. `TransitionWithFields` (application; the `transition` of `ValidationUseCases`) asks the gateway for the issue and
   the transitions, uses the domain's `pick_transition` to choose one, builds the payload with the domain's
   `transition_payload`, and either checks it against the transition's screen and returns it (dry run) or hands that
   very payload to the `JiraGateway` port, which sends it as it is: what a dry run shows is what a real run sends.
4. `RestJiraGateway` turns the call into an HTTP request on `HttpTransport` (keep-alive connection, retries for
   reads, TLS), maps the status to a domain error (`NotFoundError`, `AuthError`, ...) and the JSON to domain objects.
5. The handler prints the result as text or JSON. A `JiraCliError` anywhere on the way ends in `main.run`, which
   prints it (as JSON on stderr with `--json`) and returns the exit code the error carries.

## The command framework

A command is data: `spec.Command` holds the name, group, summary, description, arguments, examples, notes,
whether it writes, whether it supports `--json` and what the JSON holds, and the handler. That single description
feeds the argument parser, `jira help`, `jira commands --json` and the generated `docs/reference/commands.md`, so
they cannot disagree. `registry.all_commands()` collects the `COMMANDS` of the modules listed in
`commands/__init__.py`.

Quality rules are tests (`tests/cli/test_framework.py`): every command has examples and they parse, every
argument has help text, a command that returns data documents its JSON, a command that writes has `--dry-run`.

## Errors and exit codes

`domain/errors.py` defines `JiraCliError` and its subclasses. Each carries a `code`, an `exit_code`, a human
`message`, a `hint` (what to do) and `details` (structured facts). Adapters translate their failures into these
(an HTTP 404 becomes `NotFoundError`); the application raises them for rule violations; only `main.run` turns
them into output. The table of exit codes is in the same file and generates the reference and the catalog.

## Output

Results go to standard output, progress and warnings to standard error through `Diagnostics`. JSON is produced
from dataclasses by `render.to_jsonable` (camelCase keys); output that must keep a particular shape (the report,
the backlog dump) is built explicitly. A broken pipe (`| head`) ends quietly with exit code 141.

## Settings

`application/settings.py` lists every setting once (`SettingDef`): name, type, default, meaning, whether it is
secret. Parsing, `jira config show`, `jira doctor` and the generated configuration reference all read that list.
`bootstrap.effective_values` overlays the environment on the config file; the result is the immutable `Settings`
object handed to the application.

## Testing

| Level | Where | What it uses |
| --- | --- | --- |
| Domain | `tests/unit/test_domain.py`, `test_wiki.py`, `test_unwiki.py`, `test_searchterms.py`, `test_snippets.py`, `test_search_jql.py`, `test_mirror.py` | Nothing: plain functions. The exact output of the Markdown to wiki converter is pinned; the wiki to Markdown converter is round-tripped; hostile input is timed. |
| Use cases | `tests/unit/test_application_*.py` | `FakeGateway`, `MemoryFiles`, `MemoryConfigStore`: no HTTP, no files. |
| Adapters | `tests/integration/test_http_and_gateway.py`, `test_adapters.py` | `MockJira` (an HTTP server in a thread), temporary folders. |
| Command line | `tests/cli/` | `run_cli(...)` with fakes: output, exit codes, JSON shapes, help for every command, the tool descriptions for agents. |
| Whole program | `tests/integration/test_end_to_end.py`, `test_search_flow.py`, `test_sync_flow.py` | The real adapters, `MockJira` (which also evaluates the JQL of a search), a real folder, and a real child process for encoding, exit codes and pipes. |
| Rules | `tests/test_architecture.py`, `test_release.py`, `test_installer.py` | The source itself, the documentation, the installer. |

The suite runs offline in about half a minute. Run it with `python -m unittest discover -s tests -t .`.

## Decisions

- **Python, standard library only.** One interpreter is the only requirement on a managed laptop; for everything
  that talks to Jira a single process with one reused connection is cheaper than starting a program for almost every
  step. Imports that a command does not need are deferred (`http.client` and `ssl`
  load with the first request), so a command like `jira version` stays near the cost of starting Python.
- **No `pyproject.toml`, no `pip`.** The installer copies files and writes launchers; there is no build backend to
  install and nothing that can fail on a laptop without package access.
- **argparse behind our own description.** The framework is small and the standard library's parser is enough; the
  `Command` description is what the rest of the program depends on.
- **JSON is a contract.** Keys are documented, stable within a major version and checked against the docs.
- **The config file is plain `KEY=value` lines**, read as data and never executed.
- **Domain objects instead of Jira's JSON** inside the program: when Jira's JSON changes, one mapper changes.
- **A second Jira backend or another front end is an adapter.** A Jira Cloud gateway implements `JiraGateway`; another driving adapter calls the
  same `Application`: an MCP server (`jira mcp`) is one, a small HTTP service would be another.
- **Search and sync are use cases over the existing ports.** `FindIssues` (words to JQL, loosening, snippets) and
  `DiffMirror`, `PullMirror` and `PushMirror` (a folder and Jira, field by field against a record of the last
  agreement) need nothing but `JiraGateway`, `Files` and `Clock`; the pure parts (search terms, snippets, the
  conversion of Jira's markup to Markdown, the ticket file and its records) are domain code that the tests drive
  with plain strings.
- **The view for agents is generated, not written.** `jira commands --format mcp` is built from the same `Command`
  description, with its `when` text, that feeds the help and the reference, so the tool descriptions cannot drift.
  `jira mcp` is that driving adapter (`adapters/driving/cli/mcp_server.py`, `mcp_calls.py`, `mcp_paths.py`): it reads these descriptions, checks a call against the
  schema of its tool and against the working folder (the tables of `mcp_calls.py` say which property is a path, `mcp_paths.py` enforces it, with the same
  functions as the commands use to read a path (`domain.attachments.argument_paths`, `application.imports.MAP_HEADER`), so that the check and the use cannot
  drift apart) and runs the command in the same process on a new `Application` for every call; nothing else depends on it.

How to add a command, a setting or an adapter: [extend the CLI](how-to/extend-the-cli.md).
