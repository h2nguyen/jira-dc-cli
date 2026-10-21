"""The command modules. To add a command: write a module with a `COMMANDS` tuple and list it here."""
from __future__ import annotations

from . import info, issues, projects, session, sync, workflow, writing

# The order decides the order of the commands inside a group of `jira help`.
MODULES = (issues, workflow, writing, sync, projects, session, info)
