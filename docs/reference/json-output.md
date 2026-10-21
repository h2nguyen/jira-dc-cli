# JSON output and errors

Every command that returns data has `--json` (or set `JIRA_OUTPUT=json` for a whole session). The keys are
stable, camelCase and documented here and in the `JSON output:` line of every command in the
[command reference](commands.md). `jira commands --json` carries the same information for programs.

## Conventions

- **One value on standard output.** A single JSON document. When the output is piped it is one compact line;
  on a terminal it is indented. Progress and warnings never go to standard output; they go to standard error.
- **camelCase keys** (`issueType`, `storyPoints`, `fetchedAt`). A key does not disappear because a value is
  missing: missing text is `""`, a missing number, status or person is `null`, an empty list is `[]`. The
  exception is `issues --json`, which lists only the columns it was asked for.
- **People are `null` unless asked for.** `assignee` and `reporter` are `null`, comment authors are `null`, until
  `--with-users` (and never with `JIRA_HIDE_USERS=true`). Text has mentions, e-mail addresses and personal links
  redacted (`[~user]`, `[email]`, `[personal link]`).
- **`--raw`** prints Jira's own JSON instead. `issue --raw` replaces people by `{"hidden": true}` unless `--with-users`;
  `issues --raw` keeps the people Jira returns unless you add `--no-assignee` or `JIRA_HIDE_USERS=true` is set.
- **Stability.** Keys are added, not renamed or removed, within a major version. A rename needs a new major
  version and an entry in the changelog.

## Errors

With `--json`, or `JIRA_OUTPUT=json`, a failing command prints one object on **standard error** and exits with
the code of the error ([exit codes](exit-codes.md)):

```json
{"error": {"code": "NOT_FOUND", "message": "Not found: GET /issue/PROJ-999 - Issue Does Not Exist PROJ-999",
           "hint": "", "exitCode": 4, "details": {"status": 404, "request": "GET /issue/PROJ-999"}}}
```

`code` is one of `USAGE`, `CONFIG`, `INVALID`, `CONFLICT`, `NOT_FOUND`, `AUTH`, `REJECTED`, `UNAVAILABLE`, `ERROR`.
`hint` says what to do and may be empty. `details` holds structured facts (the HTTP status, the candidates of an
ambiguous transition, the `updated` time of a conflict). Without `--json` the same fails as
`ERROR: message` and `Hint: ...` on standard error.

## `jira issue KEY --json`

```json
{
  "key": "PROJ-123", "summary": "Schedule the monthly report", "issueType": "Story",
  "status": {"name": "In Progress", "category": "indeterminate"}, "priority": "High",
  "labels": ["export"], "components": ["Backend"], "fixVersions": ["Release 1.0"],
  "epicKey": "PROJ-5", "parentKey": "", "storyPoints": 5.0,
  "sprints": [{"name": "Sprint 3", "state": "ACTIVE", "id": 7}],
  "description": "...", "acceptance": "1. GIVEN ...", "created": "2026-08-01T02:19:21.723+0200", "updated": "2026-10-15T15:12:49.687+0200",
  "assignee": null, "reporter": null,
  "links": [{"typeName": "Blocks", "phrase": "blocks", "direction": "outward", "other": {"key": "PROJ-124", "summary": "...", "status": "To Do"}}],
  "subtasks": [], "comments": [], "commentTotal": 4, "extra": {}
}
```

`status.category` is `new`, `indeterminate` or `done`, which is how "done" is decided everywhere (reports, `done`
counts). `comments` is empty unless `--comments`; `commentTotal` is always the number of comments.

## `jira issues --json`

```json
{"total": 120, "count": 2, "jql": "project = \"PROJ\" ORDER BY created DESC",
 "issues": [{"key": "PROJ-120", "summary": "...", "issueType": "Story", "status": {"name": "To Do", "category": "new"},
             "priority": "High", "assignee": {"name": "...", "displayName": "...", "email": "..."}, "created": "...", "updated": "..."}]}
```

`total` is what Jira found, `count` what this answer holds (a page, or everything with `--all`). Each issue holds
`key`, `summary`, `issueType`, `status`, `priority`, `assignee`, `created`, `updated` (no `assignee` with
`--no-assignee` or `JIRA_HIDE_USERS=true`) plus the fields named in `--fields`: standard ones under their key
(`labels`, `fixVersions`, `components`, `description`, `reporter`; `parent` as `parentKey`), the custom fields by
their logical names (`story_points` as `storyPoints`, `epic_link` as `epicLink`, `sprint` as `sprints`,
`acceptance`), and any other field, such as a custom field id, as Jira returns it.

## `jira search --json`

```json
{"total": 12, "count": 10, "startAt": 0, "jql": "project = \"PROJ\" AND text ~ \"export monthly report\"", "scope": "project PROJ",
 "match": "all", "relaxed": false, "terms": ["export", "monthly", "report"], "ignored": ["how", "do", "I", "the"],
 "notes": ["Left out as filler: how, do, I, an. Put a word in quotes to keep it."], "truncated": false,
 "hits": [{"key": "PROJ-45", "issueType": "Story", "status": {"name": "To Do", "category": "new"}, "priority": "High",
           "summary": "Export the monthly report", "epicKey": "PROJ-9", "epicSummary": "Reporting", "parentKey": null,
           "labels": ["backend"], "sprints": [{"name": "Sprint 3", "state": "ACTIVE"}], "storyPoints": 5.0,
           "updated": "2026-10-10T09:00:00.000+0200", "url": "https://jira.example.com/browse/PROJ-45",
           "allWords": true, "snippet": "Open the portal and export the report. Ask [~user] or [email] ...", "matched": ["export", "monthly", "report"]}]}
```

- `terms` and `ignored` say how the text was searched: the words and phrases that went into the query, and the filler words and words beyond the twelfth that were left out. `jql` is the query that ran,
  so that `jira issues --jql` can repeat it. `scope` is `project KEY` or `every project`.
- `match` is how the hits were found: `all` (every word) or `any` (one is enough, best match first). `relaxed` is `true` when fewer than five issues held every word and the issues that hold some of them
  are shown too; `allWords` then marks the hits that hold every word (`null` after `--match any`, where nobody checked). `notes` tells the person what happened and, when nothing was found, what to try.
- `total` is what Jira found for the query that ran, `count` what this answer holds, `truncated` is `true` when `--all` stopped at 2,000 issues.
- `snippet` and `matched` are filled for the first `--snippets N` hits (`snippet` is `null` for the others, `""` is never used); the passage is cut from the description and the acceptance criteria, redacted,
  with the wiki markup shown as plain text. `matched` lists the words found there or in the summary.
- Nobody is named: there is no assignee, reporter or comment author in a hit, whatever the options.

## `jira diff`, `jira pull` and `jira push`

```json
{"scope": "project PROJ", "fetchedAt": "2026-10-16T21:48:20+00:00", "files": 118, "issues": 120,
 "counts": {"same": 117, "differs": 0, "jira-changed": 0, "local-changed": 0, "mixed": 1, "conflict": 0, "only-jira": 2, "only-local": 0, "duplicate": 0, "unreadable": 0},
 "entries": [{"key": "PROJ-2", "state": "mixed", "fields": ["summary", "labels"], "editedHere": ["summary"], "changedInJira": ["labels"],
              "conflicting": [], "unknown": [], "file": "PROJ-2-fix-the-thing.md", "message": null, "skipped": {}}],
 "leftOut": [{"file": "notes.md", "why": "has no jira_key or issue_key line"}]}
```

`jira diff --json`: `entries` lists the issues that need a decision (all of them with `--show-same`). `state` is one of `same`, `jira-changed`, `local-changed`, `mixed`, `conflict`, `differs`, `only-jira`,
`only-local`, `duplicate` and `unreadable`; `fields` are the fields that differ, split into `editedHere`, `changedInJira`, `conflicting` (both) and `unknown` (no agreement yet); `skipped` names a field left out of the
comparison and why (`description` of a sanitized file or one with a relative link, a list with a name that holds a comma); `leftOut` lists what was not looked at. The text view names the skipped fields on standard error.

```json
{"scope": "PROJ-1, PROJ-2", "dryRun": false,
 "counts": {"created": 2, "updated": 0, "adopted": 0, "unchanged": 0, "skipped": 0, "conflict": 0},
 "outcomes": [{"key": "PROJ-1", "action": "created", "file": "PROJ-1-export-the-monthly-report.md", "fields": [], "message": null, "lossy": []}],
 "onlyLocal": [], "skipped": {}, "leftOut": []}
```

`jira pull --json`: `action` is `created`, `updated`, `adopted` (a file already in step that got its sync record), `skipped` (also a file that cannot be read, a key that two files hold, and a file whose text was
edited in its spacing only, with the reason in `message`) or `conflict` (exit code 3); `fields` are the fields written or in conflict (and, for `adopted` and `skipped`, the fields whose spacing-only edit stays an edit);
`lossy` names what Markdown could not carry unchanged of a description; `unchanged` files are counted but not listed.
`skipped` (the key, not the action) maps each key to the fields that were not looked at and why, as in `jira diff`.

```json
{"dryRun": true, "pushed": 1, "unchanged": 0, "refused": 0,
 "outcomes": [{"key": "PROJ-2", "action": "pushed", "file": "PROJ-2-fix-the-thing.md", "fields": ["summary"],
               "payload": {"fields": {"summary": "Fixed here"}}, "message": null}]}
```

`jira push --json`: `action` is `pushed`, `unchanged` or `refused`; `payload` is what was (or with `dryRun` would be) sent; `message` of a `pushed` names the fields that were not compared (`not compared: description`, for a description with a relative link
or a list with a name that holds a comma; not a field that this Jira does not have), says when Jira changed another field meanwhile, and says when Jira holds the change but the file could not be brought up to
date. A refusal ends the run with the error on stderr (exit code 3 for a conflict, 1 for a placeholder or a description that does not come back from Markdown unchanged).

## `jira commands --format mcp`

```json
{"tools": [{"name": "jira_search", "title": "Find issues by words, also a whole question",
            "description": "Find issues by words, also a whole question.\n\nWhen to use: Use first when you do not know which issue holds the information: ...",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "..."}, "snippets": {"type": "integer", "description": "..."}}, "required": [], "additionalProperties": false},
            "annotations": {"title": "...", "readOnlyHint": true, "openWorldHint": true},
            "_meta": {"jira": {"command": ["jira", "search"],
                               "arguments": [{"property": "text", "positional": true, "flag": null, "type": "string"},
                                             {"property": "snippets", "positional": false, "flag": "--snippets", "type": "integer"}],
                               "alwaysAppend": ["--json"], "examples": ["jira search 'audit trail' --status 'To Do' --type Story"]}}}],
 "_meta": {"jira": {"version": "1.0.0", "only": "read", "invoke": "Run the command: `jira NAME`, then the positional arguments ...",
                    "exitCodes": [{"code": 0, "names": "OK", "meaning": "..."}]}}}
```

The same shape as the `tools` of an MCP `tools/list` answer, plus `_meta.jira`. In a tool it says how a call becomes a command line: `jira`, the command, the positional arguments in order, then each flag (a
boolean only when true), then `alwaysAppend` (`--json`; `issues` also `--no-assignee` and `ping` also `--quiet`, which keep people out of the answer); at the top it holds the instruction for the caller (people stay out of every
answer when the process that runs the commands has `JIRA_HIDE_USERS=true`) and the exit codes. A failure is a non-zero exit code and the error as JSON on standard error.
`--only read` (the default) lists the commands that do not change Jira, `--only write` the ones that do (`readOnlyHint: false`, `destructiveHint`, `idempotentHint`), `--only all` both. `pull` and `export` change no
issue but write files and can overwrite them: they are in the read set with `readOnlyHint: false`, `destructiveHint: true` and `idempotentHint: true`. The commands for the person
(the token, the settings, the current project), `api` and `wiki` (which prints any file it is given) are not listed. `--format table` is the short list for people, `--format json` the full catalog (the same as `--json`).

## `jira report --json`

```json
{"scope": "project PROJ, open sprint(s)", "issues": 11, "done": 4, "points": 58.0, "donePoints": 50.0, "withoutPoints": 1,
 "byStatus": {"To Do": {"category": "new", "issues": 2, "points": 5.0}, "Done": {"category": "done", "issues": 4, "points": 50.0}},
 "byEpic": {"PROJ-5": {"summary": "Reporting", "issues": 6, "done": 2, "points": 30.0, "donePoints": 20.0}}}
```

`byStatus` is ordered new, in progress, done. `byEpic` is empty unless `--epics`, holds `(no epic)` for issues
without one, and is the same shape as in version 1.

## Write commands

`transition`, `comment`, `link`, `update`, `update-json`, `create` and `create-json` print what they did. With
`--dry-run` the same object has `"dryRun": true` and the `payload` that would have been sent.

| Command | Keys |
| --- | --- |
| `transition` | `key`, `currentStatus`, `options[{id,name,toStatus{name,category}}]`, `chosen`, `dryRun`, `executed`, `payload` |
| `comment` | `key`, `payload`, `dryRun`, `commentId` (and with `--list`: `key`, `total`, `comments[{id,created,body,author}]`) |
| `link` | `key`, `other`, `typeName`, `phrase`, `relation`, `payload`, `dryRun`, `confirmed` (the relation read back from Jira) |
| `update`, `update-json` | `action` (`updated`), `key`, `payload`, `dryRun`, `created` (`null`) |
| `create`, `create-json` | `action` (`created`), `key`, `payload`, `dryRun`, `created{id,key,url}` |
| `import-md` | `file`, `action`, `key`, `dryRun`, `message`, `payload` |
| `import-md-dir`, `import-csv` | `dryRun`, `mapFile`, `created`, `updated`, `skipped`, `failed`, `outcomes[{file,action,key,dryRun,message}]` |

## Smaller commands

| Command | Shape |
| --- | --- |
| `branch` | `{"key": "PROJ-1", "branch": "feature/PROJ-1-title"}` |
| `fields` | `[{"id": "customfield_10001", "name": "Story Points", "custom": true}]` |
| `link-types` | `[{"id": "1", "name": "Blocks", "outward": "blocks", "inward": "is blocked by"}]` |
| `projects` | `[{"key", "name", "projectType", "lead", "description", "issueTypes"}]`; `lead` is `null` with `JIRA_HIDE_USERS=true` |
| `project` | one such object |
| `projects-admin` | `{"checked", "admin", "failed": [{"key","message"}], "projects": [...]}` |
| `metadata` | Jira's own create-metadata document |
| `ping` | `{"ok": true, "baseUrl": "...", "user": {"name","displayName","email"}}`; `user` is `null` with `--quiet` or the privacy policy |
| `doctor` | `{"ok": true, "checks": [{"name","status","detail","hint"}]}`, `status` is `ok`, `warn` or `fail` |
| `config show` | `{"file": "...", "settings": [{"name","value","source","description","secret"}]}`; secrets are masked |
| `use`, `current`, `clear-project` | `{"project", "name"}`, `{"project": null}`, `{"cleared": true}` |
| `version` | `{"name": "jira-dc-cli", "version": "1.0.0", "python": "3.13.3", "platform": "win32"}` |
| `commands` | `{"version", "commands": [...], "exitCodes": [...], "settings": [...]}`; every command has `whenToUse`. `--format mcp`: see above |
| `how-to` | without a topic: `[{"topic", "title"}]`; with a topic: `{"topic", "text"}` (the Markdown of the guide) |
| `skill` | `show`: `{"name", "text"}`; `install`: `{"path", "action", "dryRun"}` with `action` `installed`, `replaced` or `unchanged` |

## The backlog dump v1

`jira export` writes this. Other tools depend on it, so the key names are a contract.

```json
{
  "fetchedAt": "2026-10-15T08:07:04+00:00",
  "fieldIds": {"epicLink": "customfield_10002", "storyPoints": "customfield_10001", "acceptance": "customfield_10004", "sprint": "customfield_10003"},
  "issues": {
    "PROJ-1": {
      "summary": "...", "type": "Story", "status": "In Progress", "priority": "High",
      "labels": ["a"], "components": ["Backend"], "fixVersions": ["Release 1.0"],
      "description": "...", "epicLink": "PROJ-5", "storyPoints": 5.0, "acceptance": "1. GIVEN ...",
      "sprint": [{"name": "Sprint 3", "state": "ACTIVE"}]
    }
  }
}
```

- `issues` is keyed by issue key, in numeric order (`PROJ-2` before `PROJ-10`).
- `fieldIds` holds the custom field ids used in this Jira, or `null` for a field that does not exist.
- Missing values are `null` (`epicLink`, `storyPoints`, `acceptance`, `priority`, `status`), lists are `[]`.
- There is no assignee, reporter, comment or link: the dump holds no personal data.
- `fetchedAt` is when the export ran, in ISO 8601 with offset; use it with `--if-unchanged-since`.
