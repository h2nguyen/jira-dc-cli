"""Text files given to a command (`--file`, `--description-file`, `--fields-json @FILE`, a query file): what Windows PowerShell 5.1 writes
arrives as the text it holds, never with a byte order mark in front, and a value that is no file says so."""
import unittest

from jira_dc_cli.adapters.driven.http_transport import _retry_delay
from jira_dc_cli.application.settings import Settings, is_local_host
from jira_dc_cli.domain.errors import ConfigError
from tests.support.cli import run_cli
from tests.support.fakes import FakeGateway, MemoryConfigStore, MemoryFiles

TEXT = "Grüße aus Berlin — fertig"
ENCODINGS = {
    "UTF-8": lambda text: text.encode("utf-8"),
    "UTF-8 with a byte order mark": lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"),
    "UTF-16 with a mark": lambda text: text.encode("utf-16"),
    "UTF-16 without a mark": lambda text: text.encode("utf-16-le"),
    "Windows-1252": lambda text: text.encode("cp1252"),
}


class InputFilesTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.gateway.search_handler = lambda jql: []

    def run_cli(self, *argv, name="input.txt", data=b"", **options):
        files = MemoryFiles()
        files.write_bytes(name, data)
        return run_cli(*argv, gateway=self.gateway, files=files, **options)

    def test_a_comment_from_a_file_has_the_text_of_the_file(self):
        for label, encode in ENCODINGS.items():
            with self.subTest(label):
                self.gateway.calls.clear()
                result = self.run_cli("comment", "PROJ-1", "--file", "input.txt", data=encode(TEXT))
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.gateway.called("add_comment")[0][2]["body"], TEXT)

    def test_a_description_from_a_file_has_the_text_of_the_file(self):
        for label, encode in ENCODINGS.items():
            with self.subTest(label):
                self.gateway.calls.clear()
                result = self.run_cli("update", "PROJ-1", "--description-file", "input.txt", data=encode(TEXT))
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.gateway.called("update_issue")[0][2]["fields"]["description"], TEXT)

    def test_fields_json_from_a_file_may_start_with_a_byte_order_mark(self):
        document = '{"customfield_10001": "%s"}' % TEXT
        for label, encode in ENCODINGS.items():
            with self.subTest(label):
                self.gateway.calls.clear()
                result = self.run_cli("update", "PROJ-1", "--fields-json", "@input.txt", data=encode(document))
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.gateway.called("update_issue")[0][2]["fields"]["customfield_10001"], TEXT)

    def test_a_query_file_may_be_utf16_without_a_mark(self):
        result = self.run_cli("issues", "--jql", "@input.txt", "--json", data='project = "PROJ" ORDER BY key'.encode("utf-16-le"))
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json()["jql"], 'project = "PROJ" ORDER BY key')

    def test_a_text_that_starts_with_an_at_sign_is_read_as_a_file_and_the_error_says_how_to_search_for_it(self):
        result = run_cli("search", "@jdoe", gateway=self.gateway, files=MemoryFiles())
        self.assertEqual(result.code, 1)
        self.assertIn("jdoe", result.err)
        self.assertIn("put a space in front", result.err)
        self.assertEqual(self.gateway.calls, [])

    def test_a_query_file_is_read_up_to_a_megabyte_and_no_further(self):
        query = b"project = PROJ"
        exactly = query + b" " * (1024 * 1024 - len(query))
        self.assertEqual(self.run_cli("issues", "--jql", "@input.txt", "--json", data=exactly).code, 0)
        result = self.run_cli("issues", "--jql", "@input.txt", data=exactly + b" ")
        self.assertEqual(result.code, 2)
        self.assertIn("at most 1,048,576 bytes", result.err)

    def test_standard_input_is_decoded_like_a_file(self):
        for label, encode in ENCODINGS.items():
            with self.subTest(label):
                self.gateway.calls.clear()
                result = run_cli("comment", "PROJ-1", "--file", "-", gateway=self.gateway, stdin=encode(TEXT))
                self.assertEqual(result.code, 0, result.err)
                self.assertEqual(self.gateway.called("add_comment")[0][2]["body"], TEXT)

    def test_a_query_from_standard_input_has_no_byte_order_mark(self):
        result = run_cli("issues", "--jql", "-", "--json", gateway=self.gateway, stdin=b"\xef\xbb\xbfproject = PROJ\r\n")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.json()["jql"], "project = PROJ")

    def test_line_ends_of_standard_input_are_plain_newlines(self):
        result = run_cli("wiki", "-", stdin="# One\r\n\r\ntext\r\n".encode("utf-8"))
        self.assertEqual(result.code, 0, result.err)
        self.assertNotIn("\r", result.out)

    def test_an_empty_file_or_input_does_not_clear_a_description(self):
        for label, argv, data, stdin in (
            ("empty file", ("--description-file", "input.txt"), b"", ""),
            ("blank file", ("--description-file", "input.txt"), b"  \r\n", ""),
            ("empty standard input", ("--description-file", "-"), b"", b""),
        ):
            with self.subTest(label):
                self.gateway.calls.clear()
                result = self.run_cli("update", "PROJ-1", *argv, data=data, stdin=stdin)
                self.assertEqual(result.code, 2, result.err)
                self.assertIn("is empty", result.err)
                self.assertIn("--description ''", result.err)
                self.assertEqual(self.gateway.called("update_issue"), [])

    def test_an_empty_description_given_on_the_command_line_still_clears_it(self):
        result = self.run_cli("update", "PROJ-1", "--description", "")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.gateway.called("update_issue")[0][2]["fields"]["description"], "")


class TokenAndAddressTest(unittest.TestCase):
    def test_a_token_with_a_line_break_or_a_character_that_is_no_ascii_is_refused_without_being_printed(self):
        for token in ("SECRETpart1\nSECRETpart2", "SECRET\rpart", "SECRET part", "\ufeffSECRET", "SECRÉT", "SECRET\x00"):
            with self.subTest(token=token.encode("unicode_escape")):
                with self.assertRaises(ConfigError) as caught:
                    Settings.from_mapping({"JIRA_BASE_URL": "https://jira.example.com", "JIRA_PAT": token})
                self.assertNotIn("SECRET", caught.exception.message + caught.exception.hint)
        self.assertEqual(Settings.from_mapping({"JIRA_PAT": "MTIzNDU2.abc/def+ghi="}).pat, "MTIzNDU2.abc/def+ghi=")

    def test_setup_pat_refuses_what_is_no_token_and_saves_what_it_decoded(self):
        for text in (b"SECRET1\nSECRET2", "SECRÉT".encode("utf-8"), "SECRET TOKEN".encode("utf-8")):
            with self.subTest(text=text):
                store = MemoryConfigStore()
                result = run_cli("setup-pat", "--stdin", store=store, stdin=text)
                self.assertEqual(result.code, 1)
                self.assertNotIn("SECRET", result.err)
                self.assertEqual(store.values, {})
        for label, data in (("a byte order mark", b"\xef\xbb\xbfTOKEN123\n"), ("UTF-16", "TOKEN123\r\n".encode("utf-16"))):
            with self.subTest(label):
                store = MemoryConfigStore()
                self.assertEqual(run_cli("setup-pat", "--stdin", store=store, stdin=data).code, 0)
                self.assertEqual(store.values, {"JIRA_PAT": "TOKEN123"})

    def test_an_at_sign_after_the_host_is_refused_without_repeating_the_address(self):
        for address in (
            "ftp://svcuser:S3c/retPw@127.0.0.1:9",
            "https://svcuser:S3c/retPw@127.0.0.1:9/jira",
            "https://svcuser:S3c?retPw@jira.example.com/jira",
            "https://svcuser:S3c#retPw@jira.example.com/jira",
            "svcuser:S3c/retPw@jira.example.com/jira",
            "https://jdoe@jira.example.com:8443/jira?x=a@b",
        ):
            with self.subTest(address=address), self.assertRaises(ConfigError) as caught:
                Settings.from_mapping({"JIRA_BASE_URL": address})
            self.assertNotIn("S3c", caught.exception.message + caught.exception.hint)
            self.assertIn("@", caught.exception.message)

    def test_config_show_does_not_print_the_password_of_the_address_and_config_set_does_not_save_it(self):
        for stored in ("https://jdoe:S3cr3tpw@jira.example.com/jira", "https://jdoe@jira.example.com/jira"):
            with self.subTest(stored=stored):
                store = MemoryConfigStore({"JIRA_BASE_URL": stored, "JIRA_PAT": "TOKEN123"})
                for argv in (("config", "show"), ("config", "show", "--json")):
                    shown = run_cli(*argv, store=store, environ={})
                    self.assertEqual(shown.code, 0, shown.err)
                    self.assertNotIn("S3cr3tpw", shown.out + shown.err)
                    self.assertNotIn("jdoe", shown.out + shown.err)
                    self.assertIn("********@jira.example.com/jira", shown.out)
        odd = run_cli("config", "show", "--json", store=MemoryConfigStore({"JIRA_BASE_URL": "https://svc:S3c/retPw@host/jira"}), environ={})
        self.assertNotIn("S3c", odd.out + odd.err)
        env_shown = run_cli("config", "show", store=MemoryConfigStore(), environ={"JIRA_BASE_URL": "https://jdoe:S3cr3tpw@jira.example.com"})
        self.assertNotIn("S3cr3tpw", env_shown.out + env_shown.err)
        clean = run_cli("config", "show", store=MemoryConfigStore({"JIRA_BASE_URL": "https://jira.example.com/jira"}), environ={})
        self.assertIn("https://jira.example.com/jira", clean.out)
        store = MemoryConfigStore()
        result = run_cli("config", "set", "JIRA_BASE_URL", "https://jdoe:S3cr3tpw@jira.example.com/jira", store=store)
        self.assertEqual(result.code, 2)
        self.assertNotIn("S3cr3tpw", result.out + result.err)
        self.assertEqual(store.values, {})
        self.assertEqual(run_cli("config", "set", "JIRA_BASE_URL", "https://jira.example.com/jira", store=store).code, 0)

    def test_only_this_computer_may_use_plain_http_without_asking(self):
        for host in ("localhost", "127.0.0.1", "127.1.2.3", "::1"):
            self.assertTrue(is_local_host(host), host)
        for host in ("127.example.com", "127.0.0.1.nip.example", "localhost.example.com", "10.0.0.1", "jira.example.com", "", "::2"):
            self.assertFalse(is_local_host(host), host)

    def test_a_retry_after_that_is_not_a_number_of_seconds_is_ignored(self):
        for value in ("nan", "NaN", "garbage", "Wed, 21 Oct 2026 07:28:00 GMT"):
            self.assertEqual(_retry_delay(2, value), 2.0, value)
        self.assertEqual((_retry_delay(1, "3"), _retry_delay(1, "inf"), _retry_delay(1, "-5"), _retry_delay(1, None)), (3.0, 30.0, 0.0, 1.0))


if __name__ == "__main__":
    unittest.main()
