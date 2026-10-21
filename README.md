# jira-dc-cli

A command line client for **Jira Data Center**, for people at a terminal and for programs and AI agents. It reads and searches
issues (a whole question works), moves them through the workflow, comments, links, creates and imports tickets from Markdown or
CSV, keeps a folder of Markdown tickets in step with Jira, reports progress by sprint and epic, and exports a backlog as JSON.
People use it in a terminal; programs and AI agents use it through `--json` and exit codes.

> **Not affiliated with Atlassian.** jira-dc-cli is an independent open source project. It is not made, endorsed or sponsored
> by Atlassian. Atlassian, Jira and Jira Data Center are trademarks of Atlassian; they are used here only to say which product
> this program talks to.

Version 1.0.0. It is written in Python with the **standard library only**: Python 3.10 or newer is the
only requirement. No pip, no curl, no jq, no PowerShell scripts. It is released under the [MIT License](LICENSE).

## Why use it

- **Nothing to install but Python.** One process and one reused HTTPS connection per command; no other
  program is started.
- **Safe by default.** Reading is free. Every command that changes Jira has `--dry-run`, which shows exactly what would be
  sent, and writes can be guarded with `--if-unchanged-since`. The token is saved with `jira setup-pat`, never printed, never
  accepted as a command line argument, and never sent over plain http unless you allow it.
- **Built for programs and AI agents.** `--json` on every command that returns data, stable
  [exit codes](docs/reference/exit-codes.md), errors as JSON on stderr, and `jira commands --json`, which
  describes the whole CLI (with when to use each command); `jira commands --format mcp` gives the commands as tool
  descriptions. `jira search` takes a whole question, says what it left out and shows the passage around the words,
  so an agent finds an issue without opening ten. A ready-made [skill](skills/jira-dc-cli/SKILL.md) teaches an agent to use it.
- **Markdown and Jira in step.** `jira diff`, `jira pull` and `jira push` compare a folder of Markdown tickets with Jira
  field by field against the last agreement, so a change on one side is never silently overwritten by the other.
- **Friendly to people.** Help with examples for every command, did-you-mean for typos, and `jira doctor`,
  which checks the setup step by step and says how to fix what is wrong.
- **Private by default.** `issue` and the comment list hide people and remove mentions and e-mail addresses from text. The
  commands that list people by nature (the assignee column of `issues`, the lead of a project, who you are in `ping`) can be
  switched off (`--no-assignee`, `--quiet`), and `JIRA_HIDE_USERS=true` forbids showing people anywhere, in errors included.
  The program talks only to the Jira address you configure: no telemetry, no update check, no other server.
- **Easy to extend.** Hexagonal architecture with the rules enforced by tests, one declarative place per
  command, and a [guide to extending it](docs/how-to/extend-the-cli.md).

## What has been verified

- The test suite runs **offline**, against a bundled mock Jira Data Center and in-memory fakes: no Jira, no network, no token.
  It covers every command, the installer, the documentation and the architecture rules.
- The commands that only **read** were also run, read-only, against a real Jira Data Center.
- The commands that **write** (`create`, `update`, `transition`, `comment`, `link`, the imports, `push`) were tested against
  the mock only. Try them with `--dry-run` first, and in a test project before a real one.
- It was developed and used on Windows (Git Bash, PowerShell, cmd), and a real Jira Data Center has only been used from there.
  The code uses nothing that is specific to Windows, and the `CI` workflow is set up to run the test suite on Linux, macOS and
  Windows with Python 3.10 to 3.13; the Actions tab of the repository shows the current result.

`jira doctor` checks the setup against your own server and token.

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
git switch -c "$(jira branch PROJ-123)"              # feature/PROJ-123-short-title
jira transition PROJ-123 --to "In Progress"          # or list the options: jira transition PROJ-123
jira comment PROJ-123 --text "Started."              # --file notes.md --wiki converts Markdown
jira link PROJ-123 --type Blocks --to PROJ-124 --dry-run
jira report --sprint active --epics                  # issues and story points by status and epic
jira export --project PROJ --out tmp/PROJ.json       # the whole backlog as JSON, no personal data
jira pull tmp/jira                                   # one Markdown file per issue, to search together with the code
jira diff docs/tickets                               # a folder of Markdown tickets against Jira, field by field
```

The [daily workflow](docs/how-to/daily-workflow.md) shows more recipes.

## For programs and AI agents

```bash
jira commands --json                  # every command, argument, example, when to use it, output shape and exit code
jira commands --format mcp            # the read-only commands as tool descriptions (MCP tools/list shape)
jira search "a question" --snippets 3 --json   # find before you read
jira issue PROJ-123 --json            # one compact JSON line when piped
jira skill install                    # copies the skill into .github/skills for GitHub Copilot
```

Read [use the CLI with AI agents](docs/how-to/use-with-ai-agents.md), [find information in Jira](docs/how-to/find-information.md) and the
[JSON output reference](docs/reference/json-output.md).

## Commands

The full text of every command, with examples, is available offline: `jira help COMMAND`. The same text
is in the [command reference](docs/reference/commands.md).

<!-- commands:start -->

**Read**

| Command | What it does |
| --- | --- |
| `jira issue` | Show one issue |
| `jira issues` | Search and list issues |
| `jira search` | Find issues by words, also a whole question |
| `jira report` | Progress by status, sprint, fix version and epic |
| `jira branch` | Print a branch name for an issue |
| `jira export` | Export a project's backlog as JSON |
| `jira fields` | List Jira fields and their ids |

**Work on an issue**

| Command | What it does |
| --- | --- |
| `jira transition` | Move an issue to another status |
| `jira comment` | Add a comment, or list the comments |
| `jira link` | Link two issues |
| `jira link-types` | List the link types |
| `jira update` | Change fields of an issue |
| `jira update-json` | Update an issue from a complete JSON payload |

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

**Setup and information**

| Command | What it does |
| --- | --- |
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
| `jira wiki` | Convert Markdown to Jira wiki markup |
| `jira api` | Call any Jira REST endpoint |

<!-- commands:end -->

## Documentation

| Topic | Where |
| --- | --- |
| Install, update, uninstall | [docs/how-to/install-and-update.md](docs/how-to/install-and-update.md) |
| Configure the connection | [docs/how-to/configure.md](docs/how-to/configure.md) |
| Daily workflow | [docs/how-to/daily-workflow.md](docs/how-to/daily-workflow.md) |
| Use with AI agents | [docs/how-to/use-with-ai-agents.md](docs/how-to/use-with-ai-agents.md) |
| Find information in Jira (people and agents) | [docs/how-to/find-information.md](docs/how-to/find-information.md) |
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
