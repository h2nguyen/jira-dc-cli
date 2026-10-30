"""Attachments through the whole program: the real adapters against the mock Jira, with no fakes, and what really goes over the wire."""
import os
import time
import unittest

from jira_dc_cli.domain.attachments import UploadFile
from jira_dc_cli.domain.errors import AuthError, ConfigError, NotFoundError, RejectedError, UnavailableError, UsageError, ValidationError
from jira_dc_cli.domain.model import Attachment
from tests.integration.test_end_to_end import TOKEN, EndToEnd
from tests.integration.test_http_and_gateway import GatewayTestCase
from tests.support.mock_attachments import add_attachment, attachments_routes, parse_multipart
from tests.unit.test_attachments_files import make_folder_link
from tests.unit.test_attachments_ooxml import docx, para
from tests.unit.test_attachments_transport import Scripted, reply

EVERY_BYTE = bytes(range(256)) * 50
PEOPLE = ("jane.doe", "Jane Doe", "jdoe")


def hang_ups_are_quiet(jira):
    """A client that closes a connection with unread data resets it, and the server of the mock would print a traceback for that."""
    jira._server.handle_error = lambda request, client_address: None


class AttachmentsGatewayTest(GatewayTestCase):
    """The four calls of the port, over HTTP."""

    def setUp(self):
        super().setUp()
        hang_ups_are_quiet(self.jira)
        self.routes = attachments_routes(self.jira)

    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])

    def test_the_attachments_of_an_issue_in_one_request_with_nothing_else(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        add_attachment(self.jira, "PROJ-1", "shot.png", EVERY_BYTE, "image/png")
        found = self.gateway.list_attachments("PROJ-1")
        self.assertEqual([(item.filename, item.mime_type, item.size) for item in found], [("notes.txt", "text/plain", 5), ("shot.png", "image/png", len(EVERY_BYTE))])
        self.assertEqual(found[0].author.name, "jdoe")  # the gateway maps what Jira says; hiding is the use case's job
        self.assertEqual([(item.method, item.path, item.query) for item in self.jira.requests], [("GET", "/rest/api/2/issue/PROJ-1", {"fields": ["attachment"]})])

    def test_an_issue_without_attachments_and_an_unknown_one(self):
        self.assertEqual(self.gateway.list_attachments("PROJ-2"), [])
        with self.assertRaises(NotFoundError):
            self.gateway.list_attachments("PROJ-999")

    def test_the_limits(self):
        self.assertEqual((self.gateway.attachment_limits().enabled, self.gateway.attachment_limits().upload_limit), (True, 10 * 1024 * 1024))
        self.routes.enabled, self.routes.upload_limit = False, 0
        limits = self.gateway.attachment_limits()
        self.assertEqual((limits.enabled, limits.upload_limit), (False, 0))

    def test_a_download_is_byte_for_byte_and_sent_to_the_host_of_the_base_url_only(self):
        item = add_attachment(self.jira, "PROJ-1", "shot.png", EVERY_BYTE, "image/png")
        elsewhere = Scripted(lambda handler: reply(handler, 200, b"not yours"))
        self.addCleanup(elsewhere.stop)
        found = self.gateway.list_attachments("PROJ-1")[0]
        poisoned = Attachment(found.id, found.filename, found.mime_type, found.size, found.created, found.author, content_url=f"{elsewhere.origin}/secure/attachment/{item['id']}/shot.png")
        self.assertEqual(self.gateway.download_attachment(poisoned, 1_000_000), EVERY_BYTE)
        self.assertEqual(elsewhere.requests, [])  # the address in the metadata was never used
        request = self.jira.sent("GET", "/secure/attachment/")[0]
        self.assertEqual(request.path, f"/secure/attachment/{item['id']}/shot.png")
        self.assertEqual(request.headers["Accept-Encoding"], "identity")
        self.assertEqual(request.headers["Authorization"], self.jira.requests[0].headers["Authorization"])

    def test_the_file_name_goes_into_the_address_quoted_so_that_it_arrives_as_it_is(self):
        names = ["my file.png", "Übersicht (1) 日本.txt", "a#b?c%d&e=f.txt", "plus+sign.txt", "semi;colon,comma.txt"]
        for name in names:
            add_attachment(self.jira, "PROJ-1", name, name.encode("utf-8"), "text/plain")
        for item in self.gateway.list_attachments("PROJ-1"):
            with self.subTest(name=item.filename):
                self.assertEqual(self.gateway.download_attachment(item, 1000), item.filename.encode("utf-8"))

    def test_an_id_that_could_change_the_address_is_refused_before_a_request(self):
        for identifier in ("../1", "1/2", "1?x", "", " 1", "x" * 60, "1#frag"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValidationError):
                    self.gateway.download_attachment(Attachment(identifier, "a.txt", "text/plain", 1), 100)
        self.assertEqual(self.jira.requests, [])

    def test_the_limit_on_the_size_is_enforced_on_the_wire_for_every_way_a_server_can_send(self):
        add_attachment(self.jira, "PROJ-1", "big.bin", b"b" * 5000, "application/octet-stream")
        big = self.gateway.list_attachments("PROJ-1")[0]
        for mode, announced in (("normal", True), ("chunked", False), ("endless", False), ("big-length", True)):
            with self.subTest(mode=mode):
                self.routes.mode = mode
                started = time.monotonic()
                with self.assertRaises(ValidationError) as caught:
                    self.gateway.download_attachment(big, 1000)
                self.assertLess(time.monotonic() - started, 20)
                self.assertIn("big.bin is", caught.exception.message)
                self.assertEqual("The download was stopped" in caught.exception.message, not announced)
                self.assertIn("--max-bytes", caught.exception.hint)
        self.routes.mode = "normal"
        self.assertEqual(len(self.gateway.download_attachment(big, 5000)), 5000)  # the same connection logic still works afterwards

    def test_redirects_on_the_same_host_are_followed_with_the_token(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        for mode in ("redirect", "redirect-relative"):
            with self.subTest(mode=mode):
                self.routes.mode = mode
                self.jira.reset_requests()
                self.assertEqual(self.gateway.download_attachment(found, 100), b"hello")
                requests = self.jira.sent("GET", "/secure/attachment/")
                self.assertEqual([item.query for item in requests], [{}, {"hop": ["1"]}])
                self.assertEqual(requests[0].headers["Authorization"], requests[1].headers["Authorization"])

    def test_a_redirect_to_another_host_is_refused_and_no_request_reaches_it(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        elsewhere = Scripted(lambda handler: reply(handler, 200, b"a place that must not see the token"))
        self.addCleanup(elsewhere.stop)
        self.routes.mode, self.routes.elsewhere = "redirect-away", elsewhere.origin
        with self.assertRaises(ConfigError) as caught:
            self.gateway.download_attachment(found, 100)
        self.assertIn("nothing was sent there", caught.exception.message)
        self.assertIn(elsewhere.origin, caught.exception.message)
        self.assertNotIn("/secure/attachment", caught.exception.message)  # only the host, never the rest of the address
        self.assertEqual((elsewhere.requests, elsewhere.connections), ([], 0))  # no request and no Authorization reached it

    def test_a_loop_of_redirects_ends_after_three_hops(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        self.routes.mode = "redirect-loop"
        self.jira.reset_requests()
        with self.assertRaises(Exception) as caught:
            self.gateway.download_attachment(found, 100)
        self.assertIn("more than 3 times", caught.exception.message)
        self.assertEqual(len(self.jira.sent("GET", "/secure/attachment/")), 4)

    def test_an_id_with_a_line_feed_is_refused_before_a_request_and_not_retried(self):
        item = add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        for identifier in (item["id"] + "\n", item["id"] + "\r\n", "\n" + item["id"]):
            with self.subTest(identifier=ascii(identifier)):
                with self.assertRaises(ValidationError):
                    self.gateway.download_attachment(Attachment(identifier, "notes.txt", "text/plain", 5), 100)
        self.assertEqual(self.jira.requests, [])

    def test_an_address_that_no_url_can_hold_is_a_usage_error_and_nothing_is_sent(self):
        for path in ("/issue/PROJ-1\n", "/issue/PRO J-1", "/issue/PROJ-1\x00"):
            with self.subTest(path=ascii(path)):
                with self.assertRaises(UsageError) as caught:
                    self.gateway.request("GET", path)
                self.assertEqual(caught.exception.exit_code, 2)
        self.assertEqual(self.jira.requests, [])

    def test_the_address_of_a_refused_redirect_is_shown_as_text_only(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        for location in ("https://evil\x1bc.example/x", "https://evil\u009b2J.example/x", "https://evil\x1b(0.example:99/x"):
            self.routes._download = lambda handler, rest, query, location=location: reply(handler, 302, b"", {"Location": location})
            with self.subTest(location=ascii(location)):
                with self.assertRaises(ConfigError) as caught:
                    self.gateway.download_attachment(found, 100)
                self.assertIn("another address", caught.exception.message)
                for control in ("\x1b", "\u009b"):
                    self.assertNotIn(control, caught.exception.message)  # the host that Jira named is text, never a command to the terminal
                self.assertIn("evil", caught.exception.message)

    def test_a_small_limit_never_hides_what_jira_said_about_a_failure(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        for status, error, exit_code in ((401, AuthError, 5), (403, AuthError, 5), (404, NotFoundError, 4), (400, RejectedError, 6), (500, UnavailableError, 7)):
            self.jira.fail_next("/secure/attachment/", status, body={"errorMessages": ["The reason. " + "x" * 300], "errors": {}})
            with self.subTest(status=status):
                with self.assertRaises(error) as caught:
                    self.gateway.download_attachment(found, 10)  # the body of the answer is 340 bytes
                self.assertEqual(caught.exception.exit_code, exit_code)
                self.assertNotIn("more than the limit", caught.exception.message)
        self.assertEqual(self.gateway.download_attachment(found, 5), b"hello")  # and the limit is as strict as ever for the file

    def test_a_login_page_that_is_larger_than_a_small_limit_is_still_a_login_page(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        self.routes.mode = "html"
        with self.assertRaises(AuthError) as caught:
            self.gateway.download_attachment(found, 10)
        self.assertIn("web page instead of notes.txt", caught.exception.message)
        self.assertEqual(caught.exception.details["status"], 200)

    def test_a_body_shorter_than_announced_is_never_returned_as_the_file(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello" * 100, "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        self.routes.mode = "short-body"
        self.jira.reset_requests()
        with self.assertRaises(UnavailableError) as caught:
            self.gateway.download_attachment(found, 10_000)
        self.assertIn("IncompleteRead", caught.exception.message)
        self.assertEqual(len(self.jira.sent("GET", "/secure/attachment/")), 3)  # tried again like any read, then given up

    def test_a_web_page_where_a_file_was_asked_for_is_not_saved_as_the_file(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        add_attachment(self.jira, "PROJ-1", "page.html", b"<html>mine</html>", "text/html")
        notes, page = self.gateway.list_attachments("PROJ-1")
        self.routes.mode = "html"
        with self.assertRaises(AuthError) as caught:
            self.gateway.download_attachment(notes, 1000)
        self.assertIn("web page instead of notes.txt", caught.exception.message)
        self.assertIn("login page", caught.exception.hint)
        self.assertIn(b"Log in", self.gateway.download_attachment(page, 1000))  # an attachment that is a web page is fine

    def test_errors_of_a_download_carry_a_hint_that_names_what_to_do(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        self.jira.fail_next("/secure/attachment/", 403)
        with self.assertRaises(AuthError) as caught:
            self.gateway.download_attachment(found, 100)
        self.assertEqual(caught.exception.exit_code, 5)
        self.assertIn("permission to browse the project", caught.exception.hint)
        del self.routes.data[found.id]  # deleted behind our back
        with self.assertRaises(NotFoundError) as caught:
            self.gateway.download_attachment(found, 100)
        self.assertIn("jira attachments KEY", caught.exception.hint)

    def test_a_read_is_retried_and_a_write_is_not(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        found = self.gateway.list_attachments("PROJ-1")[0]
        self.jira.fail_next("/secure/attachment/", 503, times=2)
        self.assertEqual(self.gateway.download_attachment(found, 100), b"hello")
        self.assertEqual(len(self.jira.sent("GET", "/secure/attachment/")), 3)
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/attachments", 503, times=5, method="POST")
        with self.assertRaises(Exception):
            self.gateway.upload_attachments("PROJ-1", [UploadFile("a.txt", "text/plain", 1, b"a")])
        self.assertEqual(len(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/attachments")), 1)

    def test_an_upload_is_a_multipart_post_with_the_header_that_jira_requires(self):
        files = [UploadFile("shot.png", "image/png", len(EVERY_BYTE), EVERY_BYTE), UploadFile("Übersicht 日本 \"x\".txt", "text/plain", 5, b"hello")]
        created = self.gateway.upload_attachments("PROJ-1", files)
        self.assertEqual([(item.filename, item.mime_type, item.size) for item in created], [("shot.png", "image/png", len(EVERY_BYTE)), ("Übersicht 日本 %22x%22.txt", "text/plain", 5)])
        request = self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/attachments")[0]
        self.assertEqual(request.headers["X-Atlassian-Token"], "no-check")
        self.assertTrue(request.headers["Content-Type"].startswith("multipart/form-data; boundary="))
        parts = parse_multipart(request.headers["Content-Type"], request.raw)
        self.assertEqual([(part["name"], part["filename"], part["type"], part["data"]) for part in parts], [("file", "shot.png", "image/png", EVERY_BYTE), ("file", "Übersicht 日本 %22x%22.txt", "text/plain", b"hello")])
        self.assertEqual([item["filename"] for item in self.jira.issues["PROJ-1"]["fields"]["attachment"]], ["shot.png", "Übersicht 日本 %22x%22.txt"])
        self.assertEqual(self.routes.data[created[0].id], EVERY_BYTE)

    def test_the_mock_answers_like_jira_when_the_header_is_missing_or_the_issue_is_unknown(self):
        body = b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\nx\r\n--b--\r\n'
        multipart = {"Content-Type": "multipart/form-data; boundary=b"}
        send = lambda path, **headers: self.transport.request("POST", path, body=body, headers={**multipart, **headers}).status  # noqa: E731
        self.assertEqual(send("/rest/api/2/issue/PROJ-1/attachments"), 403)  # no X-Atlassian-Token: the XSRF check
        self.assertEqual(send("/rest/api/2/issue/PROJ-999/attachments", **{"X-Atlassian-Token": "no-check"}), 404)
        self.assertEqual(self.transport.request("POST", "/rest/api/2/issue/PROJ-1/attachments", body=b"{}", headers={"X-Atlassian-Token": "no-check"}).status, 400)
        self.assertEqual(send("/rest/api/2/issue/PROJ-1/attachments", **{"X-Atlassian-Token": "no-check"}), 200)
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["attachment"][0]["filename"], "a.txt")

    def test_the_mock_answers_the_metadata_of_one_attachment(self):
        item = add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        self.assertEqual(self.gateway.request("GET", f"/attachment/{item['id']}")["filename"], "notes.txt")
        with self.assertRaises(NotFoundError):
            self.gateway.request("GET", "/attachment/99999")
        self.routes.enabled = False
        with self.assertRaises(NotFoundError):
            self.gateway.request("GET", f"/attachment/{item['id']}")  # the reference: 404 when the feature is off

    def test_errors_of_an_upload_say_which_permission_or_limit(self):
        files = [UploadFile("a.txt", "text/plain", 1, b"a")]
        for status, error, words in ((403, AuthError, "Create Attachments"), (404, NotFoundError, "upload limit"), (413, RejectedError, "proxy")):
            with self.subTest(status=status):
                self.jira.fail_next("/rest/api/2/issue/PROJ-1/attachments", status, method="POST", body={"errorMessages": ["No."]})
                with self.assertRaises(error) as caught:
                    self.gateway.upload_attachments("PROJ-1", files)
                self.assertIn(words, caught.exception.hint)
        self.routes.upload_limit, self.routes.limit_status = 3, 404  # what the reference says for a file over the limit
        with self.assertRaises(NotFoundError) as caught:
            self.gateway.upload_attachments("PROJ-1", [UploadFile("big.txt", "text/plain", 10, b"0123456789")])
        self.assertIn("upload limit", caught.exception.hint)
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"].get("attachment"), None)


class AttachmentsFlowTest(EndToEnd):
    def setUp(self):
        super().setUp()
        hang_ups_are_quiet(self.jira)
        self.routes = attachments_routes(self.jira)
        self.out = self.folder / "out"

    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])

    def secure(self):
        return self.jira.sent("GET", "/secure/attachment/")

    def test_list_and_the_issue_agree_and_no_person_is_shown(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        add_attachment(self.jira, "PROJ-1", "shot.png", EVERY_BYTE, "image/png")
        result = self.cli("attachments", "PROJ-1", "--json")
        self.assertEqual(result.code, 0, result.err)
        listed = result.json()["attachments"]
        self.assertEqual([(item["filename"], item["mimeType"], item["size"], item["author"]) for item in listed], [("notes.txt", "text/plain", 5, None), ("shot.png", "image/png", len(EVERY_BYTE), None)])
        for person in PEOPLE:
            self.assertNotIn(person, result.out)
        self.assertEqual(self.cli("attachments", "PROJ-1", "--with-users", "--json").json()["attachments"][0]["author"]["name"], "jdoe")
        issue = self.cli("issue", "PROJ-1", "--json").json()
        self.assertEqual((issue["attachmentTotal"], [item["id"] for item in issue["attachments"]]), (2, [item["id"] for item in listed]))
        self.assertEqual(self.cli("attachments", "PROJ-2", "--json").json(), {"key": "PROJ-2", "total": 0, "attachments": []})
        self.assertEqual(self.cli("attachments", "PROJ-999").code, 4)

    def test_download_everything_byte_for_byte_and_the_names_are_safe(self):
        add_attachment(self.jira, "PROJ-1", "my shot.png", EVERY_BYTE, "image/png")
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"other", "text/plain")
        add_attachment(self.jira, "PROJ-1", "..\\..\\escape.txt", b"x", "text/plain")
        add_attachment(self.jira, "PROJ-1", "CON.txt", b"x", "text/plain")
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--json")
        self.assertEqual(result.code, 0, result.err)
        names = sorted(path.name for path in self.out.iterdir())
        self.assertEqual(names, ["_CON.txt", "escape.txt", "my shot.png", "notes-10002.txt", "notes-10003.txt"])
        self.assertEqual((self.out / "my shot.png").read_bytes(), EVERY_BYTE)
        self.assertEqual((self.out / "notes-10003.txt").read_bytes(), b"other")
        self.assertFalse((self.folder / "escape.txt").exists())
        self.assertEqual([item["size"] for item in result.json()["attachments"]][:1], [len(EVERY_BYTE)])
        self.assertNotIn("\x89", result.out)

    def test_download_one_by_id_and_by_name_into_the_default_folder(self):
        item = add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        shot = add_attachment(self.jira, "PROJ-1", "shot.png", b"png", "image/png")
        original = os.getcwd()
        os.chdir(self.folder)  # the default folder is relative to where the command runs
        self.addCleanup(os.chdir, original)
        result = self.cli("download", "PROJ-1", item["id"], "--name", "SHOT.png")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual((self.folder / "tmp" / "jira-attachments" / "PROJ-1" / "notes.txt").read_bytes(), b"hello")
        self.assertEqual((self.folder / "tmp" / "jira-attachments" / "PROJ-1" / "shot.png").read_bytes(), b"png")
        self.assertIn(shot["id"], result.out)

    def test_a_dry_run_asks_for_the_list_and_nothing_else(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual((self.secure(), self.out.exists()), ([], False))
        self.assertIn("would-write", result.out)

    def test_an_existing_file_is_not_overwritten_and_nothing_is_fetched_for_it(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        self.out.mkdir()
        (self.out / "notes.txt").write_bytes(b"mine")
        refused = self.cli("download", "PROJ-1", "--all", "--out", str(self.out))
        self.assertEqual(refused.code, 1)
        self.assertIn("notes.txt already exists", refused.err)
        self.assertEqual((self.secure(), (self.out / "notes.txt").read_bytes()), ([], b"mine"))
        forced = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--force")
        self.assertEqual(forced.code, 0, forced.err)
        self.assertEqual((self.out / "notes.txt").read_bytes(), b"hello")

    def test_redirects_on_the_same_host_work_and_one_to_another_host_is_an_error_that_sends_nothing_there(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        for mode in ("redirect", "redirect-relative"):
            with self.subTest(mode=mode):
                self.routes.mode = mode
                result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--force")
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual((self.out / "notes.txt").read_bytes(), b"hello")
        elsewhere = Scripted(lambda handler: reply(handler, 200, b"stolen"))
        self.addCleanup(elsewhere.stop)
        self.routes.mode, self.routes.elsewhere = "redirect-away", elsewhere.origin
        (self.out / "notes.txt").unlink()
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out))
        self.assertEqual(result.code, 1)
        self.assertIn("nothing was sent there", result.err)
        self.assertEqual((elsewhere.requests, elsewhere.connections, list(self.out.iterdir())), ([], 0, []))
        self.routes.mode = "redirect-loop"
        self.assertIn("more than 3 times", self.cli("download", "PROJ-1", "--all", "--out", str(self.out)).err)

    def test_a_file_over_the_limit_is_refused_by_the_metadata_without_a_request_and_on_the_wire_when_the_metadata_lies(self):
        add_attachment(self.jira, "PROJ-1", "big.bin", b"b" * 5000, "application/octet-stream")
        by_metadata = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--max-bytes", "1000")
        self.assertEqual(by_metadata.code, 1)
        self.assertIn("big.bin is 4.9 KB, more than the limit of 1000 B (1000 bytes)", by_metadata.err)
        self.assertIn("--max-bytes", by_metadata.err)
        self.assertEqual(self.secure(), [])
        add_attachment(self.jira, "PROJ-1", "liar.bin", b"l" * 5000, "application/octet-stream", size=10)
        for mode in ("normal", "chunked", "endless"):
            with self.subTest(mode=mode):
                self.routes.mode = mode
                result = self.cli("download", "PROJ-1", "--name", "liar.bin", "--out", str(self.out), "--max-bytes", "1000")
                self.assertEqual(result.code, 1)
                self.assertIn("liar.bin is", result.err)
                self.assertFalse((self.out / "liar.bin").exists())  # nothing half written

    def test_a_short_download_is_a_failure_and_leaves_no_file(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello" * 100, "text/plain")
        self.routes.mode = "short-body"
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), env={"JIRA_RETRY": "0"})
        self.assertEqual(result.code, 7)
        self.assertIn("Cannot reach Jira (IncompleteRead", result.err)
        self.assertFalse((self.out / "notes.txt").exists())
        self.routes.mode = "normal"
        self.assertEqual(self.cli("download", "PROJ-1", "--all", "--out", str(self.out)).code, 0)
        self.assertEqual((self.out / "notes.txt").read_bytes(), b"hello" * 100)

    def test_a_web_page_instead_of_the_file_is_an_error_not_a_file(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        self.routes.mode = "html"
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out))
        self.assertEqual(result.code, 5)
        self.assertIn("login page", result.err)
        self.assertFalse(self.out.exists())

    def test_a_403_and_a_missing_attachment_over_the_real_stack(self):
        item = add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        self.jira.fail_next("/secure/attachment/", 403, body={"errorMessages": ["No permission."]})
        denied = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--json")
        self.assertEqual((denied.code, denied.error()["code"], denied.error()["exitCode"]), (5, "AUTH", 5))
        self.assertIn("permission", denied.error()["hint"])
        del self.routes.data[item["id"]]
        self.assertEqual(self.cli("download", "PROJ-1", "--all", "--out", str(self.out)).code, 4)

    def test_text_is_read_and_a_screenshot_is_not_even_fetched(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", "Grüße to [~jdoe] <jane.doe@example.com>\r\nSecond line\r\n".encode("cp1252"), "application/octet-stream")
        add_attachment(self.jira, "PROJ-1", "spec.docx", docx(para("Goals", "Heading1") + para("Ship it.")), "application/octet-stream")
        add_attachment(self.jira, "PROJ-1", "shot.png", EVERY_BYTE, "image/png")
        result = self.cli("download", "PROJ-1", "--all", "--text", "--json")
        self.assertEqual(result.code, 0, result.err)
        notes, spec, shot = result.json()["attachments"]
        self.assertEqual(notes["text"], "Grüße to [~user] <[email]>\nSecond line\n")  # decoded, redacted, plain line ends
        self.assertEqual((spec["kind"], spec["text"]), ("docx", "# Goals\nShip it."))
        self.assertEqual((shot["action"], shot["text"]), ("not-text", ""))
        self.assertEqual([item.path.rsplit("/", 1)[1] for item in self.secure()], ["notes.txt", "spec.docx"])  # no request for the image
        self.assertEqual(list(self.folder.glob("**/*.png")), [])
        shown = self.cli("download", "PROJ-1", "--name", "notes.txt", "--text", "--with-users").out
        self.assertIn("[~jdoe] <jane.doe@example.com>", shown)

    def test_a_cut_text_says_how_long_it_is(self):
        add_attachment(self.jira, "PROJ-1", "long.log", "\n\n".join(f"entry {n}: " + "word " * 12 for n in range(60)).encode(), "text/plain")
        entry = self.cli("download", "PROJ-1", "--name", "long.log", "--text", "--max-chars", "400", "--json").json()["attachments"][0]
        self.assertEqual((entry["truncated"], len(entry["text"]) <= 400, entry["totalChars"] > 400), (True, True, True))

    def test_upload_and_read_back_what_was_attached(self):
        source = self.folder / "Bericht Übersicht.txt"
        source.write_bytes("Zeile eins\r\nZeile zwei\r\n".encode("utf-8"))
        shot = self.folder / "shot.bin"
        shot.write_bytes(EVERY_BYTE)
        result = self.cli("attach", "PROJ-1", str(source), str(shot), "--comment", "Both files.", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual([(item["filename"], item["size"]) for item in data["attached"]], [("Bericht Übersicht.txt", 24), ("shot.bin", len(EVERY_BYTE))])
        self.assertEqual(data["commentId"], "11")
        request = self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/attachments")[0]
        parts = parse_multipart(request.headers["Content-Type"], request.raw)
        self.assertEqual([(part["filename"], part["data"]) for part in parts], [("Bericht Übersicht.txt", source.read_bytes()), ("shot.bin", EVERY_BYTE)])
        self.assertEqual(request.headers["X-Atlassian-Token"], "no-check")
        self.assertEqual(self.jira.comments["PROJ-1"][0]["body"], "Both files.")
        listed = self.cli("attachments", "PROJ-1", "--json").json()["attachments"]
        self.assertEqual([item["filename"] for item in listed], ["Bericht Übersicht.txt", "shot.bin"])
        self.assertEqual(listed[0]["mimeType"], "text/plain")
        text = self.cli("download", "PROJ-1", "--name", "Bericht Übersicht.txt", "--text", "--json").json()["attachments"][0]["text"]
        self.assertEqual(text, "Zeile eins\nZeile zwei\n")
        self.assertEqual(self.cli("download", "PROJ-1", "--name", "shot.bin", "--out", str(self.out)).code, 0)
        self.assertEqual((self.out / "shot.bin").read_bytes(), EVERY_BYTE)

    def test_the_dry_run_of_an_upload_reads_the_limits_and_sends_no_post(self):
        add_attachment(self.jira, "PROJ-1", "report.txt", b"older", "text/plain")
        source = self.folder / "report.txt"
        source.write_bytes(b"newer")
        result = self.cli("attach", "PROJ-1", str(source), "--dry-run", "--json")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.jira.sent("POST"), [])
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/attachment/meta")), 1)
        self.assertEqual(result.json()["limits"], {"enabled": True, "uploadLimit": 10 * 1024 * 1024})
        self.assertEqual(len(result.json()["warnings"]), 1)  # the name exists already
        self.assertIn("exists already", result.err)

    def test_a_file_over_the_limit_or_switched_off_attachments_make_no_upload_request(self):
        source = self.folder / "big.bin"
        source.write_bytes(b"x" * 100)
        self.routes.upload_limit = 50
        over = self.cli("attach", "PROJ-1", str(source))
        self.assertEqual(over.code, 1)
        self.assertIn("big.bin (100 B) is larger than the upload limit of 50 B", over.err)
        self.routes.upload_limit, self.routes.enabled = 10_000, False
        off = self.cli("attach", "PROJ-1", str(source))
        self.assertEqual(off.code, 1)
        self.assertIn("switched off", off.err)
        self.assertEqual(self.jira.sent("POST"), [])

    def test_errors_of_an_upload_over_the_real_stack(self):
        source = self.folder / "a.txt"
        source.write_bytes(b"a")
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/attachments", 403, method="POST", body={"errorMessages": ["No permission."]})
        denied = self.cli("attach", "PROJ-1", str(source), "--json")
        self.assertEqual((denied.code, denied.error()["exitCode"]), (5, 5))
        self.assertIn("Create Attachments", denied.error()["hint"])
        self.assertEqual(self.cli("attach", "PROJ-999", str(source)).code, 4)
        self.jira.reset_requests()
        missing = self.cli("attach", "PROJ-1", str(self.folder / "absent.txt"))
        self.assertEqual(missing.code, 1)
        self.assertIn("does not exist", missing.err)
        self.assertEqual(self.jira.requests, [])  # a missing file is found before Jira is asked anything

    def test_the_comment_that_fails_after_a_good_upload(self):
        source = self.folder / "a.txt"
        source.write_bytes(b"a")
        self.jira.fail_next("/rest/api/2/issue/PROJ-1/comment", 403, method="POST", body={"errorMessages": ["No comment permission."]})
        result = self.cli("attach", "PROJ-1", str(source), "--comment", "Note", "--json")
        self.assertEqual(result.code, 5)
        error = result.error()
        self.assertIn("The files were attached to PROJ-1", error["message"])
        self.assertEqual(error["details"]["attached"][0]["filename"], "a.txt")
        self.assertEqual(len(self.jira.issues["PROJ-1"]["fields"]["attachment"]), 1)

    def test_the_token_and_people_never_show_up_in_output_not_even_with_verbose(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"Mail jane.doe@example.com", "text/plain")
        env = {"JIRA_HIDE_USERS": "true"}
        outputs = [
            self.cli("attachments", "PROJ-1", "-v", env=env),
            self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "-v", env=env),
            self.cli("download", "PROJ-1", "--all", "--text", "-v", env=env),
            self.cli("attach", "PROJ-1", str(self.out / "notes.txt"), "-v", "--dry-run", env=env),
        ]
        for result in outputs:
            self.assertEqual(result.code, 0, result.err)
            self.assertNotIn(TOKEN, result.out + result.err)
            for person in (*PEOPLE, "example.com"):
                self.assertNotIn(person, result.out + result.err)
        self.assertEqual(self.cli("attachments", "PROJ-1", "--with-users", env=env).code, 1)
        self.assertEqual(self.cli("download", "PROJ-1", "--all", "--text", "--with-users", env=env).code, 1)
        verbose = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--force", "-v").err
        self.assertIn("GET /secure/attachment/10001/notes.txt -> 200", verbose)

    def test_the_configuration_file_is_never_attached_and_neither_is_a_copy_of_it(self):
        copy = self.folder / "copy.txt"
        copy.write_bytes(self.config.read_bytes())
        for source in (self.config, copy):
            for extra in ((), ("--dry-run",)):
                with self.subTest(source=source.name, extra=extra):
                    result = self.cli("attach", "PROJ-1", str(source), *extra, "--json")
                    self.assertEqual((result.code, result.error()["code"]), (2, "USAGE"))
                    self.assertNotIn(TOKEN, result.out + result.err)
        self.assertEqual(self.jira.sent("POST"), [])
        self.assertIsNone(self.jira.issues["PROJ-1"]["fields"].get("attachment"))

    def test_the_default_location_of_the_configuration_is_protected_the_same_way(self):
        default = self.folder / ".config" / "jira-dc-cli" / "config"
        default.parent.mkdir(parents=True)
        default.write_text(f"JIRA_BASE_URL={self.jira.base_url}\nJIRA_PAT={TOKEN}\nJIRA_PROJECT=PROJ\n", encoding="utf-8")
        env = {"JIRA_CONFIG_FILE": ""}  # not set: the program looks where it always looks
        attached = self.cli("attach", "PROJ-1", str(default), "--json", env=env)
        self.assertEqual((attached.code, attached.error()["code"]), (2, "USAGE"))
        self.assertIn("is the configuration file of this CLI", attached.error()["message"])
        add_attachment(self.jira, "PROJ-1", "config", b"JIRA_BASE_URL=https://evil.example/jira\n", "text/plain")
        for extra in ((), ("--force",), ("--dry-run",), ("--dry-run", "--force")):
            with self.subTest(extra=extra):
                result = self.cli("download", "PROJ-1", "--name", "config", "--out", str(default.parent), *extra, "--json", env=env)
                self.assertEqual((result.code, result.error()["code"]), (2, "USAGE"))
                self.assertIn("another folder with --out", result.error()["hint"])
        self.assertIn(TOKEN, default.read_text(encoding="utf-8"))
        self.assertEqual(self.secure(), [])  # nothing was even fetched

    def test_a_download_cannot_overwrite_the_configuration_not_with_force_not_through_a_link_not_with_another_spelling(self):
        add_attachment(self.jira, "PROJ-1", "config", b"JIRA_BASE_URL=https://evil.example/jira\n", "text/plain")
        before = self.config.read_bytes()
        link = self.folder.parent / f"{self.folder.name}-link"
        make_folder_link(self, link, self.folder)
        self.addCleanup(lambda: os.rmdir(link) if os.path.isdir(link) else None)
        spellings = {
            "the folder": str(self.folder), "with dots": str(self.folder / "." / "sub" / ".."), "through a link": str(link),
            "a relative path": os.path.relpath(self.folder),
        }
        for label, out in spellings.items():
            for extra in ((), ("--force",)):
                with self.subTest(label, extra=extra):
                    result = self.cli("download", "PROJ-1", "--name", "config", "--out", out, *extra)
                    self.assertEqual(result.code, 2, result.err)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.secure(), [])
        elsewhere = self.cli("download", "PROJ-1", "--name", "config", "--out", str(self.out))  # an attachment named config is fine in another folder
        self.assertEqual(elsewhere.code, 0, elsewhere.err)
        self.assertEqual(self.config.read_bytes(), before)

    def test_a_file_that_appears_while_a_download_runs_is_not_overwritten(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        target = self.out / "notes.txt"
        original = self.routes._download

        def racing(handler, rest, query):
            self.out.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text("PRECIOUS LOCAL DATA", encoding="utf-8")  # another program, or the person, was faster
            original(handler, rest, query)

        self.routes._download = racing
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--json")
        self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
        self.assertIn("notes.txt appeared in", result.error()["message"])
        self.assertEqual(target.read_text(encoding="utf-8"), "PRECIOUS LOCAL DATA")
        self.assertEqual(self.names(self.out), ["notes.txt"])  # and no temporary file is left behind
        forced = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--force")
        self.assertEqual(forced.code, 0, forced.err)
        self.assertEqual(target.read_bytes(), b"hello")

    def names(self, folder):
        return sorted(path.name for path in folder.iterdir())

    def test_a_download_that_fails_after_a_good_one_names_what_was_written_and_the_retry_is_as_said(self):
        for name in ("a.txt", "b.txt", "c.txt"):
            add_attachment(self.jira, "PROJ-1", name, name.encode() * 10, "text/plain")
        original = self.routes._download
        self.routes._download = lambda handler, rest, query: handler.reply(500, {"errorMessages": ["boom"]}) if rest.endswith("/b.txt") else original(handler, rest, query)
        result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--json", env={"JIRA_RETRY": "0"})
        error = result.error()
        self.assertEqual((result.code, error["code"], error["exitCode"]), (7, "UNAVAILABLE", 7))
        self.assertIn("1 of the 3 files were written", error["message"])
        self.assertEqual([item["filename"] for item in error["details"]["written"]], ["a.txt"])
        self.assertEqual((error["details"]["failed"]["filename"], error["details"]["status"]), ("b.txt", 500))
        self.assertEqual(self.names(self.out), ["a.txt"])
        self.routes._download = original
        again = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--json")
        self.assertEqual((again.code, again.error()["code"]), (1, "INVALID"))
        self.assertIn("a.txt already exists", again.error()["message"])  # as the hint said it would
        self.assertEqual(self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--force").code, 0)
        self.assertEqual(self.names(self.out), ["a.txt", "b.txt", "c.txt"])

    def test_a_folder_where_a_file_would_go_is_an_error_in_every_mode(self):
        add_attachment(self.jira, "PROJ-1", "b.txt", b"data", "text/plain")
        (self.out / "b.txt").mkdir(parents=True)
        for extra in ((), ("--force",), ("--dry-run",), ("--dry-run", "--force")):
            with self.subTest(extra=extra):
                result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), *extra)
                self.assertEqual(result.code, 1)
                self.assertIn("b.txt is a folder", result.err)
        self.assertEqual((self.secure(), self.names(self.out)), ([], ["b.txt"]))
        self.assertTrue((self.out / "b.txt").is_dir())

    def test_the_arguments_of_attach_keep_their_boundaries_and_a_path_option_is_one_whole_path(self):
        for name, content in (("x", b"1"), ("y", b"2"), ("x y", b"3"), ("a.txt", b"4"), ("b.txt", b"5")):
            (self.folder / name).write_bytes(content)
        original = os.getcwd()
        os.chdir(self.folder)  # the names are relative to where the command runs
        self.addCleanup(os.chdir, original)
        names = lambda *argv: [item["name"] for item in self.cli("attach", "PROJ-1", *argv, "--dry-run", "--json").json()["files"]]  # noqa: E731
        self.assertEqual(names("x y"), ["x y"])  # quoted: one argument, the file `x y`, though the files `x` and `y` exist too
        self.assertEqual(names("x", "y"), ["x", "y"])  # not quoted: two arguments, the two files
        self.assertEqual(names("x y", "y"), ["x y", "y"])
        self.assertEqual(names("--path", "x y"), ["x y"])  # exactly this one, whatever else exists
        self.assertEqual(names("x", "--path", "x y", "--path", "y"), ["x", "x y", "y"])
        self.assertEqual(names("a.txt b.txt"), ["a.txt", "b.txt"])  # the files of an MCP call come as one string, and no file has that name
        missing = self.cli("attach", "PROJ-1", "absent file.txt", "--dry-run")
        self.assertEqual(missing.code, 1)
        self.assertIn("Cannot attach: absent file.txt does not exist.", missing.err)
        self.assertEqual(self.cli("attach", "PROJ-1", "--path", "absent file.txt", "--dry-run").code, 1)
        self.assertEqual(self.jira.sent("POST"), [])

    def test_a_dry_run_with_many_files_does_a_few_checks_per_file(self):
        names = []
        for index in range(600):
            (self.folder / f"log{index:05d}.txt").write_text("x")
            names.append(str(self.folder / f"log{index:05d}.txt"))
        started = time.monotonic()
        result = self.cli("attach", "PROJ-1", *names, "--dry-run", "--json")
        self.assertEqual((result.code, len(result.json()["files"])), (0, 600))
        self.assertLess(time.monotonic() - started, 20)

    def test_a_small_limit_leaves_the_exit_code_and_the_message_of_a_failure_alone(self):
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        for status, code, exit_code in ((401, "AUTH", 5), (403, "AUTH", 5), (404, "NOT_FOUND", 4), (500, "UNAVAILABLE", 7)):
            self.jira.fail_next("/secure/attachment/", status, body={"errorMessages": ["The reason. " + "x" * 300], "errors": {}})
            with self.subTest(status=status):
                result = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--max-bytes", "10", "--json", env={"JIRA_RETRY": "0"})
                self.assertEqual((result.code, result.error()["code"], result.error()["exitCode"]), (exit_code, code, exit_code))
                self.assertNotIn("more than the limit", result.error()["message"])
        self.routes.mode = "html"
        page = self.cli("download", "PROJ-1", "--all", "--out", str(self.out), "--max-bytes", "10")
        self.assertEqual(page.code, 5)
        self.assertIn("login page", page.err)

    def test_a_name_that_is_no_text_never_reaches_jira(self):
        if os.name == "nt":
            self.skipTest("Windows file names cannot hold a lone surrogate")
        odd = self.folder / os.fsdecode(b"odd\xff.txt")  # a name that is not UTF-8 on a system that allows it: a lone surrogate in a Python string
        odd.write_bytes(b"data")
        result = self.cli("attach", "PROJ-1", str(odd), "--json")
        self.assertEqual(result.code, 1)
        self.assertIn("has a name that is no text", result.error()["message"])
        self.assertEqual(self.jira.sent("POST"), [])

    def test_the_api_escape_hatch_reaches_the_metadata_of_one_attachment(self):
        item = add_attachment(self.jira, "PROJ-1", "notes.txt", b"hello", "text/plain")
        self.assertEqual(self.cli("api", "GET", f"/attachment/{item['id']}").json()["filename"], "notes.txt")


if __name__ == "__main__":
    unittest.main()
