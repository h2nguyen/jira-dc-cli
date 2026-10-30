"""`jira doctor`: check the setup step by step and say how to fix what is wrong."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from ..domain.compat import assess, shown
from ..domain.errors import JiraCliError
from ..domain.fields import FIELD_LABELS, LOGICAL_FIELDS
from .ports import Cache, Clock, Files, JiraGateway
from .settings import Settings, host_of, is_local_host

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


@dataclass(frozen=True)
class DoctorRun:
    checks: list[Check]
    cached: bool = False  # the online checks come from an earlier run: nothing was asked of Jira
    cached_at: str = ""


class Doctor:
    def __init__(
        self,
        gateway: JiraGateway,
        settings: Settings,
        files: Files,
        config_path: str,
        runtime: Mapping[str, str],
        cache: Cache | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._files = files
        self._config_path = config_path
        self._runtime = runtime
        self._cache = cache
        self._clock = clock

    def execute(self, *, quick: bool = False, strict_privacy: bool = False) -> list[Check]:
        return self.run(quick=quick, strict_privacy=strict_privacy).checks

    def run(self, *, quick: bool = False, strict_privacy: bool = False, cached_for: float = 0.0) -> DoctorRun:
        """All checks. `quick` leaves out the fields and the project (two requests at most); with `cached_for` seconds the
        online checks of a successful run that is not older are reused and nothing is asked of Jira."""
        checks = [
            Check("runtime", OK, f"jira-dc-cli {self._runtime.get('version', '?')} on Python {self._runtime.get('python', '?')}"),
            self._config_file(),
            self._base_url(),
            self._token(),
            self._tls(),
        ]
        launcher = self._launcher()
        if launcher is not None:
            checks.append(launcher)
        privacy = self._privacy(strict_privacy)
        if any(check.status == FAIL for check in checks):
            return DoctorRun([*checks, Check("connection", WARN, "skipped until the configuration above is fixed"), privacy])
        remembered = self._remembered(quick, cached_for)
        if remembered is not None:
            at, online = remembered
            return DoctorRun([*checks, *online, privacy], cached=True, cached_at=at)
        online = self._online(quick)
        self._remember(quick, online)
        return DoctorRun([*checks, *online, privacy])

    # -- what an earlier successful run left ------------------------------------------------------

    def _cache_key(self, quick: bool) -> str:
        """What a remembered run was about: the server, the API path, the project and the token (only as part of a hash, so that the
        file name tells nothing), and whether it was the quick form."""
        settings = self._settings
        identity = "|".join([settings.base_url, settings.api_path, settings.project, settings.pat, "quick" if quick else "full"])
        return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]

    def _remembered(self, quick: bool, max_age: float) -> tuple[str, list[Check]] | None:
        if self._cache is None or max_age <= 0:
            return None
        text = self._cache.get("doctor", self._cache_key(quick), max_age)
        try:
            record = json.loads(text) if text else None
            return str(record["at"]), [Check(**item) for item in record["checks"]]
        except (ValueError, KeyError, TypeError):
            return None

    def _remember(self, quick: bool, online: list[Check]) -> None:
        if self._cache is None or any(check.status == FAIL for check in online):
            return
        at = self._clock.now().isoformat(timespec="seconds") if self._clock else ""
        record = {"at": at, "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "hint": c.hint} for c in online]}
        self._cache.put("doctor", self._cache_key(quick), json.dumps(record))

    def _config_file(self) -> Check:
        if self._files.exists(self._config_path):
            return Check("config file", OK, self._config_path)
        return Check("config file", WARN, f"{self._config_path} does not exist", "Run `jira setup-pat` and `jira config set JIRA_BASE_URL URL`.")

    def _base_url(self) -> Check:
        check = self._base_url_check()
        if self._settings.base_url_credentials and check.status == OK:
            return Check(
                "base url",
                WARN,
                f"{check.detail}; JIRA_BASE_URL holds a user name or password, which signs in nothing and which the CLI leaves out of what it prints",
                "Take it out of the address: the token (`jira setup-pat`) is the credential.",
            )
        return check

    def _base_url_check(self) -> Check:
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

    def _launcher(self) -> Check | None:
        """Whether the program is found by name. Only when the runtime says (the installed program does): an agent that has to
        start `python ...\\jira.py` by hand has found out the hard way."""
        launcher = self._runtime.get("launcher")
        if launcher is None:
            return None
        if launcher:
            return Check("launcher", OK, f"`jira` is found on the PATH: {launcher}")
        program = self._runtime.get("program", "")
        installer = self._runtime.get("install_script", "")
        fix = f'python "{installer}" --bin-dir DIR' if installer else "python install.py --bin-dir DIR, run in the source folder of jira-dc-cli,"
        return Check(
            "launcher",
            WARN,
            "`jira` is not found on the PATH of this shell" + (f" (this run was started as {program})" if program else ""),
            f"Put the launcher in a folder that is on the PATH: {fix} (DIR: a folder from your PATH), or add the folder of the launcher to the PATH.",
        )

    def _online(self, quick: bool = False) -> list[Check]:
        try:
            self._gateway.current_user()
        except JiraCliError as error:
            return [Check("connection", FAIL, error.message, error.hint)]
        checks = [Check("connection", OK, "reachable, the token is accepted")]
        try:
            info = self._gateway.server_info()
            verdict = assess(str(info.get("version", "")), str(info.get("deploymentType", "")))
            checks.append(Check("server", verdict.status, f"{verdict.detail} ({shown(info.get('deploymentType', 'unknown'))})", verdict.hint))
        except JiraCliError as error:
            checks.append(Check("server", WARN, error.message, error.hint))
        if quick:
            return checks
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

    def _privacy(self, strict: bool = False) -> Check:
        profile = f" (profile {self._settings.profile})" if self._settings.profile else ""
        if self._settings.hide_users:
            return Check("privacy", OK, f"JIRA_HIDE_USERS=true: people are never printed{profile}")
        detail = "people can be shown (set JIRA_HIDE_USERS=true to forbid it)"
        if strict:
            return Check("privacy", FAIL, detail, "Set JIRA_HIDE_USERS=true, or JIRA_PROFILE=agent, so that people are never printed.")
        if self._settings.profile:
            return Check(
                "privacy",
                WARN,
                f"profile {self._settings.profile} is on, but people can still be shown: JIRA_HIDE_USERS is set to false",
                "Take that setting away so that the profile applies: jira config unset JIRA_HIDE_USERS (and check the environment).",
            )
        return Check("privacy", OK, detail)
