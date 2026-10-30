"""The create metadata is paged: `get_create_fields` reads the pages until the last one, whatever the server says about them."""
import unittest

from jira_dc_cli.adapters.driven.rest_validation import RestValidation


def field(name):
    return {"fieldId": name, "name": name.title(), "required": name == "summary"}


class Scripted(RestValidation):
    """A gateway whose server answers with the pages that the test scripted, one after the other, and keeps the paths it was asked."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.asked = []

    def _call(self, method, path, body=None, **options):
        self.asked.append(path)
        return self.pages.pop(0) if self.pages else self.pages_exhausted()

    def pages_exhausted(self):
        raise AssertionError(f"asked more often than scripted: {self.asked}")


class PagingTest(unittest.TestCase):
    PATH = "/issue/createmeta/PROJ/issuetypes/10200"

    def read(self, pages, project="PROJ", issue_type="10200"):
        gateway = Scripted(pages)
        return gateway.get_create_fields(project, issue_type), gateway.asked

    def test_one_page_that_says_it_is_the_last(self):
        rules, asked = self.read([{"startAt": 0, "maxResults": 1000, "total": 2, "isLast": True, "values": [field("summary"), field("priority")]}])
        self.assertEqual(sorted(rules), ["priority", "summary"])
        self.assertEqual(asked, [f"{self.PATH}?startAt=0&maxResults=1000"])

    def test_pages_are_joined_and_the_next_one_starts_after_what_arrived_not_after_what_was_asked_for(self):
        pages = [
            {"startAt": 0, "maxResults": 3, "total": 7, "isLast": False, "values": [field("summary"), field("description"), field("priority")]},
            {"startAt": 3, "maxResults": 3, "total": 7, "isLast": False, "values": [field("components"), field("fixVersions"), field("labels")]},
            {"startAt": 6, "maxResults": 3, "total": 7, "isLast": True, "values": [field("customfield_1")]},
        ]
        rules, asked = self.read(pages)
        self.assertEqual(len(rules), 7)
        self.assertEqual(rules["summary"].required, True)
        self.assertEqual([path.split("?")[1] for path in asked], ["startAt=0&maxResults=1000", "startAt=3&maxResults=1000", "startAt=6&maxResults=1000"])

    def test_the_total_ends_the_reading_when_the_server_does_not_say_last(self):
        rules, asked = self.read([{"total": 2, "values": [field("summary")]}, {"total": 2, "values": [field("priority")]}])
        self.assertEqual((sorted(rules), len(asked)), (["priority", "summary"], 2))

    def test_a_server_that_says_nothing_about_paging_is_asked_once(self):
        for page in ({"values": [field("summary")]}, [field("summary")]):
            with self.subTest(page=page):
                rules, asked = self.read([page])
                self.assertEqual((sorted(rules), len(asked)), (["summary"], 1))

    def test_not_last_and_no_total_goes_on_until_a_page_is_empty(self):
        rules, asked = self.read([{"isLast": False, "values": [field("summary")]}, {"isLast": False, "values": [field("priority")]}, {"isLast": False, "values": []}])
        self.assertEqual((sorted(rules), len(asked)), (["priority", "summary"], 3))

    def test_an_empty_first_page_is_no_rules_and_one_request(self):
        for page in ({"values": []}, {"isLast": True, "total": 0, "values": []}, {}, [], None, "text"):
            with self.subTest(page=page):
                rules, asked = self.read([page])
                self.assertEqual((rules, len(asked)), ({}, 1))

    def test_a_server_that_never_says_last_is_asked_at_most_20_times(self):
        same = {"isLast": False, "total": 99999, "values": [field("summary")]}
        rules, asked = self.read([same] * 25)
        self.assertEqual((sorted(rules), len(asked)), (["summary"], 20))

    def test_a_server_that_ignores_the_start_and_repeats_the_first_page_gives_each_field_once(self):
        same = {"isLast": False, "values": [field("summary"), field("priority")]}
        rules, asked = self.read([same] * 20)
        self.assertEqual((sorted(rules), len(asked)), (["priority", "summary"], 20))

    def test_what_is_no_field_is_left_out_and_the_names_are_quoted_in_the_path(self):
        rules, asked = self.read([{"isLast": True, "values": [field("summary"), "text", {"name": "no id"}, None]}], project="PR OJ", issue_type="10/200")
        self.assertEqual(sorted(rules), ["summary"])
        self.assertEqual(asked, ["/issue/createmeta/PR%20OJ/issuetypes/10%2F200?startAt=0&maxResults=1000"])


if __name__ == "__main__":
    unittest.main()
