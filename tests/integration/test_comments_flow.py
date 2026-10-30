"""Comments through the whole program: the real adapters against the mock Jira (read one, list the recent ones, rendered text, edit one)."""
import hashlib
import unittest

from jira_dc_cli.domain.timeutil import parse_timestamp
from tests.integration.test_end_to_end import EndToEnd
from tests.support.mock_comments import routes

COMMENT_PATH = "/rest/api/2/issue/PROJ-1/comment"


class CommentsFlowTest(EndToEnd):
    def setUp(self):
        super().setUp()
        self.addCleanup(lambda: self.assertEqual(self.jira.unknown_endpoints, []))  # every call is one that the 11.3 reference documents
        self.mock = routes(self.jira)
        self.mock.seed("PROJ-1", "20", "Note of a colleague, see jane.doe@example.com", author="asmith", created="2026-10-03T10:00:00.000+0200")
        self.mock.seed(
            "PROJ-1", "21", "Internal text", created="2026-10-04T10:00:00.000+0200", updated="2026-10-05T10:00:00.000+0200", visibility={"type": "role", "value": "Developers"}
        )
        self.mock.seed("PROJ-1", "22", "* one\n* two\n\nuse {{code}} and {{ spaced }}", created="2026-10-06T10:00:00.000+0200")

    def stored(self, comment_id):
        return next(item for item in self.jira.issues["PROJ-1"]["fields"]["comment"]["comments"] if item["id"] == comment_id)

    def puts(self):
        return self.jira.sent("PUT", COMMENT_PATH)


class ReadOneTest(CommentsFlowTest):
    def test_get_prints_one_comment_with_its_times_and_visibility(self):
        data = self.cli("comment", "PROJ-1", "--get", "21", "--json").json()
        self.assertEqual((data["key"], data["id"], data["body"], data["byMe"], data["visibility"]), ("PROJ-1", "21", "Internal text", True, "role:Developers"))
        self.assertEqual((data["created"], data["updated"]), ("2026-10-04T10:00:00.000+0200", "2026-10-05T10:00:00.000+0200"))
        self.assertIsNone(data["author"])
        request = self.jira.sent("GET", f"{COMMENT_PATH}/21")[0]
        self.assertEqual(request.query, {})

    def test_a_comment_of_a_colleague_is_not_by_me_and_shows_no_person(self):
        result = self.cli("comment", "PROJ-1", "--get", "20", "--json")
        data = result.json()
        self.assertEqual((data["byMe"], data["body"]), (False, "Note of a colleague, see [email]"))
        for personal in ("asmith", "Asmith", "jane.doe", "example.com"):
            self.assertNotIn(personal, result.out)

    def test_with_users_the_author_comes_with_it_and_the_policy_forbids_that(self):
        data = self.cli("comment", "PROJ-1", "--get", "20", "--with-users", "--json").json()
        self.assertEqual(data["author"]["name"], "asmith")
        refused = self.cli("comment", "PROJ-1", "--get", "20", "--with-users", env={"JIRA_HIDE_USERS": "true"})
        self.assertEqual(refused.code, 1)
        self.assertIn("JIRA_HIDE_USERS=true", refused.err)

    def test_rendered_asks_jira_and_shows_the_text_without_html_and_with_the_raw_braces_that_jira_did_not_render(self):
        result = self.cli("comment", "PROJ-1", "--get", "22", "--rendered")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(result.out.splitlines()[1:], ["- one", "- two", "", "use `code` and {{ spaced }}"])
        self.assertNotIn("<", result.out)
        self.assertEqual(self.jira.sent("GET", f"{COMMENT_PATH}/22")[0].query, {"expand": ["renderedBody"]})
        data = self.cli("comment", "PROJ-1", "--get", "22", "--rendered", "--json").json()
        self.assertEqual(data["body"], "* one\n* two\n\nuse {{code}} and {{ spaced }}")
        self.assertEqual(data["renderedBody"], "- one\n- two\n\nuse `code` and {{ spaced }}")

    def test_a_mention_is_rendered_as_the_name_of_a_person_and_stays_out_unless_people_may_be_shown(self):
        self.mock.seed("PROJ-1", "24", "Ask [~jdoe] and [~asmith] now", created="2026-10-07T10:00:00.000+0200")
        result = self.cli("comment", "PROJ-1", "--get", "24", "--rendered", "--json")
        data = result.json()
        self.assertEqual(data["renderedBody"], "Ask [~user] and [~user] now")
        for personal in ("Jane Doe", "Alex Smith", "ViewProfile", "jdoe", "asmith"):
            self.assertNotIn(personal, result.out)
        shown = self.cli("comment", "PROJ-1", "--get", "24", "--rendered", "--with-users", "--json").json()
        self.assertIn("Jane Doe", shown["renderedBody"])
        self.assertIn("Alex Smith", shown["renderedBody"])
        listed = self.cli("comment", "PROJ-1", "--list", "--rendered", "--last", "1", "--json", env={"JIRA_HIDE_USERS": "true"})
        self.assertEqual(listed.json()["comments"][0]["renderedBody"], "Ask [~user] and [~user] now")
        self.assertNotIn("Jane Doe", listed.out)

    def test_an_unknown_comment_is_exit_4_and_so_is_an_unknown_issue(self):
        missing = self.cli("comment", "PROJ-1", "--get", "999")
        self.assertEqual(missing.code, 4)
        self.assertIn("jira comment PROJ-1 --list", missing.err)
        self.assertEqual(self.cli("comment", "PROJ-999", "--get", "20").code, 4)


class ListTest(CommentsFlowTest):
    def test_every_comment_over_as_many_pages_as_the_server_wants_to_give(self):
        self.mock.page_cap = 2  # four comments: 10, 20, 21, 22
        data = self.cli("comment", "PROJ-1", "--list", "--json").json()
        self.assertEqual([item["id"] for item in data["comments"]], ["10", "20", "21", "22"])
        self.assertEqual((data["total"], data["count"]), (4, 4))
        starts = [item.query["startAt"] for item in self.jira.sent("GET", COMMENT_PATH) if "startAt" in item.query]
        self.assertEqual(starts, [["0"], ["2"]])

    def test_each_comment_has_byme_and_a_pseudonym_and_names_stay_out(self):
        result = self.cli("comment", "PROJ-1", "--list", "--json")
        comments = result.json()["comments"]
        self.assertEqual([item["byMe"] for item in comments], [True, False, True, True])
        self.assertEqual(comments[1]["visibility"], "")
        self.assertEqual(comments[2]["visibility"], "role:Developers")
        self.assertEqual(comments[0]["authorRef"], comments[2]["authorRef"])
        self.assertNotEqual(comments[0]["authorRef"], comments[1]["authorRef"])
        for personal in ("jdoe", "asmith", "Jane", "jane.doe", "example.com"):
            self.assertNotIn(personal, result.out)

    def test_since_and_last_with_an_absolute_time(self):
        data = self.cli("comment", "PROJ-1", "--list", "--since", "2026-10-04", "--last", "2", "--json").json()
        self.assertEqual([item["id"] for item in data["comments"]], ["21", "22"])
        self.assertEqual((data["total"], data["since"]), (4, "2026-10-04T00:00:00+00:00"))

    def test_since_a_time_ago_uses_the_real_clock(self):
        self.assertEqual(self.cli("comment", "PROJ-1", "--list", "--since", "1h", "--json").json()["comments"], [])

    def test_with_the_privacy_policy_in_the_environment_no_name_appears_and_byme_does(self):
        result = self.cli("comment", "PROJ-1", "--list", "--json", env={"JIRA_HIDE_USERS": "true"})
        self.assertEqual(result.code, 0, result.err)
        comments = result.json()["comments"]
        self.assertEqual([item["byMe"] for item in comments], [True, False, True, True])
        for personal in ("jdoe", "asmith", "Jane", "example.com"):
            self.assertNotIn(personal, result.out)

    def test_rendered_asks_for_the_html_once_per_page(self):
        data = self.cli("comment", "PROJ-1", "--list", "--rendered", "--last", "1", "--json").json()["comments"][0]
        self.assertEqual(data["renderedBody"], "- one\n- two\n\nuse `code` and {{ spaced }}")
        self.assertTrue(all(item.query.get("expand") == ["renderedBody"] for item in self.jira.sent("GET", COMMENT_PATH)))

    def test_the_list_without_options_asks_for_no_expansion(self):
        self.cli("comment", "PROJ-1", "--list")
        self.assertTrue(all("expand" not in item.query for item in self.jira.sent("GET", COMMENT_PATH)))

    def test_the_token_user_is_asked_once(self):
        self.cli("comment", "PROJ-1", "--list")
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/myself")), 1)

    def references(self):
        listed = self.cli("comment", "PROJ-1", "--list", "--json", env={"JIRA_HIDE_USERS": "true"})
        return {item["id"]: item["authorRef"] for item in listed.json()["comments"]}

    def test_the_pseudonym_cannot_be_recomputed_from_the_user_names_that_users_resolve_tells(self):
        policy = {"JIRA_HIDE_USERS": "true"}  # people stay out, and `users resolve` still tells the user name of whoever fits
        references = self.references()
        names = {self.cli("users", "resolve", query, "--json", env=policy).json()["name"] for query in ("Alex", "Jane Doe")}
        self.assertEqual(names, {"asmith", "jdoe"})
        for name in names:
            unkeyed = "a-" + hashlib.sha256(f"{self.jira.base_url.casefold()}\n{name}".encode()).hexdigest()[:8]
            self.assertNotIn(unkeyed, references.values())

    def test_a_new_token_gives_new_pseudonyms_for_the_same_authors(self):
        before = self.references()
        self.assertEqual(self.references(), before)  # the same token: the same pseudonyms in every command
        self.jira.token = "rotated-token"
        self.write_config(self.jira.base_url, token="rotated-token")
        after = self.references()
        self.assertEqual(sorted(after), sorted(before))
        self.assertEqual([after[key] == before[key] for key in before], [False] * len(before))
        self.assertEqual((after["10"] == after["22"], after["10"] == after["20"]), (True, False))  # who is who is as it was

    def test_the_text_output_has_a_header_per_comment(self):
        out = self.cli("comment", "PROJ-1", "--list").out
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("--- #")]), 4)
        self.assertIn("[role:Developers] edited 2026-10-05T10:00:00.000+0200", out)


class EditTest(CommentsFlowTest):
    def test_an_edit_sends_the_text_and_prints_the_new_updated_time(self):
        result = self.cli("comment", "PROJ-1", "--edit", "22", "--text", "Replaced text")
        self.assertEqual(result.out, "Edited comment 22 of PROJ-1 (updated 2026-10-20T09:00:00.000+0200)\n")
        (sent,) = self.puts()
        self.assertEqual((sent.path, sent.body), (f"{COMMENT_PATH}/22", {"body": "Replaced text"}))
        self.assertEqual(self.stored("22")["body"], "Replaced text")
        self.assertEqual(self.stored("22")["author"]["name"], "jdoe")  # the author stays
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["updated"], "2026-10-20T09:00:00.000+0200")

    def test_the_comment_reads_back_as_edited(self):
        self.cli("comment", "PROJ-1", "--edit", "22", "--text", "Replaced text")
        data = self.cli("comment", "PROJ-1", "--get", "22", "--json").json()
        self.assertEqual((data["body"], data["updated"]), ("Replaced text", "2026-10-20T09:00:00.000+0200"))
        self.assertIn("edited 2026-10-20T09:00:00.000+0200", self.cli("comment", "PROJ-1", "--get", "22").out)

    def test_a_restricted_comment_keeps_its_restriction(self):
        self.cli("comment", "PROJ-1", "--edit", "21", "--text", "Still internal")
        (sent,) = self.puts()
        self.assertEqual(sent.body, {"body": "Still internal", "visibility": {"type": "role", "value": "Developers"}})
        self.assertEqual(self.stored("21")["visibility"], {"type": "role", "value": "Developers"})
        self.assertEqual(self.cli("comment", "PROJ-1", "--get", "21", "--json").json()["visibility"], "role:Developers")

    def test_a_dry_run_sends_no_write_and_shows_the_change(self):
        result = self.cli("comment", "PROJ-1", "--edit", "22", "--text", "* one\n* two\n\nuse {{code}} and {{spaced}}", "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("-use {{code}} and {{ spaced }}", result.out)
        self.assertIn("+use {{code}} and {{spaced}}", result.out)
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.stored("22")["body"], "* one\n* two\n\nuse {{code}} and {{ spaced }}")

    def test_a_dry_run_of_a_colleagues_comment_warns_and_a_live_edit_is_exit_5(self):
        dry = self.cli("comment", "PROJ-1", "--edit", "20", "--text", "Corrected", "--dry-run")
        self.assertEqual(dry.code, 0)
        self.assertIn("Someone else wrote this comment", dry.err)
        live = self.cli("comment", "PROJ-1", "--edit", "20", "--text", "Corrected", "--json")
        self.assertEqual((live.code, live.error()["code"]), (5, "AUTH"))
        self.assertIn("Edit All Comments", live.error()["hint"])
        self.assertEqual(live.error()["details"]["status"], 403)
        self.assertEqual(self.stored("20")["body"], "Note of a colleague, see jane.doe@example.com")

    def test_a_token_that_may_edit_all_comments_can_edit_a_colleagues_comment_and_the_author_stays(self):
        self.mock.edit_all_comments = True
        self.assertEqual(self.cli("comment", "PROJ-1", "--edit", "20", "--text", "Corrected").code, 0)
        self.assertEqual((self.stored("20")["body"], self.stored("20")["author"]["name"]), ("Corrected", "asmith"))

    def test_a_stale_comment_is_exit_3_and_nothing_is_written(self):
        result = self.cli("comment", "PROJ-1", "--edit", "21", "--text", "x", "--if-unchanged-since", "2026-10-04T12:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.puts(), [])

    def test_a_comment_not_changed_since_is_edited_and_read_twice(self):
        result = self.cli("comment", "PROJ-1", "--edit", "21", "--text", "Fresh", "--if-unchanged-since", "2026-10-06T00:00:00Z")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(len(self.jira.sent("GET", f"{COMMENT_PATH}/21")), 2)

    def test_wiki_converts_markdown_before_it_is_sent(self):
        self.cli("comment", "PROJ-1", "--edit", "22", "--text", "**bold** and `code`", "--wiki")
        self.assertEqual(self.puts()[0].body, {"body": "*bold* and {{code}}"})

    def test_unknown_comment_is_exit_4_and_nothing_is_sent(self):
        self.assertEqual(self.cli("comment", "PROJ-1", "--edit", "999", "--text", "x").code, 4)
        self.assertEqual(self.puts(), [])

    def test_jira_refusing_the_text_is_exit_6_with_a_hint_about_comments(self):
        self.jira.fail_next(f"{COMMENT_PATH}/22", 400, method="PUT", body={"errorMessages": ["Comment body can not be empty!"], "errors": {}})
        result = self.cli("comment", "PROJ-1", "--edit", "22", "--text", "x", "--json")
        self.assertEqual((result.code, result.error()["code"]), (6, "REJECTED"))
        self.assertIn("Comment body can not be empty!", result.error()["message"])
        self.assertIn("role or group", result.error()["hint"])

    def test_an_error_of_the_server_is_exit_7_and_a_write_is_not_retried(self):
        self.jira.fail_next(f"{COMMENT_PATH}/22", 503, times=5, method="PUT")
        result = self.cli("comment", "PROJ-1", "--edit", "22", "--text", "x")
        self.assertEqual(result.code, 7)
        self.assertEqual(len(self.puts()), 1)

    def test_a_wrong_token_is_exit_5_and_says_what_to_do(self):
        self.write_config(self.jira.base_url, token="wrong")
        result = self.cli("comment", "PROJ-1", "--edit", "22", "--text", "x")
        self.assertEqual(result.code, 5)
        self.assertIn("setup-pat", result.err)

    def test_an_edit_gives_a_strictly_later_updated_time_so_that_the_guard_can_tell(self):
        self.mock.seed("PROJ-1", "30", "Late comment", created="2026-10-25T08:00:00.000+0200", updated="2026-10-26T08:00:00.000+0200")
        read = "2026-10-26T08:00:00.000+0200"  # what a reader saw of the comment
        first = self.cli("comment", "PROJ-1", "--edit", "30", "--text", "Edited once", "--json").json()["updated"]
        self.assertGreater(parse_timestamp(first), parse_timestamp(read))  # a fixed time of the mock would be earlier than the comment
        self.assertEqual(parse_timestamp(self.jira.issues["PROJ-1"]["fields"]["updated"]), parse_timestamp(first))
        stale = self.cli("comment", "PROJ-1", "--edit", "30", "--text", "Edited twice", "--if-unchanged-since", read, "--json")
        self.assertEqual((stale.code, stale.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.stored("30")["body"], "Edited once")
        again = self.cli("comment", "PROJ-1", "--edit", "30", "--text", "Edited twice", "--if-unchanged-since", first, "--json")
        self.assertEqual(again.code, 0, again.err)
        self.assertGreater(parse_timestamp(again.json()["updated"]), parse_timestamp(first))

    def test_the_result_of_an_edit_holds_no_person(self):
        self.mock.edit_all_comments = True
        result = self.cli("comment", "PROJ-1", "--edit", "20", "--text", "Corrected", "--json")
        for personal in ("asmith", "Asmith", "jane.doe", "example.com", "jdoe", "Jane Doe"):
            self.assertNotIn(personal, result.out)


class AddTest(CommentsFlowTest):
    def test_a_restricted_comment_is_added_with_its_restriction(self):
        result = self.cli("comment", "PROJ-1", "--text", "Only for developers", "--visible-to", "role:Developers")
        self.assertEqual(result.code, 0, result.err)
        (sent,) = self.jira.sent("POST", COMMENT_PATH)
        self.assertEqual(sent.body, {"body": "Only for developers", "visibility": {"type": "role", "value": "Developers"}})

    def test_adding_is_as_it_was(self):
        self.assertEqual(self.cli("comment", "PROJ-1", "--text", "Hello").out, "Commented on PROJ-1 (comment 11)\n")


if __name__ == "__main__":
    unittest.main()
