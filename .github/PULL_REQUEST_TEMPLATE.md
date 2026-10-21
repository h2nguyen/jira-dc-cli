## What and why

<!-- What does this change, and why? Link the issue ("Fixes #123"). For a bigger change, agree on the idea in an issue first. -->

## Checklist

- [ ] The change is small and about one thing.
- [ ] `python -m unittest discover -s tests -t .` passes.
- [ ] I ran `python scripts/generate_docs.py` (when a command or a setting changed) and `python scripts/check_release.py`, and both are clean.
- [ ] I added or updated tests; a fix comes with a test that fails without it.
- [ ] I updated the documentation, and added a line under `## [Unreleased]` in `CHANGELOG.md` for what a user can notice.
- [ ] No command, option, setting, JSON key or exit code changed, or I agreed the change in an issue first.
- [ ] No new dependency: the program and its tools use the Python standard library only.
- [ ] No token, real site name, real Jira content (text, names, e-mail addresses) or personal path (user name, computer name) is in the code, the tests, the documents or this description.
- [ ] I have the right to contribute this, and it may be released under the MIT License (see `CONTRIBUTING.md`).

## How it was verified

<!-- Against the mock Jira only, or also against a real Jira Data Center (read-only or not)? Which operating system and Python version? -->
