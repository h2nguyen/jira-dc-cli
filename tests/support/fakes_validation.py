"""The validation calls of `FakeGateway`, in memory."""
from __future__ import annotations

from jira_dc_cli.domain.errors import NotFoundError
from jira_dc_cli.domain.validation import Allowed, FieldRule, FieldRules, TransitionScreen


def names(*texts: str) -> tuple[Allowed, ...]:
    """Allowed values that have an id (their position) and a name."""
    return tuple(Allowed(str(number), text) for number, text in enumerate(texts, 1))


class FakeValidation:
    """Implements `ValidationPort` for the tests; its state lives on the gateway and is set up in `_init_validation`.

    By default Jira offers a few values for the priority, the components, the fix versions and the resolution, requires nothing but the
    summary, and has no screen for any transition. A test changes `allowed_values`, `required_to_create` (by issue type id) and `screens`
    (by transition id) to make a project stricter."""

    def _init_validation(self) -> None:
        """Called at the end of `FakeGateway.__init__`."""
        self.allowed_values: dict[str, tuple[Allowed, ...]] = {
            "priority": names("Highest", "High", "Medium", "Low", "Lowest"),
            "components": names("Demo", "Backend"),
            "fixVersions": names("Release 1.0", "Release 2.0"),
            "resolution": names("Fixed", "Won't Fix", "Duplicate"),
        }
        self.required_to_create: dict[str, list[FieldRule]] = {}
        self.screens: dict[str, list[FieldRule]] = {}

    def _standard_rules(self) -> FieldRules:
        labels = {"priority": "Priority", "components": "Component/s", "fixVersions": "Fix Version/s", "resolution": "Resolution"}
        rules = {"summary": FieldRule("summary", "Summary", required=True)}
        for field_id, values in self.allowed_values.items():
            rules[field_id] = FieldRule(field_id, labels.get(field_id, field_id), allowed=values)
        return rules

    def get_edit_metadata(self, key: str) -> FieldRules:
        self._enter("get_edit_metadata", key)
        self._issue(key)
        rules = self._standard_rules()
        del rules["resolution"]  # the edit screen of an issue has no resolution
        return rules

    def get_create_fields(self, project_key: str, issue_type_id: str) -> FieldRules:
        self._enter("get_create_fields", project_key, issue_type_id)
        if project_key not in self.projects:
            raise NotFoundError(f"Not found: GET /issue/createmeta/{project_key}/issuetypes/{issue_type_id}")
        rules = self._standard_rules()
        del rules["resolution"]
        rules.update({rule.field_id: rule for rule in self.required_to_create.get(issue_type_id, [])})
        return rules

    def list_transition_screens(self, key: str) -> list[TransitionScreen]:
        self._enter("list_transition_screens", key)
        issue = self._issue(key)
        possible = self.transitions[issue.status.name]  # type: ignore[union-attr]
        return [TransitionScreen(item, tuple(self.screens.get(item.id, ()))) for item in possible]
