"""HTTP(S) to Jira on the standard library: keep-alive, retries, TLS, gzip.

It speaks bytes and status codes only. Turning a status into an error and a body into domain
objects is the job of `jira_rest.py`. A request may carry more headers, may limit the size of the answer
(a file download) and may follow redirects on the origin it was sent to, and nowhere else.

`http.client`, `ssl` and `gzip` are imported when the first request is made: they cost about 60 ms,
which a command that never talks to Jira (`help`, `version`, `how-to`) should not pay.
"""
from __future__ import annotations

import math
import time
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping

from ...application.settings import Settings, is_local_host
from ...domain.errors import ConfigError, UnavailableError, UsageError
from ...version import __version__

if TYPE_CHECKING:
    import http.client
    import ssl

_RETRY_STATUSES = (429, 502, 503, 504)
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"http": 80, "https": 443}
_PIECE = 64 * 1024  # bytes read at a time when the size of an answer is limited
_ERROR_BODY = 64 * 1024  # the start of the body of a failed answer that is read when the size of answers is limited: enough to explain the failure
_MAX_RETRY_WAIT = 30.0  # seconds; Jira may ask for longer, but a command line tool should not hang for minutes


class ResponseTooLarge(Exception):
    """The answer is longer than the limit given to `request`. Reading stopped and the connection was closed, because the
    rest of the body is still on the wire. The limit is for a successful answer: a failed one is never refused for its size."""

    def __init__(self, limit: int, announced: int | None = None, status: int = 0, content_type: str = "") -> None:
        super().__init__(f"the answer is longer than {limit} bytes")
        self.limit = limit
        self.announced = announced  # the Content-Length when that already said so; None when the body turned out to be longer
        self.status = status  # of the answer that was too long, and what it said it was: a login page is no file, whatever its size
        self.content_type = content_type


class RedirectRefused(Exception):
    """A redirect that `request` did not follow: to another scheme, host or port (nothing was sent there, least of all the
    token), or one too many. `target` is only the origin it pointed to: the rest of an address can carry a secret."""

    def __init__(self, reason: str, target: str = "") -> None:
        super().__init__(f"redirect refused ({reason}) {target}".strip())
        self.reason = reason  # "elsewhere", "too many" or "invalid"
        self.target = target


def _merge_headers(headers: dict[str, str], extra: Mapping[str, str] | None) -> dict[str, str]:
    """The default headers with the caller's over them, matched without regard to case. `Authorization` is the one that is never
    replaced: the token goes with the default header only."""
    merged = dict(headers)
    for name, value in (extra or {}).items():
        lowered = name.lower()
        if lowered == "authorization":
            continue
        for existing in [key for key in merged if key.lower() == lowered]:
            del merged[existing]
        merged[name] = value
    return merged


def _announced_length(response: http.client.HTTPResponse) -> int | None:
    """The Content-Length as a number; None when there is none or it is no plain number. A header can hold anything: `str.isdigit` takes
    `²`, which `int()` refuses, and `int()` refuses a number of thousands of digits, so the length of the text is looked at first."""
    text = (response.getheader("Content-Length") or "").strip()
    return int(text) if 0 < len(text) <= 18 and text.isascii() and text.isdecimal() else None


def _read_body(response: http.client.HTTPResponse, limit: int | None) -> bytes:
    """The body of an answer. With a limit it is read in pieces and refused as soon as it is longer: the Content-Length can lie
    or be missing (chunked answers), so it is a hint that saves the read, never the only check."""
    if limit is None:
        return response.read()
    announced = _announced_length(response)
    if announced is not None and announced > limit:
        raise ResponseTooLarge(limit, announced)
    pieces: list[bytes] = []
    total = 0
    while True:
        piece = response.read(_PIECE)
        if not piece:
            break
        total += len(piece)
        if total > limit:
            raise ResponseTooLarge(limit)
        pieces.append(piece)
    if response.length:  # `read(amount)` does not tell when the connection ended before the announced length: a short file is no file
        import http.client

        raise http.client.IncompleteRead(b"".join(pieces), response.length)
    return b"".join(pieces)


def _read_error_body(response: http.client.HTTPResponse) -> tuple[bytes, bool]:
    """The start of the body of a failed answer, `_ERROR_BODY` bytes at most, and whether there was more: the rest is then still on the
    wire and the connection cannot be used again."""
    data = response.read(_ERROR_BODY + 1)
    return data[:_ERROR_BODY], len(data) > _ERROR_BODY


def _decoded_start(data: bytes, encoding: str) -> bytes:
    """The start of the body of a failed answer with a gzip encoding undone as far as it goes, and no longer than `_ERROR_BODY`. What
    cannot be unpacked is left out: the status is the answer, the body only explains it."""
    if encoding.lower() != "gzip" or not data:
        return data[:_ERROR_BODY]
    import zlib

    try:
        return zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(data, _ERROR_BODY)
    except zlib.error:
        return b""


def _decoded(data: bytes, encoding: str, limit: int | None) -> bytes:
    """The body with a gzip content encoding undone. With a limit the result may not be longer either: a few kilobytes can expand
    to gigabytes."""
    import gzip

    if not data or encoding.lower() != "gzip":
        return data
    if limit is None:
        return gzip.decompress(data)
    import zlib

    inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
    plain = inflater.decompress(data, limit + 1)
    if len(plain) > limit:
        raise ResponseTooLarge(limit)
    if not inflater.eof:
        raise EOFError("Compressed file ended before the end-of-stream marker was reached")
    return plain


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """The server's `Retry-After` (whole seconds), else one second more for every attempt."""
    try:
        wanted = float(retry_after) if retry_after else float(attempt)
    except ValueError:
        return float(attempt)  # an HTTP date or garbage: use the default
    if math.isnan(wanted):
        return float(attempt)  # `nan` is a number to float() and no time to sleep
    return min(max(wanted, 0.0), _MAX_RETRY_WAIT)


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
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
        redirects: int = 0,
        idempotent: bool = False,
    ) -> HttpResponse:
        """Reads are retried after a network error or HTTP 429/502/503/504; writes never are. A POST that only reads (`idempotent=True`,
        a search whose query is too long for a URL) is retried like a read.

        `headers` go over the defaults (never `Authorization`; a `Content-Type` replaces the JSON one). `max_bytes` limits the
        answer, after a gzip encoding is undone, and raises `ResponseTooLarge`. `redirects` is how many redirects of a GET are
        followed, only to the same scheme, host and port; any other redirect raises `RedirectRefused`. Without it a redirect is
        an answer like any other."""
        import gzip
        import http.client
        import zlib

        prefix = self._target()[3]
        extra = headers  # the caller's headers; the name is taken by the defaults below
        headers = {
            "Authorization": f"Bearer {self._settings.pat}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": f"jira-dc-cli/{__version__}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        headers = _merge_headers(headers, extra)
        attempts = 1 + max(self._settings.retry if retries is None else retries, 0) if method == "GET" or idempotent else 1
        full_path = prefix + path
        last_problem = ""
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response, data = self._exchange(method, full_path, body, headers, timeout, max_bytes, redirects if method == "GET" else 0)
            except http.client.InvalidURL as error:
                self._close()
                raise UsageError(
                    "The address of the request holds a character that no URL can hold (a control character or a blank).",
                    hint="Check the key, the id, the name or the path that you gave: it is part of the address.",
                ) from error
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

    def _exchange(
        self,
        method: str,
        target: str,
        body: bytes | None,
        headers: Mapping[str, str],
        timeout: float | None,
        limit: int | None,
        hops: int,
    ) -> tuple[http.client.HTTPResponse, bytes]:
        """One request and its answer: the body is read (within `limit`) and decoded, and up to `hops` redirects are followed on the
        origin of the request."""
        scheme, host, port, _ = self._target()
        followed = 0
        while True:
            connection = self._connect(scheme, host, port)
            if connection.sock is not None:
                connection.sock.settimeout(timeout if timeout is not None else self._settings.max_time)
            connection.request(method, target, body=body, headers=headers)
            response = connection.getresponse()
            # the limit is for the file that was asked for, not for the explanation of why there is none
            failed = limit is not None and not 200 <= response.status < 300
            try:
                if failed:
                    data, more = _read_error_body(response)
                    if more:
                        self._close()  # the rest of the body is still on the wire
                else:
                    data = _read_body(response, limit)
            except ResponseTooLarge as error:
                error.status = response.status
                error.content_type = response.getheader("Content-Type") or ""
                self._close()
                raise
            if response.will_close:
                self._close()  # the next request opens a new connection with the right timeouts
            encoding = response.getheader("Content-Encoding", "")
            data = _decoded_start(data, encoding) if failed else _decoded(data, encoding, limit)
            location = response.getheader("Location") or ""
            if not hops or response.status not in _REDIRECT_STATUSES or not location:
                return response, data
            if followed >= hops:
                raise RedirectRefused("too many")
            target = self._same_origin(location, target)
            followed += 1
            self._debug(f"{method} -> {response.status}, following to {target.split('?')[0]}")

    def _same_origin(self, location: str, current: str) -> str:
        """The request target (path and query) that a redirect points to, when it stays on the scheme, host and port of the request."""
        scheme, host, port, _ = self._target()
        shown = f"[{host}]" if ":" in host else host
        base = f"{scheme}://{shown}" + (f":{port}" if port else "") + current
        try:
            parts = urllib.parse.urlsplit(urllib.parse.urljoin(base, location))
            there = (parts.scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(parts.scheme, 0))
        except ValueError:
            raise RedirectRefused("invalid") from None
        if there != (scheme, host.lower(), port or _DEFAULT_PORTS[scheme]):
            raise RedirectRefused("elsewhere", f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}")
        path = urllib.parse.quote(parts.path or "/", safe="/%:@!$&'()*+,;=~-._")
        return path + ("?" + urllib.parse.quote(parts.query, safe="=&%+:,;/?@!$'()*~-._") if parts.query else "")

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
