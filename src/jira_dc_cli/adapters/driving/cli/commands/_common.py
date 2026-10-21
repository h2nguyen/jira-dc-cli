"""Helpers shared by the command modules."""
from __future__ import annotations

import json
from typing import Any

from .....domain.errors import UsageError
from ..runtime import Context


def text_option(ctx: Context, text: str | None, file: str | None, what: str) -> str | None:
    """Text given inline or from a file (`-` is standard input). None when neither was given."""
    if text is not None and file is not None:
        raise UsageError(f"Use either the {what} text or its file, not both.")
    if file is not None:
        return ctx.read_input(file)
    return text


QUOTES_HINT = (
    "Windows PowerShell 5.1 removes the double quotes inside an argument, so JSON typed on the command line arrives broken. "
    "Put the JSON in a file and pass it in single quotes: --fields-json '@fields.json' (a bare @name is splatting in PowerShell)."
)
POWERSHELL_JSON_NOTE = (
    "In Windows PowerShell 5.1 put JSON in a file and pass it in single quotes, --fields-json '@fields.json': PowerShell "
    "removes double quotes inside an argument and reads a bare @name as splatting."
)


def fields_json_option(ctx: Context, value: str | None) -> dict[str, Any]:
    """`--fields-json` takes JSON, or `@FILE` to read it from a file (`@-` is standard input)."""
    from .....domain.drafts import parse_json_object

    if not value:
        return {}
    source = ctx.read_input(value[1:]) if value.startswith("@") else value
    return parse_json_object(source, "--fields-json", hint=QUOTES_HINT)


def pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
