"""The policy for people in what the CLI prints, as the commands of the reference and collab areas apply it."""
from __future__ import annotations

from ..domain.errors import ValidationError
from .settings import Settings


def ensure_people_allowed(settings: Settings) -> None:
    """`--with-users` is an error under JIRA_HIDE_USERS=true: the policy forbids people in output, whatever the command line says."""
    if settings.hide_users:
        raise ValidationError(
            "JIRA_HIDE_USERS=true: people are not printed, --with-users is not available.",
            hint="Unset JIRA_HIDE_USERS if you need to see people.",
        )
