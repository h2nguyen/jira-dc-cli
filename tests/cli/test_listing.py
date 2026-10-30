"""`--select` and `--format`: the shared behaviour of every command that prints a list."""
import unittest

from jira_dc_cli.adapters.driving.cli.listing import FORMAT, SELECT, cell, known_paths, print_list, selected_json, structured_text
from jira_dc_cli.domain.errors import UsageError

ROWS = [
    {"key": "PROJ-1", "summary": "Export | the report", "status": {"name": "To Do", "category": "new"}, "sprints": [{"name": "S3", "state": "ACTIVE"}], "labels": ["a", "b"]},
    {"key": "PROJ-2", "summary": "Fix", "status": None, "sprints": [], "labels": []},
]
COLUMNS = [("KEY", "key"), ("STATUS", "status.name"), ("SUMMARY", "summary")]


class FakeContext:
    def __init__(self, *, json_mode=False, json_flag=False):
        self.json_mode, self.json_flag = json_mode, json_flag
        self.printed, self.json_printed = [], []

    def print(self, text=""):
        self.printed.append(text)

    def print_json(self, value):
        self.json_printed.append(value)


class Args:
    def __init__(self, select=None, format="table"):
        self.select, self.format = select, format


class SelectTest(unittest.TestCase):
    def test_nothing_selected_leaves_the_document_alone(self):
        document = {"total": 2, "issues": ROWS}
        self.assertIs(selected_json(document, None, "issues"), document)
        self.assertIs(selected_json(document, "  ", "issues"), document)

    def test_a_plain_and_a_dotted_key(self):
        picked = selected_json({"total": 2, "issues": ROWS}, "key,status.name", "issues")
        self.assertEqual(picked["total"], 2)
        self.assertEqual(picked["issues"], [{"key": "PROJ-1", "status": {"name": "To Do"}}, {"key": "PROJ-2", "status": {"name": None}}])

    def test_a_document_that_is_the_list(self):
        self.assertEqual(selected_json(ROWS, "key"), [{"key": "PROJ-1"}, {"key": "PROJ-2"}])

    def test_a_path_through_a_list_of_objects_keeps_the_list(self):
        picked = selected_json(ROWS, "sprints.name", None)
        self.assertEqual(picked, [{"sprints": [{"name": "S3"}]}, {"sprints": []}])
        both = selected_json(ROWS, "sprints.name,sprints.state", None)
        self.assertEqual(both[0], {"sprints": [{"name": "S3", "state": "ACTIVE"}]})

    def test_a_whole_object_can_be_selected(self):
        self.assertEqual(selected_json(ROWS, "status")[0], {"status": {"name": "To Do", "category": "new"}})

    def test_an_unknown_name_is_a_usage_error_that_lists_the_valid_names(self):
        with self.assertRaises(UsageError) as caught:
            selected_json({"issues": ROWS}, "key,nope", "issues")
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("nope", caught.exception.message)
        self.assertIn("status.name", caught.exception.hint)
        self.assertIn("sprints.state", caught.exception.hint)

    def test_an_empty_list_accepts_any_name(self):
        self.assertEqual(selected_json({"issues": []}, "whatever", "issues"), {"issues": []})

    def test_a_document_without_the_list_says_so(self):
        with self.assertRaises(UsageError):
            selected_json({"total": 1}, "key", "issues")

    def test_known_paths(self):
        self.assertEqual(known_paths(ROWS), ["key", "labels", "sprints", "sprints.name", "sprints.state", "status", "status.category", "status.name", "summary"])


class FormatTest(unittest.TestCase):
    def test_cells(self):
        self.assertEqual(cell(None), "")
        self.assertEqual(cell(True), "true")
        self.assertEqual(cell(5.0), "5")
        self.assertEqual(cell(["a", "b"]), "a, b")
        self.assertEqual(cell({"a": 1}), '{"a":1}')
        self.assertEqual(cell("two\nlines\tand tab"), "two lines and tab")

    def test_markdown_escapes_the_pipe(self):
        text = structured_text(ROWS, COLUMNS, None, "md")
        self.assertEqual(
            text.splitlines(),
            ["| KEY | STATUS | SUMMARY |", "| --- | --- | --- |", "| PROJ-1 | To Do | Export \\| the report |", "| PROJ-2 |  | Fix |"],
        )

    def test_tsv_has_a_header_line_and_tabs(self):
        lines = structured_text(ROWS, COLUMNS, None, "tsv").splitlines()
        self.assertEqual(lines[0], "KEY\tSTATUS\tSUMMARY")
        self.assertEqual(lines[1], "PROJ-1\tTo Do\tExport | the report")

    def test_table(self):
        lines = structured_text(ROWS, COLUMNS, None, "table").splitlines()
        self.assertTrue(lines[0].startswith("KEY"))
        self.assertIn("PROJ-1", lines[1])

    def test_select_names_the_columns(self):
        lines = structured_text(ROWS, COLUMNS, "key,status.category", "tsv").splitlines()
        self.assertEqual(lines, ["key\tstatus.category", "PROJ-1\tnew", "PROJ-2\t"])

    def test_select_with_an_unknown_name(self):
        with self.assertRaises(UsageError):
            structured_text(ROWS, COLUMNS, "nope", "md")


class PrintListTest(unittest.TestCase):
    def run_print(self, ctx, args):
        print_list(ctx, args, payload={"issues": ROWS}, rows=ROWS, list_key="issues", columns=COLUMNS, plain=lambda: "PLAIN")
        return ctx

    def test_the_commands_own_text_is_the_default(self):
        ctx = self.run_print(FakeContext(), Args())
        self.assertEqual(ctx.printed, ["PLAIN"])

    def test_json_mode_prints_the_document_with_the_selection(self):
        ctx = self.run_print(FakeContext(json_mode=True), Args(select="key"))
        self.assertEqual(ctx.json_printed, [{"issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}]}])
        self.assertEqual(ctx.printed, [])

    def test_a_format_turns_the_text_into_that_format(self):
        ctx = self.run_print(FakeContext(), Args(format="md"))
        self.assertTrue(ctx.printed[0].startswith("| KEY"))

    def test_select_alone_prints_a_table_of_those_columns(self):
        ctx = self.run_print(FakeContext(), Args(select="key"))
        self.assertTrue(ctx.printed[0].startswith("key"))
        self.assertNotIn("SUMMARY", ctx.printed[0])

    def test_a_format_beats_the_json_default_of_the_environment(self):
        ctx = self.run_print(FakeContext(json_mode=True, json_flag=False), Args(format="tsv"))
        self.assertTrue(ctx.printed[0].startswith("KEY\t"))
        self.assertEqual(ctx.json_printed, [])

    def test_json_and_a_format_on_the_command_line_clash(self):
        with self.assertRaises(UsageError):
            self.run_print(FakeContext(json_mode=True, json_flag=True), Args(format="md"))

    def test_the_shared_arguments_are_described(self):
        self.assertEqual(SELECT.dest, "select")
        self.assertEqual((FORMAT.dest, FORMAT.default, FORMAT.choices), ("format", "table", ("table", "tsv", "md")))


if __name__ == "__main__":
    unittest.main()
