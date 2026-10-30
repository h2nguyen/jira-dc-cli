"""The rules for comments: the pseudonym of an author, `--since` and `--last`, the payload of an edit and what an edit changes."""
import hashlib
import hmac
import time
import unittest
from datetime import datetime, timedelta, timezone

from jira_dc_cli.domain.comments import (
    author_ref,
    describe_change,
    edit_payload,
    ensure_comment_unchanged_since,
    hide_mentions,
    is_unchanged,
    parse_comment_id,
    parse_since,
    parse_visible_to,
    placeholders_added,
    select_comments,
    visibility_object,
)
from jira_dc_cli.domain.errors import ConflictError, ValidationError
from jira_dc_cli.domain.model import Comment
from jira_dc_cli.domain.payloads import comment_payload

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
BASE = "https://jira.example.com/jira"
SECRET = "synthetic-token-1"


def comment(identifier, created, **changes):
    return Comment(str(identifier), created, f"text {identifier}", **changes)


class AuthorRefTest(unittest.TestCase):
    def test_it_is_a_short_stable_pseudonym(self):
        reference = author_ref(SECRET, BASE, "jdoe")
        self.assertRegex(reference, r"^a-[0-9a-f]{8}$")
        self.assertEqual(reference, author_ref(SECRET, BASE, "jdoe"))

    def test_it_does_not_contain_the_name(self):
        self.assertNotIn("jdoe", author_ref(SECRET, BASE, "jdoe"))

    def test_case_and_a_trailing_slash_do_not_matter(self):
        self.assertEqual(author_ref(SECRET, BASE, "JDoe"), author_ref(SECRET, BASE + "/", " jdoe "))
        self.assertEqual(author_ref(SECRET, "HTTPS://JIRA.EXAMPLE.COM/jira", "jdoe"), author_ref(SECRET, BASE, "jdoe"))

    def test_another_author_or_another_jira_gives_another_pseudonym(self):
        self.assertNotEqual(author_ref(SECRET, BASE, "jdoe"), author_ref(SECRET, BASE, "asmith"))
        self.assertNotEqual(author_ref(SECRET, BASE, "jdoe"), author_ref(SECRET, "https://other.example.com", "jdoe"))

    def test_nobody_has_no_pseudonym(self):
        self.assertEqual((author_ref(SECRET, BASE, ""), author_ref(SECRET, BASE, "  ")), ("", ""))

    def test_another_secret_gives_another_pseudonym_for_the_same_author(self):
        names = ("jdoe", "asmith", "jane.doe", "alex.smith", "sam", "old.timer")
        first = [author_ref(SECRET, BASE, name) for name in names]
        second = [author_ref("synthetic-token-2", BASE, name) for name in names]
        self.assertEqual([a == b for a, b in zip(first, second)], [False] * len(names))

    def test_the_secret_may_be_text_or_bytes_with_the_same_result(self):
        self.assertEqual(author_ref(SECRET, BASE, "jdoe"), author_ref(SECRET.encode("utf-8"), BASE, "jdoe"))

    def test_without_the_secret_the_pseudonym_cannot_be_recomputed_from_a_name(self):
        unkeyed = "a-" + hashlib.sha256(f"{BASE.casefold()}\njdoe".encode()).hexdigest()[:8]
        self.assertNotEqual(author_ref(SECRET, BASE, "jdoe"), unkeyed)
        plain_hmac = "a-" + hmac.new(SECRET.encode(), f"{BASE.casefold()}\njdoe".encode(), hashlib.sha256).hexdigest()[:8]
        self.assertNotEqual(author_ref(SECRET, BASE, "jdoe"), plain_hmac)  # the message carries a label too: no other use of the key matches

    def test_an_empty_secret_is_a_mistake_and_never_the_unkeyed_hash(self):
        for secret in ("", b""):
            with self.subTest(secret=secret), self.assertRaises(ValueError):
                author_ref(secret, BASE, "jdoe")
        with self.assertRaises(ValueError):
            author_ref("", BASE, "")  # the check comes before the one for nobody


class ParseSinceTest(unittest.TestCase):
    def test_a_time_ago(self):
        self.assertEqual(parse_since("2d", NOW), NOW - timedelta(days=2))
        self.assertEqual(parse_since("12h", NOW), NOW - timedelta(hours=12))
        self.assertEqual(parse_since("1w", NOW), NOW - timedelta(weeks=1))
        self.assertEqual(parse_since("30M", NOW), NOW - timedelta(minutes=30))
        self.assertEqual(parse_since("1.5h", NOW), NOW - timedelta(minutes=90))
        self.assertEqual(parse_since(" 2d ", NOW), NOW - timedelta(days=2))

    def test_a_date_counts_from_midnight_utc(self):
        self.assertEqual(parse_since("2026-10-15", NOW), datetime(2026, 10, 15, tzinfo=timezone.utc))

    def test_an_iso_time_with_and_without_an_offset(self):
        self.assertEqual(parse_since("2026-10-15T08:00:00Z", NOW), datetime(2026, 10, 15, 8, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2026-10-15T08:00:00+0200", NOW), datetime(2026, 10, 15, 6, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2026-10-15T08:00:00.123+02:00", NOW), datetime(2026, 10, 15, 6, 0, 0, 123000, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2026-10-15T08:00:00", NOW), datetime(2026, 10, 15, 8, tzinfo=timezone.utc))

    def test_what_is_no_time_is_refused_with_the_forms_that_work(self):
        for text in ("yesterday", "5", "-2d", "2x", "", "2 days", "2026-13-45", "d", "1e3d"):
            with self.subTest(text=text), self.assertRaises(ValidationError) as caught:
                parse_since(text, NOW)
            self.assertIn("--since", caught.exception.message)
            self.assertIn("2d", caught.exception.hint)
            self.assertIn("2026-10-15", caught.exception.hint)

    def test_a_time_ago_too_large_to_be_a_date_is_refused_and_does_not_crash(self):
        for text in ("999999999999999999d", "99999999w", "5000000d"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_since(text, NOW)


class SelectCommentsTest(unittest.TestCase):
    def setUp(self):
        self.comments = [
            comment(1, "2026-10-01T08:00:00.000+0200"),
            comment(2, "2026-10-10T08:00:00.000+0200"),
            comment(3, "2026-10-18T16:00:00.000+0200"),
            comment(4, "2026-10-19T09:00:00.000+0200"),
            comment(5, "2026-10-20T09:00:00.000+0200"),
        ]

    def ids(self, **options):
        return [item.id for item in select_comments(self.comments, **options)]

    def test_without_a_filter_every_comment_in_the_order_of_jira(self):
        self.assertEqual(self.ids(), ["1", "2", "3", "4", "5"])

    def test_since_keeps_the_comments_created_at_or_after(self):
        self.assertEqual(self.ids(since=NOW - timedelta(days=2)), ["3", "4", "5"])
        self.assertEqual(self.ids(since=datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)), ["3", "4", "5"])  # the instant itself counts
        self.assertEqual(self.ids(since=NOW + timedelta(days=1)), [])

    def test_last_keeps_the_newest_in_the_order_of_jira(self):
        self.assertEqual(self.ids(last=2), ["4", "5"])
        self.assertEqual(self.ids(last=99), ["1", "2", "3", "4", "5"])

    def test_last_counts_after_since(self):
        self.assertEqual(self.ids(since=NOW - timedelta(days=2), last=2), ["4", "5"])
        self.assertEqual(self.ids(since=NOW - timedelta(days=20), last=1), ["5"])

    def test_the_newest_is_by_the_time_and_not_by_the_position(self):
        shuffled = [self.comments[3], self.comments[0], self.comments[4]]
        self.assertEqual([item.id for item in select_comments(shuffled, last=2)], ["4", "5"])

    def test_a_comment_whose_time_cannot_be_read_is_the_oldest_and_never_recent(self):
        broken = [comment(9, "not a time")] + self.comments
        self.assertEqual([item.id for item in select_comments(broken, last=5)], ["1", "2", "3", "4", "5"])
        self.assertNotIn("9", [item.id for item in select_comments(broken, since=NOW - timedelta(days=30))])

    def test_the_input_is_left_alone(self):
        before = list(self.comments)
        select_comments(self.comments, last=1)
        self.assertEqual(self.comments, before)


class VisibilityTest(unittest.TestCase):
    def test_the_object_that_jira_takes(self):
        self.assertEqual(visibility_object("role:Developers"), {"type": "role", "value": "Developers"})
        self.assertEqual(visibility_object("group:jira-staff"), {"type": "group", "value": "jira-staff"})
        self.assertEqual(visibility_object("group:team:blue"), {"type": "group", "value": "team:blue"})

    def test_no_restriction_is_none(self):
        for text in ("", "role", "role:", ":x", None):
            with self.subTest(text=text):
                self.assertIsNone(visibility_object(text))

    def test_visible_to_takes_a_role_or_a_group(self):
        self.assertEqual(parse_visible_to("role:Developers"), {"type": "role", "value": "Developers"})
        self.assertEqual(parse_visible_to(" GROUP : jira-staff "), {"type": "group", "value": "jira-staff"})

    def test_visible_to_refuses_anything_else(self):
        for text in ("Developers", "user:jdoe", "role:", "", "role"):
            with self.subTest(text=text), self.assertRaises(ValidationError) as caught:
                parse_visible_to(text)
            self.assertIn("role:NAME or group:NAME", caught.exception.hint)


class HideMentionsTest(unittest.TestCase):
    MENTION = '<a href="/jira/secure/ViewProfile.jspa?name=jdoe" class="user-hover" rel="jdoe">Jane Doe</a>'

    def test_a_link_to_a_person_becomes_the_stand_in_and_the_display_name_is_gone(self):
        html = f"<p>Ask {self.MENTION} about it.</p>"
        self.assertEqual(hide_mentions(html), "<p>Ask [~user] about it.</p>")

    def test_either_mark_of_such_a_link_is_enough_in_any_order_and_case(self):
        for link in (
            '<a class="user-hover" rel="jdoe" href="https://jira.example.com/secure/ViewProfile.jspa?name=jdoe">Jane Doe</a>',
            '<A HREF="/secure/ViewProfile.jspa?name=jdoe">Jane Doe</A>',
            '<a href="/browse/x" class="user-hover">Jane Doe</a >',
        ):
            with self.subTest(link=link):
                self.assertEqual(hide_mentions(f"x {link} y"), "x [~user] y")

    def test_several_mentions_and_other_links_stay_as_they_are(self):
        other = '<a href="https://example.com/page">a page</a>'
        self.assertEqual(hide_mentions(f"{self.MENTION} and {self.MENTION} and {other}"), f"[~user] and [~user] and {other}")

    def test_nothing_to_hide(self):
        self.assertEqual(hide_mentions("<p>plain</p>"), "<p>plain</p>")
        self.assertEqual(hide_mentions(""), "")
        self.assertEqual(hide_mentions(None), "")

    def test_what_is_no_link_to_a_person_or_has_no_end_stays_as_it_is(self):
        unclosed = '<p>see <a class="user-hover" href="/x">Jane Doe and more'
        self.assertEqual(hide_mentions(unclosed), unclosed)
        too_long_tag = '<a class="user-hover" data-x="' + "x" * 1100 + '">Jane Doe</a>'
        self.assertEqual(hide_mentions(too_long_tag), too_long_tag)
        too_long_text = '<a class="user-hover" href="/x">' + "x" * 1600 + "</a>"
        self.assertEqual(hide_mentions(too_long_text), too_long_text)
        self.assertEqual(hide_mentions("a < b and <a > c"), "a < b and <a > c")

    def test_the_longest_display_name_is_hidden_also_when_every_character_of_it_is_escaped(self):
        for name in ("J" * 255, "&amp;" * 255):
            with self.subTest(length=len(name)):
                self.assertEqual(hide_mentions(f'<p><a class="user-hover" href="/x">{name}</a></p>'), "<p>[~user]</p>")

    def test_a_link_with_no_end_nearby_does_not_hide_the_one_after_it(self):
        html = '<a class="user-hover" href="/1">' + "x" * 2000 + '<a class="user-hover" href="/2">Jane Doe</a>'
        self.assertEqual(hide_mentions(html), '<a class="user-hover" href="/1">' + "x" * 2000 + "[~user]")

    def test_a_hostile_text_is_handled_in_bounded_time(self):
        started = time.perf_counter()
        for hostile in (
            "<a " * 30000,
            "<a " + "x" * 200000,
            "<a class='user-hover'>" * 5000,
            "<a" + " user-hover" * 20000,
            "<a user-hover>" * 50000,
            "<a user-hover>" + "x" * 1400 + "</a>" + "<a user-hover>" * 50000,
            "<a user-hover " * 50000 + ">",
            "<a user-hover>x</a>" * 50000,
        ):
            hide_mentions(hostile)
        self.assertLess(time.perf_counter() - started, 3.0)


class EditPayloadTest(unittest.TestCase):
    def test_the_text_alone_when_the_comment_is_open(self):
        self.assertEqual(edit_payload("  New text \n"), {"body": "New text"})

    def test_the_restriction_that_the_comment_has_is_sent_again(self):
        self.assertEqual(edit_payload("New", "role:Developers"), {"body": "New", "visibility": {"type": "role", "value": "Developers"}})

    def test_adding_with_a_restriction(self):
        self.assertEqual(comment_payload("Hi", parse_visible_to("group:staff")), {"body": "Hi", "visibility": {"type": "group", "value": "staff"}})
        self.assertEqual(comment_payload(" Hi "), {"body": "Hi"})


class WhatAnEditChangesTest(unittest.TestCase):
    def test_the_same_text_is_unchanged_whatever_the_line_ends_and_the_blanks_around_it(self):
        self.assertTrue(is_unchanged("a\r\nb\n", " a\nb"))
        self.assertFalse(is_unchanged("a\nb", "a\nc"))

    def test_a_small_change_is_a_unified_diff(self):
        diff = describe_change("one\ntwo\nthree", "one\n2\nthree")
        self.assertEqual(diff.splitlines()[:2], ["--- current", "+++ new"])
        self.assertIn("-two", diff)
        self.assertIn("+2", diff)

    def test_a_big_text_or_no_change_has_no_diff(self):
        self.assertEqual(describe_change("x" * 2000, "y" * 2000), "")
        self.assertEqual(describe_change("same", "same"), "")

    def test_a_stand_in_for_personal_data_that_the_comment_does_not_hold_is_found(self):
        self.assertEqual(placeholders_added("Ask jane.doe@example.com", "Ask [email]"), ["[email]"])
        self.assertEqual(placeholders_added("Ask [~jdoe]", "Ask [~user] and [email]"), ["[~user]", "[email]"])
        self.assertEqual(placeholders_added("Ask [~user]", "Ask [~user] again"), [])  # the text holds it already: it is the real text
        self.assertEqual(placeholders_added("a", "plain text"), [])


class StaleCommentTest(unittest.TestCase):
    def test_a_comment_changed_after_the_time_is_a_conflict_that_says_which(self):
        stale = comment(10, "2026-10-01T08:00:00.000+0200", updated="2026-10-12T08:00:00.000+0200")
        with self.assertRaises(ConflictError) as caught:
            ensure_comment_unchanged_since("PROJ-1", stale, "2026-10-10T00:00:00Z")
        self.assertEqual(caught.exception.exit_code, 3)
        self.assertEqual(caught.exception.details["key"], "PROJ-1")
        self.assertEqual(caught.exception.details["commentId"], "10")
        self.assertIn("jira comment PROJ-1 --get 10", caught.exception.hint)

    def test_a_comment_not_changed_since_passes(self):
        fresh = comment(10, "2026-10-01T08:00:00.000+0200", updated="2026-10-05T08:00:00.000+0200")
        ensure_comment_unchanged_since("PROJ-1", fresh, "2026-10-05T06:00:00Z")  # the same instant
        ensure_comment_unchanged_since("PROJ-1", fresh, "2026-10-06T00:00:00Z")

    def test_without_an_updated_time_the_created_time_counts(self):
        never_edited = comment(10, "2026-10-12T08:00:00.000+0200")
        with self.assertRaises(ConflictError):
            ensure_comment_unchanged_since("PROJ-1", never_edited, "2026-10-10T00:00:00Z")

    def test_a_time_that_is_none_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            ensure_comment_unchanged_since("PROJ-1", comment(10, "2026-10-01T08:00:00.000+0200"), "yesterday")


class CommentIdTest(unittest.TestCase):
    def test_a_number(self):
        self.assertEqual(parse_comment_id(" 10100 "), "10100")

    def test_anything_else_is_refused_before_it_can_reach_a_path(self):
        for text in ("", "abc", "10/../../x", "1 2", "-1", "10?x=1", "1" * 30, None):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_comment_id(text)

    def test_only_the_digits_0_to_9_make_a_number_not_the_digits_of_other_scripts(self):
        for text in ("١٠١٠١", "１０１０１", "²", "10١", "१०१"):  # Arabic-Indic, full-width, superscript, mixed, Devanagari
            with self.subTest(text=text), self.assertRaises(ValidationError) as caught:
                parse_comment_id(text)
            self.assertIn("Invalid comment id", caught.exception.message)

    def test_a_trailing_line_break_is_no_part_of_a_number(self):
        self.assertEqual(parse_comment_id("10100\n"), "10100")
        with self.assertRaises(ValidationError):
            parse_comment_id("10\n100")

    def test_a_time_ago_has_ascii_digits_too(self):
        for text in ("٢d", "２d", "٢.٥h"):
            with self.subTest(text=text), self.assertRaises(ValidationError) as caught:
                parse_since(text, NOW)
            self.assertIn("--since", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
