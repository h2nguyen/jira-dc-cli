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
  `--with-users` (and never with `JIRA_HIDE_USERS=true`; the one exception is your own login in the `payload` of a write that sets you as the reporter or
  the assignee: `create`, `assign ... me`, `update --assignee me`). Text has mentions, e-mail addresses and personal links
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
`hint` says what to do and may be empty (a refused write, `REJECTED`, always has one). `details` holds structured facts (the HTTP
status, the candidates of an ambiguous transition, the `updated` time of a conflict). Without `--json` the same fails as
`ERROR: message` and `Hint: ...` on standard error. The settings can ask for JSON too: with `JIRA_OUTPUT=json` in the configuration
file, or the agent profile (`JIRA_PROFILE=agent`), an error is JSON without `--json`.

## The envelope: `--json-envelope`

Some agent tools report the exit code as 1 for every command and drop standard error, so that a failure cannot be told from a result.
`--json-envelope` (or `JIRA_JSON_ENVELOPE=1` in the environment, for the whole session) makes **every** command print exactly one JSON
object on standard output, whatever happens:

```json
{"ok": true,  "data": {"key": "PROJ-1", "summary": "..."}}
{"ok": false, "error": {"code": "NOT_FOUND", "message": "...", "hint": "", "exitCode": 4, "details": {}}}
```

- A command that supports `--json` prints its JSON as `data` (the envelope implies `--json`); a command without JSON (`wiki`,
  `changelog`) prints `{"text": "..."}` as `data`.
- The exit code of the process and standard error stay as they are (an error is still printed there too, as text or with `--json` as
  JSON): the envelope is added, nothing is taken away. A command that answers and still asks for a non-zero exit code (`current` without a
  project, `doctor` with a failed check) has `"ok": false`, its `data` and an `error` with `"code": "EXIT"`.
- Help (`--help`) and the commands that talk on standard input and output themselves (`jira mcp`) are not wrapped.
- Without the switch nothing changes.

## `jira api`

`jira api METHOD PATH ... --write --dry-run --json` prints the request as one JSON object, and nothing else:

```json
{"dryRun": true, "method": "PUT", "path": "/issue/PROJ-1", "query": {}, "body": {"fields": {"summary": "New"}}}
```

`query` holds the query string as an object (a name that occurs twice is a list), whether it was written into the path or given as
`--param KEY=VALUE`. A real call prints Jira's answer unchanged (compact when piped with `--json`); under `JIRA_HIDE_USERS=true` (and the agent profile) user objects, mentions, e-mail addresses and personal links are removed from it, the user keys and names in the changelog items of the user fields (`assignee`, `reporter`, `creator`, `watcher`, `voter`: `from`, `to`, `fromString`, `toString` are `null`) and the user names in the `jql` of a saved filter are cleaned (its `searchUrl` is emptied whenever the `jql` needed any redaction: a user name, an e-mail address, a mention or a personal link, because the address holds the same text percent-encoded), and a structure that holds a name elsewhere (a custom user-picker field) is not recognised. Without `--json` the dry run is the
text `Dry run: PUT /issue/PROJ-1; nothing was sent` and the body.

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
  "subtasks": [], "comments": [], "commentTotal": 4, "attachments": [], "attachmentTotal": 0, "extra": {}
}
```

`status.category` is `new`, `indeterminate` or `done`, which is how "done" is decided everywhere (reports, `done`
counts). `comments` is empty unless `--comments`; `commentTotal` is always the number of comments. `attachments` has
`{id, filename, mimeType, size, created, author}` for each file (`author` is `null` unless `--with-users`) and
`attachmentTotal` the number of them.

## `jira issue KEY1 KEY2 ... --json`

```json
{"count": 2,
 "issues": [{"key": "PROJ-3", "summary": "...", "status": {"name": "Done", "category": "done"}}, {"key": "PROJ-1", "summary": "...", "status": {"name": "To Do", "category": "new"}}],
 "errors": [{"key": "PROJ-999", "code": "NOT_FOUND", "message": "Not found: GET /issue/PROJ-999 - Issue Does Not Exist"}]}
```

Several keys print this object, one key the issue itself. Several is the number of keys given, not of different ones: `jira issue PROJ-1 PROJ-1 --json` is the object with `count: 1` as well. `issues` is in the order of the keys (not in Jira's order), `count` is the number of issues that were
found, and `errors` has one `{key, code, message}` for each key that was not (`code` is `NOT_FOUND`; a missing key given twice is an entry of `errors`, not an error message). The exit code is 4 when `errors` is not empty; the
issues that were found are printed all the same. Every option applies to every issue, except that `--raw`, `--outline` and `--section` are refused for several keys, the same key given twice included.

## The options of `jira issue --json`

Without the options the object is the one above, whole. With `--only a,b` it holds `key` and the keys of those parts: `summary`; `status`; `priority`; `labels`;
`components`; `fixVersions`; `epic` (`epicKey`, `parentKey`); `points` (`storyPoints`); `sprints`; `dates` (`created`, `updated`); `description`; `acceptance`;
`comments` (`comments`, `commentTotal`); `links`; `subtasks`; `attachments` (`attachments`, `attachmentTotal`); with `--with-users` also `assignee` and
`reporter`. The other options add keys, and only when they are given:

| Option | Keys |
| --- | --- |
| `--max-chars N` | `truncated{FIELD: bool}` and `totalChars{FIELD: number}`; the fields are `description`, `acceptance`, `renderedDescription`, `renderedAcceptance` and `comments.ID` for each comment that is shown (only the texts that are shown); `totalChars` is the size of the whole text, a line end counted as one; a text that opens with a code block that does not fit is cut at a line of the block and ends with the closing line of the block (`{code}`, `{noformat}`, a fence) |
| `--outline` | `outline[{index, level, title, chars}]` and `description: null`; `level` is 1 to 6, `chars` the size of the part with its sub-parts |
| `--section T` | `section{index, level, title}`; `description` holds the text of that part, from its heading |
| `--rendered` | `renderedDescription` and `renderedAcceptance`: the text as Jira renders it, as plain text (`""` when Jira renders nothing) |
| `--history` | `history[{created, from, to}]`, the status changes oldest first, and `historyComplete` (`false` when Jira sent only part of the changelog); only Jira's own status field is read (not a custom field that is called status) |

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

## `jira filters --json` and `jira filter ID --json`

```json
[{"id": "10000", "name": "Open bugs", "description": "Bugs that are open", "jql": "project = \"PROJ\" AND issuetype = Bug AND resolution = Unresolved", "favourite": true, "owner": null}]
```

```json
{"id": "10000", "name": "Open bugs", "description": "Bugs that are open", "jql": "project = \"PROJ\" AND issuetype = Bug AND resolution = Unresolved", "favourite": true, "owner": null,
 "searchUrl": "https://jira.example.com/rest/api/2/search?jql=...", "viewUrl": "https://jira.example.com/secure/IssueNavigator.jspa?mode=hide&requestId=10000"}
```

`filters` lists the favourite filters of the user of the token (Data Center has no call that lists every filter). `owner` is `null` unless `--with-users`
(`{name, displayName, email}`). Without `--with-users` the name and the description have mentions and e-mail addresses redacted, and the `jql` has `[user]` in place of the people that it names (the values of `assignee`, `reporter`,
`creator`, `watcher`, `voter`, `commentedBy`, `approver`, the user after `by`, and the like; a custom user field and a name written in words are not recognised, see the [guide](../how-to/read-issues.md)). `searchUrl` holds the JQL once more and is
`""` when the JQL had to be redacted; a note on standard error says so. With `--with-users` all of it is as Jira sent it. `jira issues --filter ID` and `jira search --filter ID` print their usual document, with `jql`
holding `filter = ID`.

## `--select` and `--format`

For `issues`, `search`, `fields`, `filters` and the other lists (`boards`, `sprints`, `velocity`, `backlog`, `epics`, `worklogs`, `remote-links` and the reference
lists). With `--json`, `--select a,b.c` keeps only those keys of every item (a dot reaches into an object: `status.name` gives `{"status": {"name": ...}}`); the
keys around the list (`total`, `count`, `jql`, `scope`, ...) stay. The items are the ones that `--json` prints: the issues of `issues`, the hits of `search`, the
list itself for `fields`, `filters` and the others. An unknown name is a usage error (exit code 2) that lists the valid keys. Without `--json`, `--format` prints
`table` (the default), `tsv` (with a header line) or `md` (a Markdown table), in the columns of the table or of `--select`; `--json` together with `--format tsv` or
`--format md` is a usage error.

## `jira diff`, `jira pull` and `jira push`

```json
{"scope": "project PROJ", "fetchedAt": "2026-10-16T21:48:20+00:00", "files": 118, "issues": 120,
 "counts": {"same": 117, "differs": 0, "jira-changed": 0, "local-changed": 0, "mixed": 1, "conflict": 0, "only-jira": 2, "only-local": 0, "duplicate": 0, "unreadable": 0},
 "entries": [{"key": "PROJ-2", "state": "mixed", "fields": ["summary", "labels"], "editedHere": ["summary"], "changedInJira": ["labels"],
              "conflicting": [], "unknown": [], "file": "PROJ-2-fix-the-thing.md", "message": null, "skipped": {}}],
 "leftOut": [{"file": "notes.md", "why": "has no jira_key or issue_key line"}]}
```

`jira diff --json`: `entries` lists the issues that need a decision (all of them with `--show-same`). `state` is one of `same`, `jira-changed`, `local-changed`, `mixed`, `conflict`, `differs`, `only-jira`,
`only-local`, `duplicate` and `unreadable`; `fields` are the fields that differ, split into `editedHere`, `changedInJira`, `conflicting` (both) and `unknown` (no agreement yet); `links` is one of
the fields: a link that only the file lists is in `editedHere`, one that only Jira holds in `changedInJira`; `skipped` names a field left out of the
comparison and why (`description` of a sanitized file or one with a relative link, a list with a name that holds a comma, the links of an issue that has a link type whose name holds a comma, `<` or `>`); `leftOut` lists what was not looked at. The text view names the skipped fields on standard error.

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
 "outcomes": [{"key": "PROJ-2", "action": "pushed", "file": "PROJ-2-fix-the-thing.md", "fields": ["summary", "links"],
               "payload": {"fields": {"summary": "Fixed here"}}, "message": null,
               "links": [{"entry": "Has to be done after -> PROJ-4", "relation": "PROJ-2 is required by PROJ-4",
                          "payload": {"type": {"name": "Has to be done after"}, "outwardIssue": {"key": "PROJ-2"}, "inwardIssue": {"key": "PROJ-4"}}}],
               "jiraOnlyLinks": []}]}
```

`jira push --json`: `action` is `pushed`, `unchanged` or `refused`; `payload` is what was (or with `dryRun` would be) sent to change the issue, `null` when only links are sent; `message` of a `pushed` names the fields that were not compared
(`not compared: description`, for a description with a relative link or a list with a name that holds a comma, `links` for an issue with a link type that the `links:` line cannot hold; not a field that this Jira does not have), says when Jira
changed another field meanwhile, and says when Jira holds the change but the file could not be brought up to date.

`links[]` lists the links that were created (with `dryRun`: that would be created), in the order sent: `entry` as the file writes it (`Type -> KEY`: this issue is the outward end, `Type <- KEY`: the inward end), `relation` as Jira shows it,
`payload` the body of `POST /issueLink`; `[]` when none. `jiraOnlyLinks[]` lists the entries that Jira holds and the file does not list (the CLI never removes a link: `jira has it; remove it in Jira`); `[]` when none. `fields` holds `links` when
a link was created; a push whose only difference is a link that only Jira holds is `unchanged` with `fields: ["links"]`. When a link fails after another write went through, the outcome is `pushed` with what was sent, `message` says
`links not created (...)` and the error ends the run (the exit code of the error); a refusal before anything was sent is `refused`. A refusal ends the run with the error on stderr (exit code 3 for a conflict, 1 for a placeholder, an
unknown link type or a description that does not come back from Markdown unchanged, 4 for an unknown issue in a link, 5 for a missing *Link Issues* permission).

## `jira attachments`, `jira download` and `jira attach`

```json
{"key": "PROJ-123", "total": 2, "attachments": [{"id": "10001", "filename": "shot.png", "mimeType": "image/png", "size": 12345, "created": "2026-10-05T09:30:00.000+0200", "author": null}]}
```

`jira attachments KEY --json`: `total` is what the issue has, `attachments` the files that were asked for (`--name`). `author` is `null` unless `--with-users`, and never present as a
person with `JIRA_HIDE_USERS=true`. The address of the content is never printed. `--select` and `--format` work as for every list.

```json
{"key": "PROJ-123", "dryRun": false, "outDir": "tmp/jira-attachments/PROJ-123",
 "attachments": [{"id": "10001", "filename": "shot.png", "mimeType": "image/png", "size": 12345, "path": "tmp/jira-attachments/PROJ-123/shot.png", "action": "written"}]}
```

`jira download KEY ... --json`: `action` is `written` or `overwritten`; in a dry run `would-write`, `would-overwrite`, `exists` or `too-large` (the last two would stop the real run, which
fails before it fetches anything). `size` is what was written (what Jira lists, in a dry run). No bytes, ever. When a file of an `--all` run fails after others were written, the error (as the failure's own: `UNAVAILABLE`, `AUTH` ...) has
`details.written[{id, filename, path, size}]` and `details.failed{id, filename}`. A folder where a file would go is an error (`INVALID`, exit code 1) and a folder that holds the configuration of the CLI is a usage error (`USAGE`, exit code 2), both also in a dry run.

```json
{"key": "PROJ-123", "dryRun": false,
 "attachments": [{"id": "10002", "filename": "error log.txt", "mimeType": "text/plain", "size": 1234, "kind": "text", "action": "read",
                  "text": "...", "truncated": false, "totalChars": 1198, "hint": ""}]}
```

`jira download KEY ... --text --json`: `kind` is `text`, `docx`, `xlsx`, `pptx` or `other`; `action` is `read`, or `not-text` when there is no text to show (`text` is `""`, `totalChars` is `null`,
`hint` says why and what to do: an image, a binary file, a damaged or refused document), or in a dry run `would-read` or `too-large`. `truncated` and `totalChars` (the length of the whole
text, after redaction) tell whether the text was cut at `--max-chars`. A document that expands to more than about 6.3 million characters of text is `not-text` too, whatever `--max-chars` asks for (the hint says
`expands to more text than can be read`), and a cell or a shared string of a spreadsheet is cut at the 32,767 characters that Excel allows and ends with `…`. A ZIP64 archive, an encrypted part, a part packed with bzip2 or LZMA, a document with more than a million tags in all its parts, and names that no real document has are `not-text` as well, each with a hint that says why.

```json
{"key": "PROJ-123", "dryRun": true,
 "payload": {"request": "POST /issue/PROJ-123/attachments", "headers": {"X-Atlassian-Token": "no-check"},
             "parts": [{"field": "file", "filename": "shot.png", "contentType": "image/png", "size": 12345}], "comment": {"body": "..."}},
 "files": [{"name": "shot.png", "path": "shot.png", "size": 12345, "mimeType": "image/png", "existing": []}],
 "limits": {"enabled": true, "uploadLimit": 10485760}, "warnings": [], "attached": [], "commentId": ""}
```

`jira attach KEY FILE... --json`: `payload` describes the request that a real run sends (the same function builds it); `comment` is there only with `--comment`. `payload.parts[].filename` is the name as it goes on the wire (`"` as `%22`, line breaks as `%0D` and `%0A`); `files[].name` is the name on the disk. `existing` holds the ids of the
attachments of the issue that have this file name already, and `warnings` says so. `attached[{id, filename, mimeType, size, created}]` and `commentId` are filled by a real run (never a person). When
the upload worked and the comment failed, the error (`AUTH`, `REJECTED` ... as the comment's) has `details.attached[{id, filename, size}]`. A file that is the configuration of the CLI or holds the token is a usage error (`code: "USAGE"`, exit code 2), in a dry run too.

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
 "_meta": {"jira": {"version": "1.1.0", "only": "read", "invoke": "Run the command: `jira NAME`, then the positional arguments ...",
                    "exitCodes": [{"code": 0, "names": "OK", "meaning": "..."}]}}}
```

The same shape as the `tools` of an MCP `tools/list` answer, plus `_meta.jira`. In a tool it says how a call becomes a command line: `jira`, the command, the positional arguments in order, then each flag (a
boolean only when true), then `alwaysAppend` (`--json`; `issues` also `--no-assignee` and `ping` also `--quiet`, which keep people out of the answer); at the top it holds the instruction for the caller (people stay out of the
answers when the process that runs the commands has `JIRA_HIDE_USERS=true`, apart from your own login where a command sets you as reporter or assignee) and the exit codes. A failure is a non-zero exit code and the error as JSON on standard error.
`--only read` (the default) lists the commands that neither change Jira nor write files, `--only write` the ones that do either (`readOnlyHint: false`, `destructiveHint`, `idempotentHint`), `--only all` both. `pull`, `export` and `download` change no
issue but write files and can overwrite them: they are in the write set with `readOnlyHint: false`, `destructiveHint: true` and `idempotentHint: true`, and their description says "Does not change Jira. Writes files". The commands for the person
(the token, the settings, the current project), `api`, `mcp` (the server that serves these tools) and `wiki` (which prints any file it is given) are not listed. `--format table` is the short list for people, `--format json` the full catalog (the same as `--json`).

## `jira mcp`

`jira mcp` has no `--json`: it speaks JSON-RPC 2.0 on standard input and output, one message per line (pure ASCII, no embedded line break), and writes nothing else to standard output. The tools are those of
`jira commands --format mcp`: `--allow-write` adds the ones that change Jira, `--allow-files` the three that write files (`download`, `pull`, `export`), the two independent of each other, and `--group` keeps some groups; the guide is [use the CLI as an MCP server](../how-to/mcp-server.md).

```json
{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":false}},
 "serverInfo":{"name":"jira-dc-cli","title":"Jira Data Center (jira-dc-cli)","version":"1.1.0"},"instructions":"These tools run the jira command line ..."}}
{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"jira_issue","title":"...","description":"...","inputSchema":{...},"annotations":{...},"_meta":{"jira":{...}}}]}}
{"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"{\"key\":\"PROJ-1\",...}\n"}],"isError":false}}
{"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text","text":"{\"error\":{\"code\":\"NOT_FOUND\",\"message\":\"...\",\"hint\":\"...\",\"exitCode\":4,\"details\":{}}}"}],"isError":true}}
{"jsonrpc":"2.0","id":5,"error":{"code":-32602,"message":"Invalid arguments for jira_issue: 'issueKey' is required."}}
```

`initialize` answers with the protocol version of the client when it is `2025-06-18`, `2025-03-26` or `2024-11-05` and with `2025-06-18` otherwise; `instructions` holds the rules for an agent. `tools/list` is the `tools` of
`jira commands --format mcp` (not paged). The `text` of a result is what the command prints with `--json`, so its shape is the one documented on this page; a result over 100,000 characters is cut and ends with a line
that says so. A command that fails is `isError: true` and its text is the error object that the command prints on standard error (the keys of "Errors" above); a command that answers and still fails (`doctor` with a
failed check) adds what it printed as a second block of text. A call that is refused before it runs is an error response:

| Code | When |
| --- | --- |
| `-32700` | the line is not JSON (`id` is `null`) |
| `-32600` | it is JSON but not a request object (`id` is `null` unless the request had a valid one) |
| `-32601` | the method is unknown (`initialize`, `ping`, `tools/list` and `tools/call` are answered) |
| `-32602` | an unknown tool (also a tool that changes Jira without `--allow-write` and one that writes files without `--allow-files`; the message names the switch) or arguments that do not fit: an unknown property, a missing one, a wrong type, a value outside the choices, a positional argument that starts with `-`, a value that starts with `@` for a property that offers `@FILE`, `withUsers` without `--show-users`; the message names the property |

A call that fits its schema but names a path that the server does not allow (outside the working folder, a blank or an invisible character at an end of a path, a network or device path, the settings file or its folder, the cache folder, a file that holds the token, a hidden folder or an existing file where a call writes, a map file of `jira_import_md_dir` that is no `.csv` or no map, or, for a property that the call left out, the place that the command would use instead: `out` of `jira_download` is `tmp/jira-attachments/KEY`, `mapFile` of `jira_import_md_dir` is `DIRECTORY/jira-import-map.csv`, with `was left out` in the message), or sets `force`, is not an error response: it is a result with `isError: true` whose text is `{"error": {"code": "INVALID", "message": ..., "hint": ..., "exitCode": 1, "details": {"tool": "jira_comment"}}}`. The `message` names the property and the reason, the `hint` says what is allowed, and the command did not run. The examples of `download` (`"outDir": "tmp/jira-attachments/PROJ-123"`) and of the imports show what the CLI prints; over `jira mcp` those paths are absolute, because the command is given the place that the policy approved. The descriptions that `jira mcp` serves for `download`, `export`, `pull` and `push` differ from those of `jira commands --format mcp` where they speak of overwriting: over MCP nothing is replaced, and the `force` property of `download`, `pull` and `push` says "Refused over MCP".

A batch (a JSON array) is answered as a batch, a notification gets no answer, the end of the input ends the server with exit code 0, and a closed standard output ends it quietly with exit code 141.

## `jira report --json`

```json
{"scope": "project PROJ, open sprint(s)", "issues": 11, "done": 4, "points": 58.0, "donePoints": 50.0, "withoutPoints": 1,
 "byStatus": {"To Do": {"category": "new", "issues": 2, "points": 5.0}, "Done": {"category": "done", "issues": 4, "points": 50.0}},
 "byEpic": {"PROJ-5": {"summary": "Reporting", "issues": 6, "done": 2, "points": 30.0, "donePoints": 20.0}}}
```

`byStatus` is ordered new, in progress, done. `byEpic` is empty unless `--epics`, holds `(no epic)` for issues
without one, and is the same shape as in version 1.

## `jira boards --json`

```json
{"total": 2, "count": 2,
 "boards": [{"id": 1200, "name": "Demo board", "type": "scrum", "projects": ["PROJ"]},
            {"id": 1300, "name": "Flow board", "type": "kanban", "projects": ["DEMO"]}]}
```

`total` is what Jira found, `count` what this answer holds (`--limit`). `projects` are the keys of the projects that a board is built on (`[]` when you may not see them).

## `jira sprints --json`

```json
{"boardId": 1200, "boardName": "Demo board", "total": 4, "count": 4,
 "sprints": [{"id": 7, "name": "Sprint 7", "state": "active", "startDate": "2025-09-15T16:30:00.000+02:00", "endDate": "2025-09-29T08:00:00.000+02:00",
              "completeDate": "", "goal": "Stabilise", "boardId": 1200}]}
```

`state` is `future`, `active` or `closed`. The order is the active sprints, the future ones, then the closed ones, the newest first. A date that Jira does not hold is `""`.
`total` is the number of sprints of the board in these states, `count` what this answer holds (`--limit`, default 50). The sprint name and the goal are redacted (mentions, e-mail addresses).

## `jira sprint --json`

```json
{"sprint": {"id": 6, "name": "Sprint 6", "state": "closed", "startDate": "...", "endDate": "...", "completeDate": "...", "goal": "Ship the export", "boardId": 1200},
 "issues": [{"key": "PROJ-21", "issueType": "Story", "status": {"name": "Done", "category": "done"}, "storyPoints": 5.0, "epicKey": "", "summary": "..."}],
 "review": {"start": "2025-09-01T08:00:00+02:00", "end": "2025-09-15T16:00:00+02:00", "ended": true,
            "committed": {"issues": 4, "points": 23.0, "withoutPoints": 0, "keys": ["PROJ-21", "PROJ-22", "PROJ-24", "PROJ-25"]},
            "completed": {"issues": 2, "points": 13.0, "withoutPoints": 0, "keys": ["PROJ-21", "PROJ-23"]},
            "added": {"issues": 2, "points": 8.0, "withoutPoints": 1, "keys": ["PROJ-23", "PROJ-27"]},
            "removed": {"issues": 1, "points": 2.0, "withoutPoints": 0, "keys": ["PROJ-24"]},
            "carriedOver": {"issues": 3, "points": 16.0, "withoutPoints": 1, "keys": ["PROJ-22", "PROJ-25", "PROJ-27"]},
            "excludedSubtasks": 1,
            "notes": ["Derived from the change history of the field Sprint and of the status, not from Jira's own sprint report: its numbers can differ.", "..."],
            "historyComplete": true}}
```

`issues` is `null` without `--issues` and `review` is `null` without `--review`. The issues are in the order of the rank and have no people. In the review, `start` is the start date of the
sprint and `end` its complete date, or now for a sprint that still runs (`ended` is then `false`). The groups overlap: a committed issue can be completed, an added one removed.
`points` are the current story points (an issue without any counts as 0 and in `withoutPoints`); `keys` are in the order of the numbers.

- `committed`: in the sprint at the start. `added`: entered after the start. `removed`: left before the end.
- `completed`: in the sprint at the end and in a status of the category done then (an issue done after the complete date is not). `carriedOver`: in the sprint at the end and not done.
- Sub-tasks are not counted unless `--with-subtasks` (`excludedSubtasks` says how many were left out).
- `notes` says where the numbers come from and what is incomplete: the history search that Jira refused, a history that Jira cut, a status whose category is unknown.
- `historyComplete` is `false` when the history behind the review is not whole: Jira refused the search for the issues that left the sprint (`sprint was`; then **`removed`, `committed` and `added` are lower bounds**, because an issue that was in the sprint and left it is in none of them), Jira sent only part of the history of an issue, an entry of the history has a time that cannot be read, the creation time of an issue is missing, or the history of an issue does not end where the issue is now. The text table marks `Committed`, `Added` and `Removed` with `*` then. `points` are rounded to six decimals (three issues of 0.1 points are 0.3).

## `jira velocity --json`

```json
{"boardId": 1200, "boardName": "Demo board", "count": 2, "averageCompleted": 6.5,
 "sprints": [{"id": 6, "name": "Sprint 6", "startDate": "...", "completeDate": "...", "committedIssues": 4, "committedPoints": 23.0,
              "completedIssues": 2, "completedPoints": 13.0, "historyComplete": true}],
 "leftOut": [],
 "notes": ["Derived from the change history ...", "Story points are the current values ..."]}
```

The closed sprints, the newest first, at most `--last` of them (default 5, at most 20); the numbers are those of `jira sprint --review`. `averageCompleted` is the mean of `completedPoints`, `null` without a
closed sprint. `historyComplete` is `false` for a sprint whose history is not whole (see `jira sprint --json`): its `committedIssues` and `committedPoints` are then lower bounds, the text table marks them with `*`, and `--format tsv|md` has the column `HISTORY COMPLETE`. A closed sprint that cannot be reviewed (no start date, more than 2,000 issues) is left out: its id is in `leftOut` and the reason is in `notes` (a warning on standard error), and the text says `N closed sprints could not be reviewed (see the warnings).`; `No closed sprints.` means that there are none.

## `jira backlog --json` and `jira epics --json`

```json
{"boardId": 1200, "boardName": "Demo board", "total": 3, "count": 3,
 "issues": [{"key": "PROJ-1", "issueType": "Story", "status": {"name": "To Do", "category": "new"}, "storyPoints": 5.0, "epicKey": "PROJ-9", "summary": "..."}]}
```

```json
{"boardId": 1200, "boardName": "Demo board", "count": 2,
 "epics": [{"key": "PROJ-8", "name": "Epic eight", "summary": "Epic eight", "done": false}]}
```

The backlog is in the order of the rank (`total` is the size of the backlog, `count` what this answer holds); an epic that is done is listed only with `--done`. Neither shows a person.

## `jira sprint-add --json` and `jira epic-add --json`

```json
{"sprint": {"id": 8, "name": "Sprint 8", "state": "future", "startDate": "", "endDate": "", "completeDate": "", "goal": "", "boardId": 1200},
 "keys": ["PROJ-24", "PROJ-22"], "skipped": [], "payload": {"issues": ["PROJ-24", "PROJ-22"]}, "requests": 1, "dryRun": false,
 "confirmed": ["PROJ-22", "PROJ-24"], "missing": [], "notes": ["PROJ-22 leaves Sprint 7 (active)."], "readBack": true}
```

`jira epic-add` has `"epic": "PROJ-8"` (or `"none"`) and `"epicName"` in place of `sprint`. `keys` are the issues that are sent, `skipped` those that were in the sprint (or epic) already; `payload` is the body
of the request, which is sent in `requests` calls of at most 50 issues each. `notes` says what a move takes out (the other open sprint, the other epic). The keys are the ones that Jira holds: the old key of an
issue that moved to another project is read as its new key, sent and checked under it, and `notes` says `OLD-5 is now PROJ-9.`

`readBack` is `true` when the sprint (or epic) was read after the move: `confirmed` and `missing` are then what that read found (lists; `missing: []` means that nothing is missing). Without a read-back `readBack` is
`false` and **`confirmed` and `missing` are `null`**, because nothing was checked: for `--dry-run` (`dryRun: true`), when there was nothing to send (`requests: 0`), and when the move was made but the read-back failed
(a server error, the network, a refusal). The last case is no failure: the exit code is 0 and `notes` says `The issues were moved, but they could not be read back: ...`; look with `jira sprint ID --issues` or
`jira search --under EPIC_KEY` before you say more. Repeating the command is harmless (what is there already is not sent).

## `jira rank --json`

```json
{"keys": ["PROJ-4", "PROJ-3"], "position": "before", "anchor": "PROJ-1", "payload": {"issues": ["PROJ-4", "PROJ-3"], "rankBeforeIssue": "PROJ-1"},
 "dryRun": false, "order": ["PROJ-4", "PROJ-3", "PROJ-1"], "confirmed": true, "notes": [], "readBack": true}
```

`readBack` is `true` when the order was read after the rank: `order` is then the moved issues and the anchor in the order of the rank (`[]` when Jira found none of them), and `confirmed` is `true` when they stand where they
were asked to, `false` when not. Without a read-back `readBack` is `false` and **`order` and `confirmed` are `null`**: for `--dry-run`, and when the rank was made but the read failed (exit code 0, `notes` says `The issues
were ranked, but their order could not be read back: ...`). `keys` and `anchor` are the keys that Jira holds (the old key of a moved issue is replaced, and `notes` says `OLD-5 is now PROJ-9.`).

## `jira sprint-create --json` and `jira sprint-update --json`

```json
{"payload": {"state": "active"}, "dryRun": false,
 "sprint": {"id": 8, "name": "Sprint 8", "state": "active", "startDate": "...", "endDate": "...", "completeDate": "", "goal": "", "boardId": 1200},
 "before": {"id": 8, "name": "Sprint 8", "state": "future", "startDate": "...", "endDate": "...", "completeDate": "", "goal": "", "boardId": 1200},
 "notes": [], "readBack": true}
```

`sprint` is the sprint as Jira holds it after the change, read back (`null` with `--dry-run`); `before` is the sprint as it was (`null` for a new sprint). `payload` is the body that was (or would be) sent:
only the fields that change, dates as `2026-11-02T00:00:00.000+00:00`. `readBack` is `true` when `sprint` was read from Jira after the write, and `false` for a dry run **and** when the write was made but the read-back failed
(a server error, the network): `sprint` is then what Jira answered to the write, the exit code is 0, and a note says `The sprint was created (id 101), but it could not be read back: ...` and not to send the command again. Check `dryRun` to tell the two cases of `false` apart.

## Reference lists

`priorities`, `resolutions`, `issue-types`, `components`, `fix-versions`, `permissions` and `roles` print a list; `statuses`, `dashboards` and `server` an object. Every list takes `--select a,b.c` and
`--format table|tsv|md` like the other lists. People are `null` unless `--with-users`.

| Command | Shape |
| --- | --- |
| `priorities`, `resolutions` | `[{"id": "1", "name": "Highest", "description": "..."}]` |
| `issue-types` | `[{"id": "10200", "name": "Story", "description": "...", "subtask": false}]`; `--project KEY` lists the types of that project |
| `components` | `[{"id": "10100", "name": "Backend", "description": "...", "lead": null}]`; `lead` is `{name, displayName, email}` with `--with-users` |
| `fix-versions` | `[{"id", "name", "description", "released": true, "archived": false, "releaseDate": "2026-06-01", "counts": null}]`; `counts` is `{"fixed": 12, "affected": 3, "unresolved": 0}` with `--counts` and stays `null` for the versions beyond the 25 that are counted |
| `permissions` | `[{"key": "EDIT_ISSUES", "name": "Edit Issues", "type": "PROJECT", "havePermission": true}]`; `type` is `GLOBAL` or `PROJECT`; `havePermission` is the token's own |
| `roles` | `[{"id": "10001", "name": "Developers", "actors": null}]`; with `--with-users` `actors` is `[{"name": "jdoe", "displayName": "Jane Doe", "kind": "user"}]` (`kind`: `user` or `group`) |
| `dashboards` | `{"total": 3, "dashboards": [{"id": "10001", "name": "Team Dashboard", "description": "", "view": "https://jira.example.com/secure/Dashboard.jspa?selectPageId=10001"}]}`; `total` is what Jira found (for `--mine`, `--favourite` or, without either, every dashboard you can see), the list is cut by `--limit`; `description` is usually empty (the reference states none) |

```json
{"project": "PROJ", "issueTypes": [{"id": "10200", "name": "Story", "subtask": false,
   "statuses": [{"id": "1", "name": "To Do", "category": "new", "description": "..."}, {"id": "5", "name": "Done", "category": "done", "description": "..."}]}],
 "doneStatuses": ["Done"]}
```

`jira statuses --json`: without `--project` the object has `project: null` and `statuses[{id, name, category, description}]` of the whole Jira instead of `issueTypes`. `category` is `new`, `indeterminate` or `done`;
`doneStatuses` names the statuses of the category `done`, once each: it is what "done" means.

## `jira server --json`

```json
{"baseUrl": "https://jira.example.com", "version": "11.3.12", "deploymentType": "Server", "buildNumber": 11003012,
 "compatibility": {"status": "ok", "detail": "Jira 11.3.12 is on the 11.3.x release line this CLI is written and tested for", "hint": ""},
 "settings": {"attachmentsEnabled": true, "attachmentUploadLimit": 10485760, "subTasksEnabled": true, "issueLinkingEnabled": true, "votingEnabled": true,
              "watchingEnabled": true, "unassignedIssuesAllowed": false, "timeTrackingEnabled": true,
              "timeTracking": {"workingHoursPerDay": 8.0, "workingDaysPerWeek": 5.0, "timeFormat": "pretty", "defaultUnit": "minute"}}}
```

`baseUrl` is the address the CLI is configured with. `compatibility.status` is `ok` or `warn` (see the [compatibility page](compatibility.md)). A value that Jira does not state is `null` (`buildNumber`, the booleans,
`attachmentUploadLimit`, `timeTracking`); `attachmentUploadLimit` is in bytes and is `null` when `GET /attachment/meta` failed (a warning on standard error says so).

## Collaboration on an issue

```json
{"key": "PROJ-1", "count": 2, "watching": false, "watchers": null}
{"key": "PROJ-1", "count": 1, "hasVoted": false}
{"key": "PROJ-1", "total": 2, "count": 1, "timeSpentSeconds": 7200, "timeSpent": "2h",
 "worklogs": [{"id": "10101", "started": "2026-10-18T13:00:00.000+0000", "timeSpent": "2h", "timeSpentSeconds": 7200, "comment": "", "author": null,
               "created": "2026-10-18T15:05:00.000+0000", "updated": "", "visibility": ""}]}
[{"id": "10000", "title": "Runbook", "url": "https://wiki.example.com/runbook", "relationship": "documentation", "application": "", "globalId": "wiki:runbook"}]
```

The lines above are the answers of `watchers`, `votes`, `worklogs` and `remote-links`, in that order. `watchers`: `count` and `watching` (the token's user) are always there; `watchers` is `null` unless `--with-users`, then `[{name, displayName, email}]`, and
stays `null` when Jira lists nobody to this user. Voters are never read. `worklogs`: newest first; `total` is the number of entries the issue holds, `count` the ones shown (`--since`, `--limit`), `timeSpentSeconds` and
`timeSpent` (`3h 30m`, clock hours) cover the ones shown; `author` is `null` unless `--with-users`; `comment` is redacted; `visibility` is `""` or `role:Developers` / `group:staff`. `remote-links` is a plain list.

The writes print what they did. With `--dry-run` the same object has `"dryRun": true` and what a real run sends:

| Command | Keys |
| --- | --- |
| `watch` | `key`, `action` (`added`, `removed`), `user` (what `--user` said, else `null`; `--user me` is `null` too), `payload` (adding: the user name as a JSON string; `null` for removing; under `JIRA_HIDE_USERS=true` `[you]` for your own name in a dry run and `null` in the result of a call that was made), `query` (removing: `{"username": "jdoe"}`; else `{}`; `null` in the result of a call that was made under the policy), `dryRun`, `watchCount`, `watching` (both `null` when the list could not be read back), `confirmed` (the list read back shows the change; `null` on a dry run, when Jira lists nobody, or when the list could not be read back), `redacted` (`true` when `JIRA_HIDE_USERS=true` kept your own name out, else `false`), `message` |
| `vote` | `key`, `action`, `dryRun`, `changed` (`false`: it was so already and nothing was sent; on a dry run whether a call would be made), `count`, `hasVoted` (both `null` when the votes could not be read back after the call), `message` |
| `log-work` | `key`, `payload` (`{"timeSpent": "1h 30m", "comment": "...", "started": "2026-10-15T08:30:00.000+0200"}`, only what was asked for), `query` (`{"adjustEstimate": "new", "newEstimate": "2h"}` or `{}`), `seconds` (for hours and minutes; `null` for days and weeks, which Jira converts), `dryRun`, `worklog{id, started, timeSpent, timeSpentSeconds, comment, author: null, created, updated, visibility}` (read back; what Jira answered to the write when the list could not be read; `null` on a dry run), `confirmed` (`null` on a dry run or when the list could not be read back), `message` |
| `remote-link` | `key`, `action` (`created`, `updated`), `payload` (`{"object": {"url", "title"}, "relationship", "globalId"}`), `dryRun`, `link{id, title, url, relationship, application, globalId}` (read back; `null` on a dry run and when the list could not be read back), `confirmed` (`null` on a dry run or when the list could not be read back), `message` |

`message` is `""` when all went well. It is how a result tells a program, and a model behind `jira mcp`, which never sees standard error, that **a write was made but is not confirmed**: the read after it failed (HTTP 503, a timeout, a refusal), or Jira's list does not hold the entry yet (`confirmed: false`). The command exits with 0, `confirmed` (which `vote` has not) is `null` for a read that failed, the values that came from the read-back are `null` (`count` and `hasVoted` of `vote`, `watchCount` and `watching` of `watch`, `link` of `remote-link`), and `message` says what was done, why the read failed and which command shows the result (`jira worklogs KEY`, `jira remote-links KEY`, `jira watchers KEY`, `jira votes KEY`). The same text is a warning on standard error. **The write must not be repeated**: a second `log-work` is a second entry. Everything that goes wrong before the write is an error as before, and `watch --remove` for a person who is not you is a usage error (exit code 2), not a result.

## Write commands

`transition`, `comment`, `link`, `update`, `update-json`, `create`, `create-json`, `assign` and `attach` print what they did. With
`--dry-run` the same object has `"dryRun": true` and the `payload` that would have been sent. The sprint, epic, rank, watch, vote,
work log and web link commands do the same; their keys are in the sections above.

| Command | Keys |
| --- | --- |
| `transition` | `key`, `currentStatus`, `options[{id,name,toStatus{name,category}}]`, `chosen`, `dryRun`, `executed`, `payload`, `warnings`, `screens` (empty unless `--show-fields`) |
| `comment` | adding: `key`, `payload`, `dryRun`, `commentId`; `--list`, `--get` and `--edit`: see [Comments](#comments) |
| `link` | `key`, `other`, `typeName`, `phrase`, `relation`, `payload`, `dryRun`, `confirmed` (the relation read back from Jira) |
| `update`, `update-json` | `action` (`updated`), `key`, `payload`, `dryRun`, `created` (`null`), `warnings` |
| `create`, `create-json` | `action` (`created`), `key`, `payload`, `dryRun`, `created{id,key,url}`, `warnings` |
| `assign` | `key`, `action` (`assigned`, `unassigned`, `automatic`), `payload` (`{"name": "jdoe"}`, `{"name": null}`, `{"name": "-1"}`), `dryRun`, `assignee` (the user name that Jira holds after the write; in a dry run the user name that would be sent, `null` for `unassigned` and `automatic`) |
| `attach` | `key`, `dryRun`, `payload{request,headers,parts[{field,filename,contentType,size}],comment}`, `files[{name,path,size,mimeType,existing}]`, `limits{enabled,uploadLimit}`, `warnings`, `attached[{id,filename,mimeType,size,created}]`, `commentId` |
| `import-md` | `file`, `action`, `key`, `dryRun`, `message`, `payload` |
| `import-md-dir`, `import-csv` | `dryRun`, `mapFile`, `created`, `updated`, `skipped`, `failed`, `outcomes[{file,action,key,dryRun,message}]` |

## Comments

```json
{"key": "PROJ-1", "total": 4, "count": 2, "since": "2026-10-18T12:00:00+00:00",
 "comments": [{"id": "10100", "created": "2026-10-19T14:00:00.000+0200", "body": "Latest word.", "author": null, "updated": "2026-10-19T14:00:00.000+0200",
               "byMe": true, "authorRef": "a-1b2c3d4e", "visibility": "", "renderedBody": ""}]}
```

`comment KEY --list --json`: `total` is the number of comments the issue has, `count` the number listed (`--since`, `--last`), `since` the moment that `--since` named in ISO 8601 (`null` without it).
Per comment: `author` is `null` unless `--with-users`; `byMe` says whether the token's user wrote it (`null` when Jira names no author); `authorRef` is a pseudonym of the author, `a-` and eight
hexadecimal digits, the same for the same author in every call with the same token, and another one when the token is replaced, because it is keyed with the token (`""` without an author); `visibility` is `""`, `role:Developers` or `group:staff`; `renderedBody` is `""` unless `--rendered`, then the text
as Jira renders it, as plain text. Mentions and addresses in `body` and `renderedBody` are `[~user]` and `[email]` unless `--with-users`. `--select a,b.c` keeps only those keys of every comment.

`comment KEY --get ID --json` is the keys of one comment with `key` in front: `{"key": "PROJ-1", "id", "created", "body", "author", "updated", "byMe", "authorRef", "visibility", "renderedBody"}`.
The comments in `issue --comments --json` have the same keys, but `byMe` is `null` and `authorRef` is `""` there: `issue` does not ask who you are.

```json
{"key": "PROJ-1", "commentId": "10100", "action": "updated", "dryRun": true, "payload": {"body": "New text", "visibility": {"type": "role", "value": "Developers"}},
 "updated": "2026-10-16T09:00:00.000+0200", "byMe": false, "currentBody": "Old text", "diff": "--- current\n+++ new\n@@ -1 +1 @@\n-Old text\n+New text",
 "warnings": ["Someone else wrote this comment: Jira refuses the edit unless the token's user may edit all comments."]}
```

`comment KEY --edit ID --json`: `action` is `updated`, or `unchanged` when the comment has this text already and nothing was sent. `payload` is what was (with `dryRun` would be) sent: the new text and,
for a restricted comment, its `visibility`. `updated` is the new time after a live edit and the time the comment has now in a dry run. `byMe` is filled in a dry run and `null` after a live edit.
`currentBody` is the text before the edit without mentions and addresses of people. `diff` is the unified diff of a dry run on a small comment (`""` otherwise). No person is in the result. The
conflict of `--if-unchanged-since` is an error with `code: "CONFLICT"` and `details: {key, commentId, updated, since}`.

## Dry-run checks and transition screens

`warnings` is a list of texts (`[]` when there is none) in the result of `create`, `create-json`, `update`, `update-json` and `transition`: a required field that the payload leaves out, a field that
the screen does not have and, for `create` and `create-json`, a text, or the name of a label, a component or a fix version, that holds a stand-in (`[~user]`, `[email]`, `[personal link]`). A value that Jira would refuse is an error, `code: "REJECTED"`, exit code 6:

```json
{"error": {"code": "REJECTED", "message": "priority 'Urgent' is not available in PROJ for Story (10200).",
           "hint": "Allowed priorities: Highest, High, Medium, Low, Lowest.", "exitCode": 6,
           "details": {"problems": [{"field": "priority", "kind": "value", "value": "Urgent", "allowed": ["Highest", "High", "Medium", "Low", "Lowest"]}]}}}
```

`kind` is `value` (not among the allowed values; `allowed` lists at most 30), `required` (a required field is cleared) or `screen` (a transition screen does not have the field). An `--assignee`
that fits several users is `code: "INVALID"` with `details: {"query": "Jane", "matches": 2}`; one that fits nobody is `NOT_FOUND` with `matches: 0`.

A new summary, description or acceptance criteria of `update`, `update-json` or the `--fields-json` of `transition` that holds a stand-in which the current text does not hold, and a new label,
component or fix version whose name holds one that the current list does not hold verbatim, would overwrite the real mention or address: `code: "INVALID"`, exit code 1, nothing sent (a dry run
too), and `details` names the fields and the stand-ins:

```json
{"error": {"code": "INVALID", "message": "The new labels hold [email], which stands in place of a mention or an address that the CLI hides: sending it would overwrite the real one.",
           "hint": "The CLI shows [~user], [email] and [personal link] in place of real mentions, addresses and links. Write the real mention ([~username], see `jira users resolve`) or leave that part out; a text that holds people is best edited in Jira itself. If the stand-in is really what you want to write, send it with `jira api PUT issue/PROJ-1 --data-file FILE --write` (the raw call has no guard).",
           "exitCode": 1, "details": {"fields": ["labels"], "placeholders": ["[email]"]}}}
```

`details.fields` holds `summary`, `description`, `acceptance`, `labels`, `components` and `fixVersions` (a list of names takes the plural verb in the message; the issue key in the hint is that of
the command). For a transition the hint names `jira api POST issue/KEY/transitions --data-file FILE --write`, a file with `{"transition": {"id": "ID"}, "fields": {...}}`, instead of the PUT of an
update. Custom text fields other than the acceptance criteria, a comment that is added (`comment --text`, `transition --comment`) and the `--resolution` are not judged. `comment --edit` refuses a
stand-in in the same words; `push` and the imports refuse one in their own.

```json
{"key": "PROJ-2", "currentStatus": "In Progress", "options": [{"id": "81", "name": "Finish", "toStatus": {"name": "Done", "category": "done"}}],
 "chosen": null, "dryRun": false, "executed": false, "payload": null, "warnings": [],
 "screens": [{"id": "81", "name": "Finish", "toStatus": {"name": "Done", "category": "done"},
              "fields": [{"id": "resolution", "name": "Resolution", "required": true, "allowed": ["Fixed", "Won't Fix", "Duplicate"], "allowedCount": 3}]}]}
```

`transition KEY --show-fields --json`: `screens` has an entry per possible transition (only the one named by `--to`), with the fields of its screen. `allowed` holds at most 30 names and `allowedCount`
says how many there are; both are `null` for a field without a list of values (free text, a person). Without `--show-fields` `screens` is `[]`.

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
| `users` | `resolve`: `{"query", "matches", "name", "active"}` (`name` and `active` are `null` unless `matches` is 1; with `--with-users` also `displayName`, `email`); `search`: `{"query", "count", "users": [{"name", "displayName", "email", "active"}]}` |
| `ping` | `{"ok": true, "baseUrl": "...", "user": {"name","displayName","email"}}`; `user` is `null` with `--quiet` or the privacy policy |
| `doctor` | `{"ok": true, "checks": [{"name","status","detail","hint"}], "cached": false, "cachedAt": null}`, `status` is `ok`, `warn` or `fail`; `cached` is true when `--cached` reused the online checks of an earlier run (`cachedAt` is then its time). The checks include `launcher`, `server` (the verdict on the release line, see the [compatibility page](compatibility.md)) and `privacy` |
| `config show` | `{"file": "...", "settings": [{"name","value","source","description","secret"}]}`; secrets are masked, and so are the user name and password of `JIRA_BASE_URL` (`https://********@host/jira`; `(not shown: it holds an @ after the host name)` for an address that cannot be split) |
| `use`, `current`, `clear-project` | `{"project", "name"}`, `{"project": null}`, `{"cleared": true}` |
| `version` | `{"name": "jira-dc-cli", "version": "1.1.0", "python": "3.13.3", "platform": "win32", "jiraDataCenter": "11.3.x"}`; the last key is the Jira Data Center release line that the CLI is written for |
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
