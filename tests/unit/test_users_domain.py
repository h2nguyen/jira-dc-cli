"""Which user a name stands for, and the words that `--assignee` knows."""
import unittest

from jira_dc_cli.domain.users import Person, keyword, resolve_person

JANE = Person("jdoe", "Jane Doe", "jane.doe@example.com")
JANE_ROE = Person("jroe", "Jane Roe", "jane.roe@example.com")
ALEX = Person("asmith", "Alex Smith", "alex.smith@example.com")
SAM_ONE = Person("sam1", "Sam Sample", "sam.one@example.com")
SAM_TWO = Person("sam2", "Sam Sample", "sam.two@example.com")
OLD = Person("olduser", "Old User", "old.user@example.com", active=False)
EVERYBODY = [JANE, JANE_ROE, ALEX, SAM_ONE, SAM_TWO, OLD]


class KeywordTest(unittest.TestCase):
    def test_the_words_in_any_case(self):
        self.assertEqual([keyword(text) for text in ("me", "ME", " Unassigned ", "automatic", "AUTOMATIC")], ["me", "me", "unassigned", "automatic", "automatic"])

    def test_minus_one_is_automatic(self):
        self.assertEqual(keyword("-1"), "automatic")

    def test_a_name_is_no_keyword(self):
        for text in ("jdoe", "Jane Doe", "", "meme", "un assigned", None):
            with self.subTest(text=text):
                self.assertEqual(keyword(text), "")


class ResolvePersonTest(unittest.TestCase):
    def test_an_exact_user_name_wins(self):
        found = resolve_person("jdoe", EVERYBODY)
        self.assertEqual((found.matches, found.person), (1, JANE))

    def test_case_does_not_matter(self):
        self.assertEqual(resolve_person("JDOE", EVERYBODY).person, JANE)
        self.assertEqual(resolve_person("jane doe", EVERYBODY).person, JANE)
        self.assertEqual(resolve_person("JANE.DOE@EXAMPLE.COM", EVERYBODY).person, JANE)

    def test_an_exact_user_name_beats_a_display_name_that_is_also_a_partial_match(self):
        people = [Person("jane", "Somebody Else"), Person("jdoe", "jane")]
        self.assertEqual(resolve_person("jane", people).person.name, "jane")

    def test_an_exact_display_name_beats_a_partial_match(self):
        found = resolve_person("Jane Doe", [JANE, Person("jdoe2", "Jane Doe Junior")])
        self.assertEqual((found.matches, found.person), (1, JANE))

    def test_an_exact_address(self):
        found = resolve_person("alex.smith@example.com", EVERYBODY)
        self.assertEqual(found.person, ALEX)

    def test_one_partial_match_is_enough(self):
        found = resolve_person("smi", EVERYBODY)
        self.assertEqual((found.matches, found.person), (1, ALEX))
        self.assertEqual(resolve_person("old.user@", EVERYBODY).person, OLD)

    def test_several_partial_matches_are_counted_and_none_is_chosen(self):
        found = resolve_person("Jane", EVERYBODY)
        self.assertEqual((found.matches, found.person), (2, None))

    def test_two_users_with_one_display_name_are_ambiguous_at_that_step(self):
        found = resolve_person("Sam Sample", EVERYBODY)
        self.assertEqual((found.matches, found.person), (2, None))

    def test_nobody(self):
        found = resolve_person("zed", EVERYBODY)
        self.assertEqual((found.matches, found.person), (0, None))

    def test_an_empty_query_matches_nobody_instead_of_everybody(self):
        for text in ("", "   "):
            with self.subTest(text=text):
                self.assertEqual(resolve_person(text, EVERYBODY).matches, 0)

    def test_the_same_user_twice_in_the_answer_counts_once(self):
        found = resolve_person("jane", [JANE, JANE])
        self.assertEqual((found.matches, found.person), (1, JANE))

    def test_an_inactive_user_is_found_like_any_other(self):
        self.assertEqual(resolve_person("olduser", EVERYBODY).person.active, False)

    def test_no_people_no_match(self):
        self.assertEqual(resolve_person("jdoe", []).matches, 0)


if __name__ == "__main__":
    unittest.main()
