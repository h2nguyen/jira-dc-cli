"""Use cases around comments with a fake Jira: read one, list the recent ones, edit one, add one with a restriction."""
import dataclasses
import hashlib
import unittest

from jira_dc_cli.domain.errors import AuthError, ConflictError, NotFoundError, RejectedError, ValidationError
from jira_dc_cli.domain.model import Comment, User
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, build_app

JANE = User("jdoe", "Jane Doe", "jane.doe@example.com")
ALEX = User("asmith", "Alex Smith", "alex.smith@example.com")


def comments():
    return [
        Comment("10", "2026-10-02T10:00:00.000+0200", "Looks fine, [~jdoe].", JANE, updated="2026-10-02T10:00:00.000+0200"),
        Comment("11", "2026-10-15T09:00:00.000+0200", "Mail jane.doe@example.com", ALEX, updated="2026-10-16T09:00:00.000+0200", visibility="role:Developers"),
        Comment("12", "2026-10-19T14:00:00.000+0200", "Latest word.", JANE, updated="2026-10-19T14:00:00.000+0200"),
    ]


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class CommentTestCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        issue = self.gateway.issues["PROJ-1"]
        self.gateway.issues["PROJ-1"] = dataclasses.replace(issue, comments=comments(), comment_total=3)
        self.app = build_app(gateway=self.gateway)

    def stored(self, comment_id):
        return next(item for item in self.gateway.issues["PROJ-1"].comments if item.id == comment_id)


class GetCommentTest(CommentTestCase):
    def test_one_comment_with_its_times_visibility_and_who_wrote_it(self):
        comment = self.app.comments.get.execute("proj-1", "11")
        self.assertEqual((comment.id, comment.created, comment.updated, comment.visibility), ("11", "2026-10-15T09:00:00.000+0200", "2026-10-16T09:00:00.000+0200", "role:Developers"))
        self.assertIs(comment.by_me, False)
        self.assertRegex(comment.author_ref, r"^a-[0-9a-f]{8}$")

    def test_a_comment_of_the_token_user_is_by_me(self):
        self.assertIs(self.app.comments.get.execute("PROJ-1", "10").by_me, True)

    def test_a_comment_without_an_author_is_by_nobody_we_know(self):
        self.gateway.issues["PROJ-1"].comments[0] = dataclasses.replace(self.gateway.issues["PROJ-1"].comments[0], author=None)
        comment = self.app.comments.get.execute("PROJ-1", "10")
        self.assertEqual((comment.by_me, comment.author_ref), (None, ""))

    def test_the_author_and_the_personal_data_in_the_text_stay_out_by_default(self):
        comment = self.app.comments.get.execute("PROJ-1", "11")
        self.assertIsNone(comment.author)
        self.assertEqual(comment.body, "Mail [email]")

    def test_with_users_shows_the_author_and_the_text_as_it_is(self):
        comment = self.app.comments.get.execute("PROJ-1", "11", with_users=True)
        self.assertEqual((comment.author.name, comment.body), ("asmith", "Mail jane.doe@example.com"))

    def test_the_privacy_policy_refuses_with_users_but_not_the_comment(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError) as caught:
            app.comments.get.execute("PROJ-1", "10", with_users=True)
        self.assertIn("JIRA_HIDE_USERS=true", caught.exception.message)
        comment = app.comments.get.execute("PROJ-1", "10")
        self.assertEqual((comment.author, comment.by_me), (None, True))
        self.assertNotIn("jdoe", repr(comment).replace("[~user]", ""))

    def test_rendered_asks_jira_for_it_and_turns_the_html_into_text(self):
        self.gateway.rendered["10"] = "<ul><li>one</li><li>two</li></ul><p>use <tt>x</tt></p>"
        comment = self.app.comments.get.execute("PROJ-1", "10", rendered=True)
        self.assertEqual(comment.rendered_body, "- one\n- two\n\nuse `x`")
        self.assertEqual(self.gateway.called("get_comment")[0][1:], ("PROJ-1", "10", True))

    def test_without_rendered_nothing_is_asked_for_and_nothing_is_shown(self):
        comment = self.app.comments.get.execute("PROJ-1", "10")
        self.assertEqual(comment.rendered_body, "")
        self.assertEqual(self.gateway.called("get_comment")[0][3], False)

    def test_a_text_that_jira_renders_wrongly_stays_visible(self):
        self.gateway.rendered["10"] = "<p>{{ spaced }} and {{ok}}</p>"
        self.assertEqual(self.app.comments.get.execute("PROJ-1", "10", rendered=True).rendered_body, "{{ spaced }} and {{ok}}")

    def test_the_rendered_text_has_no_mentions_or_addresses_either(self):
        self.gateway.rendered["11"] = "<p>Mail jane.doe@example.com or [~jdoe]</p>"
        self.assertEqual(self.app.comments.get.execute("PROJ-1", "11", rendered=True).rendered_body, "Mail [email] or [~user]")

    def test_a_mention_that_jira_renders_as_a_link_to_a_person_stays_out_too(self):
        link = '<a href="/jira/secure/ViewProfile.jspa?name=jdoe" class="user-hover" rel="jdoe">Jane Doe</a>'
        self.gateway.rendered["10"] = f"<p>Ask {link} now</p>"
        self.assertEqual(self.app.comments.get.execute("PROJ-1", "10", rendered=True).rendered_body, "Ask [~user] now")
        shown = self.app.comments.get.execute("PROJ-1", "10", with_users=True, rendered=True).rendered_body
        self.assertIn("Jane Doe", shown)
        found = self.app.comments.find.execute("PROJ-1", rendered=True)
        self.assertEqual(found.comments[0].rendered_body, "Ask [~user] now")

    def test_an_unknown_comment_says_where_to_find_the_ids(self):
        with self.assertRaises(NotFoundError) as caught:
            self.app.comments.get.execute("PROJ-1", "99")
        self.assertIn("jira comment PROJ-1 --list", caught.exception.hint)

    def test_an_unknown_issue(self):
        with self.assertRaises(NotFoundError):
            self.app.comments.get.execute("PROJ-999", "10")

    def test_what_is_no_id_or_key_is_refused_before_jira_is_asked(self):
        for key, comment_id in (("nonsense", "10"), ("PROJ-1", "abc"), ("PROJ-1", "")):
            with self.subTest(key=key, comment_id=comment_id), self.assertRaises(ValidationError):
                self.app.comments.get.execute(key, comment_id)
        self.assertEqual(self.gateway.calls, [])


class FindCommentsTest(CommentTestCase):
    def test_every_comment_with_the_total(self):
        found = self.app.comments.find.execute("PROJ-1")
        self.assertEqual((found.key, found.total, [item.id for item in found.comments], found.since), ("PROJ-1", 3, ["10", "11", "12"], ""))

    def test_each_comment_says_whether_it_is_mine_and_has_a_pseudonym_of_its_author(self):
        found = self.app.comments.find.execute("PROJ-1")
        self.assertEqual([item.by_me for item in found.comments], [True, False, True])
        mine, theirs = found.comments[0], found.comments[1]
        self.assertEqual(mine.author_ref, found.comments[2].author_ref)
        self.assertNotEqual(mine.author_ref, theirs.author_ref)
        self.assertIsNone(mine.author)

    def test_the_pseudonym_is_the_same_in_every_call_and_does_not_hold_the_name(self):
        first = self.app.comments.find.execute("PROJ-1").comments[0].author_ref
        again = self.app.comments.get.execute("PROJ-1", "10").author_ref
        self.assertEqual(first, again)
        self.assertNotIn("jdoe", first)

    def references(self, **changes):
        app = build_app(gateway=self.gateway, settings=settings(**changes))
        return [item.author_ref for item in app.comments.find.execute("PROJ-1").comments]

    def test_the_pseudonym_is_keyed_with_the_token_so_another_token_gives_other_pseudonyms(self):
        one, same, other = self.references(pat="synthetic-token-one"), self.references(pat="synthetic-token-one"), self.references(pat="synthetic-token-two")
        self.assertEqual(one, same)
        self.assertNotEqual(one, other)
        self.assertEqual((one[0] == one[2], one[0] == one[1]), (True, False))  # the same author keeps one pseudonym, another author has another
        self.assertEqual([a == b for a, b in zip(one, other)], [False, False, False])

    def test_the_pseudonym_cannot_be_recomputed_from_the_address_and_a_user_name_that_users_resolve_tells(self):
        shown = self.app.comments.find.execute("PROJ-1")
        asmith = self.app.users.resolve.execute("Alex Smith").person.name  # allowed under the privacy policy: it prints the user name
        unkeyed = "a-" + hashlib.sha256(f"https://jira.example.com\n{asmith}".encode()).hexdigest()[:8]
        self.assertEqual(asmith, "asmith")
        self.assertNotIn(unkeyed, [item.author_ref for item in shown.comments])
        self.assertNotEqual(shown.comments[1].author_ref, unkeyed)

    def test_the_token_is_not_part_of_what_is_returned(self):
        found = self.app.comments.find.execute("PROJ-1", with_users=True)
        self.assertNotIn("secret-token", repr(found))

    def test_without_a_token_a_key_of_the_process_is_used_and_never_the_unkeyed_hash(self):
        unkeyed = "a-" + hashlib.sha256("https://jira.example.com\njdoe".encode()).hexdigest()[:8]
        for pat in ("", "   "):
            with self.subTest(pat=repr(pat)):
                refs = self.references(pat=pat)
                self.assertRegex(refs[0], r"^a-[0-9a-f]{8}$")
                self.assertEqual(refs, self.references(pat=pat))  # the same for the whole process
                self.assertEqual((refs[0] == refs[2], refs[0] == refs[1], refs[0] == unkeyed), (True, False, False))
                self.assertNotEqual(refs, self.references(pat="synthetic-token-one"))
        self.assertEqual(self.references(pat=""), self.references(pat="   "))

    def test_with_users_the_authors_come_with_it(self):
        found = self.app.comments.find.execute("PROJ-1", with_users=True)
        self.assertEqual([item.author.name for item in found.comments], ["jdoe", "asmith", "jdoe"])
        self.assertEqual(found.comments[1].body, "Mail jane.doe@example.com")

    def test_the_list_hides_the_authors_and_the_personal_data_in_the_text_unless_people_are_asked_for(self):
        found = self.app.comments.find.execute("PROJ-1")
        self.assertEqual([item.author for item in found.comments], [None, None, None])
        self.assertEqual([item.body for item in found.comments], ["Looks fine, [~user].", "Mail [email]", "Latest word."])
        shown = self.app.comments.find.execute("PROJ-1", with_users=True)
        self.assertEqual((shown.comments[0].author.name, shown.comments[0].body), ("jdoe", "Looks fine, [~jdoe]."))
        policy = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError):
            policy.comments.find.execute("PROJ-1", with_users=True)
        self.assertEqual([item.body for item in policy.comments.find.execute("PROJ-1").comments], [item.body for item in found.comments])

    def test_the_privacy_policy_refuses_with_users_and_still_has_byme_and_the_pseudonym(self):
        app = build_app(gateway=self.gateway, settings=settings(hide_users=True))
        with self.assertRaises(ValidationError):
            app.comments.find.execute("PROJ-1", with_users=True)
        found = app.comments.find.execute("PROJ-1")
        self.assertEqual([item.by_me for item in found.comments], [True, False, True])
        for item in found.comments:
            self.assertTrue(item.author_ref)
            self.assertIsNone(item.author)

    def test_since_a_time_ago_counts_from_the_clock_of_the_application(self):
        found = self.app.comments.find.execute("PROJ-1", since="2d")  # the clock says 2026-10-20 12:00 UTC
        self.assertEqual([item.id for item in found.comments], ["12"])
        self.assertEqual((found.total, found.since), (3, "2026-10-18T12:00:00+00:00"))

    def test_since_a_date_and_since_an_iso_time(self):
        self.assertEqual([item.id for item in self.app.comments.find.execute("PROJ-1", since="2026-10-15").comments], ["11", "12"])
        self.assertEqual([item.id for item in self.app.comments.find.execute("PROJ-1", since="2026-10-19T11:00:00Z").comments], ["12"])
        self.assertEqual([item.id for item in self.app.comments.find.execute("PROJ-1", since="2026-10-19T13:00:00Z").comments], [])

    def test_last_is_the_newest_and_counts_after_since(self):
        self.assertEqual([item.id for item in self.app.comments.find.execute("PROJ-1", last=2).comments], ["11", "12"])
        self.assertEqual([item.id for item in self.app.comments.find.execute("PROJ-1", since="1w", last=3).comments], ["11", "12"])

    def test_nothing_recent_is_an_empty_list_and_costs_no_request_for_the_user(self):
        found = self.app.comments.find.execute("PROJ-1", since="1h")
        self.assertEqual((found.comments, found.total), ([], 3))
        self.assertEqual(self.gateway.called("current_user"), [])

    def test_the_token_user_is_asked_once_for_the_whole_list(self):
        self.app.comments.find.execute("PROJ-1")
        self.assertEqual(len(self.gateway.called("current_user")), 1)

    def test_rendered_text_for_every_comment(self):
        found = self.app.comments.find.execute("PROJ-1", rendered=True)
        self.assertEqual(found.comments[2].rendered_body, "Latest word.")
        self.assertEqual(self.gateway.called("fetch_comments")[0][2], True)

    def test_a_wrong_since_or_last_is_refused_before_jira_is_asked(self):
        for options in ({"since": "yesterday"}, {"last": -1}):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                self.app.comments.find.execute("PROJ-1", **options)
        self.assertEqual(self.gateway.called("fetch_comments"), [])

    def test_an_unknown_issue(self):
        with self.assertRaises(NotFoundError):
            self.app.comments.find.execute("PROJ-999")


class EditCommentTest(CommentTestCase):
    def test_the_text_is_replaced_and_nothing_else_is_sent(self):
        result = self.app.comments.edit.execute("PROJ-1", "10", "  Looks good now.\n")
        self.assertEqual((result.action, result.dry_run, result.payload), ("updated", False, {"body": "Looks good now."}))
        self.assertEqual(self.gateway.called("update_comment")[0][1:], ("PROJ-1", "10", {"body": "Looks good now."}))
        self.assertEqual(self.stored("10").body, "Looks good now.")
        self.assertEqual(result.updated, "2026-10-20T09:00:00.000+0200")

    def test_the_restriction_of_the_comment_is_sent_again(self):
        self.gateway.edit_all_comments = True  # the comment is Alex's
        result = self.app.comments.edit.execute("PROJ-1", "11", "Corrected.")
        self.assertEqual(result.payload, {"body": "Corrected.", "visibility": {"type": "role", "value": "Developers"}})
        self.assertEqual(self.stored("11").visibility, "role:Developers")  # the fake loses it, like the worst case on a server, unless it is sent

    def test_an_edit_is_always_newer_than_the_comment_it_replaced_even_one_that_was_changed_after_the_fixed_time(self):
        first = self.app.comments.edit.execute("PROJ-1", "10", "One.").updated
        self.assertEqual(first, "2026-10-20T09:00:00.000+0200")  # the comment was older than that
        second = self.app.comments.edit.execute("PROJ-1", "10", "Two.").updated
        self.assertEqual(second, "2026-10-20T09:00:01.000+0200")  # one second later: never equal to what it replaced
        late = dataclasses.replace(self.stored("12"), updated="2026-10-25T10:00:00.000+0200")
        self.gateway.issues["PROJ-1"].comments[2] = late
        self.assertEqual(self.app.comments.edit.execute("PROJ-1", "12", "Three.").updated, "2026-10-25T10:00:01.000+0200")
        with self.assertRaises(ConflictError):  # a guard that was given the time that the first reader saw
            self.app.comments.edit.execute("PROJ-1", "12", "Four.", if_unchanged_since="2026-10-25T10:00:00.000+0200")

    def test_a_comment_that_is_open_stays_open(self):
        result = self.app.comments.edit.execute("PROJ-1", "10", "Corrected.")
        self.assertNotIn("visibility", result.payload)
        self.assertEqual(self.stored("10").visibility, "")

    def test_a_dry_run_sends_nothing_and_shows_the_change(self):
        result = self.app.comments.edit.execute("PROJ-1", "10", "Looks fine, [~jdoe]. Thanks.", dry_run=True)
        self.assertEqual((result.action, result.dry_run, result.by_me), ("updated", True, True))
        self.assertEqual(self.gateway.called("update_comment"), [])
        self.assertEqual(self.stored("10").body, "Looks fine, [~jdoe].")
        self.assertIn("+Looks fine, [~jdoe]. Thanks.", result.diff)
        self.assertEqual(result.updated, "2026-10-02T10:00:00.000+0200")  # what the comment has now
        self.assertEqual(len(self.gateway.called("get_comment")), 1)

    def test_the_current_text_is_shown_without_personal_data(self):
        result = self.app.comments.edit.execute("PROJ-1", "11", "Short.", dry_run=True)
        self.assertEqual(result.current_body, "Mail [email]")
        self.assertNotIn("jane.doe", result.diff)

    def test_a_big_comment_has_no_diff_but_the_current_text(self):
        self.gateway.issues["PROJ-1"].comments[0] = dataclasses.replace(self.gateway.issues["PROJ-1"].comments[0], body="word " * 800)
        result = self.app.comments.edit.execute("PROJ-1", "10", "new " * 800, dry_run=True)
        self.assertEqual(result.diff, "")
        self.assertTrue(result.current_body.startswith("word word"))

    def test_a_dry_run_warns_when_somebody_else_wrote_the_comment(self):
        result = self.app.comments.edit.execute("PROJ-1", "11", "Corrected.", dry_run=True)
        self.assertIs(result.by_me, False)
        self.assertTrue(any("Someone else wrote this comment" in warning for warning in result.warnings))
        self.assertEqual(self.app.comments.edit.execute("PROJ-1", "10", "Corrected.", dry_run=True).warnings, [])

    def test_a_token_user_without_a_user_name_makes_byme_unknown_and_gives_no_false_warning(self):
        self.gateway.user = dataclasses.replace(self.gateway.user, name="")
        result = self.app.comments.edit.execute("PROJ-1", "11", "Corrected.", dry_run=True)
        self.assertEqual((result.by_me, result.warnings), (None, []))
        self.assertIsNone(self.app.comments.get.execute("PROJ-1", "11").by_me)

    def test_a_live_edit_asks_for_nobody_s_name(self):
        self.app.comments.edit.execute("PROJ-1", "10", "Corrected.")
        self.assertEqual(self.gateway.called("current_user"), [])

    def test_somebody_elses_comment_is_refused_by_jira_and_the_hint_says_who_may_edit(self):
        with self.assertRaises(AuthError) as caught:
            self.app.comments.edit.execute("PROJ-1", "11", "Corrected.")
        self.assertEqual(caught.exception.exit_code, 5)
        self.assertIn("Edit All Comments", caught.exception.hint)
        self.assertIn("author", caught.exception.hint)
        self.assertEqual(self.stored("11").body, "Mail jane.doe@example.com")

    def test_a_token_that_may_edit_all_comments_can_edit_one_of_somebody_else(self):
        self.gateway.edit_all_comments = True
        self.assertEqual(self.app.comments.edit.execute("PROJ-1", "11", "Corrected.").action, "updated")

    def test_a_401_is_not_taken_for_a_missing_permission(self):
        self.gateway.errors["update_comment"] = AuthError("Jira did not accept the token (HTTP 401).", hint="Create a new token.", details={"status": 401})
        with self.assertRaises(AuthError) as caught:
            self.app.comments.edit.execute("PROJ-1", "10", "Corrected.")
        self.assertEqual(caught.exception.hint, "Create a new token.")

    def test_jira_s_refusal_of_the_content_gets_a_hint_about_comments_and_not_about_issues(self):
        self.gateway.errors["update_comment"] = RejectedError("Jira refused the request (HTTP 400): bad", hint="See `jira metadata PROJ`", details={"status": 400})
        with self.assertRaises(RejectedError) as caught:
            self.app.comments.edit.execute("PROJ-1", "10", "Corrected.")
        self.assertNotIn("metadata", caught.exception.hint)
        self.assertIn("role or group", caught.exception.hint)
        self.assertEqual(caught.exception.details, {"status": 400})

    def test_the_same_text_sends_nothing(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                result = self.app.comments.edit.execute("PROJ-1", "12", " Latest word.\r\n", dry_run=dry_run)
                self.assertEqual((result.action, result.dry_run), ("unchanged", dry_run))
        self.assertEqual(self.gateway.called("update_comment"), [])

    def test_an_empty_text_is_refused_and_no_comment_is_deleted(self):
        with self.assertRaises(ValidationError) as caught:
            self.app.comments.edit.execute("PROJ-1", "10", "  \n")
        self.assertIn("cannot be deleted", caught.exception.hint)
        self.assertEqual(self.gateway.calls, [])

    def test_an_unknown_comment_is_not_found_and_says_where_to_find_the_ids(self):
        with self.assertRaises(NotFoundError) as caught:
            self.app.comments.edit.execute("PROJ-1", "99", "Text.")
        self.assertIn("jira comment PROJ-1 --list", caught.exception.hint)
        self.assertEqual(self.gateway.called("update_comment"), [])


class EditGuardTest(CommentTestCase):
    def test_a_comment_changed_after_the_time_is_a_conflict_and_nothing_is_written(self):
        with self.assertRaises(ConflictError) as caught:
            self.app.comments.edit.execute("PROJ-1", "11", "Corrected.", if_unchanged_since="2026-10-10T00:00:00Z")
        self.assertEqual((caught.exception.exit_code, caught.exception.details["commentId"]), (3, "11"))
        self.assertEqual(self.gateway.called("update_comment"), [])

    def test_a_comment_not_changed_since_is_edited(self):
        self.gateway.edit_all_comments = True
        result = self.app.comments.edit.execute("PROJ-1", "11", "Corrected.", if_unchanged_since="2026-10-17T00:00:00Z")
        self.assertEqual(result.action, "updated")

    def test_the_comment_is_read_again_right_before_the_write(self):
        self.app.comments.edit.execute("PROJ-1", "10", "Corrected.", if_unchanged_since="2026-10-17T00:00:00Z")
        self.assertEqual(len(self.gateway.called("get_comment")), 2)

    def test_without_the_guard_the_comment_is_read_once(self):
        self.app.comments.edit.execute("PROJ-1", "10", "Corrected.")
        self.assertEqual(len(self.gateway.called("get_comment")), 1)

    def test_a_change_between_the_two_reads_is_caught(self):
        self.gateway.get_comment = self.change_after_the_first_read(updated="2026-10-19T10:00:00.000+0200")
        with self.assertRaises(ConflictError):
            self.app.comments.edit.execute("PROJ-1", "10", "Corrected.", if_unchanged_since="2026-10-17T00:00:00Z")
        self.assertEqual(self.gateway.called("update_comment"), [])

    def test_the_restriction_that_the_second_read_finds_is_the_one_that_is_sent(self):
        self.gateway.get_comment = self.change_after_the_first_read(visibility="group:jira-staff")
        result = self.app.comments.edit.execute("PROJ-1", "10", "Corrected.", if_unchanged_since="2026-10-17T00:00:00Z")
        self.assertEqual(result.payload["visibility"], {"type": "group", "value": "jira-staff"})

    def change_after_the_first_read(self, **changes):
        """A `get_comment` that lets somebody change comment 10 right after the CLI has read it for the first time."""
        reads = []
        original = self.gateway.get_comment

        def read(key, comment_id, *, rendered=False):
            reads.append(comment_id)
            comment = original(key, comment_id, rendered=rendered)
            if len(reads) == 1:
                self.gateway.issues["PROJ-1"].comments[0] = dataclasses.replace(self.stored("10"), **changes)
            return comment

        return read

    def test_the_dry_run_checks_the_guard_too_and_reads_once(self):
        with self.assertRaises(ConflictError):
            self.app.comments.edit.execute("PROJ-1", "11", "Corrected.", dry_run=True, if_unchanged_since="2026-10-10T00:00:00Z")
        result = self.app.comments.edit.execute("PROJ-1", "10", "Corrected.", dry_run=True, if_unchanged_since="2026-10-17T00:00:00Z")
        self.assertTrue(result.dry_run)

    def test_a_bad_time_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            self.app.comments.edit.execute("PROJ-1", "10", "Corrected.", if_unchanged_since="soon")


class PlaceholderTest(CommentTestCase):
    def test_a_stand_in_for_a_mention_that_the_comment_does_not_hold_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.app.comments.edit.execute("PROJ-1", "10", "Looks fine, [~user].")
        self.assertIn("[~user]", caught.exception.message)
        self.assertIn("overwrite the real one", caught.exception.message)
        self.assertEqual(self.gateway.called("update_comment"), [])
        self.assertEqual(self.stored("10").body, "Looks fine, [~jdoe].")

    def test_a_stand_in_for_an_address_is_refused_too_also_in_a_dry_run(self):
        with self.assertRaises(ValidationError):
            self.app.comments.edit.execute("PROJ-1", "11", "Mail [email] now", dry_run=True)

    def test_a_real_mention_is_fine(self):
        self.assertEqual(self.app.comments.edit.execute("PROJ-1", "10", "Looks fine, [~jdoe]. Thanks.").action, "updated")

    def test_a_comment_that_holds_the_text_literally_may_keep_it(self):
        self.gateway.issues["PROJ-1"].comments[0] = dataclasses.replace(self.stored("10"), body="Write [email] here")
        self.assertEqual(self.app.comments.edit.execute("PROJ-1", "10", "Write [email] there").action, "updated")


class AddCommentTest(CommentTestCase):
    def test_a_restriction_goes_into_the_payload(self):
        result = self.app.add_comment.execute("PROJ-1", "Internal note", visible_to="role:Developers")
        self.assertEqual(result.payload, {"body": "Internal note", "visibility": {"type": "role", "value": "Developers"}})
        self.assertEqual(self.gateway.called("add_comment")[0][2], result.payload)

    def test_the_dry_run_shows_the_restriction_and_sends_nothing(self):
        result = self.app.add_comment.execute("PROJ-1", "Note", dry_run=True, visible_to="group:jira-staff")
        self.assertEqual(result.payload["visibility"], {"type": "group", "value": "jira-staff"})
        self.assertEqual(self.gateway.called("add_comment"), [])

    def test_a_restriction_that_is_neither_a_role_nor_a_group_is_refused(self):
        with self.assertRaises(ValidationError):
            self.app.add_comment.execute("PROJ-1", "Note", visible_to="everybody")
        self.assertEqual(self.gateway.called("add_comment"), [])

    def test_without_a_restriction_the_payload_is_what_it_was(self):
        self.assertEqual(self.app.add_comment.execute("PROJ-1", " Hello ").payload, {"body": "Hello"})

    def test_a_comment_that_is_added_is_new_text_and_a_stand_in_in_it_is_not_refused_only_an_edit_compares(self):
        text = "see [~user] and [email] and [personal link]"
        result = self.app.add_comment.execute("PROJ-1", text)
        self.assertEqual(result.payload, {"body": text})
        self.assertEqual(self.gateway.called("add_comment")[0][2], result.payload)
        self.assertEqual(self.app.add_comment.execute("PROJ-1", "[email]", dry_run=True).payload, {"body": "[email]"})
        self.assertEqual(self.gateway.called("get_comment"), [])  # nothing to compare a new comment with
        with self.assertRaises(ValidationError):  # the edit of a comment that holds none of them is refused
            self.app.comments.edit.execute("PROJ-1", "12", text)


if __name__ == "__main__":
    unittest.main()
