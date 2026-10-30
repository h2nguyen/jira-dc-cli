"""Which REST calls the Jira Data Center 11.3 reference documents. The mock Jira refuses every other, so that the CLI cannot come
to depend on an endpoint that the reference for 11.3 does not know (see `jira_dc_11_3_endpoints.txt` for where the list comes from).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DOCUMENTED = Path(__file__).with_name("jira_dc_11_3_endpoints.txt")

# Not REST calls, so not in the reference: the address that an attachment's `content` field points to.
OUTSIDE_THE_REFERENCE = ("/secure/attachment/",)


@lru_cache(maxsize=1)
def _patterns() -> tuple[tuple[str, "re.Pattern[str]"], ...]:
    patterns = []
    for line in DOCUMENTED.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        method, template = line.split(" ", 1)
        parts = re.split(r"(\{[^}]+\})", template)
        regex = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
        patterns.append((method, re.compile("^" + regex + "$")))
    return tuple(patterns)


def is_documented(method: str, path: str) -> bool:
    """Whether the 11.3 reference has this call. `path` is below the context path, with its `/rest` prefix and without a query."""
    if any(path.startswith(prefix) for prefix in OUTSIDE_THE_REFERENCE):
        return method == "GET"
    if not path.startswith("/rest/"):
        return False
    relative = path[len("/rest") :]
    return any(method == known and pattern.match(relative) for known, pattern in _patterns())


def template_of(method: str, path: str) -> str | None:
    """The documented path template that a request matches, such as `/api/2/issue/{issueIdOrKey}`; None when there is none."""
    relative = path[len("/rest") :] if path.startswith("/rest/") else path
    for line in DOCUMENTED.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line:
            continue
        known, template = line.split(" ", 1)
        if known != method:
            continue
        regex = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in re.split(r"(\{[^}]+\})", template))
        if re.match("^" + regex + "$", relative):
            return template
    return None
