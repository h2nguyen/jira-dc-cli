"""The Markdown to Jira wiki converter, with the exact output of every construct pinned."""
import re
import time
import unittest
from pathlib import Path

from jira_dc_cli.domain.errors import ValidationError
from jira_dc_cli.domain.wiki import _heading, markdown_to_wiki

ROOT = Path(__file__).resolve().parents[2]

CORPUS = {
    "headings": "# One\n## Two ##\n###### Six\n####### not a heading\n",
    "bullets": "- a\n  - b\n    - c\n* d\n+ e\n\t- tab\n",
    "numbers": "1. one\n  2) two\n    3. three\n",
    "quote": "> quoted *text*\n>no space\n",
    "rules": "text\n---\n***\n___\n- - -\n",
    "code fence": "```python\nprint('*not bold*')\n# not a heading\n```\nafter `code *x*` text\n",
    "unclosed fence": "```\nlost\n",
    "inline": "**bold** and __bold__ and *italic* and _italic_ and ~~gone~~ and `c`\n",
    "mixed": "***both*** **bold *nested* bold** snake_case_name and 2*3*4\n",
    "links": "[a link](https://example.com/x) and ![pic](img.png \"title\") and [rel](docs/a.md#b)\n",
    "unicode": "# Ünïcode ✓\n- Zeilen mit **Umlauten** äöü\n",
    "front matter": "---\nsummary: x\n---\n# Body\ntext\n",
    "empty": "\n\n\n",
    "blank lines": "a\n\n\nb\n\n",
}

# The Jira wiki text each CORPUS entry must convert to, byte for byte.
GOLDEN = {
    "headings": "h1. One\nh2. Two\nh6. Six\n####### not a heading\n",
    "bullets": "* a\n** b\n*** c\n* d\n* e\n** tab\n",
    "numbers": "# one\n## two\n### three\n",
    "quote": "bq. quoted _text_\nbq. no space\n",
    "rules": "text\n----\n----\n----\n----\n",
    "code fence": "{code:python}\nprint('*not bold*')\n# not a heading\n{code}\nafter {{code *x*}} text\n",
    "unclosed fence": "{code}\nlost\n{code}\n",
    "inline": "*bold* and *bold* and _italic_ and _italic_ and -gone- and {{c}}\n",
    "mixed": "_*both*_ **bold _nested_ bold** snake_case_name and 2_3_4\n",
    "links": "[a link|https://example.com/x] and !img.png! and [rel|docs/a.md#b]\n",
    "unicode": "h1. Ünïcode ✓\n* Zeilen mit *Umlauten* äöü\n",
    "front matter": "h1. Body\ntext\n",
    "empty": "\n",
    "blank lines": "a\n\n\nb\n",
}

# What the Markdown files in examples/ convert to; both hold the same ticket text.
GOLDEN_EXAMPLE = (
    "h2. Goal\n\nAllow users to export the monthly report in a reusable file format.\n\n"
    "h2. Acceptance criteria\n\n"
    "* The export action is visible to authorized users.\n"
    "* The exported file contains the same data shown in the report.\n"
    "* Empty and large reports are handled without an error.\n\n"
    "h2. Notes\n\nAdd technical details, links, and dependencies here.\n"
)


class WikiTest(unittest.TestCase):
    def test_headings_lists_and_quotes(self):
        text = "# Title\n\n- one\n  - two\n1. first\n> quote\n"
        self.assertEqual(markdown_to_wiki(text), "h1. Title\n\n* one\n** two\n# first\nbq. quote\n")

    def test_inline_markup(self):
        self.assertEqual(markdown_to_wiki("**b** *i* `c` [l](http://x) ~~s~~\n"), "*b* _i_ {{c}} [l|http://x] -s-\n")

    def test_code_is_left_alone(self):
        self.assertEqual(markdown_to_wiki("```sh\n*x* # y\n```\n"), "{code:sh}\n*x* # y\n{code}\n")

    def test_front_matter_is_dropped(self):
        self.assertEqual(markdown_to_wiki("---\nsummary: x\n---\nBody\n"), "Body\n")
        with self.assertRaises(ValidationError):
            markdown_to_wiki("---\nsummary: x\n")

    def test_windows_line_endings(self):
        self.assertEqual(markdown_to_wiki("---\r\nsummary: x\r\n---\r\n# T\r\n- a\r\n"), "h1. T\n* a\n")

    def test_a_mark_that_holds_code_or_a_link_keeps_them(self):
        self.assertEqual(markdown_to_wiki("**Read the `spec` first** and see **[the guide](https://x.example/g)**.\n"), "*Read the {{spec}} first* and see *[the guide|https://x.example/g]*.\n")
        self.assertEqual(markdown_to_wiki("_see `a` and [b](https://x.example)_\n"), "_see {{a}} and [b|https://x.example]_\n")

    def test_a_nul_in_the_text_is_no_protected_piece(self):
        self.assertEqual(markdown_to_wiki("a\x00JIRA0\x00b `c`\n"), "a JIRA0 b {{c}}\n")

    def test_what_is_a_fence_and_what_is_not(self):
        self.assertEqual(markdown_to_wiki("```\nx\n```\n"), "{code}\nx\n{code}\n")
        self.assertEqual(markdown_to_wiki("  ```  js  \nx\n```\n"), "{code:js}\nx\n{code}\n")
        for line in ("```` four", "``` a`b", "```x`"):
            with self.subTest(line=line):
                self.assertNotIn("{code", markdown_to_wiki(line + "\nx\n"))


class HeadingTest(unittest.TestCase):
    """`_heading` answers as `^(#{1,6})\\s+(.+?)\\s*#*\\s*$` did, which was cubic on a long run of blanks."""

    OLD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

    def test_the_same_lines_with_the_same_titles(self):
        pieces = ["#", "##", "######", "#######", " ", "  ", "\t", "a", "b c", "#a", "a#", "a ##", "# #", "##  ", "x\u00a0y", "\u00a0"]
        lines = {"".join(parts) for first in pieces for second in pieces for third in pieces for parts in [(first, second, third)]}
        lines |= {"# Title", "# Title ##", "## Two ##  ", "###### Six", "# ", "#  ", "#\t", "# a # b #", "#   ###", "# ###", "#x", "####### no"}
        self.assertGreater(len(lines), 1000)
        for line in sorted(lines):
            with self.subTest(line=line):
                old = self.OLD.match(line)
                self.assertEqual(_heading(line), None if old is None else (len(old.group(1)), old.group(2)))


class HostileTest(unittest.TestCase):
    """A description of 32,767 characters may be anything: the converter answers in a moment."""

    LIMIT = 3.0

    def timed(self, text):
        started = time.perf_counter()
        markdown_to_wiki(text)
        return time.perf_counter() - started

    def test_runs_of_blanks_and_marks_that_never_close(self):
        size = 40_000
        for name, text in {
            "a heading with blanks inside": "# a" + " " * size + "b",
            "a heading of blanks": "#" + " " * size,
            "a fence of blanks and a backtick": "```" + " " * size + "`",
            "links that never close": "[a](" * (size // 4),
            "images that never close": "![a](" * (size // 5),
            "a long link target": "[a](" + "b" * size,
            "a long link text": "[" + "a" * size + "](b)",
            "bold that never closes": "**a " * (size // 4),
            "many blank lines": "\n" * size,
            "many headings": "# a\n" * (size // 4),
        }.items():
            with self.subTest(name=name):
                self.assertLess(self.timed(text), self.LIMIT)


class GoldenTest(unittest.TestCase):
    """Every construct converts to exactly the pinned text, so imports keep producing the same wiki markup."""

    def test_corpus(self):
        self.assertEqual(sorted(GOLDEN), sorted(CORPUS))
        for name, text in CORPUS.items():
            with self.subTest(name=name):
                self.assertEqual(markdown_to_wiki(text), GOLDEN[name])
                self.assertNotIn("\x00", markdown_to_wiki(text))

    def test_example_files(self):
        examples = sorted((ROOT / "examples").glob("*.md"))
        self.assertTrue(examples)
        for path in examples:
            with self.subTest(file=path.name):
                self.assertEqual(markdown_to_wiki(path.read_text(encoding="utf-8")), GOLDEN_EXAMPLE)


if __name__ == "__main__":
    unittest.main()
