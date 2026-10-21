"""What every command prints and does, run through the command line with fakes behind it."""
import dataclasses
import json
import unittest

from jira_dc_cli.domain.errors import UnavailableError
from tests.support.cli import run_cli
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, MemoryConfigStore, MemoryFiles


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class ReadCommandsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()

    def run_cli(self, *argv, **options):
        return run_cli(*argv, gateway=self.gateway, **options)

    def test_issue_text(self):
        result = self.run_cli("issue", "proj-1")
        self.assertEqual(result.code, 0, result.err)
        lines = result.out.splitlines()
        self.assertEqual(lines[0], "PROJ-1  [Story | To Do | High]")
        self.assertEqual(lines[1], "Export the report")
        for expected in ("Labels:", "Components:", "Fix versions:", "Epic:         PROJ-9", "Story points: 5", "Sprint:       Demo Sprint 3 (ACTIVE)", "Description:", "Acceptance criteria:", "Comments: 1 (show with --comments)"):
            self.assertIn(expected, result.out)
        for personal in ("jane.doe", "Jane Doe", "[~jdoe]"):
            self.assertNotIn(personal, result.out)

    def test_issue_with_comments_and_users(self):
        result = self.run_cli("issue", "PROJ-1", "--comments", "--with-users")
        self.assertIn("Assignee:", result.out)
        self.assertIn("Jane Doe", result.out)
        self.assertIn("--- #10 2026-10-02T10:00:00.000+0200 Jane Doe", result.out)
        self.assertNotIn("show with --comments", result.out)

    def test_issue_json_is_one_compact_line_with_camel_case_keys(self):
        result = self.run_cli("issue", "PROJ-1", "--json")
        self.assertEqual(len(result.out.strip().splitlines()), 1)
        data = result.json()
        self.assertEqual((data["key"], data["issueType"], data["status"], data["storyPoints"], data["epicKey"]), ("PROJ-1", "Story", {"name": "To Do", "category": "new"}, 5.0, "PROJ-9"))
        self.assertIsNone(data["assignee"])
        self.assertNotIn("raw", data)
        self.assertNotIn("jane.doe", result.out)

    def test_issue_raw(self):
        data = self.run_cli("issue", "PROJ-1", "--raw").json()
        self.assertEqual(data["fields"]["assignee"], {"hidden": True})

    def test_the_privacy_policy_refuses_users(self):
        result = self.run_cli("issue", "PROJ-1", "--with-users", "--json", settings=settings(hide_users=True))
        self.assertEqual((result.code, result.error()["code"]), (1, "INVALID"))

    def test_json_by_default_from_the_environment(self):
        result = self.run_cli("issue", "PROJ-1", environ={"JIRA_OUTPUT": "json"})
        self.assertEqual(result.json()["key"], "PROJ-1")
        self.assertEqual(self.run_cli("issue", "PROJ-1", settings=settings(output="json")).json()["key"], "PROJ-1")

    def test_issues_table(self):
        result = self.run_cli("issues", "--limit", "5")
        lines = result.out.splitlines()
        self.assertEqual(lines[0].split(), ["KEY", "TYPE", "STATUS", "ASSIGNEE", "SUMMARY"])
        self.assertTrue(lines[1].startswith("PROJ-1"))
        self.assertIn("Jane Doe", lines[1])
        self.assertIn("Showing 2 of 6", result.err)

    def test_issues_without_assignee(self):
        result = self.run_cli("issues", "--no-assignee")
        self.assertEqual(result.out.splitlines()[0].split(), ["KEY", "TYPE", "STATUS", "SUMMARY"])
        self.assertNotIn("Jane", result.out)

    def test_issues_all_json(self):
        result = self.run_cli("issues", "--all", "--json", "--fields", "labels,customfield_10004")
        data = result.json()
        self.assertEqual((data["total"], data["count"]), (6, 6))
        first = data["issues"][0]
        self.assertEqual(first["key"], "PROJ-1")
        self.assertEqual(first["issueType"], "Story")
        self.assertEqual(first["assignee"]["name"], "jdoe")
        self.assertEqual(first["labels"], ["backend"])
        self.assertEqual(result.err, "")

    def test_issues_json_leaves_the_assignee_out_when_hidden(self):
        data = self.run_cli("issues", "--json", "--no-assignee").json()
        self.assertNotIn("assignee", data["issues"][0])

    def test_the_privacy_policy_covers_everything_a_search_can_print(self):
        self.gateway.issues["PROJ-1"] = dataclasses.replace(self.gateway.issues["PROJ-1"], summary="Ask [~jdoe] (jane.doe@example.com)", extra={"customfield_9": {"displayName": "Jane", "name": "jdoe"}})
        shown = self.run_cli("issues", "--json", "--fields", "reporter,customfield_9", "--all").json()["issues"]
        first = next(row for row in shown if row["key"] == "PROJ-1")
        self.assertEqual(first["summary"], "Ask [~user] ([email])")  # mentions are redacted even when people are shown
        self.assertEqual(first["assignee"]["name"], "jdoe")
        self.assertEqual(first["reporter"]["name"], "jdoe")
        for argv, options in ((("--no-assignee",), {}), ((), {"settings": settings(hide_users=True)})):
            with self.subTest(argv=argv):
                result = self.run_cli("issues", "--json", "--fields", "reporter,customfield_9", "--all", *argv, **options)
                text = result.out.lower()
                for personal in ("jane", "jdoe", "example.com"):
                    self.assertNotIn(personal, text)
                row = next(row for row in result.json()["issues"] if row["key"] == "PROJ-1")
                self.assertNotIn("assignee", row)
                self.assertIsNone(row["reporter"])
                self.assertEqual(row["customfield_9"], {"hidden": True})

    def test_issues_raw(self):
        data = self.run_cli("issues", "--raw", "--limit", "1").json()
        self.assertEqual((data["total"], data["issues"][0]["key"]), (6, "PROJ-1"))

    def test_issues_option_checks(self):
        for argv in (("--limit", "0"), ("--start-at", "-1"), ("--fields", "a;b")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli("issues", *argv).code, 2)

    def test_issues_needs_a_project_or_query(self):
        result = self.run_cli("issues", settings=settings(project=""))
        self.assertEqual(result.code, 2)
        self.assertIn("jira use", result.err)

    def test_report_text(self):
        result = self.run_cli("report", "--epics")
        self.assertIn("Scope: project PROJ", result.out)
        self.assertIn("Issues (without epics): 4 | done 1 (25%)", result.out)
        self.assertIn("Story points: 16 | done 8 (50%) | open 8", result.out)
        self.assertIn("Issues without story points: 1", result.out)
        self.assertIn("STATUS", result.out)
        self.assertIn("EPIC", result.out)
        epic_row = next(line for line in result.out.splitlines() if line.startswith("PROJ-9"))
        self.assertEqual(epic_row.split(), ["PROJ-9", "Epic", "nine", "0/2", "0/8"])

    def test_report_json(self):
        data = self.run_cli("report", "--sprint", "active", "--json").json()
        self.assertEqual((data["issues"], data["done"], data["points"], data["donePoints"], data["withoutPoints"]), (4, 1, 16.0, 8.0, 1))
        self.assertEqual(data["byStatus"]["To Do"], {"category": "new", "issues": 2, "points": 5.0})
        self.assertEqual(list(data["byStatus"]), ["To Do", "In Progress", "Done"])
        self.assertEqual(data["byEpic"], {})
        epics = self.run_cli("report", "--epics", "--json").json()["byEpic"]
        self.assertEqual(epics["PROJ-9"], {"summary": "Epic nine", "issues": 2, "done": 0, "points": 8.0, "donePoints": 0.0})

    def test_branch(self):
        self.assertEqual(self.run_cli("branch", "PROJ-2").out, "bugfix/PROJ-2-fix-the-thing\n")
        self.assertEqual(self.run_cli("branch", "PROJ-1", "--type", "chore", "--json").json(), {"key": "PROJ-1", "branch": "chore/PROJ-1-export-the-report"})

    def test_export_to_a_file_and_to_stdout(self):
        files = MemoryFiles()
        result = self.run_cli("export", "--out", "tmp/dump.json", files=files)
        self.assertEqual(result.out, "6 issues written to tmp/dump.json\n")
        dump = json.loads(files.files["tmp/dump.json"])
        self.assertEqual(sorted(dump), ["fetchedAt", "fieldIds", "issues"])
        self.assertEqual(files.files["tmp/dump.json"][-1], "\n")
        self.assertEqual(self.run_cli("export").json()["fieldIds"]["epicLink"], "customfield_10002")
        info = self.run_cli("export", "--out", "x.json", "--json", files=files).json()
        self.assertEqual((info["file"], info["issues"]), ("x.json", 6))

    def test_fields(self):
        result = self.run_cli("fields", "--search", "epic")
        self.assertEqual(result.out.splitlines()[1].split(), ["customfield_10002", "Epic", "Link", "yes"])
        self.assertEqual([item["id"] for item in self.run_cli("fields", "--custom", "--json").json()][:1], ["customfield_10004"])


class WorkCommandsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles({"note.md": "# Note\n\n- one\n", "comment.txt": "From a file\n", "fields.json": '{"customfield_10001": 8}'})

    def run_cli(self, *argv, **options):
        options.setdefault("files", self.files)
        return run_cli(*argv, gateway=self.gateway, **options)

    def test_transition_lists_the_options(self):
        result = self.run_cli("transition", "PROJ-2")
        self.assertEqual(result.out.splitlines()[0], "PROJ-2 is in status: In Progress")
        self.assertEqual(result.out.splitlines()[2].split(), ["ID", "TRANSITION", "TO", "STATUS"])
        self.assertIn("81", result.out)
        data = self.run_cli("transition", "PROJ-2", "--json").json()
        self.assertEqual((data["currentStatus"], data["options"][1]["toStatus"]), ("In Progress", {"name": "Done", "category": "done"}))

    def test_transition_moves_and_comments(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--comment-file", "note.md", "--wiki")
        self.assertEqual(result.out, "Moved PROJ-2 from 'In Progress' to 'Done' via transition 'Finish'\n")
        self.assertEqual(
            self.gateway.called("transition_issue")[0][1:],
            ("PROJ-2", {"transition": {"id": "81"}, "update": {"comment": [{"add": {"body": "h1. Note\n\n* one"}}]}}),
        )

    def test_transition_dry_run(self):
        result = self.run_cli("transition", "PROJ-2", "--to", "Done", "--dry-run")
        self.assertIn("Dry run: PROJ-2 would move from 'In Progress' to 'Done' via transition 'Finish' (81); nothing was sent", result.out)
        self.assertIn('"transition"', result.out)
        self.assertEqual(self.gateway.called("transition_issue"), [])

    def test_transition_errors(self):
        self.assertEqual(self.run_cli("transition", "PROJ-2", "--to", "Archived").code, 1)
        self.assertEqual(self.run_cli("transition", "PROJ-2", "--comment", "x").code, 1)
        self.assertEqual(self.run_cli("transition", "PROJ-2", "--comment", "x", "--comment-file", "comment.txt", "--to", "Done").code, 2)

    def test_comment_add_and_list(self):
        self.assertEqual(self.run_cli("comment", "PROJ-1", "--text", "Hello").out, "Commented on PROJ-1 (comment 11)\n")
        listed = self.run_cli("comment", "PROJ-1", "--list")
        self.assertIn("--- #10 2026-10-02T10:00:00.000+0200", listed.out)
        self.assertNotIn("Jane", listed.out)
        self.assertIn("Jane Doe", self.run_cli("comment", "PROJ-1", "--list", "--with-users").out)
        data = self.run_cli("comment", "PROJ-1", "--list", "--json").json()
        self.assertEqual((data["key"], data["total"], data["comments"][0]["author"]), ("PROJ-1", 2, None))

    def test_comment_from_a_file_and_stdin(self):
        self.run_cli("comment", "PROJ-1", "--file", "note.md", "--wiki")
        self.run_cli("comment", "PROJ-1", "--file", "-", stdin="From stdin\n")
        bodies = [call[2]["body"] for call in self.gateway.called("add_comment")]
        self.assertEqual(bodies, ["h1. Note\n\n* one", "From stdin"])

    def test_comment_dry_run(self):
        result = self.run_cli("comment", "PROJ-1", "--text", "Hi", "--dry-run")
        self.assertIn("Dry run: PROJ-1 exists; nothing was sent", result.out)
        self.assertEqual(self.gateway.called("add_comment"), [])

    def test_comment_usage(self):
        self.assertEqual(self.run_cli("comment", "PROJ-1").code, 2)
        self.assertEqual(self.run_cli("comment", "PROJ-1", "--list", "--text", "x").code, 2)
        self.assertEqual(self.run_cli("comment", "PROJ-1", "--text", "x", "--file", "comment.txt").code, 2)

    def test_link(self):
        self.assertEqual(self.run_cli("link", "PROJ-1", "--type", "Blocks", "--to", "PROJ-2").out, "Linked: PROJ-1 blocks PROJ-2\n")
        result = self.run_cli("link", "PROJ-3", "--type", "blocks", "--to", "PROJ-4", "--inverse", "--json")
        self.assertEqual(result.json()["confirmed"], "PROJ-3 is blocked by PROJ-4")

    def test_link_dry_run_and_errors(self):
        result = self.run_cli("link", "PROJ-1", "--type", "Blocks", "--to", "PROJ-2", "--dry-run")
        self.assertIn("Dry run: would link  PROJ-1 blocks PROJ-2  (type 'Blocks'); nothing was sent", result.out)
        self.assertEqual(self.run_cli("link", "PROJ-1", "--type", "requires", "--to", "PROJ-2").code, 1)
        self.assertEqual(self.run_cli("link", "PROJ-1", "--to", "PROJ-2").code, 2)

    def test_link_types(self):
        out = self.run_cli("link-types").out
        self.assertEqual(out.splitlines()[0].split()[0], "NAME")
        self.assertIn("is blocked by", out)
        self.assertEqual(self.run_cli("link-types", "--json").json()[0]["name"], "Blocks")

    def test_update_options(self):
        result = self.run_cli("update", "PROJ-1", "--summary", "New", "--labels", "a,b", "--description-file", "note.md", "--wiki", "--fields-json", "@fields.json")
        self.assertEqual(result.out, "Updated PROJ-1\n")
        payload = self.gateway.called("update_issue")[0][2]["fields"]
        self.assertEqual((payload["summary"], payload["labels"], payload["description"], payload["customfield_10001"]), ("New", ["a", "b"], "h1. Note\n\n* one", 8))

    def test_update_can_clear_a_list(self):
        self.run_cli("update", "PROJ-1", "--labels", "")
        self.assertEqual(self.gateway.called("update_issue")[0][2], {"fields": {"labels": []}})

    def test_json_that_powershell_5_mangled_says_how_to_pass_it(self):
        """Windows PowerShell 5.1 removes the double quotes inside an argument: `{"a":1}` arrives as `{a:1}`."""
        for argv, expected in (
            (("update", "PROJ-1", "--fields-json", "{customfield_1: 5}"), "'@fields.json'"),
            (("api", "PUT", "issue/PROJ-1", "--data", "{fields: {}}", "--write"), "--data-file"),
        ):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv)
                self.assertEqual(result.code, 1)
                self.assertIn("not valid JSON", result.err)
                self.assertIn(expected, result.err)
                self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_dry_run(self):
        result = self.run_cli("update", "PROJ-1", "--priority", "Low", "--dry-run", "--if-unchanged-since", "2026-10-10T07:00:00Z")
        self.assertIn("Dry run: PROJ-1 exists and is unchanged since 2026-10-10T07:00:00Z; nothing was sent", result.out)
        self.assertIn('"priority"', result.out)
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_refuses_stale_data_with_exit_code_3(self):
        result = self.run_cli("update", "PROJ-1", "--summary", "New", "--if-unchanged-since", "2026-10-01T00:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(result.error()["details"]["key"], "PROJ-1")
        self.assertEqual(self.gateway.called("update_issue"), [])

    def test_update_needs_something_to_change(self):
        self.assertEqual(self.run_cli("update", "PROJ-1").code, 1)

    def test_update_json_from_a_file_and_stdin(self):
        self.files.files["p.json"] = '{"update": {"labels": [{"add": "x"}]}}'
        self.assertEqual(self.run_cli("update-json", "PROJ-1", "p.json").out, "Updated PROJ-1\n")
        self.run_cli("update-json", "PROJ-1", "-", stdin='{"fields": {"summary": "S"}}')
        self.assertEqual([call[2] for call in self.gateway.called("update_issue")], [{"update": {"labels": [{"add": "x"}]}}, {"fields": {"summary": "S"}}])
        self.assertEqual(self.run_cli("update-json", "PROJ-1", "-", stdin="[1]").code, 1)


class CreateCommandsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles()

    def run_cli(self, *argv, **options):
        options.setdefault("files", self.files)
        return run_cli(*argv, gateway=self.gateway, **options)

    def test_create(self):
        result = self.run_cli("create", "--type", "story", "--summary", "A new story", "--labels", "x")
        self.assertEqual(result.out, "Created PROJ-101\nhttps://jira.example.com/browse/PROJ-101\n")
        self.assertEqual(self.gateway.called("create_issue")[0][1]["fields"]["issuetype"], {"id": "10200"})

    def test_create_json_and_dry_run(self):
        data = self.run_cli("create", "--type", "Bug", "--summary", "S", "--json").json()
        self.assertEqual((data["action"], data["key"], data["created"]["key"]), ("created", "PROJ-101", "PROJ-101"))
        dry = self.run_cli("create", "--type-id", "10200", "--summary", "S", "--dry-run")
        self.assertIn("Dry run: nothing was sent", dry.out)
        self.assertEqual(len(self.gateway.called("create_issue")), 1)

    def test_create_usage(self):
        self.assertEqual(self.run_cli("create", "--type", "Story").code, 2)  # no summary
        self.assertEqual(self.run_cli("create", "--summary", "S").code, 1)  # no type
        self.assertEqual(self.run_cli("create", "--summary", "S", "--type", "Story", "--type-id", "10200").code, 2)

    def test_create_json_file(self):
        self.files.files["new.json"] = '{"fields": {"project": {"key": "PROJ"}, "issuetype": {"id": "10200"}, "summary": "S"}}'
        self.assertIn("Created PROJ-101", self.run_cli("create-json", "new.json").out)
        self.assertIn("nothing was sent", self.run_cli("create-json", "new.json", "--dry-run").out)

    def test_import_md(self):
        self.files.files["t.md"] = "---\nsummary: From markdown\nissuetype: Story\n---\nBody\n"
        self.assertEqual(self.run_cli("import-md", "t.md").out, "Created PROJ-101\n")
        dry = self.run_cli("import-md", "t.md", "--dry-run", "--jira-wiki")
        self.assertIn("Dry run: would create an issue; nothing was sent", dry.out)
        self.assertEqual(self.run_cli("import-md", "t.md", "--json").json()["action"], "created")

    def test_import_md_dir(self):
        self.files.files["d/001-a.md"] = "---\nsource_id: A\nsummary: A\nissuetype: Story\n---\nx"
        self.files.files["d/002-b.md"] = "---\nsource_id: B\nsummary: B\n---\nx"  # no issue type
        failed = self.run_cli("import-md-dir", "d")
        self.assertEqual(failed.code, 1)
        self.assertIn("created       PROJ-101 d/001-a.md", failed.out)
        self.assertIn("FAILED        d/002-b.md", failed.out)
        self.assertIn("Done: files=2 created=1 updated=0 skipped=0 failed=1", failed.out)
        again = self.run_cli("import-md-dir", "d", "--continue-on-error", "--json")
        self.assertEqual(again.code, 1)
        data = again.json()
        self.assertEqual((data["skipped"], data["failed"], data["created"]), (1, 1, 0))
        self.assertEqual(data["mapFile"], "d/jira-import-map.csv")

    def test_import_md_dir_dry_run(self):
        self.files.files["d/001-a.md"] = "---\nsummary: A\nissuetype: Story\n---\nx"
        result = self.run_cli("import-md-dir", "d", "--dry-run")
        self.assertEqual(result.code, 0)
        self.assertIn("would create", result.out)
        self.assertTrue(result.out.rstrip().endswith("(dry run, nothing was sent)"))

    def test_import_csv(self):
        self.files.files["r.csv"] = "summary,issuetype\nOne,Story\n,Story\n"
        result = self.run_cli("import-csv", "r.csv", "--continue-on-error")
        self.assertEqual(result.code, 1)
        self.assertIn("line 3", result.out)
        self.assertIn("created=1", result.out)


class ProjectCommandsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()

    def run_cli(self, *argv, **options):
        return run_cli(*argv, gateway=self.gateway, **options)

    def test_projects(self):
        out = self.run_cli("projects").out
        self.assertEqual(out.splitlines()[0].split(), ["KEY", "NAME", "TYPE", "LEAD"])
        self.assertIn("Jane Doe", out)
        hidden = self.run_cli("projects", settings=settings(hide_users=True)).out
        self.assertEqual(hidden.splitlines()[0].split(), ["KEY", "NAME", "TYPE"])
        self.assertNotIn("Jane", hidden)

    def test_projects_admin(self):
        result = self.run_cli("projects-admin")
        self.assertIn("PROJ", result.out)
        self.assertNotIn("DEMO", result.out)
        self.assertIn("Checking project DEMO (1/2)", result.err)
        self.assertIn("Finished: checked=2 admin=1 failed=0", result.err)
        quiet = self.run_cli("projects-admin", "--quiet", "--json")
        self.assertEqual(quiet.err, "")
        self.assertEqual((quiet.json()["checked"], quiet.json()["admin"]), (2, 1))

    def test_projects_admin_option_checks(self):
        for argv in (("--timeout", "0"), ("--timeout", "nan"), ("--timeout", "inf"), ("--retry", "-1")):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli("projects-admin", *argv).code, 2)

    def test_project(self):
        out = self.run_cli("project").out
        self.assertIn("Key:         PROJ", out)
        self.assertIn("Issue types: Epic, Story, Bug", out)
        self.assertIn("Lead:", out)
        self.assertEqual(self.run_cli("project", "demo", "--json").json()["key"], "DEMO")
        self.assertNotIn("Lead:", self.run_cli("project", settings=settings(hide_users=True)).out)
        self.assertEqual(self.run_cli("project", "NOPE").code, 4)

    def test_metadata(self):
        self.assertIn('"values"', self.run_cli("metadata", "PROJ").out)
        self.run_cli("metadata", "PROJ", "10200")
        self.assertEqual(self.gateway.called("create_metadata")[-1][1:], ("PROJ", "10200"))


class SetupCommandsTest(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.store = MemoryConfigStore({"JIRA_PROJECT": "PROJ", "JIRA_PAT": "old-token"})

    def run_cli(self, *argv, **options):
        return run_cli(*argv, gateway=self.gateway, store=self.store, **options)

    def test_setup_pat_from_stdin_never_echoes_the_token(self):
        result = self.run_cli("setup-pat", "--stdin", stdin="  new-token-123 \n")
        self.assertEqual(result.out, "Token saved to memory:/config\n")
        self.assertEqual(self.store.values["JIRA_PAT"], "new-token-123")
        self.assertNotIn("new-token-123", result.out + result.err)

    def test_setup_pat_warns_when_the_input_is_visible(self):
        result = self.run_cli("setup-pat", stdin="piped-token\n")
        self.assertIn("may be visible", result.err)
        self.assertEqual(self.store.values["JIRA_PAT"], "piped-token")

    def test_an_empty_token_is_refused(self):
        self.assertEqual(self.run_cli("setup-pat", "--stdin", stdin="\n").code, 1)
        self.assertEqual(self.store.values["JIRA_PAT"], "old-token")

    def test_remove_pat(self):
        self.assertEqual(self.run_cli("remove-pat").out, "Token removed from memory:/config\n")
        self.assertEqual(self.run_cli("remove-pat").out, "No token was stored.\n")

    def test_use_current_clear(self):
        self.assertEqual(self.run_cli("use", "demo").out, "Current project: DEMO (Project DEMO)\n")
        self.assertEqual(self.store.values["JIRA_PROJECT"], "DEMO")
        self.assertEqual(self.run_cli("use", "NOPE").code, 4)
        self.assertEqual(self.run_cli("current").out, "PROJ\n")  # the settings of this run, not the store
        self.assertEqual(self.run_cli("clear-project").out, "The current project was cleared.\n")
        self.assertEqual(self.run_cli("clear-project").out, "No current project was set.\n")
        empty = self.run_cli("current", settings=settings(project=""))
        self.assertEqual((empty.code, empty.out), (1, ""))
        self.assertIn("jira use", empty.err)
        self.assertEqual(self.run_cli("current", "--json", settings=settings(project="")).json(), {"project": None})

    def test_config_show_masks_the_token(self):
        result = self.run_cli("config")
        self.assertIn("SETTING", result.out)
        self.assertIn("JIRA_PROJECT", result.out)
        self.assertNotIn("old-token", result.out)
        data = self.run_cli("config", "show", "--json").json()
        self.assertEqual(data["file"], "memory:/config")
        self.assertNotIn("old-token", json.dumps(data))

    def test_config_set_and_unset(self):
        self.assertEqual(self.run_cli("config", "set", "JIRA_RETRY", "4").out, "JIRA_RETRY saved to memory:/config\n")
        self.assertEqual(self.store.values["JIRA_RETRY"], "4")
        self.assertEqual(self.run_cli("config", "unset", "JIRA_RETRY").out, "JIRA_RETRY removed from memory:/config\n")
        self.assertIn("was not set", self.run_cli("config", "unset", "JIRA_RETRY").out)
        self.assertEqual(self.run_cli("config", "path").out, "memory:/config\n")
        for argv in (("set",), ("set", "JIRA_RETRY"), ("set", "JIRA_PAT", "x"), ("set", "JIRA_NOPE", "x"), ("set", "JIRA_RETRY", "many"), ("bogus",)):
            with self.subTest(argv=argv):
                self.assertNotEqual(self.run_cli("config", *argv).code, 0)

    def test_a_change_that_the_environment_overrides_is_flagged(self):
        env = {"JIRA_RETRY": "9", "JIRA_PROJECT": "ENVPROJ", "JIRA_PAT": "from-the-environment"}
        for argv in (("config", "set", "JIRA_RETRY", "4"), ("config", "unset", "JIRA_RETRY"), ("use", "demo"), ("clear-project",), ("remove-pat",)):
            with self.subTest(argv=argv):
                result = self.run_cli(*argv, environ=env)
                self.assertEqual(result.code, 0, result.err)
                self.assertIn("overrides the configuration file", result.err)
        quiet = self.run_cli("config", "set", "JIRA_RETRY", "4")
        self.assertNotIn("overrides", quiet.err)
        self.assertNotIn("overrides", self.run_cli("config", "set", "JIRA_RETRY", "4", environ={"JIRA_RETRY": ""}).err)  # empty is not set

    def test_ping(self):
        self.assertEqual(self.run_cli("ping").out, "OK: connected to https://jira.example.com as Jane Doe (jdoe)\n")
        self.assertEqual(self.run_cli("ping", "--quiet").out, "OK\n")
        self.assertEqual(self.run_cli("ping", settings=settings(hide_users=True)).out, "OK: connected to https://jira.example.com\n")
        data = self.run_cli("ping", "--json").json()
        self.assertEqual((data["ok"], data["user"]["name"]), (True, "jdoe"))
        self.assertIsNone(self.run_cli("ping", "--json", settings=settings(hide_users=True)).json()["user"])

    def test_ping_failure(self):
        self.gateway.errors["current_user"] = UnavailableError("Cannot reach Jira.")
        self.assertEqual(self.run_cli("ping").code, 7)

    def test_doctor(self):
        files = MemoryFiles({"memory:/config": ""})
        ok = self.run_cli("doctor", files=files)
        self.assertEqual(ok.code, 0)
        self.assertIn("[ok]   connection", ok.out)
        data = self.run_cli("doctor", "--json", files=files).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["checks"][0]["name"], "runtime")
        broken = self.run_cli("doctor", settings=settings(pat=""), files=files)
        self.assertEqual(broken.code, 1)
        self.assertIn("[FAIL] token", broken.out)
        self.assertIn("Hint: Run `jira setup-pat`.", broken.out)


class InformationCommandsTest(unittest.TestCase):
    def test_how_to(self):
        listing = run_cli("how-to")
        self.assertEqual(listing.out.splitlines()[0].split(), ["TOPIC", "TITLE"])
        self.assertIn("readme", listing.out)
        self.assertIn("daily-workflow", listing.out)
        self.assertEqual(run_cli("how-to", "daily-workflow").out, "# Daily workflow\n\nText.\n")
        self.assertEqual(run_cli("how-to", "README").out, "# README\n")
        self.assertEqual(run_cli("how-to", "nope").code, 4)
        self.assertEqual(run_cli("how-to", "--json").json()[0], {"topic": "readme", "title": "Overview, install and the first commands"})
        self.assertEqual(run_cli("how-to", "daily-workflow", "--json").json(), {"topic": "daily-workflow", "text": "# Daily workflow\n\nText."})

    def test_changelog(self):
        self.assertIn("## 1.0.0", run_cli("changelog").out)

    def test_skill(self):
        self.assertIn("name: jira-dc-cli", run_cli("skill").out)
        shown = run_cli("skill", "--json").json()
        self.assertEqual(shown["name"], "jira-dc-cli")
        self.assertIn("name: jira-dc-cli", shown["text"])
        files = MemoryFiles()
        result = run_cli("skill", "install", files=files)
        self.assertEqual(result.out, "Skill installed: .github/skills/jira-dc-cli/SKILL.md\n")
        self.assertIn(".github/skills/jira-dc-cli/SKILL.md", files.files)
        self.assertEqual(run_cli("skill", "install", files=files).out, "Skill already up to date: .github/skills/jira-dc-cli/SKILL.md\n")
        files.files[".github/skills/jira-dc-cli/SKILL.md"] = "edited"
        refused = run_cli("skill", "install", files=files)
        self.assertEqual(refused.code, 1)
        self.assertIn("--force", refused.err)
        self.assertEqual(run_cli("skill", "install", "--force", files=files).out, "Skill replaced: .github/skills/jira-dc-cli/SKILL.md\n")
        dry = run_cli("skill", "install", "--dir", "elsewhere", "--dry-run", files=files)
        self.assertEqual(dry.out, "Would write the skill to elsewhere/jira-dc-cli/SKILL.md\n")
        self.assertNotIn("elsewhere/jira-dc-cli/SKILL.md", files.files)
        self.assertEqual(run_cli("skill", "install", "--dir", "x", "--json", files=MemoryFiles()).json(), {"path": "x/jira-dc-cli/SKILL.md", "action": "installed", "dryRun": False})

    def test_wiki(self):
        files = MemoryFiles({"n.md": "---\nsummary: x\n---\n# Title\n- item\n"})
        self.assertEqual(run_cli("wiki", "n.md", files=files).out, "h1. Title\n* item\n")
        self.assertEqual(run_cli("wiki", stdin="**bold**\n").out, "*bold*\n")
        self.assertEqual(run_cli("wiki", "-", stdin="**bold**\n").out, "*bold*\n")
        tty = run_cli("wiki", stdin_tty=True)
        self.assertEqual(tty.code, 2)
        self.assertIn("pipe", tty.err)

    def test_api(self):
        gateway = FakeGateway()
        gateway.responses[("GET", "/issue/PROJ-1")] = {"key": "PROJ-1"}
        self.assertEqual(run_cli("api", "get", "/issue/PROJ-1", gateway=gateway).json(), {"key": "PROJ-1"})
        refused = run_cli("api", "POST", "/issue", "--data", "{}", gateway=gateway)
        self.assertEqual(refused.code, 2)
        self.assertIn("--write", refused.err)
        self.assertEqual(run_cli("api", "DELETE", "/issue/PROJ-1", "--write", gateway=gateway).code, 2)
        dry = run_cli("api", "POST", "/issue", "--data", '{"a": 1}', "--write", "--dry-run", gateway=gateway)
        self.assertIn("Dry run: POST /issue; nothing was sent", dry.out)
        self.assertEqual(len(gateway.called("request")), 1)
        files = MemoryFiles({"body.json": '{"fields": {}}'})
        run_cli("api", "PUT", "/issue/PROJ-1", "--data-file", "body.json", "--write", gateway=gateway, files=files)
        self.assertEqual(gateway.called("request")[-1][1:], ("PUT", "/issue/PROJ-1", {"fields": {}}))

    def test_api_paths_survive_git_bash(self):
        gateway = FakeGateway()
        gateway.responses[("GET", "/issue/PROJ-1")] = {"key": "PROJ-1"}
        git_bash = {"EXEPATH": "C:\\Program Files\\Git"}
        for path in ("issue/PROJ-1", "/issue/PROJ-1", "C:/Program Files/Git/issue/PROJ-1", "c:/program files/git/issue/PROJ-1"):
            with self.subTest(path=path):
                self.assertEqual(run_cli("api", "GET", path, gateway=gateway, environ=git_bash).json(), {"key": "PROJ-1"})
        unknown = run_cli("api", "GET", "D:/somewhere/issue/PROJ-1", gateway=gateway, environ=git_bash)
        self.assertEqual(unknown.code, 2)
        self.assertIn("MSYS_NO_PATHCONV", unknown.err)


class StreamDisciplineTest(unittest.TestCase):
    def test_results_go_to_stdout_and_messages_to_stderr(self):
        gateway = FakeGateway()
        result = run_cli("projects-admin", "--json", gateway=gateway)
        json.loads(result.out)  # stdout is clean JSON although progress was reported
        self.assertIn("Checking project", result.err)

    def test_the_token_is_never_printed(self):
        gateway = FakeGateway()
        for argv in (("config",), ("doctor",), ("ping",), ("commands", "--json"), ("issue", "PROJ-1", "-v")):
            result = run_cli(*argv, gateway=gateway)
            with self.subTest(argv=argv):
                self.assertNotIn("secret-token", result.out + result.err)


if __name__ == "__main__":
    unittest.main()
