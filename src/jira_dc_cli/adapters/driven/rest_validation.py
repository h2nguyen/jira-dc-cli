"""The validation calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse
from typing import Any

from ...domain.validation import FieldRules, TransitionScreen
from .map_validation import rules_from_create, rules_from_fields, screens_from

_PAGE = 1000  # fields asked for per request of the create metadata; a server may answer with fewer
_MOST_PAGES = 20  # the most requests for the fields of one issue type: a server that never says "last" cannot keep the program asking


def _last(page: Any, start: int, received: int) -> bool:
    """Whether `page`, which began at `start` and held `received` fields, was the last one. A page that says nothing about paging (a bare
    list, or no `isLast` and no `total`) is the whole answer."""
    if not isinstance(page, dict):
        return True
    if page.get("isLast") is True:
        return True
    total = page.get("total")
    if isinstance(total, int) and not isinstance(total, bool):
        return start + received >= total
    return "isLast" not in page


class RestValidation:
    """Implements `ValidationPort` (application/ports_validation.py) over Jira Data Center's REST API."""

    def get_edit_metadata(self, key: str) -> FieldRules:
        data = self._call("GET", f"/issue/{urllib.parse.quote(key, safe='')}/editmeta")
        return rules_from_fields(data.get("fields") if isinstance(data, dict) else None)

    def get_create_fields(self, project_key: str, issue_type_id: str) -> FieldRules:
        """The fields of the create screen of one issue type. The reference pages this answer (`startAt`, `maxResults`), and a server may
        cap the page below the number of fields of a screen: the pages are read until the last one and joined, so that a field on a later
        page is not taken for a field that the screen lacks. (The edit metadata and the transitions are not paged.)"""
        base = f"/issue/createmeta/{urllib.parse.quote(project_key, safe='')}/issuetypes/{urllib.parse.quote(issue_type_id, safe='')}"
        values: list[Any] = []
        start = 0
        for _ in range(_MOST_PAGES):
            page = self._call("GET", f"{base}?startAt={start}&maxResults={_PAGE}")
            items = page.get("values") if isinstance(page, dict) else page
            if not isinstance(items, list) or not items:
                break
            values.extend(items)
            if _last(page, start, len(items)):
                break
            start += len(items)
        return rules_from_create(values)

    def list_transition_screens(self, key: str) -> list[TransitionScreen]:
        return screens_from(self._call("GET", f"/issue/{urllib.parse.quote(key, safe='')}/transitions?expand=transitions.fields"))
