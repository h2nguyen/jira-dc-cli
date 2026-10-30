"""What the CLI sends is what the Jira Data Center 11.3 REST API reference documents.

The list of documented calls (`tests/support/jira_dc_11_3_endpoints.txt`) is derived from the OpenAPI document of the reference for
Jira Software Data Center 11.3.12. The mock Jira refuses every other call, so a command that came to depend on an endpoint of
another version (or of Jira Cloud) fails its own test; this module checks the list itself and sweeps the commands.
"""
import unittest

from tests.integration.test_end_to_end import EndToEnd
from tests.support.conformance import DOCUMENTED, is_documented, template_of


class DocumentedEndpointsTest(unittest.TestCase):
    def test_the_list_says_where_it_comes_from(self):
        text = DOCUMENTED.read_text(encoding="utf-8")
        self.assertIn("11.3.12", text)
        self.assertGreater(len([line for line in text.splitlines() if line and not line.startswith("#")]), 400)

    def test_calls_that_the_reference_documents(self):
        for method, path in (
            ("GET", "/rest/api/2/issue/PROJ-1"),
            ("GET", "/rest/api/2/issue/PROJ-1/comment/10"),
            ("PUT", "/rest/api/2/issue/PROJ-1/comment/10"),
            ("POST", "/rest/api/2/issue/PROJ-1/attachments"),
            ("GET", "/rest/api/2/attachment/meta"),
            ("GET", "/rest/api/2/search"),
            ("POST", "/rest/api/2/search"),
            ("GET", "/rest/api/2/issue/createmeta/PROJ/issuetypes/10200"),
            ("GET", "/rest/agile/1.0/board"),
            ("POST", "/rest/agile/1.0/sprint/7/issue"),
            ("PUT", "/rest/agile/1.0/issue/rank"),
            ("GET", "/rest/api/2/serverInfo"),
        ):
            with self.subTest(call=f"{method} {path}"):
                self.assertTrue(is_documented(method, path))

    def test_calls_it_does_not_document(self):
        for method, path in (
            ("GET", "/rest/api/2/issue/PROJ-1/changelog"),  # Jira Cloud only: on Data Center the history is `expand=changelog`
            ("GET", "/rest/api/2/attachment/content/10"),  # Jira Cloud only
            ("POST", "/rest/api/2/issue/PROJ-1/comment/10"),  # the method is not offered there
            ("GET", "/rest/api/3/issue/PROJ-1"),  # another API version
            ("GET", "/rest/greenhopper/1.0/rapid/charts/sprintreport"),  # internal, not part of the reference
            ("GET", "/issue/PROJ-1"),  # not below /rest
        ):
            with self.subTest(call=f"{method} {path}"):
                self.assertFalse(is_documented(method, path))

    def test_a_download_address_is_allowed_for_reading_only(self):
        self.assertTrue(is_documented("GET", "/secure/attachment/10/shot.png"))
        self.assertFalse(is_documented("POST", "/secure/attachment/10/shot.png"))

    def test_the_template_of_a_call(self):
        self.assertEqual(template_of("GET", "/rest/api/2/issue/PROJ-1/comment/10"), "/api/2/issue/{issueIdOrKey}/comment/{id}")
        self.assertIsNone(template_of("GET", "/rest/api/2/nothing"))


class TheMockRefusesWhatTheReferenceDoesNotKnow(EndToEnd):
    def test_an_undocumented_call_is_a_404_and_is_listed(self):
        result = self.cli("api", "GET", "issue/PROJ-1/changelog")
        self.assertEqual(result.code, 4)
        self.assertIn("not in the Jira Data Center 11.3 REST API reference", result.err)
        self.assertEqual(self.jira.unknown_endpoints, ["GET /rest/api/2/issue/PROJ-1/changelog"])

    def test_a_documented_call_is_answered_as_usual(self):
        self.assertEqual(self.cli("api", "GET", "issue/PROJ-1?fields=summary").code, 0)
        self.assertEqual(self.jira.unknown_endpoints, [])


class WhatTheCommandsSendIsDocumented(EndToEnd):
    """A sweep over the commands: each one is run against the mock, which would answer 404 to a call the reference lacks."""

    def test_the_commands_of_a_working_day(self):
        for argv in (
            ("doctor",),
            ("ping",),
            ("issue", "PROJ-1", "--comments"),
            ("issues", "--mine", "--all"),
            ("search", "report", "--snippets", "2"),
            ("report", "--sprint", "active", "--epics"),
            ("fields", "--search", "story"),
            ("link-types",),
            ("projects",),
            ("project", "PROJ"),
            ("metadata", "PROJ"),
            ("transition", "PROJ-2", "--to", "Done", "--dry-run"),
            ("comment", "PROJ-1", "--list"),
            ("comment", "PROJ-1", "--text", "A note."),
            ("update", "PROJ-1", "--summary", "A new title"),
            ("create", "--type", "Story", "--summary", "Created"),
            ("link", "PROJ-1", "--type", "Blocks", "--to", "PROJ-2"),
            ("export", "--project", "PROJ"),
            ("branch", "PROJ-1"),
        ):
            with self.subTest(argv=argv):
                result = self.cli(*argv)
                self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.jira.unknown_endpoints, [])


if __name__ == "__main__":
    unittest.main()
