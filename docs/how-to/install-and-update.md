# Install, update and uninstall

## What you need

Python 3.10 or newer. Check with `python --version` (on Windows `py -3 --version` works too). Nothing else:
the CLI uses only the Python standard library, so there is no `pip install`, and it needs no curl, jq or
PowerShell script. TLS uses the certificate store of the operating system.

## Install

From the folder that holds `install.py`, in Git Bash, PowerShell or cmd:

```bash
python install.py --dry-run     # shows the Python, the folders and the launchers; changes nothing
python install.py
```

What it does:

1. Copies the program to a **local** folder: `%LOCALAPPDATA%\jira-dc-cli` on Windows, `~/.local/share/jira-dc-cli`
   elsewhere. It contains the package, the documentation, the skill, the examples, `README.md`, `CHANGELOG.md`,
   `LICENSE` and `VERSION`. It builds the new copy in a new folder next to the old one and swaps them, so a failure
   cannot leave a half-copied program.
2. Writes two launchers to `~/bin`: `jira` (a `sh` script, for Git Bash, Linux and macOS) and `jira.cmd`
   (for cmd and PowerShell). Both start the Python that ran the installer, in isolated mode (`-I`, so a stray
   `PYTHONPATH` cannot interfere) and with UTF-8 (`-X utf8`), whatever the console encoding.
3. Precompiles the program, starts it once and checks that it reports the version it just installed.

It never touches your configuration (`~/.config/jira-dc-cli`) or a token.

**Why local.** Python reads about a hundred files every time it starts. On a network home drive (`H:\` on many
company laptops) that costs about 1.4 seconds per command, on a local disk about 0.4 seconds, so the program does not
go to `~` on Windows. The launchers are tiny and stay in `~/bin`, which is already on your `PATH`.

| Option | Meaning |
| --- | --- |
| `--dry-run` | Show what would happen, change nothing. |
| `--prefix DIR` | Install into `DIR` (default: `JIRA_CLI_INSTALL_DIR` when set, else the local folder above). |
| `--bin-dir DIR` | Write the launchers into `DIR` instead of `~/bin` (default: `JIRA_CLI_BIN_DIR` when set). A folder that is on every shell's `PATH` makes `jira` work in all of them. |
| `--python PATH` | Let the launchers start this Python instead of the one running the installer. |
| `--force` | Replace a launcher that is not from this program, and move a foreign installation folder aside (it is kept next to it, not deleted). Without it the installer refuses both. |
| `--uninstall` | Remove the program and the launchers (see below). |
| `--purge` | With `--uninstall`: also delete the configuration file and the cache files, in every folder the program looks in (see "Which home folder"). A standard folder goes only when nothing else is left in it. |

## Which home folder

Git Bash sets `HOME` (for example to `H:\`). PowerShell and cmd do not; they use the Windows home drive and path
(`HOMEDRIVE` + `HOMEPATH`, the same folder when your laptop has a home drive), else the Windows profile. So the
launchers go to the same `~/bin` whichever shell runs the installer, and the CLI finds its configuration there as
well (see [configure the connection](configure.md)). The program itself goes to the local profile, not
to that home (see "Why local" above).

## Use it from PowerShell and cmd

`~/bin` must be on the `PATH` of the shell. Git Bash usually has it; PowerShell and cmd usually do not. Three ways,
from the least to the most invasive:

1. **No setting at all.** Run the program directly, which needs no `PATH`:
   `python "$env:LOCALAPPDATA\jira-dc-cli\jira.py" issue PROJ-1` (PowerShell) or
   `python "%LOCALAPPDATA%\jira-dc-cli\jira.py" issue PROJ-1` (cmd). The launcher is the same program with
   `-I -X utf8` in front.
2. **Put the launchers where every Windows shell looks already.** The installer prints this when it applies, for
   example `python install.py --bin-dir "$env:LOCALAPPDATA\Microsoft\WindowsApps"` in PowerShell (the folder of the app
   execution aliases is on the `PATH` of every user). Git Bash finds the launchers there as well.
3. **Add `~/bin` to the user `PATH`** (Windows Settings, search for "environment variables").

Open a new terminal, and restart VS Code, after you change the `PATH`: running programs keep the old one. A
coding agent that works in PowerShell needs one of these; see [use with AI agents](use-with-ai-agents.md).

The launcher is a `.cmd` file on purpose: a company policy such as PowerShell `AllSigned` blocks `.ps1`
scripts, but not `.cmd`.

## Update

Get the new source (pull the repository or copy the folder) and run `python install.py` again. The old
program is replaced completely, the configuration stays. Afterwards:

```bash
jira version
jira doctor
```

## Another Python

The launchers remember one interpreter. When you upgrade Python or remove the one that was used, run
`python install.py` with the interpreter you want, or `python install.py --python C:\Python313\python.exe`.
`jira doctor` shows which Python and version runs.

## Run without installing

From the source folder:

```bash
python jira.py issues --mine
python -m jira_dc_cli version      # with PYTHONPATH=src
```

## Uninstall

```bash
python install.py --uninstall            # program and launchers; the configuration stays
python install.py --uninstall --purge    # also the configuration (with the token) and the cache
python install.py --uninstall --dry-run  # shows what would be removed
```

The uninstaller removes only what it recognises as this program: an installation folder (`jira.py` together with
`jira_dc_cli/`) and the launchers that carry the installer's signature **and start the program being removed**. A folder
with a lone `jira.py` and a wrapper that merely mentions `jira-dc-cli` are left alone. With `--purge` it deletes the
configuration file (the one `jira config path` prints, and the same file in the other home folders) and the cache
files (`*.json`), and a standard folder only when nothing else is left in it: a folder that holds other things stays,
and the folder of a `JIRA_CONFIG_FILE` is never removed, even when it is empty afterwards. The installer never deletes a
folder it did not create itself: its working folders have unique names, and `--force` over a foreign folder moves
that folder aside instead of deleting it.
