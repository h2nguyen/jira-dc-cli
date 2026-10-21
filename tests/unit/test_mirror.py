"""A Markdown ticket of a mirror folder: reading and writing it without disturbing the rest, and deciding which side changed."""
import json
import unittest

from jira_dc_cli.domain.errors import ValidationError
from jira_dc_cli.domain.frontmatter import parse_markdown_ticket
from jira_dc_cli.domain.mirror import (
    BACK,
    CONFLICT,
    DIFFERS,
    JIRA_CHANGED,
    LOCAL_CHANGED,
    MIXED,
    SAME,
    MirrorFile,
    Verdict,
    agreement,
    apply_remote,
    carries,
    classify,
    differing,
    local_managed,
    new_file,
    placeholders_in,
    remote_managed,
    skipped_fields,
    stamp,
    update_payload,
)
from jira_dc_cli.domain.wiki import markdown_to_wiki
from tests.support.fakes import sample_issue

IDS = {"story_points": "customfield_1", "epic_link": "customfield_2", "sprint": "customfield_3", "acceptance": "customfield_4"}
NOW = "2026-10-20T12:00:00+00:00"

STORY = """---
source_id: 32
parent_id: 3
project:
issuetype: Story
summary: Schedule the report
labels: reporting,export
priority: Very High
components: Reporting
fix_versions: Release 1.0
assignee:
source_key: SRC-014
jira_key: PROJ-33
requirement_ids: REQ-02
rank_order: 32
fields_json: {"customfield_1":5.0,"customfield_4":"1. *GIVEN* a user, *WHEN* x, *THEN* y."}
---

## User story

As an administrator, I want the report *scheduled* every month.

## Acceptance criteria

1. **GIVEN** a user, **WHEN** x, **THEN** y.
"""


def mirror(text=STORY):
    return MirrorFile.parse(text)


def issue_like(file=None, **changes):
    """An issue that holds what the file holds, as Jira would give it back."""
    values = dict(
        summary="Schedule the report",
        labels=["reporting", "export"],
        priority="Very High",
        components=["Reporting"],
        fix_versions=["Release 1.0"],
        story_points=5.0,
        acceptance="1. *GIVEN* a user, *WHEN* x, *THEN* y.",
        description="h2. User story\n\nAs an administrator, I want the report _scheduled_ every month.\n\nh2. Acceptance criteria\n\n# *GIVEN* a user, *WHEN* x, *THEN* y.",
        assignee=None,
        reporter=None,
        comments=[],
    )
    values.update(changes)
    return sample_issue("PROJ-33", "Story", "In Progress", **values)


def managed(file, issue):
    skipped = skipped_fields(file, IDS)
    return local_managed(file, IDS, skipped), remote_managed(issue, skipped)


class FileTest(unittest.TestCase):
    def test_a_file_is_written_back_as_it_was_read(self):
        self.assertEqual(mirror().render(), STORY)

    def test_blank_values_comments_and_unknown_lines_stay_where_they_are(self):
        text = "---\n# a comment\nsummary: S\nassignee:\nparent_id: \nodd line without colon\nx_y: 1\n---\nBody\n"
        self.assertEqual(MirrorFile.parse(text).render(), text)

    def test_line_endings_become_plain_line_feeds_and_a_bom_is_dropped(self):
        self.assertEqual(MirrorFile.parse("\ufeff---\r\nsummary: S\r\n---\r\nBody\r\n").render(), "---\nsummary: S\n---\nBody\n")

    def test_without_front_matter_it_is_no_ticket(self):
        for text in ("", "Body only\n", "--- \nsummary: S\n", "---\nsummary: S\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValidationError):
                    MirrorFile.parse(text)

    def test_get_set_remove(self):
        file = mirror()
        self.assertEqual((file.get("summary"), file.get("project"), file.get("nothing"), file.has("assignee")), ("Schedule the report", "", "", True))
        file.set("summary", "New\nsummary")
        self.assertEqual(file.get("summary"), "New summary")
        file.set("lossy", "table")
        self.assertEqual(file.head[-1], "lossy: table")
        file.remove("lossy")
        self.assertFalse(file.has("lossy"))
        file.set("assignee", "")
        self.assertIn("assignee:", file.head)

    def test_a_key_is_not_a_longer_name(self):
        file = MirrorFile.parse("---\nsummary_long: x\nsummary: S\n---\n")
        self.assertEqual(file.get("summary"), "S")

    def test_quotes_around_a_value_are_taken_off_like_the_importer_does(self):
        self.assertEqual(MirrorFile.parse("---\nsummary: \"Quoted\"\npriority: 'Low'\n---\n").get("summary"), "Quoted")

    def test_the_key_is_the_jira_key_or_the_issue_key(self):
        self.assertEqual((mirror().key, mirror().key_line), ("PROJ-33", "jira_key"))
        other = MirrorFile.parse("---\nissue_key: proj-7\n---\n")
        self.assertEqual((other.key, other.key_line), ("PROJ-7", "issue_key"))
        for text in ("---\nsummary: S\n---\n", "---\njira_key:\n---\n", "---\njira_key: not a key\n---\n"):
            self.assertEqual(MirrorFile.parse(text).key, "")

    def test_fields_json_keeps_its_other_entries_and_their_order(self):
        file = mirror()
        file.set_fields({"customfield_1": 8.0, "customfield_9": "new"})
        self.assertEqual(file.get("fields_json"), '{"customfield_1":8.0,"customfield_4":"1. *GIVEN* a user, *WHEN* x, *THEN* y.","customfield_9":"new"}')
        file.set_fields({"customfield_4": None})
        self.assertNotIn("customfield_4", file.fields())

    def test_a_broken_fields_json_is_an_error_with_a_name(self):
        file = MirrorFile.parse("---\nfields_json: {broken\n---\n")
        with self.assertRaises(ValidationError) as caught:
            file.fields()
        self.assertIn("fields_json", caught.exception.message)
        nested = MirrorFile.parse("---\nfields_json: " + "[" * 100_000 + "\n---\n")  # deeper than the parser can follow
        with self.assertRaises(ValidationError) as caught:
            nested.fields()
        self.assertIn("fields_json", caught.exception.message)

    def test_the_base_line(self):
        file = mirror()
        self.assertIsNone(file.base)
        file.set("jira_base", "summary=0123ab/ba9876,labels=aaaaaa/bbbbbb")
        self.assertEqual(file.base, {"summary": ("0123ab", "ba9876"), "labels": ("aaaaaa", "bbbbbb")})
        for broken in ("garbage", "", "summary=xyz/123", "summary=0123456789ab-ba9876543210"):
            file.set("jira_base", broken)
            self.assertIsNone(file.base, broken)


class ManagedTest(unittest.TestCase):
    def test_what_is_kept_in_step(self):
        file = mirror()
        local, remote = managed(file, issue_like())
        self.assertEqual(sorted(local.values), ["acceptance", "components", "description", "fixVersions", "labels", "priority", "storyPoints", "summary"])
        self.assertEqual(local.values["storyPoints"], 5.0)
        self.assertEqual(local.raw["labels"], ["reporting", "export"])
        self.assertEqual(differing(local, remote), [])

    def test_the_order_of_labels_and_the_spacing_of_text_do_not_count(self):
        file = mirror()
        issue = issue_like(labels=["export", "reporting"], summary="  Schedule   the report ")
        local, remote = managed(file, issue)
        self.assertEqual(differing(local, remote), [])

    def test_a_difference_names_the_field(self):
        local, remote = managed(mirror(), issue_like(summary="Another", story_points=3.0, labels=["x"]))
        self.assertEqual(sorted(differing(local, remote)), ["labels", "storyPoints", "summary"])

    def test_people_and_placeholders_are_redacted_before_they_are_compared(self):
        file = mirror(STORY.replace("As an administrator", "As [~jdoe] (jane.doe@example.com)"))
        local, remote = managed(file, issue_like(description="h2. User story\n\nAs [~other] (a@b.example)..."))
        self.assertNotIn("jane.doe", str(local.values))
        self.assertIn("[~user]", local.values["description"])

    def test_sanitized_and_relative_links_leave_the_description_out(self):
        sanitized = mirror(STORY.replace("rank_order: 32", "rank_order: 32\nmirror: sanitized"))
        self.assertEqual(list(skipped_fields(sanitized, IDS)), ["description"])
        local, remote = managed(sanitized, issue_like(description="something else"))
        self.assertNotIn("description", local.values)
        self.assertNotIn("description", remote.values)
        self.assertEqual(differing(local, remote), [])
        linked = mirror(STORY + "\nSee [the ADR](../../docs/adr/0001.md#x).\n")
        self.assertIn("relative link", skipped_fields(linked, IDS)["description"])
        self.assertEqual(list(skipped_fields(mirror(STORY + "\n[web](https://example.com) [here](#a) [mail](mailto:a@b.example)\n"), IDS)), [])

    def test_a_jira_without_the_fields_leaves_them_out(self):
        ids = {"story_points": None, "acceptance": None}
        skipped = skipped_fields(mirror(), ids)
        self.assertEqual(sorted(skipped), ["acceptance", "storyPoints"])
        self.assertNotIn("storyPoints", local_managed(mirror(), ids, skipped).values)

    def test_the_digests_follow_the_text_and_not_the_order_of_a_list(self):
        file = mirror()
        local, _ = managed(file, issue_like())
        other, _ = managed(mirror(STORY.replace("labels: reporting,export", "labels: export,reporting")), issue_like())
        changed, _ = managed(mirror(STORY.replace("summary: Schedule the report", "summary: Other")), issue_like())
        self.assertEqual(local.digests(), other.digests())
        self.assertEqual({name for name in local.digests() if local.digests()[name] != changed.digests()[name]}, {"summary"})
        for digest in local.digests().values():
            self.assertRegex(digest, r"^[0-9a-f]{6}$")

    def test_a_description_with_windows_line_endings_has_the_digest_of_the_same_text_with_plain_ones(self):
        plain = remote_managed(issue_like(description="h2. Goal\n\nText\n"), {})
        for name, text in {"windows": "h2. Goal\r\n\r\nText  \r\n", "old Mac": "h2. Goal\r\rText\r"}.items():
            with self.subTest(name=name):
                self.assertEqual(remote_managed(issue_like(description=text), {}).digests(), plain.digests())

    def test_an_edit_of_nothing_but_the_indentation_or_the_blank_lines_changes_the_digest(self):
        base = "---\njira_key: PROJ-33\nsummary: S\n---\n\nFirst.\n\nSecond.\n\n```\n    code\n```\n"
        before = managed(mirror(base), issue_like())[0].digests()["description"]
        for edit in (base.replace("    code", "        code"), base.replace("First.\n\nSecond.", "First.\nSecond.")):
            with self.subTest(edit=edit):
                self.assertNotEqual(managed(mirror(edit), issue_like())[0].digests()["description"], before)
        for harmless in (base.replace("\n", "\r\n"), base.replace("First.", "First.   ")):  # line endings and trailing spaces are no edit
            with self.subTest(harmless=harmless):
                self.assertEqual(managed(mirror(harmless), issue_like())[0].digests()["description"], before)

    def test_story_points_may_be_a_text_that_holds_a_number_and_nothing_else(self):
        for written, expected in (('"8"', 8.0), ('" 5.5 "', 5.5), ('""', None), ("null", None), ("3", 3.0)):
            with self.subTest(written=written):
                file = mirror(STORY.replace('"customfield_1":5.0,', f'"customfield_1":{written},'))
                self.assertEqual(local_managed(file, IDS, {}).values["storyPoints"], expected)
        for written in ('"five"', "true", "[1]", '"nan"', '"inf"', "1" + "0" * 400):  # the last one is an integer that no float can hold
            with self.subTest(written=written):
                file = mirror(STORY.replace('"customfield_1":5.0,', f'"customfield_1":{written},'))
                with self.assertRaises(ValidationError) as caught:
                    local_managed(file, IDS, {})
                self.assertIn("story points", caught.exception.message)

    def test_a_backslash_and_an_n_count_as_a_line_break(self):
        from jira_dc_cli.domain.mirror import normalize

        self.assertEqual(normalize("one\\ntwo"), normalize("one\ntwo"))
        self.assertEqual(normalize("  a \n b\t c "), "a b c")
        self.assertEqual(normalize(None), "")


class ClassifyTest(unittest.TestCase):
    def pair(self, file_text=STORY, **issue_changes):
        file = mirror(file_text)
        return managed(file, issue_like(**issue_changes))

    def base_of(self, file_text=STORY, **issue_changes):
        local, remote = self.pair(file_text, **issue_changes)
        return agreement(local, remote)

    def test_equal_is_the_same_with_or_without_a_record(self):
        local, remote = self.pair()
        self.assertEqual(classify(local, remote, None), Verdict(SAME))
        self.assertEqual(classify(local, remote, agreement(local, remote)), Verdict(SAME))

    def test_different_and_never_synced_has_no_direction(self):
        local, remote = self.pair(summary="Other")
        verdict = classify(local, remote, None)
        self.assertEqual((verdict.state, verdict.fields, verdict.unknown), (DIFFERS, ["summary"], ["summary"]))

    def test_only_the_file_changed(self):
        base = self.base_of()
        local, remote = self.pair(STORY.replace("summary: Schedule the report", "summary: Edited here"))
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.fields, verdict.local, verdict.jira, verdict.conflicts), (LOCAL_CHANGED, ["summary"], ["summary"], [], []))

    def test_only_jira_changed(self):
        base = self.base_of()
        local, remote = self.pair(summary="Changed there")
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.fields, verdict.local, verdict.jira), (JIRA_CHANGED, ["summary"], [], ["summary"]))

    def test_the_same_field_changed_on_both_sides_is_a_conflict(self):
        base = self.base_of()
        local, remote = self.pair(STORY.replace("summary: Schedule the report", "summary: Mine"), summary="Theirs")
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.conflicts, verdict.local, verdict.jira), (CONFLICT, ["summary"], [], []))

    def test_different_fields_changed_on_the_two_sides_are_no_conflict(self):
        base = self.base_of()
        local, remote = self.pair(STORY.replace("summary: Schedule the report", "summary: Mine"), labels=["x"])
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.local, verdict.jira, verdict.conflicts), (MIXED, ["summary"], ["labels"], []))
        self.assertEqual(verdict.fields, ["summary", "labels"])

    def test_one_conflicting_field_makes_the_file_a_conflict_but_the_others_keep_their_side(self):
        base = self.base_of()
        local, remote = self.pair(STORY.replace("summary: Schedule the report", "summary: Mine").replace("priority: Very High", "priority: Low"), summary="Theirs", labels=["x"])
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.conflicts, verdict.local, verdict.jira), (CONFLICT, ["summary"], ["priority"], ["labels"]))

    def test_both_changed_the_same_way_is_the_same(self):
        base = self.base_of()
        local, remote = self.pair(STORY.replace("summary: Schedule the report", "summary: Agreed"), summary="Agreed")
        self.assertEqual(classify(local, remote, base), Verdict(SAME))

    def test_a_pair_that_always_differed_the_same_way_is_the_same(self):
        local, remote = self.pair(story_points=3.0)  # the file says 5, Jira 3: that is how they were recorded
        self.assertEqual(classify(local, remote, agreement(local, remote)), Verdict(SAME))

    def test_a_field_without_a_record_is_unknown_even_when_the_others_have_one(self):
        base = self.base_of()
        del base["labels"]
        local, remote = self.pair(labels=["x"])
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.unknown), (DIFFERS, ["labels"]))

    def test_a_conflict_outranks_a_field_without_a_record(self):
        base = self.base_of()
        del base["labels"]
        local, remote = self.pair(STORY.replace("summary: Schedule the report", "summary: Mine"), summary="Theirs", labels=["x"])
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.conflicts, verdict.unknown), (CONFLICT, ["summary"], ["labels"]))

    def test_a_file_that_becomes_sanitized_does_not_turn_the_other_fields_into_conflicts(self):
        base = self.base_of()
        text = STORY.replace("rank_order: 32", "rank_order: 32\nmirror: sanitized").replace("summary: Schedule the report", "summary: Mine")
        local, remote = self.pair(text)
        verdict = classify(local, remote, base)
        self.assertEqual((verdict.state, verdict.fields, verdict.local), (LOCAL_CHANGED, ["summary"], ["summary"]))
        self.assertNotIn("description", local.values)


class StampTest(unittest.TestCase):
    def test_the_state_both_agree_on_is_recorded_for_every_field(self):
        file = mirror()
        local, remote = managed(file, issue_like())
        stamp(file, local, remote, NOW)
        self.assertEqual(file.get("jira_synced"), NOW)
        self.assertEqual(file.base, agreement(local, remote))
        self.assertEqual(sorted(file.base), sorted(local.values))
        before = file.render()
        stamp(file, local, remote, NOW)
        self.assertEqual(file.render(), before)  # again: the same two lines

    def test_only_the_named_fields_are_recorded_and_the_others_keep_waiting(self):
        file = mirror()
        local, remote = managed(file, issue_like())
        stamp(file, local, remote, NOW)
        earlier = dict(file.base)
        changed_local, changed_remote = managed(mirror(STORY.replace("summary: Schedule the report", "summary: Mine")), issue_like(labels=["x"]))
        stamp(file, changed_local, changed_remote, NOW, ["summary"])
        self.assertEqual(file.base["summary"], (changed_local.digests()["summary"], changed_remote.digests()["summary"]))
        self.assertEqual(file.base["labels"], earlier["labels"])  # not recorded: it is still waiting
        self.assertEqual({name for name in file.base if file.base[name] != earlier[name]}, {"summary"})

    def test_a_field_that_no_longer_takes_part_loses_its_record(self):
        file = mirror()
        local, remote = managed(file, issue_like())
        stamp(file, local, remote, NOW)
        self.assertIn("description", file.base)
        sanitized = mirror(STORY.replace("rank_order: 32", "rank_order: 32\nmirror: sanitized"))
        local, remote = managed(sanitized, issue_like())
        sanitized.set("jira_base", file.get("jira_base"))
        stamp(sanitized, local, remote, NOW, [])
        self.assertNotIn("description", sanitized.base)
        self.assertIn("summary", sanitized.base)

    def test_the_records_come_in_the_order_of_the_fields(self):
        file = mirror()
        local, remote = managed(file, issue_like())
        stamp(file, local, remote, NOW)
        self.assertEqual([item.split("=")[0] for item in file.get("jira_base").split(",")], list(local.values))

    def test_a_field_kept_as_an_edit_takes_only_jiras_digest(self):
        file = mirror()
        local, remote = managed(file, issue_like())
        stamp(file, local, remote, NOW)
        before = dict(file.base)
        spaced_local, spaced_remote = managed(mirror(STORY.replace("summary: Schedule the report", "summary: Schedule  the report")), issue_like(summary="Schedule the  report"))
        self.assertNotEqual(spaced_local.digests()["summary"], before["summary"][0])
        self.assertNotEqual(spaced_remote.digests()["summary"], before["summary"][1])
        stamp(file, spaced_local, spaced_remote, NOW, ["summary"], keep=["summary"])
        self.assertEqual(file.base["summary"], (before["summary"][0], spaced_remote.digests()["summary"]))  # what the file held then stays the reference
        fresh = mirror()
        stamp(fresh, spaced_local, spaced_remote, NOW, ["summary"], keep=["summary"])  # nothing was recorded for it before: both digests are
        self.assertEqual(fresh.base["summary"], (spaced_local.digests()["summary"], spaced_remote.digests()["summary"]))


class ApplyRemoteTest(unittest.TestCase):
    def test_only_the_named_fields_change_and_the_rest_of_the_file_stays(self):
        file = mirror()
        apply_remote(file, issue_like(summary="Changed there", labels=["a", "b"], priority="Low", story_points=8.0), IDS, ["summary", "labels", "priority", "storyPoints"])
        text = file.render()
        self.assertIn("summary: Changed there", text)
        self.assertIn("labels: a,b", text)
        self.assertIn("priority: Low", text)
        self.assertIn('"customfield_1":8.0', text)
        for untouched in ("source_id: 32", "parent_id: 3", "source_key: SRC-014", "requirement_ids: REQ-02", "rank_order: 32", "components: Reporting", "## User story"):
            self.assertIn(untouched, text)

    def test_the_description_becomes_markdown_and_says_what_it_lost(self):
        file = mirror()
        dropped = apply_remote(file, issue_like(description="h2. Goal\n\n||a||b||\n|c|d|\n\nA *bold* word."), IDS, ["description"])
        self.assertEqual(dropped, ("table",))
        self.assertEqual(file.get("lossy"), "table")
        self.assertIn("## Goal", file.render())
        self.assertIn("**bold**", file.render())
        apply_remote(file, issue_like(description="h2. Goal\n\nPlain."), IDS, ["description"])
        self.assertFalse(file.has("lossy"))

    def test_story_points_and_acceptance_in_fields_json(self):
        file = mirror()
        apply_remote(file, issue_like(story_points=None, acceptance="1. *GIVEN* new"), IDS, ["storyPoints", "acceptance"])
        self.assertEqual(file.fields(), {"customfield_4": "1. *GIVEN* new"})
        apply_remote(file, issue_like(acceptance=""), IDS, ["acceptance"])
        self.assertEqual(file.fields(), {})

    def test_a_line_that_already_says_the_same_is_not_rewritten(self):
        text = STORY.replace("issuetype: Story", "issuetype:   Story  ")
        file = mirror(text)
        apply_remote(file, issue_like(), IDS, [])
        self.assertEqual(file.render(), text)  # nothing to change: not even the spacing of a line

    def test_the_type_and_the_epic_of_a_file_never_change(self):
        file = mirror(STORY.replace("issuetype: Story", "issuetype: Task") + "")
        file.head.append("epic_key: PROJ-1")
        before = file.render()
        apply_remote(file, issue_like(epic_key="PROJ-5"), IDS, [])
        apply_remote(file, issue_like(epic_key="PROJ-5", summary="Changed there"), IDS, ["summary"])
        self.assertEqual((file.get("issuetype"), file.get("epic_key")), ("Task", "PROJ-1"))
        self.assertEqual(file.render(), before.replace("summary: Schedule the report", "summary: Changed there"))

    def test_a_value_that_begins_and_ends_with_a_quote_survives_the_file(self):
        file = mirror()
        apply_remote(file, issue_like(summary='"Audit" trail for "users"'), IDS, ["summary"])
        self.assertIn('summary: ""Audit" trail for "users""', file.render())
        self.assertEqual(local_managed(file, IDS, {}).raw["summary"], '"Audit" trail for "users"')
        apply_remote(file, issue_like(labels=["'a'"]), IDS, ["labels"])
        self.assertEqual(local_managed(file, IDS, {}).raw["labels"], ["'a'"])
        again = new_file(issue_like(summary="'Quoted'"), IDS, key_line="issue_key", project="PROJ")[0]
        self.assertEqual(MirrorFile.parse(again.render()).get("summary"), "'Quoted'")

    def test_a_value_that_merely_starts_and_ends_with_the_same_letter_is_written_as_it_is(self):
        file = mirror()
        apply_remote(file, issue_like(summary="level"), IDS, ["summary"])
        self.assertIn("\nsummary: level\n", file.render())

    def test_the_blanks_inside_a_text_of_fields_json_are_not_collapsed_when_another_entry_changes(self):
        text = STORY.replace('"1. *GIVEN* a user, *WHEN* x, *THEN* y."', '"1. a,  WHEN b,\\u00a0THEN\\tc"')
        file = mirror(text)
        before = file.fields()["customfield_4"]
        apply_remote(file, issue_like(story_points=8.0), IDS, ["storyPoints"])
        self.assertEqual(file.fields(), {"customfield_1": 8.0, "customfield_4": before})
        self.assertIn("a,  WHEN b,\u00a0THEN\\tc", file.get("fields_json"))
        fresh, _ = new_file(issue_like(acceptance="1. a,  WHEN b"), IDS, key_line="issue_key", project="PROJ")
        self.assertEqual(fresh.fields()["customfield_4"], "1. a,  WHEN b")

    def test_a_line_break_that_json_leaves_as_it_is_cannot_break_the_line(self):
        for mark in ("\u0085", "\u2028", "\u2029"):  # `str.splitlines`, which the importer reads the front matter with, breaks a line at each of them
            with self.subTest(mark=repr(mark)):
                file = mirror()
                apply_remote(file, issue_like(acceptance=f"1. a{mark}b"), IDS, ["acceptance"])
                self.assertEqual(file.fields()["customfield_4"], f"1. a{mark}b")
                self.assertEqual(len(file.render().splitlines()), len(STORY.splitlines()))
                ticket = parse_markdown_ticket(file.render())
                self.assertEqual(json.loads(ticket.meta["fields_json"])["customfield_4"], f"1. a{mark}b")
                fresh, _ = new_file(issue_like(acceptance=f"1. a{mark}b"), IDS, key_line="issue_key", project="PROJ")
                self.assertEqual(json.loads(parse_markdown_ticket(fresh.render()).meta["fields_json"])["customfield_4"], f"1. a{mark}b")


class CarriesTest(unittest.TestCase):
    """A description is sent back from Markdown only when the way there and back changes nothing."""

    def test_what_the_converter_names_is_what_is_said(self):
        self.assertEqual(carries("h2. Goal\n\n||a||b||\n|c|d|\n"), ("table",))

    def test_a_dash_bullet_is_a_bullet_like_the_star_and_comes_back_as_one(self):
        self.assertEqual(carries("h2. Goal\n\n- one\n- two\n-- nested\n"), ())
        self.assertEqual(carries("{code}\n- a\n- b\n{code}\n"), ())  # in code the lines are what they are, and they come back
        self.assertEqual(carries("+ one\n+ two\n"), (BACK,))  # a plus is no bullet in Jira's markup: sending it back would make one

    def test_a_text_that_changes_on_the_way_back_without_being_named_is_caught(self):
        for name, text in {
            "a code parameter": "{code:java|linenumbers=true}\nint a;\n{code}\n",
            "a literal star": "Use \\*literal\\* stars and 2 * 3 * 4.\n",
            "underscores in a name": "The method __init__ must run.\n",
        }.items():
            with self.subTest(name=name):
                self.assertEqual(carries(text), (BACK,))

    def test_what_comes_back_unchanged_is_carried(self):
        for text in (
            "h2. Goal\n\nSome *bold* and _italic_ text with {{code}}.\n\n* one\n** two\n# first\n# second\n\n[a link|https://x.example/a]\n",
            "{code:python}\nprint('*not bold*')\n{code}\n\nbq. quoted\n",
            "Line one\r\nLine two  \r\n\r\n\r\nAfter two blank lines.\r\n",
            "*Read the {{spec}} first* and see *[the guide|https://x.example/guide]*.\n",  # a mark that holds code or a link: it came back damaged before
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual(carries(text), ())

    def test_converting_never_leaves_a_protected_piece_in_the_text(self):
        self.assertNotIn("\x00", markdown_to_wiki("**Read the `spec` first** and see **[the guide](https://x.example/g)**.\n"))
        self.assertNotIn("\x00", markdown_to_wiki("a\x00JIRA0\x00b `c`\n"))


class ListsTest(unittest.TestCase):
    def test_a_name_with_a_comma_cannot_be_a_line_and_leaves_the_field_out(self):
        issue = issue_like(components=["Data, Quality", "Backend"])
        left = skipped_fields(mirror(), IDS, issue)
        self.assertEqual(list(left), ["components"])
        self.assertIn("comma", left["components"])
        self.assertEqual(skipped_fields(mirror(), IDS, issue_like()), {})
        self.assertEqual(skipped_fields(mirror(), IDS), {})


class RelativeLinkTest(unittest.TestCase):
    def skipped(self, body):
        return "description" in skipped_fields(mirror(STORY + body), IDS)

    def test_a_link_to_a_document_is_relative(self):
        self.assertTrue(self.skipped("\nSee [the ADR](../docs/adr/0001.md).\n"))
        self.assertTrue(self.skipped("\nSee [the shot](shot.png).\n"))

    def test_an_image_is_no_link_but_a_path_to_a_picture_is_relative(self):
        for body in ("\n![](shot.png)\n", "\nSteps:\n\n![dialog](shot.png)\n\nThe dialog is wrong.\n", "\n![](https://x.example/a.png)\n"):
            with self.subTest(body=body):
                self.assertFalse(self.skipped(body))
        for body in ("\n![diagram](../docs/img/a.png)\n", "\n![](images/a.png)\n", "\n![](images\\a.png)\n"):
            with self.subTest(body=body):
                self.assertTrue(self.skipped(body))

    def test_an_address_an_anchor_an_issue_key_and_code_are_not(self):
        for body in (
            "\n[web](https://example.com) [here](#a) [mail](mailto:a@b.example)\n",
            "\nSee [the related story](PROJ-2) and [another](PROJ-12).\n",
            "\nUse `handlers[0](event)` in the callback.\n",
            "\n```js\nhandlers[0](event);\n[a](b.md)\n```\n",
            "\n~~~\n[a](b.md)\n~~~\n",
        ):
            with self.subTest(body=body):
                self.assertFalse(self.skipped(body))

    def test_a_link_after_a_code_block_still_counts(self):
        self.assertTrue(self.skipped("\n```\ncode\n```\n\nSee [the ADR](docs/a.md).\n"))
        self.assertTrue(self.skipped("\nA `code` span and [the ADR](docs/a.md).\n"))


class NewFileTest(unittest.TestCase):
    def test_a_new_file_is_a_ticket_the_importer_reads(self):
        issue = issue_like(epic_key="PROJ-5", description="h2. Goal\n\nText with *bold*.")
        file, dropped = new_file(issue, IDS, key_line="jira_key", project="PROJ", parent_source="3")
        self.assertEqual(dropped, ())
        ticket = parse_markdown_ticket(file.render())
        meta = ticket.meta
        self.assertEqual((meta["jira_key"], meta["project"], meta["issuetype"], meta["summary"], meta["priority"]), ("PROJ-33", "PROJ", "Story", "Schedule the report", "Very High"))
        self.assertEqual((meta["labels"], meta["components"], meta["fix_versions"], meta["epic_key"], meta["parent_id"]), ("reporting,export", "Reporting", "Release 1.0", "PROJ-5", "3"))
        self.assertEqual(meta["assignee"], "")
        self.assertEqual(file.fields(), {"customfield_1": 5.0, "customfield_4": "1. *GIVEN* a user, *WHEN* x, *THEN* y."})
        self.assertTrue(ticket.body.startswith("## Goal"))

    def test_it_holds_no_person_and_no_status(self):
        file, _ = new_file(issue_like(), IDS, key_line="issue_key", project="PROJ")
        text = file.render()
        for absent in ("jdoe", "Jane", "jane.doe", "status:", "sprint:", "reporter"):
            self.assertNotIn(absent, text)

    def test_the_line_of_the_key_is_the_one_the_folder_uses(self):
        file, _ = new_file(issue_like(), IDS, key_line="issue_key", project="PROJ")
        self.assertEqual(file.key_line, "issue_key")

    def test_without_story_points_or_acceptance_the_json_is_empty(self):
        file, _ = new_file(issue_like(story_points=None, acceptance=""), IDS, key_line="jira_key", project="PROJ")
        self.assertEqual(file.get("fields_json"), "{}")


class PayloadTest(unittest.TestCase):
    def local(self, text=STORY):
        file = mirror(text)
        return local_managed(file, IDS, skipped_fields(file, IDS))

    def test_only_the_fields_that_differ_are_sent(self):
        payload = update_payload(["summary", "priority", "labels", "components", "fixVersions", "storyPoints", "acceptance", "description"], self.local(), IDS)
        fields = payload["fields"]
        self.assertEqual(fields["summary"], "Schedule the report")
        self.assertEqual(fields["priority"], {"name": "Very High"})
        self.assertEqual(fields["labels"], ["reporting", "export"])
        self.assertEqual(fields["components"], [{"name": "Reporting"}])
        self.assertEqual(fields["fixVersions"], [{"name": "Release 1.0"}])
        self.assertEqual(fields["customfield_1"], 5.0)
        self.assertEqual(fields["customfield_4"], "1. *GIVEN* a user, *WHEN* x, *THEN* y.")
        self.assertTrue(fields["description"].startswith("h2. User story"))
        self.assertEqual(update_payload(["summary"], self.local(), IDS), {"fields": {"summary": "Schedule the report"}})

    def test_the_description_is_wiki_markup_without_the_front_matter(self):
        description = update_payload(["description"], self.local(), IDS)["fields"]["description"]
        self.assertNotIn("source_id", description)
        self.assertIn("# *GIVEN* a user", description)
        self.assertIn("As an administrator, I want the report _scheduled_ every month.", description)

    def test_a_removed_story_point_clears_the_field(self):
        local = self.local(STORY.replace('"customfield_1":5.0,', ""))
        self.assertEqual(update_payload(["storyPoints"], local, IDS), {"fields": {"customfield_1": None}})

    def test_an_empty_summary_or_priority_is_refused(self):
        for line in ("summary: Schedule the report", "priority: Very High"):
            with self.subTest(line=line):
                with self.assertRaises(ValidationError):
                    local = self.local(STORY.replace(line, line.split(":")[0] + ":"))
                    update_payload(["summary", "priority"], local, IDS)

    def test_a_placeholder_in_a_text_field_that_is_sent_is_found(self):
        local = self.local(STORY.replace("Schedule the report", "Ask [~user] about it"))
        self.assertEqual(placeholders_in(["summary", "labels"], local), ["summary"])
        self.assertEqual(placeholders_in(["labels"], local), [])
        mail = self.local(STORY.replace("As an administrator", "Write to [email]"))
        self.assertEqual(placeholders_in(["description"], mail), ["description"])
        link = self.local(STORY.replace('"1. *GIVEN*', '"[personal link] *GIVEN*'))
        self.assertEqual(placeholders_in(["acceptance"], link), ["acceptance"])

    def test_a_nul_in_a_text_or_in_the_name_of_a_list_is_not_sent(self):
        for name, text in {
            "summary": STORY.replace("Schedule the report", "Schedule\x00 the report"),
            "labels": STORY.replace("labels: reporting,export", "labels: reporting,exp\x00ort"),
            "components": STORY.replace("components: Reporting", "components: Month\x00ly Reports"),
            "fixVersions": STORY.replace("fix_versions: Release 1.0", "fix_versions: Release\x00 1.0"),
        }.items():
            with self.subTest(name=name):
                with self.assertRaises(ValidationError) as caught:
                    update_payload([name], self.local(text), IDS)
                self.assertIn("NUL", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
