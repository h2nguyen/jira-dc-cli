"""Helpers shared by the command modules."""
from __future__ import annotations

import json
from typing import Any

from .....domain.errors import UsageError, ValidationError
from ..runtime import Context


def text_option(ctx: Context, text: str | None, file: str | None, what: str) -> str | None:
    """Text given inline or from a file (`-` is standard input). None when neither was given."""
    if text is not None and file is not None:
        raise UsageError(f"Use either the {what} text or its file, not both.")
    if file is not None:
        return ctx.read_input(file)
    return text


def replacing_text_option(ctx: Context, text: str | None, file: str | None, what: str) -> str | None:
    """Like `text_option`, for a text that REPLACES what Jira holds (`update --description-file`). An empty file or empty standard
    input is refused: it is nearly always a mistake (an editor that saved nothing, a pipe that failed), and it would clear the text.
    An empty value given inline (`--description ''`) clears it on purpose."""
    value = text_option(ctx, text, file, what)
    if file is not None and value is not None and not value.strip():
        raise UsageError(
            f"The {what} is empty: {'standard input' if file == '-' else file} holds no text.",
            hint=f"Check the file or the pipe. To clear the {what} on purpose, give --{what} '' on the command line.",
        )
    return value


QUERY_NOTE = (
    "In Windows PowerShell 5.1 a query with double quotes (\"Epic Link\" = X) arrives broken, because PowerShell removes them "
    "inside an argument: put the query in a file and pass it in single quotes, '@query.jql' (a bare @name is splatting in "
    "PowerShell), or pipe it in with `-`."
)


QUERY_FILE_LIMIT = 1024 * 1024  # a query is some lines long; a bigger file is a mistake (for example a log), not a query
AT_HINT = "A value that starts with @ is read from the file of that name. To use a text that starts with @, put a space in front of it (or use --phrase)."


def query_option(ctx: Context, value: str | None, what: str = "query") -> str | None:
    """A query (JQL or search text) given inline, as `@FILE` (read from a file) or as `-` (read from standard input).

    A file is the form that survives every shell: Windows PowerShell 5.1 removes the double quotes inside an argument. The file
    may be UTF-8, UTF-8 with a byte order mark or UTF-16, which is what PowerShell writes. None when no query was given."""
    if value is None:
        return None
    if value in ("-", "@-"):
        text = ctx.read_stdin()
    elif value.startswith("@") and len(value) > 1:
        text = _read_query_file(ctx, value[1:], what)
    else:
        return value
    query = text.strip()
    if not query:
        raise UsageError(f"The {what} is empty: {'standard input' if value in ('-', '@-') else value[1:]} holds no text.")
    return query


def _read_query_file(ctx: Context, path: str, what: str) -> str:
    from .....domain.textio import decode_text

    files = ctx.app.files
    try:
        size = files.file_size(path)
        if size > QUERY_FILE_LIMIT:
            raise UsageError(f"{path} is {size:,} bytes: a file with a {what} may hold at most {QUERY_FILE_LIMIT:,} bytes.")
        return decode_text(files.read_bytes(path))
    except ValidationError as error:
        raise ValidationError(error.message, hint=error.hint or AT_HINT) from error


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
