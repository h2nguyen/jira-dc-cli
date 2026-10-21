"""What a person or an agent types into a search becomes words and phrases."""
import unittest

from jira_dc_cli.domain.searchterms import MAX_TERMS, STOP_WORDS, Term, parse_terms, terms_from_jql


def shown(text):
    result = parse_terms(text)
    return [term.display for term in result.used], list(result.ignored)


class ParseTermsTest(unittest.TestCase):
    def test_a_question_becomes_its_meaningful_words_and_says_what_it_left_out(self):
        self.assertEqual(shown("how do I export the monthly report for ACME?"), (["export", "monthly", "report", "ACME"], ["how", "do", "I", "the", "for"]))

    def test_the_same_in_german(self):
        self.assertEqual(shown("wie kann ich den PDF-Export fuer ACME starten"), (["PDF-Export", "ACME", "starten"], ["wie", "kann", "ich", "den", "fuer"]))

    def test_a_phrase_in_quotes_stays_together_also_in_typographic_quotes(self):
        for text in ('"monthly report" ACME', "\u201emonthly report\u201c ACME", "\u201cmonthly report\u201d ACME"):
            with self.subTest(text=text):
                result = parse_terms(text)
                self.assertEqual([(term.text, term.phrase) for term in result.used], [("monthly report", True), ("ACME", False)])
        self.assertEqual(Term("monthly report", True).display, '"monthly report"')

    def test_punctuation_at_the_end_of_a_word_is_not_part_of_it(self):
        self.assertEqual(shown("ACME? roles, (groups) v0.1.0. [TO CLARIFY]"), (["ACME", "roles", "groups", "v0.1.0", "TO", "CLARIFY"], []))

    def test_punctuation_inside_a_word_stays(self):
        self.assertEqual(shown("src/load run_id e-mail C# what's CI/CD"), (["src/load", "run_id", "e-mail", "C#", "what's", "CI/CD"], []))

    def test_wildcards_stay_and_a_lone_symbol_is_no_term(self):
        self.assertEqual(shown("rol* *rule = - ! ?"), (["rol*", "*rule"], []))

    def test_abbreviations_in_capitals_are_not_filler_but_the_pronouns_are(self):
        self.assertEqual(shown("IT department and the US team"), (["IT", "department", "US", "team"], ["and", "the"]))
        self.assertEqual(shown("it is for us"), (["it", "is", "for", "us"], []))  # only filler: kept, there is nothing else

    def test_a_quoted_word_is_kept_although_it_is_filler(self):
        self.assertEqual(shown('"how" do I'), (["how"], ["do", "I"]))

    def test_operators_are_never_terms_in_any_spelling(self):
        self.assertEqual(shown("foo AND bar or baz NOT qux"), (["foo", "bar", "baz", "qux"], ["AND", "or", "NOT"]))

    def test_a_text_of_filler_words_only_is_searched_as_it_is(self):
        self.assertEqual(shown("how to"), (["how", "to"], []))

    def test_nothing_gives_no_terms(self):
        for text in ("", "   ", "?!", "= - =", '""', None, "~", "[]", "()", "{}", "^", "!"):
            with self.subTest(text=text):
                self.assertEqual(shown(text), ([], []))

    def test_a_word_is_used_once_whatever_its_case(self):
        self.assertEqual(shown("Roles roles ROLES role"), (["Roles", "role"], []))

    def test_the_number_of_terms_is_limited_and_the_rest_is_reported(self):
        words = [f"word{number}" for number in range(MAX_TERMS + 3)]
        result = parse_terms(" ".join(words))
        self.assertEqual(len(result.used), MAX_TERMS)
        self.assertEqual(list(result.ignored), words[MAX_TERMS:])

    def test_the_limit_is_the_twelve_that_the_guide_names(self):
        words = [f"word{number}" for number in range(13)]
        result = parse_terms(" ".join(words))
        self.assertEqual(([term.text for term in result.used], list(result.ignored)), (words[:12], ["word12"]))

    def test_control_characters_and_quotes_cannot_break_what_follows(self):
        self.assertEqual(shown('x" OR space = "SECRET'), (["x", "space", "SECRET"], ["OR"]))
        self.assertEqual(shown("a\x00b\x1b[2Jc"), (["b", "2Jc"], ["a"]))  # a control character separates words, and `a` is filler

    def test_the_filler_list_has_no_word_that_can_be_the_subject_of_a_search(self):
        for word in ("list", "show", "find", "bin", "war", "man", "get", "role", "group", "rule", "issue", "sprint", "label", "epic"):
            with self.subTest(word=word):
                self.assertNotIn(word, STOP_WORDS)

    def test_a_term_is_truthy_when_it_has_terms(self):
        self.assertTrue(parse_terms("a b"))
        self.assertFalse(parse_terms("?"))


class WhatJiraRefusesTest(unittest.TestCase):
    """Measured on a Jira 11 Data Center: these made `text ~ ...` fail with HTTP 400, so they never reach the query."""

    REFUSED = ["~a", "(a", "a)", "((", "x]", "[x", "{x}", "x}", "a{", "!", "audit ^", "-", "+", "[x]", "x!", "a~b", "a^b", "a[b]c"]

    def test_no_word_holds_a_character_that_jira_refuses(self):
        for text in self.REFUSED:
            with self.subTest(text=text):
                for term in parse_terms(text).used:
                    self.assertNotRegex(term.text, r"[~^\[\]{}()!\\]")

    def test_a_word_that_held_such_a_character_becomes_two_words(self):
        self.assertEqual(shown("audit~trail role(s)"), (["audit", "trail", "role", "s"], []))
        self.assertEqual(shown("api[v2]"), (["api", "v2"], []))

    def test_a_phrase_loses_them_too(self):
        result = parse_terms('"audit [trail]!" roles')
        self.assertEqual([(term.text, term.phrase) for term in result.used], [("audit trail", True), ("roles", False)])
        self.assertEqual([term.text for term in parse_terms('"(((" audit').used], ["audit"])

    def test_a_leading_plus_or_minus_is_dropped_because_jira_reads_it_as_an_operator(self):
        self.assertEqual(shown("-draft +final C++ e-mail"), (["draft", "final", "C++", "e-mail"], []))

    def test_a_trailing_question_mark_is_punctuation_not_a_wildcard(self):
        self.assertEqual(shown("audit trail? roles?"), (["audit", "trail", "roles"], []))


class TermsFromJqlTest(unittest.TestCase):
    def test_the_words_of_text_clauses(self):
        self.assertEqual([t.display for t in terms_from_jql('project = PROJ AND text ~ "export monthly report" AND status = "To Do"')], ["export", "monthly", "report"])
        self.assertEqual([t.display for t in terms_from_jql('summary ~ "release notes"')], ["release", "notes"])

    def test_a_phrase_and_an_or_group(self):
        self.assertEqual([t.display for t in terms_from_jql('text ~ "\\"monthly report\\"" AND text ~ "ACME"')], ['"monthly report"', "ACME"])
        self.assertEqual([t.display for t in terms_from_jql('(text ~ "a1" OR text ~ "b2") AND description ~ "c3 d4"')], ["a1", "b2", "c3", "d4"])

    def test_other_clauses_have_no_words(self):
        self.assertEqual(terms_from_jql('project = PROJ AND labels = "adr" AND updated >= "2026-01-01" ORDER BY created DESC'), ())
        self.assertEqual(terms_from_jql(""), ())

    def test_a_word_once(self):
        self.assertEqual([t.display for t in terms_from_jql('text ~ "Roles" AND summary ~ "roles"')], ["Roles"])


if __name__ == "__main__":
    unittest.main()
