"""Settings: every key the CLI understands, with its type, default and meaning.

The definitions are the single source for parsing, `jira config show` and the generated reference.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..domain.errors import ConfigError
from ..domain.fields import ACCEPTANCE, EPIC_LINK, SPRINT, STORY_POINTS


@dataclass(frozen=True)
class SettingDef:
    name: str
    attr: str
    kind: str  # str | bool | int | float
    default: Any
    description: str
    secret: bool = False
    example: str = ""


SETTING_DEFS: tuple[SettingDef, ...] = (
    SettingDef("JIRA_BASE_URL", "base_url", "str", "", "Base URL of Jira, including the context path if there is one.", example="https://jira.example.com/jira"),
    SettingDef("JIRA_PAT", "pat", "str", "", "Personal access token. Save it with `jira setup-pat`; it is never printed.", secret=True),
    SettingDef("JIRA_PROJECT", "project", "str", "", "Current project; used when --project is left out. Set it with `jira use KEY`.", example="PROJ"),
    SettingDef("JIRA_API_PATH", "api_path", "str", "/rest/api/2", "REST API path below the base URL."),
    SettingDef("JIRA_CA_BUNDLE", "ca_bundle", "str", "", "PEM file with the certificate authorities to trust, for a private company CA. Empty: the system store.", example="C:/certs/company-ca.pem"),
    SettingDef("JIRA_INSECURE", "insecure", "bool", False, "Do not verify TLS certificates. Only as a temporary diagnostic."),
    SettingDef("JIRA_ALLOW_HTTP", "allow_http", "bool", False, "Allow a plain http:// JIRA_BASE_URL. The token then travels unencrypted, so use it only on an isolated network. Addresses on this computer (localhost) are always allowed."),
    SettingDef("JIRA_REPORTER", "reporter", "str", "", "User name used as reporter when creating issues. Empty: the token's user."),
    SettingDef("JIRA_HIDE_USERS", "hide_users", "bool", False, "Policy: never print people (names, e-mail addresses). `--with-users` is then refused."),
    SettingDef("JIRA_EPIC_LINK_FIELD", "epic_link_field", "str", "", "Field id of Epic Link. Empty: found by its name.", example="customfield_10002"),
    SettingDef("JIRA_STORY_POINTS_FIELD", "story_points_field", "str", "", "Field id of Story Points. Empty: found by its name.", example="customfield_10001"),
    SettingDef("JIRA_SPRINT_FIELD", "sprint_field", "str", "", "Field id of Sprint. Empty: found by its name."),
    SettingDef("JIRA_ACCEPTANCE_FIELD", "acceptance_field", "str", "", "Field id of the acceptance criteria. Empty: found by its name.", example="customfield_10004"),
    SettingDef("JIRA_CONNECT_TIMEOUT", "connect_timeout", "float", 10.0, "Seconds to wait for the connection."),
    SettingDef("JIRA_MAX_TIME", "max_time", "float", 120.0, "Seconds to wait for an answer."),
    SettingDef("JIRA_RETRY", "retry", "int", 2, "Retries of a read request after a network error or HTTP 429/502/503/504. Writes are never retried."),
    SettingDef("JIRA_OUTPUT", "output", "str", "text", "`json` makes every command that supports it print JSON, as if --json was given. Meant for scripts and agents.", example="json"),
    SettingDef("JIRA_NO_CACHE", "no_cache", "bool", False, "Do not use the local cache of field and issue type lists."),
)

_BY_NAME = {definition.name: definition for definition in SETTING_DEFS}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"", "0", "false", "no", "off"}


def setting_definition(name: str) -> SettingDef | None:
    return _BY_NAME.get(name)


def is_local_host(host: str) -> bool:
    """An address on this computer: what is sent to it does not leave it."""
    return host == "localhost" or host == "::1" or host.startswith("127.")


def host_of(url: str) -> str:
    """The host name of a web address, without credentials, port and brackets."""
    match = re.match(r"^[a-z]+://(?:[^/@]*@)?(\[[^\]]*\]|[^/:?#]*)", url.strip(), re.IGNORECASE)
    return match.group(1).strip("[]").lower() if match else ""


def mask_secret(value: str) -> str:
    return "(not set)" if not value else f"******** ({len(value)} characters)"


def _parse(definition: SettingDef, raw: str) -> Any:
    text = (raw or "").strip()
    if definition.kind == "str":
        return text if text or not definition.default else definition.default
    if definition.kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ConfigError(f"{definition.name} must be true or false, not '{text}'.")
    if definition.kind == "int":
        try:
            return int(text) if text else definition.default
        except ValueError as error:
            raise ConfigError(f"{definition.name} must be a whole number, not '{text}'.") from error
    try:
        return float(text) if text else definition.default
    except ValueError as error:
        raise ConfigError(f"{definition.name} must be a number, not '{text}'.") from error


@dataclass(frozen=True)
class Settings:
    base_url: str = ""
    pat: str = field(default="", repr=False)
    project: str = ""
    api_path: str = "/rest/api/2"
    ca_bundle: str = ""
    insecure: bool = False
    allow_http: bool = False
    reporter: str = ""
    hide_users: bool = False
    epic_link_field: str = ""
    story_points_field: str = ""
    sprint_field: str = ""
    acceptance_field: str = ""
    connect_timeout: float = 10.0
    max_time: float = 120.0
    retry: int = 2
    output: str = "text"
    no_cache: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        parsed = {definition.attr: _parse(definition, values.get(definition.name, "")) for definition in SETTING_DEFS}
        if parsed["output"] not in ("text", "json"):
            raise ConfigError(f"JIRA_OUTPUT must be text or json, not '{parsed['output']}'.")
        for name, attr in (("JIRA_CONNECT_TIMEOUT", "connect_timeout"), ("JIRA_MAX_TIME", "max_time")):
            if not math.isfinite(parsed[attr]) or parsed[attr] <= 0:
                raise ConfigError(f"{name} must be a number of seconds greater than 0, not '{values.get(name, '')}'.")
        if parsed["retry"] < 0:
            raise ConfigError(f"JIRA_RETRY must be 0 or more, not '{values.get('JIRA_RETRY', '')}'.")
        return cls(**parsed)

    def field_overrides(self) -> dict[str, str]:
        """Field ids that were set explicitly, by logical field name."""
        pairs = {
            EPIC_LINK: self.epic_link_field,
            STORY_POINTS: self.story_points_field,
            SPRINT: self.sprint_field,
            ACCEPTANCE: self.acceptance_field,
        }
        return {logical: value for logical, value in pairs.items() if value}

    @property
    def json_by_default(self) -> bool:
        return self.output == "json"
