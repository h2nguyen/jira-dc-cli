"""The people that a JQL names are left out: the clauses of the user fields, the history predicates, what stays, and hostile input."""
import time
import unittest

from jira_dc_cli.domain.jqlpeople import PLACEHOLDER, USER_FIELDS, redact_jql
from jira_dc_cli.domain.model import User
from jira_dc_cli.domain.savedfilters import SavedFilter, without_people

REVIEW = 'assignee = jdoe AND reporter in ("asmith","jane.doe") AND watcher = "Alex Smith"'
NAMES = ("jdoe", "asmith", "jane.doe", "Alex", "Smith", "jroe", "jpoe", "John")


class RedactJqlTest(unittest.TestCase):
    def assertRedacted(self, jql, expected):
        self.assertEqual(redact_jql(jql), expected, jql)

    def test_the_jql_of_the_review(self):
        self.assertEqual(redact_jql(REVIEW), "assignee = [user] AND reporter in ([user],[user]) AND watcher = [user]")
        self.assertEqual(PLACEHOLDER, "[user]")

    def test_every_user_field_in_any_case_with_the_name_quoted_or_not(self):
        fields = ("assignee", "reporter", "creator", "watcher", "voter", "commentedBy", "approver", '"Request participants"')
        for field in fields:
            for written in (field, field.upper(), field.title(), f'"{field}"' if not field.startswith('"') else field, f"'{field.strip(chr(34))}'"):
                with self.subTest(field=written):
                    self.assertRedacted(f"{written} = jdoe", f"{written} = [user]")
        self.assertEqual({"assignee", "reporter", "creator", "watcher", "voter", "commentedby", "approver", "request participants"}, set(USER_FIELDS))

    def test_the_comparison_operators(self):
        for operator in ("=", "!=", "~", "!~"):
            with self.subTest(operator=operator):
                self.assertRedacted(f"assignee {operator} jdoe", f"assignee {operator} [user]")
                self.assertRedacted(f"assignee{operator}jdoe", f"assignee{operator}[user]")
                self.assertRedacted(f'assignee {operator} "jane.doe"', f"assignee {operator} [user]")
                self.assertRedacted(f"assignee {operator} 'Jane Doe'", f"assignee {operator} [user]")

    def test_every_field_with_every_form_of_clause(self):
        forms = (
            "{f} = jdoe",
            "{f} != jdoe",
            "{f} ~ jdoe",
            "{f} !~ jdoe",
            "{f} is jdoe",
            "{f} in (jdoe)",
            "{f} not in (jdoe)",
            "{f} was jdoe",
            "{f} was in (jdoe)",
            "{f} was not jdoe",
            "{f} was not in (jdoe)",
            "{f} changed from jdoe",
            "{f} changed to jdoe",
            "{f} changed by jdoe",
        )
        for field in ("assignee", "reporter", "creator", "watcher", "voter", "commentedBy", "approver", '"Request participants"'):
            for form in forms:
                for written in (field, field.upper()):
                    jql = form.format(f=written)
                    with self.subTest(jql=jql):
                        self.assertEqual(redact_jql(jql), jql.replace("jdoe", "[user]"))

    def test_in_and_not_in_with_a_list_in_brackets(self):
        self.assertRedacted("assignee in (jdoe, asmith)", "assignee in ([user], [user])")
        self.assertRedacted("assignee IN(jdoe,asmith)", "assignee IN([user],[user])")
        self.assertRedacted("assignee not in (jdoe)", "assignee not in ([user])")
        self.assertRedacted("assignee NOT IN ( 'a b' , \"c d\" )", "assignee NOT IN ( [user] , [user] )")
        self.assertRedacted("assignee in ((jdoe), (asmith))", "assignee in (([user]), ([user]))")
        self.assertRedacted("assignee in (jdoe,", "assignee in ([user],")  # a list that is never closed

    def test_is_and_is_not(self):
        self.assertRedacted("assignee is EMPTY", "assignee is EMPTY")
        self.assertRedacted("assignee is not EMPTY", "assignee is not EMPTY")
        self.assertRedacted("assignee is jdoe", "assignee is [user]")  # not JQL that Jira takes, but a name all the same
        self.assertRedacted("assignee is not jdoe", "assignee is not [user]")

    def test_was_in_the_history_of_the_field(self):
        self.assertRedacted("assignee was jdoe", "assignee was [user]")
        self.assertRedacted("reporter was jdoe", "reporter was [user]")
        self.assertRedacted("assignee was not jdoe", "assignee was not [user]")
        self.assertRedacted("assignee was in (jdoe, asmith)", "assignee was in ([user], [user])")
        self.assertRedacted("assignee was not in (jdoe, asmith)", "assignee was not in ([user], [user])")
        self.assertRedacted('assignee WAS "Jane Doe" BEFORE "2026-01-01"', 'assignee WAS [user] BEFORE "2026-01-01"')

    def test_changed_from_to_and_by(self):
        self.assertRedacted("assignee changed", "assignee changed")
        self.assertRedacted("assignee changed from jdoe", "assignee changed from [user]")
        self.assertRedacted("assignee changed to asmith", "assignee changed to [user]")
        self.assertRedacted("assignee changed from jdoe to asmith", "assignee changed from [user] to [user]")
        self.assertRedacted("reporter changed by jdoe", "reporter changed by [user]")
        self.assertRedacted("reporter changed from EMPTY to jdoe by asmith after -1w", "reporter changed from EMPTY to [user] by [user] after -1w")
        self.assertRedacted("assignee CHANGED FROM jdoe TO asmith BY jroe", "assignee CHANGED FROM [user] TO [user] BY [user]")

    def test_the_user_who_changed_any_field_is_a_person_too(self):
        self.assertRedacted("status changed to Done by jdoe", "status changed to Done by [user]")
        self.assertRedacted('status was "Open" by (jdoe, asmith)', 'status was "Open" by ([user], [user])')
        self.assertRedacted('"Story Points" changed by jdoe', '"Story Points" changed by [user]')
        self.assertRedacted("priority was High by currentUser()", "priority was High by currentUser()")

    def test_dates_and_the_values_of_other_fields_stay_in_a_history_clause(self):
        jql = 'status changed from "To Do" to "In Progress" after "2026-01-01" before -1d on startOfDay() during ("2026-01-01", "2026-02-01")'
        self.assertRedacted(jql, jql)
        self.assertRedacted("status was in (Open, Done) by jdoe", "status was in (Open, Done) by [user]")
        self.assertRedacted('assignee was jdoe during ("2026-01-01", "2026-02-01")', 'assignee was [user] during ("2026-01-01", "2026-02-01")')

    def test_functions_and_words_that_are_no_person_stay(self):
        for jql in (
            "assignee = currentUser()",
            "assignee != currentUser ()",
            'assignee in membersOf("jira-developers")',
            "reporter in membersOf(developers)",
            "assignee in (currentUser(), membersOf('x'))",
            "assignee = EMPTY",
            "assignee = empty OR assignee = NULL OR assignee = null",
            "assignee = unassigned",
            "assignee in (EMPTY, unassigned)",
            "assignee was currentUser()",
            "assignee changed by currentUser()",
            "assignee changed from currentUser() to EMPTY",
            "watcher = currentUser() AND voter = currentUser()",
        ):
            with self.subTest(jql=jql):
                self.assertRedacted(jql, jql)
        self.assertRedacted("assignee in (EMPTY, currentUser(), jdoe)", "assignee in (EMPTY, currentUser(), [user])")
        self.assertRedacted('assignee in (membersOf("a (b)"), jdoe, membersOf(c(d)))', 'assignee in (membersOf("a (b)"), [user], membersOf(c(d)))')

    def test_the_user_of_a_function_that_takes_one(self):
        self.assertRedacted('issue in updatedBy(jdoe, "2026-01-01")', 'issue in updatedBy([user], "2026-01-01")')
        self.assertRedacted('project in projectsLeadByUser("asmith")', "project in projectsLeadByUser([user])")
        self.assertRedacted("component in componentsLeadByUser(jdoe)", "component in componentsLeadByUser([user])")
        self.assertRedacted("issue in updatedBy(currentUser())", "issue in updatedBy(currentUser())")
        self.assertRedacted("project in projectsLeadByUser()", "project in projectsLeadByUser()")

    def test_what_is_not_recognised_stays(self):
        for jql in (
            "cf[10100] = jdoe",
            '"Reviewer" = jdoe',
            'cf[10100] in (jdoe, "asmith")',
            'text ~ "assignee = jdoe"',
            'summary ~ "reporter in (asmith)"',
            "labels = assignee",
            "labels in (assignee, reporter)",
            "project = PROJ ORDER BY assignee ASC, reporter DESC",
            "watchers > 3 AND votes < 2",
            "assignee",
            "= jdoe",
        ):
            with self.subTest(jql=jql):
                self.assertRedacted(jql, jql)

    def test_the_other_clauses_around_a_person_stay_as_they_are(self):
        self.assertRedacted(
            'project = "PROJ" AND status in (Open, "In Progress") AND assignee = jdoe AND created >= -30d ORDER BY priority DESC, created ASC',
            'project = "PROJ" AND status in (Open, "In Progress") AND assignee = [user] AND created >= -30d ORDER BY priority DESC, created ASC',
        )
        self.assertRedacted("(assignee = jdoe OR assignee = asmith) AND x = y", "(assignee = [user] OR assignee = [user]) AND x = y")
        self.assertRedacted("assignee = jdoe && reporter = asmith || creator = jroe", "assignee = [user] && reporter = [user] || creator = [user]")
        self.assertRedacted("NOT assignee = jdoe", "NOT assignee = [user]")
        self.assertRedacted("! assignee = jdoe", "! assignee = [user]")

    def test_the_layout_is_kept(self):
        self.assertRedacted("assignee   =\tjdoe\nAND\n  reporter in (\n    'a b',\n    c\n  )\nORDER BY key", "assignee   =\t[user]\nAND\n  reporter in (\n    [user],\n    [user]\n  )\nORDER BY key")

    def test_a_jql_without_people_comes_back_byte_identical(self):
        for jql in (
            "",
            "   ",
            'project = "PROJ" AND type = Bug ORDER BY created DESC',
            "assignee = currentUser() AND resolution = Unresolved ORDER BY updated DESC",
            'project in ("A", "B") AND labels = "x y" AND text ~ "audit trail"',
            "filter = 10000",
            "key in (PROJ-1, PROJ-2)",
            "Sprint in openSprints() AND status != Done",
            '"Epic Link" = PROJ-9',
            "summary ~ \u00fcnic\u00f6de\u200b AND \t\n",
        ):
            with self.subTest(jql=jql):
                self.assertEqual(redact_jql(jql), jql)

    def test_a_value_that_is_a_name_or_an_address_or_with_accents(self):
        self.assertRedacted('reporter = "J\u00f6rg M\u00fcller"', "reporter = [user]")
        self.assertRedacted("reporter = jane.doe@example.com", "reporter = [user]")
        self.assertRedacted('reporter in ("jane.doe@example.com", j.roe@example.com)', "reporter in ([user], [user])")
        self.assertRedacted('assignee = "has \\" a quote and (a bracket"', "assignee = [user]")
        self.assertRedacted('assignee = "a" AND reporter = "b"', "assignee = [user] AND reporter = [user]")

    def test_a_string_that_is_not_closed_runs_to_the_end(self):
        self.assertRedacted('assignee = "jane doe', "assignee = [user]")
        self.assertRedacted("assignee = 'jane doe AND reporter = x", "assignee = [user]")

    def test_it_is_idempotent_and_takes_what_is_no_text(self):
        for jql in (REVIEW, "assignee changed from jdoe to asmith by jroe", 'issue in updatedBy("jdoe")'):
            with self.subTest(jql=jql):
                once = redact_jql(jql)
                self.assertEqual(redact_jql(once), once)
        self.assertEqual(redact_jql(None), "")  # type: ignore[arg-type]

    def test_no_name_survives_in_any_clause_of_a_long_filter(self):
        jql = (
            "assignee = jdoe AND reporter in (asmith, 'jroe') AND creator != jpoe AND watcher ~ \"John\" AND voter was asmith "
            "AND commentedBy changed to jdoe by jroe AND status changed by jpoe AND issue in updatedBy(asmith)"
        )
        redacted = redact_jql(jql)
        for name in NAMES:
            self.assertNotIn(name, redacted)

    def test_hostile_input_costs_time_in_proportion_to_its_length(self):
        hostile = {
            "clauses without an end": "assignee = " * 20_000,
            "brackets that open": "(" * 200_000,
            "brackets that close": ")" * 200_000,
            "lists that never close": "assignee in (" * 16_000,
            "one long list": "assignee in (" + "a," * 100_000,
            "a string full of escapes": '"' + '\\"' * 100_000,
            "quotes": "'" * 200_001,
            "backslashes": "\\" * 200_000,
            "a field without an operator": "assignee " * 25_000,
            "history clauses": "assignee was " * 15_000,
            "changed by": "assignee changed by " * 10_000,
            "blanks first": " " * 200_000 + "assignee = jdoe",
            "operators": "assignee " + "=" * 200_000 + " jdoe",
            "one word": "assignee = " + "x" * 200_000,
            "one string": 'assignee = "' + "x" * 200_000 + '"',
            "not in": "assignee not in (a) " * 10_000,
            "functions": "updatedBy(a) " * 15_000,
            "commas": "," * 200_000,
            "a mix": 'assignee in ("a", b, c(d), (e)) and reporter was x by y ' * 3_000,
        }
        started = time.perf_counter()
        for name, text in hostile.items():
            with self.subTest(name=name):
                begun = time.perf_counter()
                redact_jql(text)
                self.assertLess(time.perf_counter() - begun, 3.0)
        self.assertLess(time.perf_counter() - started, 15.0)

    def test_the_time_grows_with_the_length_and_not_faster(self):
        def seconds(text):
            best = 1e9
            for _ in range(3):
                started = time.perf_counter()
                redact_jql(text)
                best = min(best, time.perf_counter() - started)
            return best

        for unit in ("assignee in (a, b, c(d)) AND reporter was x by y ", "assignee = ", "((((", 'assignee = "' + "\\" * 5):
            with self.subTest(unit=unit):
                small, large = seconds(unit * 1_000), seconds(unit * 8_000)
                self.assertLess(large, max(small * 40, 1.0))  # eight times the text: well below sixty-four times the time


class WithoutPeopleTest(unittest.TestCase):
    OWNER = User("jdoe", "Jane Doe", "jane.doe@example.com")

    def item(self, jql, **more):
        base = dict(
            id="10000",
            name="Bugs",
            description="Open bugs",
            jql=jql,
            favourite=True,
            owner=self.OWNER,
            search_url="https://jira.example.com/rest/api/2/search?jql=" + jql.replace(" ", "%20").replace("=", "%3D").replace('"', "%22"),
            view_url="https://jira.example.com/issues/?filter=10000",
        )
        return SavedFilter(**{**base, **more})

    def test_the_people_of_the_jql_the_owner_and_the_search_address_are_gone(self):
        shown = without_people(self.item(REVIEW))
        self.assertEqual(shown.jql, "assignee = [user] AND reporter in ([user],[user]) AND watcher = [user]")
        self.assertEqual((shown.owner, shown.search_url, shown.people_hidden), (None, "", True))
        self.assertEqual((shown.id, shown.favourite, shown.view_url), ("10000", True, "https://jira.example.com/issues/?filter=10000"))
        for name in NAMES:
            self.assertNotIn(name, repr(shown))

    def test_a_jql_without_people_and_its_address_stay_as_they_are(self):
        original = self.item('project = "PROJ" AND assignee = currentUser() ORDER BY created DESC')
        shown = without_people(original)
        self.assertEqual((shown.jql, shown.search_url, shown.people_hidden), (original.jql, original.search_url, False))
        self.assertIsNone(shown.owner)

    def test_the_text_is_redacted_like_every_other_text(self):
        shown = without_people(self.item("project = PROJ", name="Bugs of jane.doe@example.com", description="Open [~jdoe] bugs"))
        self.assertEqual((shown.name, shown.description), ("Bugs of [email]", "Open [~user] bugs"))
        self.assertFalse(shown.people_hidden)  # the name and the description are not the JQL

    def test_a_mention_or_an_address_in_a_text_of_the_jql_drops_the_address_too(self):
        for jql in ('text ~ "jane.doe@example.com"', 'comment ~ "[~jdoe]"'):
            with self.subTest(jql=jql):
                shown = without_people(self.item(jql))
                self.assertEqual((shown.search_url, shown.people_hidden), ("", True))
                self.assertNotIn("jane.doe", shown.jql)
                self.assertNotIn("jdoe", shown.jql)

    def test_an_address_that_names_a_person_is_dropped_whatever_the_jql_says(self):
        escaped = "https://jira.example.com/rest/api/2/search?jql=assignee%20%3D%20jdoe"
        plus = "https://jira.example.com/rest/api/2/search?jql=reporter+%3D+%22asmith%22"
        accents = "https://jira.example.com/rest/api/2/search?jql=assignee%20%3D%20J%C3%B6rg"
        for address in (escaped, plus, accents):
            with self.subTest(address=address):
                shown = without_people(self.item("project = PROJ", search_url=address))
                self.assertEqual((shown.search_url, shown.people_hidden), ("", True))

    def test_a_filter_without_an_address_is_not_a_filter_that_hid_something(self):
        shown = without_people(self.item("project = PROJ", search_url=""))
        self.assertEqual((shown.search_url, shown.people_hidden), ("", False))
        self.assertFalse(SavedFilter("1").people_hidden)  # a filter that nobody redacted says nothing about it


if __name__ == "__main__":
    unittest.main()
