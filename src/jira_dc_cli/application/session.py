"""Use cases around the CLI's own configuration and a quick connection check."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..domain.errors import UsageError, ValidationError
from ..domain.model import Project, User, parse_project_key
from .ports import ConfigStore, JiraGateway
from .settings import SETTING_DEFS, Settings, mask_secret, setting_definition


@dataclass(frozen=True)
class ConfigEntry:
    name: str
    value: str
    source: str  # environment | config file | default
    description: str
    secret: bool = False


class ConfigService:
    def __init__(
        self, store: ConfigStore, settings: Settings, environ: Mapping[str, str], gateway: JiraGateway
    ) -> None:
        self._store = store
        self._settings = settings
        self._environ = environ
        self._gateway = gateway

    @property
    def path(self) -> str:
        return self._store.path

    def entries(self) -> list[ConfigEntry]:
        stored = self._store.load()
        rows = []
        for definition in SETTING_DEFS:
            if self._environ.get(definition.name):
                value, source = self._environ[definition.name], "environment"
            elif definition.name in stored:
                value, source = stored[definition.name], "config file"
            else:
                value, source = str(definition.default), "default"
            if definition.kind == "bool" and source == "default":
                value = "false" if not definition.default else "true"
            shown = mask_secret(value) if definition.secret else value
            rows.append(ConfigEntry(definition.name, shown, source, definition.description, definition.secret))
        return rows

    def set_value(self, name: str, value: str) -> None:
        definition = setting_definition(name)
        if definition is None:
            known = ", ".join(sorted(item.name for item in SETTING_DEFS if not item.secret))
            raise UsageError(f"Unknown setting: {name}", hint=f"Known settings: {known}")
        if definition.secret:
            raise UsageError(
                f"{name} is a secret and is not set on the command line (it would stay in the shell history).",
                hint="Use `jira setup-pat`.",
            )
        Settings.from_mapping({name: value})  # rejects a value of the wrong type
        self._store.set(name, value)

    def unset_value(self, name: str) -> bool:
        if setting_definition(name) is None:
            raise UsageError(f"Unknown setting: {name}")
        return self._store.unset(name)

    def save_pat(self, pat: str) -> None:
        token = pat.strip()
        if not token:
            raise ValidationError("The token is empty.", hint="Paste the personal access token from your Jira profile.")
        self._store.set("JIRA_PAT", token)

    def remove_pat(self) -> bool:
        return self._store.unset("JIRA_PAT")

    def select_project(self, key: str) -> Project:
        project = self._gateway.get_project(parse_project_key(key))  # proves access before saving
        self._store.set("JIRA_PROJECT", project.key)
        return project

    def current_project(self) -> str:
        return self._settings.project

    def clear_project(self) -> bool:
        return self._store.unset("JIRA_PROJECT")


@dataclass(frozen=True)
class PingResult:
    ok: bool
    user: User | None


class Ping:
    def __init__(self, gateway: JiraGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def execute(self, *, quiet: bool = False) -> PingResult:
        user = self._gateway.current_user()
        return PingResult(True, None if quiet or self._settings.hide_users else user)
