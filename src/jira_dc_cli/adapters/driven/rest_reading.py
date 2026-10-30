"""The reading calls of `RestJiraGateway`: a mixin that uses the gateway's `_call`, `_transport` and `_settings`."""
from __future__ import annotations

import urllib.parse
from typing import Any, Mapping, Sequence

from ...domain.issueparts import ExpandedIssue
from ...domain.savedfilters import SavedFilter
from . import map_reading

KEYS_PER_SEARCH = 100  # the keys of one `key in (...)`: a query that fits into a URL, and a page that every server gives


def _quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="")


def _query(params: Mapping[str, object]) -> str:
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


class RestReading:
    """Implements `ReadingPort` (application/ports_reading.py) over Jira Data Center's REST API."""

    def get_issue_with(self, key: str, expand: Sequence[str] = ()) -> ExpandedIssue:
        path = f"/issue/{_quote(key)}" + (f"?{_query({'expand': ','.join(expand)})}" if expand else "")
        return map_reading.expanded_from(self._call("GET", path), self.field_ids())

    def get_issues_by_key(self, keys: Sequence[str], expand: Sequence[str] = ()) -> list[ExpandedIssue]:
        found: list[ExpandedIssue] = []
        for start in range(0, len(keys), KEYS_PER_SEARCH):
            found += self._search_by_key(list(keys[start : start + KEYS_PER_SEARCH]), expand)
        return found

    def _search_by_key(self, keys: list[str], expand: Sequence[str]) -> list[ExpandedIssue]:
        """One search for these keys, and the next page when a server answers with fewer issues than asked for. `*all` gives what
        `GET /issue/{key}` gives: a search returns only the navigable fields (not the comments) unless it is told."""
        ids = self.field_ids()
        found: list[ExpandedIssue] = []
        total = len(keys)
        while len(found) < total:
            params: dict[str, Any] = {"jql": "key in (" + ", ".join(keys) + ")", "startAt": len(found), "maxResults": len(keys), "fields": "*all"}
            if expand:
                params["expand"] = ",".join(expand)
            data = self._call("GET", f"/search?{_query(params)}")
            page = [item for item in data.get("issues") or [] if isinstance(item, dict)]
            if not page:
                break
            found += [map_reading.expanded_from(item, ids) for item in page]
            total = data["total"] if isinstance(data.get("total"), int) else total
        return found

    def list_filters(self) -> list[SavedFilter]:
        data = self._call("GET", "/filter/favourite")
        return [map_reading.filter_from(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def get_filter(self, filter_id: str) -> SavedFilter:
        return map_reading.filter_from(self._call("GET", f"/filter/{_quote(filter_id)}"))
