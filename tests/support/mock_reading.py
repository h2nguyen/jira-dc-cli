"""The reading routes of the mock Jira: an issue or a search with `expand=renderedFields` and `expand=changelog`, a search that holds a
key that does not exist (HTTP 400 for the whole query, as Jira answers it), `fields=*all`, a saved filter in the JQL (`filter = ID`),
and the saved filters themselves. What it does not handle goes to the built-in routes of `MockJira`."""
from __future__ import annotations

import copy
import html
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from .mock_jira import MockJira, _Handler

_MENTION = re.compile(r"\[~([A-Za-z0-9._-]+)\]")
_MONOSPACE = re.compile(r"\{\{([^{}\n]+)\}\}")
_BOLD = re.compile(r"(?<![\w*])\*([^*\s][^*\n]*?)\*(?![\w*])")
_ITALIC = re.compile(r"(?<![\w_])_([^_\s][^_\n]*?)_(?![\w_])")
_KEYS_IN = re.compile(r"\bkey\s+in\s*\(([^)]*)\)", re.IGNORECASE)
_FILTER = re.compile(r"\bfilter\s*=\s*(\d+)", re.IGNORECASE)
_CODE_MARK = re.compile(r"^\{(?:code|noformat)(?::[^}]*)?\}$")


def render_wiki(text: str, base_url: str) -> str:
    """The HTML that Jira renders from wiki markup, for the parts that the tests use: paragraphs (a line end is `<br/>`), headings, lists,
    code blocks, bold, italic, monospace, and a mention as the link to the profile that Jira writes (with the display name)."""

    def mention(found: re.Match[str]) -> str:
        name = found.group(1)
        display = "Jane Doe" if name == "jdoe" else name
        return f'<a href="{base_url}/secure/ViewProfile.jspa?name={name}" class="user-hover" rel="{name}">{display}</a>'

    def inline(line: str) -> str:
        line = html.escape(line, quote=False)
        line = _MONOSPACE.sub(r"<tt>\1</tt>", line)
        line = _BOLD.sub(r"<b>\1</b>", line)
        line = _ITALIC.sub(r"<em>\1</em>", line)
        return _MENTION.sub(mention, line)

    parts: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    kind = ""
    code: list[str] | None = None

    def flush() -> None:
        nonlocal kind
        if paragraph:
            parts.append("<p>" + "<br/>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            parts.append(f"<{kind}>" + "".join(f"<li>{item}</li>" for item in items) + f"</{kind}>")
            items.clear()
        kind = ""

    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if _CODE_MARK.match(line.strip()):
            if code is None:
                flush()
                code = []
            else:
                parts.append('<div class="code panel"><pre>' + html.escape("\n".join(code), quote=False) + "</pre></div>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        heading = re.match(r"^h([1-6])\.\s+(.*)$", line)
        bullet = re.match(r"^([*#])\s+(.*)$", line)
        if heading:
            flush()
            parts.append(f"<h{heading.group(1)}>{inline(heading.group(2))}</h{heading.group(1)}>")
        elif bullet:
            wanted = "ul" if bullet.group(1) == "*" else "ol"
            if paragraph or (kind and kind != wanted):
                flush()
            kind = wanted
            items.append(inline(bullet.group(2)))
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            paragraph.append(inline(line))
    flush()
    return "".join(parts)


class ReadingRoutes:
    def __init__(self, jira: "MockJira") -> None:
        self.jira = jira
        self.filters: dict[str, dict[str, Any]] = {}  # by id: id, name, description, jql, favourite and the owner
        self.histories: dict[str, list[dict[str, Any]]] = {}  # by issue key: the entries of the changelog, oldest first
        self.changelog_limit: int | None = None  # Jira Data Center sends at most this many entries of a changelog (100) and says how many it holds

    # -- the routes ------------------------------------------------------------------------------

    def handle(self, handler: "_Handler", method: str, path: str, query: dict[str, list[str]], body: Any) -> bool:
        """Answer the request with `handler.reply(...)` and return True, or return False when it is not for this area.

        `path` is below the context path and keeps its `/rest/...` prefix; `query` holds the decoded parameters.
        """
        if method != "GET":
            return False
        if path == "/rest/api/2/filter/favourite":
            handler.reply(200, [self._filter_json(item) for item in self.filters.values() if item.get("favourite")])
            return True
        found = re.fullmatch(r"/rest/api/2/filter/([^/]+)", path)
        if found:
            return self._filter(handler, found.group(1))
        if path == "/rest/api/2/search":
            return self._search(handler, query)
        found = re.fullmatch(r"/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)", path)
        if found and found.group(1) in self.jira.issues and self._expand(query):
            handler.reply(200, self._issue_json(found.group(1), query))
            return True
        return False

    @staticmethod
    def _expand(query: dict[str, list[str]]) -> set[str]:
        return {name.strip() for item in query.get("expand", []) for name in item.split(",") if name.strip()} & {"renderedFields", "changelog"}

    @staticmethod
    def _wants_every_field(query: dict[str, list[str]]) -> bool:
        return any(name in ("*all", "*navigable") for item in query.get("fields", []) for name in item.split(","))

    def _filter(self, handler: "_Handler", filter_id: str) -> bool:
        if filter_id not in self.filters:
            handler.reply(404, {"errorMessages": [f"The filter with id '{filter_id}' does not exist."], "errors": {}})
            return True
        handler.reply(200, self._filter_json(self.filters[filter_id]))
        return True

    def _filter_json(self, item: dict[str, Any]) -> dict[str, Any]:
        from .mock_jira import USER

        base = self.jira.base_url
        return {
            "self": f"{base}/rest/api/2/filter/{item['id']}",
            "id": item["id"],
            "name": item["name"],
            "description": item.get("description", ""),
            "owner": item.get("owner") or USER,
            "jql": item["jql"],
            "viewUrl": f"{base}/secure/IssueNavigator.jspa?mode=hide&requestId={item['id']}",
            "searchUrl": f"{base}/rest/api/2/search?jql={quote(item['jql'])}",
            "favourite": bool(item.get("favourite")),
            "sharePermissions": [],
            "editable": True,
        }

    # -- issues and searches ---------------------------------------------------------------------

    def _issue_json(self, key: str, query: dict[str, list[str]]) -> dict[str, Any]:
        """An issue as `GET /issue/{key}` and the search answer it: the fields asked for (every field for `*all`), the comments that were
        added by a write, and the expansions."""
        issue = self.jira.issues[key]
        shown = copy.deepcopy(issue["fields"] if self._wants_every_field(query) else self.jira.select(issue["fields"], query))
        if "comment" in shown:
            shown["comment"]["comments"] += copy.deepcopy(self.jira.comments.get(key, []))
            shown["comment"]["total"] = len(shown["comment"]["comments"])
        data: dict[str, Any] = {"id": issue["id"], "key": key, "fields": shown}
        expand = self._expand(query)
        if "renderedFields" in expand:
            data["renderedFields"] = self._rendered(issue["fields"])
        if "changelog" in expand:
            data["changelog"] = self._changelog(key)
        return data

    def _rendered(self, fields: dict[str, Any]) -> dict[str, Any]:
        from .mock_jira import ACCEPT

        base = self.jira.base_url
        return {"description": render_wiki(fields.get("description") or "", base), ACCEPT: render_wiki(fields.get(ACCEPT) or "", base)}

    def _changelog(self, key: str) -> dict[str, Any]:
        histories = self.histories.get(key, [])
        sent = histories[: self.changelog_limit] if self.changelog_limit else histories
        return {"startAt": 0, "maxResults": self.changelog_limit or len(histories), "total": len(histories), "histories": copy.deepcopy(sent)}

    def _search(self, handler: "_Handler", query: dict[str, list[str]]) -> bool:
        jira = self.jira
        jql = query.get("jql", [""])[0]
        asked = _KEYS_IN.search(jql)
        unknown = [key for key in _key_list(asked.group(1)) if key not in jira.issues] if asked else []
        filtered = _FILTER.search(jql)
        if not (unknown or filtered or self._expand(query) or self._wants_every_field(query)):
            return False
        if unknown:
            handler.reply(400, {"errorMessages": [f"An issue with key '{unknown[0]}' does not exist for field 'key'."], "errors": {}})
            return True
        if filtered:
            stored = self.filters.get(filtered.group(1))
            if stored is None:
                handler.reply(400, {"errorMessages": [f"The value '{filtered.group(1)}' does not exist for the field 'filter'."], "errors": {}})
                return True
            from .mock_jira import _split_order

            condition, order = _split_order(stored["jql"])
            jql = jql[: filtered.start()] + f"({condition})" + jql[filtered.end() :]
            if order and "order by" not in jql.lower():
                jql += f" ORDER BY {order}"
        keys = jira.matching(jql)
        start = int(query.get("startAt", ["0"])[0])
        size = min(int(query.get("maxResults", ["50"])[0]), jira.page_cap)
        handler.reply(
            200,
            {"startAt": start, "maxResults": size, "total": len(keys), "issues": [self._issue_json(key, query) for key in keys[start : start + size]]},
        )
        return True


def _key_list(text: str) -> list[str]:
    return [item.strip().strip("\"'").upper() for item in text.split(",") if item.strip()]


# -- helpers for the tests ---------------------------------------------------------------------------


def reading_routes(jira: "MockJira") -> ReadingRoutes:
    """The routes of this area on a mock Jira, to seed them: `reading_routes(jira).changelog_limit = 2`."""
    return next(item for item in jira.extensions if isinstance(item, ReadingRoutes))


def add_filter(
    jira: "MockJira", filter_id: str, name: str, jql: str, *, description: str = "", favourite: bool = True, owner: dict[str, Any] | None = None
) -> None:
    """A saved filter that `/filter/favourite` lists (when it is a favourite), `/filter/{id}` returns and `filter = ID` resolves."""
    reading_routes(jira).filters[filter_id] = {"id": filter_id, "name": name, "description": description, "jql": jql, "favourite": favourite, "owner": owner}


def add_history(jira: "MockJira", key: str, created: str, *changes: tuple[str, ...], author: dict[str, Any] | None = None) -> None:
    """One entry of the changelog of an issue: the time and the changes as `(field, from, to)`, or `(field, from, to, fieldtype)` for a
    field that is not one of Jira's own (`custom`; the others are `jira`). Jira names the author in it."""
    from .mock_jira import USER

    histories = reading_routes(jira).histories.setdefault(key, [])
    histories.append(
        {
            "id": str(10000 + len(histories)),
            "author": author or USER,
            "created": created,
            "items": [
                {"field": change[0], "fieldtype": change[3] if len(change) > 3 else "jira", "from": None, "fromString": change[1], "to": None, "toString": change[2]}
                for change in changes
            ],
        }
    )
