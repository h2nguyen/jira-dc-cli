"""`--select` and `--format` for the commands that print a list: keep some keys, or print a table, TSV or Markdown.

A command that prints a list builds its rows as plain dicts (the same shape as its JSON) and hands them to `print_list`:

    print_list(ctx, args, payload=document, rows=rows, list_key="issues", columns=DEFAULT_COLUMNS, plain=lambda: issues_text(result))

`payload` is the JSON document (the rows sit under `list_key`; `None` when the document is the list itself) and `plain` is the
command's own text. The helper decides which of them to print, so that every list verb behaves the same.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

from ....domain.errors import UsageError
from .render import number, table
from .runtime import Context
from .spec import Arg

SELECT = Arg(
    ("--select",),
    "Keep only these keys per item (a,b.c: a dot reaches into an object). With --format they are the columns.",
    metavar="a,b.c",
)
FORMAT = Arg(
    ("--format",),
    "table (the default), tsv (with a header line) or md (a Markdown table). --json wins over the default.",
    kind="choice",
    choices=("table", "tsv", "md"),
    default="table",
)
Column = tuple[str, str]  # (header, dotted path of the value in a row)


def split_select(spec: str | None) -> list[str]:
    return [name.strip() for name in (spec or "").split(",") if name.strip()]


def _walk(value: Any, parts: Sequence[str]) -> Any:
    """The value at a dotted path. A list is walked item by item; a missing key gives None."""
    if not parts:
        return value
    if isinstance(value, list):
        return [_walk(item, parts) for item in value]
    if isinstance(value, Mapping):
        return _walk(value.get(parts[0]), parts[1:]) if parts[0] in value else None
    return None


def _paths(value: Any, prefix: str = "", depth: int = 0) -> set[str]:
    found: set[str] = set()
    if isinstance(value, list):
        for item in value[:50]:
            found |= _paths(item, prefix, depth)
    elif isinstance(value, Mapping) and depth < 4:
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.add(path)
            found |= _paths(child, path, depth + 1)
    return found


def known_paths(rows: Sequence[Any]) -> list[str]:
    found: set[str] = set()
    for row in rows[:50]:
        found |= _paths(row)
    return sorted(found)


def _check_names(names: Sequence[str], rows: Sequence[Any]) -> None:
    if not rows:
        return  # nothing to compare with: an empty list stays empty
    valid = known_paths(rows)
    unknown = [name for name in names if name not in valid]
    if unknown:
        raise UsageError(
            f"Unknown name in --select: {', '.join(unknown)}.",
            hint="Valid names: " + ", ".join(valid) + ".",
        )


def _put(target: dict[str, Any], parts: Sequence[str], value: Any) -> None:
    head, *rest = parts
    if not rest:
        target[head] = value
        return
    child = target.setdefault(head, {})
    if isinstance(child, dict):
        _put(child, rest, value)


def _pick(row: Any, names: Sequence[str]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for name in names:
        parts = name.split(".")
        value = _walk(row, parts)
        if isinstance(row, Mapping) and isinstance(row.get(parts[0]), list) and len(parts) > 1:
            # a list of objects keeps its shape: sprints.name -> sprints: [{"name": ...}]
            items = row.get(parts[0]) or []
            existing = kept.setdefault(parts[0], [{} for _ in items])
            for slot, item in zip(existing, items):
                _put(slot, parts[1:], _walk(item, parts[1:]))
            continue
        _put(kept, parts, value)
    return kept


def selected_json(payload: Any, select: str | None, list_key: str | None = None) -> Any:
    """The JSON document with only the selected keys per item; unchanged when nothing was selected."""
    names = split_select(select)
    if not names:
        return payload
    rows = payload if list_key is None else payload.get(list_key)
    if not isinstance(rows, list):
        raise UsageError("--select needs a list: this answer has none.", hint="Use it with a command that prints a list.")
    _check_names(names, rows)
    picked = [_pick(row, names) for row in rows]
    if list_key is None:
        return picked
    return {**payload, list_key: picked}


def cell(value: Any) -> str:
    """A value as one line of text for a table cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return number(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(cell(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return " ".join(str(value).split())


def structured_text(rows: Sequence[Mapping[str, Any]], columns: Sequence[Column], select: str | None, fmt: str) -> str:
    """The rows as a table, TSV or Markdown. With `--select` the columns are the selected paths."""
    names = split_select(select)
    if names:
        _check_names(names, rows)
        columns = [(name, name) for name in names]
    headers = [header for header, _ in columns]
    body = [[cell(_walk(row, path.split("."))) for _, path in columns] for row in rows]
    if fmt == "tsv":
        return "\n".join("\t".join(line) for line in [headers, *body])
    if fmt == "md":
        escape = lambda text: text.replace("|", "\\|")  # noqa: E731
        lines = ["| " + " | ".join(escape(item) for item in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        lines += ["| " + " | ".join(escape(item) for item in line) + " |" for line in body]
        return "\n".join(lines)
    return table(headers, body)


def print_list(
    ctx: Context,
    args: Any,
    *,
    payload: Any,
    rows: Sequence[Mapping[str, Any]],
    list_key: str | None,
    columns: Sequence[Column],
    plain: Callable[[], str],
) -> None:
    """Print the result of a list command: JSON, a structured text (`--format`, `--select`) or the command's own text."""
    fmt = getattr(args, "format", "table") or "table"
    select = getattr(args, "select", None)
    if fmt != "table" and ctx.json_flag:
        raise UsageError(f"--json and --format {fmt} cannot be combined.", hint="Use one of them.")
    if ctx.json_mode and fmt == "table":
        ctx.print_json(selected_json(payload, select, list_key))
    elif fmt != "table" or select:
        ctx.print(structured_text(rows, columns, select, fmt))
    else:
        ctx.print(plain())
