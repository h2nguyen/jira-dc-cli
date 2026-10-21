"""HTTP(S) to Jira on the standard library: keep-alive, retries, TLS, gzip.

It speaks bytes and status codes only. Turning a status into an error and a body into domain
objects is the job of `jira_rest.py`.

`http.client`, `ssl` and `gzip` are imported when the first request is made: they cost about 60 ms,
which a command that never talks to Jira (`help`, `version`, `how-to`) should not pay.
"""
from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping

from ...application.settings import Settings, is_local_host
from ...domain.errors import ConfigError, UnavailableError
from ...version import __version__

if TYPE_CHECKING:
    import http.client
    import ssl

_RETRY_STATUSES = (429, 502, 503, 504)
_MAX_RETRY_WAIT = 30.0  # seconds; Jira may ask for longer, but a command line tool should not hang for minutes


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """The server's `Retry-After` (whole seconds), else one second more for every attempt."""
    try:
        return min(max(float(retry_after), 0.0), _MAX_RETRY_WAIT) if retry_after else float(attempt)
    except ValueError:
        return float(attempt)  # an HTTP date or garbage: use the default


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport:
    def __init__(
        self,
        settings: Settings,
        debug: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._debug = debug or (lambda message: None)
        self._sleep = sleep
        self._connection: http.client.HTTPConnection | None = None
        self._origin: tuple[str, str, int | None, str] | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> HttpResponse:
        """Reads are retried after a network error or HTTP 429/502/503/504; writes never are."""
        import gzip
        import http.client
        import zlib

        scheme, host, port, prefix = self._target()
        headers = {
            "Authorization": f"Bearer {self._settings.pat}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": f"jira-dc-cli/{__version__}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        attempts = 1 + max(self._settings.retry if retries is None else retries, 0) if method == "GET" else 1
        full_path = prefix + path
        last_problem = ""
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                connection = self._connect(scheme, host, port)
                if connection.sock is not None:
                    connection.sock.settimeout(timeout if timeout is not None else self._settings.max_time)
                connection.request(method, full_path, body=body, headers=headers)
                response = connection.getresponse()
                data = response.read()
                if response.will_close:
                    self._close()  # the next request opens a new connection with the right timeouts
                if data and response.getheader("Content-Encoding", "").lower() == "gzip":
                    data = gzip.decompress(data)
            except (OSError, EOFError, zlib.error, http.client.HTTPException) as error:
                self._close()
                last_problem = f"{type(error).__name__}: {error}"
                self._debug(f"{method} {path} failed: {last_problem}")
                if attempt < attempts:
                    self._sleep(float(attempt))
                    continue
                if isinstance(error, (EOFError, zlib.error, gzip.BadGzipFile)):
                    raise UnavailableError(
                        f"Jira sent an answer that could not be read ({last_problem}).",
                        hint="Try again; if it stays, a proxy between you and Jira may be damaging the response.",
                    ) from error
                raise UnavailableError(
                    f"Cannot reach Jira ({last_problem}).",
                    hint="Check JIRA_BASE_URL and the network or VPN. For a certificate error see JIRA_CA_BUNDLE. `jira doctor` checks all of it.",
                ) from error
            self._debug(f"{method} {path} -> {response.status} ({(time.perf_counter() - started) * 1000:.0f} ms)")
            if response.status in _RETRY_STATUSES and attempt < attempts:
                self._close()
                self._sleep(_retry_delay(attempt, response.getheader("Retry-After")))
                continue
            return HttpResponse(response.status, {key.lower(): value for key, value in response.getheaders()}, data)
        raise UnavailableError(f"Cannot reach Jira ({last_problem}).")  # pragma: no cover

    def close(self) -> None:
        self._close()

    def _target(self) -> tuple[str, str, int | None, str]:
        if self._origin is None:
            if not self._settings.base_url:
                raise ConfigError(
                    "JIRA_BASE_URL is not set.",
                    hint="Run `jira config set JIRA_BASE_URL https://jira.example.com/jira`.",
                )
            if not self._settings.pat:
                raise ConfigError("JIRA_PAT is not set.", hint="Run `jira setup-pat`.")
            parts = urllib.parse.urlsplit(self._settings.base_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ConfigError(
                    f"JIRA_BASE_URL is not a valid web address: {self._settings.base_url}",
                    hint="It must look like https://jira.example.com/jira",
                )
            if parts.scheme == "http" and not self._settings.allow_http and not is_local_host(parts.hostname):
                raise ConfigError(
                    "JIRA_BASE_URL uses plain http: the token would travel unencrypted with every request.",
                    hint="Use the https address of Jira. On an isolated network you can accept the risk with `jira config set JIRA_ALLOW_HTTP true`.",
                )
            self._origin = (parts.scheme, parts.hostname, parts.port, parts.path.rstrip("/"))
        return self._origin

    def _connect(self, scheme: str, host: str, port: int | None) -> http.client.HTTPConnection:
        import http.client

        if self._connection is not None:
            return self._connection
        if scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                host, port, context=self._context(), timeout=self._settings.connect_timeout
            )
        else:
            connection = http.client.HTTPConnection(host, port, timeout=self._settings.connect_timeout)
        connection.connect()
        self._connection = connection
        return connection

    def _context(self) -> ssl.SSLContext:
        import ssl

        try:
            context = ssl.create_default_context(cafile=self._settings.ca_bundle or None)
        except OSError as error:
            raise ConfigError(
                f"Cannot read JIRA_CA_BUNDLE ({self._settings.ca_bundle}): {error}",
                hint="Fix the path, or unset JIRA_CA_BUNDLE to use the system certificate store.",
            ) from error
        if self._settings.insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def _close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
