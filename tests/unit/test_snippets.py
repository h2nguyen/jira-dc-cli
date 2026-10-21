"""The passage of an issue around the words that were searched for."""
import unittest

from jira_dc_cli.domain.searchterms import Term, parse_terms
from jira_dc_cli.domain.snippets import LEAD, WIDTH, find_passages, lead, matched_terms

TEXT = (
    "Background ACME builds its monthly reports from the sales figures of each team. Every ACME report needs its own template and its own team membership. "
    + "Filler sentence number one is here to push things apart. " * 12
    + "Steps: open the Dashboard self-service portal and choose the template for a new report request. "
    + "More filler follows here and there and everywhere. " * 12
    + "Recommended members: the product owner."
)


def terms(text):
    return parse_terms(text).used


class PassagesTest(unittest.TestCase):
    def test_the_passage_is_around_the_words_and_says_which_were_found(self):
        result = find_passages(TEXT, terms("Dashboard portal"))
        self.assertIn("Dashboard self-service portal", result.text)
        self.assertEqual(result.matched, ("Dashboard", "portal"))
        self.assertTrue(result.text.startswith("\u2026"))  # the text goes on before it

    def test_a_passage_is_about_one_window_wide(self):
        result = find_passages(TEXT, terms("Dashboard"), count=1)
        self.assertLessEqual(len(result.text), WIDTH + 12)  # the window and the ellipses

    def test_the_window_that_holds_most_different_words_wins(self):
        result = find_passages(TEXT, terms("report request"), count=1)
        self.assertIn("new report request", result.text)  # both words together, not the early `report` alone

    def test_a_second_window_appears_only_when_it_shows_something_new(self):
        both_in_one = find_passages(TEXT, terms("Dashboard portal"), count=2)
        self.assertEqual(both_in_one.text.count("\u2026"), 2)  # one window: an ellipsis before and after, none between
        two_places = find_passages(TEXT, terms("ACME owner"), count=2)
        self.assertIn("ACME builds", two_places.text)
        self.assertIn("product owner", two_places.text)
        self.assertIn(" \u2026 ", two_places.text)  # two windows, joined by an ellipsis

    def test_a_second_window_that_only_repeats_what_the_first_shows_is_left_out(self):
        text = "alpha beta sit together here. " + "Filler goes on and on. " * 20 + "Later alpha comes again, and beta too. " + "More filler. " * 20
        result = find_passages(text, terms("alpha beta"), count=2)
        self.assertEqual(result.text.count(" \u2026 "), 0)  # one window: nothing joins two of them
        self.assertEqual(result.text.count("sit together"), 1)
        self.assertNotIn("comes again", result.text)

    def test_two_windows_never_show_the_same_words_twice(self):
        text = "alpha " + "x " * 40 + "beta " + "y " * 65 + "gamma " + "z " * 100
        result = find_passages(text, terms("alpha beta gamma"), count=2)
        self.assertEqual(result.text.count("alpha"), 1)
        self.assertEqual(result.text.count("beta"), 1)  # a window that overlaps the first is not shown as well
        self.assertEqual(result.matched, ("alpha", "beta", "gamma"))  # but the words are still reported

    def test_stems_match_in_both_directions_of_the_plural(self):
        self.assertEqual(find_passages(TEXT, terms("templates")).matched, ("templates",))
        self.assertIn("own template", find_passages(TEXT, terms("templates")).text)
        self.assertEqual(find_passages("The role is set.", terms("roles")).matched, ("roles",))
        self.assertEqual(find_passages("Exporting a report.", terms("export")).matched, ("export",))

    def test_a_phrase_must_stand_together(self):
        self.assertEqual(find_passages(TEXT, [Term("monthly reports", True)]).matched, ('"monthly reports"',))
        self.assertEqual(find_passages(TEXT, [Term("reports monthly", True)]).matched, ())

    def test_wildcards(self):
        self.assertEqual(find_passages(TEXT, terms("memb*")).matched, ("memb*",))
        self.assertEqual(find_passages(TEXT, terms("*ership")).matched, ("*ership",))

    def test_case_does_not_matter(self):
        self.assertEqual(find_passages(TEXT, terms("dashboard PORTAL")).matched, ("dashboard", "PORTAL"))

    def test_a_word_that_is_not_there_is_not_reported_and_no_match_gives_nothing(self):
        self.assertEqual(find_passages(TEXT, terms("Dashboard nonexistent")).matched, ("Dashboard",))
        self.assertEqual((find_passages(TEXT, terms("nonexistent")).text, find_passages(TEXT, terms("nonexistent")).matched), ("", ()))
        self.assertEqual(find_passages("", terms("Dashboard")).text, "")
        self.assertEqual(find_passages(TEXT, ()).text, "")

    def test_regular_expression_characters_in_a_word_are_just_characters(self):
        for word in ("(a+", "[x", "a.b", "a|b", "\\d", "c++", "^$"):
            with self.subTest(word=word):
                find_passages("text with (a+ and [x and a.b and c++ in it", [Term(word)])

    def test_a_text_with_many_matches_is_handled_in_good_time(self):
        find_passages("a " * 200_000, [Term("a")])

    def test_wildcards_match_whole_words_and_many_of_them_stay_fast(self):
        import time

        text = "Requests for groups and requesting roles"
        for pattern, expected in (("request*", True), ("requ?sts", True), ("*ests", True), ("gr*ps", True), ("g*u*p*s", True), ("grp*", False), ("roles*", True), ("rol?", True), ("ole?", False)):
            with self.subTest(pattern=pattern):
                self.assertEqual(bool(matched_terms(text, [Term(pattern)])), expected)
        started = time.perf_counter()
        for hostile in (Term("a*" * 30 + "Z"), Term("*a" * 30 + "*b")):
            self.assertEqual(matched_terms("a" * 100_000 + "! " + "a" * 30, [hostile]), ())
            self.assertEqual(find_passages(("a" * 5_000 + " ") * 20, [hostile]).matched, ())
        self.assertLess(time.perf_counter() - started, 3.0)

    def test_a_wildcard_in_a_word_with_punctuation_is_a_prefix_match(self):
        self.assertEqual(matched_terms("the ci/cd pipeline", [Term("ci/cd*")]), ("ci/cd*",))

    def test_a_word_that_occurs_often_does_not_hide_one_that_occurs_once(self):
        text = "a " * 600 + "unique"
        result = find_passages(text, [Term("a"), Term("unique")])
        self.assertEqual(result.matched, ("a", "unique"))
        self.assertIn("unique", result.text)  # the window that shows it is among those shown
        many = [Term(f"w{n}") for n in range(12)]
        spread = " ".join(f"w{n} " * 80 for n in range(11)) + " w11"
        self.assertIn("w11", find_passages(spread, many).text)

    def test_the_matched_terms_of_a_title(self):
        self.assertEqual(matched_terms("How to export a monthly report", terms("monthly export charts")), ("monthly", "export"))


class LeadTest(unittest.TestCase):
    def test_the_beginning_of_a_text_is_cut_at_a_word(self):
        text = "word " * 100
        result = lead(text)
        self.assertLessEqual(len(result), LEAD + 2)
        self.assertTrue(result.endswith(" \u2026"))
        self.assertNotIn("wor ", result)

    def test_a_short_text_is_whole_and_whitespace_is_collapsed(self):
        self.assertEqual(lead("a\n\n b\t c"), "a b c")
        self.assertEqual(lead(""), "")

    def test_the_cut_never_lands_inside_a_word(self):
        self.assertEqual(lead("aaaa bbbb cccc dddd eeee ffff", width=12), "aaaa bbbb \u2026")


if __name__ == "__main__":
    unittest.main()
