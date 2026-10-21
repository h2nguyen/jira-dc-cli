"""The whole program: the command line, the real adapters and a mock Jira, with no fakes."""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jira_dc_cli.adapters.driving.cli.main import run
from jira_dc_cli.bootstrap import create_application
from jira_dc_cli.version import __version__
from tests.support.cli import Result
from tests.support.mock_jira import MockJira

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-token"


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.jira = MockJira(token=TOKEN)
        self.jira.__enter__()
        self.addCleanup(self.jira.__exit__, None, None, None)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.config = self.folder / "config"
        self.write_config(self.jira.base_url)
        self.env = {"JIRA_CONFIG_FILE": str(self.config), "XDG_CACHE_HOME": str(self.folder / "cache"), "HOME": str(self.folder)}

    def write_config(self, base_url, token=TOKEN):
        self.config.write_text(f"JIRA_BASE_URL={base_url}\nJIRA_PAT={token}\nJIRA_PROJECT=PROJ\n", encoding="utf-8")

    def cli(self, *argv, stdin="", env=None) -> Result:
        environ = {**self.env, **(env or {})}
        out, err = io.StringIO(), io.StringIO()
        code = run(list(argv), app_factory=lambda diagnostics: create_application(diagnostics, environ), environ=environ, stdout=out, stderr=err, stdin=io.StringIO(stdin))
        return Result(code, out.getvalue(), err.getvalue())


class ReadingTest(EndToEnd):
    def test_ping_and_doctor(self):
        self.assertEqual(self.cli("ping").out, f"OK: connected to {self.jira.base_url} as Jane Doe (jdoe)\n")
        doctor = self.cli("doctor")
        self.assertEqual(doctor.code, 0, doctor.out)
        self.assertIn("Jira 9.12.0", doctor.out)

    def test_issue(self):
        result = self.cli("issue", "PROJ-1")
        self.assertEqual(result.code, 0, result.err)
        for expected in ("PROJ-1  [Story | To Do | High]", "Story points: 5", "Sprint:       Demo Sprint 3 (ACTIVE)", "Epic:         PROJ-9", "Comments: 1"):
            self.assertIn(expected, result.out)
        for personal in ("jane.doe", "Jane", "[~jdoe]"):
            self.assertNotIn(personal, result.out)

    def test_issue_in_json_with_comments_and_users(self):
        data = self.cli("issue", "PROJ-1", "--comments", "--with-users", "--json").json()
        self.assertEqual((data["assignee"]["name"], data["comments"][0]["author"]["name"], data["storyPoints"]), ("jdoe", "jdoe", 5.0))

    def test_issues_pages_through_a_server_that_returns_less_than_asked(self):
        result = self.cli("issues", "--all", "--json", "--fields", "labels,customfield_10004")
        data = result.json()
        self.assertEqual((data["total"], data["count"]), (6, 6))
        self.assertEqual(data["issues"][0]["customfield_10004"], "1. GIVEN a, WHEN b, THEN c.")
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/search")), 3)
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/field")), 1)

    def test_custom_fields_by_logical_name(self):
        data = self.cli("issues", "--all", "--json", "--fields", "story_points,epic_link,sprint,acceptance,parent").json()
        first = next(row for row in data["issues"] if row["key"] == "PROJ-1")
        self.assertEqual((first["storyPoints"], first["epicLink"], first["acceptance"]), (5.0, "PROJ-9", "1. GIVEN a, WHEN b, THEN c."))
        self.assertEqual(first["sprints"], [{"name": "Demo Sprint 3", "state": "ACTIVE"}])
        self.assertEqual(first["parentKey"], "")

    def test_mine_and_explicit_jql(self):
        self.assertEqual(self.cli("issues", "--mine", "--all", "--json").json()["count"], 5)
        self.assertEqual(self.cli("issues", "--jql", 'project = "PROJ" AND issuetype = Epic', "--all", "--json").json()["count"], 2)

    def test_report(self):
        data = self.cli("report", "--sprint", "active", "--epics", "--json").json()
        self.assertEqual((data["issues"], data["done"], data["points"], data["donePoints"]), (4, 1, 16.0, 8.0))
        self.assertEqual(set(data["byEpic"]), {"PROJ-8", "PROJ-9", "(no epic)"})
        self.assertEqual(data["byEpic"]["PROJ-9"]["summary"], "Epic nine")

    def test_export_matches_the_dump_format_and_has_no_personal_data(self):
        target = self.folder / "tmp" / "PROJ.json"
        self.assertEqual(self.cli("export", "--out", str(target)).code, 0)
        dump = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(sorted(dump["issues"], key=lambda key: int(key.split("-")[1])), ["PROJ-1", "PROJ-2", "PROJ-3", "PROJ-4", "PROJ-8", "PROJ-9"])
        self.assertEqual(dump["fieldIds"], {"epicLink": "customfield_10002", "storyPoints": "customfield_10001", "acceptance": "customfield_10004", "sprint": "customfield_10003"})
        entry = dump["issues"]["PROJ-1"]
        self.assertEqual((entry["summary"], entry["epicLink"], entry["storyPoints"], entry["sprint"]), ("Export the report: Ünïcode & more", "PROJ-9", 5.0, [{"name": "Demo Sprint 3", "state": "ACTIVE"}]))
        self.assertEqual(entry["description"], "Ask [~user] or [email]\nSecond line")
        self.assertNotIn("jane", target.read_text(encoding="utf-8").lower())

    def test_branch_projects_fields(self):
        self.assertEqual(self.cli("branch", "PROJ-1").out, "feature/PROJ-1-export-the-report-unicode-more\n")
        self.assertIn("PROJ", self.cli("projects-admin", "--quiet").out)
        self.assertNotIn("DEMO", self.cli("projects-admin", "--quiet").out)
        self.assertEqual(self.cli("fields", "--search", "epic", "--json").json()[0]["id"], "customfield_10002")

    def test_raw_api(self):
        self.assertEqual(self.cli("api", "GET", "/rest/agile/1.0/board").json()["values"][0]["id"], 1200)

    def test_non_ascii_text_comes_through(self):
        self.assertIn("Ünïcode", self.cli("issues", "--all", "--no-assignee").out)


class WritingTest(EndToEnd):
    def test_transition_with_a_comment(self):
        self.assertEqual(self.cli("transition", "PROJ-1", "--to", "In Progress", "--comment", "Starting.").code, 0)
        self.assertEqual(self.cli("issue", "PROJ-1", "--json").json()["status"]["name"], "In Progress")
        self.assertEqual(self.jira.comments["PROJ-1"][0]["body"], "Starting.")

    def test_comment_and_link_and_update(self):
        self.assertIn("Commented on PROJ-1", self.cli("comment", "PROJ-1", "--text", "Note").out)
        self.assertEqual(self.cli("link", "PROJ-1", "--type", "Blocks", "--to", "PROJ-2", "--inverse").out, "Linked: PROJ-1 is blocked by PROJ-2\n")
        self.assertEqual(self.cli("update", "PROJ-1", "--summary", "Renamed", "--labels", "x,y").out, "Updated PROJ-1\n")
        sent = self.jira.sent("PUT", "/rest/api/2/issue/PROJ-1")[0].body
        self.assertEqual(sent, {"fields": {"summary": "Renamed", "labels": ["x", "y"]}})

    def test_create_resolves_the_type_once_and_the_reporter_from_the_token(self):
        first = self.cli("create", "--type", "Story", "--summary", "Fresh story", "--labels", "a", "--json")
        self.assertEqual(first.json()["key"], "PROJ-101")
        self.cli("create", "--type", "bug", "--summary", "Another")
        fields = self.jira.sent("POST", "/rest/api/2/issue")[0].body["fields"]
        self.assertEqual((fields["issuetype"], fields["reporter"], fields["project"]), ({"id": "10200"}, {"name": "jdoe"}, {"key": "PROJ"}))
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/createmeta/PROJ")), 1)
        self.assertEqual(self.cli("issue", "PROJ-101", "--json").json()["summary"], "Fresh story")

    def test_dry_runs_send_no_write(self):
        self.cli("transition", "PROJ-1", "--to", "In Progress", "--dry-run")
        self.cli("comment", "PROJ-1", "--text", "x", "--dry-run")
        self.cli("link", "PROJ-1", "--type", "Blocks", "--to", "PROJ-2", "--dry-run")
        self.cli("update", "PROJ-1", "--summary", "x", "--dry-run")
        self.cli("create", "--type", "Story", "--summary", "x", "--dry-run")
        writes = [item for item in self.jira.requests if item.method in ("POST", "PUT", "DELETE")]
        self.assertEqual(writes, [])

    def test_the_stale_guard(self):
        result = self.cli("update", "PROJ-1", "--summary", "x", "--if-unchanged-since", "2026-10-01T00:00:00Z", "--json")
        self.assertEqual((result.code, result.error()["code"]), (3, "CONFLICT"))
        self.assertEqual(self.jira.sent("PUT"), [])
        self.assertEqual(self.cli("update", "PROJ-1", "--summary", "x", "--if-unchanged-since", "2026-10-11T00:00:00Z").code, 0)

    def test_import_md_dir_with_epics_and_wiki(self):
        folder = self.folder / "tickets"
        folder.mkdir()
        (folder / "001-epic.md").write_text("---\nsource_id: E-1\nsummary: An epic\nissuetype: Epic\n---\nEpic text\n", encoding="utf-8")
        (folder / "002-story.md").write_text("---\nsource_id: S-1\nparent_id: E-1\nsummary: A story\nissuetype: Story\nfix_versions: Release 1.0\n---\n# Goal\n\n- **one**\n", encoding="utf-8")
        result = self.cli("import-md-dir", str(folder), "--wiki")
        self.assertEqual(result.code, 0, result.out + result.err)
        self.assertIn("created=2", result.out)
        story = self.jira.sent("POST", "/rest/api/2/issue")[1].body["fields"]
        self.assertEqual((story["customfield_10002"], story["description"], story["fixVersions"]), ("PROJ-101", "h1. Goal\n\n* *one*", [{"name": "Release 1.0"}]))
        self.assertEqual((folder / "jira-import-map.csv").read_text(encoding="utf-8").splitlines()[2], "S-1,,PROJ-102,Story,Created,002-story.md")
        self.assertEqual(self.cli("import-md-dir", str(folder)).out.count("skipped"), 3)

    def test_import_csv(self):
        sheet = self.folder / "rows.csv"
        sheet.write_text("summary,issuetype,labels\nFrom csv,Story,\"a,b\"\n", encoding="utf-8")
        self.assertEqual(self.cli("import-csv", str(sheet)).code, 0)
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue")[0].body["fields"]["labels"], ["a", "b"])


class FailureTest(EndToEnd):
    def test_exit_codes_follow_what_jira_said(self):
        cases = (
            (("issue", "PROJ-999"), {}, 4),
            (("issues", "--jql", "BROKEN"), {}, 6),
            (("transition", "PROJ-1", "--to", "Done"), {}, 1),
            (("issue", "PROJ-1"), {"JIRA_PAT": "wrong"}, 5),
        )
        for argv, env, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(self.cli(*argv, env=env).code, expected)

    def test_a_server_that_is_down(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        result = self.cli("issue", "PROJ-1", "--json", env={"JIRA_BASE_URL": f"http://127.0.0.1:{port}", "JIRA_RETRY": "0"})
        self.assertEqual((result.code, result.error()["code"]), (7, "UNAVAILABLE"))

    def test_a_retry_after_a_server_hiccup(self):
        self.jira.fail_next("/rest/api/2/issue/PROJ-1", 503)
        result = self.cli("issue", "PROJ-1", env={"JIRA_RETRY": "1"})
        self.assertEqual(result.code, 0, result.err)

    def test_the_token_is_never_written_to_the_output(self):
        for argv in (("issue", "PROJ-1", "-v"), ("doctor",), ("config",), ("issue", "PROJ-999", "-v")):
            result = self.cli(*argv)
            with self.subTest(argv=argv):
                self.assertNotIn(TOKEN, result.out + result.err)

    def test_verbose_logs_requests_to_stderr(self):
        result = self.cli("issue", "PROJ-1", "-v")
        self.assertIn("GET /rest/api/2/issue/PROJ-1", result.err)
        self.assertIn("-> 200", result.err)
        self.assertNotIn("-> 200", result.out)

    def test_the_environment_overrides_the_config_file(self):
        self.assertEqual(self.cli("current", env={"JIRA_PROJECT": "DEMO"}).out, "DEMO\n")
        self.assertEqual(self.cli("current").out, "PROJ\n")


class ProcessTest(EndToEnd):
    """The real program in its own process, as a shell starts it."""

    def process(self, *argv, stdin=b"", entry=None, **env):
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_") and key != "PYTHONPATH"}
        environment = {**inherited, **self.env, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", **env}
        command = entry or [sys.executable, str(ROOT / "jira.py")]
        return subprocess.run([*command, *argv], input=stdin, capture_output=True, env=environment, cwd=self.folder, timeout=60)

    def test_utf8_and_plain_newlines_whatever_the_console_encoding(self):
        done = self.process("issues", "--all", "--no-assignee")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Ünïcode".encode("utf-8"), done.stdout)
        self.assertNotIn(b"\r", done.stdout)
        self.assertNotIn(b"\r", self.process("branch", "PROJ-1").stdout)

    def test_the_module_entry_point(self):
        done = self.process("version", entry=[sys.executable, "-m", "jira_dc_cli"], PYTHONPATH=str(ROOT / "src"))
        self.assertEqual((done.returncode, done.stdout), (0, f"jira-dc-cli {__version__}\n".encode()))

    def test_exit_codes_reach_the_shell(self):
        self.assertEqual(self.process("issue", "PROJ-999").returncode, 4)
        self.assertEqual(self.process("issue").returncode, 2)
        self.assertEqual(self.process("nonsense").returncode, 2)

    def test_standard_input_is_read_as_utf8(self):
        done = self.process("wiki", "-", stdin="# Überschrift\n".encode("utf-8"))
        self.assertEqual(done.stdout, "h1. Überschrift\n".encode("utf-8"))

    def test_a_closed_pipe_ends_quietly(self):
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_")}
        child = subprocess.Popen([sys.executable, str(ROOT / "jira.py"), "issues", "--all"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**inherited, **self.env}, cwd=self.folder)
        child.stdout.close()  # like `jira issues | head -0`
        error = child.stderr.read()
        child.stderr.close()
        child.wait(timeout=60)
        self.assertNotIn(b"Traceback", error)
        self.assertNotIn(b"Exception ignored", error)


    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "needs Windows PowerShell 5.1")
    def test_windows_powershell_delivers_a_quoted_at_file_and_strips_inline_json(self):
        """What the help, the hint and the guides tell PowerShell users: '@file' arrives, inline JSON loses its quotes."""
        (self.folder / "fields.json").write_text('{"customfield_10001": 5}', encoding="utf-8")
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_") and key != "PYTHONPATH"}

        def powershell(argument: str):
            command = f"& '{sys.executable}' -X utf8 '{ROOT / 'jira.py'}' update PROJ-1 --fields-json {argument} --dry-run --json; exit $LASTEXITCODE"
            return subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env={**inherited, **self.env}, cwd=self.folder, timeout=120,
            )

        quoted = powershell("'@fields.json'")
        self.assertEqual(quoted.returncode, 0, quoted.stdout + quoted.stderr)
        self.assertEqual(json.loads(quoted.stdout)["payload"]["fields"]["customfield_10001"], 5)
        inline = powershell("""'{"customfield_10001": 5}'""")
        self.assertEqual(inline.returncode, 1, inline.stdout)
        self.assertIn("is not valid JSON", inline.stderr)  # PowerShell wraps long lines, so only the start of the hint is checked
        self.assertIn("PowerShell 5.1", inline.stderr)


class ConfigurationTest(EndToEnd):
    def test_config_commands_work_on_the_real_file(self):
        self.assertEqual(self.cli("config", "set", "JIRA_REPORTER", "o'brien").code, 0)
        self.assertIn("JIRA_REPORTER='o'\"'\"'brien'", self.config.read_text(encoding="utf-8"))
        shown = self.cli("config", "show", "--json").json()
        entries = {entry["name"]: entry for entry in shown["settings"]}
        self.assertEqual((entries["JIRA_REPORTER"]["value"], entries["JIRA_REPORTER"]["source"]), ("o'brien", "config file"))
        self.assertNotIn(TOKEN, json.dumps(shown))
        self.assertEqual(self.cli("config", "unset", "JIRA_REPORTER").code, 0)
        self.assertNotIn("JIRA_REPORTER", self.config.read_text(encoding="utf-8"))

    def test_setup_pat_replaces_only_the_token(self):
        result = self.cli("setup-pat", "--stdin", stdin="a-new-token\n")
        self.assertEqual(result.code, 0)
        text = self.config.read_text(encoding="utf-8")
        self.assertIn("JIRA_PAT=a-new-token", text)
        self.assertNotIn(TOKEN, text)
        self.assertIn(f"JIRA_BASE_URL={self.jira.base_url}", text)
        self.assertNotIn("a-new-token", result.out + result.err)
        self.assertEqual(self.cli("remove-pat").code, 0)
        self.assertNotIn("JIRA_PAT", self.config.read_text(encoding="utf-8"))

    def test_use_checks_the_project_and_remembers_it(self):
        self.assertEqual(self.cli("use", "DEMO").out, "Current project: DEMO (Project DEMO)\n")
        self.assertIn("JIRA_PROJECT=DEMO", self.config.read_text(encoding="utf-8"))
        self.assertEqual(self.cli("use", "NOPE").code, 4)
        self.assertEqual(self.cli("clear-project").code, 0)
        self.assertEqual(self.cli("current").code, 1)

    def test_a_first_run_without_any_configuration_says_what_to_do(self):
        empty = {"JIRA_CONFIG_FILE": str(self.folder / "none"), "JIRA_BASE_URL": "", "JIRA_PAT": ""}
        result = self.cli("issue", "PROJ-1", "--json", env=empty)
        self.assertEqual((result.code, result.error()["code"]), (1, "CONFIG"))
        self.assertIn("jira config set JIRA_BASE_URL", result.error()["hint"])
        doctor = self.cli("doctor", env=empty)
        self.assertEqual(doctor.code, 1)
        self.assertIn("[FAIL] base url", doctor.out)

    def test_a_config_file_in_the_documented_format_is_understood(self):
        self.config.write_text(f"JIRA_BASE_URL={self.jira.base_url}\nJIRA_PAT={TOKEN}\nJIRA_PROJECT=PROJ\nJIRA_HIDE_USERS=true\n", encoding="utf-8")
        self.assertEqual(self.cli("ping").out, f"OK: connected to {self.jira.base_url}\n")
        self.assertEqual(self.cli("ping", "--quiet").out, "OK\n")
        self.assertEqual(self.cli("issue", "PROJ-1", "--with-users").code, 1)


if __name__ == "__main__":
    unittest.main()
