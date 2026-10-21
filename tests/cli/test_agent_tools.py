"""`jira commands --format mcp`: the commands an agent may use, described as tools (name, input schema, annotations)."""
import json
import re
import unittest

from jira_dc_cli.adapters.driving.cli.help import (
    MCP_DESTRUCTIVE,
    MCP_EXCLUDED,
    MCP_FILES,
    MCP_IDEMPOTENT,
    MCP_LOCAL,
    catalog,
    mcp_property_names,
    mcp_tool,
    mcp_tools,
    render_command_help,
)
from jira_dc_cli.adapters.driving.cli.parser import build_parser
from jira_dc_cli.adapters.driving.cli.registry import all_commands, find_command
from jira_dc_cli.adapters.driving.cli.spec import Arg, Command
from jira_dc_cli.version import __version__
from tests.support.cli import run_cli

JSON_TYPES = {"string", "integer", "number", "boolean"}
ADDITIVE = {"comment", "link", "create", "create-json"}  # they add and never replace, move or remove
FOR_THE_PERSON = ("setup-pat", "remove-pat", "config", "use", "clear-project", "api", "help", "commands", "skill", "changelog", "wiki")  # `wiki` prints any file it is given
FRONT_MATTER_LINES = {"jira_key", "jira_base", "jira_synced"}  # they look like tool names in a description of `diff`, `pull` and `push`


def tools(only="all"):
    return mcp_tools(all_commands(), __version__, only=only)["tools"]


def argv_from(tool, arguments):
    """What a program that reads `_meta.jira` does: the arguments of a call become a command line."""
    meta = tool["_meta"]["jira"]
    argv = list(meta["command"][1:])
    for item in meta["arguments"]:
        value = arguments.get(item["property"])
        if item["positional"] and value is not None:
            argv.append(str(value))
    for item in meta["arguments"]:
        value = arguments.get(item["property"])
        if item["positional"] or value is None or value is False:
            continue
        argv.append(item["flag"]) if item["type"] == "boolean" else argv.extend([item["flag"], str(value)])
    return argv + meta["alwaysAppend"]


def sample(schema):
    if "enum" in schema:
        return schema["enum"][-1]
    return {"boolean": True, "integer": 7, "number": 1.5}.get(schema["type"], "sample text")


class SchemaTest(unittest.TestCase):
    def test_every_tool_is_a_well_formed_description(self):
        names = set()
        for tool in tools():
            with self.subTest(tool=tool["name"]):
                self.assertRegex(tool["name"], r"^[a-zA-Z0-9_-]{1,64}$")
                self.assertNotIn(tool["name"], names)
                names.add(tool["name"])
                self.assertTrue(tool["title"])
                self.assertIn("When to use:", tool["description"])
                schema = tool["inputSchema"]
                self.assertEqual((schema["type"], schema["additionalProperties"]), ("object", False))
                self.assertLessEqual(set(schema["required"]), set(schema["properties"]))
                for name, prop in schema["properties"].items():
                    self.assertRegex(name, r"^[a-z][A-Za-z0-9]*$")
                    self.assertIn(prop["type"], JSON_TYPES)
                    self.assertTrue(prop["description"])
                    if "enum" in prop:
                        self.assertTrue(prop["enum"])
                    if "default" in prop and "enum" in prop:
                        self.assertIn(prop["default"], prop["enum"])

    def test_the_properties_of_a_tool_have_distinct_names(self):
        for command in all_commands():
            names = list(mcp_property_names(command).values())
            with self.subTest(command=command.name):
                self.assertEqual(len(names), len(set(names)))

    def test_an_option_is_named_in_camel_case_and_a_positional_by_what_it_is(self):
        by_name = {t["name"]: t for t in tools()}
        search = by_name["jira_search"]["inputSchema"]["properties"]
        self.assertIn("text", search)
        self.assertEqual(search["match"]["enum"], ["auto", "all", "any"])
        self.assertEqual({"allProjects", "fixVersion", "startAt", "snippets"} - set(search), set())
        update = by_name["jira_update"]["inputSchema"]
        self.assertEqual(update["required"], ["issueKey"])
        self.assertEqual({"ifUnchangedSince", "dryRun", "fieldsJson"} - set(update["properties"]), set())
        self.assertEqual(by_name["jira_import_md_dir"]["inputSchema"]["required"], ["directory"])

    def test_the_total_stays_small_enough_to_load_into_a_context(self):
        read = json.dumps(mcp_tools(all_commands(), __version__, only="read"), ensure_ascii=False)
        self.assertLess(len(read), 45_000)
        for tool in tools():
            self.assertLess(len(json.dumps(tool, ensure_ascii=False)), 7_500, tool["name"])


class WhatIsOfferedTest(unittest.TestCase):
    def test_what_is_for_the_person_is_never_offered(self):
        offered = {tool["name"] for tool in tools()}
        for name in FOR_THE_PERSON:
            self.assertNotIn("jira_" + name.replace("-", "_"), offered)
        self.assertTrue(MCP_EXCLUDED <= {command.name for command in all_commands()})  # no stale name in the list
        self.assertEqual(set(FOR_THE_PERSON), set(MCP_EXCLUDED))

    def test_the_default_set_changes_nothing_in_jira(self):
        by_name = {command.name: command for command in all_commands()}
        for tool in tools("read"):
            with self.subTest(tool=tool["name"]):
                command = by_name[tool["_meta"]["jira"]["command"][1]]
                self.assertFalse(command.writes)
                if command.name in MCP_FILES:
                    continue  # it writes files, not issues: see the next test
                self.assertTrue(tool["annotations"]["readOnlyHint"])
                self.assertNotIn("destructiveHint", tool["annotations"])  # meaningless for a read-only tool

    def test_what_writes_files_says_so_and_is_not_read_only(self):
        by_name = {tool["name"]: tool for tool in tools("read")}
        self.assertEqual(MCP_FILES, {"pull", "export"})
        for name in MCP_FILES:
            tool = by_name["jira_" + name]
            with self.subTest(tool=tool["name"]):
                self.assertFalse(tool["annotations"]["readOnlyHint"])
                self.assertTrue(tool["annotations"]["destructiveHint"])  # `--update`, `--out` and a file that exists are overwritten
                self.assertTrue(tool["annotations"]["idempotentHint"])
                self.assertIn("Does not change Jira. Writes files", tool["description"])

    def test_an_answer_that_would_show_people_is_asked_without_them(self):
        by_name = {tool["name"]: tool["_meta"]["jira"] for tool in tools("read")}
        self.assertEqual(by_name["jira_issues"]["alwaysAppend"], ["--json", "--no-assignee"])
        self.assertEqual(by_name["jira_ping"]["alwaysAppend"], ["--json", "--quiet"])
        self.assertEqual(by_name["jira_search"]["alwaysAppend"], ["--json"])
        for command, flag in (("issues", "--no-assignee"), ("ping", "--quiet")):  # the flags exist, or the call would fail
            self.assertIn(flag, {name for arg in find_command(command).args for name in arg.names})
        self.assertIn("JIRA_HIDE_USERS=true", mcp_tools(all_commands(), __version__)["_meta"]["jira"]["invoke"])

    def test_no_tool_prints_a_file_it_is_given(self):
        self.assertNotIn("jira_wiki", {tool["name"] for tool in tools()})

    def test_the_write_set_is_all_writes_and_says_how_far_they_go(self):
        for tool in tools("write"):
            with self.subTest(tool=tool["name"]):
                annotations = tool["annotations"]
                self.assertFalse(annotations["readOnlyHint"])
                self.assertIsInstance(annotations["destructiveHint"], bool)
                self.assertIsInstance(annotations["idempotentHint"], bool)
                self.assertIn("Changes Jira", tool["description"])
        self.assertEqual(len(tools("all")), len(tools("read")) + len(tools("write")))

    def test_a_write_tool_says_exactly_how_far_it_goes(self):
        by_name = {tool["name"]: tool["annotations"] for tool in tools("write")}
        for name in ("update", "update_json", "transition", "import_md", "import_md_dir", "import_csv", "push"):
            self.assertTrue(by_name["jira_" + name]["destructiveHint"], name)  # it overwrites or moves
        for name in ("create", "create_json", "comment", "link"):
            self.assertFalse(by_name["jira_" + name]["destructiveHint"], name)  # it only adds
        for name in ("update", "update_json", "push"):
            self.assertTrue(by_name["jira_" + name]["idempotentHint"], name)  # the same call again changes nothing more
        for name in ("create", "comment", "link", "transition", "import_md"):
            self.assertFalse(by_name["jira_" + name]["idempotentHint"], name)

    def test_every_command_that_changes_jira_is_classified(self):
        writing = {command.name for command in all_commands() if command.writes and command.name not in MCP_EXCLUDED}
        self.assertEqual(writing, MCP_DESTRUCTIVE | ADDITIVE, "a new command that writes needs a place in MCP_DESTRUCTIVE or ADDITIVE")
        self.assertTrue(MCP_IDEMPOTENT <= writing)
        self.assertTrue(MCP_LOCAL <= {command.name for command in all_commands()})
        self.assertTrue(MCP_LOCAL.isdisjoint(writing))

    def test_a_tool_says_whether_it_talks_to_jira(self):
        by_name = {tool["name"]: tool for tool in tools()}
        self.assertFalse(by_name["jira_version"]["annotations"]["openWorldHint"])
        self.assertTrue(by_name["jira_search"]["annotations"]["openWorldHint"])

    def test_the_whole_answer_names_its_version_its_set_and_how_to_run_a_tool(self):
        answer = mcp_tools(all_commands(), __version__, only="write")
        meta = answer["_meta"]["jira"]
        self.assertEqual((meta["version"], meta["only"]), (__version__, "write"))
        self.assertIn("jira NAME", meta["invoke"])
        self.assertIn("non-zero exit code", meta["invoke"])
        self.assertEqual([item["code"] for item in meta["exitCodes"]][:2], [0, 1])
        with self.assertRaises(ValueError):
            mcp_tools(all_commands(), __version__, only="some")


class CommandLineTest(unittest.TestCase):
    """The description is only good if a call made from it is a command line that the parser reads as meant."""

    def check(self, tool, arguments):
        command = find_command(tool["_meta"]["jira"]["command"][1])
        argv = argv_from(tool, arguments)
        namespace = build_parser(command).parse_args(argv[1:])
        names = mcp_property_names(command)
        for arg in command.args:
            wanted = arguments.get(names[arg.names[0]])
            if wanted is None or wanted is False:
                continue
            self.assertEqual(getattr(namespace, arg.dest), wanted, f"{tool['name']}: {arg.names[0]} in {argv}")
        self.assertEqual(bool(getattr(namespace, "json", False)), command.json_output)

    def test_a_call_with_only_what_is_required_becomes_a_command_line(self):
        for tool in tools():
            with self.subTest(tool=tool["name"]):
                properties = tool["inputSchema"]["properties"]
                self.check(tool, {name: sample(properties[name]) for name in tool["inputSchema"]["required"]})

    def test_a_call_with_every_property_becomes_a_command_line(self):
        for tool in tools():
            with self.subTest(tool=tool["name"]):
                self.check(tool, {name: sample(schema) for name, schema in tool["inputSchema"]["properties"].items()})

    def test_the_meta_lists_every_property_once_and_in_the_order_of_the_command(self):
        for tool in tools():
            with self.subTest(tool=tool["name"]):
                listed = [item["property"] for item in tool["_meta"]["jira"]["arguments"]]
                self.assertEqual(listed, list(tool["inputSchema"]["properties"]))
                for item in tool["_meta"]["jira"]["arguments"]:
                    self.assertEqual(item["flag"] is None, item["positional"])
                    self.assertEqual(item["type"], tool["inputSchema"]["properties"][item["property"]]["type"])

    def test_a_search_from_a_call_is_what_a_person_would_type(self):
        search = {t["name"]: t for t in tools()}["jira_search"]
        argv = argv_from(search, {"text": "monthly report", "project": "PROJ", "snippets": 5, "match": "any", "label": "adr"})
        self.assertEqual(argv, ["search", "monthly report", "--project", "PROJ", "--label", "adr", "--match", "any", "--snippets", "5", "--json"])

    def test_a_false_flag_is_left_out(self):
        update = {t["name"]: t for t in tools()}["jira_update"]
        argv = argv_from(update, {"issueKey": "PROJ-1", "summary": "New", "dryRun": True})
        self.assertEqual(argv, ["update", "PROJ-1", "--summary", "New", "--dry-run", "--json"])
        self.assertNotIn("--dry-run", argv_from(update, {"issueKey": "PROJ-1", "summary": "New", "dryRun": False}))


class SyntheticCommandTest(unittest.TestCase):
    """What the real commands do not happen to contain: a name used twice, a flag named in a text."""

    def command(self):
        return Command(
            name="demo-run",
            group="Tools",
            summary="Run a demo",
            handler=lambda ctx, args: 0,
            when="Use it with --dry-run or `--dry-run` first.",
            description="Changes a file. Add `--dry-run` to see it, or --dry-run again.",
            args=(Arg(("file",), "The file.", required=True, metavar="FILE"), Arg(("--file",), "Another file."), Arg(("--dry-run",), "Preview only.", kind="flag")),
            writes=True,
        )

    def test_a_name_that_would_occur_twice_gets_a_suffix(self):
        self.assertEqual(mcp_property_names(self.command()), {"file": "file", "--file": "fileOption", "--dry-run": "dryRun"})
        self.assertEqual(sorted(mcp_tool(self.command())["inputSchema"]["properties"]), ["dryRun", "file", "fileOption"])

    def test_a_flag_in_a_text_becomes_its_property_with_or_without_backticks(self):
        description = mcp_tool(self.command())["description"]
        self.assertNotIn("--dry-run", description)
        self.assertIn("Use it with `dryRun` or `dryRun` first.", description)
        self.assertIn("Add `dryRun` to see it, or `dryRun` again.", description)

    def test_a_write_tool_with_a_dry_run_says_to_use_it_first(self):
        self.assertTrue(mcp_tool(self.command())["description"].endswith("Changes Jira. Run it with dryRun first and show the result."))
        update = {tool["name"]: tool for tool in tools("write")}["jira_update"]["description"]
        self.assertTrue(update.endswith("Changes Jira. Run it with dryRun first and show the result."))


class TextsTest(unittest.TestCase):
    def test_a_text_names_the_properties_and_tools_a_reader_of_the_description_has(self):
        for tool in tools():
            text = tool["description"] + " ".join(prop["description"] for prop in tool["inputSchema"]["properties"].values())
            with self.subTest(tool=tool["name"]):
                for item in tool["_meta"]["jira"]["arguments"]:
                    if item["flag"]:
                        self.assertIsNone(
                            re.search(r"(?<![\w-])" + re.escape(item["flag"]) + r"(?![\w-])", text),
                            f"{item['flag']} is a command line flag, here it is `{item['property']}`",
                        )
        offered = {tool["name"] for tool in tools()}
        for tool in tools():
            for mention in re.findall(r"`(jira_[a-z_]+)`", tool["description"]):
                if mention in FRONT_MATTER_LINES:
                    continue  # a line of a Markdown ticket, not a tool
                self.assertIn(mention, offered, f"{tool['name']} names {mention}")

    def test_the_search_tool_tells_when_to_use_it_and_what_comes_back(self):
        description = {t["name"]: t for t in tools()}["jira_search"]["description"]
        for phrase in ("When to use: Use first", "`snippets`", "Returns JSON:", "epicKey", "allWords", "`jira_issue`", "`jira_issues`"):
            self.assertIn(phrase, description)

    def test_every_command_says_when_to_use_it_in_the_help_and_the_catalog(self):
        entries = {entry["name"]: entry for entry in catalog(all_commands(), __version__)["commands"]}
        for command in all_commands():
            with self.subTest(command=command.name):
                self.assertTrue(command.when, "write one sentence: when to use it, and when another command is better")
                self.assertEqual(entries[command.name]["whenToUse"], command.when)
                self.assertIn("When to use:", render_command_help(command))


class CommandsCommandTest(unittest.TestCase):
    def test_the_default_is_the_read_only_set_as_json(self):
        result = run_cli("commands", "--format", "mcp")
        self.assertEqual(result.code, 0, result.err)
        answer = json.loads(result.out)
        names = [tool["name"] for tool in answer["tools"]]
        self.assertIn("jira_search", names)
        self.assertNotIn("jira_update", names)
        self.assertEqual(answer["_meta"]["jira"]["only"], "read")

    def test_only_write_and_only_all(self):
        write = json.loads(run_cli("commands", "--format", "mcp", "--only", "write").out)["tools"]
        everything = json.loads(run_cli("commands", "--format", "mcp", "--only", "all").out)["tools"]
        self.assertEqual({tool["name"] for tool in write} & {"jira_update", "jira_create", "jira_push"}, {"jira_update", "jira_create", "jira_push"})
        self.assertNotIn("jira_search", [tool["name"] for tool in write])
        self.assertEqual(len(everything), len(json.loads(run_cli("commands", "--format", "mcp").out)["tools"]) + len(write))

    def test_json_format_is_the_catalog_and_the_table_stays_the_default(self):
        catalog_by_flag = json.loads(run_cli("commands", "--json").out)
        self.assertEqual(json.loads(run_cli("commands", "--format", "json").out), catalog_by_flag)
        self.assertIn("whenToUse", catalog_by_flag["commands"][0])
        self.assertEqual(run_cli("commands").out.splitlines()[0].split(), ["COMMAND", "GROUP", "SUMMARY"])

    def test_a_wrong_format_or_set_is_a_usage_error(self):
        self.assertEqual(run_cli("commands", "--format", "xml").code, 2)
        self.assertEqual(run_cli("commands", "--format", "mcp", "--only", "some").code, 2)

    def test_the_help_of_a_command_says_when_to_use_it(self):
        self.assertIn("When to use: Use first when you do not know which issue holds the information", run_cli("help", "search").out.replace("\n", " ").replace("  ", " "))


if __name__ == "__main__":
    unittest.main()
