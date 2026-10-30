"""The Markdown to wiki converter: blanks inside inline code, and `jira wiki --check`."""
import time
import unittest

from jira_dc_cli.domain.wiki import check_markdown, markdown_to_wiki
from tests.support.cli import run_cli


def wiki(markdown: str) -> str:
    return markdown_to_wiki(markdown).rstrip("\n")


class CodeSpanTest(unittest.TestCase):
    def test_a_clean_span_is_as_before(self):
        self.assertEqual(wiki("use `token` here"), "use {{token}} here")

    def test_a_blank_inside_the_backticks_goes_outside_the_braces(self):
        self.assertEqual(wiki("a `token ` b"), "a {{token}}  b")
        self.assertEqual(wiki("a ` token` b"), "a  {{token}} b")
        self.assertEqual(wiki("a ` token ` b"), "a  {{token}}  b")

    def test_no_blank_stands_directly_inside_the_braces_whatever_the_input(self):
        for markdown in ("`x `", "` x`", "` x `", "`\tx\t`", "`  x  y  `", "a`b `c` d`e"):
            with self.subTest(markdown=markdown):
                text = wiki(markdown)
                self.assertNotIn("{{ ", text)
                self.assertNotIn(" }}", text)
                self.assertNotIn("{{\t", text)
                self.assertNotIn("\t}}", text)

    def test_a_span_of_blanks_is_only_blanks(self):
        self.assertEqual(wiki("a ` ` b"), "a   b")
        self.assertNotIn("{{", wiki("a `   ` b"))

    def test_braces_in_code_are_escaped(self):
        self.assertEqual(wiki("`{a: 1}`"), "{{\\{a: 1\\}}}")
        self.assertEqual(wiki("call `f({x})` now"), "call {{f(\\{x\\})}} now")

    def test_bold_around_a_span_and_a_span_at_the_ends_of_a_line(self):
        self.assertEqual(wiki("**see `a `**"), "*see {{a}} *")
        self.assertEqual(wiki("`start ` and `end `"), "{{start}}  and {{end}}")

    def test_inside_a_fence_nothing_changes(self):
        self.assertEqual(wiki("```\n`a ` {b}\n```"), "{code}\n`a ` {b}\n{code}")

    def test_many_spans_are_converted_in_bounded_time(self):
        started = time.perf_counter()
        wiki("` a ` " * 20_000)
        wiki("`" + " " * 30_000 + "`")
        self.assertLess(time.perf_counter() - started, 8.0)  # a quadratic pattern needs minutes here; the margin is for a loaded machine


class CheckTest(unittest.TestCase):
    def messages(self, markdown: str) -> list[tuple[int, str]]:
        return check_markdown(markdown)

    def test_clean_markdown_has_nothing_to_report(self):
        self.assertEqual(self.messages("# Title\n\nSome *text* with `code {x}` and a [link](https://example.com).\n\n- one\n- two\n"), [])

    def test_a_table(self):
        found = self.messages("Intro\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.assertEqual([line for line, _ in found], [3, 4, 5])
        self.assertIn("table", found[0][1])

    def test_a_task_list_item(self):
        self.assertIn("task list", self.messages("- [ ] do it")[0][1])
        self.assertIn("task list", self.messages("* [x] done")[0][1])

    def test_a_reference_link_and_its_definition(self):
        found = self.messages("See [the docs][1].\n\n[1]: https://example.com\n")
        self.assertEqual([line for line, _ in found], [1, 3])

    def test_a_footnote(self):
        self.assertIn("footnote", self.messages("A claim[^1].")[0][1])

    def test_html_but_not_a_placeholder_in_angle_brackets(self):
        self.assertIn("HTML", self.messages("line<br>break")[0][1])
        self.assertEqual(self.messages("pass <name> and a List<String>"), [])

    def test_a_nested_quote(self):
        found = self.messages("> a\n> > b")
        self.assertEqual([line for line, _ in found], [2])
        self.assertIn("nested block quote", found[0][1])

    def test_an_underlined_heading_needs_a_line_of_text_above_it(self):
        found = self.messages("Title\n=====\n")
        self.assertEqual([line for line, _ in found], [2])
        self.assertIn("underlined heading", found[0][1])
        self.assertEqual(self.messages("text\n\n---\n"), [])  # a rule after a blank line

    def test_a_brace_outside_code_can_start_a_macro_and_inside_code_it_cannot(self):
        self.assertIn("macro", self.messages("write {color:red} here")[0][1])
        self.assertEqual(self.messages("write `{color:red}` here"), [])
        self.assertEqual(self.messages("```\n{code}\n```"), [])
        self.assertEqual(self.messages("an escaped \\{ brace"), [])

    def test_the_line_numbers_count_from_one_and_survive_windows_line_ends(self):
        self.assertEqual([line for line, _ in self.messages("ok\r\nok\r\n- [ ] x\r\n")], [3])

    def test_front_matter_is_not_checked_but_its_lines_are_counted(self):
        markdown = "---\ntitle: Release notes\nlabels: [a, b]\nnote: {x}\n---\n\nSee [the docs][1].\n"
        self.assertEqual([line for line, _ in self.messages(markdown)], [7])

    def test_a_rule_after_a_code_fence_is_no_underlined_heading(self):
        self.assertEqual(self.messages("```\ncode\n```\n---\nnext\n"), [])

    def test_a_hostile_line_is_checked_in_bounded_time(self):
        for hostile in ("[" * 32_000, "[^" * 16_000, "<a " * 12_000, "`" * 32_000, "|" * 32_000, "[x][" * 8000):
            started = time.perf_counter()
            check_markdown(hostile + "\n" + hostile)
            self.assertLess(time.perf_counter() - started, 6.0, hostile[:12])

    def test_a_line_too_long_to_check_says_so(self):
        found = self.messages("a" * 6000)
        self.assertEqual([line for line, _ in found], [1])
        self.assertIn("too long to check (6000 characters)", found[0][1])


class WikiCommandCheckTest(unittest.TestCase):
    def test_check_prints_the_conversion_and_warns_on_standard_error(self):
        result = run_cli("wiki", "-", "--check", stdin="# T\n\n| a | b |\n")
        self.assertEqual(result.code, 0)
        self.assertTrue(result.out.startswith("h1. T"))
        self.assertIn("WARNING: line 3: a Markdown table", result.err)

    def test_check_says_when_there_is_nothing(self):
        result = run_cli("wiki", "-", "--check", stdin="Plain text.\n")
        self.assertIn("no markup found", result.err)

    def test_without_check_there_is_no_commentary(self):
        result = run_cli("wiki", "-", stdin="| a | b |\n")
        self.assertEqual(result.err, "")

    def test_a_blank_inside_the_backticks_goes_outside_the_braces(self):
        result = run_cli("wiki", "-", stdin="a `x ` b\n")
        self.assertEqual(result.out, "a {{x}}  b\n")


if __name__ == "__main__":
    unittest.main()
