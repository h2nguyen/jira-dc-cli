"""Printing: tables, key/value blocks and JSON (stable camelCase keys for scripts and agents)."""
from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence


def camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


def to_jsonable(value: Any) -> Any:
    """Dataclasses become dicts with camelCase keys; fields marked `json: False` are left out."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            camel(item.name): to_jsonable(getattr(value, item.name))
            for item in fields(value)
            if item.metadata.get("json", True)
        }
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def dumps_json(value: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *, indent: str = "") -> str:
    """Left-aligned columns separated by two spaces; the last column is not padded."""
    body = [[one_line(cell) for cell in row] for row in rows]
    widths = [max([len(headers[i])] + [len(row[i]) for row in body]) for i in range(len(headers))]

    def line(cells: Sequence[str]) -> str:
        padded = [cell.ljust(widths[i]) for i, cell in enumerate(cells[:-1])] + [cells[-1]]
        return (indent + "  ".join(padded)).rstrip()

    return "\n".join([line(list(headers)), *(line(row) for row in body)])


def key_values(pairs: Iterable[tuple[str, Any]], *, indent: str = "") -> str:
    """`Label:  value` lines with aligned values; empty values are skipped."""
    shown = [(label, str(value)) for label, value in pairs if value not in ("", None)]
    if not shown:
        return ""
    width = max(len(label) for label, _ in shown) + 2
    return "\n".join(f"{indent}{(label + ':').ljust(width)}{value}" for label, value in shown)


def number(value: float) -> str:
    return f"{value:g}"
