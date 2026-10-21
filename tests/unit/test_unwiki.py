"""Jira wiki markup to Markdown: what converts, what is reported as dropped, and that a hostile text costs little."""
import re
import time
import unittest

from jira_dc_cli.domain.unwiki import MAX_INLINE_CHARS, wiki_to_markdown, wiki_to_text
from jira_dc_cli.domain.wiki import markdown_to_wiki


def md(text):
    return wiki_to_markdown(text).text


def dropped(text):
    return list(wiki_to_markdown(text).dropped)


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


class BlocksTest(unittest.TestCase):
    def test_headings(self):
        self.assertEqual(md("h1. One\nh3. Three *bold*\n"), "# One\n### Three **bold**\n")
        self.assertEqual(md("h1.Title\nh7. x\n"), "h1.Title\nh7. x\n")  # Jira needs a space, and has six levels

    def test_bullets_and_numbers_nest_and_the_numbers_count(self):
        self.assertEqual(md("* a\n** b\n*** c\n* d\n"), "- a\n  - b\n    - c\n- d\n")
        self.assertEqual(md("# one\n## sub\n## sub two\n# two\n"), "1. one\n  1. sub\n  2. sub two\n2. two\n")
        self.assertEqual(md("# a\ntext\n# b\n"), "1. a\ntext\n1. b\n")  # a text between two items starts the list again

    def test_a_dash_is_a_bullet_too_and_a_bold_word_is_not_a_list(self):
        self.assertEqual(md("- one\n-- two\n*bold* word\n"), "- one\n  - two\n**bold** word\n")

    def test_a_mixed_list_is_reported(self):
        self.assertEqual(md("# one\n#* two\n"), "1. one\n  - two\n")
        self.assertEqual(dropped("# one\n#* two\n"), ["mixed list"])

    def test_quotes_and_rules(self):
        self.assertEqual(md("bq. quoted\n----\n"), "> quoted\n---\n")
        self.assertEqual(md("{quote}\nfirst\n\nsecond\n{quote}\n"), "> first\n>\n> second\n")
        self.assertEqual(dropped("{quote}\nx\n{quote}\n"), ["quote block"])

    def test_code_blocks_keep_their_text_and_language(self):
        self.assertEqual(md("{code:python}\nprint('*x*')\n# not a heading\n{code}\n"), "```python\nprint('*x*')\n# not a heading\n```\n")
        self.assertEqual(md("{code}\nplain\n{code}\n"), "```\nplain\n```\n")
        self.assertEqual(md("{code:title=Demo|language=java}\nint x;\n{code}\n"), "```java\nint x;\n```\n")
        self.assertEqual(dropped("{code:title=Demo|language=java}\nint x;\n{code}\n"), ["code title"])

    def test_a_code_block_that_holds_a_fence_gets_a_longer_one_and_an_unclosed_one_is_closed(self):
        fence = "`" * 3
        self.assertEqual(md("{code}\n" + fence + "\n{code}\n"), "`" * 4 + "\n" + fence + "\n" + "`" * 4 + "\n")
        self.assertEqual(md("{code}\nlost\n"), "```\nlost\n```\n")

    def test_noformat_is_reported_because_it_comes_back_as_code(self):
        self.assertEqual(md("{noformat}\nx\n{noformat}\n"), "```\nx\n```\n")
        self.assertEqual(dropped("{noformat}\nx\n{noformat}\n"), ["noformat"])

    def test_a_panel_keeps_its_text_and_its_title(self):
        self.assertEqual(md("{panel:title=Note|borderStyle=dashed}\nSee *this*.\n{panel}\n"), "> **Note**\n> See **this**.\n")
        self.assertEqual(dropped("{panel}\nx\n{panel}\n"), ["panel"])

    def test_tables(self):
        self.assertEqual(md("||Name||Role||\n|Ann|Dev|\n|Bob|[QA|http://x.test]|\n"), "| Name | Role |\n| --- | --- |\n| Ann | Dev |\n| Bob | [QA](http://x.test) |\n")
        self.assertEqual(md("|a|b|\n|c|d|\n"), "|  |  |\n| --- | --- |\n| a | b |\n| c | d |\n")
        self.assertEqual(dropped("|a|b|\n"), ["table"])


class InlineTest(unittest.TestCase):
    def test_emphasis(self):
        self.assertEqual(md("*bold* _italic_ -gone- {{code}}\n"), "**bold** *italic* ~~gone~~ `code`\n")

    def test_marks_nest(self):
        self.assertEqual(md("*bold _and italic_ bold*\n"), "**bold *and italic* bold**\n")
        self.assertEqual(md("*_both_*\n"), "***both***\n")

    def test_a_mark_needs_its_boundaries(self):
        self.assertEqual(md("snake_case_name 2*3*4 a-b-c e-mail\n"), "snake_case_name 2*3*4 a-b-c e-mail\n")
        self.assertEqual(md("* not bold *\n"), "- not bold *\n")  # a list item, and the star is no mark

    def test_code_keeps_its_marks(self):
        self.assertEqual(md("{{*x* _y_}}\n"), "`*x* _y_`\n")

    def test_links_and_images(self):
        self.assertEqual(md("[a link|https://example.com/x] and [~user] and [PROJ-1] and [^file.pdf]\n"), "[a link](https://example.com/x) and [~user] and [PROJ-1] and [^file.pdf]\n")
        self.assertEqual(md("see !diagram.png! and !https://example.com/a!\n"), "see ![](diagram.png) and ![](https://example.com/a)\n")
        self.assertEqual(md("Wow! really! nice!\n"), "Wow! really! nice!\n")

    def test_what_markdown_cannot_carry_back_is_reported(self):
        for text, expected in (
            ("{color:red}warning{color} text\n", ["color"]),
            ("+underlined+ text\n", ["underline"]),
            ("??cited?? text\n", ["citation"]),
            ("x^2^ and H~2~O\n", ["subscript", "superscript"]),
            ("{anchor:top}text\n", ["anchor"]),
            ("{toc}\ntext\n", ["macro toc"]),
            ("!img.png|width=100!\n", ["image options"]),
            ("[text|http://x.test|tip]\n", ["link title"]),
            ("[text|a b]\n", ["link"]),
        ):
            with self.subTest(text=text):
                self.assertEqual(dropped(text), expected)
        self.assertEqual(md("{color:red}warning{color} and +under+\n"), "warning and under\n")

    def test_an_escaped_mark_stays_literal(self):
        self.assertEqual(md("\\*not bold\\* and \\_x\\_\n"), "\\*not bold\\* and \\_x\\_\n")

    def test_a_null_byte_cannot_hide_a_mark_or_break_the_pieces(self):
        self.assertEqual(md("*a\x00b* {{c}}\n"), "**a b** `c`\n")


class WholeTextTest(unittest.TestCase):
    def test_empty_and_line_endings(self):
        self.assertEqual(md(""), "")
        self.assertEqual(md(None), "")
        self.assertEqual(md("h1. T\r\n* a\r\n"), "# T\n- a\n")

    def test_blank_lines_stay(self):
        self.assertEqual(md("a\n\nb\n"), "a\n\nb\n")

    def test_the_dropped_list_is_sorted_and_without_doubles(self):
        self.assertEqual(dropped("+a+ +b+ {color:red}c{color}\n{toc}\n"), ["color", "macro toc", "underline"])


class RoundTripTest(unittest.TestCase):
    """What Markdown carries comes back as the same Jira text, so that a copy in a file can be pushed."""

    CORPUS = {
        "heading and text": "h1. Title\n\nPlain paragraph.\n",
        "bullets": "* a\n** b\n*** c\n* d\n",
        "numbers": "# one\n## two\n# three\n",
        "inline": "*bold* _italic_ -strike- {{code}} [text|http://x.test] !img.png!\n",
        "mention": "Ask [~user] about [PROJ-1].\n",
        "code": "{code:python}\nprint(1)\n{code}\n",
        "quote": "bq. quoted text\n",
        "rule": "text\n----\nmore\n",
        "user story": "h2. User story\n\nAs an administrator, I want the *report* scheduled, so that it arrives on time.\n\nh2. Acceptance criteria\n\n# *GIVEN* a user, *WHEN* they open the report, *THEN* they see this month.\n# *GIVEN* none, *WHEN* x, *THEN* y.\n",
    }

    def test_corpus(self):
        for name, wiki in self.CORPUS.items():
            with self.subTest(name=name):
                converted = wiki_to_markdown(wiki)
                self.assertEqual(converted.dropped, ())
                self.assertEqual(norm(markdown_to_wiki(converted.text)), norm(wiki))

    def test_what_does_not_come_back_is_always_reported(self):
        for name, wiki in {
            "noformat": "{noformat}\nx\n{noformat}\n",
            "quote block": "{quote}\nx\n{quote}\n",
            "panel": "{panel}\nx\n{panel}\n",
            "table": "||a||b||\n|c|d|\n",
            "mixed list": "# a\n#* b\n",
            "color": "{color:red}x{color}\n",
            "underline": "+x+\n",
        }.items():
            with self.subTest(name=name):
                converted = wiki_to_markdown(wiki)
                if norm(markdown_to_wiki(converted.text)) != norm(wiki):
                    self.assertTrue(converted.dropped, f"{name} changes on the way back and is not reported")


class WikiToTextTest(unittest.TestCase):
    def test_the_words_without_markup(self):
        text = "h2. Goal\n\nAs a *user* I want [reports|http://x.test] with {{code}} and !img.png!.\n\n* one\n* two\n# first\n"
        self.assertEqual(wiki_to_text(text), "Goal\nAs a user I want reports with code and .\none\ntwo\nfirst")

    def test_code_blocks_keep_their_words_and_tables_become_words(self):
        self.assertEqual(wiki_to_text("{code}\nprint(1)\n{code}\n||a||b||\n|c|d|\n"), "print(1)\na b\nc d")

    def test_nothing_gives_nothing(self):
        self.assertEqual(wiki_to_text(""), "")
        self.assertEqual(wiki_to_text("\n\n"), "")


class HostileInputTest(unittest.TestCase):
    """Jira limits a text field to 32,767 characters, but the converter does not rely on it."""

    LIMIT = 3.0

    def timed(self, text):
        started = time.perf_counter()
        wiki_to_markdown(text)
        wiki_to_text(text)
        return time.perf_counter() - started

    def test_marks_that_never_close(self):
        for unit in ("*a ", "_a ", "-a ", "+a ", "{{a ", "[a|", "!a.png ", "{color:red}", "^a ", "~a ", "??a "):
            with self.subTest(unit=unit):
                self.assertLess(self.timed(unit * 80_000), self.LIMIT)

    def test_a_long_line_of_pipes_brackets_and_braces(self):
        for unit in ("|", "||a", "[", "{", "{code", "{{", "]|["):
            with self.subTest(unit=unit):
                self.assertLess(self.timed(("|" + unit) * 60_000 + "|"), self.LIMIT)

    def test_many_short_lines(self):
        self.assertLess(self.timed("* a\n" * 200_000), self.LIMIT)
        self.assertLess(self.timed("h1. a\n" * 200_000), self.LIMIT)
        self.assertLess(self.timed("{code}\n" * 200_000), self.LIMIT)

    def test_long_runs_of_blanks(self):
        size = 40_000
        blanks = " " * size
        for name, text in {
            "a heading": "h2. Title" + blanks + "end",
            "a heading of blanks": "h2." + blanks,
            "tabs in a heading": "h2. a" + "\t" * size + "b",
            "a list item": "* a" + blanks + "b",
            "a table cell": "|a" + blanks + "|b|",
            "pipes among blanks": ("|" + blanks) * 3,
            "a code line": "{code}\n" + blanks + "\n{code}",
            "a quote": "bq. a" + blanks + "b",
            "a link text of blanks": "[" + blanks + "|x]",
            "a rule of blanks": "----" + blanks,
        }.items():
            with self.subTest(name=name):
                self.assertLess(self.timed(text), self.LIMIT)

    def test_a_heading_keeps_its_text_and_loses_only_its_trailing_blanks(self):
        self.assertEqual(wiki_to_markdown("h2. Title \t \nText\n").text, "## Title\nText\n")
        self.assertEqual(wiki_to_markdown("h3.   spaced   out   \nText\n").text, "### spaced   out\nText\n")

    def test_a_text_beyond_the_inline_limit_keeps_its_blocks_and_says_so(self):
        text = "h1. Title\n" + "*not converted* " * (MAX_INLINE_CHARS // 10)
        converted = wiki_to_markdown(text)
        self.assertTrue(converted.text.startswith("# Title"))
        self.assertIn("*not converted*", converted.text)
        self.assertNotIn("**", converted.text)  # a text this large is not searched for marks: it would be slow
        self.assertEqual(converted.dropped, ("large text",))


if __name__ == "__main__":
    unittest.main()
