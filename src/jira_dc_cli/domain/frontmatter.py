"""The two file formats the CLI imports: Markdown tickets with front matter, and CSV rows."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .errors import ValidationError


@dataclass(frozen=True)
class MarkdownTicket:
    meta: dict[str, str]
    body: str

    @property
    def description(self) -> str:
        return self.meta.get("description", self.body)


def parse_markdown_ticket(text: str) -> MarkdownTicket:
    """Front matter is a `---` block of `key: value` lines; the rest is the description."""
    meta: dict[str, str] = {}
    text = text.replace("\r\n", "\n")
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ValidationError("missing closing --- in front matter")
        head = text[4:end]
        body = text[end + 5 :].strip()
        for raw in head.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if ":" not in raw:
                raise ValidationError(f"invalid front matter line: {raw}")
            key, value = raw.split(":", 1)
            meta[key.strip()] = value.strip().strip('"').strip("'")
    return MarkdownTicket(meta, body)


@dataclass(frozen=True)
class CsvRow:
    line: int
    values: dict[str, str]

    def get(self, *names: str) -> str:
        for name in names:
            if self.values.get(name):
                return self.values[name]
        return ""


def parse_csv(text: str) -> list[CsvRow]:
    """One row per issue. `line` counts the header as line 1, like a spreadsheet."""
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = []
    for line_no, row in enumerate(reader, start=2):
        clean = {str(key).strip(): (value or "") for key, value in row.items() if key is not None}
        rows.append(CsvRow(line_no, clean))
    return rows
