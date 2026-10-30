"""The command modules. To add a command: write a module with a `COMMANDS` tuple and list it here."""
from __future__ import annotations

from . import agile, attachments, collab, info, issues, mcp, projects, reading, reference, session, sync, users, workflow, writing

# The order decides the order of the commands inside a group of `jira help`.
MODULES = (issues, reading, attachments, workflow, collab, users, agile, writing, sync, projects, reference, session, mcp, info)
