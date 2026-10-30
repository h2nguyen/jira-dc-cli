"""`jira mcp` as a real process, the way an MCP client starts it: standard input and output are pipes, Jira is the mock, nothing is faked."""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests.integration.test_end_to_end import ROOT, TOKEN, EndToEnd
from tests.support.mock_attachments import add_attachment, attachments_routes

WAIT = 60  # seconds: a test that waits longer has hung


def make_link(test, link, target):
    """A symbolic link to a folder, or on Windows a junction, which the user may make without a privilege; the link (and not what it leads to) is
    taken away again when the test ends. False when the system makes none."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            return False
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False)
        if done.returncode != 0:
            return False

    def take_away():
        try:
            os.rmdir(link)  # a junction, or a link to a folder on Windows
        except OSError:
            try:
                os.unlink(link)
            except OSError:
                pass

    test.addCleanup(take_away)
    return True


class Client:
    """An MCP client over the pipes of a child process: one JSON message per line, and it never waits for ever."""

    def __init__(self, test, *args, env=None):
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_") and key != "PYTHONPATH"}
        # a console that is not UTF-8 must not matter: the server writes ASCII and reads UTF-8
        environment = {**inherited, **test.env, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252", **(env or {})}
        self.child = subprocess.Popen(
            [sys.executable, str(ROOT / "jira.py"), "mcp", *args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd=test.folder,
        )
        test.addCleanup(self.stop)
        self.lines: queue.Queue = queue.Queue()
        self.stderr = bytearray()
        threading.Thread(target=self._read_messages, daemon=True).start()
        self._err_reader = threading.Thread(target=self._read_log, daemon=True)
        self._err_reader.start()
        self.next_id = 0

    def _read_messages(self):
        for raw in iter(self.child.stdout.readline, b""):
            self.lines.put(raw)
        self.lines.put(None)  # the end of the output

    def _read_log(self):
        for raw in iter(self.child.stderr.readline, b""):
            self.stderr.extend(raw)

    def send(self, message):
        self.child.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self.child.stdin.flush()

    def receive(self):
        raw = self.lines.get(timeout=WAIT)
        assert raw is not None, "the server ended"
        assert raw.endswith(b"\n") and b"\r" not in raw, raw
        return json.loads(raw.decode("ascii"))  # one line of pure ASCII that is one JSON message

    def request(self, method, params=None):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, **({} if params is None else {"params": params})})
        reply = self.receive()
        assert reply["id"] == self.next_id, reply
        return reply

    def call(self, tool, arguments=None):
        return self.request("tools/call", {"name": tool, "arguments": arguments or {}})

    def notify(self, method):
        self.send({"jsonrpc": "2.0", "method": method})

    def finish(self):
        """The end of the input: the server ends by itself. Its exit code, and what it still wrote."""
        self.child.stdin.close()
        code = self.child.wait(timeout=WAIT)
        leftover = []
        while True:
            raw = self.lines.get(timeout=WAIT)
            if raw is None:
                break
            leftover.append(raw)
        self._err_reader.join(timeout=WAIT)
        return code, leftover

    def stop(self):
        if self.child.poll() is None:
            self.child.kill()
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            try:
                stream.close()
            except OSError:
                pass
        self.child.wait(timeout=WAIT)

    @property
    def log(self):
        return bytes(self.stderr).decode("utf-8", "replace")


class McpProcessTest(EndToEnd):
    def tearDown(self):
        self.assertEqual(self.jira.unknown_endpoints, [])  # nothing was asked of Jira that the 11.3 reference does not document

    def key_property(self, client):
        tool = next(tool for tool in client.request("tools/list")["result"]["tools"] if tool["name"] == "jira_issue")
        return tool["inputSchema"]["required"][0]

    def test_a_client_starts_the_server_lists_the_tools_and_reads_an_issue(self):
        client = Client(self)
        hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}
        initialized = client.request("initialize", hello)
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "jira-dc-cli")
        client.notify("notifications/initialized")  # no answer: the next line is the answer to the next request
        listing = client.request("tools/list")["result"]["tools"]
        names = {tool["name"] for tool in listing}
        self.assertIn("jira_issue", names)
        self.assertTrue(names.isdisjoint({"jira_update", "jira_create", "jira_mcp", "jira_api", "jira_setup_pat"}))
        key = self.key_property(client)
        reply = client.call("jira_issue", {key: "PROJ-1"})
        self.assertFalse(reply["result"]["isError"])
        issue = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual((issue["key"], issue["summary"]), ("PROJ-1", "Export the report: Ünïcode & more"))
        code, leftover = client.finish()
        self.assertEqual((code, leftover), (0, []))
        self.assertEqual(len(self.jira.sent("GET", "/rest/api/2/issue/PROJ-1")), 1)
        self.assertEqual(self.jira.sent("PUT"), [])
        self.assertNotIn("Traceback", client.log)
        self.assertIn("MCP server", client.log)

    def test_a_failing_call_is_an_error_result_with_the_hint_and_the_server_goes_on(self):
        client = Client(self)
        key = self.key_property(client)
        self.jira.fail_next("/rest/api/2/issue/PROJ-1", 403, body={"errorMessages": ["You do not have the permission to see the specified issue."]})
        denied = client.call("jira_issue", {key: "PROJ-1"})["result"]
        self.assertTrue(denied["isError"])
        error = json.loads(denied["content"][0]["text"])["error"]
        self.assertEqual((error["code"], error["exitCode"]), ("AUTH", 5))
        self.assertTrue(error["hint"])
        missing = json.loads(client.call("jira_issue", {key: "PROJ-999"})["result"]["content"][0]["text"])["error"]
        self.assertEqual((missing["code"], missing["exitCode"]), ("NOT_FOUND", 4))
        self.assertFalse(client.call("jira_issue", {key: "PROJ-1"})["result"]["isError"])
        self.assertEqual(client.finish()[0], 0)

    def test_what_changes_jira_exists_only_with_allow_write_and_a_dry_run_sends_nothing(self):
        read_only = Client(self)
        refused = read_only.call("jira_transition", {"issueKey": "PROJ-1", "to": "In Progress"})
        self.assertEqual((refused["error"]["code"], refused["error"]["message"].startswith("Unknown tool: jira_transition")), (-32602, True))
        self.assertEqual(read_only.finish()[0], 0)
        self.assertEqual(self.jira.sent("POST"), [])
        writer = Client(self, "--allow-write")
        self.assertIn("jira_transition", {tool["name"] for tool in writer.request("tools/list")["result"]["tools"]})
        arguments = {"issueKey": "PROJ-1", "to": "In Progress", "comment": "Zusätzlich: Ünïcode — 日本語"}
        preview = json.loads(writer.call("jira_transition", {**arguments, "dryRun": True})["result"]["content"][0]["text"])
        self.assertEqual((preview["dryRun"], preview["executed"]), (True, False))
        self.assertEqual(preview["payload"]["update"]["comment"][0]["add"]["body"], arguments["comment"])  # UTF-8 in, whatever the console encoding
        self.assertEqual(self.jira.sent("POST"), [])
        done = writer.call("jira_transition", arguments)["result"]
        self.assertFalse(done["isError"], done)
        self.assertEqual(len(self.jira.sent("POST", "/rest/api/2/issue/PROJ-1/transitions")), 1)
        self.assertEqual(self.jira.issues["PROJ-1"]["fields"]["status"]["name"], "In Progress")
        self.assertEqual(self.jira.comments["PROJ-1"][0]["body"], arguments["comment"])
        self.assertEqual(writer.finish()[0], 0)

    def test_people_stay_out_unless_the_server_is_started_to_show_them(self):
        client = Client(self)
        key = self.key_property(client)
        refused = client.call("jira_issue", {key: "PROJ-1", "withUsers": True})
        self.assertEqual(refused["error"]["code"], -32602)
        self.assertIn("withUsers", refused["error"]["message"])
        self.assertEqual(client.finish()[0], 0)
        shown = Client(self, "--show-users")
        data = json.loads(shown.call("jira_issue", {key: "PROJ-1", "withUsers": True})["result"]["content"][0]["text"])
        self.assertEqual(data["assignee"]["name"], "jdoe")
        self.assertEqual(shown.finish()[0], 0)

    def test_the_environment_cannot_wrap_a_result_in_an_envelope_or_add_a_line(self):
        client = Client(self, env={"JIRA_JSON_ENVELOPE": "1"})
        key = self.key_property(client)
        issue = json.loads(client.call("jira_issue", {key: "PROJ-1"})["result"]["content"][0]["text"])
        self.assertEqual(issue["key"], "PROJ-1")
        self.assertEqual(client.finish(), (0, []))

    def test_the_end_of_the_input_ends_the_server_with_exit_code_0(self):
        client = Client(self)
        self.assertEqual(client.finish(), (0, []))
        self.assertIn("MCP server", client.log)

    def test_with_dash_v_the_requests_to_jira_are_logged_and_never_the_token(self):
        client = Client(self, "-v")
        key = self.key_property(client)
        reply = client.call("jira_issue", {key: "PROJ-1"})
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(client.finish(), (0, []))
        self.assertIn("GET /rest/api/2/issue/PROJ-1", client.log)
        self.assertIn("-> 200", client.log)
        self.assertNotIn(TOKEN, client.log)
        self.assertNotIn(TOKEN, json.dumps(reply))

    def test_an_unknown_group_ends_the_process_with_a_usage_error_and_no_message_on_standard_output(self):
        client = Client(self, "--group", "Nope")
        code, leftover = client.finish()
        self.assertEqual((code, leftover), (2, []))
        self.assertIn("Unknown group: 'Nope'", client.log)

    def listed(self, *args):
        client = Client(self, *args)
        names = {tool["name"] for tool in client.request("tools/list")["result"]["tools"]}
        self.assertEqual(client.finish()[0], 0)
        return names

    def test_the_tools_that_write_files_exist_only_with_allow_files(self):
        files = {"jira_download", "jira_pull", "jira_export"}
        for args in ((), ("--allow-write",), ("--group", "Read"), ("--allow-write", "--group", "Read")):
            with self.subTest(args=args):
                self.assertEqual(self.listed(*args) & files, set())
        for args in (("--allow-files",), ("--allow-write", "--allow-files")):
            with self.subTest(args=args):
                names = self.listed(*args)
                self.assertEqual(names & files, files)
                self.assertEqual("jira_update" in names, "--allow-write" in args)  # each switch offers its own tools
        refused = Client(self, "--group", "Read")
        answer = refused.call("jira_download", {"issueKey": "PROJ-1", "all": True})
        self.assertEqual(answer["error"]["code"], -32602)
        self.assertIn("--allow-files", answer["error"]["message"])
        self.assertEqual(refused.finish()[0], 0)
        self.assertFalse((self.folder / "tmp").exists())  # nothing was written

    def test_a_tool_cannot_read_the_settings_file_that_lies_in_the_working_folder(self):
        client = Client(self, "--allow-write")
        notes = self.folder / "notes.md"
        notes.write_text("a note for the issue", encoding="utf-8")
        cases = [
            ("jira_comment", {"issueKey": "PROJ-1", "file": "config", "dryRun": True}),
            ("jira_comment", {"issueKey": "PROJ-1", "file": str(self.config), "dryRun": True}),
            ("jira_update", {"issueKey": "PROJ-1", "descriptionFile": "./config", "dryRun": True}),
            ("jira_transition", {"issueKey": "PROJ-1", "to": "In Progress", "commentFile": "sub/../config", "dryRun": True}),
            ("jira_create_json", {"payloadJson": "config", "dryRun": True}),
            ("jira_update_json", {"issueKey": "PROJ-1", "payloadJson": str(self.config), "dryRun": True}),
        ]
        for tool, arguments in cases:
            with self.subTest(tool=tool, arguments=arguments):
                reply = client.call(tool, arguments)
                self.assertTrue(reply["result"]["isError"], reply)
                self.assertNotIn(TOKEN, json.dumps(reply))
                self.assertIn("settings file", reply["result"]["content"][0]["text"])
        copy = self.folder / "copy.txt"
        copy.write_text(self.config.read_text(encoding="utf-8"), encoding="utf-8")
        held = client.call("jira_comment", {"issueKey": "PROJ-1", "file": "copy.txt", "dryRun": True})
        self.assertTrue(held["result"]["isError"])
        self.assertIn("holds the token", held["result"]["content"][0]["text"])
        fine = client.call("jira_comment", {"issueKey": "PROJ-1", "file": "notes.md", "dryRun": True})["result"]  # a file of the project is still read
        self.assertFalse(fine["isError"], fine)
        self.assertEqual(json.loads(fine["content"][0]["text"])["payload"]["body"], "a note for the issue")
        self.assertEqual(client.finish()[0], 0)
        self.assertEqual(self.jira.sent("POST"), [])
        self.assertNotIn(TOKEN, client.log)
        self.assertEqual(client.log.count("was refused"), len(cases) + 1)
        self.assertNotIn("Traceback", client.log)

    def test_with_allow_files_the_files_go_below_the_working_folder_and_nothing_is_replaced(self):
        attachments_routes(self.jira)
        for name, data in (("tasks.json", b'{"attacker": "controlled bytes"}'), ("notes.txt", b"plain")):
            add_attachment(self.jira, "PROJ-1", name, data, "text/plain")
        outside = Path(tempfile.mkdtemp(prefix="mcp-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        (outside / "tasks.json").write_text("ORIGINAL user file", encoding="utf-8")
        client = Client(self, "--allow-files")

        def refused(tool, arguments, *needles):
            reply = client.call(tool, arguments)["result"]
            self.assertTrue(reply["isError"], reply)
            text = reply["content"][0]["text"]
            for needle in needles:
                self.assertIn(needle, text)
            self.assertNotIn(TOKEN, text)

        saved = client.call("jira_download", {"issueKey": "PROJ-1", "all": True})["result"]
        self.assertFalse(saved["isError"], saved)
        folder = self.folder / "tmp" / "jira-attachments" / "PROJ-1"
        self.assertEqual((folder / "tasks.json").read_bytes(), b'{"attacker": "controlled bytes"}')
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "out": str(outside), "force": True}, "'force' is not honoured")
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "out": str(outside)}, "not below the working folder")
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "out": os.path.relpath(outside, self.folder)}, "not below the working folder")
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "out": ".vscode"}, "would write into '.vscode'")
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "out": "."}, "is the working folder itself")
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "out": "\\\\nonexistent.invalid\\share"}, "network or device path")
        refused("jira_download", {"issueKey": "PROJ-1", "all": True, "force": True}, "'force' is not honoured")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["tasks.json"])
        self.assertEqual((outside / "tasks.json").read_text(encoding="utf-8"), "ORIGINAL user file")
        self.assertFalse((self.folder / ".vscode").exists())
        (folder / "tasks.json").write_text("EDITED by the user", encoding="utf-8")
        client.call("jira_download", {"issueKey": "PROJ-1", "all": True})  # a file that exists stays as it is: there is no force over MCP
        self.assertEqual((folder / "tasks.json").read_text(encoding="utf-8"), "EDITED by the user")
        exported = client.call("jira_export", {"project": "PROJ", "out": "tmp/PROJ.json"})["result"]
        self.assertFalse(exported["isError"], exported)
        dump = (self.folder / "tmp" / "PROJ.json").read_bytes()
        self.assertIn(b"PROJ-1", dump)
        refused("jira_export", {"project": "PROJ", "out": "tmp/PROJ.json"}, "a file that exists")
        refused("jira_export", {"project": "PROJ", "out": "config"}, "settings file")
        refused("jira_export", {"project": "PROJ", "out": str(outside / "dump.json")}, "not below the working folder")
        self.assertEqual((self.folder / "tmp" / "PROJ.json").read_bytes(), dump)
        self.assertFalse((outside / "dump.json").exists())
        self.assertIn(TOKEN, self.config.read_text(encoding="utf-8"))  # the settings file is as it was
        self.assertEqual(client.finish()[0], 0)
        self.assertNotIn("Traceback", client.log)

    def test_blanks_around_the_folder_of_a_download_do_not_switch_the_policy_off(self):
        """As a real process: the download strips `out`, so ` .git/hooks` was approved as a folder with a blank in its name and a
        stranger's attachment became a git hook; ` ../../x` wrote above the working folder."""
        attachments_routes(self.jira)
        add_attachment(self.jira, "PROJ-1", "pre-commit", b"#!/bin/sh\necho owned\n", "application/octet-stream")
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"plain", "text/plain")
        (self.folder / ".git" / "hooks").mkdir(parents=True)
        (self.folder / "src").mkdir()
        above = self.folder.parent
        names = ("escaped", "escaped2", "deep")
        for name in names:
            self.assertFalse((above / name).exists(), "a folder of an earlier run is in the way")
        files = lambda: sorted(str(path.relative_to(self.folder)) for path in self.folder.rglob("*") if path.is_file())  # noqa: E731
        before = files()
        client = Client(self, "--allow-files")
        spellings = (
            " .git/hooks", "\u00a0.git/hooks", "\u3000.vscode", "\u2003.github/workflows", "\u0085.git/hooks", "\u200b.git/hooks", "\t.git/hooks",
            " ../../escaped2", "\u00a0../../deep", " ../escaped", "\u00a0.", "\u00a0..", " ../src", ".git/hooks ",
        )
        for out in spellings:
            with self.subTest(out=out):
                reply = client.call("jira_download", {"issueKey": "PROJ-1", "name": "pre-commit", "out": out})
                self.assertTrue(reply["result"]["isError"], reply)
                self.assertIn("starts or ends with a blank or an invisible character", reply["result"]["content"][0]["text"])
        self.assertEqual(files(), before)
        self.assertEqual(list((self.folder / ".git" / "hooks").iterdir()), [])
        self.assertEqual([name for name in names if (above / name).exists()], [])  # nothing above the working folder either
        saved = client.call("jira_download", {"issueKey": "PROJ-1", "name": "notes.txt", "out": "./tmp/../downloads"})["result"]
        self.assertFalse(saved["isError"], saved)
        self.assertEqual((self.folder / "downloads" / "notes.txt").read_bytes(), b"plain")  # the folder that was approved, written in full
        self.assertFalse((self.folder / "tmp").exists())
        self.assertEqual(client.finish()[0], 0)
        self.assertEqual(client.log.count("was refused"), len(spellings))

    def test_the_map_file_of_an_import_must_be_a_csv_that_is_empty_or_a_map(self):
        tickets = self.folder / "tickets"
        tickets.mkdir()
        (tickets / "001-epic.md").write_text("---\nsource_id: E-1\nsummary: An epic\nissuetype: Epic\n---\nEpic text\n", encoding="utf-8")
        readme = self.folder / "README.md"
        readme.write_text("# My project\n\nImportant text.\n", encoding="utf-8")
        (self.folder / "src").mkdir()
        (self.folder / "src" / "__init__.py").write_bytes(b"")
        client = Client(self, "--allow-write")
        for name in ("README.md", "src/__init__.py"):
            with self.subTest(map_file=name):
                refused = client.call("jira_import_md_dir", {"directory": "tickets", "mapFile": name})["result"]
                self.assertTrue(refused["isError"], refused)
                self.assertIn("must be a .csv file", refused["content"][0]["text"])
        self.assertEqual(readme.read_text(encoding="utf-8"), "# My project\n\nImportant text.\n")
        self.assertEqual((self.folder / "src" / "__init__.py").read_bytes(), b"")
        self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue"), [])
        done = client.call("jira_import_md_dir", {"directory": "tickets", "mapFile": "maps/import.csv"})["result"]
        self.assertFalse(done["isError"], done)
        self.assertEqual(len(self.jira.sent("POST", "/rest/api/2/issue")), 1)
        self.assertTrue((self.folder / "maps" / "import.csv").read_text(encoding="utf-8").startswith("source_id,source_key,jira_key,"))
        self.assertEqual(client.finish()[0], 0)

    def test_a_link_in_the_working_folder_does_not_take_a_call_that_leaves_a_place_out_anywhere_else(self):
        """As a real process: a junction at `tmp` put the default folder of `jira_download` outside the working folder (the files of the
        ticket's author were written there), and a hard link with the name of the default map made an import add a row to the README.
        A given `out` and a given `mapFile` were refused all along; now the place that a call leaves out is judged too."""
        attachments_routes(self.jira)
        add_attachment(self.jira, "PROJ-1", "authorized_keys", b"ssh-rsa ATTACKER", "text/plain")
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"plain", "text/plain")
        outside = Path(tempfile.mkdtemp(prefix="mcp-outside-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        tickets = self.folder / "tickets"
        tickets.mkdir()
        (tickets / "001-epic.md").write_text("---\nsource_id: E-1\nsummary: An epic\nissuetype: Epic\n---\nEpic text\n", encoding="utf-8")
        readme = self.folder / "README.md"
        readme.write_text("# My project\n\nImportant text.\n", encoding="utf-8")
        linked = make_link(self, self.folder / "tmp", outside)
        try:
            os.link(readme, tickets / "jira-import-map.csv")
            hard = True
        except (OSError, NotImplementedError):
            hard = False
        if not (linked or hard):
            self.skipTest("this system makes no link")
        client = Client(self, "--allow-files", "--allow-write")
        if linked:
            for given in ({}, {"out": "tmp/explicit"}):
                reply = client.call("jira_download", {"issueKey": "PROJ-1", "all": True, **given})["result"]
                self.assertTrue(reply["isError"], reply)
                self.assertIn("is not below the working folder", reply["content"][0]["text"])
            self.assertEqual(list(outside.iterdir()), [])  # nothing was written outside the working folder
            read = client.call("jira_download", {"issueKey": "PROJ-1", "name": "notes.txt", "text": True})["result"]  # no file is written: no matter
            self.assertFalse(read["isError"], read)
            self.assertEqual(list(outside.iterdir()), [])
        if hard:
            for given in ({}, {"mapFile": "tickets/jira-import-map.csv"}):
                reply = client.call("jira_import_md_dir", {"directory": "tickets", **given})["result"]
                self.assertTrue(reply["isError"], reply)
                self.assertIn("is not an import map", reply["content"][0]["text"])
            self.assertEqual(readme.read_text(encoding="utf-8"), "# My project\n\nImportant text.\n")
            self.assertEqual(self.jira.sent("POST", "/rest/api/2/issue"), [])
        self.assertEqual(client.finish()[0], 0)
        self.assertEqual(client.log.count("was refused"), 2 * linked + 2 * hard)
        self.assertNotIn("Traceback", client.log)

    def test_without_a_link_the_default_places_are_used_and_named_in_full(self):
        attachments_routes(self.jira)
        add_attachment(self.jira, "PROJ-1", "notes.txt", b"plain", "text/plain")
        tickets = self.folder / "tickets"
        tickets.mkdir()
        (tickets / "001-epic.md").write_text("---\nsource_id: E-1\nsummary: An epic\nissuetype: Epic\n---\nEpic text\n", encoding="utf-8")
        client = Client(self, "--allow-files", "--allow-write")
        saved = json.loads(client.call("jira_download", {"issueKey": "proj-1", "all": True})["result"]["content"][0]["text"])
        folder = self.folder / "tmp" / "jira-attachments" / "PROJ-1"
        self.assertEqual(Path(saved["outDir"]).resolve(), folder.resolve())
        self.assertEqual((folder / "notes.txt").read_bytes(), b"plain")
        done = client.call("jira_import_md_dir", {"directory": "tickets"})["result"]
        self.assertFalse(done["isError"], done)
        self.assertEqual(Path(json.loads(done["content"][0]["text"])["mapFile"]).resolve(), (tickets / "jira-import-map.csv").resolve())
        self.assertTrue((tickets / "jira-import-map.csv").read_text(encoding="utf-8").startswith("source_id,source_key,jira_key,"))
        self.assertEqual(client.finish()[0], 0)
        self.assertEqual(client.log.count("was refused"), 0)

    def test_a_closed_standard_output_ends_the_server_quietly(self):
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_")}
        child = subprocess.Popen(
            [sys.executable, str(ROOT / "jira.py"), "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**inherited, **self.env}, cwd=self.folder,
        )
        self.addCleanup(child.kill)
        child.stdout.close()  # the client went away
        child.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        child.stdin.flush()
        error = child.stderr.read()
        child.stderr.close()
        self.assertEqual(child.wait(timeout=WAIT), 141)
        self.assertNotIn(b"Traceback", error)
        self.assertNotIn(b"Exception ignored", error)
        child.stdin.close()


if __name__ == "__main__":
    unittest.main()
