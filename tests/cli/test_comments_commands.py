"""`jira comment` from the command line: --get, --edit, --list with --since, --last and --rendered, --visible-to; text, JSON and exit codes."""
import dataclasses
import json
import unittest

from jira_dc_cli.domain.model import Comment, User
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, MemoryFiles

JANE = User("jdoe", "Jane Doe", "jane.doe@example.com")
ALEX = User("asmith", "Alex Smith", "alex.smith@example.com")
COMMENT_KEYS = ["id", "created", "body", "author", "updated", "byMe", "authorRef", "visibility", "renderedBody"]


def comments():
    return [
        Comment("10", "2026-10-02T10:00:00.000+0200", "Looks fine, [~jdoe].", JANE, updated="2026-10-02T10:00:00.000+0200"),
        Comment("11", "2026-10-15T09:00:00.000+0200", "Mail jane.doe@example.com", ALEX, updated="2026-10-16T09:00:00.000+0200", visibility="role:Developers"),
        Comment("12", "2026-10-19T14:00:00.000+0200", "Latest word.", JANE, updated="2026-10-19T14:00:00.000+0200"),
    ]


class CommentCommandTestCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        issue = self.gateway.issues["PROJ-1"]
        self.gateway.issues["PROJ-1"] = dataclasses.replace(issue, comments=comments(), comment_total=3)
        self.files = MemoryFiles({"new.md": "# Title\n\n- one\n- two\n", "plain.txt": "Corrected text\n"})

    def run_cli(self, *argv, **options):
        options.setdefault("files", self.files)
        return run_cli("comment", "PROJ-1", *argv, gateway=self.gateway, **options)

    def hidden(self):
        return dataclasses.replace(DEFAULT_SETTINGS, hide_users=True)


class GetTest(CommentCommandTestCase):
    def test_text_has_the_header_and_the_text_as_stored_without_personal_data(self):
        result = self.run_cli("--get", "11")
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertRegex(lines[0], r"^--- #11 2026-10-15T09:00:00\.000\+0200 a-[0-9a-f]{8} \[role:Developers\] edited 2026-10-16T09:00:00\.000\+0200$")
        self.assertEqual(lines[1], "Mail [email]")
        for personal in ("Alex", "asmith", "jane.doe"):
            self.assertNotIn(personal, result.out)

    def test_my_own_comment_says_so(self):
        self.assertIn("(you)", self.run_cli("--get", "10").out.splitlines()[0])
        self.assertNotIn("(you)", self.run_cli("--get", "11").out)

    def test_json_has_the_keys_of_one_comment_with_the_issue_key(self):
        data = self.run_cli("--get", "10", "--json").json()
        self.assertEqual(list(data), ["key", *COMMENT_KEYS])
        self.assertEqual((data["key"], data["id"], data["byMe"], data["author"], data["visibility"], data["renderedBody"]), ("PROJ-1", "10", True, None, "", ""))
        self.assertEqual((data["created"], data["updated"]), ("2026-10-02T10:00:00.000+0200", "2026-10-02T10:00:00.000+0200"))
        self.assertEqual(self.run_cli("--get", "11", "--json").json()["visibility"], "role:Developers")

    def test_with_users_shows_the_author_and_the_text_as_it_is(self):
        text = self.run_cli("--get", "11", "--with-users").out
        self.assertIn("Alex Smith", text.splitlines()[0])
        self.assertIn("jane.doe@example.com", text)
        data = self.run_cli("--get", "11", "--with-users", "--json").json()
        self.assertEqual(data["author"], {"name": "asmith", "displayName": "Alex Smith", "email": "alex.smith@example.com"})

    def test_the_privacy_policy_refuses_with_users_and_shows_no_name_without_it(self):
        refused = self.run_cli("--get", "11", "--with-users", "--json", settings=self.hidden())
        self.assertEqual((refused.code, refused.error()["code"]), (1, "INVALID"))
        self.assertIn("JIRA_HIDE_USERS=true", refused.error()["message"])
        result = self.run_cli("--get", "11", "--json", settings=self.hidden())
        self.assertEqual(result.code, 0)
        data = result.json()
        self.assertEqual((data["byMe"], data["author"]), (False, None))
        self.assertTrue(data["authorRef"])
        for personal in ("Alex", "asmith", "Smith", "jane.doe", "alex.smith"):
            self.assertNotIn(personal, result.out)

    def test_rendered_prints_the_text_as_jira_shows_it(self):
        self.gateway.rendered["10"] = "<ol><li>first</li><li>second</li></ol><p>and <tt>code</tt></p>"
        result = self.run_cli("--get", "10", "--rendered")
        self.assertEqual(result.out.splitlines()[1:], ["1. first", "2. second", "", "and `code`"])
        self.assertNotIn("<", result.out)

    def test_rendered_in_json_is_a_key_next_to_the_body(self):
        self.gateway.rendered["10"] = "<p>Looks <b>fine</b>.</p>"
        data = self.run_cli("--get", "10", "--rendered", "--json").json()
        self.assertEqual((data["body"], data["renderedBody"]), ("Looks fine, [~user].", "Looks fine."))

    def test_a_mention_that_jira_renders_as_a_link_to_a_person_shows_no_name(self):
        self.gateway.rendered["10"] = '<p>Ask <a href="/secure/ViewProfile.jspa?name=jdoe" class="user-hover">Jane Doe</a> now</p>'
        result = self.run_cli("--get", "10", "--rendered")
        self.assertEqual(result.out.splitlines()[1:], ["Ask [~user] now"])
        for personal in ("Jane", "Doe", "jdoe", "ViewProfile"):
            self.assertNotIn(personal, result.out)
        self.assertIn("Jane Doe", self.run_cli("--get", "10", "--rendered", "--with-users").out)

    def test_a_text_that_jira_renders_wrongly_shows_its_raw_braces(self):
        self.gateway.rendered["10"] = "<p>run {{ spaced }} now</p>"
        self.assertIn("{{ spaced }}", self.run_cli("--get", "10", "--rendered").out)

    def test_without_rendered_nothing_changes_in_the_text(self):
        result = self.run_cli("--get", "10")
        lines = result.out.splitlines()
        self.assertRegex(lines[0], r"^--- #10 2026-10-02T10:00:00\.000\+0200 a-[0-9a-f]{8} \(you\)$")  # header: id, time, pseudonym, mine
        self.assertEqual(lines[1:], ["Looks fine, [~user]."])  # the stored text with its mention hidden, and nothing rendered
        self.assertEqual(self.gateway.called("get_comment")[0][3], False)
        self.assertEqual(self.run_cli("--get", "10", "--json").json()["renderedBody"], "")

    def test_an_unknown_comment_is_exit_4_with_a_hint(self):
        result = self.run_cli("--get", "99")
        self.assertEqual(result.code, 4)
        self.assertIn("jira comment PROJ-1 --list", result.err)

    def test_a_comment_id_that_is_no_number_is_refused(self):
        for comment_id in ("abc", "10/../../x", "1;2", "10?expand=x", "١٠١٠١", "１０"):  # the last two are digits of other scripts
            with self.subTest(comment_id=comment_id):
                result = self.run_cli("--get", comment_id)
                self.assertEqual(result.code, 1)
                self.assertIn("Invalid comment id", result.err)
        self.assertEqual(self.gateway.called("get_comment"), [])

    def test_the_pseudonym_is_keyed_with_the_token(self):
        def reference(token):
            settings = dataclasses.replace(DEFAULT_SETTINGS, pat=token)
            return self.run_cli("--get", "10", "--json", settings=settings).json()["authorRef"]

        self.assertEqual(reference("synthetic-token-one"), reference("synthetic-token-one"))
        self.assertNotEqual(reference("synthetic-token-one"), reference("synthetic-token-two"))


class ListTest(CommentCommandTestCase):
    def test_the_text_is_what_it_was_with_a_longer_header(self):
        result = self.run_cli("--list")
        lines = result.out.splitlines()
        self.assertEqual(len([line for line in lines if line.startswith("--- #")]), 3)
        self.assertTrue(lines[0].startswith("--- #10 2026-10-02T10:00:00.000+0200 a-"))
        self.assertIn("Looks fine, [~user].", lines)
        self.assertNotIn("Jane", result.out)

    def test_json_has_the_total_the_count_the_cut_off_and_the_new_keys(self):
        data = self.run_cli("--list", "--json").json()
        self.assertEqual((data["key"], data["total"], data["count"], data["since"]), ("PROJ-1", 3, 3, None))
        self.assertEqual(list(data["comments"][0]), COMMENT_KEYS)
        self.assertEqual([item["byMe"] for item in data["comments"]], [True, False, True])
        self.assertEqual(data["comments"][1]["visibility"], "role:Developers")
        self.assertEqual(data["comments"][1]["updated"], "2026-10-16T09:00:00.000+0200")
        self.assertTrue(all(item["author"] is None for item in data["comments"]))

    def test_since_and_last_pick_the_recent_ones(self):
        data = self.run_cli("--list", "--since", "7d", "--last", "1", "--json").json()
        self.assertEqual([item["id"] for item in data["comments"]], ["12"])
        self.assertEqual((data["total"], data["count"], data["since"]), (3, 1, "2026-10-13T12:00:00+00:00"))
        self.assertEqual([item["id"] for item in self.run_cli("--list", "--since", "2026-10-15", "--json").json()["comments"]], ["11", "12"])
        self.assertEqual([item["id"] for item in self.run_cli("--list", "--last", "2", "--json").json()["comments"]], ["11", "12"])

    def test_nothing_recent_says_so(self):
        self.assertEqual(self.run_cli("--list", "--since", "1h").out, "No comments.\n")
        self.assertEqual(self.run_cli("--list", "--since", "1h", "--json").json()["comments"], [])

    def test_a_wrong_since_is_exit_1_with_the_forms_that_work(self):
        result = self.run_cli("--list", "--since", "yesterday")
        self.assertEqual(result.code, 1)
        self.assertIn("2d", result.err)
        self.assertEqual(self.run_cli("--list", "--last", "0").code, 2)
        self.assertEqual(self.run_cli("--list", "--last", "many").code, 2)

    def test_with_users_shows_the_authors(self):
        out = self.run_cli("--list", "--with-users").out
        self.assertIn("Jane Doe", out)
        self.assertIn("Alex Smith", out)
        self.assertEqual(self.run_cli("--list", "--with-users", "--json", settings=self.hidden()).code, 1)

    def test_the_privacy_policy_leaves_byme_and_the_pseudonym_and_no_name(self):
        result = self.run_cli("--list", "--json", settings=self.hidden())
        data = result.json()
        self.assertEqual([item["byMe"] for item in data["comments"]], [True, False, True])
        self.assertEqual(len({item["authorRef"] for item in data["comments"]}), 2)
        for personal in ("jdoe", "asmith", "Jane", "Alex", "example.com"):
            self.assertNotIn(personal, result.out)

    def test_rendered_in_the_text_replaces_the_stored_text(self):
        self.gateway.rendered["12"] = "<ul><li>a</li></ul>"
        out = self.run_cli("--list", "--rendered", "--last", "1").out
        self.assertEqual(out.splitlines()[1:], ["- a"])
        data = self.run_cli("--list", "--rendered", "--last", "1", "--json").json()["comments"][0]
        self.assertEqual((data["body"], data["renderedBody"]), ("Latest word.", "- a"))

    def test_without_rendered_the_rendered_text_is_empty_in_json(self):
        self.assertTrue(all(item["renderedBody"] == "" for item in self.run_cli("--list", "--json").json()["comments"]))

    def test_select_keeps_the_keys_and_format_prints_a_table(self):
        data = self.run_cli("--list", "--select", "id,byMe", "--json").json()
        self.assertEqual(data["comments"], [{"id": "10", "byMe": True}, {"id": "11", "byMe": False}, {"id": "12", "byMe": True}])
        self.assertEqual(data["total"], 3)
        tsv = self.run_cli("--list", "--format", "tsv").out.splitlines()
        self.assertEqual(tsv[0], "ID\tCREATED\tBY\tME\tBODY")
        self.assertTrue(tsv[1].startswith("10\t2026-10-02T10:00:00.000+0200\ta-"))
        self.assertIn("\ttrue\t", tsv[1])
        md = self.run_cli("--list", "--select", "id,visibility", "--format", "md").out.splitlines()
        self.assertEqual(md[0], "| id | visibility |")
        self.assertIn("| 11 | role:Developers |", md)

    def test_json_and_a_format_together_are_a_usage_error(self):
        self.assertEqual(self.run_cli("--list", "--json", "--format", "md").code, 2)

    def test_a_list_without_comments(self):
        self.gateway.issues["PROJ-4"] = dataclasses.replace(self.gateway.issues["PROJ-4"], comments=[], comment_total=0)
        result = run_cli("comment", "PROJ-4", "--list", gateway=self.gateway)
        self.assertEqual(result.out, "No comments.\n")

    def test_the_pseudonym_of_an_author_is_the_same_in_the_list_and_in_get(self):
        listed = self.run_cli("--list", "--json").json()["comments"][1]["authorRef"]
        got = self.run_cli("--get", "11", "--json").json()["authorRef"]
        self.assertEqual(listed, got)


class EditTest(CommentCommandTestCase):
    def stored(self, comment_id):
        return next(item for item in self.gateway.issues["PROJ-1"].comments if item.id == comment_id)

    def test_a_dry_run_shows_the_change_and_what_would_be_sent_and_sends_nothing(self):
        result = self.run_cli("--edit", "10", "--text", "Looks fine, [~jdoe]. Thanks.", "--dry-run")
        self.assertEqual(result.code, 0, result.err)
        self.assertIn("Dry run: comment 10 of PROJ-1 would be edited; nothing was sent", result.out)
        self.assertIn("-Looks fine, [~user].", result.out)
        self.assertIn("+Looks fine, [~jdoe]. Thanks.", result.out)
        self.assertIn("Would send:", result.out)
        self.assertIn('"body": "Looks fine, [~jdoe]. Thanks."', result.out)
        self.assertEqual(self.gateway.called("update_comment"), [])
        self.assertEqual(self.stored("10").body, "Looks fine, [~jdoe].")

    def test_a_live_edit_says_what_it_did_and_when(self):
        result = self.run_cli("--edit", "10", "--text", "Looks fine, [~jdoe]. Thanks.")
        self.assertEqual(result.out, "Edited comment 10 of PROJ-1 (updated 2026-10-20T09:00:00.000+0200)\n")
        self.assertEqual(self.stored("10").body, "Looks fine, [~jdoe]. Thanks.")

    def test_the_text_may_come_from_a_file_or_standard_input(self):
        self.run_cli("--edit", "10", "--file", "plain.txt")
        self.assertEqual(self.stored("10").body, "Corrected text")
        self.run_cli("--edit", "10", "--file", "-", stdin="From stdin\n")
        self.assertEqual(self.stored("10").body, "From stdin")

    def test_wiki_converts_markdown_as_when_adding(self):
        self.run_cli("--edit", "10", "--file", "new.md", "--wiki")
        self.assertEqual(self.stored("10").body, "h1. Title\n\n* one\n* two")
        added = self.run_cli("--file", "new.md", "--wiki")
        self.assertEqual(added.code, 0)
        self.assertEqual(self.gateway.called("add_comment")[0][2]["body"], "h1. Title\n\n* one\n* two")

    def test_the_json_has_the_documented_keys(self):
        dry = self.run_cli("--edit", "11", "--text", "Short.", "--dry-run", "--json").json()
        self.assertEqual(list(dry), ["key", "commentId", "action", "dryRun", "payload", "updated", "byMe", "currentBody", "diff", "warnings"])
        self.assertEqual((dry["key"], dry["commentId"], dry["action"], dry["dryRun"]), ("PROJ-1", "11", "updated", True))
        self.assertEqual(dry["payload"], {"body": "Short.", "visibility": {"type": "role", "value": "Developers"}})
        self.assertEqual((dry["updated"], dry["byMe"], dry["currentBody"]), ("2026-10-16T09:00:00.000+0200", False, "Mail [email]"))
        self.assertEqual(len(dry["warnings"]), 1)
        self.gateway.edit_all_comments = True
        live = self.run_cli("--edit", "11", "--text", "Short.", "--json").json()
        self.assertEqual((live["action"], live["dryRun"], live["updated"], live["byMe"], live["warnings"]), ("updated", False, "2026-10-20T09:00:00.000+0200", None, []))

    def test_no_person_is_in_the_result_of_an_edit(self):
        self.gateway.edit_all_comments = True
        for options in ((), ("--dry-run",)):
            result = self.run_cli("--edit", "11", "--text", "Short.", "--json", *options)
            for personal in ("asmith", "Alex", "Smith", "jane.doe", "alex.smith", "jdoe"):
                self.assertNotIn(personal, result.out)

    def test_the_visibility_stays(self):
        self.gateway.edit_all_comments = True
        self.run_cli("--edit", "11", "--text", "Short.")
        self.assertEqual(self.stored("11").visibility, "role:Developers")
        self.assertEqual(self.gateway.called("update_comment")[0][3]["visibility"], {"type": "role", "value": "Developers"})

    def test_the_warning_of_a_dry_run_goes_to_standard_error(self):
        result = self.run_cli("--edit", "11", "--text", "Short.", "--dry-run")
        self.assertIn("WARNING: Someone else wrote this comment", result.err)
        self.assertNotIn("WARNING", result.out)

    def test_somebody_elses_comment_is_exit_5_with_a_hint_about_the_permission(self):
        result = self.run_cli("--edit", "11", "--text", "Short.", "--json")
        error = result.error()
        self.assertEqual((result.code, error["code"], error["exitCode"]), (5, "AUTH", 5))
        self.assertIn("Edit All Comments", error["hint"])
        self.assertEqual(self.stored("11").body, "Mail jane.doe@example.com")

    def test_a_stale_comment_is_exit_3_and_nothing_is_written(self):
        result = self.run_cli("--edit", "11", "--text", "Short.", "--if-unchanged-since", "2026-10-10T00:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(result.error()["details"]["commentId"], "11")
        self.assertEqual(self.gateway.called("update_comment"), [])

    def test_a_fresh_comment_passes_the_guard(self):
        result = self.run_cli("--edit", "10", "--text", "Short.", "--if-unchanged-since", "2026-10-17T00:00:00Z")
        self.assertEqual(result.code, 0, result.err)

    def test_the_same_text_is_no_write(self):
        result = self.run_cli("--edit", "12", "--text", "Latest word.")
        self.assertEqual(result.out, "comment 12 of PROJ-1 already has this text; nothing was sent\n")
        self.assertEqual(self.gateway.called("update_comment"), [])
        dry = self.run_cli("--edit", "12", "--text", "Latest word.", "--dry-run")
        self.assertEqual(dry.out, "Dry run: comment 12 of PROJ-1 already has this text; nothing was sent\n")

    def test_a_stand_in_for_personal_data_is_refused(self):
        result = self.run_cli("--edit", "10", "--text", "Looks fine, [~user].")
        self.assertEqual(result.code, 1)
        self.assertIn("overwrite the real one", result.err)
        self.assertEqual(self.gateway.called("update_comment"), [])

    def test_a_big_comment_shows_the_current_text_and_not_a_diff(self):
        self.gateway.issues["PROJ-1"].comments[0] = dataclasses.replace(self.stored("10"), body="word " * 800)
        result = self.run_cli("--edit", "10", "--text", "new " * 800, "--dry-run")
        self.assertIn("Current text:\nword word", result.out)
        self.assertNotIn("@@", result.out)

    def test_an_unknown_comment_is_exit_4(self):
        result = self.run_cli("--edit", "99", "--text", "x")
        self.assertEqual(result.code, 4)
        self.assertIn("jira comment PROJ-1 --list", result.err)

    def test_an_empty_text_is_refused_and_there_is_no_delete(self):
        self.assertEqual(self.run_cli("--edit", "10", "--text", "  ").code, 1)
        self.assertEqual(self.gateway.called("update_comment"), [])


class UsageTest(CommentCommandTestCase):
    def test_what_does_not_go_together_is_exit_2_and_names_the_options(self):
        cases = (
            (("--list", "--get", "10"), "--list and --get cannot be combined"),
            (("--get", "10", "--edit", "10", "--text", "x"), "--get and --edit cannot be combined"),
            (("--list", "--text", "x"), "--list cannot be combined with --text or --file"),
            (("--get", "10", "--file", "plain.txt"), "--get cannot be combined with --text or --file"),
            (("--text", "x", "--rendered"), "--rendered applies to --list and --get"),
            (("--get", "10", "--since", "2d"), "--since applies to --list"),
            (("--get", "10", "--last", "2"), "--last applies to --list"),
            (("--get", "10", "--select", "id"), "--select applies to --list"),
            (("--get", "10", "--format", "tsv"), "--format applies to --list"),
            (("--list", "--if-unchanged-since", "2026-10-01T00:00:00Z"), "--if-unchanged-since applies to --edit"),
            (("--edit", "10", "--text", "x", "--visible-to", "role:Developers"), "--visible-to applies to adding a comment"),
            (("--list", "--visible-to", "role:Developers"), "--visible-to applies to adding a comment"),
        )
        for argv, expected in cases:
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.code, 2, result.err)
                self.assertIn(expected, result.err)
        self.assertEqual(self.gateway.called("update_comment") + self.gateway.called("add_comment"), [])

    def test_nothing_to_do_names_every_mode(self):
        result = self.run_cli()
        self.assertEqual(result.code, 2)
        for word in ("--text", "--list", "--get", "--edit"):
            self.assertIn(word, result.err)

    def test_edit_needs_the_new_text(self):
        result = self.run_cli("--edit", "10")
        self.assertEqual(result.code, 2)
        self.assertIn("--edit needs the new text", result.err)

    def test_the_text_and_its_file_are_not_both_given(self):
        self.assertEqual(self.run_cli("--edit", "10", "--text", "x", "--file", "plain.txt").code, 2)

    def test_an_empty_id_is_not_a_missing_option(self):
        self.assertEqual(self.run_cli("--get", "").code, 1)


class VisibleToTest(CommentCommandTestCase):
    def test_a_restricted_comment(self):
        result = self.run_cli("--text", "Internal.", "--visible-to", "role:Developers")
        self.assertEqual(result.code, 0, result.err)
        self.assertEqual(self.gateway.called("add_comment")[0][2], {"body": "Internal.", "visibility": {"type": "role", "value": "Developers"}})

    def test_the_dry_run_shows_the_restriction_and_sends_nothing(self):
        result = self.run_cli("--text", "Internal.", "--visible-to", "group:jira-staff", "--dry-run", "--json").json()
        self.assertEqual(result["payload"], {"body": "Internal.", "visibility": {"type": "group", "value": "jira-staff"}})
        self.assertEqual(self.gateway.called("add_comment"), [])

    def test_a_restriction_that_is_neither_a_role_nor_a_group(self):
        result = self.run_cli("--text", "Internal.", "--visible-to", "everyone")
        self.assertEqual(result.code, 1)
        self.assertIn("role:NAME or group:NAME", result.err)


class AddIsUnchangedTest(CommentCommandTestCase):
    def test_adding_prints_what_it_printed(self):
        self.assertEqual(self.run_cli("--text", "Hello").out, "Commented on PROJ-1 (comment 11)\n")
        dry = self.run_cli("--text", "Hi", "--dry-run")
        self.assertIn("Dry run: PROJ-1 exists; nothing was sent", dry.out)
        data = self.run_cli("--text", "Hello again", "--json").json()
        self.assertEqual(sorted(data), ["commentId", "dryRun", "key", "payload"])

    def test_a_comment_that_is_added_may_hold_a_stand_in_and_an_edit_of_one_may_not(self):
        added = self.run_cli("--text", "see [~user] and [email]", "--json")
        self.assertEqual(added.code, 0, added.err)
        self.assertEqual(self.gateway.called("add_comment")[0][2], {"body": "see [~user] and [email]"})
        edited = self.run_cli("--edit", "12", "--text", "see [~user] and [email]")
        self.assertEqual(edited.code, 1)
        self.assertIn("The new text holds [~user], [email]", edited.err)

    def test_the_help_lists_the_new_options_with_an_example_of_each(self):
        out = run_cli("help", "comment").out
        for word in ("--get ID", "--edit ID", "--rendered", "--since WHEN", "--last N", "--visible-to RESTRICTION", "--select", "--format"):
            self.assertIn(word, out)
        catalog = json.loads(run_cli("commands", "--json").out)
        comment = next(item for item in catalog["commands"] if item["name"] == "comment")
        self.assertTrue(comment["changesData"])
        self.assertTrue(comment["whenToUse"])
        self.assertIn("--edit", [name for arg in comment["arguments"] for name in arg["names"]])


if __name__ == "__main__":
    unittest.main()
