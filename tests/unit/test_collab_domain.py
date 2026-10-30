"""The pure rules of the collab area: time as Jira reads it, the worklog request, web links and watchers, with hostile input."""
import time
import unittest
from datetime import datetime, timedelta, timezone

from jira_dc_cli.domain.collab import user_name, watcher_payload, watcher_query
from jira_dc_cli.domain.errors import ValidationError
from jira_dc_cli.domain.remotelinks import MAX_TEXT, remote_link_payload, web_address
from jira_dc_cli.domain.worktime import format_seconds, jira_timestamp, parse_since, parse_time_text, worklog_request

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


class TimeTextTest(unittest.TestCase):
    def test_hours_and_minutes_have_a_length_that_no_configuration_changes(self):
        for text, canonical, seconds in (("1h 30m", "1h 30m", 5400), ("90m", "90m", 5400), ("2h", "2h", 7200), ("45 min", "45m", 2700), ("1H 5M", "1h 5m", 3900)):
            with self.subTest(text=text):
                parsed = parse_time_text(text)
                self.assertEqual((parsed.text, parsed.seconds), (canonical, seconds))

    def test_days_and_weeks_are_left_to_jira_and_have_no_length_here(self):
        for text, canonical in (("2d", "2d"), ("1w 2d", "1w 2d"), ("1w", "1w"), ("2d 3h", "2d 3h"), ("1 week 2 days", "1w 2d")):
            with self.subTest(text=text):
                parsed = parse_time_text(text)
                self.assertEqual((parsed.text, parsed.seconds), (canonical, None))

    def test_the_text_is_put_in_jiras_order_and_words_and_separators_are_forgiven(self):
        self.assertEqual(parse_time_text("30m 1h").text, "1h 30m")
        self.assertEqual(parse_time_text("1h30m").text, "1h 30m")
        self.assertEqual(parse_time_text("1 hour, 30 minutes").text, "1h 30m")
        self.assertEqual(parse_time_text("2 days 3 hrs").text, "2d 3h")
        self.assertEqual(parse_time_text("  3h  ").text, "3h")

    def test_what_jira_could_read_differently_or_not_at_all_is_refused(self):
        for text in ("", "   ", "30", "1.5h", "1,5h", "0m", "0h 0m", "1h 2h", "abc", "1x", "-1h", "1h;", "h", "1h 30", "1h and 30m", "١h", "1h\x00", "1d" * 200, "9" * 6 + "h"):
            with self.subTest(text=text[:20]):
                with self.assertRaises(ValidationError) as caught:
                    parse_time_text(text, what="TIME")
                self.assertTrue(caught.exception.message.startswith("TIME "))
                self.assertIn("1h 30m", caught.exception.hint)

    def test_a_zero_estimate_is_allowed_where_it_means_done(self):
        self.assertEqual(parse_time_text("0m", allow_zero=True).text, "0m")

    def test_hostile_text_is_refused_quickly_and_not_echoed_whole(self):
        started = time.perf_counter()
        for text in ("1" * 100_000, "1h " * 50_000, " " * 100_000 + "!", "1" + "h" * 100_000):
            with self.assertRaises(ValidationError) as caught:
                parse_time_text(text)
            self.assertLess(len(caught.exception.message), 200)
        self.assertLess(time.perf_counter() - started, 1.0)

    def test_seconds_as_hours_and_minutes(self):
        self.assertEqual([format_seconds(value) for value in (0, 59, 60, 3600, 5400, 86400, 90061, -5)], ["0m", "0m", "1m", "1h", "1h 30m", "24h", "25h 1m", "0m"])


class SinceTest(unittest.TestCase):
    def test_a_time_ago_is_clock_time(self):
        for text, delta in (("30m", timedelta(minutes=30)), ("12h", timedelta(hours=12)), ("2d", timedelta(days=2)), ("1w", timedelta(weeks=1)), (" 3 D ", timedelta(days=3))):
            with self.subTest(text=text):
                self.assertEqual(parse_since(text, NOW), NOW - delta)

    def test_a_date_or_a_time_with_or_without_an_offset(self):
        self.assertEqual(parse_since("2026-10-15", NOW), datetime(2026, 10, 15, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2026-10-15T08:30:00+02:00", NOW), datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc))
        self.assertEqual(parse_since("2026-10-15T08:30:00.000+0200", NOW), datetime(2026, 10, 15, 6, 30, tzinfo=timezone.utc))

    def test_anything_else_is_refused_with_what_to_write(self):
        for text in ("", "yesterday", "2x", "-2d", "10000d", "2026-13-45", "1d 2h"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as caught:
                    parse_since(text, NOW)
                self.assertIn("--since", caught.exception.message)
                self.assertIn("2d", caught.exception.hint)


class JiraTimestampTest(unittest.TestCase):
    def test_the_offset_has_no_colon_and_the_fraction_is_milliseconds(self):
        plus = datetime(2026, 10, 15, 8, 30, 5, 123456, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(jira_timestamp(plus), "2026-10-15T08:30:05.123+0200")
        self.assertEqual(jira_timestamp(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)), "2026-01-02T03:04:05.000+0000")
        self.assertEqual(jira_timestamp(datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=-5, minutes=-30)))), "2026-01-02T00:00:00.000-0530")


class WorklogRequestTest(unittest.TestCase):
    def test_the_smallest_request_sends_nothing_that_was_not_asked_for(self):
        request = worklog_request("1h 30m")
        self.assertEqual((request.body, request.query, request.seconds), ({"timeSpent": "1h 30m"}, {}, 5400))

    def test_comment_and_started_are_sent_in_the_form_jira_reads(self):
        request = worklog_request("2d", comment="  Reviewed.  ", started="2026-10-15T08:30:00+02:00")
        self.assertEqual(request.body, {"timeSpent": "2d", "comment": "Reviewed.", "started": "2026-10-15T08:30:00.000+0200"})
        self.assertIsNone(request.seconds)
        self.assertNotIn("comment", worklog_request("1h", comment="   ").body)

    def test_a_date_without_a_zone_counts_as_utc(self):
        self.assertEqual(worklog_request("1h", started="2026-10-15").body["started"], "2026-10-15T00:00:00.000+0000")

    def test_how_the_estimate_is_adjusted(self):
        self.assertEqual(worklog_request("1h", adjust="leave").query, {"adjustEstimate": "leave"})
        self.assertEqual(worklog_request("1h", adjust="AUTO").query, {"adjustEstimate": "auto"})
        self.assertEqual(worklog_request("1h", adjust="new", new_estimate="1 day").query, {"adjustEstimate": "new", "newEstimate": "1d"})
        self.assertEqual(worklog_request("1h", adjust="new", new_estimate="0m").query["newEstimate"], "0m")
        self.assertEqual(worklog_request("1h", adjust="manual", reduce_by="30 minutes").query, {"adjustEstimate": "manual", "reduceBy": "30m"})

    def test_options_that_do_not_belong_together_are_refused(self):
        for options, fragment in (
            ({"adjust": "new"}, "--new-estimate"),
            ({"adjust": "manual"}, "--reduce-by"),
            ({"new_estimate": "2d"}, "--adjust-estimate new"),
            ({"adjust": "leave", "reduce_by": "1h"}, "--adjust-estimate manual"),
            ({"adjust": "sometimes"}, "one of auto, leave, new, manual"),
            ({"adjust": "new", "new_estimate": "soon"}, "--new-estimate is not a length of time"),
            ({"adjust": "manual", "reduce_by": "0m"}, "--reduce-by must be more than zero"),
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValidationError) as caught:
                    worklog_request("1h", **options)
                self.assertIn(fragment, caught.exception.message + caught.exception.hint)

    def test_a_bad_time_or_start_is_refused_before_anything_is_built(self):
        with self.assertRaises(ValidationError):
            worklog_request("1.5h")
        with self.assertRaises(ValidationError):
            worklog_request("1h", started="when it was done")


class RemoteLinkTest(unittest.TestCase):
    def test_the_smallest_payload(self):
        self.assertEqual(remote_link_payload("https://wiki.example.com/runbook", "Runbook"), {"object": {"url": "https://wiki.example.com/runbook", "title": "Runbook"}})

    def test_relationship_and_global_id_are_added_when_given(self):
        payload = remote_link_payload("http://example.com/a?b=1&c=2#top", "  A   page  ", relationship=" documents ", global_id="wiki:1")
        self.assertEqual(payload, {"object": {"url": "http://example.com/a?b=1&c=2#top", "title": "A page"}, "relationship": "documents", "globalId": "wiki:1"})

    def test_only_http_and_https_addresses_without_credentials_are_taken(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://example.com/x", "example.com/x", "//example.com/x", "https://", "https:///x",
                    "https://exa mple.com/x", "https://example.com/a b", "https://user:secret@example.com/x", "https://user@example.com/x", "https://example.com/\x00", "", "   ",
                    "https://example.com/" + "a" * 2000):
            with self.subTest(url=url[:40]):
                with self.assertRaises(ValidationError):
                    web_address(url)

    def test_the_scheme_is_not_case_sensitive_and_a_port_or_an_address_literal_is_fine(self):
        for url in ("HTTPS://Example.com/x", "http://localhost:8080/x", "https://10.0.0.1/x", "https://[::1]:8443/x"):
            with self.subTest(url=url):
                self.assertEqual(web_address(url), url)

    def test_the_credentials_are_never_echoed(self):
        with self.assertRaises(ValidationError) as caught:
            web_address("https://jdoe:hunter2@example.com/x")
        self.assertNotIn("hunter2", caught.exception.message + caught.exception.hint)

    def test_title_and_texts_are_checked(self):
        for title in ("", "   ", "x" * (MAX_TEXT + 1), "bell\x07"):
            with self.subTest(title=title[:10]):
                with self.assertRaises(ValidationError):
                    remote_link_payload("https://example.com/", title)
        with self.assertRaises(ValidationError):
            remote_link_payload("https://example.com/", "T", relationship="r" * (MAX_TEXT + 1))
        with self.assertRaises(ValidationError):
            remote_link_payload("https://example.com/", "T", global_id="g\x00")


class WatcherTest(unittest.TestCase):
    def test_the_body_is_the_user_name_and_the_query_names_it_for_a_removal(self):
        self.assertEqual(watcher_payload(" jdoe "), "jdoe")
        self.assertEqual(watcher_query("jdoe"), {"username": "jdoe"})

    def test_a_user_name_that_jira_could_not_take_is_refused(self):
        for text in ("", "  ", "a\nb", "x" * 256):
            with self.subTest(text=text[:10]):
                with self.assertRaises(ValidationError):
                    user_name(text)
        self.assertEqual(user_name("jane.doe@example.com"), "jane.doe@example.com")  # a login can look like an address


if __name__ == "__main__":
    unittest.main()
