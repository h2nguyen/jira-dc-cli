# Contributing to jira-dc-cli

Thank you for thinking about helping. A bug report, a better sentence in a guide or a new test is as welcome as a new command.
This page says what the project is for, how to report a problem, how to set up and check your work, and what a pull request
needs. Everyone who takes part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## What the project is for

jira-dc-cli is a small, safe command line client for **Jira Data Center**, for people at a terminal and for programs and AI
agents. Four ideas shape every decision:

- **Safe by default.** Reading is free. A command that changes Jira has `--dry-run` and can be guarded with
  `--if-unchanged-since`. The token is never printed, and people and personal data are kept out of the output.
- **Nothing to install but Python.** The standard library only, Python 3.10 or newer. No dependency, no build step.
- **Equally good for people and for programs.** Help with examples, `--json` with stable keys, fixed exit codes, and a
  description of every command that a program can read.
- **Small and declarative.** One description per command feeds the parser, the help, the generated reference and the tool
  descriptions for agents.

Good contributions fit those ideas: a fix, a clearer message, a missing option, a better guide, a test for something that
broke, a platform that does not work yet. These are likely to be declined, so please ask in an issue before you invest time:
support for Jira Cloud (it has other authentication and other endpoints), a graphical interface, a runtime dependency, a
command that writes without a dry run, anything that sends data anywhere but your Jira. A project maintained in free time has
to say no sometimes; a decision is about scope and never about the person.

## Report a bug

1. Look at the existing issues and at [troubleshooting](docs/how-to/troubleshooting.md), and run `jira doctor`: most setup
   problems are explained there.
2. Open an issue with the **Bug report** form. It asks for the version (`jira version`), the operating system, the Python
   version, the command, what you expected and what happened. `-v` on any command logs each request to standard error, and
   `--json` gives errors as JSON.
3. **Redact before you paste.** Never put a token, the name of a real Jira site, an issue key from a real project, or real
   Jira content (text, names, e-mail addresses, links) into an issue, a pull request or a comment. Use `https://jira.example.com`,
   `PROJ-123` and `[token]`. Remove your user name, your computer name and local folder paths from pasted output as well
   (`jira doctor` prints where your settings file is). A bug that can be shown with the bundled mock Jira
   (`tests/support/mock_jira.py`) is the easiest to fix; a failing test is the best report.

A security problem is not an issue: see [SECURITY.md](SECURITY.md).

## Suggest a feature

Open an issue with the **Feature request** form. Describe the problem you have before the command you wish for, say how you
work today, and check that the idea fits the scope above. For anything bigger than a small option, agree on the idea in the
issue before you write code.

## Set up and check your work

You need Python 3.10 or newer and Git. There is nothing to install with pip: fork the repository, clone your fork, and run
the checks from its folder.

```bash
git clone <the URL of your fork>
cd jira-dc-cli
python -m unittest discover -s tests -t .   # the whole suite: offline, no Jira, no network
python scripts/generate_docs.py             # regenerate docs/reference and the command table in the README
python scripts/check_release.py             # version, changelog, generated files, skill and links agree
python jira.py version                      # run the program straight from the source
```

The suite takes about a minute. One module: `python -m unittest tests.unit.test_domain`. `python install.py --dry-run` shows
what the installer would do, and `python install.py` installs your working copy for your user.

The reference documents in `docs/reference/` and the command table in the README are **generated**. Do not edit them by hand:
change the command description (`src/jira_dc_cli/adapters/driving/cli/commands/`) or the setting
(`src/jira_dc_cli/application/settings.py`) and run `python scripts/generate_docs.py`. `check_release.py` and the tests fail
when a generated file is out of date.

Use synthetic data in tests, documents and examples: `example.com` hosts, project keys such as `PROJ` or `DEMO`,
`jane.doe@example.com`. Never copy text from a real Jira.

## Architecture rules

The program is hexagonal (ports and adapters): the rules sit in the middle and know nothing about HTTP, files or the
terminal. [docs/architecture.md](docs/architecture.md) explains it and
[docs/how-to/extend-the-cli.md](docs/how-to/extend-the-cli.md) walks through a change. `tests/test_architecture.py` enforces
the rules by reading the imports of every module, and a failure names the file and the import:

- **Dependencies point inwards.** `domain` imports nothing from the program except `domain`. `application` imports `domain`
  and `application` only. Driven adapters (`adapters/driven`) implement the ports and import `domain`, `application`, their
  own package and `version`, never a driving adapter. The command line (`adapters/driving`) imports `domain`, `application`,
  its own package and `version`, never a driven adapter.
- **No I/O inside.** `domain` and `application` do not import `os`, `sys`, `pathlib`, `socket`, `ssl`, `http`, `urllib`,
  `subprocess`, `shutil`, `tempfile`, `getpass`, `platform`, `time`, `glob`, `logging` or `threading`. Reading the clock, a
  file or the network goes through a port.
- **One composition root.** Only `bootstrap.py` imports both sides, and nothing imports it except the entry points.
- **The standard library only**, in the program and in the tools (`jira.py`, `install.py`, `scripts/`). No
  `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile`, `poetry.lock` or `package.json`: there is nothing to install.
- **Every port has a test double** (`tests/support/fakes.py`) and, except `Diagnostics`, which lives with the command line,
  a real adapter. **Every command module is registered** in `commands/__init__.py`.

`tests/cli/test_framework.py` adds rules for commands: every command has examples that parse and every argument has help
text, a command that returns data documents its JSON, and a command that changes Jira has `--dry-run`.

## Pull requests

- **Keep it small and about one thing.** A fix and an unrelated clean-up are two pull requests.
- **Branch from `main`**, and open the pull request against `main`. The pull request template lists what to check.
- **Test what you change.** A fix comes with a test that fails without it; a new command with tests at the use case, the
  command line and, when it talks to Jira, the mock Jira level.
- **Run the three checks** above before you push. The `CI` workflow runs the tests and `scripts/check_release.py` on Linux,
  Windows and macOS, and has to pass.
- **Update the documentation** that the change touches, and add a line under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md) for everything a user can notice.
- **Change no command, option, setting, JSON key or exit code** without discussing it in an issue first: they are the
  interface, and a change of one needs a new major version (see [the release process](docs/how-to/release-process.md)).
- **No new dependency, no new tool in the build.** Keep to the standard library.
- **Say what you verified**, and where: against the mock Jira, or against a real Jira Data Center (read-only or not).
- A clear subject line in the imperative for every commit is enough; a ticket key is not needed.

A maintainer reviews every pull request, may ask for changes, and may decline a change that does not fit the project.
Declining is never about the person.

## What to expect from the maintainers

This is a volunteer project and the answer time is **best effort**. Expect a first reply within about a week, and a review
within about two. If nothing has happened after two weeks, one friendly reminder in the issue or pull request is welcome.
There is no support promise, and no shame in a project that moves slowly.

**Please ask in the open.** Do not e-mail or message the maintainers about a bug, a question or a pull request: an issue
helps everyone who has the same problem, and keeps the answer where it can be found. The only private channels are
[SECURITY.md](SECURITY.md) for vulnerabilities and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for conduct reports.

**Conduct reports.** The Community Moderators named in the [Code of Conduct](CODE_OF_CONDUCT.md) are the maintainers. If a
report concerns a maintainer, send it to GitHub instead, through its
[abuse reporting](https://docs.github.com/en/communities/maintaining-your-safety-on-github/reporting-abuse-or-spam).

**Decisions.** The maintainers listen, explain their reasons in the issue, and decide. When contributors disagree and
cannot settle it in the discussion, the maintainers make the call. A project of this size has no committee or vote.

Beginner-friendly issues carry the label `good first issue`; documentation fixes need no test.

## Your contribution and the license

The project is released under the [MIT License](LICENSE). By contributing, you agree that your contribution is licensed under
the same license, and you confirm that you have the right to contribute it: it is your own work and contains nothing that
belongs to your employer or to somebody else. There is no contributor license agreement and no sign-off to add to your commits.
