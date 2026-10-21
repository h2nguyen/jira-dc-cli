"""Errors of the Jira CLI. Every error carries the exit code that the CLI reports for it."""
from __future__ import annotations

from typing import Any


class JiraCliError(Exception):
    exit_code = 1
    code = "ERROR"

    def __init__(self, message: str, *, hint: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}


class UsageError(JiraCliError):
    exit_code = 2
    code = "USAGE"


class ConfigError(JiraCliError):
    code = "CONFIG"


class ValidationError(JiraCliError):
    code = "INVALID"


class ConflictError(JiraCliError):
    exit_code = 3
    code = "CONFLICT"


class NotFoundError(JiraCliError):
    exit_code = 4
    code = "NOT_FOUND"


class AuthError(JiraCliError):
    exit_code = 5
    code = "AUTH"


class RejectedError(JiraCliError):
    exit_code = 6
    code = "REJECTED"


class UnavailableError(JiraCliError):
    exit_code = 7
    code = "UNAVAILABLE"


# (exit code, error codes, meaning) - the single source for the docs and `jira commands --json`.
EXIT_CODES: tuple[tuple[int, str, str], ...] = (
    (0, "OK", "The command succeeded (also for a dry run)."),
    (1, "ERROR, CONFIG, INVALID", "The command failed: bad input, missing configuration or another error."),
    (2, "USAGE", "The command line is wrong: unknown command or option, missing argument."),
    (3, "CONFLICT", "The issue changed in Jira after the time given to --if-unchanged-since, or `pull` and `push` found the same field changed in the file and in Jira."),
    (4, "NOT_FOUND", "Jira has no such issue, project or resource (HTTP 404)."),
    (5, "AUTH", "Jira refused the token or the permission (HTTP 401 or 403)."),
    (6, "REJECTED", "Jira understood the request and refused it (for example HTTP 400); its message is shown."),
    (7, "UNAVAILABLE", "Jira cannot be reached or answered with a server error (network, timeout, HTTP 5xx)."),
)
