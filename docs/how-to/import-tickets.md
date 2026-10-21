# Import tickets from Markdown or CSV

Three commands create or update many issues from files: `import-md` (one Markdown file), `import-md-dir` (a
folder of them) and `import-csv` (a spreadsheet). They share one rule: **a ticket with an `issue_key` updates
that issue, a ticket without one creates an issue.** Always start with `--dry-run`.

Templates to copy: `examples/ticket-create.md`, `examples/tickets-create.csv`, `examples/issues.csv`.

## Before the first import

1. Run `jira doctor`: connection, token, custom fields and the current project must be fine.
2. Find out what the project accepts: `jira project KEY` (issue types) and `jira metadata KEY` (create fields).
   `jira metadata KEY ISSUE_TYPE_ID` lists the fields of one issue type and which are required.
3. Check that components and fix versions exist: Jira refuses unknown ones. The importer checks fix versions
   itself and refuses with a clear message (see below); components are checked by Jira.
4. Create **one** issue with `jira create --dry-run`, then for real in a safe project.
5. Import two or three tickets, look at them in Jira, then import the rest.
6. Keep the output as the import log. Rotate the token if it was exposed.

## A Markdown ticket

Simple front matter between `---` lines, one `key: value` per line; the body becomes the description.

```markdown
---
project: DEMO
issuetype: Story
summary: User can export the monthly report
labels: reporting,backlog
priority: Medium
components: Reporting
fix_versions: Release 1.0
fields_json: {"customfield_12345": "Example"}
---
## Goal

Allow users to export the monthly report.

## Acceptance criteria

- The export action is visible to authorized users.
```

| Key | Meaning |
| --- | --- |
| `issue_key` | Present: update that issue (every field present in the front matter is rewritten). Absent: create. |
| `project` | Project key. Absent: the current project (`jira use`). |
| `issuetype` | Required to create. The name or id of an issue type the project can create (`Story`, `Task`, ...). |
| `summary` | Required to create. |
| `description` | Optional; replaces the body as the description. |
| `labels`, `components`, `fix_versions` | Comma lists. Components and fix versions must already exist. |
| `priority`, `assignee` | A priority name; an assignee user name. |
| `fields_json` | A JSON object on one line for any other field, such as custom fields. A field that has its own key here (`summary`, `labels`, `priority`, ...) cannot be set in `fields_json` too; the CLI refuses instead of guessing which should win. |
| `source_id`, `parent_id`, `source_key` | For `import-md-dir`: see below. |

`--wiki` (also `--jira-wiki`) converts the body from Markdown to Jira wiki markup: headings, lists, quotes, code
blocks, bold and italic, links. Jira would otherwise show the Markdown characters as text. The same converter is
`jira wiki FILE`; you can see the result before importing.

```bash
jira import-md examples/ticket-create.md --dry-run --wiki     # shows the payload, sends nothing
jira import-md examples/ticket-create.md --wiki               # creates the issue
```

Do not use `import-md` to change one field of an existing issue: it rewrites every field of the front matter.
Use `jira update` or `jira update-json` for that, or edit the file you got from `jira pull` and send the change with `jira push FILE`, which sends
only the fields you edited since the last sync and refuses when Jira changed the same field meanwhile
([export and sync](export-and-sync.md#keep-a-folder-of-markdown-tickets-in-step-with-jira)).

## A folder of Markdown tickets

```bash
jira import-md-dir ./tickets --dry-run
jira import-md-dir ./tickets --wiki --continue-on-error
```

- Files named `NNN-*.md` (three digits) are imported **in name order**; other files are ignored.
- `source_id` is the logical id of a ticket in your source. `parent_id` names the `source_id` of an earlier ticket,
  usually an epic. The importer resolves it to the key Jira returned and sets the **Epic Link** of the story, so
  import epics first (a lower number). `source_key` is optional: the key your source gives the ticket (for example
  `SRC-001`), which is only copied into the map file.
- The Epic Link field is found by name; set `JIRA_EPIC_LINK_FIELD` if your Jira names it differently. Creating an
  **Epic** usually also needs the Epic Name field: pass it with `fields_json`.
- Jira keys are never predicted. After each creation of a ticket that has a `source_id`, the importer appends a row to
  the **map file** (`DIRECTORY/jira-import-map.csv`, or `--map-file FILE`):

  ```text
  source_id,source_key,jira_key,issue_type,status,file
  E-1,SRC-001,PROJ-101,Epic,Created,001-epic.md
  ```

  A tool such as a Markdown mirror can read it to learn which key belongs to which source. The `source_key` column
  keeps whatever `source_key:` the front matter held.
- **Resuming:** a source id that is in the map with status `Created` is skipped, so an interrupted run can be
  started again, and a second run creates nothing twice. Every `source_id` must be unique in the folder: two files
  with the same one are refused before anything is created.
- The map file (and its folders) is created before the first issue, so a created issue is always recorded. If the map
  cannot be updated later (disk full), the run stops at once and the output names the key that was created but not
  recorded: add that row by hand before you run the import again.
- A dry run neither creates issues nor writes the map. A story whose parent is created earlier in the same run shows
  a placeholder as its Epic Link (`<key of E-1, assigned when it is created>`); a parent that is neither in the map nor
  earlier in the folder is reported as a failure, so a wrong order shows up in the dry run.
- **Failures:** the run stops at the first failure and exits with that error's code. With `--continue-on-error` it
  goes on and still exits with the first error's code; the summary line counts created, updated, skipped and
  failed files.
- **Fix versions** are checked before the issue is created. An unknown one refuses the ticket; with
  `--skip-invalid-fix-version` the issue is created without it, and a warning names it.

## A CSV file

Columns, with these header names. A row that creates needs `issuetype` and `summary`, and a `project` unless one is
selected with `jira use`; a row that updates needs `issue_key` and the columns to change:

```text
issue_key,project,issuetype,summary,description,labels,priority,assignee,components,fix_versions,fields_json
```

```csv
issue_key,project,issuetype,summary,description,labels,priority,assignee,components,fix_versions,fields_json
,DEMO,Task,Imported task,"Description with, a comma","import,backlog",Medium,,Platform,,"{""customfield_12345"":""Value""}"
DEMO-123,,,Updated summary,Updated from CSV,ready,,,,,"{}"
```

- An empty `issue_key` creates, a filled one updates; an update changes only the columns that are filled.
- Lists are comma separated inside the cell; quote cells that hold commas or line breaks. UTF-8, with or without a
  byte order mark.
- `type` and `fixVersions` are accepted as alternative names of `issuetype` and `fix_versions`.
- Errors name the line (`line 4`); the header is line 1.

```bash
jira import-csv examples/issues.csv --dry-run
jira import-csv examples/issues.csv                      # stops at the first failure
jira import-csv examples/issues.csv --continue-on-error  # reports all failures at the end
```

## A complete Jira payload

When you need exact control, send the payload yourself:

```bash
jira create-json create.json --dry-run
jira update-json DEMO-123 update.json --if-unchanged-since 2026-10-15T08:00:00Z
```

`create.json` is `{"fields": {"project": {"key": "DEMO"}, "issuetype": {"name": "Task"}, "summary": "..."}}`;
`update.json` holds `fields` and/or `update`. Jira validates every field against the project configuration.

## The output

Text: one line per file or row (`created`, `updated`, `skipped`, `would create` in a dry run, `FAILED` with the
reason) and a summary. With `--json`: `dryRun`, `mapFile`, `created`, `updated`, `skipped`, `failed` and
`outcomes[]` with `file`, `action`, `key`, `dryRun`, `message`.

## Recommended workflow

1. `jira doctor`, then `jira metadata KEY` and `jira project KEY`.
2. One issue with `jira create --dry-run`, then a real test issue in a safe project.
3. A small CSV or folder with `--dry-run`, then for real with two or three items; check them in Jira.
4. The rest, with `--continue-on-error` and the output saved: `jira import-md-dir ./tickets --wiki | tee import.log`.
5. Repeat the same command after fixing failures; what was created is skipped.

## Example profile: the PROJ project

Fields confirmed for the PROJ Jira project: Epic Name `customfield_10005`, Epic Link `customfield_10002`,
Story Points `customfield_10001`, Acceptance criteria (DoD) `customfield_10004`; component
`Reporting`; the priority `Highest` is called `Very High`. Epic Link, Story Points and the acceptance
criteria are found by name; only the Epic Name has to be given in `fields_json` when you create an epic. Fix
versions must exist before a file names them.
