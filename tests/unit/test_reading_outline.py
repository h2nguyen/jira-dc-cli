"""The parts of a long text: the outline of its headings, one section, and a cut at the end of a paragraph."""
import time
import unittest

from jira_dc_cli.domain.errors import ValidationError
from jira_dc_cli.domain.outline import LISTED, Cut, find_section, outline, truncate

WIKI = (
    "h1. Goal\n"
    "Ship the *report*.\n"
    "\n"
    "h2. Steps\n"
    "# open the page\n"
    "# click export\n"
    "\n"
    "h2. Result\n"
    "It works.\n"
    "{code}\n"
    "h2. not a heading\n"
    "{code}\n"
    "h3. {{Deep}} part\n"
    "end"
)


def titles(text):
    return [(item.level, item.title) for item in outline(text)]


class OutlineTest(unittest.TestCase):
    def test_wiki_headings_with_their_level_and_plain_titles(self):
        self.assertEqual(titles(WIKI), [(1, "Goal"), (2, "Steps"), (2, "Result"), (3, "Deep part")])

    def test_markdown_headings(self):
        text = "# One\n\ntext\n\n## Two ##\n\n###### Six\n####### seven hashes are text\n#NoSpace is text\n"
        self.assertEqual(titles(text), [(1, "One"), (2, "Two"), (6, "Six")])

    def test_markdown_headings_that_follow_each_other_are_all_headings(self):
        self.assertEqual(titles("# One\n## Two\n### Three\ntext"), [(1, "One"), (2, "Two"), (3, "Three")])

    def test_a_title_is_plain_text(self):
        self.assertEqual(titles("## **Bold** and `code` and [a link](http://example.com)\nx"), [(2, "Bold and code and a link")])
        self.assertEqual(titles("h2. *Bold* _and_ [link|http://example.com]\nx"), [(2, "Bold and link")])

    def test_a_wiki_heading_needs_a_blank_after_the_dot(self):
        self.assertEqual(titles("h2.Title\nh2. Real"), [(2, "Real")])
        self.assertEqual(titles("h7. Seven\nxh2. Not at the start"), [])

    def test_code_blocks_hold_no_heading(self):
        text = "h1. A\n{noformat}\nh2. no\n{noformat}\n```\n# no\n```\n~~~~\n## no\n~~~~\nh2. B\n"
        self.assertEqual(titles(text), [(1, "A"), (2, "B")])

    def test_a_code_block_that_never_closes_swallows_the_rest(self):
        self.assertEqual(titles("h1. A\n{code}\nh2. no\nh2. no"), [(1, "A")])

    def test_a_wiki_numbered_list_is_not_a_set_of_headings(self):
        text = "h2. Steps\n# open\n# click\n## nested\n# done\n\nh2. Result\nok"
        self.assertEqual(titles(text), [(2, "Steps"), (2, "Result")])

    def test_a_markdown_heading_alone_between_wiki_headings_is_a_heading(self):
        self.assertEqual(titles("h1. A\ntext\n## Sub\nmore\n# x\n# y\n"), [(1, "A"), (2, "Sub")])

    def test_without_wiki_headings_a_run_of_hash_lines_are_headings(self):
        self.assertEqual(titles("# one\n# two\n"), [(1, "one"), (1, "two")])

    def test_line_ends_of_every_kind(self):
        self.assertEqual(titles("h1. A\r\ntext\r\nh2. B\rmore\r\n"), [(1, "A"), (2, "B")])

    def test_the_size_of_a_section_counts_its_subsections(self):
        headings = outline(WIKI)
        text, goal = find_section(WIKI, "Goal")
        self.assertEqual((goal.chars, goal.line, goal.end), (len(text), 0, len(WIKI.split("\n"))))
        steps = headings[1]
        self.assertEqual((steps.line, steps.end), (3, 7))
        self.assertEqual(headings[2].end, 14)  # `Result` runs to the end of the text: `Deep part` is a deeper level, so it belongs to it
        self.assertEqual([item.index for item in headings], [1, 2, 3, 4])

    def test_empty_and_odd_input(self):
        for text in ("", None, "no headings at all", "\n\n"):
            self.assertEqual(outline(text), [])
        self.assertEqual(titles("h1. "), [])
        self.assertEqual(titles("# \n"), [])

    def test_hostile_input_is_read_in_linear_time(self):
        started = time.perf_counter()
        for hostile in ("# a" + " " * 200_000 + "b\n", "h2. T\n" * 6000, "{code" * 50_000, "#" * 200_000, "\n".join(f"{'#' * (index % 6 + 1)} t{index}" for index in range(8000))):
            outline(hostile)
        self.assertLess(time.perf_counter() - started, 5.0)


class SectionTest(unittest.TestCase):
    def test_a_section_from_its_heading_to_the_next_one_of_the_same_level(self):
        text, heading = find_section(WIKI, "Steps")
        self.assertEqual(text, "h2. Steps\n# open the page\n# click export")
        self.assertEqual((heading.index, heading.level, heading.title), (2, 2, "Steps"))

    def test_a_section_holds_its_subsections_and_code(self):
        text, _ = find_section(WIKI, "result")
        self.assertEqual(text, "h2. Result\nIt works.\n{code}\nh2. not a heading\n{code}\nh3. {{Deep}} part\nend")

    def test_the_last_section_runs_to_the_end(self):
        self.assertEqual(find_section(WIKI, "deep part")[0], "h3. {{Deep}} part\nend")

    def test_case_and_a_trailing_colon_do_not_matter(self):
        self.assertEqual(find_section("h2. Acceptance Criteria:\nx", "ACCEPTANCE criteria")[1].title, "Acceptance Criteria:")
        self.assertEqual(find_section("## Steps\nx\n## Other\ny", "steps:")[0], "## Steps\nx")

    def test_the_whole_title_wins_over_a_part_of_a_longer_one(self):
        text = "h2. Test\na\nh2. Test plan\nb\nh2. Retest\nc"
        self.assertEqual(find_section(text, "test")[1].title, "Test")

    def test_a_part_of_a_title_is_enough_when_only_one_has_it(self):
        self.assertEqual(find_section(WIKI, "dee")[1].title, "Deep part")

    def test_the_number_of_the_outline_selects_a_heading(self):
        self.assertEqual(find_section(WIKI, "3")[1].title, "Result")
        self.assertEqual(find_section("h1. 2\na\nh1. Two\nb", "2")[1].index, 1)  # a title that is the number wins over the position
        self.assertEqual(find_section(WIKI, "02")[1].title, "Steps")

    def test_a_selector_that_only_looks_like_a_number_is_a_clean_error(self):
        # `isdigit()` is true for these, `int()` refuses them (or, for the long ones, their length): the answer is "no such heading", never a crash
        for selector in ("\u00b2", "\u2460", "\u00bd", "\u0662", "\uff12", "1" * 7, "1" * 5_000, "9" * 4_301, "0", "5", "-1"):
            with self.subTest(selector=selector[:8], length=len(selector)), self.assertRaises(ValidationError) as caught:
                find_section(WIKI, selector)
            self.assertIn("No heading matches", caught.exception.message)
            self.assertIn("1. Goal | 2. Steps | 3. Result | 4. Deep part", caught.exception.hint)

    def test_a_title_made_of_such_characters_is_still_found_by_its_text(self):
        text = "h1. \u00b2\na\nh1. \u2460\nb"
        self.assertEqual(find_section(text, "\u2460")[0], "h1. \u2460\nb")
        self.assertEqual(find_section(text, "\u00b2")[1].index, 1)

    def test_no_match_lists_the_headings(self):
        with self.assertRaises(ValidationError) as caught:
            find_section(WIKI, "nothing")
        self.assertIn("nothing", caught.exception.message)
        self.assertIn("1. Goal | 2. Steps | 3. Result | 4. Deep part", caught.exception.hint)

    def test_several_matches_list_only_those_and_their_numbers(self):
        with self.assertRaises(ValidationError) as caught:
            find_section("h2. Test plan\na\nh2. Test report\nb\nh2. Other\nc", "test")
        self.assertIn("2 headings match", caught.exception.message)
        self.assertIn("1. Test plan | 2. Test report", caught.exception.hint)
        self.assertNotIn("Other", caught.exception.hint)

    def test_the_same_title_twice_is_several_matches(self):
        with self.assertRaises(ValidationError) as caught:
            find_section("h2. Notes\na\nh2. Notes\nb", "notes")
        self.assertIn("1. Notes | 2. Notes", caught.exception.hint)

    def test_a_text_without_headings_has_no_sections(self):
        with self.assertRaises(ValidationError) as caught:
            find_section("just text", "x")
        self.assertIn("no headings", caught.exception.message)

    def test_an_empty_selector_is_refused(self):
        for selector in ("", "   ", "*"):
            with self.subTest(selector=selector), self.assertRaises(ValidationError):
                find_section(WIKI, selector)

    def test_a_long_list_of_headings_is_cut_in_the_error(self):
        text = "\n".join(f"h2. Part {index}\nx" for index in range(LISTED + 5))
        with self.assertRaises(ValidationError) as caught:
            find_section(text, "missing")
        self.assertIn("and 5 more", caught.exception.hint)

    def test_markdown_sections(self):
        text = "# One\nfirst\n## Sub\nsecond\n# Two\nthird"
        self.assertEqual(find_section(text, "one")[0], "# One\nfirst\n## Sub\nsecond")
        self.assertEqual(find_section(text, "sub")[0], "## Sub\nsecond")

    def test_crlf_text_gives_a_clean_section(self):
        self.assertEqual(find_section("h1. A\r\nx\r\nh1. B\r\ny", "a")[0], "h1. A\nx")


class TruncateTest(unittest.TestCase):
    def test_a_text_that_fits_is_unchanged(self):
        self.assertEqual(truncate("short text", 100), Cut("short text", False, 10))
        self.assertEqual(truncate("exactly", 7), Cut("exactly", False, 7))
        self.assertEqual(truncate("", 5), Cut("", False, 0))

    def test_a_cut_ends_at_the_end_of_a_paragraph(self):
        text = "first paragraph\n\nsecond paragraph is longer\n\nthird"
        cut = truncate(text, 40)
        self.assertEqual((cut.text, cut.truncated, cut.total_chars), ("first paragraph", True, len(text)))
        self.assertEqual(truncate(text, 43).text, "first paragraph\n\nsecond paragraph is longer")

    def test_the_cut_never_exceeds_the_limit(self):
        text = "\n\n".join("word " * 30 for _ in range(20))
        for limit in (1, 7, 50, 160, 333, 1000):
            with self.subTest(limit=limit):
                self.assertLessEqual(len(truncate(text, limit).text), limit)

    def test_the_first_paragraph_longer_than_the_limit_is_cut_at_a_line_then_at_a_word(self):
        self.assertEqual(truncate("line one\nline two\nline three", 20).text, "line one\nline two")
        cut = truncate("alpha beta gamma delta epsilon", 17)
        self.assertEqual(cut.text, "alpha beta gamma")
        self.assertTrue(cut.truncated)

    def test_a_word_is_never_cut_unless_the_text_has_no_blank_to_cut_at(self):
        self.assertEqual(truncate("abcdefghij" * 5, 12).text, "abcdefghijab")
        self.assertEqual(truncate("short supercalifragilistic", 14).text, "short")

    def test_a_code_block_is_never_cut_in_the_middle(self):
        text = "intro\n\n{code}\nline 1\n\nline 2\n{code}\n\nafter"
        self.assertEqual(truncate(text, 22).text, "intro")
        self.assertEqual(truncate(text, 37).text, "intro\n\n{code}\nline 1\n\nline 2\n{code}")
        fenced = "intro\n\n```\ncode\n\nmore\n```\n\nafter"
        self.assertEqual(truncate(fenced, 20).text, "intro")
        self.assertEqual(truncate(fenced, 20).closed, "")

    def test_a_text_that_opens_with_a_code_block_is_cut_at_a_line_of_the_block_and_the_block_is_closed(self):
        text = "{code}\n" + "\n".join(f"at line {n}" for n in range(60)) + "\n{code}\n\nWhat happened after the stack trace."
        cut = truncate(text, 100)
        self.assertEqual(cut.text, "{code}\n" + "\n".join(f"at line {n}" for n in range(8)) + "\n{code}")
        self.assertEqual((cut.truncated, cut.closed, cut.total_chars), (True, "{code}", len(text)))
        self.assertLessEqual(len(cut.text), 100)

    def test_every_form_of_block_that_opens_the_text_is_closed_with_its_own_end(self):
        body = "\n".join(f"line {n}" for n in range(30))
        forms = {
            "{code}": ("{code}", "{code}"),
            "{code:java}": ("{code:java}", "{code}"),
            "{noformat}": ("{noformat}", "{noformat}"),
            "```": ("```", "```"),
            "```python": ("```python", "```"),
            "~~~~": ("~~~~", "~~~~"),
            "````": ("````", "````"),
        }
        for name, (opening, closing) in forms.items():
            text = f"{opening}\n{body}\n{closing}\n\nafter"
            with self.subTest(name):
                cut = truncate(text, 60)
                lines = cut.text.split("\n")
                self.assertEqual((lines[0], lines[-1], cut.closed), (opening, closing, closing))
                self.assertGreater(len(lines), 3)
                self.assertLessEqual(len(cut.text), 60)
                self.assertTrue(all(line in text.split("\n") for line in lines), cut.text)  # whole lines of the text, and the end of the block

    def test_a_heading_is_not_enough_to_show_before_a_block_that_does_not_fit(self):
        text = "h2. Trace\n{code}\n" + "\n".join(f"line {n}" for n in range(30)) + "\n{code}"
        cut = truncate(text, 60)
        self.assertEqual(cut.text.split("\n")[:3], ["h2. Trace", "{code}", "line 0"])
        self.assertEqual((cut.text.split("\n")[-1], cut.closed), ("{code}", "{code}"))

    def test_blank_lines_before_the_block_are_not_something_to_show(self):
        cut = truncate("\n\n{code}\n" + "line\n" * 40 + "{code}", 40)
        self.assertEqual((cut.closed, cut.text.count("{code}")), ("{code}", 2))

    def test_the_sweep_over_every_limit_never_leaves_a_block_open_or_a_line_cut(self):
        body = "\n".join(f"line {n}" for n in range(30))
        text = f"{{code}}\n{body}\n{{code}}\n\nafter the block, a sentence."
        lines_of_text = set(text.split("\n"))
        for limit in range(20, len(text)):  # 20: `{code}`, one line and the end of the block
            cut = truncate(text, limit)
            with self.subTest(limit=limit):
                self.assertLessEqual(len(cut.text), limit)
                self.assertEqual(cut.text.count("{code}") % 2, 0, cut.text)
                self.assertTrue(set(cut.text.split("\n")) <= lines_of_text, cut.text)

    def test_a_block_that_fits_is_not_cut_and_what_follows_it_is_cut_at_a_paragraph(self):
        text = "{code}\nx = 1\n{code}\n\nlong paragraph " + "word " * 20
        cut = truncate(text, 40)
        self.assertEqual((cut.text, cut.closed), ("{code}\nx = 1\n{code}", ""))

    def test_a_line_that_is_longer_than_the_limit_is_the_one_case_that_is_cut_inside_a_line(self):
        cut = truncate("{code}\n" + "x" * 50 + "\n{code}", 30)  # not one line of the block fits: the first line is all there is
        self.assertEqual((cut.text, cut.closed, cut.truncated), ("{code}", "", True))
        cut = truncate("{code}\nline 0\n{code}", 13)  # a whole line of the block fits, and no room is left to close it
        self.assertEqual((cut.text, cut.closed), ("{code}\nline 0", ""))
        cut = truncate("{code}\nline 0\n{code}", 11)  # not even that: cut at the word
        self.assertEqual((cut.text, cut.closed), ("{code}\nline", ""))

    def test_a_long_block_is_cut_in_linear_time(self):
        started = time.perf_counter()
        cut = truncate("{code}\n" + "line\n" * 400_000, 1000)
        self.assertEqual((cut.closed, len(cut.text) <= 1000), ("{code}", True))
        self.assertEqual(truncate("```\n" + "x\n" * 400_000, 500_000).closed, "```")
        self.assertLess(time.perf_counter() - started, 3.0)

    def test_a_heading_is_not_left_at_the_end(self):
        text = "h2. Title\n\nbody text is here\n\nh2. Next\n\nmore body text here"
        self.assertEqual(truncate(text, 40).text, "h2. Title\n\nbody text is here")
        self.assertEqual(truncate("# Title\n\nsome long paragraph without end here", 13).text, "# Title\n\nsome")

    def test_the_size_counts_a_line_end_as_one_character(self):
        text = "a\r\n\r\nb\r\n\r\nc"
        self.assertEqual(truncate(text, 4).total_chars, 7)
        self.assertEqual(truncate(text, 100).text, "a\n\nb\n\nc")

    def test_no_limit_means_no_cut(self):
        self.assertEqual(truncate("a\n\nb", 0), Cut("a\n\nb", False, 4))

    def test_unicode_is_counted_in_characters(self):
        cut = truncate("Überschrift ünïcode text\n\nzweiter Absatz", 26)
        self.assertEqual(cut.text, "Überschrift ünïcode text")

    def test_a_long_text_is_cut_in_linear_time(self):
        started = time.perf_counter()
        text = "x " * 400_000
        self.assertLessEqual(len(truncate(text, 1000).text), 1000)
        self.assertLess(time.perf_counter() - started, 3.0)


if __name__ == "__main__":
    unittest.main()
