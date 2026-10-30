"""Jira's rendered HTML as plain text."""
import time
import unittest

from jira_dc_cli.domain.htmltext import html_to_text


class StructureTest(unittest.TestCase):
    def test_paragraphs_and_line_breaks(self):
        self.assertEqual(html_to_text("<p>One</p><p>Two<br/>three</p>"), "One\n\nTwo\nthree")

    def test_bullets_and_numbers_stay_recognisable(self):
        html = "<ul><li>first</li><li>second<ul><li>nested</li></ul></li></ul><ol><li>one</li><li>two</li></ol>"
        self.assertEqual(html_to_text(html), "- first\n- second\n  - nested\n\n1. one\n2. two")

    def test_a_code_block_keeps_its_fences_and_its_lines(self):
        html = '<div class="code panel"><pre>def f():\n    return 1</pre></div><p>after</p>'
        self.assertEqual(html_to_text(html), "```\ndef f():\n    return 1\n```\n\nafter")

    def test_inline_code_gets_backticks_and_raw_braces_show(self):
        self.assertEqual(html_to_text("<p>Use <tt>token</tt> and {{token }} here</p>"), "Use `token` and {{token }} here")

    def test_headings_keep_their_level(self):
        self.assertEqual(html_to_text("<h1>Title</h1><p>text</p><h3>Sub</h3>"), "# Title\n\ntext\n\n### Sub")

    def test_links_images_and_tables(self):
        self.assertEqual(html_to_text('<p>See <a href="https://example.com/x">the page</a> and <a href="#top">top</a>.</p>'), "See the page (https://example.com/x) and top.")
        self.assertEqual(html_to_text('<img src="a.png" alt="a chart"/>'), "[image: a chart]")
        self.assertEqual(html_to_text("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"), "A | B\n1 | 2")

    def test_entities_and_whitespace(self):
        self.assertEqual(html_to_text("<p>  a &amp; b &lt;c&gt;\n  d </p>"), "a & b <c> d")

    def test_scripts_and_styles_are_dropped(self):
        self.assertEqual(html_to_text("<style>p{}</style><p>x</p><script>alert(1)</script>"), "x")

    def test_empty_and_plain_text(self):
        self.assertEqual(html_to_text(""), "")
        self.assertEqual(html_to_text(None), "")
        self.assertEqual(html_to_text("plain"), "plain")

    def test_a_paragraph_inside_a_list_item_stays_with_its_marker(self):
        self.assertEqual(html_to_text("<ul><li><p>one</p></li><li>two</li></ul>"), "- one\n- two")
        self.assertEqual(html_to_text("<ol><li><p>one</p><p>more</p></li><li>two</li></ol>"), "1. one\nmore\n2. two")

    def test_a_paragraph_inside_a_cell_stays_in_its_row(self):
        self.assertEqual(html_to_text("<table><tr><td><p>a</p></td><td><p>b</p></td></tr><tr><td>c</td><td>d</td></tr></table>"), "a | b\nc | d")

    def test_a_cell_that_was_not_closed_does_not_swallow_what_follows(self):
        self.assertEqual(html_to_text("<table><tr><td>a<td>b</table><p>after</p>"), "a | b\n\nafter")

    def test_a_less_than_sign_that_opens_no_tag_is_text(self):
        self.assertEqual(html_to_text("<p>1 < 2 and 3 <= 4 and <b>bold</b></p>"), "1 < 2 and 3 <= 4 and bold")


class HostileInputTest(unittest.TestCase):
    def test_a_large_or_deeply_nested_document_is_converted_in_bounded_time(self):
        hostile = (
            "<ul>" * 5000 + "<li>x" + "</ul>" * 5000,
            "<p>" + "a " * 200_000 + "</p>",
            "<" * 100_000,
            "<li>" * 20_000,
            "<a " * 4000,  # unterminated tags: the parser of the standard library is slow on them
            "<a href=" * 20_000,
            "<div " * 30_000 + ">",
            "<!--" * 20_000,
        )
        for html in hostile:
            started = time.perf_counter()
            html_to_text(html)
            self.assertLess(time.perf_counter() - started, 8.0, html[:20])  # a slow parser needs minutes here; the margin is for a loaded machine

    def test_what_is_beyond_the_limit_is_not_read(self):
        text = html_to_text("<p>start</p>" + "x" * 3_000_000)
        self.assertTrue(text.startswith("start"))
        self.assertLess(len(text), 2_100_000)


if __name__ == "__main__":
    unittest.main()
