"""Ports: what the application needs from the outside world.

The use cases depend on these interfaces only. Adapters implement them (Jira over HTTP, a config
file, the file system) and tests replace them with fakes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..domain.model import (
    Comment,
    CreatedIssue,
    FieldInfo,
    Issue,
    IssueTypeInfo,
    LinkType,
    Project,
    SearchPage,
    Transition,
    User,
)
from .ports_agile import AgilePort
from .ports_attachments import AttachmentsPort
from .ports_collab import CollabPort
from .ports_comments import CommentsPort
from .ports_reading import ReadingPort
from .ports_reference import ReferencePort
from .ports_users import UsersPort
from .ports_validation import ValidationPort


class JiraGateway(
    AttachmentsPort, AgilePort, CommentsPort, UsersPort, ValidationPort, ReadingPort, ReferencePort, CollabPort, ABC
):
    """Everything the CLI asks of Jira. Field names are the standard Jira ones plus the logical
    names `story_points`, `epic_link`, `sprint` and `acceptance`, which the adapter maps to the
    custom field ids of the instance.

    The operations of a feature area (attachments, agile, comments, ...) are declared in the `ports_<area>.py` file of
    that area and inherited here, so that this file stays readable."""

    @abstractmethod
    def current_user(self) -> User: ...

    @abstractmethod
    def server_info(self) -> dict[str, Any]: ...

    @abstractmethod
    def list_fields(self, *, refresh: bool = False) -> list[FieldInfo]: ...

    @abstractmethod
    def field_ids(self) -> dict[str, str | None]:
        """Custom field id per logical field, None when the instance has no such field."""

    @abstractmethod
    def get_issue(self, key: str, *, fields: Sequence[str] | None = None) -> Issue:
        """`fields=None` loads everything, including comments and links."""

    @abstractmethod
    def search(self, jql: str, *, fields: Sequence[str], start_at: int = 0, max_results: int = 50) -> SearchPage: ...

    @abstractmethod
    def list_transitions(self, key: str) -> list[Transition]: ...

    @abstractmethod
    def transition_issue(self, key: str, payload: dict[str, Any]) -> None:
        """Sends `payload` as it is (see `domain.payloads.transition_payload`): what a dry run shows is what is sent."""

    @abstractmethod
    def list_comments(self, key: str) -> list[Comment]: ...

    @abstractmethod
    def add_comment(self, key: str, payload: dict[str, Any]) -> Comment:
        """Sends `payload` as it is (see `domain.payloads.comment_payload`)."""

    @abstractmethod
    def list_link_types(self) -> list[LinkType]: ...

    @abstractmethod
    def create_link(self, payload: dict[str, Any]) -> None:
        """Sends `payload` as it is (see `domain.payloads.link_payload`)."""

    @abstractmethod
    def create_issue(self, payload: dict[str, Any]) -> CreatedIssue: ...

    @abstractmethod
    def update_issue(self, key: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_projects(self) -> list[Project]: ...

    @abstractmethod
    def get_project(self, key: str) -> Project: ...

    @abstractmethod
    def list_versions(self, project_key: str) -> list[str]: ...

    @abstractmethod
    def has_permission(
        self, project_key: str, permission: str, *, timeout: float | None = None, retries: int | None = None
    ) -> bool: ...

    @abstractmethod
    def list_issue_types(self, project_key: str, *, refresh: bool = False) -> list[IssueTypeInfo]: ...

    @abstractmethod
    def create_metadata(self, project_key: str, issue_type_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    def request(self, method: str, path: str, body: Any = None) -> Any:
        """A raw call below the API root (or, with a path starting with /rest/, below the host)."""


class ConfigStore(ABC):
    """Persistent settings: `KEY=value` pairs."""

    @property
    @abstractmethod
    def path(self) -> str: ...

    @abstractmethod
    def load(self) -> dict[str, str]: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    def unset(self, key: str) -> bool:
        """True when the key was there."""


class Cache(ABC):
    @abstractmethod
    def get(self, namespace: str, key: str, max_age: float) -> str | None: ...

    @abstractmethod
    def put(self, namespace: str, key: str, value: str) -> None: ...


class Files(ABC):
    @abstractmethod
    def read_text(self, path: str, encoding: str = "utf-8") -> str: ...

    @abstractmethod
    def write_text(self, path: str, text: str) -> None:
        """Creates missing parent folders."""

    @abstractmethod
    def read_bytes(self, path: str) -> bytes: ...

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None:
        """Creates missing parent folders; the file appears whole or not at all."""

    @abstractmethod
    def file_size(self, path: str) -> int:
        """The size in bytes of an existing file."""

    @abstractmethod
    def append_text(self, path: str, text: str) -> None: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def is_dir(self, path: str) -> bool: ...

    @abstractmethod
    def list_matching(self, directory: str, pattern: str) -> list[str]:
        """Full paths of the files in `directory` that match the glob `pattern`, sorted by name."""

    @abstractmethod
    def join(self, *parts: str) -> str: ...

    @abstractmethod
    def name(self, path: str) -> str: ...


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """The current time, timezone aware (UTC)."""


class Diagnostics(ABC):
    """Messages for the person at the terminal (or the agent), apart from the result."""

    @abstractmethod
    def info(self, message: str) -> None: ...

    @abstractmethod
    def warn(self, message: str) -> None: ...

    @abstractmethod
    def debug(self, message: str) -> None: ...


@dataclass(frozen=True)
class DocTopic:
    name: str
    title: str


class Docs(ABC):
    """The documentation that ships with the CLI."""

    @abstractmethod
    def topics(self) -> list[DocTopic]: ...

    @abstractmethod
    def topic(self, name: str) -> str: ...

    @abstractmethod
    def readme(self) -> str: ...

    @abstractmethod
    def changelog(self) -> str: ...

    @abstractmethod
    def skill(self) -> str: ...
