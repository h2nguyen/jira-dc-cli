"""Time tracking as Jira reads it (`1h 30m`, `2d`, `1w 2d`) and the request that logs work on an issue.

Jira converts days and weeks with the working time of its own configuration (8 hours a day and 5 days a week unless an administrator
changed it), so the text goes to Jira as it is, in Jira's words, and is never turned into seconds here. Only hours and minutes have a
length that no configuration changes: `seconds` is filled for those and is None otherwise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .errors import ValidationError
from .timeutil import parse_timestamp

ADJUST_MODES = ("auto", "leave", "new", "manual")  # what Jira does with the remaining estimate; `auto` is what it does without a word
_ORDER = ("w", "d", "h", "m")
_UNITS = {
    "w": "w", "wk": "w", "wks": "w", "week": "w", "weeks": "w",
    "d": "d", "day": "d", "days": "d",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "m": "m", "min": "m", "mins": "m", "minute": "m", "minutes": "m",
}
_PART = re.compile(r"([0-9]+)\s*([A-Za-z]+)")
_AGO = re.compile(r"^([0-9]{1,4})\s*([mhdw])$", re.IGNORECASE)
_MAX_TEXT = 80
_MAX_DIGITS = 5
_TIME_HINT = (
    "Write whole numbers with w, d, h and m, for example 1h 30m, 2d or 1w 2d. "
    "A number without a unit, a decimal such as 1.5h and seconds are not accepted."
)


@dataclass(frozen=True)
class TimeText:
    text: str  # in Jira's words, the units once each in the order w d h m: `1w 2d 3h 4m`
    seconds: int | None  # the length when the text holds only hours and minutes; None with days or weeks, which Jira converts


@dataclass(frozen=True)
class WorklogRequest:
    """What logging work sends: the JSON body and the query (how Jira adjusts the remaining estimate), and a readable length."""

    body: dict[str, Any]
    query: dict[str, str]
    seconds: int | None


def _shown(text: str) -> str:
    return text if len(text) <= 40 else text[:40] + "..."


def parse_time_text(text: str, *, what: str = "the time", allow_zero: bool = False) -> TimeText:
    """`1h 30m`, `90m`, `2d`, `1w 2d` and the long words (`2 days 3 hours`) as the text that is sent to Jira.

    Units are required: Jira reads a bare number in the default unit of its configuration, which this CLI cannot know."""
    value = (text or "").strip()
    failure = ValidationError(f"{what} is not a length of time: '{_shown(value)}'.", hint=_TIME_HINT)
    if not value or len(value) > _MAX_TEXT:
        raise failure  # before any pattern runs: a hostile text of a million characters costs nothing
    found = list(_PART.finditer(value))
    leftover = _PART.sub("", value).replace(",", "").strip()
    if not found or leftover:
        raise failure
    amounts: dict[str, int] = {}
    for part in found:
        unit = _UNITS.get(part.group(2).lower())
        if unit is None or unit in amounts or len(part.group(1)) > _MAX_DIGITS:
            raise failure
        amounts[unit] = int(part.group(1))
    if not allow_zero and not any(amounts.values()):
        raise ValidationError(f"{what} must be more than zero: '{_shown(value)}'.", hint=_TIME_HINT)
    canonical = " ".join(f"{amounts[unit]}{unit}" for unit in _ORDER if unit in amounts)
    seconds = None if "w" in amounts or "d" in amounts else amounts.get("h", 0) * 3600 + amounts.get("m", 0) * 60
    return TimeText(canonical, seconds)


def format_seconds(seconds: int) -> str:
    """`5400` as `1h 30m`: hours and minutes of the clock, which no working-time configuration changes."""
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes = rest // 60
    parts = ([f"{hours}h"] if hours else []) + ([f"{minutes}m"] if minutes else [])
    return " ".join(parts) or "0m"


def parse_since(text: str, now: datetime) -> datetime:
    """The moment that `--since` names: `30m`, `12h`, `2d` or `1w` ago (clock time, not working time), or an ISO 8601 date or time."""
    value = (text or "").strip()
    ago = _AGO.match(value)
    if ago:
        unit = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[ago.group(2).lower()]
        return now - timedelta(**{unit: int(ago.group(1))})
    if len(value) <= _MAX_TEXT:  # a longer text is no time, and nothing is run on it
        try:
            return parse_timestamp(value)
        except ValidationError:
            pass
    raise ValidationError(
        f"--since is neither a time ago nor a date: '{_shown(value)}'.",
        hint="Write 2d, 12h, 1w or 30m for a time ago, or a date such as 2026-10-15 or 2026-10-15T08:00:00+02:00.",
    )


def jira_timestamp(moment: datetime) -> str:
    """`2026-10-15T08:30:00.000+0200`: the form in which a worklog's `started` is read; an offset with a colon is refused."""
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}{moment:%z}"


def _estimate_query(adjust: str, new_estimate: str, reduce_by: str) -> dict[str, str]:
    mode = (adjust or "").strip().lower()
    if mode and mode not in ADJUST_MODES:
        raise ValidationError(f"--adjust-estimate is one of {', '.join(ADJUST_MODES)}, not '{_shown(adjust)}'.")
    if new_estimate and mode != "new":
        raise ValidationError("--new-estimate belongs to --adjust-estimate new.", hint="Add --adjust-estimate new, or leave --new-estimate out.")
    if reduce_by and mode != "manual":
        raise ValidationError("--reduce-by belongs to --adjust-estimate manual.", hint="Add --adjust-estimate manual, or leave --reduce-by out.")
    if mode == "new":
        if not new_estimate:
            raise ValidationError("--adjust-estimate new needs --new-estimate.", hint="Example: --adjust-estimate new --new-estimate 2d")
        return {"adjustEstimate": "new", "newEstimate": parse_time_text(new_estimate, what="--new-estimate", allow_zero=True).text}
    if mode == "manual":
        if not reduce_by:
            raise ValidationError("--adjust-estimate manual needs --reduce-by.", hint="Example: --adjust-estimate manual --reduce-by 1h")
        return {"adjustEstimate": "manual", "reduceBy": parse_time_text(reduce_by, what="--reduce-by").text}
    return {"adjustEstimate": mode} if mode else {}


def worklog_request(
    time_spent: str, *, comment: str = "", started: str = "", adjust: str = "", new_estimate: str = "", reduce_by: str = ""
) -> WorklogRequest:
    """The one place that builds what `log-work` sends, so that the dry run shows exactly the call. Nothing that is not asked for is sent:
    without `started` Jira uses the moment of the call, without `adjust` it reduces the remaining estimate itself."""
    spent = parse_time_text(time_spent, what="TIME")
    body: dict[str, Any] = {"timeSpent": spent.text}
    if comment.strip():
        body["comment"] = comment.strip()
    if started.strip():
        body["started"] = jira_timestamp(parse_timestamp(started))
    return WorklogRequest(body, _estimate_query(adjust, new_estimate, reduce_by), spent.seconds)
