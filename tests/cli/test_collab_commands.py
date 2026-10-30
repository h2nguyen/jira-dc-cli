"""The commands of the collab area, run through the command line with a fake Jira behind it: text, JSON, dry runs, privacy, errors."""
import dataclasses
import json
import unittest

from jira_dc_cli.adapters.driving.cli.help import mcp_tools
from jira_dc_cli.adapters.driving.cli.registry import all_commands, find_command
from jira_dc_cli.domain.collab import Worklog
from jira_dc_cli.domain.errors import AuthError, UnavailableError
from jira_dc_cli.version import __version__
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway

POLICY = dataclasses.replace(DEFAULT_SETTINGS, hide_users=True)
UNAVAILABLE = UnavailableError("Jira is not available (HTTP 503).", details={"status": 503})


class CommandCase(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()

    def run_cli(self, *argv, **options):
        return run_cli(*argv, gateway=self.gateway, **options)

    def ok(self, *argv, **options):
        result = self.run_cli(*argv, **options)
        self.assertEqual(result.code, 0, result.err)
        return result

    def sent(self, name):
        return self.gateway.called(name)

    def fail_the_read_back_of(self, name, error=UNAVAILABLE):
        """`name` works once, for the read before the write, and then fails for good: that is the read-back."""
        self.gateway.__dict__.pop(name, None)  # the method of the fake, not the failure of an earlier call
        original, calls = getattr(self.gateway, name), []

        def second_call_fails(*args):
            calls.append(args)
            if len(calls) > 1:
                raise error
            return original(*args)

        setattr(self.gateway, name, second_call_fails)


class WatchersTest(CommandCase):
    def test_the_count_and_whether_you_watch_but_not_who(self):
        self.assertEqual(self.ok("watchers", "proj-1").out, "PROJ-1: 2 watchers, you are not watching\nWho they are: add --with-users.\n")
        self.assertEqual(self.ok("watchers", "PROJ-2").out, "PROJ-2: 0 watchers, you are not watching\n")
        data = self.ok("watchers", "PROJ-1", "--json").json()
        self.assertEqual(data, {"key": "PROJ-1", "count": 2, "watching": False, "watchers": None})

    def test_who_with_with_users(self):
        lines = self.ok("watchers", "PROJ-1", "--with-users").out.splitlines()
        self.assertEqual(lines[1].split(), ["NAME", "DISPLAY", "NAME"])
        self.assertEqual(lines[2].split(), ["asmith", "Alex", "Smith"])
        data = self.ok("watchers", "PROJ-1", "--with-users", "--json").json()
        self.assertEqual(data["watchers"][0], {"name": "asmith", "displayName": "Alex Smith", "email": "alex.smith@example.com"})

    def test_when_jira_lists_nobody_it_says_why(self):
        self.gateway.collab_watchers_listed = False
        out = self.ok("watchers", "PROJ-1", "--with-users").out
        self.assertIn("2 watchers", out)
        self.assertIn("View Voters and Watchers", out)
        self.assertIsNone(self.ok("watchers", "PROJ-1", "--with-users", "--json").json()["watchers"])

    def test_the_policy_refuses_the_option_and_never_shows_a_person(self):
        result = self.run_cli("watchers", "PROJ-1", "--with-users", "--json", settings=POLICY)
        self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
        plain = self.ok("watchers", "PROJ-1", "--json", settings=POLICY)
        self.assertNotIn("asmith", plain.out + plain.err)

    def test_an_unknown_issue_and_a_bad_key(self):
        self.assertEqual(self.run_cli("watchers", "PROJ-999").code, 4)
        self.assertEqual(self.run_cli("watchers", "nope").code, 1)
        self.assertEqual(self.run_cli("watchers").code, 2)


class WatchTest(CommandCase):
    def test_a_dry_run_shows_the_body_and_sends_nothing(self):
        result = self.ok("watch", "PROJ-2", "--dry-run")
        self.assertEqual(result.out, 'Dry run: you would be added to the watchers of PROJ-2 (you do not watch it now); nothing was sent\n"jdoe"\n')
        self.assertEqual(self.sent("add_watcher"), [])
        data = self.ok("watch", "PROJ-2", "--dry-run", "--json").json()
        self.assertEqual(
            data,
            {"key": "PROJ-2", "action": "added", "user": None, "payload": "jdoe", "query": {}, "dryRun": True, "watchCount": 0, "watching": False,
             "confirmed": None, "redacted": False, "message": ""},
        )

    def test_you_start_watching_and_the_list_is_read_back(self):
        self.assertEqual(self.ok("watch", "PROJ-2").out, "Added you to the watchers of PROJ-2 (1 watcher now).\n")
        data = self.ok("watch", "PROJ-2", "--json").json()
        self.assertEqual((data["dryRun"], data["watching"], data["confirmed"], data["watchCount"]), (False, True, True, 1))

    def test_you_stop_watching_with_a_query_and_no_body(self):
        self.ok("watch", "PROJ-2")
        preview = self.ok("watch", "PROJ-2", "--remove", "--dry-run")
        self.assertEqual(preview.out, 'Dry run: you would be removed from the watchers of PROJ-2 (you watch it now); nothing was sent\nQuery: {"username": "jdoe"}\n')
        self.assertEqual(self.ok("watch", "PROJ-2", "--remove").out, "Removed you from the watchers of PROJ-2 (0 watchers now).\n")
        self.assertEqual(self.sent("remove_watcher")[0][1:], ("PROJ-2", {"username": "jdoe"}))

    def test_another_person_by_login_name(self):
        self.assertEqual(self.ok("watch", "PROJ-2", "--user", "bwong").out, "Added bwong to the watchers of PROJ-2 (1 watcher now).\n")
        data = self.ok("watch", "PROJ-2", "--user", "asmith", "--json").json()
        self.assertEqual((data["user"], data["payload"], data["query"], data["confirmed"]), ("asmith", "asmith", {}, True))
        self.assertEqual([call[2] for call in self.sent("add_watcher")], ["bwong", "asmith"])

    def test_removing_another_person_is_refused_with_exit_code_2_and_sends_nothing(self):
        for argv in (("watch", "PROJ-1", "--user", "bwong", "--remove"), ("watch", "PROJ-1", "--user", "bwong", "--remove", "--dry-run")):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual((result.code, result.out), (2, ""))
                self.assertIn("'bwong' is not you", result.err)
                self.assertIn("Remove another person in Jira", result.err)
        self.assertEqual([call[0] for call in self.gateway.calls], ["current_user", "current_user"])  # who you are, and nothing else
        error = self.run_cli("watch", "PROJ-1", "--user", "bwong", "--remove", "--json").error()
        self.assertEqual((error["code"], error["exitCode"]), ("USAGE", 2))
        self.assertIn("Manage Watchers", error["hint"])
        self.assertEqual(self.sent("remove_watcher"), [])
        self.assertEqual(len(self.gateway.collab_watchers["PROJ-1"]), 2)

    def test_you_may_remove_yourself_by_your_name_in_any_case_and_as_me(self):
        self.ok("watch", "PROJ-2")
        for typed, who in {"jdoe": "jdoe", "JDOE": "JDOE", "me": "you", "ME": "you"}.items():
            with self.subTest(typed=typed):
                preview = self.ok("watch", "PROJ-2", "--user", typed, "--remove", "--dry-run")
                self.assertIn(f"Dry run: {who} would be removed from the watchers of PROJ-2 (you watch it now); nothing was sent", preview.out)
                self.assertIn('Query: {"username": "jdoe"}', preview.out)  # the name as Jira spells it
        self.assertEqual(self.sent("remove_watcher"), [])
        data = self.ok("watch", "PROJ-2", "--user", "Me", "--remove", "--json").json()
        self.assertEqual((data["user"], data["query"], data["watching"], data["confirmed"]), (None, {"username": "jdoe"}, False, True))
        self.ok("watch", "PROJ-2")
        self.assertEqual(self.ok("watch", "PROJ-2", "--user", "JDOE", "--remove").out, "Removed JDOE from the watchers of PROJ-2 (0 watchers now).\n")
        self.assertEqual([call[2] for call in self.sent("remove_watcher")], [{"username": "jdoe"}, {"username": "jdoe"}])

    def test_me_adds_you(self):
        self.assertEqual(self.ok("watch", "PROJ-2", "--user", "me").out, "Added you to the watchers of PROJ-2 (1 watcher now).\n")
        self.assertEqual(self.sent("add_watcher")[0][2], "jdoe")

    def test_the_help_says_that_removing_is_for_yourself_only(self):
        command = find_command("watch")
        helps = {name: arg.help for arg in command.args for name in arg.names}
        self.assertIn("yourself", helps["--remove"])
        self.assertIn("`me`", helps["--user"])
        self.assertIn("only your own watch", command.description)
        self.assertIn("with --remove to stop watching yourself", command.when)

    def test_under_the_policy_a_made_call_says_nothing_of_what_it_sent_and_a_preview_shows_a_stand_in(self):
        sample = {"key": "PROJ-2", "action": "added", "user": None, "dryRun": True, "watchCount": 0, "watching": False, "confirmed": None, "message": ""}
        preview = self.ok("watch", "PROJ-2", "--dry-run", "--json", settings=POLICY).json()
        self.assertEqual(preview, {**sample, "payload": "[you]", "query": {}, "redacted": True})
        made = self.ok("watch", "PROJ-2", "--json", settings=POLICY).json()
        self.assertEqual(made, {**sample, "payload": None, "query": None, "dryRun": False, "watchCount": 1, "watching": True, "confirmed": True, "redacted": True})
        undone = self.ok("watch", "PROJ-2", "--remove", "--json", settings=POLICY).json()
        self.assertEqual(undone, {**sample, "action": "removed", "payload": None, "query": None, "dryRun": False, "watching": False, "confirmed": True, "redacted": True})
        typed = self.ok("watch", "PROJ-2", "--user", "bwong", "--json", settings=POLICY).json()
        self.assertEqual((typed["user"], typed["payload"], typed["query"], typed["redacted"]), ("bwong", "bwong", {}, False))
        self.assertEqual(self.sent("add_watcher")[0][2], "jdoe")  # the call itself carried the real name
        self.assertEqual(self.sent("remove_watcher")[0][2], {"username": "jdoe"})

    def test_under_the_policy_your_own_name_is_not_printed_and_a_typed_one_is(self):
        for argv in (("watch", "PROJ-2", "--dry-run"), ("watch", "PROJ-2"), ("watch", "PROJ-2", "--remove", "--dry-run", "--json"), ("watch", "PROJ-2", "--json")):
            with self.subTest(argv=argv):
                result = self.ok(*argv, settings=POLICY)
                for personal in ("jdoe", "Jane", "jane.doe"):
                    self.assertNotIn(personal, result.out + result.err)
        self.assertIn('"[you]"', self.ok("watch", "PROJ-2", "--dry-run", settings=POLICY).out)
        self.assertIn("Query: " + '{"username": "[you]"}', self.ok("watch", "PROJ-2", "--remove", "--dry-run", settings=POLICY).out)
        self.assertEqual(self.sent("add_watcher")[0][2], "jdoe")  # what is sent is the real name
        self.assertIn('"bwong"', self.ok("watch", "PROJ-2", "--user", "bwong", "--dry-run", settings=POLICY).out)

    def test_a_refusal_says_which_permission_is_missing(self):
        self.gateway.errors["add_watcher"] = AuthError("Jira did not accept the token (HTTP 401).")
        result = self.run_cli("watch", "PROJ-2", "--user", "bwong", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("MANAGE_WATCHERS", result.error()["hint"])

    def test_an_unknown_user_a_bad_name_and_an_unknown_issue(self):
        self.assertEqual(self.run_cli("watch", "PROJ-2", "--user", "nobody").code, 4)
        self.assertIn("login name", self.run_cli("watch", "PROJ-2", "--user", "nobody").err)
        self.assertEqual(self.run_cli("watch", "PROJ-2", "--user", " ").code, 0)  # blank means: you
        self.assertEqual(self.run_cli("watch", "PROJ-999", "--dry-run").code, 4)

    def test_it_is_a_write_and_has_a_dry_run(self):
        command = find_command("watch")
        self.assertTrue(command.writes and any("--dry-run" in arg.names for arg in command.args))
        self.assertEqual(command.group, "Work on an issue")
        self.assertEqual(find_command("watchers").group, "Read")


class VotesTest(CommandCase):
    def test_the_count_and_whether_you_voted(self):
        self.assertEqual(self.ok("votes", "PROJ-1").out, "PROJ-1: 1 vote, you have not voted\n")
        self.assertEqual(self.ok("votes", "proj-1", "--json").json(), {"key": "PROJ-1", "count": 1, "hasVoted": False})
        self.assertEqual(self.run_cli("votes", "PROJ-999").code, 4)

    def test_nobody_else_is_ever_named(self):
        result = self.ok("votes", "PROJ-1", "--json")
        self.assertNotIn("asmith", result.out + result.err)
        self.assertEqual(self.sent("get_votes"), [("get_votes", "PROJ-1")])

    def test_you_vote_and_withdraw(self):
        self.assertEqual(self.ok("vote", "PROJ-2", "--dry-run").out, "Dry run: you would vote for PROJ-2 (POST /issue/PROJ-2/votes, no body); nothing was sent\n")
        self.assertEqual(self.sent("add_vote"), [])
        self.assertEqual(self.ok("vote", "PROJ-2").out, "Voted for PROJ-2 (1 vote now).\n")
        self.assertEqual(self.ok("vote", "PROJ-2").out, "You have voted for PROJ-2 already; nothing was sent.\n")
        self.assertEqual(self.ok("vote", "PROJ-2", "--remove", "--dry-run").out, "Dry run: you would withdraw your vote for PROJ-2 (DELETE /issue/PROJ-2/votes, no body); nothing was sent\n")
        self.assertEqual(self.ok("vote", "PROJ-2", "--remove").out, "Withdrew your vote for PROJ-2 (0 votes now).\n")
        self.assertEqual(self.ok("vote", "PROJ-2", "--remove").out, "You have no vote for PROJ-2 to withdraw; nothing was sent.\n")

    def test_the_json_of_a_vote(self):
        data = self.ok("vote", "PROJ-2", "--json").json()
        self.assertEqual(data, {"key": "PROJ-2", "action": "added", "dryRun": False, "changed": True, "count": 1, "hasVoted": True, "message": ""})
        self.assertEqual(self.ok("vote", "PROJ-2", "--dry-run", "--json").json()["changed"], False)

    def test_jiras_refusal_is_shown_in_its_own_words_with_exit_code_6(self):
        self.gateway.collab_vote_refusal = "You cannot vote for an issue you have reported."
        result = self.run_cli("vote", "PROJ-2", "--json")
        self.assertEqual((result.code, result.error()["code"]), (6, "REJECTED"))
        self.assertIn("You cannot vote for an issue you have reported.", result.error()["message"])
        self.assertIn("jira server", result.error()["hint"])


class WorklogsTest(CommandCase):
    def test_a_table_newest_first_with_the_total(self):
        lines = self.ok("worklogs", "PROJ-1").out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "STARTED", "TIME", "COMMENT"])
        self.assertEqual(lines[1].split(), ["10101", "2026-10-18T13:00:00.000+0000", "2h"])
        self.assertIn("Reviewed with [~user] ([email])", lines[2])
        self.assertEqual(lines[3], "Total: 2 of 2 worklogs, 3h 30m (12600 s).")

    def test_the_json_shape(self):
        data = self.ok("worklogs", "PROJ-1", "--json").json()
        self.assertEqual((data["key"], data["total"], data["count"], data["timeSpentSeconds"], data["timeSpent"]), ("PROJ-1", 2, 2, 12600, "3h 30m"))
        self.assertEqual(
            data["worklogs"][1],
            {"id": "10100", "started": "2026-10-14T09:00:00.000+0000", "timeSpent": "1h 30m", "timeSpentSeconds": 5400, "comment": "Reviewed with [~user] ([email])",
             "author": None, "created": "2026-10-14T10:40:00.000+0000", "updated": "", "visibility": ""},
        )

    def test_authors_only_with_with_users_and_never_under_the_policy(self):
        self.assertEqual(self.ok("worklogs", "PROJ-1", "--with-users").out.splitlines()[0].split()[-1], "AUTHOR")
        self.assertEqual(self.ok("worklogs", "PROJ-1", "--with-users", "--json").json()["worklogs"][0]["author"]["name"], "jdoe")
        refused = self.run_cli("worklogs", "PROJ-1", "--with-users", settings=POLICY)
        self.assertEqual(refused.code, 1)
        shown = self.ok("worklogs", "PROJ-1", "--json", settings=POLICY)
        for personal in ("jdoe", "asmith", "Alex", "alex.smith"):
            self.assertNotIn(personal, shown.out + shown.err)
        plain = self.ok("worklogs", "PROJ-1")
        for personal in ("jdoe", "asmith", "Alex", "alex.smith"):
            self.assertNotIn(personal, plain.out)

    def test_since_and_limit(self):
        self.assertEqual([row["id"] for row in self.ok("worklogs", "PROJ-1", "--since", "3d", "--json").json()["worklogs"]], ["10101"])
        self.assertEqual(self.ok("worklogs", "PROJ-1", "--since", "3d").out.splitlines()[-1], "Total: 1 of 2 worklogs, 2h (7200 s).")
        self.assertEqual(self.ok("worklogs", "PROJ-1", "--since", "1h").out, "No worklogs on PROJ-1 in that period.\n")
        self.assertEqual([row["id"] for row in self.ok("worklogs", "PROJ-1", "--limit", "1", "--json").json()["worklogs"]], ["10101"])
        self.assertEqual(self.ok("worklogs", "PROJ-2").out, "No worklogs on PROJ-2.\n")

    def test_a_bad_since_and_a_negative_limit(self):
        bad = self.run_cli("worklogs", "PROJ-1", "--since", "yesterday", "--json")
        self.assertEqual((bad.code, bad.error()["code"]), (1, "INVALID"))
        self.assertIn("2d", bad.error()["hint"])
        self.assertEqual(self.run_cli("worklogs", "PROJ-1", "--limit", "-1").code, 2)
        self.assertEqual(self.run_cli("worklogs", "PROJ-999").code, 4)

    def test_select_and_format_work_like_in_every_list(self):
        self.assertEqual(self.ok("worklogs", "PROJ-1", "--json", "--select", "id,timeSpent").json()["worklogs"], [{"id": "10101", "timeSpent": "2h"}, {"id": "10100", "timeSpent": "1h 30m"}])
        tsv = self.ok("worklogs", "PROJ-1", "--format", "tsv", "--select", "id,timeSpentSeconds").out.splitlines()
        self.assertEqual(tsv, ["id\ttimeSpentSeconds", "10101\t7200", "10100\t5400"])
        self.assertEqual(self.ok("worklogs", "PROJ-1", "--format", "md").out.splitlines()[0], "| ID | STARTED | TIME SPENT | SECONDS | COMMENT |")

    def test_the_visibility_of_a_restricted_entry_is_in_the_json(self):
        self.gateway.collab_worklogs["PROJ-2"] = [Worklog("1", "2026-10-19T08:00:00.000+0000", "1h", 3600, visibility="role:Developers")]
        self.assertEqual(self.ok("worklogs", "PROJ-2", "--json").json()["worklogs"][0]["visibility"], "role:Developers")


class LogWorkTest(CommandCase):
    def test_a_dry_run_shows_exactly_what_is_sent_and_sends_nothing(self):
        result = self.ok("log-work", "PROJ-2", "1h 30m", "--comment", "Did it", "--adjust-estimate", "new", "--new-estimate", "2d", "--dry-run")
        self.assertIn("Dry run: PROJ-2 exists; would log 1h 30m (5400 s); nothing was sent", result.out)
        self.assertIn('"timeSpent": "1h 30m"', result.out)
        self.assertIn('Query: {"adjustEstimate": "new", "newEstimate": "2d"}', result.out)
        self.assertEqual(self.sent("add_worklog"), [])
        data = self.ok("log-work", "PROJ-2", "2d", "--dry-run", "--json").json()
        self.assertEqual(
            data,
            {"key": "PROJ-2", "payload": {"timeSpent": "2d"}, "query": {}, "seconds": None, "dryRun": True, "worklog": None, "confirmed": None, "message": ""},
        )

    def test_a_real_run_logs_and_reads_the_entry_back(self):
        self.assertEqual(self.ok("log-work", "PROJ-2", "1h 30m", "--started", "2026-10-15T08:30:00+02:00").out, "Logged 1h 30m on PROJ-2 (worklog 10201, 5400 s)\n")
        data = self.ok("log-work", "PROJ-2", "30m", "--comment", "More", "--json").json()
        self.assertEqual(data["payload"], {"timeSpent": "30m", "comment": "More"})
        self.assertEqual((data["confirmed"], data["dryRun"], data["seconds"]), (True, False, 1800))
        self.assertEqual((data["worklog"]["timeSpent"], data["worklog"]["timeSpentSeconds"], data["worklog"]["author"]), ("30m", 1800, None))
        request = self.sent("add_worklog")[0][2]
        self.assertEqual(request.body["started"], "2026-10-15T08:30:00.000+0200")

    def test_markdown_in_the_comment_is_converted_on_request(self):
        self.ok("log-work", "PROJ-2", "1h", "--comment", "Fixed **the** export", "--wiki")
        self.assertEqual(self.sent("add_worklog")[0][2].body["comment"], "Fixed *the* export")
        self.ok("log-work", "PROJ-2", "1h", "--comment", "Fixed **the** export")
        self.assertEqual(self.sent("add_worklog")[1][2].body["comment"], "Fixed **the** export")

    def test_a_time_jira_would_read_differently_is_refused_with_what_to_write(self):
        for text in ("1.5h", "30", "0m", "tomorrow"):
            with self.subTest(text=text):
                result = self.run_cli("log-work", "PROJ-2", text, "--json")
                self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
                self.assertIn("1h 30m", result.error()["hint"])
        self.assertEqual(self.gateway.calls, [])

    def test_options_that_do_not_belong_together(self):
        self.assertEqual(self.run_cli("log-work", "PROJ-2", "1h", "--adjust-estimate", "new").code, 1)
        self.assertEqual(self.run_cli("log-work", "PROJ-2", "1h", "--reduce-by", "1h").code, 1)
        self.assertEqual(self.run_cli("log-work", "PROJ-2", "1h", "--adjust-estimate", "sometimes").code, 2)
        self.assertEqual(self.run_cli("log-work", "PROJ-2").code, 2)
        self.assertEqual(self.sent("add_worklog"), [])

    def test_a_guard_against_a_change_in_between(self):
        result = self.run_cli("log-work", "PROJ-2", "1h", "--if-unchanged-since", "2026-10-01T00:00:00Z")
        self.assertEqual(result.code, 3)
        self.assertEqual(self.sent("add_worklog"), [])
        self.assertEqual(self.run_cli("log-work", "PROJ-2", "1h", "--if-unchanged-since", "2026-10-20T00:00:00Z").code, 0)

    def test_the_refusals_of_jira_keep_their_exit_codes_and_hints(self):
        self.gateway.errors["add_worklog"] = AuthError("Jira refused this request (HTTP 403).")
        result = self.run_cli("log-work", "PROJ-2", "1h", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("WORK_ON_ISSUES", result.error()["hint"])
        self.assertEqual(self.run_cli("log-work", "PROJ-999", "1h", "--dry-run").code, 4)

    def test_it_is_a_write_and_has_a_dry_run_and_a_guard(self):
        command = find_command("log-work")
        names = {name for arg in command.args for name in arg.names}
        self.assertTrue(command.writes)
        self.assertLessEqual({"--dry-run", "--if-unchanged-since", "--wiki", "--started", "--adjust-estimate", "--new-estimate", "--reduce-by", "--comment"}, names)


class RemoteLinksTest(CommandCase):
    def test_the_links_as_a_table_and_as_json(self):
        lines = self.ok("remote-links", "PROJ-1").out.splitlines()
        self.assertEqual(lines[0].split(), ["ID", "TITLE", "URL", "RELATIONSHIP", "GLOBAL", "ID"])
        self.assertEqual(lines[1].split(), ["10000", "Runbook", "https://wiki.example.com/runbook", "documentation", "wiki:runbook"])
        data = self.ok("remote-links", "PROJ-1", "--json").json()
        self.assertEqual(data, [{"id": "10000", "title": "Runbook", "url": "https://wiki.example.com/runbook", "relationship": "documentation", "application": "", "globalId": "wiki:runbook"}])

    def test_no_links_and_an_unknown_issue(self):
        self.assertEqual(self.ok("remote-links", "PROJ-2").out, "No web links on PROJ-2.\n")
        self.assertEqual(self.ok("remote-links", "PROJ-2", "--json").json(), [])
        self.assertEqual(self.run_cli("remote-links", "PROJ-999").code, 4)

    def test_personal_data_in_a_link_is_redacted(self):
        self.gateway.collab_remote_links["PROJ-2"] = []
        self.ok("remote-link", "PROJ-2", "--url", "https://example.com/a", "--title", "Ask jane.doe@example.com")
        self.assertEqual(self.ok("remote-links", "PROJ-2", "--json").json()[0]["title"], "Ask [email]")

    def test_select_and_format(self):
        self.assertEqual(self.ok("remote-links", "PROJ-1", "--json", "--select", "title,url").json(), [{"title": "Runbook", "url": "https://wiki.example.com/runbook"}])
        self.assertEqual(self.ok("remote-links", "PROJ-1", "--format", "tsv", "--select", "id,title").out.splitlines(), ["id\ttitle", "10000\tRunbook"])


class RemoteLinkTest(CommandCase):
    def test_a_dry_run_shows_the_payload_and_sends_nothing(self):
        result = self.ok("remote-link", "PROJ-2", "--url", "https://example.com/pr/7", "--title", "Pull request 7", "--dry-run")
        self.assertIn("Dry run: PROJ-2 exists; would add a web link; nothing was sent", result.out)
        self.assertIn('"url": "https://example.com/pr/7"', result.out)
        self.assertEqual(self.sent("save_remote_link"), [])
        data = self.ok("remote-link", "PROJ-1", "--url", "https://example.com/a", "--title", "A", "--global-id", "wiki:runbook", "--dry-run", "--json").json()
        self.assertEqual((data["action"], data["dryRun"], data["link"], data["payload"]["globalId"]), ("updated", True, None, "wiki:runbook"))
        self.assertIn("would update the web link with global id wiki:runbook", self.ok("remote-link", "PROJ-1", "--url", "https://example.com/a", "--title", "A", "--global-id", "wiki:runbook", "--dry-run").out)

    def test_a_link_is_added_and_read_back(self):
        out = self.ok("remote-link", "PROJ-2", "--url", "https://example.com/pr/7", "--title", "Pull request 7", "--relationship", "implements").out
        self.assertEqual(out, "Added web link 10201 on PROJ-2: Pull request 7 (https://example.com/pr/7)\n")
        data = self.ok("remote-link", "PROJ-2", "--url", "https://example.com/pr/8", "--title", "Pull request 8", "--json").json()
        self.assertEqual(data["link"]["title"], "Pull request 8")
        self.assertEqual((data["action"], data["confirmed"], data["dryRun"]), ("created", True, False))
        self.assertEqual(set(data["link"]), {"id", "title", "url", "relationship", "application", "globalId"})

    def test_the_same_global_id_updates_instead_of_adding_twice(self):
        out = self.ok("remote-link", "PROJ-1", "--url", "https://wiki.example.com/runbook/v2", "--title", "Runbook v2", "--global-id", "wiki:runbook").out
        self.assertEqual(out, "Updated web link 10000 on PROJ-1: Runbook v2 (https://wiki.example.com/runbook/v2)\n")
        self.assertEqual(len(self.ok("remote-links", "PROJ-1", "--json").json()), 1)

    def test_a_second_link_to_an_address_that_is_there_warns_on_standard_error(self):
        result = self.ok("remote-link", "PROJ-1", "--url", "https://wiki.example.com/runbook", "--title", "Again")
        self.assertIn("web link to this address already", result.err)
        self.assertNotIn("already", result.out)

    def test_what_is_no_web_address_is_refused_before_anything_is_asked(self):
        for url in ("javascript:alert(1)", "https://user:secret@example.com/", "ftp://example.com/x"):
            with self.subTest(url=url):
                result = self.run_cli("remote-link", "PROJ-2", "--url", url, "--title", "T", "--json")
                self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))
                self.assertNotIn("secret", result.err)
        self.assertEqual(self.gateway.calls, [])

    def test_the_options_that_are_needed(self):
        self.assertEqual(self.run_cli("remote-link", "PROJ-2", "--title", "T").code, 2)
        self.assertEqual(self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/").code, 2)
        self.assertEqual(self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/", "--title", " ").code, 1)

    def test_a_refusal_says_which_permission_is_missing(self):
        self.gateway.errors["save_remote_link"] = AuthError("Jira refused this request (HTTP 403).")
        result = self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/", "--title", "T", "--json")
        self.assertEqual((result.code, result.error()["code"]), (5, "AUTH"))
        self.assertIn("LINK_ISSUES", result.error()["hint"])

    def test_a_guard_against_a_change_in_between(self):
        result = self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/", "--title", "T", "--if-unchanged-since", "2026-10-01T00:00:00Z")
        self.assertEqual(result.code, 3)
        self.assertEqual(self.sent("save_remote_link"), [])


class ReadBackFailureTest(CommandCase):
    """The write was made and the read after it failed: the command says so, exits 0 and tells the reader not to repeat it (the
    worklog of a repeat would count twice and cannot be deleted with this CLI)."""

    def call(self, tool, key, **arguments):
        """One call of `jira mcp --allow-write`: its reply, as the model gets it. The property that holds the issue key is asked of the
        description of the tool, so that the name of it does not matter here."""
        listed = mcp_tools([command for command in all_commands() if command.name != "mcp"], __version__, only="write")["tools"]
        key_property = next(item for item in listed if item["name"] == tool)["inputSchema"]["required"][0]
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {key_property: key, **arguments}}}
        result = self.run_cli("mcp", "--allow-write", stdin=json.dumps(request) + "\n")
        self.assertEqual(result.code, 0, result.err)
        return json.loads(result.out.splitlines()[0])["result"]

    def mcp(self, tool, key, **arguments):
        """What a model sees of a successful call is the output of the command alone, because the messages go to the log."""
        reply = self.call(tool, key, **arguments)
        return reply, json.loads(reply["content"][0]["text"])

    def test_log_work_exits_0_and_the_text_says_so_on_standard_error(self):
        self.gateway.errors["list_worklogs"] = UNAVAILABLE
        result = self.run_cli("log-work", "PROJ-2", "1h", "--comment", "Once")
        self.assertEqual((result.code, result.out), (0, "Logged 1h on PROJ-2 (worklog 10201, 3600 s)\n"))
        for words in ("WAS logged", "(worklog 10201)", "HTTP 503", "Do not run the command again", "jira worklogs PROJ-2"):
            self.assertIn(words, result.err)
        self.assertEqual(len(self.sent("add_worklog")), 1)

    def test_log_work_json_has_the_entry_that_jira_answered_and_a_message_that_says_not_to_repeat_it(self):
        self.gateway.errors["list_worklogs"] = UNAVAILABLE
        result = self.run_cli("log-work", "PROJ-2", "1h", "--comment", "Once", "--json")
        self.assertEqual(result.code, 0, result.err)
        data = result.json()
        self.assertEqual(set(data), {"key", "payload", "query", "seconds", "dryRun", "worklog", "confirmed", "message"})
        self.assertEqual((data["confirmed"], data["dryRun"], data["seconds"]), (None, False, 3600))
        self.assertEqual((data["worklog"]["id"], data["worklog"]["timeSpent"], data["worklog"]["author"]), ("10201", "1h", None))
        self.assertIn("WAS logged", data["message"])
        self.assertIn("Do not run the command again", data["message"])
        self.assertEqual(result.err.strip().splitlines()[-1].split(": ", 1)[-1], data["message"])  # the warning is the same text
        self.assertEqual(len(self.sent("add_worklog")), 1)

    def test_remote_link_exits_0_and_says_that_the_link_was_added(self):
        self.fail_the_read_back_of("list_remote_links")
        text = self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/pr/7", "--title", "Pull request 7")
        self.assertEqual((text.code, text.out), (0, "Added web link on PROJ-2: Pull request 7 (https://example.com/pr/7)\n"))
        for words in ("WAS added", "(id 10201)", "Do not run the command again", "second link", "jira remote-links PROJ-2"):
            self.assertIn(words, text.err)
        self.fail_the_read_back_of("list_remote_links")
        data = self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/pr/8", "--title", "Pull request 8", "--json").json()
        self.assertEqual(set(data), {"key", "action", "payload", "dryRun", "link", "confirmed", "message"})
        self.assertEqual((data["action"], data["link"], data["confirmed"], data["dryRun"]), ("created", None, None, False))
        self.assertIn("WAS added", data["message"])
        self.assertEqual(len(self.sent("save_remote_link")), 2)  # one per command

    def test_watch_exits_0_and_says_that_the_change_was_made(self):
        self.gateway.errors["get_watchers"] = UNAVAILABLE
        text = self.run_cli("watch", "PROJ-2")
        self.assertEqual((text.code, text.out), (0, "Added you to the watchers of PROJ-2.\n"))
        self.assertIn("the watcher was added, but reading the watcher list afterwards failed", text.err)
        data = self.run_cli("watch", "PROJ-2", "--remove", "--json").json()
        self.assertEqual((data["watchCount"], data["watching"], data["confirmed"], data["action"]), (None, None, None, "removed"))
        self.assertIn("jira watchers PROJ-2", data["message"])
        self.assertEqual((len(self.sent("add_watcher")), len(self.sent("remove_watcher"))), (1, 1))

    def test_vote_exits_0_and_says_that_the_vote_was_made(self):
        self.fail_the_read_back_of("get_votes")
        text = self.run_cli("vote", "PROJ-2")
        self.assertEqual((text.code, text.out), (0, "Voted for PROJ-2.\n"))
        self.assertIn("your vote was added, but reading the votes afterwards failed", text.err)
        self.fail_the_read_back_of("get_votes")
        data = self.run_cli("vote", "PROJ-2", "--remove", "--json").json()
        self.assertEqual((data["count"], data["hasVoted"], data["changed"], data["action"]), (None, None, True, "removed"))
        self.assertIn("jira votes PROJ-2", data["message"])

    def test_over_mcp_the_result_itself_says_that_the_work_was_logged(self):
        self.gateway.errors["list_worklogs"] = UNAVAILABLE
        reply, data = self.mcp("jira_log_work", "PROJ-2", time="1h", comment="Once")
        self.assertFalse(reply["isError"])
        self.assertEqual((data["confirmed"], data["worklog"]["id"], data["dryRun"]), (None, "10201", False))
        for words in ("WAS logged", "Do not run the command again", "jira worklogs PROJ-2"):
            self.assertIn(words, data["message"])
        self.assertEqual(len(self.sent("add_worklog")), 1)

    def test_over_mcp_the_other_writes_say_so_in_their_result_too(self):
        self.fail_the_read_back_of("list_remote_links")
        reply, link = self.mcp("jira_remote_link", "PROJ-2", url="https://example.com/pr/7", title="Pull request 7")
        self.assertFalse(reply["isError"])
        self.assertEqual((link["link"], link["confirmed"]), (None, None))
        self.assertIn("WAS added", link["message"])
        self.gateway.errors["get_watchers"] = UNAVAILABLE
        reply, watch = self.mcp("jira_watch", "PROJ-2")
        self.assertFalse(reply["isError"])
        self.assertEqual((watch["watching"], watch["confirmed"]), (None, None))
        self.assertIn("the watcher was added", watch["message"])
        self.fail_the_read_back_of("get_votes")
        reply, vote = self.mcp("jira_vote", "PROJ-2")
        self.assertFalse(reply["isError"])
        self.assertEqual((vote["count"], vote["hasVoted"], vote["changed"]), (None, None, True))
        self.assertIn("your vote was added", vote["message"])
        self.assertEqual([len(self.sent(name)) for name in ("save_remote_link", "add_watcher", "add_vote")], [1, 1, 1])

    def test_over_mcp_removing_another_person_is_a_refusal_that_sends_nothing(self):
        reply = self.call("jira_watch", "PROJ-1", user="bwong", remove=True)
        self.assertTrue(reply["isError"])
        error = json.loads(reply["content"][0]["text"])["error"]
        self.assertEqual((error["code"], error["exitCode"]), ("USAGE", 2))
        self.assertIn("Manage Watchers", error["hint"])
        self.assertEqual(self.sent("remove_watcher"), [])

    def test_what_goes_wrong_before_the_write_is_still_an_error_and_sends_nothing(self):
        self.gateway.errors["get_votes"] = UNAVAILABLE
        vote = self.run_cli("vote", "PROJ-2", "--json")
        self.assertEqual((vote.code, vote.error()["code"]), (7, "UNAVAILABLE"))
        self.gateway.errors["list_remote_links"] = UNAVAILABLE
        link = self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/pr/7", "--title", "Pull request 7", "--json")
        self.assertEqual((link.code, link.error()["code"]), (7, "UNAVAILABLE"))
        self.assertEqual([len(self.sent(name)) for name in ("add_vote", "save_remote_link")], [0, 0])

    def test_a_write_that_fails_is_a_failure_with_its_exit_code(self):
        self.gateway.errors["add_worklog"] = UNAVAILABLE
        result = self.run_cli("log-work", "PROJ-2", "1h", "--json")
        self.assertEqual((result.code, result.error()["code"]), (7, "UNAVAILABLE"))
        self.assertNotIn("WAS logged", result.err)

    def test_a_read_back_that_works_has_an_empty_message_and_no_warning(self):
        logged = self.run_cli("log-work", "PROJ-2", "1h", "--json")
        link = self.run_cli("remote-link", "PROJ-2", "--url", "https://example.com/pr/7", "--title", "Pull request 7", "--json")
        self.assertEqual((logged.json()["message"], link.json()["message"], logged.err, link.err), ("", "", "", ""))


class EveryCommandTest(unittest.TestCase):
    def test_writes_have_a_dry_run_and_reads_have_none(self):
        writes = {"watch", "vote", "log-work", "remote-link"}
        reads = {"watchers", "votes", "worklogs", "remote-links"}
        for name in writes | reads:
            command = find_command(name)
            with self.subTest(command=name):
                self.assertEqual(command.writes, name in writes)
                self.assertEqual(any("--dry-run" in arg.names for arg in command.args), name in writes)
                self.assertTrue(command.json_output and command.output and command.when)
                self.assertEqual(command.group, "Work on an issue" if name in writes else "Read")


if __name__ == "__main__":
    unittest.main()
