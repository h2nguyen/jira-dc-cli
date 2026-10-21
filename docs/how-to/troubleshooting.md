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
| `JIRA_BASE_URL uses plain http` | The CLI will not send the token unencrypted. | Use the https address. Only on a network you trust: `jira config set JIRA_ALLOW_HTTP true`. |
| `Jira is limiting the rate of requests (HTTP 429)`, exit code 7 | Jira throttles you. The CLI already waited as long as Jira asked (at most 30 s) and tried again. | Wait a moment; use fewer requests (`--limit` instead of `--all`). |
| `Jira sent an answer that could not be read` | A proxy damaged the response. | Try again; ask the network team if it stays. |
| `JIRA_MAX_TIME must be a number of seconds greater than 0` (or another setting) | A value in the environment or the config file is wrong. | `jira config show` finds it; `jira config set` or `jira config unset` fixes it. |
| `Cannot reach Jira (...)`, exit code 7 | Network, VPN or proxy; or the address is wrong. | Check `JIRA_BASE_URL`, the VPN, and `jira doctor`. |
| A certificate error | Jira uses a company certificate authority that Python does not trust. | `jira config set JIRA_CA_BUNDLE "C:/certs/company-ca.pem"` (a PEM file with the CA certificates). `JIRA_INSECURE=true` is only a short diagnostic. Python 3.13 and newer also check the certificate itself more strictly: a CA certificate without an Authority Key Identifier is refused with `Missing Authority Key Identifier`, also from a bundle. Ask the administrators for a corrected CA certificate, or run the CLI with Python 3.12 or older. |
| `Jira answered with a redirect (HTTP 302)` | `JIRA_BASE_URL` has the wrong context path or uses `http` where Jira wants `https`. | Open Jira in the browser and copy the address up to the context path (`https://host/jira`). |
| `Not found: GET /issue/...`, exit code 4 | The key does not exist or is not visible to the token's user. | Check the key and the project. |
| `Jira refused the request (HTTP 400): ...`, exit code 6 | A field is required, not allowed on the issue type, or has a value Jira does not know. | Read the explanation; `jira metadata PROJECT ISSUE_TYPE_ID` lists the fields and which are required. See below. |
| `Issue type 'X' was not found` | The type is not creatable in this project. | The hint lists the available names and ids; `jira metadata PROJECT`. |
| `Fix Version 'X' does not exist in project` | Jira refuses unknown versions. | Create it in Project settings, Versions, or import with `--skip-invalid-fix-version`. |
| `No transition to 'X' is available from the current status` | The workflow does not allow that move from here. | `jira transition KEY` lists the possible moves. |
| `'X' is ambiguous` (transition) | Two transitions lead to the same status. | Use the transition id from the hint. |
| `changed in Jira at ..., after ...`, exit code 3 | Someone changed the issue after the time you gave to `--if-unchanged-since`. | Read the issue again and decide; this check stops a write based on a stale read. |
| `... changed in Jira after the last sync and was edited here too` (`pull`) or `... was edited too (the same summary): nothing was sent` (`push`), exit code 3 | The same field changed in the file and in Jira since the last agreement. Nothing was overwritten or sent. | `jira diff DIR --issue KEY` shows the field. Then `jira push FILE --force` (the file wins), `jira pull DIR --update --force --issue KEY` (Jira wins), or edit the file to the text you want and push that. |
| `... differ in ... and were never synced` (`push`), exit code 3 | The file never went through `pull` or `push`, so nobody can say which side is newer. | `jira pull DIR` notes the files that are equal; with `--update --force` Jira's value wins, with `jira push FILE --force` the file's. |
| `... holds a placeholder ([~user], [personal link] or [email])` (`push`) | The file shows where Jira holds a person's data, and sending the placeholder would overwrite it. | Leave that field out of your edit, or write the text again without the placeholder. `--force` does not help on purpose. |
| `The description of KEY holds a table (or a panel, a colour, a macro, text that changes on the way back), which Markdown cannot carry unchanged` (`push`) | Sending the file's text would change more than you edited (the file's `lossy:` line says what). | Change that description in Jira, or `--force` to send the file's text anyway. |
| `... holds a placeholder ([~user], [personal link] or [email])` (`import-md`, `import-csv`) | The file or row was written by `jira pull`, which redacts people; updating from it would overwrite the real text. | Use `jira push FILE` instead: it leaves such a field alone. `jira update` changes a field on purpose. |
| `The story points in fields_json are not a number` (`diff`, `pull`, `push`) | The entry is a word, a list or `true`; reading it as "no points" would let a push clear the field. | Write a number (`5` or `"5"`), or take the entry out. |
| `--jql has a bracket or a quote that does not close` (`search`) | An unbalanced condition could end the brackets that keep the other filters in force. | Balance the brackets and quotes. |
| `A folder does not say which issue to change` | `jira push` takes a file, or a folder together with `--issue`. | `jira push DIR --issue KEY` or name the file. |
| `Nothing found in project X` (`jira search`) | No issue holds the words, or the issues are in another language: the search does not translate. | Read the `notes` and `ignored` in the answer; try the words the issues use, fewer words, a wildcard (`rol*`), `--match any` or `--all-projects`. [Find information](find-information.md#7-when-nothing-is-found). |
| `No project selected` (`jira search`) | The search is limited to one project and none is set. | `jira use KEY`, `--project KEY`, or `--all-projects`. |
| `jira issues --jql ...` shows keys of other projects, with a note on stderr | A `--jql` without `project = ...` replaces the project condition and searches every project. | Add `project = KEY AND (...)` to the query, or use `jira search --jql`, which adds your condition to the project. |
| `Link type 'X' not found; use the exact name` | Phrases such as "requires" belong to several link types. | `jira link-types` lists the exact names. |
| `JIRA_HIDE_USERS=true: people are not printed` | The privacy policy is on. | Unset `JIRA_HIDE_USERS` if you really need to see people. |
| `JIRA_X uses shell syntax that the CLI does not execute` | The config file holds `$(...)`, backticks or `$VAR` outside single quotes. | Write the value itself, or put it in single quotes. |
| No such field `Story Points` (story points are 0 in a report) | The field has another name in your Jira. | `jira fields --search points`, then `jira config set JIRA_STORY_POINTS_FIELD customfield_NNNNN`. |
| `jira` is not found in PowerShell or cmd | `~/bin` is not on the Windows `PATH`. | Run the program directly, which needs no `PATH`: `python "$env:LOCALAPPDATA\jira-dc-cli\jira.py" version` (PowerShell). Or put the launchers where every shell looks, or add `~/bin` to the user `PATH` ([install](install-and-update.md#use-it-from-powershell-and-cmd)). Restart VS Code and the terminals afterwards. |
| `JIRA_BASE_URL is not set` in one shell, but `jira` works in another | The shells read different configuration files (`HOME`, `XDG_CONFIG_HOME` or `JIRA_CONFIG_FILE` differ between them). | `jira config path` shows the file each shell reads; set `JIRA_CONFIG_FILE` to one file for all of them. |
| `--fields-json is not valid JSON` for JSON that looks right, in Windows PowerShell 5.1 | PowerShell 5.1 removes the double quotes inside an argument: `'{"a":1}'` arrives as `{a:1}`. | Put the JSON in a file and pass it in single quotes: `--fields-json '@fields.json'` (a bare `@fields.json` is a PowerShell parse error: `@name` is splatting). For `api` use `--data-file`; or use `jira update-json KEY FILE`. Writing each quote as `\"` works too. |
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

On Jira Data Center the assignee is the **user name**, not the display name or the e-mail address.

## Markdown looks wrong in Jira

Jira's description field uses wiki markup, not Markdown. Convert with `--wiki` (`create`, `update`, `comment`,
`transition`, `import-md`, `import-md-dir`), or look at the result first: `jira wiki file.md`.

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
