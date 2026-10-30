# Troubleshooting

Start with `jira doctor`. It checks the configuration, the token, TLS, the connection, the server version, the
custom fields and the current project, and prints a hint for everything that is wrong. `jira doctor --json` gives
the same for a program. Add `-v` to any command to log each request (method, path, status, time) to standard error;
the token is never logged.

## Symptoms

| You see | Why | What to do |
| --- | --- | --- |
| `JIRA_BASE_URL is not set` / `JIRA_PAT is not set` | The settings were not found. | `jira config path` shows which file is read (the same in every shell, see [configure](configure.md)); then `jira config set JIRA_BASE_URL ...` and `jira setup-pat`. |
| `Jira did not accept the token (HTTP 401)`, exit code 5 | The token is wrong, expired or revoked. | Create a new personal access token in your Jira profile and run `jira setup-pat`. |
| `Jira refused this request (HTTP 403)`, exit code 5 | The account lacks the permission. | Ask a Jira administrator; no setting in the CLI changes it. |
| `JIRA_BASE_URL uses plain http` | The CLI will not send the token unencrypted (`localhost` and loopback addresses such as `127.0.0.1` are exempt; a name that only starts with `127.` is not). | Use the https address. Only on a network you trust: `jira config set JIRA_ALLOW_HTTP true`. |
| `JIRA_PAT is not a token` (from `jira setup-pat`: `That is not a token`; exit code 1) | The token holds a blank, a line break or a character that is not ASCII, which no personal access token has. The text was not saved and not printed. | Paste only the token, as Jira showed it (one line), with `jira setup-pat`. |
| `JIRA_BASE_URL has an @ after the host name` (exit code 1) | The address looks like a user name and password in it. They sign in nothing, and an address in which the CLI cannot tell them from the path is refused (the message does not repeat it, and `jira config show` prints `(not shown: ...)` for it). | Credentials do not belong in the address: write `https://host/context` and use the token (`jira setup-pat`). |
| `JIRA_BASE_URL holds a user name or password ... it was not saved` (`jira config set`, exit code 2) | The address has `user:password@` in it. They sign in nothing, so `jira config set` does not save it. A settings file that was written by hand keeps whatever it holds: `jira config show` prints `********` in place of the credentials, and `jira doctor` warns. | Write the address as `https://host/context` and save the token with `jira setup-pat`. |
| `Jira is limiting the rate of requests (HTTP 429)`, exit code 7 | Jira throttles you. The CLI already waited as long as Jira asked (at most 30 s) and tried again. | Wait a moment; use fewer requests (`--limit` instead of `--all`). |
| `Jira sent an answer that could not be read` | A proxy damaged the response. | Try again; ask the network team if it stays. |
| `JIRA_MAX_TIME must be a number of seconds greater than 0 and at most 86400, not '...'` (also `JIRA_CONNECT_TIMEOUT`, or another setting) | A value in the environment or the config file is wrong; a timeout is at most 86,400 seconds, a day (`jira projects-admin --timeout` takes 1 to 86,400). | `jira config show` finds it; `jira config set` or `jira config unset` fixes it. |
| `Cannot reach Jira (...)`, exit code 7 | Network, VPN or proxy; or the address is wrong. | Check `JIRA_BASE_URL`, the VPN, and `jira doctor`. |
| A certificate error | Jira uses a company certificate authority that Python does not trust. | `jira config set JIRA_CA_BUNDLE "C:/certs/company-ca.pem"` (a PEM file with the CA certificates). `JIRA_INSECURE=true` is only a short diagnostic. Python 3.13 and newer also check the certificate itself more strictly: a CA certificate without an Authority Key Identifier is refused with `Missing Authority Key Identifier`, also from a bundle. Ask the administrators for a corrected CA certificate, or run the CLI with Python 3.12 or older. |
| `Jira answered with a redirect (HTTP 302)` | `JIRA_BASE_URL` has the wrong context path or uses `http` where Jira wants `https`. | Open Jira in the browser and copy the address up to the context path (`https://host/jira`). |
| `Not found: GET /issue/...`, exit code 4 | The key does not exist or is not visible to the token's user. | Check the key and the project. |
| `Jira refused the request (HTTP 400): ...`, exit code 6 | A field is required, not allowed on the issue type, or has a value Jira does not know. | Read the explanation; `jira metadata PROJECT ISSUE_TYPE_ID` lists the fields and which are required. See below. |
| `Issue type 'X' was not found` | The type is not creatable in this project. | The hint lists the available names and ids; `jira metadata PROJECT`. |
| `Fix Version 'X' does not exist in project` | Jira refuses unknown versions. | Create it in Project settings, Versions, or import with `--skip-invalid-fix-version`. |
| `priority 'Urgent' is not available in PROJ for Story (10200).` (also a component or a fix version; a `--dry-run`, exit code 6) | The project does not offer that value. Nothing was sent. | The hint lists the allowed values (at most 30); use one of them. [Dry runs that check values](comments-and-people.md#5-dry-runs-that-check-values). |
| `resolution cannot be set for the transition 'Finish' (81) of PROJ-2: it is not on the screen.` (`transition --dry-run`, exit code 6) | The screen of that transition does not hold the field, or does not allow the value. | `jira transition KEY --to STATUS --show-fields` lists the fields of the screen with their values. |
| `'Jane' matches 2 users, so it does not say which one is meant.` (`--assignee`, `assign`; exit code 1) | The name fits several users. The CLI counts them and never lists people. | `jira users resolve Jane --issue KEY` says how many fit; give the exact user name. |
| `No user who can be assigned for PROJ-1 matches 'Nobody'.` (`--assignee`, `assign`; exit code 4) | The name fits nobody who can be assigned: a misspelling, an inactive user, or a user who may not be assigned issues there. | Check the spelling with `jira users resolve NAME --issue KEY`. |
| `No transition to 'X' is available from the current status` | The workflow does not allow that move from here. | `jira transition KEY` lists the possible moves. |
| `'X' is ambiguous` (transition) | Two transitions lead to the same status. | Use the transition id from the hint. |
| `changed in Jira at ..., after ...`, exit code 3 | Someone changed the issue (with `comment --edit`: the comment) after the time you gave to `--if-unchanged-since`. | Read the issue (or the comment: `jira comment KEY --get ID`) again and decide; this check stops a write based on a stale read. |
| `... changed in Jira after the last sync and was edited here too` (`pull`) or `... was edited too (the same summary): nothing was sent` (`push`), exit code 3 | The same field changed in the file and in Jira since the last agreement. Nothing was overwritten or sent. | `jira diff DIR --issue KEY` shows the field. Then `jira push FILE --force` (the file wins), `jira pull DIR --update --force --issue KEY` (Jira wins), or edit the file to the text you want and push that. |
| `... differ in ... and were never synced` (`push`), exit code 3 | The file never went through `pull` or `push`, so nobody can say which side is newer. | `jira pull DIR` notes the files that are equal; with `--update --force` Jira's value wins, with `jira push FILE --force` the file's. |
| `... holds a placeholder ([~user], [personal link] or [email])` (`push`) | The file shows where Jira holds a person's data, and sending the placeholder would overwrite it. | Leave that field out of your edit, or write the text again without the placeholder. `--force` does not help on purpose. |
| `The description of KEY holds a table (or a panel, a colour, a macro, text that changes on the way back), which Markdown cannot carry unchanged` (`push`) | Sending the file's text would change more than you edited (the file's `lossy:` line says what). | Change that description in Jira, or `--force` to send the file's text anyway. |
| `... holds a placeholder ([~user], [personal link] or [email])` (`import-md`, `import-csv`) | The file or row was written by `jira pull`, which redacts people; updating from it would overwrite the real text. | Use `jira push FILE` instead: it leaves such a field alone. `jira update` changes a field on purpose, and refuses a text that holds a placeholder. |
| `The new text holds [~user], which stands in place of a mention or an address that the CLI hides ...` (`comment --edit`; `The new description holds ...` for `update`, `update-json` and the `--fields-json` of `transition`, also for the summary, the acceptance criteria, `The new labels hold ...`, the components and the fix versions; exit code 1) | The text or the names came from a reading that hides people (`jira issue`); sending the stand-in would overwrite the real mention. Nothing was sent, in a dry run too. | Write the real mention (`[~username]`) or leave that part out; a text that holds people is best edited in Jira itself. If the stand-in really is what you want, the hint names `jira api PUT issue/KEY --data-file FILE --write` (`jira api POST issue/KEY/transitions ...` for a transition; no guard). [Dry runs that check values](comments-and-people.md#5-dry-runs-that-check-values). |
| `The description is empty: FILE holds no text` (`update --description-file`, exit code 2) | The file, or the standard input, was empty: an editor that saved nothing, a pipe that failed. An empty text would clear the description. | Check the file or the pipe. `--description ''` clears the description on purpose. |
| `--remove takes only your own watch off PROJ-1, and 'bwong' is not you: nothing was sent.` (`watch --user NAME --remove`, exit code 2) | `--remove` undoes your own watch only; the CLI does not take another person off an issue. | Remove the person in Jira (the permission Manage Watchers). `--user me` and your own login name are you. |
| `confirmed` is `null` and `message` says that the entry **was** made (`log-work`, `remote-link`, `watch`; for `vote` it is `count` and `hasVoted` that are `null`; exit code 0) | Jira took the write, and reading the result back failed (a server error, a timeout). `readBack` is `false` in the result of `sprint-add`, `epic-add`, `rank`, `sprint-create` or `sprint-update`: the write was made and nothing was checked, see [Sprints, boards and the sprint review](sprints-and-boards.md). | Look with `jira worklogs KEY` (or `remote-links`, `watchers`, `votes`). Do not run the write again: a second `log-work` is a second entry. [Collaborate on an issue](collaborate-and-reference.md). |
| `The story points in fields_json are not a number` (`diff`, `pull`, `push`) | The entry is a word, a list or `true`; reading it as "no points" would let a push clear the field. | Write a number (`5` or `"5"`), or take the entry out. |
| `The links line of the file cannot be read: '...'` (`diff`, `pull`: the file is `unreadable`; `push`: exit code 1) | An entry of the `links:` line is not `Type -> KEY` or `Type <- KEY` (a `>` alone, a long arrow, no key, a link of the issue to itself). Nothing is guessed, because a push creates links. | Write each link as `Type -> KEY` (this issue blocks KEY) or `Type <- KEY` (KEY blocks this issue), separated by commas. `jira link-types` lists the type names. [Links between issues](export-and-sync.md#links-between-issues). |
| `... PROJ-77 does not exist in Jira, or you may not see it` (`push`, exit code 4) | The other key of a link is wrong, or the account cannot browse that issue. Nothing was sent. | Check the key in the `links:` line. |
| `... jira has it; remove it in Jira` (`push`) | You removed a link from the `links:` line, and Jira still holds it. The CLI deletes no link, and the file stays `edited here` until the two agree. | Remove the link in Jira's web interface, or put the entry back into the line. |
| `PROJ-2 blocks PROJ-4: Jira refused this request (HTTP 403)` (or `did not accept the token (HTTP 401)`) with a hint about the Link Issues permission (`push`, exit code 5) | Jira refused to create a link; it answers 401 or 403 when that permission is missing, and the token was accepted for the reads before. | Ask a Jira administrator for the Link Issues permission in the project of the issue. |
| `... already exists in tmp/jira-attachments/PROJ-1.` (`download`, exit code 1) | A file with that name is in the folder. Nothing was written. | `--force` overwrites, `--out` chooses another folder, `--dry-run` shows what would be written. [Attachments](attachments.md). |
| `... larger than the upload limit of ... that Jira reports.` (`attach`, exit code 1) | Jira refuses larger files. Nothing was sent. | Shrink or split the file; `jira server` shows the limit. |
| `Jira redirected the download to another address (...)` (`download`, exit code 1) | Jira, or a proxy in front of it, sent the download elsewhere. The token goes to `JIRA_BASE_URL` only: nothing was sent there. | Use the address that serves the files as `JIRA_BASE_URL`, or download the file in the web interface. |
| `... is a folder in tmp/jira-attachments/PROJ-1, where a file would be written.` (`download`, exit code 1) | A folder has the name that the file would get. Nothing was written, in a dry run too. | Remove or rename the folder, or choose another `--out`; `--force` does not replace a folder. |
| `... appeared in ... while the download ran. It was left as it is.` (`download`, exit code 1) | Another program, or a second run, made the file after the CLI had checked. | `--force` overwrites it, `--out` chooses another folder. |
| `... of the 3 files were written to ... before that` (`download --all`; the exit code of the failure, `details.written` lists the files) | A file failed after others were saved. They stay. | A new run stops at the files that are there: add `--force`, or choose the missing ones by ID or `--name`. |
| `... would be written to ..., where the CLI keeps its own configuration (with the token)` (`download`, exit code 2) | `--out` is the folder that holds the settings file. Nothing was written, with `--force` or without. | Choose another folder with `--out`. |
| `... is the configuration file of this CLI` or `... holds the access token of this CLI` (`attach`, exit code 2) | The file is the settings file, or it contains the token (a copy of the settings, a log with a header dump). Nothing was sent. | Attach another file. If the token was sent anywhere, replace it with `jira setup-pat`. |
| `Cannot attach: ... does not exist` (`attach`, exit code 1) | The path is wrong, or a name with blanks lost its quotes; the error names each path as the CLI took it. | Put a path with blanks in quotes (`'my report.pdf'`) or give it with `--path 'my report.pdf'`. |
| `The document expands to more text than can be read`, `... holds more than 1,000,000 tags`, `... is packed with bzip2` or `... too large for a text read` (`download --text`; an entry with a `hint`, exit code 0) | A document that makes more than about 6.3 million characters of text or has more than a million tags in all its parts, a ZIP64 archive, an XML part over 6 MiB, a part packed with bzip2 or LZMA (an Office file never does that), a part that is used twice, or names that no real document has. The other files are still read. | Save the file without `--text` and open it with your own tool. |
| `--jql has a bracket or a quote that does not close` (`search`) | An unbalanced condition could end the brackets that keep the other filters in force. | Balance the brackets and quotes. |
| `A folder does not say which issue to change` | `jira push` takes a file, or a folder together with `--issue`. | `jira push DIR --issue KEY` or name the file. |
| `Nothing found in project X` (`jira search`) | No issue holds the words, or the issues are in another language: the search does not translate. | Read the `notes` and `ignored` in the answer; try the words the issues use, fewer words, a wildcard (`rol*`), `--match any` or `--all-projects`. [Find information](find-information.md#7-when-nothing-is-found). |
| `No project selected` (`jira search`) | The search is limited to one project and none is set. | `jira use KEY`, `--project KEY`, or `--all-projects`. |
| `jira issues --jql ...` shows keys of other projects, with a note on stderr | A `--jql` without `project = ...` replaces the project condition and searches every project. | Add `project = KEY AND (...)` to the query, or use `jira search --jql`, which adds your condition to the project. |
| `Link type 'X' not found; use the exact name` (`link`; `push` for a `links:` line, exit code 1) | The name is not a link type of this Jira: a phrase such as "requires" or `is blocked by` belongs to several link types or to none. Nothing was sent. | The hint lists the names; `jira link-types` shows them with their phrases. |
| `JIRA_HIDE_USERS=true: people are not printed` | The privacy policy is on. | Unset `JIRA_HIDE_USERS` if you really need to see people. |
| `JIRA_X uses shell syntax that the CLI does not execute` | The config file holds `$(...)`, backticks or `$VAR` outside single quotes. | Write the value itself, or put it in single quotes. |
| No such field `Story Points` (story points are 0 in a report) | The field has another name in your Jira. | `jira fields --search points`, then `jira config set JIRA_STORY_POINTS_FIELD customfield_NNNNN`. |
| `jira` is not found in PowerShell or cmd | `~/bin` is not on the Windows `PATH`. | Run the program directly, which needs no `PATH`: `python "$env:LOCALAPPDATA\jira-dc-cli\jira.py" version` (PowerShell). Or put the launchers where every shell looks, or add `~/bin` to the user `PATH` ([install](install-and-update.md#use-it-from-powershell-and-cmd)). `jira doctor` has a `launcher` check that says whether `jira` is found by name, and the installer prints the exact `--bin-dir` command for the folder that holds the launcher of `conflu`. Restart VS Code and the terminals afterwards. |
| `JIRA_BASE_URL is not set` in one shell, but `jira` works in another | The shells read different configuration files (`HOME`, `XDG_CONFIG_HOME` or `JIRA_CONFIG_FILE` differ between them). | `jira config path` shows the file each shell reads; set `JIRA_CONFIG_FILE` to one file for all of them. |
| `--fields-json is not valid JSON` for JSON that looks right, in Windows PowerShell 5.1 | PowerShell 5.1 removes the double quotes inside an argument: `'{"a":1}'` arrives as `{a:1}`. | Put the JSON in a file and pass it in single quotes: `--fields-json '@fields.json'` (a bare `@fields.json` is a PowerShell parse error: `@name` is splatting). For `api` use `--data-file`; or use `jira update-json KEY FILE`. Writing each quote as `\"` works too. |
| `unrecognized arguments: Link = Y ORDER BY key` for `--jql 'project = X AND "Epic Link" = Y'`, in Windows PowerShell 5.1 | The same: the quotes are removed and the query falls into two arguments (the hint of the error says so). | Put the query in a file and pass it in single quotes: `jira issues --jql '@query.jql'`, or on standard input: `--jql -`. For `search`, the question can be a file too (`jira search '@question.txt'`), and `--phrase 'exact words'` gives a phrase without quotes inside the text. A file written by PowerShell (UTF-8 with or without a byte order mark, UTF-16 or "ANSI") is read correctly. A query file may hold at most 1 MiB (`a file with a JQL may hold at most 1,048,576 bytes`, exit code 2). |
| `Cannot read jdoe: No such file or directory` (`jira search '@jdoe'`, also `--jql '@jdoe'`; exit code 1) | A value that starts with `@` is read from the file of that name. The words after the colon are the system's own, in its language. | The hint says it: a text that starts with `@` needs a space in front of it (`jira search ' @jdoe'`), or use `--phrase`. |
| A query string in `jira api` is cut off at an `&` (HTTP 500 `sprintId is null`) | `cmd.exe` and PowerShell treat `&` specially. | Use `--param KEY=VALUE` (repeat it) instead of writing `&` in the path. |
| Every command waits a while at the start (PowerShell, cmd), then works | The configuration is looked for on the home drive (`H:`) first, and that drive does not answer (VPN off, share asleep). | Reconnect the drive, or point the program at local files: `JIRA_CONFIG_FILE` and `XDG_CACHE_HOME` set to a local file and folder, so that the home drive is not touched. |
| `jira` runs an old version | Another `jira` comes first on the `PATH`, or an old launcher is left. | `where jira` (cmd) or `which -a jira` (Git Bash); re-run `python install.py`. |
| `jira api GET /issue/X` fails in Git Bash with a `C:/Program Files/Git/...` path | Git Bash rewrites an argument that starts with `/` before the program sees it. | The CLI undoes it for `api`; write the path without the leading `/` (`issue/X`), or run with `MSYS_NO_PATHCONV=1`. The same rewrite touches other values that start with `/`, such as `--text "/x"`. |
| Garbled characters (`Ã¼`) in a file you wrote | Your tool decoded UTF-8 as another encoding. | The CLI always writes UTF-8 with plain `\n`; read it as UTF-8. |
| Every command takes more than a second, even `jira version` | The program is installed on a network drive: Python reads about a hundred files at each start (1.4 s there, 0.4 s on a local disk). | `python install.py` puts it in `%LOCALAPPDATA%` by default. If you chose `--prefix` or `JIRA_CLI_INSTALL_DIR`, pick a local folder; the launcher shows where the program is (`type %USERPROFILE%\bin\jira.cmd` or the file in your `~/bin`). |
| `ERROR: unexpected ...` | A bug. | Run again with `--verbose` for the traceback and report it with the command and the output. |

## Custom fields are rejected

Custom field ids differ per Jira and per project. Find them with `jira fields --search NAME`, or ask which fields an
issue type accepts: `jira metadata PROJECT ISSUE_TYPE_ID`. Pass them as `--fields-json '{"customfield_12345": "x"}'`
(or `fields_json` in a Markdown or CSV import). A field that is on no screen of the project is refused by Jira
whatever the CLI sends.

## An assignee is rejected

`--assignee` (`create`, `update`, the imports) and `jira assign` take a user name, a display name or an e-mail address that fits exactly one user among the users who can be
assigned the issue (for a new issue: issues of the project), and `me`, `unassigned` or `automatic`. A name that fits nobody is exit code 4, one that fits several users is exit
code 1 with their number and no list of people. `jira users resolve NAME --issue KEY` says how many users a name fits and which user name to give; inactive users and users who
may not be assigned issues there are not found. See [comments, people and checked values](comments-and-people.md#4-people-find-the-user-name-and-assign).

## Markdown looks wrong in Jira

Jira's description field uses wiki markup, not Markdown. Convert with `--wiki` (`create`, `update`, `comment`,
`transition`, `import-md`, `import-md-dir`), or look at the result first: `jira wiki file.md`. `jira wiki file.md --check` also
warns, on standard error, about what Jira is known not to render as the Markdown meant it: tables, task lists, HTML, reference-style
links, footnotes, an underlined heading and a brace outside code (it can start a Jira macro). Code spans (`` `x` ``) become `{{x}}`
with any blank moved outside the braces, because Jira does not read `{{ x }}` as monospace text.

## A rejected value

A write that Jira refuses (`REJECTED`, exit code 6) names the field and says where to look: `jira metadata PROJECT ISSUE_TYPE_ID` lists
the fields of an issue type and the values they allow. `--dry-run` of `create`, `update`, `create-json`, `update-json`, the imports and `transition` checks the priority, the
components, the fix versions and the fields of a transition against the metadata of Jira first (exit code 6, and the allowed values in the hint), so that the mistake shows before
anything is sent. A required field that you left out is a warning, not an error. See [dry runs that check values](comments-and-people.md#5-dry-runs-that-check-values).

## The same command is slow

A single read should take a fraction of a second after the connection is open. Run with `-v` to see which request
is slow. A report or export of a large project fetches 100 issues per request; that is the page size of Jira.
`JIRA_MAX_TIME` bounds a request. A VPN or proxy that inspects traffic adds time to every request, not to the CLI.

## Reset

```bash
jira config show                 # what is set, and where it comes from
jira remove-pat                  # forget the token
jira clear-project               # forget the current project
python install.py --uninstall --purge   # remove the program, the configuration and the cache
```

`JIRA_NO_CACHE=true` ignores the cache of field and issue-type lists without deleting it.
