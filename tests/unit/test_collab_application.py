"""The use cases of the collab area, with a fake Jira: what they ask, what they send, what they leave out, what they refuse."""
import dataclasses
import unittest

from jira_dc_cli.domain.collab import RemoteLink, Worklog, WorklogPage
from jira_dc_cli.domain.errors import AuthError, ConflictError, NotFoundError, RejectedError, UnavailableError, UsageError, ValidationError
from jira_dc_cli.domain.worktime import worklog_request
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, RecordingDiagnostics, build_app


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class CollabCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.diagnostics = RecordingDiagnostics()
        self.app = self.make()
        self.collab = self.app.collab

    def make(self, **changes):
        return build_app(gateway=self.gateway, settings=settings(**changes), diagnostics=self.diagnostics)

    def names(self, call):
        return [item[0] for item in self.gateway.calls if item[0] == call]


class WatchersTest(CollabCase):
    def test_the_count_and_whether_you_watch_but_not_who(self):
        found = self.collab.watchers.execute("proj-1")
        self.assertEqual((found.count, found.watching, found.users), (2, False, None))

    def test_who_only_when_asked_for(self):
        self.assertEqual([user.name for user in self.collab.watchers.execute("PROJ-1", with_users=True).users], ["asmith", "bwong"])

    def test_jira_that_lists_nobody_leaves_the_list_open_and_the_count_standing(self):
        self.gateway.collab_watchers_listed = False
        found = self.collab.watchers.execute("PROJ-1", with_users=True)
        self.assertEqual((found.count, found.users), (2, None))

    def test_the_policy_refuses_the_option_before_asking(self):
        with self.assertRaises(ValidationError) as caught:
            self.make(hide_users=True).collab.watchers.execute("PROJ-1", with_users=True)
        self.assertIn("JIRA_HIDE_USERS=true", caught.exception.message)
        self.assertEqual(self.gateway.calls, [])
        self.assertIsNone(self.make(hide_users=True).collab.watchers.execute("PROJ-1").users)

    def test_a_bad_key_and_an_unknown_issue(self):
        with self.assertRaises(ValidationError):
            self.collab.watchers.execute("nope")
        with self.assertRaises(NotFoundError):
            self.collab.watchers.execute("PROJ-999")


class WatchTest(CollabCase):
    def test_you_are_added_with_your_own_name_and_the_list_is_read_back(self):
        result = self.collab.watch.execute("proj-2")
        self.assertEqual((result.action, result.user, result.payload, result.query, result.dry_run), ("added", None, "jdoe", {}, False))
        self.assertEqual((result.watch_count, result.watching, result.confirmed), (1, True, True))
        self.assertEqual(self.gateway.called("add_watcher"), [("add_watcher", "PROJ-2", "jdoe")])
        self.assertEqual([call[0] for call in self.gateway.calls], ["current_user", "add_watcher", "get_watchers"])

    def test_a_dry_run_reads_and_sends_nothing(self):
        result = self.collab.watch.execute("PROJ-2", dry_run=True)
        self.assertEqual((result.dry_run, result.payload, result.watching, result.confirmed), (True, "jdoe", False, None))
        self.assertEqual([call[0] for call in self.gateway.calls], ["current_user", "get_watchers"])
        self.assertEqual(self.gateway.collab_watchers.get("PROJ-2", []), [])

    def test_a_dry_run_for_an_issue_that_does_not_exist_says_so(self):
        with self.assertRaises(NotFoundError):
            self.collab.watch.execute("PROJ-999", dry_run=True)

    def test_you_are_taken_off_again_with_a_query_and_no_body(self):
        self.collab.watch.execute("PROJ-2")
        result = self.collab.watch.execute("PROJ-2", remove=True)
        self.assertEqual((result.action, result.payload, result.query, result.watching, result.confirmed), ("removed", None, {"username": "jdoe"}, False, True))
        self.assertEqual(self.gateway.called("remove_watcher"), [("remove_watcher", "PROJ-2", {"username": "jdoe"})])

    def test_another_person_is_added_by_name_and_confirmed_from_the_list(self):
        result = self.collab.watch.execute("PROJ-2", user="bwong")
        self.assertEqual((result.user, result.payload, result.watching, result.confirmed), ("bwong", "bwong", False, True))

    def test_when_jira_lists_nobody_another_persons_change_cannot_be_confirmed(self):
        self.gateway.collab_watchers_listed = False
        self.assertIsNone(self.collab.watch.execute("PROJ-2", user="bwong").confirmed)

    def test_a_change_that_the_list_does_not_show_is_reported_as_not_confirmed(self):
        self.gateway.add_watcher = lambda key, payload: None
        result = self.collab.watch.execute("PROJ-2")
        self.assertEqual(result.confirmed, False)
        self.assertIn("does not show it yet", result.message)
        self.assertIn("jira watchers PROJ-2", result.message)
        self.assertEqual(self.diagnostics.warnings, [result.message])

    def test_a_confirmed_change_has_no_message_and_no_warning(self):
        self.assertEqual(self.collab.watch.execute("PROJ-2").message, "")
        self.assertEqual(self.collab.watch.execute("PROJ-2", dry_run=True).message, "")
        self.assertEqual(self.diagnostics.warnings, [])

    def test_your_own_name_typed_is_you(self):
        result = self.collab.watch.execute("PROJ-2", user="jdoe")
        self.assertEqual((result.user, result.watching, result.confirmed), ("jdoe", True, True))

    def test_me_is_you_and_is_sent_as_your_own_name(self):
        result = self.collab.watch.execute("PROJ-2", user=" ME ")
        self.assertEqual((result.user, result.payload, result.watching, result.confirmed), (None, "jdoe", True, True))
        self.assertEqual(self.gateway.called("add_watcher"), [("add_watcher", "PROJ-2", "jdoe")])
        removed = self.collab.watch.execute("PROJ-2", user="me", remove=True)
        self.assertEqual((removed.user, removed.query, removed.watching, removed.confirmed), (None, {"username": "jdoe"}, False, True))

    def test_the_policy_shows_a_stand_in_in_a_preview_and_says_nothing_about_what_a_made_call_sent(self):
        app = self.make(hide_users=True)
        preview = app.collab.watch.execute("PROJ-2", dry_run=True)
        self.assertEqual((preview.payload, preview.query, preview.user, preview.redacted), ("[you]", {}, None, True))
        undo = app.collab.watch.execute("PROJ-2", remove=True, dry_run=True)
        self.assertEqual((undo.payload, undo.query, undo.redacted), (None, {"username": "[you]"}, True))
        self.assertEqual(self.gateway.called("add_watcher") + self.gateway.called("remove_watcher"), [])
        added = app.collab.watch.execute("PROJ-2")
        self.assertEqual((added.payload, added.query, added.user, added.dry_run, added.redacted), (None, None, None, False, True))
        self.assertEqual(self.gateway.called("add_watcher"), [("add_watcher", "PROJ-2", "jdoe")])  # the call itself carries the real name
        removed = app.collab.watch.execute("PROJ-2", remove=True)
        self.assertEqual((removed.payload, removed.query, removed.redacted, removed.confirmed), (None, None, True, True))
        self.assertEqual(self.gateway.called("remove_watcher"), [("remove_watcher", "PROJ-2", {"username": "jdoe"})])

    def test_a_name_that_the_caller_typed_is_no_secret_under_the_policy(self):
        app = self.make(hide_users=True)
        preview = app.collab.watch.execute("PROJ-2", user="bwong", dry_run=True)
        self.assertEqual((preview.user, preview.payload, preview.redacted), ("bwong", "bwong", False))
        added = app.collab.watch.execute("PROJ-2", user="bwong")
        self.assertEqual((added.user, added.payload, added.query, added.redacted), ("bwong", "bwong", {}, False))
        own = app.collab.watch.execute("PROJ-2", user="jdoe", remove=True)  # the caller typed the own name: shown as typed
        self.assertEqual((own.user, own.query, own.redacted), ("jdoe", {"username": "jdoe"}, False))

    def test_without_the_policy_nothing_is_redacted(self):
        self.assertFalse(self.collab.watch.execute("PROJ-2", dry_run=True).redacted)
        self.assertFalse(self.collab.watch.execute("PROJ-2").redacted)

    def test_removing_is_for_yourself_only_and_is_refused_before_anything_is_sent(self):
        with self.assertRaises(UsageError) as caught:
            self.collab.watch.execute("PROJ-1", user="bwong", remove=True)
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("'bwong' is not you", caught.exception.message)
        self.assertIn("nothing was sent", caught.exception.message)
        self.assertIn("Manage Watchers", caught.exception.hint)
        self.assertIn("Remove another person in Jira", caught.exception.hint)
        self.assertEqual([call[0] for call in self.gateway.calls], ["current_user"])  # it asked who you are, and nothing else
        self.assertEqual([user.name for user in self.gateway.collab_watchers["PROJ-1"]], ["asmith", "bwong"])

    def test_the_refusal_is_the_same_on_a_dry_run_and_does_not_read_the_issue(self):
        with self.assertRaises(UsageError):
            self.collab.watch.execute("PROJ-1", user="asmith", remove=True, dry_run=True)
        self.assertEqual([call[0] for call in self.gateway.calls], ["current_user"])

    def test_the_refusal_comes_even_for_an_issue_that_does_not_exist_or_a_person_who_does_not_exist(self):
        for key, user in (("PROJ-999", "asmith"), ("PROJ-2", "nobody")):
            with self.subTest(key=key, user=user):
                with self.assertRaises(UsageError):
                    self.collab.watch.execute(key, user=user, remove=True)
        self.assertEqual(self.gateway.called("remove_watcher"), [])

    def test_your_own_name_may_remove_in_any_case_and_is_sent_as_jira_spells_it(self):
        self.collab.watch.execute("PROJ-2")
        for typed in ("jdoe", "JDOE", "Jdoe", " jdoe "):
            with self.subTest(typed=typed):
                result = self.collab.watch.execute("PROJ-2", user=typed, remove=True, dry_run=True)
                self.assertEqual((result.action, result.query, result.watching), ("removed", {"username": "jdoe"}, True))
        removed = self.collab.watch.execute("PROJ-2", user="JDOE", remove=True)
        self.assertEqual((removed.user, removed.query, removed.watching, removed.confirmed), ("JDOE", {"username": "jdoe"}, False, True))
        self.assertEqual(self.gateway.called("remove_watcher"), [("remove_watcher", "PROJ-2", {"username": "jdoe"})])

    def test_adding_another_person_stays_as_it_was(self):
        added = self.collab.watch.execute("PROJ-2", user="bwong")
        self.assertEqual((added.action, added.user, added.payload, added.confirmed), ("added", "bwong", "bwong", True))
        self.assertEqual(self.gateway.called("add_watcher"), [("add_watcher", "PROJ-2", "bwong")])

    def test_a_bad_user_name_is_refused_before_anything_is_asked(self):
        with self.assertRaises(ValidationError):
            self.collab.watch.execute("PROJ-2", user="a\nb")
        self.assertEqual(self.gateway.calls, [])

    def test_a_refusal_keeps_its_kind_and_says_which_permission_is_missing(self):
        self.gateway.errors["add_watcher"] = AuthError("Jira did not accept the token (HTTP 401).", details={"status": 401})
        with self.assertRaises(AuthError) as other:
            self.collab.watch.execute("PROJ-2", user="bwong")
        self.assertEqual((other.exception.exit_code, other.exception.details, other.exception.message), (5, {"status": 401}, "Jira did not accept the token (HTTP 401)."))
        self.assertIn("MANAGE_WATCHERS", other.exception.hint)
        self.assertIn("jira permissions --issue PROJ-2", other.exception.hint)
        with self.assertRaises(AuthError) as own:
            self.collab.watch.execute("PROJ-2")
        self.assertNotIn("MANAGE_WATCHERS", own.exception.hint)
        self.assertIn("jira server", own.exception.hint)

    def test_an_unknown_user_gets_a_hint_and_an_unknown_issue_does_not(self):
        with self.assertRaises(NotFoundError) as caught:
            self.collab.watch.execute("PROJ-2", user="nobody")
        self.assertIn("login name", caught.exception.hint)
        self.assertIn("nobody", caught.exception.message)
        with self.assertRaises(NotFoundError) as unknown:
            self.collab.watch.execute("PROJ-999")
        self.assertEqual(unknown.exception.hint, "")


class VotesTest(CollabCase):
    def test_the_count_and_whether_you_voted(self):
        votes = self.collab.votes.execute("proj-1")
        self.assertEqual((votes.count, votes.has_voted), (1, False))

    def test_you_vote_and_the_count_is_read_back(self):
        result = self.collab.vote.execute("PROJ-2")
        self.assertEqual((result.action, result.changed, result.dry_run, result.count, result.has_voted), ("added", True, False, 1, True))
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_votes", "add_vote", "get_votes"])

    def test_a_vote_that_is_given_already_is_not_sent_again(self):
        self.collab.vote.execute("PROJ-2")
        self.gateway.calls.clear()
        result = self.collab.vote.execute("PROJ-2")
        self.assertEqual((result.changed, result.count, result.has_voted), (False, 1, True))
        self.assertEqual(self.names("add_vote"), [])

    def test_your_vote_is_withdrawn_and_what_was_not_given_cannot_be_withdrawn(self):
        self.collab.vote.execute("PROJ-2")
        result = self.collab.vote.execute("PROJ-2", remove=True)
        self.assertEqual((result.action, result.changed, result.count, result.has_voted), ("removed", True, 0, False))
        self.gateway.calls.clear()
        again = self.collab.vote.execute("PROJ-2", remove=True)
        self.assertEqual((again.changed, self.names("remove_vote")), (False, []))

    def test_a_dry_run_reads_and_sends_nothing(self):
        result = self.collab.vote.execute("PROJ-2", dry_run=True)
        self.assertEqual((result.dry_run, result.changed, result.has_voted), (True, True, False))
        self.assertEqual([call[0] for call in self.gateway.calls], ["get_votes"])
        self.assertEqual(self.collab.vote.execute("PROJ-1", remove=True, dry_run=True).changed, False)

    def test_the_vote_that_jira_refuses_is_rejected_with_jiras_own_words(self):
        self.gateway.collab_vote_refusal = "You cannot vote for an issue you have reported."
        with self.assertRaises(RejectedError) as caught:
            self.collab.vote.execute("PROJ-2")
        self.assertEqual(caught.exception.exit_code, 6)
        self.assertIn("take a vote for PROJ-2: You cannot vote for an issue you have reported.", caught.exception.message)
        self.assertIn("jira server", caught.exception.hint)

    def test_an_unknown_issue_is_not_found(self):
        with self.assertRaises(NotFoundError):
            self.collab.vote.execute("PROJ-999")
        with self.assertRaises(NotFoundError):
            self.collab.votes.execute("PROJ-999")


class WorklogsTest(CollabCase):
    def test_newest_first_with_the_time_of_what_is_shown(self):
        result = self.collab.worklogs.execute("proj-1")
        self.assertEqual(([entry.id for entry in result.worklogs], result.total, result.seconds), (["10101", "10100"], 2, 12600))

    def test_people_and_mentions_stay_out_unless_asked_for(self):
        entries = self.collab.worklogs.execute("PROJ-1").worklogs
        self.assertEqual([entry.author for entry in entries], [None, None])
        self.assertEqual(entries[1].comment, "Reviewed with [~user] ([email])")
        shown = self.collab.worklogs.execute("PROJ-1", with_users=True).worklogs
        self.assertEqual([entry.author.name for entry in shown], ["jdoe", "asmith"])
        self.assertEqual(shown[1].comment, "Reviewed with [~user] ([email])")

    def test_the_policy_refuses_the_option_before_asking(self):
        with self.assertRaises(ValidationError):
            self.make(hide_users=True).collab.worklogs.execute("PROJ-1", with_users=True)
        self.assertEqual(self.gateway.calls, [])

    def test_since_is_counted_from_now_or_from_a_date(self):
        for since in ("3d", "72h", "2026-10-15", "2026-10-17T12:00:00Z"):
            with self.subTest(since=since):
                self.assertEqual([entry.id for entry in self.collab.worklogs.execute("PROJ-1", since=since).worklogs], ["10101"])
        self.assertEqual(len(self.collab.worklogs.execute("PROJ-1", since="7d").worklogs), 2)
        self.assertEqual(self.collab.worklogs.execute("PROJ-1", since="1h").worklogs, [])

    def test_a_bad_since_is_refused_before_asking(self):
        with self.assertRaises(ValidationError):
            self.collab.worklogs.execute("PROJ-1", since="yesterday")
        self.assertEqual(self.gateway.calls, [])

    def test_the_limit_keeps_the_newest_entries_and_the_time_of_those(self):
        result = self.collab.worklogs.execute("PROJ-1", limit=1)
        self.assertEqual(([entry.id for entry in result.worklogs], result.total, result.seconds), (["10101"], 2, 7200))
        with self.assertRaises(UsageError):
            self.collab.worklogs.execute("PROJ-1", limit=-1)

    def test_an_issue_without_work_and_an_unknown_issue(self):
        self.assertEqual(self.collab.worklogs.execute("PROJ-2").seconds, 0)
        with self.assertRaises(NotFoundError):
            self.collab.worklogs.execute("PROJ-999")

    def test_a_server_that_returns_less_than_it_holds_says_so(self):
        self.gateway.collab_worklog_total_extra = 3
        result = self.collab.worklogs.execute("PROJ-1")
        self.assertEqual((result.total, len(result.worklogs)), (5, 2))
        self.assertIn("2 of the 5 worklogs", self.diagnostics.warnings[0])

    def test_an_entry_whose_start_cannot_be_read_counts_as_the_oldest(self):
        self.gateway.collab_worklogs["PROJ-1"].append(Worklog("10199", "sometime", "1h", 3600))
        result = self.collab.worklogs.execute("PROJ-1")
        self.assertEqual([entry.id for entry in result.worklogs], ["10101", "10100", "10199"])
        self.assertEqual([entry.id for entry in self.collab.worklogs.execute("PROJ-1", since="3d").worklogs], ["10101"])


class LogWorkTest(CollabCase):
    def test_the_time_is_sent_in_jiras_words_and_the_entry_is_read_back(self):
        result = self.collab.log_work.execute("proj-2", "1 hour 30 minutes", comment=" Did it. ", started="2026-10-15T08:30:00+02:00", adjust="new", new_estimate="2d")
        self.assertEqual(result.payload, {"timeSpent": "1h 30m", "comment": "Did it.", "started": "2026-10-15T08:30:00.000+0200"})
        self.assertEqual((result.query, result.seconds, result.dry_run, result.confirmed), ({"adjustEstimate": "new", "newEstimate": "2d"}, 5400, False, True))
        self.assertEqual((result.worklog.time_spent, result.worklog.time_spent_seconds, result.worklog.author), ("1h 30m", 5400, None))
        sent = self.gateway.called("add_worklog")[0]
        self.assertEqual((sent[1], sent[2]), ("PROJ-2", worklog_request("1h 30m", comment="Did it.", started="2026-10-15T08:30:00+02:00", adjust="new", new_estimate="2d")))
        self.assertEqual(self.names("list_worklogs"), ["list_worklogs"])

    def test_days_are_left_to_jira(self):
        result = self.collab.log_work.execute("PROJ-2", "2d")
        self.assertEqual((result.payload["timeSpent"], result.seconds), ("2d", None))
        self.assertEqual(result.worklog.time_spent_seconds, 57600)  # what Jira made of it

    def test_a_dry_run_checks_the_issue_and_sends_nothing(self):
        result = self.collab.log_work.execute("PROJ-2", "30m", comment="x", dry_run=True)
        self.assertEqual((result.dry_run, result.payload, result.query, result.seconds, result.worklog, result.confirmed), (True, {"timeSpent": "30m", "comment": "x"}, {}, 1800, None, None))
        self.assertEqual(self.names("add_worklog"), [])
        self.assertEqual(self.gateway.called("get_issue"), [("get_issue", "PROJ-2", ("summary", "updated"))])
        with self.assertRaises(NotFoundError):
            self.collab.log_work.execute("PROJ-999", "30m", dry_run=True)

    def test_a_time_jira_would_read_differently_is_refused_before_anything_is_asked(self):
        for text in ("1.5h", "30", "0m", "later"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    self.collab.log_work.execute("PROJ-2", text)
        with self.assertRaises(ValidationError):
            self.collab.log_work.execute("PROJ-2", "1h", adjust="new")
        self.assertEqual(self.gateway.calls, [])

    def test_a_guard_against_a_change_in_between(self):
        with self.assertRaises(ConflictError):
            self.collab.log_work.execute("PROJ-2", "1h", if_unchanged_since="2026-10-01T00:00:00Z")
        self.assertEqual(self.names("add_worklog"), [])
        self.assertEqual(self.collab.log_work.execute("PROJ-2", "1h", if_unchanged_since="2026-10-20T00:00:00Z").confirmed, True)

    def test_the_comment_that_comes_back_is_redacted(self):
        result = self.collab.log_work.execute("PROJ-2", "1h", comment="Thanks [~jdoe] (jane.doe@example.com)")
        self.assertEqual(result.payload["comment"], "Thanks [~jdoe] (jane.doe@example.com)")  # what the caller wrote is what is sent
        self.assertEqual(result.worklog.comment, "Thanks [~user] ([email])")

    def test_an_entry_that_the_list_does_not_hold_is_not_confirmed(self):
        self.gateway.list_worklogs = lambda key: WorklogPage(0, [])
        result = self.collab.log_work.execute("PROJ-2", "1h")
        self.assertEqual((result.confirmed, result.worklog.time_spent), (False, "1h"))
        for words in ("does not hold the entry", "Do not run the command again", "jira worklogs PROJ-2"):
            self.assertIn(words, result.message)
        self.assertEqual(self.diagnostics.warnings, [result.message])

    def test_a_refusal_keeps_its_kind_and_says_what_to_check(self):
        self.gateway.errors["add_worklog"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as denied:
            self.collab.log_work.execute("PROJ-2", "1h")
        self.assertEqual((denied.exception.exit_code, denied.exception.details), (5, {"status": 403}))
        self.assertIn("WORK_ON_ISSUES", denied.exception.hint)
        self.assertIn("jira permissions --issue PROJ-2", denied.exception.hint)
        self.gateway.errors["add_worklog"] = RejectedError("Jira refused the request (HTTP 400): timeLogged: Invalid time duration entered.", hint="See jira metadata PROJ.")
        with self.assertRaises(RejectedError) as rejected:
            self.collab.log_work.execute("PROJ-2", "1h")
        self.assertIn("Invalid time duration", rejected.exception.message)
        self.assertNotIn("metadata", rejected.exception.hint)
        self.assertIn("time tracking", rejected.exception.hint)


class RemoteLinksTest(CollabCase):
    def test_the_links_are_listed_with_personal_data_redacted(self):
        self.gateway.collab_remote_links["PROJ-2"] = [RemoteLink("1", "Notes of [~jdoe]", "https://x.example.com/personal/jane_doe/doc", "see jane.doe@example.com", "App", "g")]
        link = self.collab.remote_links.execute("PROJ-2")[0]
        self.assertEqual((link.title, link.url, link.relationship, link.application, link.global_id), ("Notes of [~user]", "[personal link]", "see [email]", "App", "g"))
        self.assertEqual(self.collab.remote_links.execute("proj-1")[0].title, "Runbook")
        with self.assertRaises(NotFoundError):
            self.collab.remote_links.execute("PROJ-999")

    def test_a_new_link_is_added_and_read_back(self):
        result = self.collab.remote_link.execute("proj-2", "https://example.com/pr/7", "Pull request 7", relationship="implements")
        self.assertEqual((result.action, result.dry_run, result.confirmed), ("created", False, True))
        self.assertEqual(result.payload, {"object": {"url": "https://example.com/pr/7", "title": "Pull request 7"}, "relationship": "implements"})
        self.assertEqual((result.link.title, result.link.url), ("Pull request 7", "https://example.com/pr/7"))
        self.assertEqual(self.gateway.called("save_remote_link"), [("save_remote_link", "PROJ-2", result.payload)])

    def test_the_same_global_id_updates_the_link_that_has_it(self):
        result = self.collab.remote_link.execute("PROJ-1", "https://wiki.example.com/runbook/v2", "Runbook v2", global_id="wiki:runbook")
        self.assertEqual((result.action, result.link.id, result.link.title, result.confirmed), ("updated", "10000", "Runbook v2", True))
        self.assertEqual(len(self.gateway.collab_remote_links["PROJ-1"]), 1)

    def test_a_global_id_that_the_issue_does_not_hold_creates_a_link(self):
        result = self.collab.remote_link.execute("PROJ-1", "https://example.com/other", "Other", global_id="new-id")
        self.assertEqual((result.action, result.link.global_id), ("created", "new-id"))
        self.assertEqual(len(self.gateway.collab_remote_links["PROJ-1"]), 2)

    def test_a_dry_run_says_which_it_would_do_and_sends_nothing(self):
        update = self.collab.remote_link.execute("PROJ-1", "https://example.com/a", "A", global_id="wiki:runbook", dry_run=True)
        create = self.collab.remote_link.execute("PROJ-1", "https://example.com/a", "A", dry_run=True)
        self.assertEqual((update.action, create.action, update.dry_run, update.link), ("updated", "created", True, None))
        self.assertEqual(self.names("save_remote_link"), [])
        self.assertEqual(self.names("get_issue"), ["get_issue", "get_issue"])
        with self.assertRaises(NotFoundError):
            self.collab.remote_link.execute("PROJ-999", "https://example.com/a", "A", dry_run=True)

    def test_a_second_link_to_the_same_address_is_added_with_a_warning(self):
        result = self.collab.remote_link.execute("PROJ-1", "https://wiki.example.com/runbook", "Runbook again")
        self.assertEqual(result.action, "created")
        self.assertEqual(len(self.diagnostics.warnings), 1)
        self.assertIn("id 10000", self.diagnostics.warnings[0])
        self.assertIn("--global-id", self.diagnostics.warnings[0])
        self.collab.remote_link.execute("PROJ-1", "https://wiki.example.com/runbook", "Runbook", global_id="wiki:runbook")
        self.assertEqual(len(self.diagnostics.warnings), 1)  # an update is no duplicate

    def test_an_address_that_is_no_web_address_is_refused_before_anything_is_asked(self):
        for url in ("javascript:alert(1)", "https://user:secret@example.com/", "example.com", "https://example.com/a b"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    self.collab.remote_link.execute("PROJ-1", url, "T")
        with self.assertRaises(ValidationError):
            self.collab.remote_link.execute("PROJ-1", "https://example.com/", "  ")
        self.assertEqual(self.gateway.calls, [])

    def test_a_guard_against_a_change_in_between(self):
        with self.assertRaises(ConflictError):
            self.collab.remote_link.execute("PROJ-1", "https://example.com/a", "A", if_unchanged_since="2026-10-01T00:00:00Z")
        self.assertEqual(self.names("save_remote_link"), [])

    def test_what_is_read_back_is_redacted(self):
        result = self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "Thanks [~jdoe]")
        self.assertEqual(result.link.title, "Thanks [~user]")
        self.assertEqual(result.payload["object"]["title"], "Thanks [~jdoe]")

    def test_a_refusal_keeps_its_kind_and_says_what_to_check(self):
        self.gateway.errors["save_remote_link"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError) as denied:
            self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "A")
        self.assertIn("LINK_ISSUES", denied.exception.hint)
        self.gateway.errors["save_remote_link"] = RejectedError("Jira refused the request (HTTP 400): url", hint="See jira metadata PROJ.")
        with self.assertRaises(RejectedError) as rejected:
            self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "A")
        self.assertNotIn("metadata", rejected.exception.hint)

    def test_a_link_that_the_list_does_not_hold_is_not_confirmed(self):
        self.gateway.list_remote_links = lambda key: []
        result = self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "A")
        self.assertEqual((result.confirmed, result.link), (False, None))
        for words in ("does not hold it", "Do not run the command again", "jira remote-links PROJ-2"):
            self.assertIn(words, result.message)
        self.assertEqual(self.diagnostics.warnings, [result.message])


class ReadBackFailureTest(CollabCase):
    """A write that Jira took is never reported as a failure because the read after it failed: whoever sees a failure runs the command
    again, and this CLI cannot delete a worklog. The result says what was done, `confirmed` is None, and the command still succeeds."""

    UNAVAILABLE = UnavailableError("Jira is not available (HTTP 503).", details={"status": 503})
    FAILURES = (
        UNAVAILABLE,
        AuthError("Jira refused this request (HTTP 403).", details={"status": 403}),
        NotFoundError("Not found: GET /issue/PROJ-2 - Issue Does Not Exist"),
    )

    def fail_the_read_back_of(self, name, error=UNAVAILABLE):
        """`name` works once, for the read before the write, and then fails: that is the read-back."""
        self.gateway.__dict__.pop(name, None)  # the method of the fake, not the failure of an earlier call
        original, calls = getattr(self.gateway, name), []

        def second_call_fails(*args):
            calls.append(args)
            if len(calls) > 1:
                raise error
            return original(*args)

        setattr(self.gateway, name, second_call_fails)

    def test_log_work_says_that_the_work_was_logged_and_must_not_be_logged_again(self):
        for error in self.FAILURES:
            with self.subTest(error=type(error).__name__):
                self.gateway.calls.clear()
                self.diagnostics.warnings.clear()
                self.gateway.errors["list_worklogs"] = error
                result = self.collab.log_work.execute("PROJ-2", "1h", comment="Once")
                self.assertEqual((result.dry_run, result.confirmed), (False, None))
                self.assertEqual(result.payload, {"timeSpent": "1h", "comment": "Once"})
                self.assertEqual((result.worklog.time_spent, result.worklog.author), ("1h", None))  # what Jira answered to the write
                for words in ("WAS logged", f"(worklog {result.worklog.id})", error.message.rstrip("."), "Do not run the command again", "twice", "jira worklogs PROJ-2"):
                    self.assertIn(words, result.message)
                self.assertEqual(self.diagnostics.warnings, [result.message])
                self.assertEqual(len(self.gateway.called("add_worklog")), 1)
        self.assertEqual(len(self.gateway.collab_worklogs["PROJ-2"]), len(self.FAILURES))  # one entry per call, as made

    def test_the_entry_that_is_shown_instead_of_the_read_back_is_redacted_as_well(self):
        self.gateway.errors["list_worklogs"] = self.UNAVAILABLE
        result = self.collab.log_work.execute("PROJ-2", "1h", comment="Thanks [~jdoe] (jane.doe@example.com)")
        self.assertEqual((result.worklog.comment, result.worklog.author, result.confirmed), ("Thanks [~user] ([email])", None, None))

    def test_remote_link_says_that_the_link_was_added_and_must_not_be_added_again(self):
        for error in self.FAILURES:
            with self.subTest(error=type(error).__name__):
                self.gateway.calls.clear()
                self.diagnostics.warnings.clear()
                self.fail_the_read_back_of("list_remote_links", error)
                result = self.collab.remote_link.execute("PROJ-2", f"https://example.com/{type(error).__name__}", "Page")
                self.assertEqual((result.action, result.dry_run, result.link, result.confirmed), ("created", False, None, None))
                for words in ("WAS added", "(id 1020", error.message.rstrip("."), "Do not run the command again", "second link", "jira remote-links PROJ-2"):
                    self.assertIn(words, result.message)
                self.assertEqual(self.diagnostics.warnings, [result.message])
                self.assertEqual(len(self.gateway.called("save_remote_link")), 1)
        self.assertEqual(len(self.gateway.collab_remote_links["PROJ-2"]), len(self.FAILURES))

    def test_a_link_with_a_global_id_may_be_repeated_because_it_only_updates(self):
        self.fail_the_read_back_of("list_remote_links")
        result = self.collab.remote_link.execute("PROJ-1", "https://example.com/new", "New", global_id="wiki:runbook")
        self.assertEqual((result.action, result.link, result.confirmed), ("updated", None, None))
        self.assertIn("WAS updated", result.message)
        self.assertIn("(id 10000)", result.message)
        self.assertIn("with the same --global-id only updates that link", result.message)
        self.assertNotIn("Do not run", result.message)
        self.assertIn("jira remote-links PROJ-1", result.message)

    def test_watch_says_that_the_watcher_was_added_or_taken_off(self):
        self.gateway.errors["get_watchers"] = self.UNAVAILABLE
        added = self.collab.watch.execute("PROJ-2")
        self.assertEqual((added.action, added.watch_count, added.watching, added.confirmed, added.dry_run), ("added", None, None, None, False))
        self.assertEqual((added.payload, added.query, added.redacted), ("jdoe", {}, False))
        for words in ("PROJ-2: the watcher was added", "reading the watcher list afterwards failed", "HTTP 503", "jira watchers PROJ-2"):
            self.assertIn(words, added.message)
        removed = self.collab.watch.execute("PROJ-2", remove=True)
        self.assertEqual((removed.action, removed.watch_count, removed.watching, removed.confirmed), ("removed", None, None, None))
        self.assertIn("the watcher was taken off", removed.message)
        self.assertEqual(self.diagnostics.warnings, [added.message, removed.message])
        self.assertEqual((len(self.gateway.called("add_watcher")), len(self.gateway.called("remove_watcher"))), (1, 1))

    def test_a_watch_that_is_read_back_under_the_policy_still_says_nothing_about_what_it_sent(self):
        self.gateway.errors["get_watchers"] = self.UNAVAILABLE
        result = self.make(hide_users=True).collab.watch.execute("PROJ-2")
        self.assertEqual((result.payload, result.query, result.redacted, result.confirmed), (None, None, True, None))
        self.assertNotIn("jdoe", result.message)

    def test_vote_says_that_the_vote_was_given_or_withdrawn(self):
        self.fail_the_read_back_of("get_votes")
        added = self.collab.vote.execute("PROJ-2")
        self.assertEqual((added.action, added.changed, added.dry_run, added.count, added.has_voted), ("added", True, False, None, None))
        for words in ("PROJ-2: your vote was added", "reading the votes afterwards failed", "HTTP 503", "jira votes PROJ-2"):
            self.assertIn(words, added.message)
        self.assertEqual(self.gateway.collab_voters["PROJ-2"], {"jdoe"})  # it was given
        self.fail_the_read_back_of("get_votes")
        removed = self.collab.vote.execute("PROJ-2", remove=True)
        self.assertEqual((removed.action, removed.changed, removed.count, removed.has_voted), ("removed", True, None, None))
        self.assertIn("your vote was withdrawn", removed.message)
        self.assertEqual(self.diagnostics.warnings, [added.message, removed.message])
        self.assertEqual((len(self.gateway.called("add_vote")), len(self.gateway.called("remove_vote"))), (1, 1))

    def test_what_fails_before_the_write_is_still_a_failure_and_sends_nothing(self):
        self.gateway.errors["get_watchers"] = self.UNAVAILABLE
        with self.assertRaises(UnavailableError):
            self.collab.watch.execute("PROJ-2", dry_run=True)
        self.gateway.errors["get_votes"] = self.UNAVAILABLE
        with self.assertRaises(UnavailableError):
            self.collab.vote.execute("PROJ-2")
        self.gateway.errors["list_remote_links"] = self.UNAVAILABLE
        with self.assertRaises(UnavailableError):
            self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "A")
        self.assertEqual([call[0] for call in self.gateway.calls if call[0].startswith(("add_", "remove_", "save_"))], [])
        self.assertEqual(self.diagnostics.warnings, [])

    def test_a_write_that_fails_is_a_failure_and_is_not_confirmed_by_anything(self):
        for name, run in (
            ("add_worklog", lambda: self.collab.log_work.execute("PROJ-2", "1h")),
            ("save_remote_link", lambda: self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "A")),
            ("add_watcher", lambda: self.collab.watch.execute("PROJ-2")),
            ("add_vote", lambda: self.collab.vote.execute("PROJ-2")),
        ):
            with self.subTest(write=name):
                self.gateway.errors[name] = self.UNAVAILABLE
                with self.assertRaises(UnavailableError):
                    run()
        self.assertEqual(self.diagnostics.warnings, [])

    def test_only_what_jira_or_the_network_does_is_caught_not_a_bug(self):
        self.gateway.errors["list_worklogs"] = RuntimeError("a bug")
        with self.assertRaises(RuntimeError):
            self.collab.log_work.execute("PROJ-2", "1h")

    def test_a_read_back_that_works_has_no_message_and_no_warning(self):
        results = (
            self.collab.log_work.execute("PROJ-2", "1h"),
            self.collab.remote_link.execute("PROJ-2", "https://example.com/a", "A"),
            self.collab.vote.execute("PROJ-2"),
            self.collab.vote.execute("PROJ-2"),  # nothing to send
            self.collab.watch.execute("PROJ-2"),
        )
        self.assertEqual([item.message for item in results], [""] * 5)
        self.assertEqual(self.diagnostics.warnings, [])


if __name__ == "__main__":
    unittest.main()
