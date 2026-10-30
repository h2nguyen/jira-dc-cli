# Compatibility with Jira Data Center

jira-dc-cli is written for **Jira Data Center 11.3.x**: the REST API 2 (`/rest/api/2`) and the Jira Software API (`/rest/agile/1.0`) as the
[Jira Data Center 11.3 REST API reference](https://developer.atlassian.com/server/jira/platform/rest/v11003/intro/) describes them. This page says
what that means, how it is checked, what the CLI says about a server that is not on that release line, which parts of the API the commands
cover and which calls the newer commands make.

## How the CLI is checked against the reference

- **Every call is in the reference.** The OpenAPI document of the reference for Jira Software Data Center 11.3.12 lists the calls (method and
  path) of the API. `tests/support/jira_dc_11_3_endpoints.txt` holds that list. The mock Jira of the test suite answers HTTP 404 to a call that is
  not in it, and the tests of every command assert that no such call was made. A command therefore cannot come to depend on an endpoint of another
  version, of Jira Cloud (such as `/issue/{key}/changelog`, which Data Center does not have: the history is `expand=changelog`), or on an internal
  one (`rest/greenhopper/*`). One address is no REST call and so not in the list: the content of an attachment, `GET /secure/attachment/{id}/{name}`, which
  `jira download` reads. The mock serves it, and the tests let it pass for a GET only.
- **What this proves, and what it does not.** It proves that the CLI sends only calls that the 11.3 reference knows, with the methods the reference
  lists. It does not prove that a real 11.3 server answers as the mock does: the answers of the mock follow the reference, and the tests of the
  commands that write ran against the mock only. Use `--dry-run` first, and a test project before a real one.
- The settings that Jira Data Center lets an administrator change (attachments off, a custom workflow, other field names, a permission scheme)
  are met at run time: `jira doctor` checks the custom fields, `jira server` shows the settings that matter, `jira permissions` says what you may do,
  and Jira's own refusal is shown with its message.

## What `jira doctor` says about your server

`jira doctor` reads the version and the deployment type of the server (`GET /rest/api/2/serverInfo`) and compares them with the release line.

| Your server | The `server` check | Meaning |
| --- | --- | --- |
| 11.3.x | `ok` | The release line the CLI is written and tested for. |
| another 11.x | `ok` | The REST API 2 is stable inside a major release; only the newest features may differ. |
| 9.x or 10.x | `warn` | Older than 11.3 and not tested: most commands are expected to work, some newer endpoints or fields may be missing. A command that fails with 404 may need a newer Jira. |
| older than 9.0 | `warn` | Much older: the create metadata of a project is read from endpoints that exist only from 9.0. |
| newer than 11.x | `warn` | Newer than the release line the CLI was tested with; the REST API 2 usually stays compatible. Report what does not work. |
| Jira Cloud | `warn` | Not supported: Jira Cloud has other authentication and other endpoints. |

A `warn` never stops a command. `jira version --json` names the release line (`jiraDataCenter`); `jira server` shows the version, the deployment
type and the same verdict.

## What the commands cover, and what stays with `jira api`

The CLI offers named, safe commands for what people and agents do with issues every day. The administration of Jira stays out on purpose: a
command that changes how Jira is configured does not belong into an agent's reach, and the `api` command offers it to a person who knows what
they do (GET is free, POST and PUT need `--write`, DELETE does not exist).

| Group of the reference | Commands |
| --- | --- |
| issue (read, create, edit, transitions) | `issue`, `issues`, `search`, `create`, `create-json`, `update`, `update-json`, `transition`, `branch`, `export`, `import-md`, `import-md-dir`, `import-csv`, `diff`, `pull`, `push` |
| issue: comments, issue links | `comment` (add, list, `--get`, `--edit`), `link`, `link-types`, `push` (creates the links of a file) |
| issue: attachments | `attachments`, `download`, `attach` |
| issue: assignee, watchers, votes, worklogs, remote links | `assign`, `watchers`, `watch`, `votes`, `vote`, `worklogs`, `log-work`, `remote-links`, `remote-link` |
| search, filter, field, jql | `issues --jql`, `search`, `report`, `filters`, `filter`, `fields` |
| board, sprint, epic, backlog, rank (Jira Software) | `boards`, `sprints`, `sprint`, `velocity`, `backlog`, `epics`, `sprint-add`, `sprint-create`, `sprint-update`, `epic-add`, `rank` |
| project, version, component, role, status, priority, resolution, issuetype | `projects`, `project`, `projects-admin`, `metadata`, `components`, `fix-versions`, `roles`, `statuses`, `priorities`, `resolutions`, `issue-types` |
| user, mypermissions, serverInfo, configuration, dashboard | `users`, `permissions`, `server`, `dashboards`, `ping`, `doctor` |

Not covered by a command, on purpose (administrator functions, or no use for a person at a terminal): the cluster, index and re-index, monitoring and
the license; application properties and application roles; the permission, notification, workflow, screen, issue type and priority schemes; the
creation, archiving and deletion of projects, versions, components and issue types; users, groups and passwords; avatars; the terminology and
e-mail templates. Nothing that deletes content is offered: not comments, worklogs, attachments, links, issues, sprints nor versions. The only commands that send a
`DELETE` are `watch --remove` and `vote --remove`, which undo your own watch or your own vote (`watch --remove` for another person is refused before anything is sent).

The other commands are not tied to a group of the reference: `use`, `current`, `clear-project`, `config`, `setup-pat`, `remove-pat`, `help`, `commands`, `version`,
`changelog`, `how-to`, `skill` and `wiki` work on your computer, `mcp` runs the commands above as tools for an MCP client, and `api` reaches any endpoint.

## Which REST calls each command makes

The calls that the commands added after 1.0.0 make, and the new calls of `issue`, `comment`, `transition`, `diff`, `pull`, `push` and of the dry runs (paths below `/rest`; each one is in
`tests/support/jira_dc_11_3_endpoints.txt`, and the tests assert that no other call is made, except for the one address named above). A command that writes reads its targets first and reads
the result back, and `--dry-run` makes the reads and none of the writes.

| Command | Calls |
| --- | --- |
| `issue` | `GET /api/2/issue/{key}`; several keys: `GET /api/2/search` with `key in (...)` and `fields=*all`, a hundred keys a call (Jira answers HTTP 400 for a query with an unknown key: then `GET /api/2/issue/{key}` for each key). `--history` adds `expand=changelog` and `--rendered` adds `expand=renderedFields` to that request |
| `filters`, `filter` | `GET /api/2/filter/favourite`, `GET /api/2/filter/{id}` |
| `issues --filter`, `search --filter` | `GET /api/2/search` with `filter = ID` in the JQL |
| `diff`, `pull` | `GET /api/2/search`: the links of an issue (`issuelinks`) come with the issue |
| `push` (links) | `GET /api/2/issueLinkType`, `GET /api/2/issue/{key}` (the other end of each link), `POST /api/2/issueLink` |
| `boards` | `GET /agile/1.0/board` (paged), `GET /agile/1.0/board/{boardId}/project` for each board |
| `sprints` | the board, then `GET /agile/1.0/board/{boardId}/sprint` (paged; `state=` as asked) |
| `sprint` | by id: `GET /agile/1.0/sprint/{sprintId}`; by name: the board, then `GET .../board/{boardId}/sprint` (paged; no other call for the sprint). `--issues`: `GET /api/2/field`, `GET /agile/1.0/sprint/{sprintId}/issue` (paged). `--review`: `GET /api/2/status`, `GET /api/2/field`, then `GET /api/2/search` with `sprint = ID` and with `sprint was ID AND sprint != ID` (both `expand=changelog`, paged) |
| `velocity` | the board, `GET .../board/{boardId}/sprint` (`state=closed`), `GET /api/2/status` once, `GET /api/2/field` once, and for each closed sprint (at most `--last`) the two searches of `sprint --review` |
| `backlog` | the board, `GET /api/2/field`, `GET /agile/1.0/board/{boardId}/backlog` (paged) |
| `epics` | the board, `GET /agile/1.0/board/{boardId}/epic` (`done=false` unless `--done`) |
| `sprint-add` | the sprint as for `sprint` (`GET /agile/1.0/sprint/{sprintId}` for an id, the board and its sprints for a name), `GET /api/2/field`, `GET /api/2/issue/{key}` per issue; then, unless `--dry-run` or every issue is there already: `POST /agile/1.0/sprint/{sprintId}/issue` (at most 50 issues a call) and `GET /agile/1.0/sprint/{sprintId}/issue` (read back, `jql=key in (...)`, 50 keys a call) |
| `rank` | `GET /api/2/field`, `GET /api/2/issue/{key}` per issue and for the anchor; then, unless `--dry-run`: `PUT /agile/1.0/issue/rank` (a `207` answer with an entry that failed is an error) and `GET /api/2/search` (`key in (...) ORDER BY Rank ASC`, read back) |
| `epic-add` | `GET /agile/1.0/epic/{epicIdOrKey}` (not for `--epic none`), `GET /api/2/field`, `GET /api/2/issue/{key}` per issue; then, unless `--dry-run` or every issue is there already: `POST /agile/1.0/epic/{epicIdOrKey}/issue` or `.../epic/none/issue` (at most 50 a call) and `GET .../epic/{epicIdOrKey}/issue` or `.../epic/none/issue` (read back) |
| `sprint-create` | `GET /agile/1.0/board/{boardId}`, `GET .../board/{boardId}/sprint` (`state=future,active`: the check for a duplicate name); then, unless `--dry-run`: `POST /agile/1.0/sprint` and `GET /agile/1.0/sprint/{sprintId}` (read back) |
| `sprint-update` | the sprint as for `sprint-add`; then, unless `--dry-run`: `POST /agile/1.0/sprint/{sprintId}` (a partial update) and `GET /agile/1.0/sprint/{sprintId}` (read back) |
| `priorities`, `resolutions` | `GET /api/2/priority`, `GET /api/2/resolution` |
| `issue-types`, `statuses` | `GET /api/2/issuetype` and `GET /api/2/status`; with `--project`: `GET /api/2/project/{key}?expand=issueTypes` and `GET /api/2/project/{key}/statuses` |
| `components`, `fix-versions`, `roles` | `GET /api/2/project/{key}/components`, `.../versions` (`--counts`, for each of the first 25 versions: `GET /api/2/version/{id}/relatedIssueCounts` and `.../unresolvedIssueCount`), `.../role` (`--with-users`: `GET /api/2/project/{key}/role/{id}` for each role) |
| `permissions` | `GET /api/2/mypermissions` (with `projectKey` or `issueKey`) |
| `dashboards` | `GET /api/2/dashboard` (`filter=my` or `filter=favourite`; at most 100 a call) |
| `server` | `GET /api/2/serverInfo`, `GET /api/2/configuration`, `GET /api/2/attachment/meta` (when it fails, a warning says so) |
| `watchers`, `votes`, `worklogs`, `remote-links` | `GET /api/2/issue/{key}/watchers`, `.../votes`, `.../worklog`, `.../remotelink` |
| `watch` | `GET /api/2/myself` (with `--remove` and a person who is not you it stops here: a usage error); a dry run then `GET .../watchers`; otherwise `POST .../watchers` or, with `--remove`, `DELETE .../watchers?username=`, then `GET .../watchers` (read back; when it fails the result says so, with exit code 0) |
| `vote` | `GET .../votes`; then, unless the vote is as asked already or it is a dry run, `POST .../votes` or, with `--remove`, `DELETE .../votes`, and `GET .../votes` (read back) |
| `log-work` | `GET /api/2/issue/{key}` (dry run and `--if-unchanged-since`), `POST .../worklog` (query `adjustEstimate`, `newEstimate`, `reduceBy`), `GET .../worklog` (read back) |
| `remote-link` | `GET /api/2/issue/{key}` (dry run and `--if-unchanged-since`), `GET .../remotelink`, `POST .../remotelink`, `GET .../remotelink` (read back) |
| `attachments` | `GET /api/2/issue/{key}?fields=attachment` |
| `download` | `GET /api/2/issue/{key}?fields=attachment`, then for each file `GET /secure/attachment/{id}/{name}` below `JIRA_BASE_URL` (no REST call; a redirect is followed only on the same scheme, host and port) |
| `attach` | `GET /api/2/issue/{key}?fields=attachment` (the issue and its file names), `GET /api/2/attachment/meta`, `POST /api/2/issue/{key}/attachments` (one multipart part per file, header `X-Atlassian-Token: no-check`; not in a dry run); with `--comment` `POST /api/2/issue/{key}/comment` |
| `comment` | adding: `POST .../comment` (a dry run: `GET /api/2/issue/{key}`); `--list`: `GET .../comment` (100 a call; `expand=renderedBody` with `--rendered`) and `GET /api/2/myself`; `--get`: `GET .../comment/{id}` and `GET /api/2/myself`; `--edit`: `GET .../comment/{id}`, then `GET /api/2/myself` in a dry run or `PUT .../comment/{id}` in a real run (read again just before the `PUT` with `--if-unchanged-since`) |
| `users` | `GET /api/2/user/search` (`includeInactive=true`); with `--project` or `--issue`: `GET /api/2/user/assignable/search`; for `me`: `GET /api/2/myself` |
| `assign` | `GET /api/2/issue/{key}` (dry run and `--if-unchanged-since`), the user as `users` finds it with `--issue`, `PUT /api/2/issue/{key}/assignee`, `GET /api/2/issue/{key}` (read back) |
| `transition` | `GET /api/2/issue/{key}` (when `--fields-json` holds `[~user]`, `[email]` or `[personal link]` the same read also asks for the fields that are compared: no extra request), `GET .../transitions` (with `?expand=transitions.fields` for `--show-fields` and in a dry run with `--to`), `POST .../transitions` |
| `update`, `update-json` (a new text, label, component or fix version that holds `[~user]`, `[email]` or `[personal link]`) | `GET /api/2/issue/{key}` once, the read that `--if-unchanged-since` uses too, to compare with the current text or list; then the update as before |
| the dry run of `create`, `create-json`, `update`, `update-json` and the imports | `GET /api/2/issue/createmeta/{project}/issuetypes/{id}` (read page by page with `startAt` and `maxResults`, at most 20 requests) and `GET /api/2/issue/{key}/editmeta`, once per project and issue type and per issue in a run; for `--assignee` `GET /api/2/user/assignable/search`; for an issue type that the cached list does not hold, `GET /api/2/issue/createmeta/{project}/issuetypes` once more, bypassing the cache |

In the rows of the agile commands `(paged)` means one call or more (a list is read page by page, `maxResults=50`), `GET /api/2/field` is the list of the field ids (read once, then kept in the cache), and "the board" is `GET /agile/1.0/board` with `projectKeyOrId` (and `type=scrum` for the commands that need sprints) for a project, or `GET /agile/1.0/board/{boardId}` for `--board`.

The read-back after a write of `watch`, `vote`, `log-work` and `remote-link` is not part of the write: when it fails (HTTP 503, a timeout, a refusal), the result says that the write was made (`confirmed: null`, `message`; for `vote` `count: null`, `hasVoted: null`) and the command exits with 0. `sprint-add`, `epic-add`, `rank`, `sprint-create` and `sprint-update` do the same with `readBack: false` and a note; the fields that the read-back would have filled (`confirmed` and `missing`, `order` and `confirmed` of a rank) are then `null`, and they are `null` too for a dry run and when there was nothing to send.

What the reference does not say, and what was therefore not seen on a real 11.3 server: which changelog entries `expand=changelog` returns (the status history and the sprint
review), whether the Sprint field accepts the history operator (`sprint was ID`; the review falls back and says so), how a very busy issue holds its comments in a search
answer, the HTML that a customised renderer writes for a mention, the words of Jira's refusals, the shape of the `207` answer of a rank, the permission names in the hints,
whether an edit of a comment that leaves out its restriction removes it (the CLI sends it again), how Jira's user search matches names (the CLI compares the answer itself),
which fields the create and edit metadata lists, and how a real server serves an attachment (redirects, a missing `Content-Length`).

## When something differs on your server

Run `jira doctor --json` and `jira server --json`, and `jira api GET ...` for the call that failed with `-v` (which logs the requests, never the
token). Redact names, hosts and issue keys before you paste anything into an issue (see [CONTRIBUTING.md](../../CONTRIBUTING.md)).
