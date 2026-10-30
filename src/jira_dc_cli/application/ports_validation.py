"""Port operations for validation: part of `JiraGateway` (see ports.py), kept in a file of its own."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.validation import FieldRules, TransitionScreen


class ValidationPort(ABC):
    """What the application asks of Jira to check values before they are sent, besides `create_metadata` of `JiraGateway`."""

    @abstractmethod
    def get_edit_metadata(self, key: str) -> FieldRules:
        """The fields of the edit screen of the issue by field id, with what each allows (`GET /issue/{key}/editmeta`)."""

    @abstractmethod
    def get_create_fields(self, project_key: str, issue_type_id: str) -> FieldRules:
        """The fields that an issue of this type can be created with, by field id: the `values` of `create_metadata` for the type."""

    @abstractmethod
    def list_transition_screens(self, key: str) -> list[TransitionScreen]:
        """The transitions that are possible now, each with the fields of its screen (`expand=transitions.fields`)."""
