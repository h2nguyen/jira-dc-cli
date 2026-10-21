"""`jira doctor`: check the setup step by step and say how to fix what is wrong."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..domain.errors import JiraCliError
from ..domain.fields import FIELD_LABELS, LOGICAL_FIELDS
from .ports import Files, JiraGateway
from .settings import Settings, host_of, is_local_host

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


class Doctor:
    def __init__(
        self, gateway: JiraGateway, settings: Settings, files: Files, config_path: str, runtime: Mapping[str, str]
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._files = files
        self._config_path = config_path
        self._runtime = runtime

    def execute(self) -> list[Check]:
        checks = [
            Check("runtime", OK, f"jira-dc-cli {self._runtime.get('version', '?')} on Python {self._runtime.get('python', '?')}"),
            self._config_file(),
            self._base_url(),
            self._token(),
            self._tls(),
        ]
        if any(check.status == FAIL for check in checks):
            checks.append(Check("connection", WARN, "skipped until the configuration above is fixed"))
        else:
            checks.extend(self._online())
        checks.append(self._privacy())
        return checks

    def _config_file(self) -> Check:
        if self._files.exists(self._config_path):
            return Check("config file", OK, self._config_path)
        return Check("config file", WARN, f"{self._config_path} does not exist", "Run `jira setup-pat` and `jira config set JIRA_BASE_URL URL`.")

    def _base_url(self) -> Check:
        url = self._settings.base_url
        if not url:
            return Check("base url", FAIL, "JIRA_BASE_URL is not set", "Run `jira config set JIRA_BASE_URL https://jira.example.com/jira`.")
        if not url.startswith(("http://", "https://")):
            return Check("base url", FAIL, "JIRA_BASE_URL must start with http:// or https://", "Include the scheme.")
        if url.startswith("http://"):
            host = host_of(url)
            if is_local_host(host):
                return Check("base url", OK, "plain http, on this computer")
            if self._settings.allow_http:
                return Check("base url", WARN, "plain http, allowed by JIRA_ALLOW_HTTP: the token travels unencrypted", "Use https if Jira offers it.")
            return Check("base url", FAIL, "plain http: the CLI refuses to send the token unencrypted", "Use the https address, or on an isolated network `jira config set JIRA_ALLOW_HTTP true`.")
        return Check("base url", OK, "https")

    def _token(self) -> Check:
        if self._settings.pat:
            return Check("token", OK, "set (not shown)")
        return Check("token", FAIL, "JIRA_PAT is not set", "Run `jira setup-pat`.")

    def _tls(self) -> Check:
        if self._settings.insecure:
            return Check("tls", WARN, "certificate checks are off (JIRA_INSECURE)", "Use JIRA_CA_BUNDLE for a company CA instead.")
        if self._settings.ca_bundle:
            if self._files.exists(self._settings.ca_bundle):
                return Check("tls", OK, "company CA bundle")
            return Check("tls", FAIL, f"JIRA_CA_BUNDLE does not exist: {self._settings.ca_bundle}", "Fix the path or unset it.")
        return Check("tls", OK, "system certificate store")

    def _online(self) -> list[Check]:
        try:
            self._gateway.current_user()
        except JiraCliError as error:
            return [Check("connection", FAIL, error.message, error.hint)]
        checks = [Check("connection", OK, "reachable, the token is accepted")]
        try:
            info = self._gateway.server_info()
            checks.append(Check("server", OK, f"Jira {info.get('version', '?')} ({info.get('deploymentType', 'unknown')})"))
        except JiraCliError as error:
            checks.append(Check("server", WARN, error.message, error.hint))
        try:
            ids = self._gateway.field_ids()
            missing = [FIELD_LABELS[name] for name in LOGICAL_FIELDS if not ids.get(name)]
            if missing:
                checks.append(Check("fields", WARN, "not found: " + ", ".join(missing), "Set JIRA_*_FIELD (see `jira fields`) if your fields have other names."))
            else:
                checks.append(Check("fields", OK, "story points, epic link, sprint and acceptance criteria found"))
        except JiraCliError as error:
            checks.append(Check("fields", WARN, error.message, error.hint))
        if self._settings.project:
            try:
                self._gateway.get_project(self._settings.project)
                checks.append(Check("project", OK, f"{self._settings.project} is accessible"))
            except JiraCliError as error:
                checks.append(Check("project", FAIL, f"{self._settings.project}: {error.message}", "Pick another with `jira use KEY` or `jira config unset JIRA_PROJECT`."))
        else:
            checks.append(Check("project", WARN, "no current project", "Run `jira use KEY` to avoid typing --project."))
        return checks

    def _privacy(self) -> Check:
        if self._settings.hide_users:
            return Check("privacy", OK, "JIRA_HIDE_USERS=true: people are never printed")
        return Check("privacy", OK, "people can be shown (set JIRA_HIDE_USERS=true to forbid it)")
