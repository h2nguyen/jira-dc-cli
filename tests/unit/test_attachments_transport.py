"""The HTTP transport as attachments need it: more headers, binary answers, a limit on the size of an answer, redirects that stay on the host."""
import gzip
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jira_dc_cli.adapters.driven.http_transport import HttpTransport, RedirectRefused, ResponseTooLarge, _announced_length
from jira_dc_cli.application.settings import Settings
from jira_dc_cli.domain.errors import UnavailableError, UsageError

TOKEN = "test-token"


def reply(handler, status=200, body=b"", headers=None):
    handler.send_response(status)
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def chunked(handler, pieces, headers=None):
    handler.send_response(200)
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    for piece in pieces:
        handler.wfile.write(f"{len(piece):x}\r\n".encode("ascii") + piece + b"\r\n")
    handler.wfile.write(b"0\r\n\r\n")


class Scripted:
    """A tiny HTTP server for one test: `script(handler)` answers every request, and every request is recorded as
    `(method, path, [(header, value), ...], body)`."""

    def __init__(self, script):
        self.requests = []
        self.connections = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append((self.command, self.path, list(self.headers.items()), body))
                script(self)

            do_GET = do_POST = serve

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                outer.connections += 1
                return super().get_request()

            def handle_error(self, request, client_address):
                pass  # a client that hangs up is what several tests do: no traceback for it

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.base_url = self.origin + "/jira"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def values(self, index, name):
        """What the request with this index carried under a header name, without regard to case: one entry for every time it was sent."""
        return [value for header, value in self.requests[index][2] if header.lower() == name.lower()]


class TransportCase(unittest.TestCase):
    def serve(self, script):
        server = Scripted(script)
        self.addCleanup(server.stop)
        return server

    def transport(self, server, **settings):
        values = dict(base_url=server.base_url, pat=TOKEN, retry=0)
        values.update(settings)
        transport = HttpTransport(Settings(**values), sleep=lambda seconds: None)
        self.addCleanup(transport.close)
        return transport


class HeadersTest(TransportCase):
    def test_extra_headers_go_over_the_defaults_without_regard_to_case(self):
        server = self.serve(lambda handler: reply(handler, 200, b"{}"))
        self.transport(server).request("GET", "/x", headers={"accept": "*/*", "X-Atlassian-Token": "no-check"})
        self.assertEqual(server.values(0, "Accept"), ["*/*"])  # one Accept, the caller's
        self.assertEqual(server.values(0, "X-Atlassian-Token"), ["no-check"])
        self.assertEqual(server.values(0, "Accept-Encoding"), ["gzip"])  # what was not asked for stays

    def test_the_token_header_is_never_replaced(self):
        server = self.serve(lambda handler: reply(handler, 200, b"{}"))
        transport = self.transport(server)
        transport.request("GET", "/plain")
        transport.request("GET", "/other", headers={"Authorization": "something else", "authorization": "and another"})
        self.assertEqual(len(server.values(1, "Authorization")), 1)
        self.assertEqual(server.values(1, "Authorization"), server.values(0, "Authorization"))
        self.assertNotIn("else", server.values(1, "Authorization")[0])

    def test_a_json_body_keeps_its_content_type_unless_one_is_given(self):
        server = self.serve(lambda handler: reply(handler, 200, b"{}"))
        transport = self.transport(server)
        transport.request("POST", "/json", body=b"{}")
        transport.request("POST", "/multipart", body=b"--b--", headers={"content-type": "multipart/form-data; boundary=b"})
        self.assertEqual(server.values(0, "Content-Type"), ["application/json"])
        self.assertEqual(server.values(1, "Content-Type"), ["multipart/form-data; boundary=b"])
        self.assertEqual(server.requests[1][3], b"--b--")

    def test_a_binary_answer_comes_back_byte_for_byte(self):
        data = bytes(range(256)) * 40
        server = self.serve(lambda handler: reply(handler, 200, data, {"Content-Type": "application/octet-stream"}))
        response = self.transport(server).request("GET", "/file", headers={"Accept-Encoding": "identity"}, max_bytes=len(data))
        self.assertEqual((response.status, response.body, response.headers["content-type"]), (200, data, "application/octet-stream"))
        self.assertEqual(server.values(0, "Accept-Encoding"), ["identity"])

    def test_a_write_with_extra_headers_is_still_sent_once(self):
        server = self.serve(lambda handler: reply(handler, 503, b"{}"))
        response = self.transport(server, retry=3).request("POST", "/w", body=b"x", headers={"X-Atlassian-Token": "no-check"})
        self.assertEqual((response.status, len(server.requests)), (503, 1))  # a write is never retried

    def test_a_read_with_a_limit_is_retried_like_any_read(self):
        answers = iter([503, 200])
        server = self.serve(lambda handler: reply(handler, next(answers), b"data"))
        response = self.transport(server, retry=2).request("GET", "/r", max_bytes=100)
        self.assertEqual((response.status, response.body, len(server.requests)), (200, b"data", 2))


class SizeLimitTest(TransportCase):
    def test_a_content_length_over_the_limit_is_refused_before_the_body_is_read(self):
        server = self.serve(lambda handler: reply(handler, 200, b"x" * 5000))
        transport = self.transport(server)
        with self.assertRaises(ResponseTooLarge) as caught:
            transport.request("GET", "/big", max_bytes=1000)
        self.assertEqual((caught.exception.limit, caught.exception.announced), (1000, 5000))
        self.assertIsNone(transport._connection)  # closed: the body is still on the wire

    def test_the_limit_is_exact(self):
        server = self.serve(lambda handler: reply(handler, 200, b"x" * 1000 if handler.path.endswith("fits") else b"x" * 1001))
        transport = self.transport(server)
        self.assertEqual(len(transport.request("GET", "/fits", max_bytes=1000).body), 1000)
        with self.assertRaises(ResponseTooLarge):
            transport.request("GET", "/over", max_bytes=1000)

    def test_a_body_without_a_content_length_is_limited_while_it_is_read(self):
        server = self.serve(lambda handler: chunked(handler, [b"x" * 400] * 5))
        transport = self.transport(server)
        with self.assertRaises(ResponseTooLarge) as caught:
            transport.request("GET", "/chunked", max_bytes=1000)
        self.assertIsNone(caught.exception.announced)  # nobody said; the body turned out to be longer
        self.assertEqual(len(transport.request("GET", "/chunked", max_bytes=2000).body), 2000)  # and the same body fits a larger limit

    def test_an_endless_body_is_stopped_early(self):
        sent = []

        def endless(handler):
            handler.send_response(200)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            try:
                for _ in range(4096):  # 256 MiB at most
                    handler.wfile.write(b"10000\r\n" + b"x" * 65536 + b"\r\n")
                    sent.append(65536)
            except OSError:
                pass  # the client hung up
            handler.close_connection = True

        server = self.serve(endless)
        started = time.monotonic()
        with self.assertRaises(ResponseTooLarge):
            self.transport(server).request("GET", "/endless", max_bytes=100_000)
        self.assertLess(time.monotonic() - started, 20)
        time.sleep(0.2)
        self.assertLess(sum(sent), 64 * 1024 * 1024)  # it did not read it all, and the server learned that nobody listens

    def test_the_transport_is_usable_again_after_an_aborted_read(self):
        server = self.serve(lambda handler: reply(handler, 200, b"x" * 5000 if handler.path.endswith("big") else b"fine"))
        transport = self.transport(server)
        with self.assertRaises(ResponseTooLarge):
            transport.request("GET", "/big", max_bytes=1000)
        response = transport.request("GET", "/small", max_bytes=1000)
        self.assertEqual((response.status, response.body), (200, b"fine"))
        self.assertEqual(server.connections, 2)  # a new connection: the first one could not be trusted any more

    def test_a_gzip_bomb_is_stopped_at_the_limit_after_decoding(self):
        bomb = gzip.compress(b"\0" * 20_000_000)
        self.assertLess(len(bomb), 100_000)
        server = self.serve(lambda handler: reply(handler, 200, bomb, {"Content-Encoding": "gzip"}))
        with self.assertRaises(ResponseTooLarge):
            self.transport(server).request("GET", "/bomb", max_bytes=1_000_000)

    def test_a_gzip_answer_within_the_limit_is_decoded_and_a_cut_one_is_a_clean_failure(self):
        packed = gzip.compress(b"hello " * 100)
        server = self.serve(lambda handler: reply(handler, 200, packed[:-8] if handler.path.endswith("cut") else packed, {"Content-Encoding": "gzip"}))
        transport = self.transport(server)
        self.assertEqual(transport.request("GET", "/ok", max_bytes=10_000).body, b"hello " * 100)
        with self.assertRaises(UnavailableError) as caught:
            transport.request("GET", "/cut", max_bytes=10_000)
        self.assertIn("could not be read", caught.exception.message)

    def test_a_body_that_ends_when_the_server_closes_the_connection_is_limited_too(self):
        def until_closed(handler):
            handler.send_response(200)
            handler.send_header("Connection", "close")  # no Content-Length and no chunks: the end is the end of the connection
            handler.end_headers()
            handler.wfile.write(b"x" * 5000)
            handler.close_connection = True

        server = self.serve(until_closed)
        with self.assertRaises(ResponseTooLarge) as caught:
            self.transport(server).request("GET", "/closed", max_bytes=1000)
        self.assertIsNone(caught.exception.announced)
        self.assertEqual(len(self.transport(server).request("GET", "/closed", max_bytes=5000).body), 5000)

    def test_a_body_shorter_than_announced_is_a_failure_and_never_a_short_file(self):
        def cut_short(handler):
            handler.send_response(200)
            handler.send_header("Content-Length", "5000")
            handler.end_headers()
            handler.wfile.write(b"x" * 100)
            handler.close_connection = True

        server = self.serve(cut_short)
        for options in ({"max_bytes": 10_000}, {}):
            with self.subTest(options=options):
                with self.assertRaises(UnavailableError) as caught:
                    self.transport(server).request("GET", "/short", **options)
                self.assertIn("IncompleteRead", caught.exception.message)

    def test_without_a_limit_nothing_changes(self):
        server = self.serve(lambda handler: reply(handler, 200, b"x" * 100_000))
        self.assertEqual(len(self.transport(server).request("GET", "/any").body), 100_000)


class RedirectTest(TransportCase):
    def script(self, routes):
        """A server whose answer is `routes[path]`: a status and headers, or a body."""

        def answer(handler):
            status, headers, body = routes.get(handler.path.split("?")[0], (404, {}, b""))
            reply(handler, status, body, headers)

        return answer

    def test_without_the_option_a_redirect_is_an_answer_like_any_other(self):
        server = self.serve(self.script({"/jira/a": (302, {"Location": "/jira/b"}, b"")}))
        response = self.transport(server).request("GET", "/a")
        self.assertEqual((response.status, response.headers["location"], len(server.requests)), (302, "/jira/b", 1))

    def test_a_redirect_on_the_same_host_is_followed_with_the_token_on_the_same_connection(self):
        server = self.serve(self.script({"/jira/a": (302, {"Location": "/jira/b?x=1"}, b""), "/jira/b": (200, {}, b"arrived")}))
        response = self.transport(server).request("GET", "/a", redirects=3)
        self.assertEqual((response.status, response.body), (200, b"arrived"))
        self.assertEqual([request[1] for request in server.requests], ["/jira/a", "/jira/b?x=1"])
        self.assertEqual(server.values(1, "Authorization"), server.values(0, "Authorization"))
        self.assertEqual(server.connections, 1)

    def test_an_absolute_a_relative_and_an_untidy_location_are_all_resolved(self):
        for location, expected in (
            ("{origin}/jira/b", "/jira/b"),
            ("b", "/jira/b"),  # relative to the folder of the request
            ("{origin}/jira/with space/b?q=a b", "/jira/with%20space/b?q=a%20b"),
            ("HTTP://127.0.0.1:{port}/jira/b", "/jira/b"),
        ):
            with self.subTest(location=location):
                holder = {}
                routes = {"/jira/b": (200, {}, b"ok"), "/jira/with%20space/b": (200, {}, b"ok")}

                def answer(handler, holder=holder, routes=routes):
                    if handler.path == "/jira/a":
                        reply(handler, 302, b"", {"Location": holder["location"]})
                    else:
                        self.script(routes)(handler)

                server = self.serve(answer)
                holder["location"] = location.format(origin=server.origin, port=server.port)
                response = self.transport(server).request("GET", "/a", redirects=3)
                self.assertEqual(response.status, 200)
                self.assertEqual(server.requests[1][1], expected)

    def test_three_redirects_are_followed_and_a_fourth_is_not(self):
        routes = {f"/jira/{n}": (302, {"Location": f"/jira/{n + 1}"}, b"") for n in range(1, 6)}
        routes["/jira/4"] = (200, {}, b"end")
        server = self.serve(self.script(routes))
        self.assertEqual(self.transport(server).request("GET", "/1", redirects=3).body, b"end")  # 1 -> 2 -> 3 -> 4: three hops
        server = self.serve(self.script({**routes, "/jira/4": (302, {"Location": "/jira/5"}, b""), "/jira/5": (200, {}, b"end")}))
        with self.assertRaises(RedirectRefused) as caught:
            self.transport(server).request("GET", "/1", redirects=3)
        self.assertEqual(caught.exception.reason, "too many")
        self.assertEqual(len(server.requests), 4)  # the fifth address was never asked for

    def test_a_redirect_elsewhere_is_refused_and_nothing_is_sent_there(self):
        elsewhere = self.serve(lambda handler: reply(handler, 200, b"secret place"))
        for location in (f"{elsewhere.origin}/jira/x", "http://localhost:{port}/jira/x", "https://127.0.0.1:{port}/jira/x", "//127.0.0.2:{port}/jira/x"):
            with self.subTest(location=location):
                holder = {}
                server = self.serve(lambda handler, holder=holder: reply(handler, 302, b"", {"Location": holder["location"]}))
                holder["location"] = location.format(port=server.port)
                transport = self.transport(server)
                with self.assertRaises(RedirectRefused) as caught:
                    transport.request("GET", "/a", redirects=3)
                self.assertEqual(caught.exception.reason, "elsewhere")
                self.assertNotIn("/jira/x", caught.exception.target)  # only the origin: the rest of an address can carry a secret
                self.assertEqual(len(server.requests), 1)
        self.assertEqual(elsewhere.requests, [])  # not a request, so no token either
        self.assertEqual(elsewhere.connections, 0)

    def test_a_user_name_in_the_location_does_not_make_another_host_the_same(self):
        holder = {}
        elsewhere = self.serve(lambda handler: reply(handler, 200, b""))
        server = self.serve(lambda handler: reply(handler, 302, b"", {"Location": holder["location"]}))
        holder["location"] = f"http://127.0.0.1:{server.port}@127.0.0.1:{elsewhere.port}/x"
        with self.assertRaises(RedirectRefused) as caught:
            self.transport(server).request("GET", "/a", redirects=3)
        self.assertEqual(caught.exception.target, f"http://127.0.0.1:{elsewhere.port}")  # without the user name
        self.assertEqual(elsewhere.requests, [])

    def test_the_transport_stays_usable_after_a_refused_redirect(self):
        server = self.serve(lambda handler: reply(handler, 302, b"", {"Location": "http://127.0.0.2:1/x"}) if handler.path.endswith("/a") else reply(handler, 200, b"fine"))
        transport = self.transport(server)
        with self.assertRaises(RedirectRefused):
            transport.request("GET", "/a", redirects=3)
        self.assertEqual(transport.request("GET", "/b").body, b"fine")
        self.assertEqual(server.connections, 1)

    def test_only_a_get_follows_a_redirect_and_a_redirect_without_a_target_is_an_answer(self):
        server = self.serve(self.script({"/jira/p": (302, {"Location": "/jira/b"}, b""), "/jira/n": (302, {}, b""), "/jira/b": (200, {}, b"b")}))
        transport = self.transport(server)
        self.assertEqual(transport.request("POST", "/p", body=b"x", redirects=3).status, 302)
        self.assertEqual(transport.request("GET", "/n", redirects=3).status, 302)
        self.assertEqual([request[1] for request in server.requests], ["/jira/p", "/jira/n"])

    def test_the_default_port_is_the_same_origin_as_the_port_written_out(self):
        transport = HttpTransport(Settings(base_url="https://jira.example.com:443/jira", pat=TOKEN))
        self.assertEqual(transport._same_origin("https://JIRA.example.com/jira/x?y=1", "/jira/a"), "/jira/x?y=1")
        plain = HttpTransport(Settings(base_url="http://localhost/jira", pat=TOKEN))
        self.assertEqual(plain._same_origin("http://localhost:80/jira/x", "/jira/a"), "/jira/x")
        for location in ("https://jira.example.com:8443/jira/x", "http://jira.example.com/jira/x", "https://jira.example.org/jira/x", "https://jira.example.com.evil.test/x"):
            with self.subTest(location=location):
                with self.assertRaises(RedirectRefused):
                    transport._same_origin(location, "/jira/a")

    def test_a_host_written_as_an_address_with_brackets_works_and_a_broken_one_is_refused(self):
        transport = HttpTransport(Settings(base_url="http://[::1]:8080/jira", pat=TOKEN))
        self.assertEqual(transport._same_origin("http://[::1]:8080/jira/x", "/jira/a"), "/jira/x")
        for location in ("http://[::1:8080/x", "http://127.0.0.1:notaport/x"):
            with self.subTest(location=location):
                with self.assertRaises(RedirectRefused):
                    transport._same_origin(location, "/jira/a")

    def test_a_connection_that_the_server_closes_after_the_redirect_is_opened_again(self):
        def answer(handler):
            if handler.path.endswith("/a"):
                reply(handler, 302, b"", {"Location": "/jira/b", "Connection": "close"})
                handler.close_connection = True
            else:
                reply(handler, 200, b"after")

        server = self.serve(answer)
        self.assertEqual(self.transport(server).request("GET", "/a", redirects=3).body, b"after")
        self.assertEqual(server.connections, 2)


class AnnouncedLengthTest(TransportCase):
    """A header can hold anything: `str.isdigit` takes `²`, `int()` refuses it, and `int()` refuses a number of thousands of digits."""

    class Answer:
        def __init__(self, value):
            self.value = value

        def getheader(self, name, default=None):
            return self.value if name == "Content-Length" else default

    def test_only_a_plain_number_is_a_length(self):
        for value, expected in (
            ("5", 5), (" 12 ", 12), ("0", 0), ("123456789012345678", 123456789012345678), ("\u00b2", None), ("\u0663", None), ("9" * 5000, None),
            ("1e3", None), ("-5", None), ("+7", None), ("0x10", None), ("12 34", None), ("", None), (None, None), ("9" * 19, None),
        ):
            with self.subTest(value=(value or "")[:12]):
                self.assertEqual(_announced_length(self.Answer(value)), expected)

    def test_an_answer_with_such_a_length_is_read_to_its_end_and_limited_by_what_arrives(self):
        for value in ("\u00b2", "\u00b9\u00b3", "9" * 5000, "1e3", "-5"):
            def odd(handler, value=value):
                handler.send_response(200)
                handler.send_header("Content-Length", value)
                handler.end_headers()
                handler.wfile.write(b"x" * 3000)
                handler.close_connection = True

            server = self.serve(odd)
            with self.subTest(value=value[:12]):
                transport = self.transport(server)
                self.assertEqual(transport.request("GET", "/odd", max_bytes=5000).body, b"x" * 3000)  # no ValueError, and the body is what arrived
                with self.assertRaises(ResponseTooLarge):
                    transport.request("GET", "/odd", max_bytes=1000)  # the limit still holds, on what arrives


class ErrorAnswerTest(TransportCase):
    """The limit is for the file that was asked for. The explanation of why there is none is read with a small cap of its own."""

    def test_an_error_answer_is_never_refused_for_its_size_and_keeps_its_status(self):
        body = json.dumps({"errorMessages": ["No permission " + "x" * 300], "errors": {}}).encode()
        for status in (400, 401, 403, 404, 500, 501):
            server = self.serve(lambda handler, status=status: reply(handler, status, body, {"Content-Type": "application/json"}))
            with self.subTest(status=status):
                response = self.transport(server).request("GET", "/f", max_bytes=10)
                self.assertEqual((response.status, response.body), (status, body))

    def test_a_long_error_body_is_cut_and_the_connection_is_not_used_again(self):
        big = b"E" * 300_000
        server = self.serve(lambda handler: reply(handler, 404, big) if handler.path.endswith("missing") else reply(handler, 200, b"fine"))
        transport = self.transport(server)
        response = transport.request("GET", "/missing", max_bytes=10)
        self.assertEqual((response.status, response.body), (404, big[: 64 * 1024]))
        self.assertIsNone(transport._connection)  # the rest of the body is still on the wire
        self.assertEqual(transport.request("GET", "/ok", max_bytes=10).body, b"fine")
        self.assertEqual(server.connections, 2)

    def test_a_short_error_body_leaves_the_connection_to_be_used_again(self):
        server = self.serve(lambda handler: reply(handler, 403, b"no") if handler.path.endswith("denied") else reply(handler, 200, b"fine"))
        transport = self.transport(server)
        self.assertEqual(transport.request("GET", "/denied", max_bytes=10).status, 403)
        self.assertEqual(transport.request("GET", "/ok", max_bytes=10).body, b"fine")
        self.assertEqual(server.connections, 1)

    def test_a_gzip_error_body_is_decoded_as_far_as_it_goes_and_a_damaged_one_is_empty(self):
        text = b"No permission to see this attachment. " * 20
        packed = gzip.compress(text)
        bodies = {"/intact": packed, "/cut": packed[:-30], "/garbage": b"\x1f\x8b" + b"garbage that is no deflate stream" * 3, "/bomb": gzip.compress(b"\0" * 5_000_000)}
        server = self.serve(lambda handler: reply(handler, 403, bodies[handler.path.removeprefix("/jira")], {"Content-Encoding": "gzip"}))
        transport = self.transport(server)
        self.assertEqual(transport.request("GET", "/intact", max_bytes=10).body, text)
        cut = transport.request("GET", "/cut", max_bytes=10)
        self.assertEqual((cut.status, text.startswith(cut.body), len(cut.body) > 0), (403, True, True))  # what could be unpacked
        garbage = transport.request("GET", "/garbage", max_bytes=10)
        self.assertEqual((garbage.status, garbage.body), (403, b""))  # the status is the answer
        bomb = transport.request("GET", "/bomb", max_bytes=10)
        self.assertEqual((bomb.status, len(bomb.body)), (403, 64 * 1024))  # cut, never expanded to the end

    def test_a_success_is_still_limited_and_says_what_it_was(self):
        server = self.serve(lambda handler: reply(handler, 200, b"<html>" + b"x" * 5000, {"Content-Type": "text/html; charset=UTF-8"}))
        with self.assertRaises(ResponseTooLarge) as caught:
            self.transport(server).request("GET", "/page", max_bytes=1000)
        self.assertEqual((caught.exception.status, caught.exception.content_type), (200, "text/html; charset=UTF-8"))

    def test_the_body_of_a_redirect_is_read_with_the_small_cap_too(self):
        routes = {"/jira/a": (302, {"Location": "/jira/b"}, b"R" * 200_000), "/jira/b": (200, {}, b"target")}

        def answer(handler):
            status, headers, body = routes[handler.path]
            reply(handler, status, body, headers)

        server = self.serve(answer)
        response = self.transport(server).request("GET", "/a", max_bytes=1000, redirects=3)
        self.assertEqual((response.status, response.body), (200, b"target"))  # the page that said `moved` is no file that the limit is for
        self.assertEqual(server.connections, 2)  # the connection that held the rest of that page was not trusted


class InvalidAddressTest(TransportCase):
    def test_an_address_that_no_url_can_hold_is_a_usage_error_without_a_retry(self):
        server = self.serve(lambda handler: reply(handler, 200, b"{}"))
        slept = []
        transport = HttpTransport(Settings(base_url=server.base_url, pat=TOKEN, retry=3), sleep=slept.append)
        self.addCleanup(transport.close)
        for path in ("/x\n", "/a b", "/\x00", "/tab\t", "/del\x7f"):
            with self.subTest(path=ascii(path)):
                with self.assertRaises(UsageError) as caught:
                    transport.request("GET", path)
                self.assertEqual(caught.exception.exit_code, 2)
                self.assertNotIn("\n", caught.exception.message)  # the address is not echoed
                self.assertTrue(caught.exception.hint)
        self.assertEqual((server.requests, slept), ([], []))  # nothing was sent and nothing was waited for
        self.assertEqual(transport.request("GET", "/fine").status, 200)  # and the transport is as good as it was


if __name__ == "__main__":
    unittest.main()
