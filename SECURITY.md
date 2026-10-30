# Security policy

jira-dc-cli talks to your Jira Data Center with your personal access token and prints what Jira holds. This page says
which versions get security fixes, how to report a problem privately, what the program protects and what it does not, and
what happens after a report.

## Supported versions

Only the newest release receives security fixes. A fix is released as a new patch (or, when it has to change an interface,
minor) version.

| Version | Security fixes |
| --- | --- |
| 1.0.x | yes |
| older | there are none |

## Reporting a vulnerability

**Do not open a public issue, pull request or discussion for a vulnerability.** Report it privately with GitHub's private
vulnerability reporting: open the **Security** tab of this repository and choose **Report a vulnerability**. Only the
maintainers see the report, and a fix can be prepared in private before it is announced.

Say what you can: the version (`jira version`), the operating system and the Python version, the command, what you expected
and what happened, and why you think it matters. The project is maintained by volunteers, so the times below are goals and not
promises: an answer within 7 days, and a first assessment (accepted, not a vulnerability, need more) within 30 days.

**Never post, send or attach a real token, the address of a real Jira site, or real Jira content** (issue text, names, e-mail
addresses, links to personal files), in a report or anywhere else in this repository. Replace them with placeholders such as
`https://jira.example.com`, `PROJ-123` and `[token]`. Also remove your user name, your computer name and local folder paths
from pasted output (`jira doctor` prints where your settings file is). If a problem can only be shown with real data,
describe it instead.
If you ever pasted a token somewhere public, revoke it in your Jira profile at once: deleting the post is not enough.

## What the program protects, and what it trusts

The program runs on your computer with your rights. It sends requests to the one Jira address you configured and reads and
writes some local files: its settings file and cache, the Markdown tickets and import files you name, exports, the attachments you
download into a folder you name, the files you name to attach to an issue, and the skill you install. It has no runtime dependencies
beyond the Python standard library and it contacts no other server.

It trusts you, your computer and your Python, and the Jira server you configured. A vulnerability in Jira, in Python or in the
operating system, or an attacker who can already run code as you or read your profile, is outside the scope of this project.

### Tokens and credentials

- `jira setup-pat` saves the token in the settings file, written with owner-only permissions where the file system supports
  them (on Windows the protection is that of your profile folder). The token is not kept in a keyring.
- The token is never printed, logged (also not with `-v`), accepted as a command line argument or shown by `jira config show`,
  and it is taken out of the error text of Jira before that text is shown. A token must be printable ASCII without blanks: `jira setup-pat` and
  `JIRA_PAT` refuse anything else with an error that does not repeat the text.
- It is sent only to the configured address, and only over https, unless you allow plain http (`JIRA_ALLOW_HTTP`; `localhost` and loopback
  addresses such as `127.0.0.1` and `::1` are always allowed, a name that only starts with `127.` is not). A redirect is not followed, except that `jira download` follows a redirect to the same address (same scheme, host and
  port; three at most) and never sends the token anywhere else: it builds the address of a file from the id and the name of the attachment
  below the configured address, never from the `content` field that Jira (or a proxy in front of it) reports. `JIRA_INSECURE` switches the
  TLS check off and is meant for a short diagnosis, never for a setting.
- `jira attach` never publishes the token: it refuses the configuration file of the CLI (found by its place, not by how the path is written) and any file
  whose bytes contain the token, in UTF-8 or UTF-16, in a dry run too, and `jira download` never writes into the folder that holds the configuration, with
  `--force` or without. The message never shows the token. A token in another form (base64, inside a compressed archive) is not found.
- The settings file is read as data and never executed: `$(...)`, backticks and `$VAR` outside single quotes are refused.
- Credentials inside `JIRA_BASE_URL` (`https://user:password@host/jira`) are not used for authentication. The CLI takes them out of
  every address it prints (`jira ping`, `jira server`, links in results, error texts, the results of `jira mcp`) and `jira doctor`
  warns about them. `jira config show` replaces the user name and password by `********`, and `jira config set JIRA_BASE_URL` refuses to save an address
  with credentials; a settings file that was written by hand keeps whatever it holds, which only `jira doctor` mentions. Authenticate with the token only, and never put a
  password in the address. An address in which an `@` follows the host name (a password that holds `/`, `?` or `#`) is refused with an error that does not
  repeat it, and `jira config show` does not show it at all.

In scope: any way for the token to reach a log, a file other than the settings file, the output of a command, an attachment, or a host other
than your Jira. Out of scope: a token that someone reads from your profile, or that you pasted into a chat.

### Terminal output

Jira text is written by other people. The program prints it as data and never runs it, but it does not remove terminal escape
sequences from the text of issues and comments. Read untrusted issues with `--json`, where control characters below U+0020, the
escape character included, are escaped, or in a terminal you trust. A file is treated more strictly: the names of attachments
(`jira attachments`, `jira issue`) and the text that `jira download --text` prints are cleaned of escape sequences and other control
characters, and of the characters that a reader cannot see, which are all the format characters (the controls that turn text around
on a screen, zero width characters, the byte order mark and the tag characters U+E0000 to U+E007F that can spell a hidden instruction),
so that a file cannot rewrite your terminal or carry a hidden message; the zero width joiner and non-joiner stay only between two
characters of emoji or of a script such as Persian, where they belong to the text. An AI agent must
treat Jira text, and the text of an attachment, as data and never as instructions; the skill says so, but no program can enforce it.

### File paths

`pull`, `export --out`, `download`, the import map, `skill install --dir`, the cache and the settings file write to disk. A file name
made from an issue is only its key and an ASCII slug of its title; a key that is not a key (`../PROJ-1`) is refused. `download` writes
into the folder you name (`--out`; by default `tmp/jira-attachments/KEY`) and does not trust the name that Jira holds for a file: the
name is made safe (no folder or drive, no control character, no trailing dot or blank, a prefix for a Windows device name such as
`CON`, about 150 characters at most, `-ID` for two files with one name), nothing is overwritten without `--force` (a file is created only when
nothing is there at the moment it is written, so a file that another program makes meanwhile is kept), nothing is written into the folder
that holds the configuration of the CLI, and a file is written whole or not at all. Files are replaced atomically. `pull` never deletes a
file and never overwrites an edit of yours without `--force`, and the installer removes only what it recognises as its own. `attach` reads
the files you name (never the configuration of the CLI or a file that holds the token) and sends their content to
your Jira, where everyone who can see the issue can open them; the CLI cannot take an attachment back. Pulled, exported and
downloaded files can hold confidential business content: treat them as you treat the Jira project, and do not commit them to a public
repository.

### The MCP server

`jira mcp` hands the commands to a model that chooses the arguments of every call, perhaps after it read text that a stranger put into a
ticket. So it is off by default for what changes Jira (`--allow-write`) and for what writes files (`--allow-files`; the two switches are
independent), and the person who starts it chooses the working folder: every path of a call must lie below that folder after the links in
it are followed, a path with a blank or an invisible character at an end is refused, a network or device path is refused without being looked at, and
the command is given the path that was checked, in full, never the text that the model wrote (a command that strips or splits a text must not reach
another place than the one that was judged). This holds for a path that the call leaves out as well when the command then works at a place of its own
(the default folder of a download, the map file of an import): the place is worked out, judged as a given path is, and given to the command. The settings
file, the folder that holds it and the cache
folder cannot be named. A file that holds the token is not read through a text option, and the refusal never shows the token. `force` is not
honoured, and a call that writes cannot write into a hidden folder, export onto a file that exists, or let `download` write into the working
folder itself; the map file of an import must be a `.csv` that is empty or a map. The server logs each refusal on standard error.

It does not make the working folder safe. Anything below it that the agent may name is as readable to it as with its own file tools: start
the server in the folder of the project, never in your home folder or the root of a drive (a call that names a path is refused there). A folder that a
call reads is not searched for the token, and a link inside it is not looked at. There is a gap between the check of a path and the command that uses it
(the command builds its application, and a download asks Jira, before the first file is written): a client that also gives the agent tools that change files
in the working folder (file or shell tools, an editor) can use it to put a link where the check saw a folder or a file, and the command follows it. Only a
client that gives the agent no such tools, or an operating-system sandbox around the server, closes that gap. The bytes and the name of an attachment that
`jira_download` saves come from the author of the ticket, so the agent must not open
or run them without asking. In scope: a way for a tool call to read the settings file, to learn the token, to write outside the working
folder or to replace a file with an export, to make a command write or read somewhere else than the place the policy approved (a blank, an
invisible character, a normalisation by the command) or work at a place that the policy did not judge (a default place), and to change Jira without
`--allow-write`. Out of scope: what a person allows an agent to do in the working folder with the agent's own tools.

### Personal data in page content

Issue text can hold names, e-mail addresses, mentions and links to personal files. `issue`, the comment list, search snippets,
`download --text`, `pull`, `export` and the files they write remove mentions, e-mail addresses and personal share links. The JQL of a saved filter
(`jira filters`, `jira filter`) has `[user]` in place of the people that it names (a custom user field and a name written in words are not
recognised), and `searchUrl` is left empty when the JQL had to be redacted. The comment list shows an author as a pseudonym (`authorRef`) that is
keyed with your token: it is the same for one person while the token stays, it changes when the token is replaced, and nobody without the token can
compute it from a user name (`jira users resolve` prints user names). Jira's own
explanation of a refusal (the error message, its hint and `details.jira`) gets the same removal whatever the setting, and the token is
always taken out of it. `JIRA_HIDE_USERS=true` goes further: it removes people from what the commands print (names, display names, e-mail addresses,
avatars, the people in lists, histories and saved filters), replaces whole user objects, and applies the removal to the raw output of `jira api`, which
without it shows what Jira sent (user objects, mentions, e-mail addresses and personal links are removed; the user keys and names in the changelog items
of the user fields `assignee`, `reporter`, `creator`, `watcher` and `voter`, and in the JQL of a saved filter, are cleaned, and the `searchUrl` of the filter is
emptied when its JQL needed any redaction). One exception is made on
purpose: your own login appears where a command sets you as the reporter or the assignee (the `reporter` in the payload of `create`, `assign KEY me`,
`update --assignee me`, in the dry run and in the result); `ping` and `watch` hide it, and `watch` shows `[you]` in a preview. The removal is pattern
based: a plain name inside free text, or a plain user name inside Jira's own error sentence, cannot be recognised, and neither can a structure that
holds a name elsewhere, such as a custom user-picker field. In scope:
a mention, an e-mail address or a personal link that gets through where the removal is documented. Out of scope: a name in
free text.

## When something goes wrong

A lightweight plan, for a project with one maintainer:

1. **Triage.** The maintainer reproduces the report, decides whether it is in scope and how severe it is (a token that leaks
   or code that runs is critical, a redaction that is bypassed is moderate), and answers the reporter.
2. **Fix in private.** The fix and a test that fails without it are prepared in the private fork that GitHub opens for the
   report, and checked with `python -m unittest discover -s tests -t .` and `python scripts/check_release.py`.
3. **Release.** A patch version is released as described in [the release process](docs/how-to/release-process.md), and the
   entry in [CHANGELOG.md](CHANGELOG.md) names the fix under `Security`.
4. **Announce.** The advisory is published with the affected and the fixed versions and, when it is warranted, a CVE. The
   reporter is credited if they wish.
5. **Tell users what to do.** If a token could have been exposed, the advisory says to revoke it in the Jira profile and to
   create a new one with `jira setup-pat`.
6. **Learn.** The threat model above is updated, and the weakness gets a lasting test.

If a vulnerability is already being exploited, steps 2 and 3 go first and the advisory follows as soon as a fixed version
exists.
