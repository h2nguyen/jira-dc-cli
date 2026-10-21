# Changelog

All notable changes to jira-dc-cli are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`.

## [Unreleased]

### Added

### Changed

### Fixed

### Security

## [1.0.0] - 2026-10-18

jira-dc-cli is a command line client for Jira Data Center, for people at a terminal and for programs and AI agents. It is
written in Python 3.10 or newer with the standard library only. Every command is tested offline against a bundled mock Jira
Data Center and in-memory fakes; the commands that only read were also run, read-only, against a real Jira Data Center. The
commands that write were tested against the mock only: use `--dry-run` first. It was developed and used on Windows (Git Bash,
PowerShell and cmd).

### Read and navigate Jira

- `jira issue` shows one issue (with `--comments` its comments), `jira issues` searches and lists issues (`--mine`, `--all`,
  filters, `--jql`), `jira report` shows progress by status, sprint, fix version and epic, `jira branch` prints a branch name
  for an issue, `jira fields` lists field ids, and `jira projects`, `jira projects-admin`, `jira project`, `jira metadata`
  and `jira link-types` show projects, what a project can create and the link types.
- Custom fields (story points, epic link, sprint, acceptance criteria) are found by name once and cached per Jira address
  (`JIRA_NO_CACHE`, `jira fields --refresh`); `issues --fields` accepts the logical names `story_points`, `epic_link`,
  `sprint` and `acceptance` next to ids. `JIRA_*_FIELD` settings name a field that cannot be found by name.
- `jira export` writes a whole project backlog as JSON in a stable format ("backlog dump v1"), with the custom fields and
  without personal data, and refuses to write a partial dump (exit code 7).

### Search that works for questions

- `jira search` turns the text into words and `"phrases"` (several words need no quotes), leaves out filler words (English and
  German), the punctuation at the end of a word and the characters that make Jira's text search fail with HTTP 400, and says
  what it left out (`terms`, `ignored`). It searches one project (the current one, `--project`, or `--all-projects`) and
  filters with `--type`, `--status`, `--label`, `--under EPIC`, `--sprint`, `--fix-version`, `--since 7d` and `--jql`. Every
  hit has its epic (with the name), status, labels, sprints, story points and address.
- When fewer than five issues hold every word it also shows the issues that hold some, best match first, and marks the ones
  with every word (`relaxed`, `match`, `allWords`); `--match all|any|auto` chooses.
- `--snippets N` cuts the passage around the words from the description and the acceptance criteria of the first N hits, from
  the text that came with the search (no extra request), redacted and shown as Markdown text.

### Work on issues, safely

- `jira transition`, `jira comment`, `jira link`, `jira update`, `jira update-json`, `jira create` and `jira create-json`
  change Jira. Every one has `--dry-run`, which shows the very payload a real run sends, and a write can be guarded with
  `--if-unchanged-since` (exit code 3, nothing written, when somebody changed the issue in between).
- A field that has its own option cannot also be set in `--fields-json`: the CLI refuses instead of guessing which wins.
  Writes are never retried, because a repeated write could duplicate.
- `jira transition --to` takes a status, a transition name or a transition id; when two transitions lead to the same status
  it refuses and lists them. `jira link` takes the exact link type name, can reverse the direction (`--inverse`), says the
  relation in words and reads it back from Jira.
- Issue types are resolved per project by name or id (`jira create --type-id`, `--refresh-metadata`), and the reporter is the
  token's user unless `JIRA_REPORTER` or `--reporter` says otherwise.
- `--wiki` converts Markdown to Jira wiki markup (headings, lists, quotes, code, bold, italic, links) in `create`, `update`,
  `comment` and the imports; `jira wiki` shows the result without calling Jira.

### Create and import from files

- `jira import-md` (one Markdown file with front matter), `jira import-md-dir` (a folder of `NNN-*.md` files) and
  `jira import-csv` (a spreadsheet) share one rule: a ticket with an `issue_key` updates that issue, a ticket without one
  creates an issue. All of them support `--dry-run` and `--continue-on-error`.
- `import-md-dir` links a story to its epic through `source_id` and `parent_id`, writes a map file
  (`jira-import-map.csv`) after every created issue, resumes an interrupted run without creating anything twice, refuses two
  files with one `source_id`, and checks fix versions before it creates the issue (`--skip-invalid-fix-version`).

### Markdown tickets and Jira in step

- `jira diff DIR` compares a folder of Markdown tickets with Jira, `jira pull DIR` writes the issues that have no file (and
  with `--update` the fields that Jira changed), `jira push FILE` sends the fields you edited.
- Every field is compared with what the file and Jira last agreed on (the `jira_base` line written by `pull` and `push`), so
  an edit of one field and a change of another are not a conflict, while the same field changed on both sides is (exit code 3,
  nothing overwritten or sent).
- `push` never reverts what only Jira changed, never sends a placeholder (`[~user]`, `[email]`, `[personal link]`), sends a
  description only when the way to Markdown and back changes nothing (`lossy:` names what would change; `--force` sends it
  anyway), checks again just before the write that the issue did not change, then reads it back. `pull` never deletes a file and
  never overwrites an edit of yours without `--force`.
- Jira's wiki markup becomes Markdown on the way in (headings, lists, bold, italic, code, links, quotes, tables).

### Built for programs and AI agents

- `--json` on every command that returns data (or `JIRA_OUTPUT=json`), with documented, stable camelCase keys; exit codes 0
  to 7 with a fixed meaning; errors as one JSON object on standard error; results on standard output, messages on standard
  error; no prompts except `setup-pat`, which also reads the token from standard input.
- `jira commands --json` describes every command (arguments, examples, when to use it, the JSON it prints, whether it
  changes data), the exit codes and the settings. `jira commands --format mcp [--only read|write|all]` prints the commands in
  the shape of an MCP `tools/list` answer; no MCP server is included.
- `jira skill` prints a skill that teaches an AI agent to use the CLI safely, and `jira skill install` copies it into
  `.github/skills` or any skills folder.
- `jira api METHOD PATH` calls any REST endpoint (GET is free, POST and PUT need `--write`, DELETE is not offered).

### Privacy and safety

- `issue` and the comment list hide people. Mentions, e-mail addresses and personal share links are removed from text in
  `issue`, the comment list, search snippets, exports and pulled files. `JIRA_HIDE_USERS=true` forbids showing people
  anywhere, makes `--with-users` an error, and applies the same removal to Jira's error messages and to the raw output of
  `jira api`.
- The token is saved with restricted permissions by `jira setup-pat`, never printed, logged or accepted as a command line
  argument. A plain `http://` address is refused unless you allow it (`JIRA_ALLOW_HTTP`); `localhost` is always allowed.
- The settings file is read as data and never executed: `$(...)`, backticks and `$VAR` outside single quotes are refused
  with a message.
- A text of tens of thousands of characters, or a hostile one, is converted and redacted in bounded time.

### Setup, installation and documentation

- `jira setup-pat`, `jira config show|path|set|unset`, `jira use KEY` and `jira ping` set up and check the connection;
  `jira doctor` checks the configuration, the token, TLS, the connection, the server version, the custom fields and the
  current project, and says how to fix each finding.
- `python install.py` (also `--dry-run`, `--uninstall`, `--purge`) installs the program for the current user into a local
  folder, builds the new copy next to the old one and swaps them, and writes the launchers `jira` and `jira.cmd`. It
  replaces and removes only what it recognises as its own.
- Help with examples for every command, did-you-mean for typos, and guides that ship with the program and read offline
  (`jira how-to`, `jira changelog`).

### Quality

- Hexagonal architecture (domain, application with ports, driven and driving adapters, a composition root) with the layering
  rules, the standard-library-only rule and the command registration enforced by tests.
- The command reference, the settings, the exit codes and the command table in the README are generated from the command
  descriptions, and `scripts/check_release.py` checks that the version, the changelog, the generated files, the skill and
  the links agree.
