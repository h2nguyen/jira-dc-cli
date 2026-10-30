"""Lengths of time written as people write them: `90s`, `10m`, `2h`, `3d`, `1w`."""
from __future__ import annotations

import re

from .errors import ValidationError

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 7 * 86400}
_DURATION = re.compile(r"^\s*([0-9]{1,9}(?:\.[0-9]{1,9})?)\s*([smhdw]?)\s*$", re.IGNORECASE)


def parse_duration(text: str, *, default_unit: str = "s") -> float:
    """The seconds in `10m`, `2h`, `1.5h` or `90` (a bare number counts in `default_unit`)."""
    match = _DURATION.match(text or "")
    if not match:
        shown = text if len(text or "") <= 40 else text[:39] + "…"
        raise ValidationError(f"Not a length of time: '{shown}'.", hint="Write a number and a unit: 90s, 10m, 2h, 3d or 1w.")
    return float(match.group(1)) * _UNITS[(match.group(2) or default_unit).lower()]
