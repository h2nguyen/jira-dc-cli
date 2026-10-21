"""Choosing a workflow transition."""
from __future__ import annotations

from typing import Sequence

from .errors import ValidationError
from .model import Transition


def pick_transition(transitions: Sequence[Transition], query: str) -> Transition:
    """An exact ID wins, then the target status, then the transition name (case-insensitive).

    Transition names can differ from status names (`Reopen` leads to `To Do`), which is why the
    target status comes before the name.
    """
    wanted = query.strip().casefold()
    for matches in (
        [t for t in transitions if t.id == query.strip()],
        [t for t in transitions if t.to_status.name.casefold() == wanted],
        [t for t in transitions if t.name.casefold() == wanted],
    ):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            options = ", ".join(f"{t.id}:{t.name}->{t.to_status.name}" for t in matches)
            raise ValidationError(
                f"'{query}' is ambiguous.",
                hint=f"Use the transition ID. Candidates: {options}",
                details={"candidates": [t.id for t in matches]},
            )
    options = ", ".join(f"{t.to_status.name} (via {t.name}, id {t.id})" for t in transitions)
    raise ValidationError(
        f"No transition to '{query}' is available from the current status.",
        hint=f"Available: {options or 'none'}",
        details={"available": [t.id for t in transitions]},
    )
