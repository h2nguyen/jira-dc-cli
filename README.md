# jira-dc-cli

A command line client for **Jira Data Center**, for people at a terminal and for programs and AI agents. It reads and searches
issues (a whole question works; several issues with one call, a long one in parts), moves them through the workflow, comments, assigns, links,
watches, votes and logs work, saves and adds attachments, creates and imports tickets from Markdown or CSV, keeps a folder of Markdown tickets in step with Jira,
shows boards and sprints and reviews a sprint, reports progress by sprint and epic, and exports a backlog as JSON.
People use it in a terminal; programs and AI agents use it through `--json` and exit codes, or as a local MCP server (`jira mcp`).

> **Not affiliated with Atlassian.** jira-dc-cli is an independent open source project. It is not made, endorsed or sponsored
> by Atlassian. Atlassian, Jira and Jira Data Center are trademarks of Atlassian; they are used here only to say which product
> this program talks to.

Version 1.1.0. It is written in Python with the **standard library only**: Python 3.10 or newer is the
only requirement. No pip, no curl, no jq, no PowerShell scripts. It is released under the [MIT License](LICENSE).
It is written for **Jira Data Center 11.3.x** (see [Compatibility](#compatibility)).

## Why use it

- **Nothing to install but Python.** One process and one reused HTTPS connection per command; no other
  program is started.
- **Safe by default.** Reading is free. Every command that changes Jira has `--dry-run`, which shows exactly what would be
  sent, and writes can be guarded with `--if-unchanged-since`. The token is saved with `jira setup-pat`, never printed, never
  accepted as a command line argument, and never sent over plain http unless you allow it.
- **Built for programs and AI agents.** `--json` on every command that returns data, stable
  [exit codes](docs/reference/exit-codes.md), errors as JSON on stderr, and `jira commands --json`, which
  describes the whole CLI (with when to use each command); `jira commands --format mcp` gives the commands as tool
  descriptions, and `jira mcp` serves them to an MCP client (read-only unless it is started with `--allow-write` or `--allow-files`). `jira search` takes a whole question, says what it left out and shows the passage around the words,
  so an agent finds an issue without opening ten. A ready-made [skill](skills/jira-dc-cli/SKILL.md) teaches an agent to use it.
- **Markdown and Jira in step.** `jira diff`, `jira pull` and `jira push` compare a folder of Markdown tickets with Jira
  field by field against the last agreement (the links between issues too), so a change on one side is never silently overwritten by the other.
- **Attachments.** `jira attachments`, `jira download` and `jira attach` list, save, read (text files and Word, Excel and PowerPoint documents) and
  add the files of an issue, with safe names, a size limit and the token sent only to your Jira. `attach` refuses the configuration of the CLI and any file that holds the token.
- **Friendly to people.** Help with examples for every command, did-you-mean for typos, and `jira doctor`,
  which checks the setup step by step and says how to fix what is wrong.
- **Private by default.** `issue`, the comment list and the saved filters hide people and remove mentions and e-mail addresses from text (the JQL
  of a filter shows `[user]` for the people it names). The commands that list people by nature (the assignee column of `issues`, the lead of a
  project, who you are in `ping`) can be switched off (`--no-assignee`, `--quiet`), and `JIRA_HIDE_USERS=true` removes people from everything the commands
  print (names, display names, e-mail addresses, avatars, the people in lists, histories and saved filters); the few exceptions are in
  [SECURITY.md](SECURITY.md#personal-data-in-page-content).
  The program talks only to the Jira address you configure: no telemetry, no update check, no other server.
- **Easy to extend.** Hexagonal architecture with the rules enforced by tests, one declarative place per
  command, and a [guide to extending it](docs/how-to/extend-the-cli.md).

## What has been verified

- The test suite runs **offline**, against a bundled mock Jira Data Center and in-memory fakes: no Jira, no network, no token.
  It covers every command, the installer, the documentation and the architecture rules.
- The commands of version 1.0.0 that only **read** were also run, read-only, against a real Jira Data Center. What came after it (the entries
  of version 1.1.0 in the [changelog](CHANGELOG.md)) was tested against the mock only: no Jira 11.3 server was available.
- The commands that **write** (`create`, `update`, `transition`, `comment`, `link`, the imports, `push`, and every other command marked `*` in
  `jira help --brief`) were tested against the mock only. Try them with `--dry-run` first, and in a test project before a real one.
- It was developed and used on Windows (Git Bash, PowerShell, cmd), and a real Jira Data Center has only been used from there.
  The code uses nothing that is specific to Windows, and the `CI` workflow is set up to run the test suite on Linux, macOS and
  Windows with Python 3.10 to 3.13; the Actions tab of the repository shows the current result.

`jira doctor` checks the setup against your own server and token.

## Compatibility

jira-dc-cli is written for **Jira Data Center 11.3.x**: the REST API 2 (`/rest/api/2`) and the Jira Software API (`/rest/agile/1.0`) as the
[Jira Data Center 11.3 REST API reference](https://developer.atlassian.com/server/jira/platform/rest/v11003/intro/) describes them.

- **Every call is checked against that reference.** The list of its calls (derived from the OpenAPI document of 11.3.12) sits in the tests, and
  the mock Jira refuses a call that is not in it: a command cannot depend on an endpoint of another version, of Jira Cloud or an internal one. The one
  address that is no REST call is the content of an attachment, `GET /secure/attachment/{id}/{name}`, which `jira download` reads.
  What this does not prove is on the [compatibility page](docs/reference/compatibility.md), which also lists what the commands cover and what stays
  with `jira api`.
- `jira doctor` compares your server with 11.3.x (`ok` on the same release line and in the same major release, `warn` on an older or newer major
  release and on Jira Cloud, which is not supported); `jira version --json` names the release line.
- Releases before 11.3 (9.x, 10.x) were not tested: most commands are expected to work, because the REST API 2 changes little between releases,
  and `jira doctor` says so.

## Install

You need Python 3.10 or newer (`python --version`) and Git. Install from a clone of the repository, in Git Bash,
PowerShell or cmd:

```bash
git clone https://github.com/h2nguyen/jira-dc-cli.git
cd jira-dc-cli
python install.py --dry-run    # shows what would happen
python install.py
```

Without Git, unpack the source archive of a release and run the same two `python install.py` commands in that folder.
The installer copies the program to your local profile (`%LOCALAPPDATA%\jira-dc-cli` on Windows,
`~/.local/share/jira-dc-cli` elsewhere) and writes the launchers `jira` (Git Bash, Linux, macOS) and `jira.cmd` (cmd and
PowerShell) to `~/bin`. Your configuration stays where it is. To update, pull the new source and run `python install.py` again.
Details, updating and uninstalling: [install and update](docs/how-to/install-and-update.md).

## First steps

```bash
jira setup-pat                                              # asks for the token; the input is not shown
jira config set JIRA_BASE_URL https://jira.example.com/jira
jira use PROJECTKEY                                         # remember the project
jira doctor                                                 # checks everything, says how to fix problems
```

More on the settings: [configure the connection](docs/how-to/configure.md).

## A tour

```bash
jira issues --mine                                   # your open issues
jira search "how do I export the monthly report?" --snippets 3   # a whole question; the passage around your words
jira issue PROJ-123 --comments                       # one issue with its comments
jira issue PROJ-1 PROJ-2 PROJ-3 --only summary,status   # several issues with one request, only the parts you need
jira issue PROJ-123 --outline                        # the headings of a long description; --section TITLE reads one part
jira filters                                         # your saved filters; jira issues --filter ID runs one
jira download PROJ-123 --all --out tmp/files         # save the attachments (safe names, a size limit, no overwrite); --text reads a text or Office file
git switch -c "$(jira branch PROJ-123)"              # feature/PROJ-123-short-title
jira transition PROJ-123 --to "In Progress"          # or list the options: jira transition PROJ-123
jira comment PROJ-123 --text "Started."              # --file notes.md --wiki converts Markdown
jira comment PROJ-123 --edit 10100 --file fixed.md --wiki --dry-run   # correct a comment you wrote; the dry run shows a diff
jira users resolve "Jane Doe"                                         # the user name for --assignee, no people printed
jira assign PROJ-123 "Jane Doe" --dry-run                             # also unassigned, automatic, me
jira link PROJ-123 --type Blocks --to PROJ-124 --dry-run
jira report --sprint active --epics                  # issues and story points by status and epic
jira sprint 12 --review                              # committed, completed, added, removed, carried over, from the change history
jira permissions --issue PROJ-123 --only EDIT_ISSUES,TRANSITION_ISSUES   # may I? before a write
jira log-work PROJ-123 '1h 30m' --comment 'Reviewed.' --dry-run          # time on an issue; also watch, vote, remote-link
jira export --project PROJ --out tmp/PROJ.json       # the whole backlog as JSON, no personal data
jira pull tmp/jira                                   # one Markdown file per issue, to search together with the code
jira diff docs/tickets                               # a folder of Markdown tickets against Jira, field by field
```

The [daily workflow](docs/how-to/daily-workflow.md) shows more recipes.

## For programs and AI agents

```bash
jira commands --json                  # every command, argument, example, when to use it, output shape and exit code
jira commands --format mcp            # the commands that neither change Jira nor write files, as tool descriptions (MCP tools/list shape)
jira mcp                              # a local MCP server (stdio) for VS Code and other clients: read-only; --allow-write, --allow-files
jira search "a question" --snippets 3 --json   # find before you read
jira issue PROJ-123 --json            # one compact JSON line when piped
jira skill install                    # copies the skill into .github/skills for GitHub Copilot
```

Read [use the CLI with AI agents](docs/how-to/use-with-ai-agents.md), [use the CLI as an MCP server](docs/how-to/mcp-server.md),
[find information in Jira](docs/how-to/find-information.md) and the [JSON output reference](docs/reference/json-output.md).

## Commands

The full text of every command, with examples, is available offline: `jira help COMMAND`. The same text
is in the [command reference](docs/reference/commands.md).

<!-- commands:start -->

**Read**

| Command | What it does |
| --- | --- |
| `jira issue` | Show one or several issues |
| `jira issues` | Search and list issues |
| `jira search` | Find issues by words, also a whole question |
| `jira report` | Progress by status, sprint, fix version and epic |
| `jira branch` | Print a branch name for an issue |
| `jira export` | Export a project's backlog as JSON |
| `jira fields` | List Jira fields and their ids |
| `jira filters` | List your favourite saved filters |
| `jira filter` | Show one saved filter |
| `jira attachments` | List the attachments of an issue |
| `jira download` | Save an attachment as a file, or read it as text |
| `jira watchers` | Show who watches an issue |
| `jira votes` | Show the votes of an issue |
| `jira worklogs` | List the work logged on an issue |
| `jira remote-links` | List the web links of an issue |

**Work on an issue**

| Command | What it does |
| --- | --- |
| `jira attach` | Attach files to an issue |
| `jira transition` | Move an issue to another status |
| `jira comment` | Add, read or edit a comment |
| `jira link` | Link two issues |
| `jira link-types` | List the link types |
| `jira update` | Change fields of an issue |
| `jira update-json` | Update an issue from a complete JSON payload |
| `jira watch` | Start or stop watching an issue |
| `jira vote` | Vote for an issue, or withdraw your vote |
| `jira log-work` | Log work on an issue |
| `jira remote-link` | Add a web link to an issue |
| `jira users` | Find the user name for an assignee |
| `jira assign` | Assign an issue to a user |

**Agile**

| Command | What it does |
| --- | --- |
| `jira boards` | List the boards of Jira Software |
| `jira sprints` | List the sprints of a board |
| `jira sprint` | Show a sprint, its issues or its review |
| `jira velocity` | Committed and completed points per closed sprint |
| `jira backlog` | List the backlog of a board in rank order |
| `jira epics` | List the epics of a board |
| `jira sprint-add` | Move issues into a sprint |
| `jira rank` | Rank issues before or after another issue |
| `jira epic-add` | Move issues into an epic, or out of their epic |
| `jira sprint-create` | Create a future sprint |
| `jira sprint-update` | Rename, re-date, start or complete a sprint |

**Create and import**

| Command | What it does |
| --- | --- |
| `jira create` | Create one issue |
| `jira create-json` | Create one issue from a JSON payload |
| `jira import-md` | Create or update one issue from a Markdown file |
| `jira import-md-dir` | Import all Markdown tickets of a folder |
| `jira import-csv` | Create or update issues from a CSV file |

**Sync with files**

| Command | What it does |
| --- | --- |
| `jira diff` | Compare a folder of Markdown tickets with Jira |
| `jira pull` | Bring Jira into a folder of Markdown tickets |
| `jira push` | Send what you edited in a Markdown ticket to Jira |

**Projects**

| Command | What it does |
| --- | --- |
| `jira projects` | List the projects you can see |
| `jira projects-admin` | List the projects you administer |
| `jira project` | Show one project |
| `jira metadata` | Show what a project can create |

**Reference**

| Command | What it does |
| --- | --- |
| `jira priorities` | List the issue priorities |
| `jira resolutions` | List the issue resolutions |
| `jira issue-types` | List the issue types |
| `jira statuses` | List the statuses and what counts as done |
| `jira components` | List the components of a project |
| `jira fix-versions` | List the versions of a project |
| `jira permissions` | Show what your account may do |
| `jira roles` | List the roles of a project |
| `jira dashboards` | List dashboards |

**Setup and information**

| Command | What it does |
| --- | --- |
| `jira server` | Show the Jira version and its settings |
| `jira setup-pat` | Save the personal access token |
| `jira remove-pat` | Remove the stored token |
| `jira use` | Select the current project |
| `jira current` | Print the current project |
| `jira clear-project` | Forget the current project |
| `jira config` | Show or change the settings |
| `jira ping` | Check the connection and the token |
| `jira doctor` | Check the setup and say how to fix it |
| `jira help` | Show the commands, or the help of one command |
| `jira commands` | List every command, for programs |
| `jira version` | Show the version |
| `jira changelog` | Show the release history |
| `jira how-to` | Read the guides that ship with the CLI |
| `jira skill` | Show or install the AI agent skill |

**Tools**

| Command | What it does |
| --- | --- |
| `jira mcp` | Run a local MCP server for AI tools |
| `jira wiki` | Convert Markdown to Jira wiki markup |
| `jira api` | Call any Jira REST endpoint |

<!-- commands:end -->

## Documentation

| Topic | Where |
| --- | --- |
| Install, update, uninstall | [docs/how-to/install-and-update.md](docs/how-to/install-and-update.md) |
| Configure the connection | [docs/how-to/configure.md](docs/how-to/configure.md) |
| Daily workflow | [docs/how-to/daily-workflow.md](docs/how-to/daily-workflow.md) |
| Comments, people and checked dry runs (edit a comment, find a user name, assign, validate values) | [docs/how-to/comments-and-people.md](docs/how-to/comments-and-people.md) |
| Use with AI agents | [docs/how-to/use-with-ai-agents.md](docs/how-to/use-with-ai-agents.md) |
| Use the CLI as an MCP server (VS Code and other clients) | [docs/how-to/mcp-server.md](docs/how-to/mcp-server.md) |
| Find information in Jira (people and agents) | [docs/how-to/find-information.md](docs/how-to/find-information.md) |
| Read issues in batches and in parts, saved filters, queries from files | [docs/how-to/read-issues.md](docs/how-to/read-issues.md) |
| Sprints, boards and the sprint review | [docs/how-to/sprints-and-boards.md](docs/how-to/sprints-and-boards.md) |
| Collaborate on an issue and read the facts of a Jira (watch, vote, log work, web links, permissions, server) | [docs/how-to/collaborate-and-reference.md](docs/how-to/collaborate-and-reference.md) |
| Attachments: list, save, read as text, add | [docs/how-to/attachments.md](docs/how-to/attachments.md) |
| Import tickets from Markdown or CSV | [docs/how-to/import-tickets.md](docs/how-to/import-tickets.md) |
| Export a backlog, keep Markdown tickets in step with Jira | [docs/how-to/export-and-sync.md](docs/how-to/export-and-sync.md) |
| Extend the CLI | [docs/how-to/extend-the-cli.md](docs/how-to/extend-the-cli.md) |
| Troubleshooting | [docs/how-to/troubleshooting.md](docs/how-to/troubleshooting.md) |
| Release a new version | [docs/how-to/release-process.md](docs/how-to/release-process.md) |
| Architecture | [docs/architecture.md](docs/architecture.md) |
| Reference: commands, settings, exit codes, JSON | [docs/reference/](docs/reference/commands.md) |
| Release history | [CHANGELOG.md](CHANGELOG.md) |

All of it ships with the CLI and reads offline: `jira how-to`, `jira how-to TOPIC`, `jira changelog`.

## Getting help

- Run `jira doctor`. It checks the setup step by step and says how to fix what is wrong. `jira help COMMAND` shows the options
  and examples of one command, and [troubleshooting](docs/how-to/troubleshooting.md) lists the symptoms.
- Search the existing issues on the Issues tab of this repository, then open a bug report or a feature request with the
  matching form. For a question that the documentation does not answer, use the feature request form and start the title
  with "Question:".
- **Never paste a token, the name of a real site or real Jira content into an issue.** Redact them. How to write a good
  report is in [CONTRIBUTING.md](CONTRIBUTING.md).
- A security problem is not an issue: report it privately as described in [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome, from a typo in a guide to a new command. [CONTRIBUTING.md](CONTRIBUTING.md) explains what the
project is for, how to set up and run the checks, the architecture rules that the tests enforce and what a pull request
needs. Everyone who takes part follows the [Code of Conduct](CODE_OF_CONDUCT.md). The project is maintained on a best-effort
basis; expect an answer in days, not hours.

## Platforms and TLS

TLS uses the certificate store of the operating system; a private company CA can be named with `JIRA_CA_BUNDLE`. What was
tested where is under "What has been verified" above.

## Development

```bash
python -m unittest discover -s tests -t .      # the whole suite: offline, against a mock Jira
python scripts/generate_docs.py                # regenerate docs/reference and the command table above
python scripts/check_release.py                # version, changelog, docs and skill agree
```

The suite needs no Jira and no network. It includes tests that enforce the architecture rules (layers,
standard library only), that every command is documented with examples, and that the documentation is
current. See [CONTRIBUTING.md](CONTRIBUTING.md), [extend the CLI](docs/how-to/extend-the-cli.md) and
[architecture](docs/architecture.md).

## Layout

```text
src/jira_dc_cli/        the program: domain, application, adapters (driven, driving), bootstrap
tests/                  unit, integration (mock Jira), command line and architecture tests
docs/                   how-to guides, reference, architecture
skills/jira-dc-cli/     the AI agent skill
examples/               ticket templates in Markdown and CSV
scripts/                generate_docs.py, check_release.py
.github/                issue forms, pull request template, CI and security workflows
install.py  jira.py     installer and launcher
LICENSE  CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md  CHANGELOG.md
```

## License and trademarks

jira-dc-cli is released under the [MIT License](LICENSE). The [Code of Conduct](CODE_OF_CONDUCT.md) is the Contributor
Covenant 3.0, which has its own license (CC BY-SA 4.0, see the attribution in that file). Atlassian, Jira and Jira Data Center
are trademarks of Atlassian. This project is independent: it is not affiliated with, endorsed by or sponsored by Atlassian.
