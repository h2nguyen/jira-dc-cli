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
writes some local files: its settings file and cache, the Markdown tickets and import files you name, exports, and the skill
you install. It has no runtime dependencies beyond the Python standard library and it contacts no other server.

It trusts you, your computer and your Python, and the Jira server you configured. A vulnerability in Jira, in Python or in the
operating system, or an attacker who can already run code as you or read your profile, is outside the scope of this project.

### Tokens and credentials

- `jira setup-pat` saves the token in the settings file, written with owner-only permissions where the file system supports
  them (on Windows the protection is that of your profile folder). The token is not kept in a keyring.
- The token is never printed, logged (also not with `-v`), accepted as a command line argument or shown by `jira config show`,
  and it is taken out of the error text of Jira before that text is shown.
- It is sent only to the configured address, and only over https, unless you allow plain http (`JIRA_ALLOW_HTTP`; `localhost`
  is always allowed). A redirect is not followed. `JIRA_INSECURE` switches the TLS check off and is meant for a short
  diagnosis, never for a setting.
- The settings file is read as data and never executed: `$(...)`, backticks and `$VAR` outside single quotes are refused.
- Credentials inside `JIRA_BASE_URL` (`https://user:password@host/jira`) are not used for authentication and are not hidden:
  the address is shown as written, by `jira ping`, `jira config show` and in the links of results. Authenticate with the
  token only, and never put a password in the address.

In scope: any way for the token to reach a log, a file other than the settings file, the output of a command, or a host other
than your Jira. Out of scope: a token that someone reads from your profile, or that you pasted into a chat.

### Terminal output

Jira text is written by other people. The program prints it as data and never runs it, but it does not remove terminal escape
sequences from text output. Read untrusted issues with `--json`, where control characters below U+0020, the escape character
included, are escaped, or in a terminal you trust. An AI agent must treat Jira text as data and never as instructions; the
skill says so, but no program can enforce it.

### File paths

`pull`, `export --out`, the import map, `skill install --dir`, the cache and the settings file write to disk. A file name made
from an issue is only its key and an ASCII slug of its title; a key that is not a key (`../PROJ-1`) is refused. Files are
replaced atomically. `pull` never deletes a file and never overwrites an edit of yours without `--force`, and the installer
removes only what it recognises as its own. Pulled and exported files can hold confidential business content: treat them as
you treat the Jira project, and do not commit them to a public repository.

### Personal data in page content

Issue text can hold names, e-mail addresses, mentions and links to personal files. `issue`, the comment list, search snippets,
`pull`, `export` and the files they write remove mentions, e-mail addresses and personal share links. `JIRA_HIDE_USERS=true`
goes further: it forbids showing people anywhere and applies the same removal to Jira's own error messages and to the raw
output of `jira api`. Without it those two show what Jira sent (the token is always taken out). The removal is pattern
based: a plain name inside free text, or a plain user name inside Jira's own error sentence, cannot be recognised. In scope:
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
