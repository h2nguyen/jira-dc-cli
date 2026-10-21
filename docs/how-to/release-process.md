# Release a new version

A release is a version number, a changelog entry, a Git tag and a GitHub release that carries the text of the entry.

The version lives in two files that must agree, `VERSION` and `src/jira_dc_cli/version.py`, and in the changelog.
`scripts/check_release.py` checks that they do, together with the documentation. The project follows
[semantic versioning](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`.

| Change | Bump |
| --- | --- |
| A removed or renamed command, option, setting, JSON key or exit code; a changed meaning | MAJOR |
| A new command, option, setting or JSON key; a new feature that keeps everything working | MINOR |
| A fix that changes no interface | PATCH |

## Steps

1. **Work on a branch** and open a pull request into `main`. Add a line under `## [Unreleased]` in `CHANGELOG.md`
   for every change a user can notice (`Added`, `Changed`, `Fixed`, `Security`, `Removed`).
2. **Choose the version** with the table above.
3. **Edit the version in both places**: `VERSION` (one line, no `v`) and `src/jira_dc_cli/version.py`
   (`__version__`).
4. **Close the changelog entry**: move the content of `## [Unreleased]` into a new heading
   `## [x.y.z] - YYYY-MM-DD` above the older entries, and leave an empty `## [Unreleased]` section on top. A
   breaking change needs a short migration note in the entry.
5. **Regenerate the documentation**: `python scripts/generate_docs.py` (the command reference and the command table
   in the README embed the version and the command descriptions).
6. **Run the checks**:

   ```bash
   python -m unittest discover -s tests -t .
   python scripts/check_release.py
   ```

   `check_release.py` fails when `VERSION` and `version.py` differ, when the newest changelog entry is not the
   current version or is out of order, when a generated file is out of date, when the skill does not mention a
   command, and when a link in the documentation leads nowhere.
7. **Install and try it**: `python install.py --dry-run`, then `python install.py`, then `jira version` and
   `jira doctor`.
8. **Merge the pull request** once the `CI` workflow is green on it (the checks run on Linux, Windows and macOS).
9. **Tag the merge commit** on `main` and push the tag. The tag is the version with a `v` in front:

   ```bash
   git switch main
   git pull
   git tag -a vX.Y.Z -m "jira-dc-cli X.Y.Z"
   git push origin vX.Y.Z
   ```

10. **Create the GitHub release** from that tag (Releases, Draft a new release, choose the tag). The title is
    `jira-dc-cli X.Y.Z` and the description is the text of the `## [X.Y.Z]` entry of `CHANGELOG.md`, copied as it
    stands. With the GitHub CLI:

    ```bash
    gh release create vX.Y.Z --title "jira-dc-cli X.Y.Z" --notes-file release-notes.md
    ```

    where `release-notes.md` holds the entry. Mark a pre-release as such. There is nothing to build or upload: the
    source archive that GitHub attaches to the release is what people install from (`python install.py`).

For the version 1.0.0 the tag is `v1.0.0` and the description of the release is the `## [1.0.0]` entry.

## What is in a release

Everything the installer copies: the package, `docs/`, `skills/`, `examples/`, `README.md`, `CHANGELOG.md`,
`LICENSE`, `VERSION`, `jira.py` and `install.py`. `jira version`, `jira changelog` and `jira how-to` read the
installed copies, so a release is complete only when those files are current.

## Rules for the changelog

- Write for the person who updates: what they can now do, what changed for them, what they must change.
- Name the commands and settings in backticks and give the exit code or key when it matters.
- Do not describe the implementation unless it changes what a user sees (speed, requirements).
- State only what was verified, and where: against the bundled mock Jira Data Center, or against a real one.
