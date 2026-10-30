# Configure the connection

Four commands set it up. Run `jira doctor` at the end; it checks every step and says how to fix what fails.

```bash
jira setup-pat                                               # the personal access token
jira config set JIRA_BASE_URL https://jira.example.com/jira  # where Jira lives, with its context path
jira use PROJECTKEY                                          # the project to work in
jira doctor
```

## The token

Create a personal access token in your Jira profile (Profile, Personal Access Tokens) and run
`jira setup-pat`. The input is not shown. The token goes into the configuration file with restricted
permissions and is never printed, logged or accepted as a command line argument, so it does not end up in
the shell history. A token is one line of printable ASCII characters without blanks: `jira setup-pat` and the setting `JIRA_PAT` refuse anything else,
and the error does not repeat the text.

- **Scripts and agents:** `printf '%s' "$TOKEN" | jira setup-pat --stdin`.
- **Git Bash without a real terminal:** `jira setup-pat` warns that the input may be visible when it cannot hide
  it. Use `winpty jira setup-pat`, or pipe the token in with `--stdin`.
- **Remove it:** `jira remove-pat`. Rotate the token in Jira if it was ever exposed.

## Where the settings live

Settings come from, in this order: **environment variables**, the **configuration file**, the **defaults**.
A variable that is set but empty counts as not set (so `export JIRA_HIDE_USERS=` cannot switch off a policy of the
file). `jira config show` lists every setting with its value and its source; secrets are masked, and so are the user name and password of an address. `jira config path`
prints the file. Because the environment wins, `jira config set`, `jira use` and the other commands that change the
file warn when the variable they change is also set in your environment.

The file is `~/.config/jira-dc-cli/config`. It holds `KEY=value` lines (UTF-8, or UTF-16 and Windows-1252 as an editor such as Notepad saves it: all are read); quote a value that contains spaces
(`JIRA_CA_BUNDLE='C:/certs/my ca.pem'`). It is read as data and never executed: a value that needs a shell
(`$(...)`, backticks, `$VAR`, a leading `~`) is refused with a message instead of being guessed. Inside single
quotes a `$` or `~` is plain text.

Which file is used, in this order:

1. `JIRA_CONFIG_FILE`, when set.
2. `XDG_CONFIG_HOME/jira-dc-cli/config`, when that file exists.
3. `.config/jira-dc-cli/config` in the first home folder that has one. The home folders, in this order: `HOME` when it
   is set (Git Bash sets it, for example to `H:\`); otherwise the Windows home drive and path (`HOMEDRIVE` such as `H:`
   plus `HOMEPATH` such as `\`, which Git Bash itself falls back to; skipped when they look otherwise or the folder
   does not exist); then always the Windows profile.
4. Otherwise a new file is created in `XDG_CONFIG_HOME` when that is set, else in the first home folder.

PowerShell and cmd do not set `HOME`, but they get the same answer as Git Bash on a laptop with a home drive: the
configuration you made in Git Bash is the one they read. `jira config path` prints the file in use, and
`jira doctor` checks it. The cache folder (`.cache/jira-dc-cli`) is in the home folder of the configuration in use,
or in `XDG_CACHE_HOME`. With `JIRA_CONFIG_FILE` and `XDG_CACHE_HOME` both set no home folder is looked at.

All settings are in the [configuration reference](../reference/configuration.md). The ones you will meet:

| Setting | Use |
| --- | --- |
| `JIRA_BASE_URL` | Address of Jira, including the context path (`https://host/jira`). Required. Must be https; see below. Put no user name or password in it: they sign in nothing, the CLI leaves them out of every address it prints (`jira config show` shows `********` in place of them, and `jira config set` refuses to save them) and `jira doctor` warns about them. The token is the only credential. |
| `JIRA_PAT` | The token. Set it with `jira setup-pat`. Required. |
| `JIRA_PROJECT` | The current project. Set it with `jira use KEY`, forget it with `jira clear-project`. |
| `JIRA_HIDE_USERS` | `true` removes people from what the commands print. See below. |
| `JIRA_PROFILE` | `agent` for an AI agent: `JIRA_HIDE_USERS=true` and `JIRA_OUTPUT=json` become the defaults and informational messages stay off standard error. See below. |
| `JIRA_CA_BUNDLE` | A PEM file for a private company certificate authority. |
| `JIRA_ALLOW_HTTP` | `true` accepts a plain `http://` address. See below. |
| `JIRA_OUTPUT` | `json` makes every command that supports it print JSON. For scripts and agents. |
| `JIRA_*_FIELD` | Ids of the custom fields, when they cannot be found by name. |

## https only

The token travels in every request, so the CLI refuses a plain `http://` address: anybody on the network path could read
the token. Addresses on this computer (`localhost` and loopback addresses such as `127.0.0.1` and `::1`) are always accepted, for tests; a name that only
starts with `127.` is a name that the network resolves, and is not one of them. If Jira really only
offers http, on a network you trust, accept the risk explicitly: `jira config set JIRA_ALLOW_HTTP true`.
`jira doctor` reports the state.

## A private certificate authority

The CLI trusts what the operating system trusts. On a managed Windows laptop a company CA is normally
installed there already, so nothing is needed. If `jira doctor` reports a certificate error, save the CA
certificates as a PEM file and name it:

```bash
jira config set JIRA_CA_BUNDLE "C:/certs/company-ca.pem"
```

`JIRA_INSECURE=true` switches the check off. Use it only as a short diagnostic, never as a setting.

## Keep people out of the output

Some commands show people because that is what they list: the assignee column of `issues`, the lead in `projects`
and `project`, and who you are in `ping`. Everywhere else people are hidden unless you ask (`issue --with-users`,
`comment --list --with-users`), and mentions, e-mail addresses and personal share links are removed from text; Jira's own explanation of a refusal (the error
message and its details) is cleaned the same way, whatever the setting. You
can switch the listing columns off (`issues --no-assignee`, `ping --quiet`). To make "no people" a rule instead of a
habit:

```bash
jira config set JIRA_HIDE_USERS true
```

Now `--with-users` is refused, and the assignee column, reporter, project lead and the identity in `ping` disappear, as do the people in lists, histories and
saved filters. The people in the details of Jira's error messages are replaced too (a plain user name inside Jira's sentence cannot be recognised). One
exception stays: your own login appears where a command sets you as the reporter or the assignee (`create`, `assign KEY me`, `update --assignee me`, in
the dry run and in the result); `ping` and `watch` hide it. An environment variable `JIRA_HIDE_USERS=true` in the shell of
an agent does the same for one session.

### One setting for an agent: the profile

An agent needs "no people", JSON and few messages. `JIRA_PROFILE=agent` (in the environment of the agent, or once with
`jira config set JIRA_PROFILE agent`) sets all of it:

| Setting | With the profile |
| --- | --- |
| `JIRA_HIDE_USERS` | `true`, unless the setting itself is set (even to `false`): what you set wins. |
| `JIRA_OUTPUT` | `json`, unless the setting itself is set. Errors then come as JSON on standard error too. |
| informational messages | Not printed on standard error ("Showing 50 of 120", notes of a search); warnings and errors stay. The JSON holds the same facts. |

`jira config show` lists the profile and, as `profile agent`, the settings it implies. `jira doctor` shows the privacy check as `warn` when
the profile is on and people can still be shown (because `JIRA_HIDE_USERS` was set to `false`), and `jira doctor --strict-privacy` fails
whenever people can be shown, profile or not.

## Custom fields

Story Points, Epic Link, Sprint and the acceptance criteria are custom fields with a different id in every
Jira. The CLI finds them by name once and caches the field list for a day (`jira fields --refresh` reloads it).
If your Jira names them differently, find the id and set it:

```bash
jira fields --search "story points"
jira config set JIRA_STORY_POINTS_FIELD customfield_10001
```

## Timeouts and retries

`JIRA_CONNECT_TIMEOUT` (default 10 seconds) and `JIRA_MAX_TIME` (120 seconds) bound a request; each is at most 86,400 seconds (a day), as is
`jira projects-admin --timeout`. A larger value is refused with a message.
`JIRA_RETRY` (default 2) retries a **read** after a network error or HTTP 429, 502, 503 or 504. Writes are
never retried, because a repeated write could create a duplicate.

## Cache

The CLI caches the field list (one day) and the issue types of a project (one week) in
`~/.cache/jira-dc-cli`, separately for every Jira address. `JIRA_NO_CACHE=true` switches it off;
`jira create --refresh-metadata` and `jira fields --refresh` reload one list.
