# Changelog

All notable changes to jira-dc-cli are recorded in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`.

## [Unreleased]

## [1.1.0] - 2026-10-30

Everything below was tested offline against the bundled mock Jira Data Center and in-memory fakes. The mock now refuses every call
that the Jira Data Center 11.3 REST API reference does not document (the one exception is the address of an attachment's content,
`GET /secure/attachment/{id}/{name}`, which is no REST call), and the tests assert that no command makes one. No Jira 11.3 server
was available: use `--dry-run` first, as before. What a real server may do differently, because the reference does not say, was not seen:
which changelog entries Data Center sends with `expand=changelog` (`jira issue --history`, the sprint review), whether the Sprint field accepts
the history operator (the review then falls back and says so), the HTML of a customised renderer, the words of Jira's refusals, the permission
names in the hints, the shape of the `207` answer of a rank, whether an edit of a comment that leaves out its restriction removes it (the CLI
sends it again), how Jira's user search matches names, which fields its metadata lists, and how a real server serves an attachment (redirects,
a missing `Content-Length`). `jira mcp` ran as a child process over pipes in the tests, and once with the official MCP Python SDK as the client; no MCP client
such as VS Code was available, and the path checks (of `jira mcp`, and the places of the configuration that `jira attach` and `jira download` compare) were tested with a junction and a hard link, not with a symbolic link to a file (creating one needs a privilege that was missing).

### Added

- **Compatibility with Jira Data Center 11.3.x** is stated and checked. The CLI is written for the REST API 2 and the Jira Software API
  of 11.3 ([compatibility](docs/reference/compatibility.md)): the list of the calls of the reference (derived from its OpenAPI document of
  11.3.12) sits in `tests/support`, and a sweep of the commands proves that each one sends only documented calls. `jira doctor`
  compares the server with 11.3.x (`ok` on the same release line or major release, `warn` on an older or newer major release and on
  Jira Cloud), and `jira version --json` has the new key `jiraDataCenter`.
- `JIRA_PROFILE=agent` (a setting, also `jira config set JIRA_PROFILE agent`): one setting for an AI agent. It makes `JIRA_HIDE_USERS=true`
  and `JIRA_OUTPUT=json` the defaults (a value you set wins) and keeps informational messages off standard error. `jira config show`
  lists the profile and what it implies; errors are JSON when the settings ask for JSON, not only with `--json`.
- `--json-envelope` on every command (or `JIRA_JSON_ENVELOPE=1`): exactly one JSON object on standard output, `{"ok": true, "data": ...}`
  or `{"ok": false, "error": {...}}`, for tools that report the exit code as 1 always or drop standard error. The exit code and standard
  error stay as they are; without the switch nothing changes.
- `jira doctor --quick` (the local checks and at most two requests), `--cached AGE` (the online checks of a successful run that is not
  older, no request, and the answer says so; `cached` and `cachedAt` in the JSON) and `--strict-privacy` (a failed `privacy` check, exit
  code 1, whenever people can be shown). A `launcher` check says whether `jira` is found on the `PATH` and prints the installer command that
  fixes it; the `privacy` check is a `warn` when the profile is on and people can still be shown.
- `jira api --param KEY=VALUE` (repeatable): query parameters, URL-encoded, so that no `&` has to survive `cmd.exe` or PowerShell.
  `jira api ... --dry-run --json` prints the request as one JSON object (`dryRun`, `method`, `path`, `query`, `body`).
- `jira help --brief`: every command with its arguments and main options in at most 4 KB. The skill carries, generated, the full options
  and one example of `comment`, `api`, `create`, `update`, `pull` and `diff`.
- `jira wiki --check` warns, on standard error, about markup that Jira is known not to render as the Markdown meant it (tables, task lists,
  HTML, reference-style links, footnotes, an underlined heading, a brace outside code).
- `jira metadata --project KEY --type NAME` (besides `jira metadata KEY ID`; the current project is used when none is given).
- A search whose JQL is too long for a URL (a long `key in (...)`) is sent as a `POST /search`.
- The installer prints the exact `--bin-dir` command for the folder on the `PATH` that already holds the launcher of `conflu`.
- **`jira issue KEY [KEY ...]` reads several issues with one request** (`key in (...)`, a hundred keys per request). The keys may be separated
  by blanks or commas, and the issues come back in that order. One key prints the issue as before; several print `{count, issues[], errors[]}` (the number of keys given decides, not the number
  of different ones: `PROJ-1 PROJ-1` is a list of one).
  A key that does not exist, or that you may not see, has its own entry `{key, code, message}` in `errors` and a line in the text, the exit
  code is 4, and the issues that were found are still printed. (Jira refuses a whole `key in (...)` query that holds an unknown key, HTTP 400:
  the CLI then reads the keys one by one.)
- **`jira issue` reads a long issue in parts**: `--only NAMES` (`summary`, `status`, `priority`, `labels`, `components`, `fixVersions`, `epic`,
  `points`, `sprints`, `dates`, `description`, `acceptance`, `comments`, `links`, `subtasks`, `attachments`; an unknown name is a usage error
  that lists them), `--max-chars N` (every long text is cut at the end of a paragraph, never inside a word and without splitting a code block (a text
  that opens with one is cut at a line and the block is closed); `truncated` and `totalChars` by field in the JSON), `--outline` (the headings of the
  description with their level and size) and `--section TITLE|N` (the part under one heading; one to six digits `0`-`9` are the number of a heading,
  anything else is looked for as the text of a heading; no match, or several, is an error that lists the headings). Guide:
  [read issues in batches and in parts](docs/how-to/read-issues.md).
- `jira issue --rendered` shows the description and the acceptance criteria as Jira renders them (plain text that keeps lists, code and headings;
  `renderedDescription` and `renderedAcceptance` in the JSON), and `jira issue --history` the status changes (Jira's own status field, not a
  custom field that is called status) with their time, oldest first and without people (`history[{created, from, to}]` and `historyComplete`, which is `false` when Jira sent only part of a long changelog; Data Center
  has no `/changelog` endpoint, so the history comes with `expand=changelog` on the same request). The text of `jira issue` names the attachments
  (`Attachments: 2` and the file names), and `--only attachments` shows them alone; `attachments[]` and `attachmentTotal` are in its JSON.
- **Saved filters**: `jira filters` lists the favourite filters of the user (id, name, description, JQL; Data Center has no call that lists
  every filter), `jira filter ID` shows one with the address that runs it and the address that opens it, and `--filter ID` runs one in
  `jira issues` (`--jql` narrows it) and in `jira search` (added with AND to the project, the words and the other conditions). The owner, and the people that the JQL names
  (`assignee = [user]`), are shown only with `--with-users`.
- **Queries that survive Windows PowerShell 5.1**: `jira issues --jql @FILE` and `--jql -` (standard input), `jira search --jql @FILE`,
  `jira search '@question.txt'` and `jira search -` read the text from a file or from standard input. A file written by Windows PowerShell 5.1 is
  read correctly: UTF-8 with or without a byte order mark, UTF-16 with or without one, and Windows-1252 ("ANSI") text. A query file may hold at most
  1 MiB. A search text that starts with `@` is read as a file name; the error says how to search for it (put a space in front of it, or use
  `--phrase`). `jira search --phrase 'exact words'` (repeatable) adds an exact phrase without a double quote. The notes and examples of both
  commands show the file form.
- `--select a,b.c` and `--format table|tsv|md` on `jira issues`, `jira search`, `jira fields`, `jira filters`, `jira comment --list` and the list commands below
  (the reference lists, `boards`, `sprints`, `velocity`, `backlog`, `epics`, `worklogs`, `remote-links`, `attachments`, `users`): with `--json` only the named (dotted)
  keys of every item, otherwise a table, TSV or Markdown in the columns of the table (or of `--select`). Without the options the output is unchanged.
- **Boards, sprints and the sprint review without `jira api`** (group `Agile`; [guide](docs/how-to/sprints-and-boards.md)): eleven commands over
  the Jira Software REST API of Data Center 11.3 (`/rest/agile/1.0`) and the platform search; the internal `rest/greenhopper` endpoints are not
  used. No command shows a person. Read: `jira boards [--project KEY] [--type scrum|kanban|simple] [--name TEXT]`;
  `jira sprints [--board ID | --project KEY] [--state active,closed,future]` (the active sprint first, then the future ones, then the closed ones,
  the newest first; the board is resolved from the project, several scrum boards are listed in the error, a kanban board has no sprints);
  `jira sprint ID|NAME [--issues] [--review] [--with-subtasks]` (a name is looked up among the sprints of the board, exact and case-insensitive;
  an unknown or ambiguous name lists the candidates); `jira velocity [--last N]` (committed and completed points and issues of the last closed
  sprints, default 5, at most 20, and the average of the completed points); `jira backlog [--limit N]` (rank order) and `jira epics [--done]`.
- `jira sprint ID --review` counts the issues, the story points and the keys **committed** (in the sprint when it started), **completed**,
  **added** after the start, **removed** and **carried over**. It is derived from the change history of the issues (`expand=changelog`: the field
  Sprint and the status), not from Jira's own sprint report, and says so in `notes`. Story points are the current values (sums are rounded: 0.1
  three times is 0.3); sub-tasks are left out unless `--with-subtasks`. A review whose history is not whole says so: when Jira refuses the search
  `sprint was`, `removed`, `committed` and `added` (and the committed numbers of `velocity`) are lower bounds, and `notes`, `historyComplete: false`
  (the review and every `velocity` row) and a `*` in the text table say it. The flag is also set for an issue whose history Jira cut, whose history
  does not end where the issue is, or that holds a time that cannot be read. `velocity` names the closed sprints that cannot be reviewed (no start
  date, more than 2,000 issues) in `leftOut` and in a sentence, not as "No closed sprints.", and `has no start date` replaces `has not started`.
- **Sprint planning from the command line**: `jira sprint-add KEY... --sprint ID|NAME` (batches of 50, the limit of the reference),
  `jira rank KEY... --before|--after KEY`, `jira epic-add KEY... --epic KEY|none`, `jira sprint-create NAME --board ID [--goal --start --end]` and
  `jira sprint-update ID|NAME [--name --goal|--no-goal --start --end --state active|closed]`. Each has `--dry-run`, reads its targets first (an
  unknown key, sprint or epic fails with `NOT_FOUND` before anything is sent) and prints what it read back afterwards. `--if-state` (sprints) and
  `--if-unchanged-since` (issues) guard them: exit code 3, and nothing is sent, when the sprint is not in that state or an issue changed after that
  time. The rules of the reference (a closed sprint cannot be changed, a sprint is started from future with
  both dates, completed from active, at most 50 issues per call) are checked before Jira is asked; a refusal with HTTP 403 names the permission.
  `sprint-add`, `epic-add` and `rank` use the key that Jira holds for an issue (the old key of an issue that moved to another project is read as
  its new key, and `notes` says `OLD-5 is now PROJ-9.`), so a moved issue causes no false "accepted but not found" warning. `sprint-add`, `epic-add`,
  `rank`, `sprint-create` and `sprint-update` say with `readBack` (true or false) whether their read-back worked. Without a read-back (a dry run, nothing to
  send, or a read-back that failed after Jira took the write) `confirmed` and `missing` (`order` and `confirmed` of a rank) are `null` instead of empty, so
  that "nothing is missing" cannot be mistaken for "nothing could be checked"; a failed read-back leaves a note that says not to send the command again,
  and the exit code is 0.
- **The facts of a Jira, read-only** (`--json` everywhere, every list takes `--select` and `--format`): `jira priorities`, `jira resolutions`,
  `jira issue-types [--project KEY]`, `jira statuses [--project KEY]` (with `doneStatuses`: the statuses of the category `done`, which is what
  decides that an issue is done), `jira components [PROJECT]`, `jira fix-versions [PROJECT] [--released|--unreleased] [--counts]` (`--counts` adds
  `counts{fixed,affected,unresolved}` and is bounded to the first 25 versions, two requests each), `jira roles [PROJECT]`,
  `jira dashboards [--mine|--favourite] [--limit N]`, `jira permissions [--project KEY | --issue KEY] [--only NAME,NAME]` (what the token's user
  may do, `havePermission`; an unknown name is a usage error that lists the valid ones) and `jira server` (version, deployment type, build number,
  the base URL as configured, the verdict `compatibility{status,detail,hint}` on the 11.3.x release line, and the settings that matter: attachments
  and their upload limit, sub-tasks, issue linking, voting, watching, unassigned issues, time tracking).
- **Collaboration on an issue**: `jira watchers KEY` and `jira votes KEY` (counts; who only with `--with-users`, voters never),
  `jira watch KEY [--user NAME] [--remove]`, `jira vote KEY [--remove]`, `jira worklogs KEY [--since ISO|2d] [--limit N]` (newest first, with the
  total of the time), `jira log-work KEY TIME [--comment TEXT] [--wiki] [--started ISO] [--adjust-estimate auto|leave|new|manual]
  [--new-estimate TIME] [--reduce-by TIME]`, `jira remote-links KEY` and `jira remote-link KEY --url URL --title TEXT [--relationship TEXT]
  [--global-id ID]`. Every write has `--dry-run` (it shows the body and the query that a real run sends, and checks that the issue exists) and
  reads its result back (when that read fails the write still counts: exit code 0, `confirmed` is `null`, or `count` and `hasVoted` for a vote, and a
  `message` says that the entry was made and not to repeat it); `log-work` and `remote-link` also take `--if-unchanged-since`. Nothing deletes content: `watch --remove` takes only your
  own watch off (it is refused for another person, exit code 2; `--user me` is you) and `vote --remove` only withdraws your own vote. The time of
  `log-work` is validated (whole numbers with `w d h m`; a decimal, a bare
  number, zero and a repeated unit are refused) and goes to Jira in its own words, so that Jira converts days and weeks with its working time;
  `--started` is sent in the form Jira reads (`+0200`, no colon). The same `--global-id` updates a web link instead of adding it twice. Guide:
  [collaborate on an issue and read the facts of a Jira](docs/how-to/collaborate-and-reference.md).
- **Attachments** ([guide](docs/how-to/attachments.md)). `jira attachments KEY [--name FILE] [--with-users]` lists the files of an issue (`id`, `filename`,
  `mimeType`, `size`, `created`; one request, `GET /issue/{key}?fields=attachment`). The uploader (`author`) is `null` unless `--with-users`, which
  `JIRA_HIDE_USERS=true` refuses; `--name` keeps the files with that name (case-insensitive, exact) and `total` still says how many the issue has.
- `jira download KEY [ID] [--name FILE ...] [--all] [--out DIR] [--max-bytes N] [--force] [--dry-run]` saves attachments in `tmp/jira-attachments/KEY` (or
  `--out`). Names are made safe (no folder or drive, no control character, no trailing dot or blank, a prefix for Windows device names such as `CON`, at
  most about 150 characters, `-ID` for two files with one name). Nothing is overwritten without `--force`, and a file is created only when nothing is there at the
  moment it is written, so a file that another program makes while the download runs is kept, with an error that says so; a folder where a file would go is
  an error, in a dry run too. A file over `--max-bytes` (default 25 MiB) is
  refused by its metadata before the request and stopped while it is read when Jira sends more than it said (the limit is for the file: a 401, 403, 404 or
  5xx and a login page keep their exit codes and messages); a file is written whole or not at all, and
  a download that ends before the announced length is an error, never a short file. When a file of an `--all` run fails after others were written, the error
  counts and names them (`details.written`, `details.failed`) and says that a new run needs `--force` or an ID. The result names path, size and type and never prints bytes;
  `--dry-run` shows what would be written. With `--text` it prints the decoded text of text files (by MIME type and by extension) and of Word, Excel and
  PowerPoint files (`docx`, `xlsx`, `pptx`, with the standard library only), cut at the end of a paragraph or line at `--max-chars` (default 20000) with
  `truncated` and `totalChars`; mentions and e-mail addresses are redacted unless `--with-users`; images, PDFs and other types print their metadata and a
  hint that points to the plain `download`. A document that is damaged, protected with a password, refused as unsafe or too large to be read as text (see
  Security) is an entry with a `hint` that names the kind of failure and never the content, not a failure of the run: the other files are still read. A Word
  list level is at most 8, a paragraph whose numbering is `numId` 0 is no list item, and a cell or a shared string is cut at the 32,767 characters that Excel
  allows (it ends with `…`). In `jira commands --format mcp` it is a tool that writes files (`readOnlyHint: false`), as `pull` and `export` are:
  of `--only write`, and `jira mcp` offers it only with `--allow-files`.
  Tested against the attachment routes of the mock Jira, among them a redirect to another host, a redirect loop, a body without `Content-Length`, an
  endless body, a lying `Content-Length`, a `Content-Length` that is no number and a login page, and, for `--text`, a spreadsheet whose cells all point to one
  long shared string and a Word file whose `xmlns` address of 20,000 characters is used by thousands of attributes.
- `jira attach KEY [FILE ...] [--path FILE ...] [--comment TEXT] [--dry-run]` adds files to an issue (`POST /issue/{key}/attachments`, one multipart part named
  `file` per file, header `X-Atlassian-Token: no-check`). The dry run lists name, size and MIME type, checks that the issue exists, reads `GET /attachment/meta`
  (attachments switched on, upload limit), warns when a name exists on the issue already (Jira keeps both) and sends nothing; a missing, empty, oversized or folder
  file, and a name that is no text (a damaged character), are refused before any upload request, in the dry run and in the real run. The `payload` of the dry run
  shows each name as it goes on the wire (`"` as `%22`, line breaks as `%0D` and `%0A`). **The arguments keep their boundaries**: an argument that names a file is
  one path, blanks and all (a quoted `'x y'` is the file `x y` even when the files `x` and `y` exist too, and `x y` without quotes are the files `x` and `y`);
  forgotten quotes are forgiven when the words together name a file (eight words at most, the shortest join first), an argument that names no file and holds a
  blank is split at it (the text of a program that joined its words, as the MCP tool passes the files), and what leads to no file is named whole in the error.
  `--path FILE` (repeatable) is the exact form, one whole path that is never split or joined; `FILE` is optional when `--path` is given. `--comment` adds a comment
  after the upload; when the comment fails the
  error says that the files are attached, names them (`details.attached`) and has the exit code of the comment's error. The configuration of the CLI and any file
  that holds the token are refused (see Security). No command deletes an attachment.
- **Read and correct one comment** ([guide](docs/how-to/comments-and-people.md)). `jira comment KEY --get ID` prints one comment (id, created, updated,
  text as stored, `byMe`, `authorRef`, `visibility`; the author only with `--with-users`). `jira comment KEY --edit ID (--text TEXT | --file FILE) [--wiki]
  [--dry-run] [--if-unchanged-since ISO]` replaces the text of a comment: it reads the comment first, **sends the restriction (role or group) again** so
  that Jira cannot drop it, and the dry run shows a unified diff of the current and the new text (the current text without mentions and addresses of
  people) and what would be sent. `--if-unchanged-since` compares the `updated` time of the comment (exit code 3, nothing written) and reads the comment
  again just before the write. Somebody else's comment is refused by Jira with exit code 5 and a hint that names the permission (`Edit All Comments`); the
  dry run warns when the comment is not yours. A new text that holds `[~user]`, `[email]` or `[personal link]` (what a reading puts in place of people) is
  refused, and the same text as the comment sends nothing (`action: "unchanged"`). A comment is never deleted.
- `jira comment KEY --list` gets `--since 2d|12h|1w|DATE|ISO` and `--last N` (they look at the time a comment was created; a time ago counts from the clock; a comment id and a time ago take the digits `0` to `9` only)
  and `--rendered` (also with `--get`: `expand=renderedBody`, shown as plain text with bullets, numbers and code kept, JSON key `renderedBody`; markup that
  Jira renders wrongly stays visible; a mention that Jira renders as a link with a display name is `[~user]` unless `--with-users`). Each comment has
  `byMe` (one extra request, `GET /myself`) and `authorRef`, a stable pseudonym (`a-` and eight hexadecimal digits of an HMAC-SHA-256, keyed with the
  token, over the address of the Jira and the lower-cased user name) so that authors can be told apart while names are hidden; it is the same in
  every command that uses the same token and changes when the token is replaced. `jira comment KEY --text TEXT --visible-to role:NAME|group:NAME`
  adds a comment that only that role or group can see.
- **Users.** `jira users resolve NAME [--project KEY | --issue KEY]` finds the user name that `--assignee` needs and prints only `query`, `matches`, `name`
  (`null` unless exactly one user fits) and `active`; an ambiguous name is counted, never listed. Order: exact user name, exact display name, exact address,
  one partial match; `me` is the token's user. `jira users search NAME [--project KEY | --issue KEY] --with-users` lists people; it is refused without
  `--with-users` and when `JIRA_HIDE_USERS=true`. `jira assign KEY ASSIGNEE [--dry-run] [--if-unchanged-since ISO]` assigns an issue
  (`PUT /issue/{key}/assignee`) to a user name, display name, address, `me`, `unassigned` or `automatic`, and reads the assignee back; Jira answers 401 when
  the token's user may not assign issues, and the error says so and names the permission.
- `--assignee` of `jira update`, `jira create` and the imports (`import-md`, `import-md-dir`, `import-csv`) accepts a display name or an address that fits
  exactly one user (among the users who can be assigned the issue or, for a new issue, issues of the project), `me`, `unassigned` and `automatic`. The dry run
  shows the resolved user name.
- **The dry run checks values.** `--dry-run` of `create`, `create-json`, `update`, `update-json` and the imports checks the priority (by name or id), the
  components, the fix versions, the affected versions and, for `create`, the issue type against the metadata of Jira
  (`GET /issue/createmeta/{project}/issuetypes/{id}` and `GET /issue/{key}/editmeta`). A value that the project does not offer is exit code 6 (`REJECTED`)
  with the allowed values in the hint (at most 30, then "and N more"). A required field that the payload leaves out, and a field that the screen does not
  have, are `warnings` (standard error, and the JSON result). Custom fields are not judged and a live run is not checked. A blank value (`--priority ''`)
  is exit code 6 as well, and the create metadata is read page by page (every page, at most 20 requests).
- `jira transition KEY --to STATUS` gets `--fields-json JSON|@FILE` and `--resolution NAME` (they add `fields` to the transition payload), `--show-fields`
  (what the screen of each possible transition holds, with the allowed values: `GET /issue/{key}/transitions?expand=transitions.fields`) and
  `--if-unchanged-since ISO` (exit code 3 when the issue changed after that time, as for `update`). The dry run checks the fields against the screen (exit
  code 6 for a value or a field it does not allow, a warning for a required field that is missing).
- **Links between issues are part of the file sync** (`jira diff`, `jira pull`, `jira push`). A Markdown ticket lists the links of its issue in
  one front matter line, `links: Blocks -> PROJ-2, Blocks <- PROJ-3`: the name of the link type, `->` when this issue is the outward end (it
  blocks PROJ-2) or `<-` when it is the inward end (PROJ-3 blocks it), then the other key. The links are compared per field against `jira_base`
  (a `links=` record), like every other field: a link that only the file lists is `edited here`, one that only Jira holds is `jira changed`, the
  same links changed on both sides are a `conflict` (exit code 3, nothing sent). `pull` writes the line into the files it makes and, with
  `--update`, the links that Jira changed (an edit of yours is never overwritten). `push` creates the links that only the file lists
  (`POST /issueLink`; the type and the other issue are checked first, a link is never created twice, `--dry-run` shows the request of each) and
  never removes one: a link that Jira holds and the file no longer lists is named (`jira has it; remove it in Jira`), stays `edited here` and
  blocks no other field. [Guide](docs/how-to/export-and-sync.md#links-between-issues).
- `jira push --json`: every outcome has `links[]` (`entry`, `relation`, `payload` of each link that was created, or with `--dry-run` would be)
  and `jiraOnlyLinks[]` (links that Jira holds and the file does not list); `payload` is `null` when only links are sent. The hint of a refused
  link names the missing permission (*Link Issues*; the 11.3 reference answers 401 for it, Jira also answers 403), and a 404 says that linking
  may be disabled or that an issue is not visible.
- **`jira mcp [--allow-write] [--allow-files] [--show-users] [--group NAME]`: a local MCP server** on standard input and output, for an MCP client such as GitHub
  Copilot in VS Code (`.vscode/mcp.json`: `"type": "stdio", "command": "jira", "args": ["mcp"], "cwd": "${workspaceFolder}"`; the guide
  [use the CLI as an MCP server](docs/how-to/mcp-server.md) also covers another client and what to do when the launcher is not on the `PATH`).
  JSON-RPC 2.0, one message per line, protocol versions 2025-06-18, 2025-03-26 and 2024-11-05, standard library only. The commands that
  `jira commands --format mcp` describes are the tools (`jira_issue`, `jira_search`, ...): a call runs inside the server process, with no shell
  and a new application for every call (the settings file and the token are read again, and what a run looks up, such as a user or the components of
  a project, is asked of Jira again), and answers with what the command prints with `--json`. The tools that change Jira are offered only with
  `--allow-write`, the three that write files (`download`, `pull`, `export`) only with `--allow-files`, and a path in a call must lie below the
  folder the server was started in. A failure is `isError: true` and the error object
  (`code`, `message`, `hint`, `exitCode`, `details`), and a result over 100,000 characters is cut with a note. `--group` keeps only the
  commands of some groups of `jira help`; `mcp` itself, the commands for the person, `api` and `wiki` are never offered. Nothing else in the CLI
  depends on it.

### Changed

- A usage error `unrecognized arguments` says which command has an option that this one lacks (`issues` has no `--type`, `search` has) and,
  when the text of a query fell apart, that Windows PowerShell 5.1 removed its double quotes and what to do (`'@query.jql'`, `\"`).
- A write that Jira refuses (`REJECTED`, exit code 6) always has a hint: it names the field that Jira names and points to
  `jira metadata PROJECT ISSUE_TYPE_ID`, which lists the values the project allows.
- The mock Jira and the fake of the tests report Jira 11.3.12, and the mock answers a vote as the reference says (200 with the vote count).
- The first argument of `jira issue` is `keys` (`ISSUE_KEY ...`, several keys). The text and the JSON of one key without the new options are
  unchanged, apart from the Attachments block of the text for an issue that has attachments.
- With `--select` or `--format` and no `--json`, `jira issues` and `jira search` print the "Showing N of M" note on standard error when a page is
  shown, as they do for the table; with `--json` they print none, as before.
- A file that `jira pull` makes has a `links=` record in `jira_base` and, when its issue has links, a `links:` line after `fix_versions:`. Files
  from 1.0.0 keep working: a file without a record for the links is taken to have listed none, so the first `pull` only records the agreement
  when the issue has no links, and the links that Jira holds for any other are `jira changed` until `jira pull --update` writes the line (never
  `differs`, never a reason to refuse a push of another field).
- `jira push` can send requests that are no update of the issue (the links). It still creates no issue and deletes nothing; `--dry-run` lists
  each link and its request body.
- The links of an issue whose link type has a comma, `<` or `>` in its name are not compared (named under `Not compared`, `skipped` in `--json`);
  a `links:` line that cannot be read makes the file `unreadable` (`diff`, `pull`) or is refused (`push`), with the entry named.
- `jira commands`, its help and the guides say that `jira mcp` serves the tool descriptions to an MCP client; they no longer say that no MCP
  server comes with the CLI. `mcp` itself is never one of the tools.
- `jira commands --format mcp --only read` (the default) no longer lists `pull`, `export` and `download`, and `--only write` lists them besides the
  commands that change Jira: they change no issue but write files, so they are not read-only. Their annotations are as before (`readOnlyHint: false`,
  `destructiveHint: true`, `idempotentHint: true`); `--only all` is unchanged. The descriptions of the tools of `jira mcp` name only the tools that the
  server offers (without `--allow-files` a description says `download`, not `jira_download`). Over `jira mcp` the descriptions of the tools that write files
  say what they are there: nothing is replaced (`download` stops at a file that exists, `export` refuses one, `pull` only adds files and brings in what Jira
  changed) and the property `force` is marked "Refused over MCP"; `jira commands --format mcp` still prints the description for any other client. Over
  `jira mcp` the answer of `jira_download` and `jira_import_md_dir` names the default place in full (`outDir`, `mapFile` as an absolute path), as it does for a
  place that the call gave.
- Exit code 2 also covers a value that a command refuses before it does anything (an empty `--description-file`, or for `attach` the configuration of the CLI
  and a file that holds the token); the reference of the exit codes says so.
- `comment` is no longer an additive tool in `jira commands --format mcp` and `jira mcp`: it carries `destructiveHint: true` (and `idempotentHint:
  false`), because `--edit` replaces the text of a comment.
- The JSON of `jira comment --list` is `{key, total, count, since, comments[...]}`: `total` is the number of comments of the issue (it was the number
  listed, which is the same without a filter), `count` the number listed, `since` the moment `--since` named (`null` without it). In every comment of the
  list `updated`, `byMe`, `authorRef`, `visibility` and `renderedBody` are filled; the comments in `jira issue --json` have the same keys, with `byMe`
  `null` and `authorRef` `""`. The text of a comment in `--list` starts with the pseudonym of the author (or the display name with `--with-users`),
  `(you)` for your own comments, the restriction and the time of the last edit.
- `warnings` is a new key of the result of `create`, `create-json`, `update`, `update-json` and `transition`; `transition` also has `screens`.
- A name for `--assignee` of `update` and `create` that fits nobody is exit code 4, and one that fits several users is exit code 1; before, the
  text was sent as typed and Jira decided.
- The HTTP transport can limit the size of a successful answer and, only when asked (`jira download`), follow a redirect on the same scheme, host and port;
  every other call is unchanged. The body of any other answer (a redirect included) is read up to 64 KiB and never refused for its size, so that a 401, 403,
  404, 5xx or a login page keeps its exit code and message with a small `--max-bytes`; a `Content-Length` that is no plain number counts as unknown.

### Fixed

- The Markdown to wiki converter turned `` `token ` `` into `{{token }}`, which Jira does not render as monospace text. A blank inside the
  backticks now goes outside the braces, a span of blanks is only blanks, and a brace in code is escaped.
- `jira api METHOD PATH --write --dry-run --json` printed a text line and the body instead of JSON.
- `create --dry-run` and `update --dry-run` passed a priority, a component or a fix version that Jira then refused with HTTP 400 and an empty hint;
  they now refuse it (see the dry-run checks under Added).
- `--file`, `--description-file`, `--comment-file`, `--fields-json @FILE`, `jira api --data-file`, the files of `create-json` and `update-json`, the
  file of `jira wiki`, the settings file and standard input (`-`: `--file -`, `--description-file -`, `--jql -`, `setup-pat --stdin` and the other commands
  that read it) read UTF-8 with or without a byte order mark, UTF-16 (what Windows PowerShell 5.1 pipes and writes) and Windows-1252 text, and line ends
  are plain newlines; a byte order mark no longer ends up in a comment or a description that is sent to Jira. A settings file saved as UTF-16 (Notepad
  "Unicode") or Windows-1252 ended in an "unexpected error" before, over `jira mcp` in an internal error.
- `update --description-file` with an empty file, or with empty standard input, is refused (exit code 2): it would clear the description, and an empty
  file is nearly always a failed editor or pipe. `--description ''` still clears it on purpose.
- A token must be printable ASCII without blanks: `JIRA_PAT` in the settings and `jira setup-pat` refuse anything else (a line break would be a second
  header, a byte order mark or a letter like É breaks the request) with an error that does not repeat the text. Before, such a text was saved and the HTTP
  library then printed it in an "unexpected error".
- A `Retry-After` of `nan` is ignored; it crashed the retry.
- `JIRA_MAX_TIME`, `JIRA_CONNECT_TIMEOUT` and `jira projects-admin --timeout` accept at most 86,400 seconds; a larger value made the socket layer fail
  with an OverflowError.
- A Jira version string with an absurdly long number is no version (the `server` check of `jira doctor` and `jira server` say that the version is not
  a number and that compatibility is unknown) instead of an error.
- The names of attachments in `jira issue` are cleaned of terminal control characters, as the names in `jira attachments` are.
- **`jira update`, `jira update-json` and `jira transition` no longer overwrite a real mention or address with the stand-in that `jira issue` shows.** `jira issue` shows
  `[~user]`, `[email]` and `[personal link]` where Jira holds a mention, an address or a link, in the texts and in the names of labels, components and fix
  versions, and an update from that reading replaced the real one. A new summary, description or acceptance criteria (`--summary`, `--description`,
  `--description-file`, `--fields-json`; the `fields` and the `set` of `update` in `update-json`) that holds a stand-in that the current text does not hold,
  and a new label, component or fix version (`--labels`, `--components`, `--fix-versions`, `--fields-json`; the `fields`, the `add` and the `set` in
  `update-json`) whose name holds one that the current list in Jira does not hold verbatim, is now refused: exit code 1, the words of the refusal of
  `comment --edit`, the fields in `details.fields` (`summary`, `description`, `acceptance`, `labels`, `components`, `fixVersions`) and the stand-ins in
  `details.placeholders`, nothing sent, in a dry run too. The same holds for the `--fields-json` of `jira transition`, which sent them to the transition screen
  unchecked. The hint names the way out for a person who really wants to write the stand-in: `jira api PUT issue/KEY --data-file FILE --write` (for a transition
  `jira api POST issue/KEY/transitions --data-file FILE --write`), whose raw call has no guard. The issue is read once for this, and only when a new text or name
  holds a stand-in (the same read serves `--if-unchanged-since`; a transition reads the issue in any case). A text that holds the literal `[email]` in Jira
  already may keep it, and so may a label that Jira holds. Other custom text fields, a comment that is added (`comment --text`, `transition --comment`) and the
  `--resolution` are not judged (the documented limit). The dry run of `create` and `create-json` only adds a note to `warnings` (also for a label, a component
  or a fix version): a new issue has nothing to overwrite.
- **An issue type that was added to Jira in the last week is found.** The list of issue types is kept for a week, so a type that was added since was
  refused. A type that is not in the list is now looked for once more in a list that bypasses the cache before it is refused (`create --type NAME`,
  the live `create`, the dry runs and the imports), `create --refresh-metadata` reaches the check when the type is an id, and the refusal says that
  the list was just read from Jira. The same second look is taken for a component or a version that the metadata read earlier does not have and for a
  user name that fitted nobody, or several, earlier in the same run.
- A request address that no URL can hold (`http.client.InvalidURL`) is a usage error (exit code 2, nothing sent, no retry) instead of "Cannot reach Jira" after
  the retries.
- A sprint id, a board id or an issue key with thousands of digits is a usage error or is ignored instead of ending a command with an internal error
  (the backlog dump of `jira export` sorts such a key last).

### Security

- **`jira mcp` is safe by default.** The tools that change Jira are absent unless `--allow-write` (a call of one is refused as an unknown tool,
  `-32602`, and each has `dryRun`), the tools that write files on the computer (`jira_download`, `jira_pull`, `jira_export`) are absent unless
  `--allow-files` (the two switches are independent: neither offers the other's tools, and not even `--group Read` offers them), and the property
  `withUsers` is refused unless `--show-users` (and always under `JIRA_HIDE_USERS=true`). A call is checked against the schema of its tool before
  anything runs (an unknown or a missing property, a wrong type, a value outside the choices). Every option is written `--flag=value`, so that a value
  never becomes an option; a positional argument that starts with `-` is refused, and so is a value that starts with `@` for the properties that
  accept `@FILE` (the JQL of `issues` and `search`, the search text, the user name of `users`, `fieldsJson`): a model must not read a file of the
  computer through them. They are named in a table by command and argument, not found by the English of a help text, and a test over every real
  command fails when an argument looks like a path or `@FILE` and is in no table.
- **`jira mcp` keeps a model away from the files of the computer.** A property that names a path (the `file` of `jira_comment`, `descriptionFile`,
  `commentFile`, `payloadJson`, `fileMd`, `fileCsv`, the files of `jira_attach`, `out`, the folders of `jira_pull`, `jira_diff`, `jira_import_md_dir`
  and `jira_push`) is refused, with an error result that says what is allowed and without running the command, unless the path lies below the folder
  the server was started in once `..` and links are resolved. A network or device path (`\\host\share`, `//host`, `\\?\`, `\\.\`, `\??\`) is refused
  without being looked at, also when blanks stand in front of it, and a device name is read as Windows reads it (`con .txt`). The settings file of the
  CLI and anything in the folder of the settings or of the cache are refused, and a file that holds the token (UTF-8 or UTF-16) is not read by a text
  option, and the refusal does not show it. A call that writes may not write into a hidden folder (`.git`, `.vscode`), export onto a file that exists, or
  let `download` write into the working folder itself (it takes a folder below it, `tmp/jira-attachments/KEY` by default); `force` is not honoured
  (`download`, `pull`, `push`); and a working folder that is a drive root, the home folder or a folder above it, or the folder of the settings or of the
  cache (`~/.config/jira-dc-cli`, `~/.cache/jira-dc-cli`) or one above it, is refused. A folder that a call reads is not searched for the token, and the
  files that `jira_download` writes come from the author of the ticket.
- **`jira mcp` judges the path that the command uses, and gives the command that path.** A blank or an invisible character at either end of a path (a
  space, a no-break space, U+3000, U+0085, a zero-width space) and a control or format character anywhere in it are refused (the message writes it out, as
  `\xa0` or `\u200b`), because a command may strip, split or normalise a text: `jira download` strips the blanks off `out`, so ` .git/hooks` would be one folder to the
  check and another to the command. A call that is allowed gives the command the approved path in full (resolved, absolute), never the text that the model
  wrote, so that a command cannot reach another place than the one that was checked.
- **`jira mcp` adds rows only to a map.** The `mapFile` of `jira_import_md_dir` must end in `.csv`, and a file that exists must be empty or start with the
  header of a map (`source_id,source_key,jira_key,issue_type,status,file`): a `README.md` gets no CSV rows. A file that does not exist is created, as in a
  shell.
- **`jira mcp` judges the place that a command uses when a call leaves a path out.** `jira_download` without `out` writes to `tmp/jira-attachments/KEY`, and
  `jira_import_md_dir` without `mapFile` adds rows to `DIRECTORY/jira-import-map.csv`: with a junction or a symbolic link at `tmp` that leads out of the
  working folder (a cloned repository can bring one), the files of the ticket's author would be written outside it, and with a map name that is a link to a
  README, a row would be appended to the README. Both places are worked out as the commands work them out, judged as a given path is, and handed to the
  command as `--out` and `--map-file`. A refusal says that the property "was left out" and names the place. `download` with `text: true` writes no file and
  is not judged. No other tool that `jira mcp` offers has a place of its own (all 66 were audited: `export` without `out` prints, `pull`, `diff` and `push`
  take their folder from the call, the cache and the guides are named by the environment).
- The new read commands keep people out: the lead of a component, the actors of a role, the watchers, the authors of worklogs, comments and
  attachments, the owner of a filter, and the people that its JQL names, show only with `--with-users` (refused under `JIRA_HIDE_USERS=true`),
  voters never, and the agile commands and `jira issue --history` name nobody. `jira users resolve` prints a count and a user name, never a list of
  people; `jira users search` needs `--with-users`.
  Under that policy a preview of `jira watch` (`--dry-run`) shows your own name as `[you]`, and the result of a call that was made does not say what it
  sent (`payload` and `query` are `null`, `redacted` is `true`). Texts are redacted (`[~user]`, `[email]`). `authorRef` is keyed with the token, so that
  nobody without it can compute the pseudonym of a user name (`jira users resolve` tells user names) or find the name behind one.
- **A saved filter does not print the people that its JQL names.** `jira filters` and `jira filter ID` show `[user]` in place of the literal values of
  `assignee`, `reporter`, `creator`, `watcher`, `voter`, `commentedBy`, `approver` and `"Request participants"` in a condition, of the user after `by`
  in a `was` or `changed` clause, and of the user in `updatedBy()`, `projectsLeadByUser()` and `componentsLeadByUser()`, with or without
  `JIRA_HIDE_USERS=true`; with `--with-users` (which the policy refuses) the JQL is as Jira sent it. `currentUser()`, `membersOf("group")`, `EMPTY` and
  `unassigned` stay, and so does what the CLI cannot recognise: a custom user field (`cf[10100] = jdoe`) and a name written in words. `searchUrl` holds
  the JQL once more, so it is `""` when the JQL had to be redacted.
- `jira branch` builds the name from the summary as `jira issue` shows it: a mention or an e-mail address in the summary is `user` or `email` in the
  branch name (it was `feature/PROJ-1-ask-jdoe-or-jane-doe-example-com-about`), also under `JIRA_HIDE_USERS=true`. A branch is pushed, and a name or an
  address in its name would go with it.
- `jira remote-link` takes only `http` and `https` addresses without credentials.
- Credentials inside `JIRA_BASE_URL` (`https://user:password@host/jira`) are left out of everything the CLI prints (`jira ping`, `jira server`, the
  links in results, error texts, also over `jira mcp`), and `jira doctor` warns about them. `jira config show` replaces the user name and password by
  `********` (`https://********@host/jira`; an address that holds an `@` after the host name is not shown at all), and `jira config set JIRA_BASE_URL`
  refuses to save an address with credentials (exit code 2); a settings file that was written by hand keeps whatever it holds. They
  sign in nothing: the token is the only credential. An address in which an `@` follows the host name (a password that holds `/`, `?` or `#`) is refused
  with an error that does not repeat it.
- Jira's explanation of a refusal (the error message, its hint and `details.jira`) is always cleaned of mentions, e-mail addresses and personal links,
  not only under `JIRA_HIDE_USERS=true`. The policy still replaces whole user objects. `jira api` under the policy (and the agent profile) also clears the
  user keys and display names in the changelog items of the user fields (`assignee`, `reporter`, `creator`, `watcher`, `voter`: `from`, `to`, `fromString`
  and `toString` become `null`) and the user names in the `jql` of a saved filter (its `searchUrl` is emptied whenever the `jql` needed any redaction: a user
  name, an e-mail address, a mention or a personal link, because the address holds the same text percent-encoded); a custom user-picker field is not recognised.
- Plain http without `JIRA_ALLOW_HTTP` is accepted only for `localhost` and loopback addresses (`127.0.0.1`, `::1`, ...). A name that only starts with
  `127.` (`127.example.com`) is a name that the network resolves, so it is not "on this computer" any more.
- **`jira download` sends the token to your Jira only.** The address of a file is built from its id and name below `JIRA_BASE_URL`, never taken from the
  `content` field that Jira (or a proxy in front of it) reports, and a redirect is followed only on the same scheme, host and port (three at most); a
  redirect anywhere else is an error, and nothing is sent there (its address is shown as text). A file name is made safe before it is used, and nothing is
  overwritten without `--force`, also not when a file appears while the download runs.
- **`jira download --text` reads a document as untrusted input.** A small file cannot take the time or the memory of the run: the command refuses, with a hint,
  an archive of more than 2,000 entries or in ZIP64 form (also when a comment of up to 64 KiB hides the record), a part that is encrypted or packed with
  anything but store or deflate (bzip2 and LZMA unpack a whole block at once), an XML part that declares a document type or an entity, is not UTF-8 or unpacks to
  more than 6 MiB (32 MiB for all the parts read, each part once), more than a million tags in all the parts together, names that no real document has (an
  `xmlns` address of more than 200 characters, a name of more than 128, an element with more than 256 attributes) and a document that expands to more than about
  6.3 million characters of text, whatever `--max-chars` asks for. Any other failure of the reader is an entry with a hint that names the kind of failure and
  never the content, and never stops the other files of the run. A refusal rests on a size or a count that is known before the work is done and comes within a
  fraction of a second; the slowest document that the limits let through takes about two seconds to read.
- **Text from an attachment cannot rewrite the terminal or carry a hidden message.** The text that `--text` prints, and the names in tables and messages, lose
  escape sequences, control characters and the characters that a reader cannot see: all the format characters (the controls that turn text around, zero width
  characters, the byte order mark and the tag characters U+E0000 to U+E007F that can spell a hidden instruction); lone surrogates become U+FFFD. The zero
  width joiner and non-joiner stay only where they belong to the text (emoji sequences, Persian and Hindi words). What an attachment says is data, never
  instructions.
- **`jira attach` and `jira download` cannot publish or replace the configuration of the CLI**, which holds the token and the address that the token is sent to.
  `attach` refuses (exit code 2, nothing sent, in a dry run too) the configuration file (`JIRA_CONFIG_FILE`, or `~/.config/jira-dc-cli/config`), found by its
  place so that a link or `..` does not help, and any file whose bytes contain the token, in UTF-8 or UTF-16: a copy of the configuration, a log with a
  header dump. `download` refuses (exit code 2, with `--force` and in a dry run too) to write into the folder that holds the configuration; the hint says to
  choose another `--out`. The message never shows the token.
- **`jira comment --edit` cannot make a restricted comment public or overwrite a mention.** It sends the restriction again, and it refuses a new text
  that holds `[~user]`, `[email]` or `[personal link]`, which stand in place of people in what the CLI shows; `update`, `update-json` and `transition --fields-json`
  refuse them in a new summary, description, acceptance criteria, label, component or fix version, and `push` and the imports refuse them in their own words.
  Other custom text fields, and a comment that is added, are not checked.

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
