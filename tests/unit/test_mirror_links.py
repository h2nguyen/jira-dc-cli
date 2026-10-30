"""The `links:` line of a Markdown ticket: its format, the links that Jira holds, and what a push has to create."""
import time
import unittest

from jira_dc_cli.domain.errors import ValidationError
from jira_dc_cli.domain.frontmatter import parse_markdown_ticket
from jira_dc_cli.domain.mirror import MirrorFile
from jira_dc_cli.domain.mirror_links import (
    INWARD,
    OUTWARD,
    LinkEntry,
    clean_name,
    entries_of,
    forms,
    new_link,
    parse_line,
    plan_links,
    render_line,
    resolve_type,
    unwritable_types,
    writable,
)
from jira_dc_cli.domain.model import IssueLink, IssueRef, LinkType
from jira_dc_cli.domain.payloads import link_payload
from tests.support.fakes import sample_issue

TYPES = [
    LinkType("1", "Blocks", "blocks", "is blocked by"),
    LinkType("2", "Has to be done after", "is required by", "requires"),
    LinkType("3", "Pre-requisite", "requires", "is a requisite of"),
    LinkType("4", "Relates", "relates to", "relates to"),
]


def held(type_name, direction, key):
    return IssueLink(type_name, "phrase", direction, IssueRef(key))


def issue_with(*links):
    return sample_issue("PROJ-1", links=list(links))


def shown(entries):
    return [entry.text() for entry in entries]


class LineTest(unittest.TestCase):
    def test_a_line_lists_the_type_name_the_end_this_issue_is_and_the_other_key(self):
        entries = parse_line("Blocks -> PROJ-2, Blocks <- PROJ-3, Relates -> DEMO-7")
        self.assertEqual([(e.type_name, e.direction, e.key) for e in entries], [("Blocks", OUTWARD, "PROJ-2"), ("Blocks", INWARD, "PROJ-3"), ("Relates", OUTWARD, "DEMO-7")])

    def test_the_arrow_follows_the_relation_the_tail_does_what_the_type_says(self):
        self.assertEqual(parse_line("Blocks -> PROJ-2")[0].direction, OUTWARD)  # this issue blocks PROJ-2
        self.assertEqual(parse_line("Blocks <- PROJ-2")[0].direction, INWARD)  # PROJ-2 blocks this issue

    def test_blanks_around_the_arrow_and_the_case_of_the_key_do_not_matter(self):
        for text in ("Blocks->PROJ-2", "Blocks  ->  PROJ-2", "  Blocks -> proj-2  ", "Blocks ->PROJ-2"):
            with self.subTest(text=text):
                self.assertEqual(shown(parse_line(text)), ["Blocks -> PROJ-2"])

    def test_a_type_name_may_hold_blanks_hyphens_and_colons(self):
        text = "Has to be done after -> PROJ-2, Pre-requisite <- PROJ-3, Dependency: hard -> DEMO-1"
        self.assertEqual(shown(parse_line(text)), ["Dependency: hard -> DEMO-1", "Has to be done after -> PROJ-2", "Pre-requisite <- PROJ-3"])

    def test_the_blanks_inside_a_type_name_are_one_blank_each(self):
        self.assertEqual(parse_line("Has   to be \t done after -> PROJ-2")[0].type_name, "Has to be done after")

    def test_an_empty_line_lists_no_link(self):
        for text in ("", "   ", " , ,, "):
            with self.subTest(text=text):
                self.assertEqual(parse_line(text), [])

    def test_a_link_listed_twice_is_one_link_whatever_its_spelling(self):
        self.assertEqual(shown(parse_line("Blocks -> PROJ-2, blocks->proj-2, BLOCKS  ->  PROJ-2")), ["Blocks -> PROJ-2"])

    def test_the_end_is_part_of_the_link_so_both_ends_of_one_relation_are_two_links(self):
        self.assertEqual(shown(parse_line("Relates -> PROJ-2, Relates <- PROJ-2")), ["Relates -> PROJ-2", "Relates <- PROJ-2"])

    def test_the_order_is_fixed_by_type_end_and_key_and_a_key_is_ordered_as_a_number(self):
        entries = parse_line("Relates <- PROJ-3, Blocks <- PROJ-9, Blocks -> PROJ-10, Blocks -> PROJ-2, blocks -> DEMO-5")
        self.assertEqual(shown(entries), ["blocks -> DEMO-5", "Blocks -> PROJ-2", "Blocks -> PROJ-10", "Blocks <- PROJ-9", "Relates <- PROJ-3"])
        self.assertEqual(shown(parse_line("Blocks -> PROJ-10, Blocks -> PROJ-02, Blocks -> PROJ-2")), ["Blocks -> PROJ-02", "Blocks -> PROJ-2", "Blocks -> PROJ-10"])  # a tie is broken by the text

    def test_a_line_that_is_written_comes_back_the_same(self):
        line = "Blocks -> PROJ-2, Blocks <- PROJ-3, Relates -> PROJ-7"
        self.assertEqual(render_line(parse_line(line)), line)
        self.assertEqual(render_line(parse_line(render_line(parse_line("Relates<-PROJ-3,Blocks->proj-2")))), "Blocks -> PROJ-2, Relates <- PROJ-3")

    def test_the_line_survives_the_file_and_the_importer_reads_it_as_one_line(self):
        wanted = [LinkEntry("Has to be done after", OUTWARD, "PROJ-2"), LinkEntry("Pre-requisite", INWARD, "PROJ-10"), LinkEntry("Dependency: hard", OUTWARD, "DEMO-3")]
        file = MirrorFile.parse("---\njira_key: PROJ-1\nsummary: S\nfix_versions:\n---\nBody\n")
        file.set("links", render_line(wanted), after=("fix_versions",))
        again = MirrorFile.parse(file.render())
        self.assertEqual(shown(parse_line(again.get("links"), again.key)), ["Dependency: hard -> DEMO-3", "Has to be done after -> PROJ-2", "Pre-requisite <- PROJ-10"])
        self.assertEqual(file.head, ["jira_key: PROJ-1", "summary: S", "fix_versions:", "links: " + render_line(wanted)])
        ticket = parse_markdown_ticket(file.render())  # `import-md` reads the same file and ignores the line it does not know
        self.assertEqual(ticket.meta["links"], render_line(wanted))
        self.assertEqual(ticket.meta["summary"], "S")

    def test_a_value_in_quotes_is_read_like_the_other_lines(self):
        file = MirrorFile.parse('---\nlinks: "Blocks -> PROJ-2"\n---\n')
        self.assertEqual(shown(parse_line(file.get("links"))), ["Blocks -> PROJ-2"])


class UnreadableTest(unittest.TestCase):
    def test_an_entry_that_is_not_a_link_is_an_error_that_names_it_and_says_how_to_write_it(self):
        for text in (
            "Blocks PROJ-2",  # no arrow
            "Blocks > PROJ-2",
            "Blocks => PROJ-2",
            "Blocks → PROJ-2",
            "Blocks --> PROJ-2",  # a long arrow
            "Blocks <-> PROJ-2",
            "-> PROJ-2",  # no type
            "  <- PROJ-2",
            "Blocks ->",  # no key
            "Blocks -> 12",
            "Blocks -> PROJ-2 and more",
            "Blocks -> PROJ 2",
            "A -> B -> PROJ-2",  # an arrow in the name
            "Blocks: PROJ-2",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError) as caught:
                    parse_line(f"Relates -> PROJ-5, {text}")
                self.assertIn(text.strip(), caught.exception.message)
                self.assertIn("Type -> KEY", caught.exception.hint)
                self.assertIn("Type <- KEY", caught.exception.hint)
                self.assertIn("jira link-types", caught.exception.hint)

    def test_a_link_to_the_issue_itself_is_an_error(self):
        with self.assertRaises(ValidationError) as caught:
            parse_line("Blocks -> PROJ-2, Blocks -> proj-1", own_key="PROJ-1")
        self.assertIn("PROJ-1 to itself", caught.exception.message)
        self.assertEqual(shown(parse_line("Blocks -> PROJ-2, Blocks -> PROJ-10", own_key="PROJ-1")), ["Blocks -> PROJ-2", "Blocks -> PROJ-10"])  # another key that begins alike
        self.assertEqual(shown(parse_line("Blocks -> PROJ-1")), ["Blocks -> PROJ-1"])  # without the key of the file nothing can be said

    def test_a_long_entry_is_cut_in_the_message(self):
        with self.assertRaises(ValidationError) as caught:
            parse_line("x" * 500)
        self.assertLess(len(caught.exception.message), 250)
        self.assertIn("...", caught.exception.message)


class HostileTest(unittest.TestCase):
    """A line is data from a file: no shape of it may take long or crash."""

    def assertQuick(self, work):
        started = time.perf_counter()
        try:
            work()
        except ValidationError:
            pass
        self.assertLess(time.perf_counter() - started, 1.0)

    def test_long_lines_of_every_shape_are_read_or_refused_quickly(self):
        for text in (
            "x" * 200_000,
            " " * 200_000,
            "-" * 100_000,
            ",".join(["Blocks -> PROJ-2"] * 20_000),
            "<-" * 50_000 + " PROJ-2",
            "Blocks " + "->" * 50_000 + " PROJ-2",
            "a" * 50_000 + " -> " + "PROJ-" + "9" * 50_000,
            "Blocks ->" + " " * 100_000 + "PROJ-2",
            ",".join(f"Type {number} -> PROJ-{number}" for number in range(20_000)),
        ):
            with self.subTest(text=text[:30]):
                self.assertQuick(lambda: parse_line(text, "PROJ-1"))

    def test_a_key_with_thousands_of_digits_is_ordered_without_converting_it_to_a_number(self):
        entries = parse_line("Blocks -> PROJ-" + "9" * 6000 + ", Blocks -> PROJ-2, Blocks -> PROJ-10")
        self.assertEqual([entry.key for entry in entries][:2], ["PROJ-2", "PROJ-10"])

    def test_a_control_character_or_a_nul_in_a_name_does_not_break_the_line(self):
        entries = parse_line("Bl\x00ocks -> PROJ-2")
        self.assertEqual(entries[0].type_name, "Bl\x00ocks")  # it is a name that no type has: a push refuses it with the names that exist
        self.assertEqual(shown(parse_line("Blocks\u2028x -> PROJ-2")), ["Blocks x -> PROJ-2"])


class NamesTest(unittest.TestCase):
    def test_what_a_line_can_hold(self):
        for name in ("Blocks", "Has to be done after", "Pre-requisite", "Dependency: hard", "Duplicate (old)", "ä-Typ"):
            self.assertTrue(writable(name), name)
        for name in ("", "A, B", "A -> B", "A <- B", "Less < More", "More > Less", "Odd-", "<"):
            self.assertFalse(writable(name), name)

    def test_blanks_are_collapsed(self):
        self.assertEqual(clean_name("  Has \t to  be\n done "), "Has to be done")
        self.assertEqual(clean_name(None), "")

    def test_the_link_types_of_an_issue_that_a_line_cannot_hold(self):
        issue = issue_with(held("Blocks", OUTWARD, "PROJ-2"), held("Data, Quality", OUTWARD, "PROJ-3"), held("A -> B", INWARD, "PROJ-4"), held("Data, Quality", INWARD, "PROJ-5"))
        self.assertEqual(unwritable_types(issue), ["A -> B", "Data, Quality"])
        self.assertEqual(unwritable_types(issue_with(held("Blocks", OUTWARD, "PROJ-2"))), [])
        self.assertEqual(unwritable_types(issue_with()), [])


class JiraSideTest(unittest.TestCase):
    def test_the_links_of_an_issue_as_the_line_lists_them(self):
        issue = issue_with(held("Relates", INWARD, "PROJ-7"), held("Blocks", INWARD, "PROJ-3"), held("Blocks", OUTWARD, "PROJ-2"), held("Blocks", OUTWARD, "PROJ-2"))
        self.assertEqual(shown(entries_of(issue)), ["Blocks -> PROJ-2", "Blocks <- PROJ-3", "Relates <- PROJ-7"])  # a link that is there twice is listed once

    def test_a_link_without_a_usable_other_end_or_type_is_left_out(self):
        issue = issue_with(held("Blocks", OUTWARD, ""), held("", OUTWARD, "PROJ-2"), held("Blocks", "sideways", "PROJ-3"), held("Blocks", OUTWARD, "not a key"), held("Blocks", OUTWARD, "PROJ-4"))
        self.assertEqual(shown(entries_of(issue)), ["Blocks -> PROJ-4"])

    def test_the_other_key_is_written_in_capitals_and_the_type_name_as_jira_has_it(self):
        self.assertEqual(shown(entries_of(issue_with(held("Has  to be done after", OUTWARD, "proj-2")))), ["Has to be done after -> PROJ-2"])

    def test_an_issue_without_links_lists_none(self):
        self.assertEqual(entries_of(issue_with()), [])


class FormTest(unittest.TestCase):
    def test_the_same_link_has_the_same_form_whatever_the_order_the_case_and_the_blanks(self):
        one = forms(parse_line("Blocks -> PROJ-2, Relates <- PROJ-3"))
        two = forms(parse_line("relates<-proj-3 , BLOCKS -> PROJ-2"))
        self.assertEqual(one, two)
        self.assertEqual(one, ["blocks -> PROJ-2", "relates <- PROJ-3"])

    def test_another_end_another_key_or_another_type_is_another_form(self):
        base = forms(parse_line("Blocks -> PROJ-2"))
        for other in ("Blocks <- PROJ-2", "Blocks -> PROJ-3", "Relates -> PROJ-2", "Blocks -> PROJ-2, Blocks -> PROJ-3"):
            with self.subTest(other=other):
                self.assertNotEqual(forms(parse_line(other)), base)

    def test_what_jira_holds_and_what_the_file_lists_have_one_form(self):
        self.assertEqual(forms(entries_of(issue_with(held("Blocks", OUTWARD, "PROJ-2")))), forms(parse_line("blocks -> proj-2")))


class PlanTest(unittest.TestCase):
    A, B, C = "Blocks -> PROJ-2", "Blocks <- PROJ-3", "Relates -> PROJ-7"

    def plan(self, file, jira):
        plan = plan_links(parse_line(file), parse_line(jira))
        return shown(plan.create), shown(plan.jira_only)

    def test_every_combination_of_the_file_and_jira(self):
        a, b, c = self.A, self.B, self.C
        table = {
            ("", ""): ([], []),
            (a, ""): ([a], []),
            ("", a): ([], [a]),
            (a, a): ([], []),
            (f"{a}, {b}", a): ([b], []),
            (a, f"{a}, {b}"): ([], [b]),
            (f"{a}, {c}", f"{a}, {b}"): ([c], [b]),
            (f"{a}, {b}, {c}", f"{c}, {b}, {a}"): ([], []),
            (b, a): ([b], [a]),
        }
        for (file, jira), expected in table.items():
            with self.subTest(file=file, jira=jira):
                self.assertEqual(self.plan(file, jira), expected)

    def test_compare_first_the_case_of_the_type_and_of_the_key_is_no_difference(self):
        self.assertEqual(self.plan("blocks -> proj-2", "Blocks -> PROJ-2"), ([], []))

    def test_the_end_makes_a_difference(self):
        self.assertEqual(self.plan("Relates -> PROJ-2", "Relates <- PROJ-2"), (["Relates -> PROJ-2"], ["Relates <- PROJ-2"]))


class ResolveTest(unittest.TestCase):
    def test_a_type_is_found_by_its_name_without_regard_to_case_and_blanks(self):
        for name in ("Blocks", "blocks", "BLOCKS", " Blocks "):
            self.assertEqual(resolve_type(TYPES, name).id, "1")
        self.assertEqual(resolve_type(TYPES, "has TO be   done after").id, "2")

    def test_a_phrase_is_no_type_and_an_unknown_type_is_refused_with_the_names_that_exist(self):
        for name in ("requires", "is blocked by", "Blokcs", ""):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as caught:
                    resolve_type(TYPES, name)
                self.assertIn("Available: Blocks, Has to be done after, Pre-requisite, Relates", caught.exception.hint)

    def test_two_types_of_one_name_are_not_guessed(self):
        types = [LinkType("1", "Blocks", "blocks", "is blocked by"), LinkType("2", "blocks", "x", "y")]
        with self.assertRaises(ValidationError):
            resolve_type(types, "BLOCKS")

    def test_the_name_with_blanks_in_jira_matches_the_name_the_line_holds(self):
        types = [LinkType("1", "Has  to be", "a", "b")]
        self.assertEqual(resolve_type(types, "Has to be").id, "1")

    def test_a_name_with_umlauts_is_found_whatever_its_case(self):
        types = [LinkType("1", "Abhängig von", "hängt ab von", "ist Voraussetzung für"), LinkType("2", "Straße", "a", "b")]
        for name in ("abhängig von", "ABHÄNGIG VON"):
            self.assertEqual(resolve_type(types, name).id, "1")
        self.assertEqual(resolve_type(types, "STRASSE").id, "2")  # `casefold`, not `lower`: the sharp s is `ss`
        self.assertEqual(forms(parse_line("ABHÄNGIG VON -> PROJ-2")), forms(entries_of(issue_with(held("Abhängig von", OUTWARD, "PROJ-2")))))


class NewLinkTest(unittest.TestCase):
    def test_this_issue_at_the_tail_is_the_outward_end(self):
        made = new_link("PROJ-1", parse_line("Blocks -> PROJ-2")[0], TYPES[0])
        self.assertEqual(made.payload, link_payload("Blocks", "PROJ-1", "PROJ-2"))
        self.assertEqual(made.payload, {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PROJ-1"}, "inwardIssue": {"key": "PROJ-2"}})
        self.assertEqual((made.relation, made.type_name), ("PROJ-1 blocks PROJ-2", "Blocks"))

    def test_this_issue_at_the_head_is_the_inward_end(self):
        made = new_link("PROJ-1", parse_line("Blocks <- PROJ-3")[0], TYPES[0])
        self.assertEqual(made.payload, link_payload("Blocks", "PROJ-3", "PROJ-1"))
        self.assertEqual(made.relation, "PROJ-1 is blocked by PROJ-3")

    def test_the_type_is_sent_by_the_name_jira_has_not_the_one_that_was_typed(self):
        made = new_link("PROJ-1", parse_line("has to be   done after -> PROJ-2")[0], TYPES[1])
        self.assertEqual(made.payload["type"], {"name": "Has to be done after"})
        self.assertEqual(made.relation, "PROJ-1 is required by PROJ-2")

    def test_a_phrase_that_two_types_share_does_not_decide_the_type(self):
        outward = new_link("PROJ-1", parse_line("Pre-requisite -> PROJ-2")[0], TYPES[2])  # "requires" is its outward phrase
        inward = new_link("PROJ-1", parse_line("Has to be done after <- PROJ-2")[0], TYPES[1])  # and the inward phrase of another type
        self.assertEqual((outward.relation, inward.relation), ("PROJ-1 requires PROJ-2", "PROJ-1 requires PROJ-2"))
        self.assertEqual((outward.payload["type"]["name"], inward.payload["type"]["name"]), ("Pre-requisite", "Has to be done after"))
        self.assertNotEqual(outward.payload, inward.payload)


if __name__ == "__main__":
    unittest.main()
