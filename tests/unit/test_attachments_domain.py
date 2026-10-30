"""Rules for attachments: choosing, kinds of content, cutting text, split paths, the upload and the limits."""
import time
import unicodedata
import unittest

from jira_dc_cli.domain import attachments as attachments_module
from jira_dc_cli.domain.attachments import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_CHARS,
    UploadFile,
    argument_paths,
    by_name,
    choose_paths,
    cut_text,
    describe,
    guess_mime_type,
    human_size,
    kind_of,
    looks_binary,
    printable,
    select_attachments,
    split_paths,
    too_large,
    too_large_many,
    upload_payload,
)
from jira_dc_cli.domain.errors import NotFoundError, UsageError, ValidationError
from jira_dc_cli.domain.model import Attachment


def att(identifier, filename, size=10, mime="text/plain"):
    return Attachment(identifier, filename, mime, size)


ISSUE = [att("10001", "shot.png", 2048, "image/png"), att("10002", "Notes.txt"), att("10003", "notes.TXT"), att("10004", "log.txt", 5)]


class SelectionTest(unittest.TestCase):
    def pick(self, **choice):
        return [item.id for item in select_attachments("PROJ-1", ISSUE, **choice)]

    def test_by_id_by_name_and_all(self):
        self.assertEqual(self.pick(identifier="10002"), ["10002"])
        self.assertEqual(self.pick(names=["LOG.txt"]), ["10004"])  # case aside, exact
        self.assertEqual(self.pick(everything=True), ["10001", "10002", "10003", "10004"])

    def test_an_id_and_names_together_and_each_attachment_once_in_the_order_of_the_issue(self):
        self.assertEqual(self.pick(identifier="10004", names=["shot.png", "log.txt", "SHOT.PNG"]), ["10001", "10004"])

    def test_nothing_chosen_is_a_usage_error_that_lists_the_attachments(self):
        with self.assertRaises(UsageError) as caught:
            select_attachments("PROJ-1", ISSUE)
        self.assertEqual(caught.exception.exit_code, 2)
        self.assertIn("10001 shot.png (2.0 KB)", caught.exception.hint)
        with self.assertRaises(UsageError) as caught:
            select_attachments("PROJ-1", [])
        self.assertIn("PROJ-1 has no attachments", caught.exception.hint)

    def test_two_attachments_with_one_name_make_the_name_ambiguous(self):
        with self.assertRaises(UsageError) as caught:
            self.pick(names=["notes.txt"])
        self.assertIn("10002, 10003", caught.exception.message)
        self.assertIn("jira download PROJ-1 10002", caught.exception.hint)

    def test_an_unknown_id_or_name_is_not_found_and_says_what_exists(self):
        with self.assertRaises(NotFoundError) as caught:
            self.pick(identifier="99999")
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertIn("10004 log.txt", caught.exception.hint)
        with self.assertRaises(NotFoundError) as caught:
            self.pick(names=["nothing.txt"])
        self.assertIn("nothing.txt", caught.exception.message)

    def test_a_file_name_given_as_an_id_is_pointed_out(self):
        with self.assertRaises(NotFoundError) as caught:
            self.pick(identifier="shot.png")
        self.assertIn("use --name 'shot.png'", caught.exception.hint)

    def test_all_cannot_be_combined_with_a_choice(self):
        for choice in ({"identifier": "10001"}, {"names": ["shot.png"]}):
            with self.subTest(choice=choice):
                with self.assertRaises(UsageError):
                    select_attachments("PROJ-1", ISSUE, everything=True, **choice)

    def test_blank_names_choose_nothing(self):
        with self.assertRaises(UsageError):
            select_attachments("PROJ-1", ISSUE, names=["  ", ""])

    def test_by_name_and_describe(self):
        self.assertEqual([item.id for item in by_name(ISSUE, " NOTES.txt ")], ["10002", "10003"])
        many = [att(str(n), f"f{n}.txt") for n in range(30)]
        self.assertTrue(describe(many).endswith("; and 18 more"))
        self.assertEqual(describe([att("1", "a b.txt", 1500)]), "1 a b.txt (1.5 KB)")


class KindTest(unittest.TestCase):
    def test_the_type_and_the_extension_both_count(self):
        cases = (
            ("text/plain", "a.bin", "text"),
            ("text/markdown", "README", "text"),
            ("application/octet-stream", "build.log", "text"),  # Jira says nothing useful, the name does
            ("application/vnd.ms-excel", "export.csv", "text"),  # a browser on Windows with Excel installed
            ("", "notes.md", "text"),
            ("application/json", "data", "text"),
            ("application/vnd.api+json", "x", "text"),
            ("image/svg+xml", "logo.svg", "text"),
            ("application/x-yaml", "values", "text"),
            ("application/octet-stream", "Dockerfile", "text"),
            ("text/plain; charset=utf-8", "a.txt", "text"),
            ("TEXT/PLAIN", "a", "text"),
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "x.bin", "docx"),
            ("application/octet-stream", "Report.DOCX", "docx"),
            ("application/zip", "budget.xlsx", "xlsx"),
            ("application/octet-stream", "deck.pptx", "pptx"),
            ("application/vnd.ms-word.document.macroEnabled.12", "macro.docm", "docx"),
            ("image/png", "shot.png", "other"),
            ("image/png", "mislabelled.txt", "other"),  # an extension of text does not make an image text
            ("application/pdf", "spec.pdf", "other"),
            ("application/zip", "bundle.zip", "other"),
            ("video/mp4", "demo.mp4", "other"),
            ("application/octet-stream", "blob", "other"),
            ("", "", "other"),
        )
        for mime, name, expected in cases:
            with self.subTest(mime=mime, name=name):
                self.assertEqual(kind_of(mime, name), expected)

    def test_a_zero_byte_tells_text_from_binary_unless_the_bom_says_utf16(self):
        self.assertFalse(looks_binary("plain text\nwith lines".encode("utf-8")))
        self.assertFalse(looks_binary("Übersicht".encode("utf-8")))
        self.assertTrue(looks_binary(b"PK\x03\x04\x00\x00data"))
        self.assertTrue(looks_binary(b"text" + b"\x00" + b"more"))
        self.assertFalse(looks_binary("text with a BOM".encode("utf-16")))
        self.assertFalse(looks_binary(b""))
        self.assertFalse(looks_binary(b"x" * 9000 + b"\x00"))  # only the beginning is looked at


class CutTextTest(unittest.TestCase):
    def test_a_text_within_the_limit_is_untouched(self):
        self.assertEqual(cut_text("short", 5), ("short", False))
        self.assertEqual(cut_text("", 1), ("", False))

    def test_the_cut_is_at_the_end_of_a_paragraph_if_that_keeps_half_the_room(self):
        text = "first paragraph here\n\nsecond paragraph is longer than the rest\n\nthird"
        shown, truncated = cut_text(text, 45)
        self.assertEqual((shown, truncated), ("first paragraph here\n\nsecond paragraph is", True))  # no paragraph end in the second half: a word end
        shown, truncated = cut_text(text, 62)
        self.assertEqual((shown, truncated), ("first paragraph here\n\nsecond paragraph is longer than the rest", True))

    def test_else_at_a_line_then_at_a_word_then_anywhere(self):
        self.assertEqual(cut_text("line one\nline two\nline three", 20), ("line one\nline two", True))
        self.assertEqual(cut_text("word " * 10, 12), ("word word", True))
        self.assertEqual(cut_text("x" * 100, 30), ("x" * 30, True))  # nothing to cut at: a minified file

    def test_a_cut_that_would_throw_most_away_is_no_cut_at_all(self):
        shown, truncated = cut_text("a\n" + "b" * 100, 50)
        self.assertEqual((len(shown), truncated), (50, True))  # the only line end is at 1 of 50

    def test_it_never_returns_more_than_the_limit(self):
        for limit in (1, 2, 5, 17, 100):
            for text in ("a b c " * 40, "\n\n".join(["para"] * 30), "x" * 500, "é" * 300):
                shown, truncated = cut_text(text, limit)
                self.assertLessEqual(len(shown), limit)
                self.assertEqual(truncated, len(text) > limit)


class ChoosePathsTest(unittest.TestCase):
    """The same rules for a command line that keeps its arguments: an argument that is a file is one path, blanks and all."""

    def choose(self, arguments, existing):
        return choose_paths(arguments, lambda path: path in existing)

    def test_an_argument_that_is_a_file_is_one_path_whatever_it_holds(self):
        existing = {"my docs/trace log.txt", "x y", "x", "y"}
        self.assertEqual(self.choose(["my docs/trace log.txt", "x y", "x"], existing), ["my docs/trace log.txt", "x y", "x"])  # `x y` is the file `x y`, not `x` and `y`
        self.assertEqual(self.choose(["x", "y"], existing), ["x", "y"])

    def test_only_an_argument_that_is_no_file_is_joined_with_the_ones_after_it(self):
        self.assertEqual(self.choose(["my", "shot.png", "x"], {"my shot.png", "x"}), ["my shot.png", "x"])
        self.assertEqual(self.choose(["my", "shot.png"], {"my"}), ["my", "shot.png"])
        self.assertEqual(self.choose(["nothing", "here"], set()), ["nothing here"])
        self.assertEqual(self.choose([], set()), [])
        self.assertEqual(self.choose(["", ""], set()), [])

    def test_split_paths_is_the_same_rule_on_joined_text(self):
        existing = {"my shot.png", "x", "y", "x y"}
        for text in ("my shot.png x", "x y", "x y my shot.png", "nothing here x"):
            with self.subTest(text=text):
                self.assertEqual(split_paths(text, lambda path: path in existing), choose_paths(text.split(" "), lambda path: path in existing))


class ArgumentPathsTest(unittest.TestCase):
    """The arguments as the program received them: an element of `argv` that names a file is one path, blanks and all."""

    def paths(self, arguments, existing):
        return argument_paths(arguments, lambda path: path in existing)

    def test_a_quoted_name_is_the_one_file_that_it_names_even_when_the_files_of_its_words_exist_too(self):
        existing = {"x", "y", "x y"}
        self.assertEqual(self.paths(["x y"], existing), ["x y"])  # quoted: one argument
        self.assertEqual(self.paths(["x", "y"], existing), ["x", "y"])  # not quoted: two arguments
        self.assertEqual(self.paths(["x y", "x", "y"], existing), ["x y", "x", "y"])
        self.assertEqual(self.paths(["y", "x y", "x"], existing), ["y", "x y", "x"])

    def test_a_name_that_exists_only_with_its_blank_is_that_file_quoted_or_not(self):
        self.assertEqual(self.paths(["x y"], {"x y"}), ["x y"])
        self.assertEqual(self.paths(["x", "y"], {"x y"}), ["x y"])  # the quotes were forgotten: the words are joined
        self.assertEqual(self.paths(["C:\\My", "Documents\\report.pdf"], {"C:\\My Documents\\report.pdf"}), ["C:\\My Documents\\report.pdf"])

    def test_a_name_that_does_not_exist_is_named_whole(self):
        self.assertEqual(self.paths(["absent file.txt"], set()), ["absent file.txt"])
        self.assertEqual(self.paths(["a.txt", "absent file.txt"], {"a.txt"}), ["a.txt", "absent file.txt"])
        self.assertEqual(self.paths(["absent", "file.txt"], set()), ["absent file.txt"])

    def test_the_text_of_a_program_that_joined_its_words_is_split_where_no_file_has_that_name(self):
        existing = {"a.txt", "b.txt", "my shot.png"}
        self.assertEqual(self.paths(["a.txt b.txt"], existing), ["a.txt", "b.txt"])  # the files of an MCP call: one string
        self.assertEqual(self.paths(["a.txt b.txt c.txt"], existing), ["a.txt", "b.txt", "c.txt"])  # `c.txt` is none: it is named in the error
        self.assertEqual(self.paths(["my shot.png a.txt"], existing), ["my shot.png", "a.txt"])  # a name with a blank in it is joined again

    def test_a_name_that_was_cut_in_the_middle_by_the_quotes_is_still_found(self):
        self.assertEqual(self.paths(["a b", "c"], {"a b c"}), ["a b c"])
        self.assertEqual(self.paths(["a b", "c"], {"a b"}), ["a b", "c"])  # the file that the first argument names wins

    def test_an_argument_that_names_a_file_is_never_taken_apart_for_what_stands_next_to_it(self):
        existing = {"my docs/trace log.txt", "x", "my", "docs/trace", "log.txt"}
        self.assertEqual(self.paths(["my docs/trace log.txt", "x"], existing), ["my docs/trace log.txt", "x"])

    def test_empty_arguments_are_no_path_and_an_empty_path_is_never_asked_for(self):
        asked = []
        self.assertEqual(argument_paths(["", "  ", " ", ""], lambda path: asked.append(path) or False), [])
        self.assertNotIn("", asked)
        self.assertEqual(argument_paths([], lambda path: True), [])

    def test_without_blanks_in_the_arguments_it_is_the_rule_of_choose_paths(self):
        existing = {"a.png", "log.txt", "my shot.png"}
        for arguments in (["a.png", "log.txt"], ["my", "shot.png"], ["nothing", "here"], ["a.png", "nothing", "log.txt"], []):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.paths(arguments, existing), choose_paths(arguments, lambda path: path in existing))

    def test_a_name_that_the_file_system_refuses_to_look_at_is_no_file_and_no_failure(self):
        def exists(path):
            if len(path) > 20:
                raise OSError(36, "File name too long")
            if "\x00" in path:
                raise ValueError("embedded null byte")
            return path == "short.txt"

        arguments = ["short.txt", "a very long name that is far over the limit.txt", "x\x00y"]
        self.assertEqual(argument_paths(arguments, exists), ["short.txt", "a very long name that is far over the limit.txt x\x00y"])
        self.assertEqual(choose_paths(arguments, exists), ["short.txt", "a very long name that is far over the limit.txt x\x00y"])
        self.assertEqual(split_paths("short.txt " + "n" * 5000, exists), ["short.txt", "n" * 5000])
        self.assertEqual(argument_paths(["short.txt " + "m" * 5000], exists), ["short.txt", "m" * 5000])

    def test_every_path_is_asked_for_once_and_the_work_is_a_few_checks_per_argument(self):
        asked = []

        def exists(path):
            asked.append(path)
            return True

        names = [f"my logs/log{index:05d}.txt" for index in range(1500)]  # a shell passes a name with a blank as one argument
        started = time.monotonic()
        found = argument_paths(names, exists)
        self.assertEqual(len(found), 1500)
        self.assertTrue(found == names)  # not assertEqual: a list of 1,500 that differs would take minutes to be described
        self.assertEqual((len(asked), len(set(asked))), (1500, 1500))  # each one asked once, though the name is looked at twice
        self.assertLess(time.monotonic() - started, 5)
        asked.clear()
        plain = [f"log{index:05d}.txt" for index in range(1500)]
        found = argument_paths(plain, exists)
        self.assertTrue(found == plain)
        self.assertEqual(len(asked), 1500)

    def test_arguments_that_name_nothing_cost_a_bounded_number_of_checks(self):
        asked = []
        words = [f"no file {index}" for index in range(20_000)]  # each holds blanks and names nothing
        started = time.monotonic()
        found = argument_paths(words, lambda path: asked.append(path) or False)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0] == " ".join(words))  # all of it is one path that does not exist, named for the error
        self.assertLessEqual(len(asked), 25 * len(words))
        self.assertLess(time.monotonic() - started, 5)


class SplitPathsTest(unittest.TestCase):
    """The words of `jira attach` arrive joined by blanks: the argument boundaries are gone, so the split follows the files that exist."""

    def split(self, text, existing):
        return split_paths(text, lambda path: path in existing)

    def test_words_that_name_files_are_one_path_each(self):
        self.assertEqual(self.split("a.png log.txt", {"a.png", "log.txt"}), ["a.png", "log.txt"])
        self.assertEqual(self.split("a.png", {"a.png"}), ["a.png"])

    def test_a_word_that_is_a_file_is_a_file_and_a_longer_join_never_takes_its_place(self):
        self.assertEqual(self.split("x y", {"x", "y", "x y"}), ["x", "y"])  # the third file `x y` does not replace the two files
        self.assertEqual(self.split("my shot.png log.txt", {"my shot.png", "log.txt", "my"}), ["my", "shot.png", "log.txt"])
        self.assertEqual(self.split("a b c", {"a", "a b c"}), ["a", "b c"])  # `a` is a file; what follows it names nothing, so it stays together for the error

    def test_a_word_that_is_none_is_joined_with_the_words_after_it_and_the_shortest_join_that_exists_wins(self):
        self.assertEqual(self.split("my shot.png log.txt", {"my shot.png", "log.txt"}), ["my shot.png", "log.txt"])
        self.assertEqual(self.split("C:\\Users\\Jane Doe\\a b.png", {"C:\\Users\\Jane Doe\\a b.png"}), ["C:\\Users\\Jane Doe\\a b.png"])  # quotes forgotten
        self.assertEqual(self.split("a b c", {"a b", "a b c", "c"}), ["a b", "c"])

    def test_at_most_eight_words_make_one_path(self):
        words = "a b c d e f g h i j".split()
        self.assertEqual(self.split(" ".join(words), {" ".join(words[1:9])}), ["a", "b c d e f g h i", "j"])  # eight words from b
        self.assertEqual(self.split(" ".join(words), {" ".join(words[1:10])}), [" ".join(words)])  # nine are not looked for: all of it is one that is missing

    def test_words_that_name_nothing_stay_together_so_that_the_error_can_name_the_file(self):
        self.assertEqual(self.split("nothing here", set()), ["nothing here"])
        self.assertEqual(self.split("my shot.png log.txt", {"log.txt"}), ["my shot.png", "log.txt"])
        self.assertEqual(self.split("a.png missing one", {"a.png"}), ["a.png", "missing one"])

    def test_empty_and_odd_spacing(self):
        self.assertEqual(self.split("", set()), [])
        self.assertEqual(self.split("   ", set()), [])
        self.assertEqual(self.split("a  b.txt", {"a  b.txt"}), ["a  b.txt"])  # two blanks inside a name stay two blanks
        asked = []
        split_paths(" a  b ", lambda path: asked.append(path) or False)
        self.assertNotIn("", asked)  # an empty path is never asked for: it would be the folder that the command runs in

    def test_the_work_is_a_few_checks_per_word_and_never_grows_faster_than_the_words(self):
        for count in (1500, 20_000):
            names = [f"log{index:05d}.txt" for index in range(count)]
            asked = []

            def exists(path):
                asked.append(path)
                return True

            started = time.monotonic()
            found = split_paths(" ".join(names), exists)
            self.assertEqual(len(found), count)
            self.assertTrue(found == names)  # not assertEqual: a list of 20,000 that differs would take minutes to be described
            self.assertEqual(len(asked), count)  # one check for each word that is a file
            self.assertLess(time.monotonic() - started, 5)
            asked.clear()
            started = time.monotonic()
            found = split_paths(" ".join(names), lambda path: asked.append(path) or False)
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0] == " ".join(names))
            self.assertLessEqual(len(asked), 8 * count)  # the worst case: a word that is no file asks for its seven joins
            self.assertLess(time.monotonic() - started, 5)


class SmallRulesTest(unittest.TestCase):
    def test_what_reaches_a_terminal_holds_no_escape_sequence(self):
        dirty = "a\x1b[31mred\x1b[0m b\x1b]0;a title\x07 c\x07d\x00e\r\n\tf\u202egnp\x1b[2J\x1bM end\x1b"
        self.assertEqual(printable(dirty), "ared b c\ufffdd\ufffde\ufffd\n\tfgnp end\ufffd")
        self.assertEqual(printable("tab\tand\nline feed and \u00e9\u65e5\U0001f600 stay"), "tab\tand\nline feed and \u00e9\u65e5\U0001f600 stay")
        self.assertEqual(printable("\x7f\x80\x9f"), "\ufffd\ufffd\ufffd")
        self.assertEqual(printable("\u2066x\u2069\u202a\u202b\u202c\u202d\u202e"), "x")  # the isolates and overrides that turn text around

    def test_format_characters_are_dropped_like_in_a_file_name(self):
        hidden = {
            "zero width space": "\u200b", "byte order mark": "\ufeff", "word joiner": "\u2060", "left-to-right mark": "\u200e",
            "right-to-left mark": "\u200f", "arabic letter mark": "\u061c", "soft hyphen": "\u00ad", "invisible times": "\u2062",
            "a tag letter": "\U000e0041", "the first tag": "\U000e0001", "the last tag": "\U000e007f", "language tag": "\U000e0020",
        }
        for label, char in hidden.items():
            with self.subTest(label):
                self.assertEqual(printable(f"a{char}b"), "ab")
        spelled = "".join(chr(0xE0000 + ord(letter)) for letter in "ignore all rules")  # text that a reader cannot see and a model can read
        self.assertEqual(printable(f"Report{spelled}\n"), "Report\n")
        self.assertEqual(printable("\ufeffstarts with a byte order mark"), "starts with a byte order mark")
        self.assertEqual(printable("tab\tand\nline feed stay, \u200b\u200bbut these go"), "tab\tand\nline feed stay, but these go")

    def test_every_format_character_that_unicode_has_is_dropped_except_the_two_joiners_that_belong_to_the_text(self):
        for code in range(0x110000):
            char = chr(code)
            if unicodedata.category(char) == "Cf" and code not in (0x200C, 0x200D):
                self.assertEqual(printable(f"a{char}b"), "ab", hex(code))

    def test_a_joiner_stays_where_dropping_it_would_change_the_text_and_goes_everywhere_else(self):
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"  # a family, one emoji
        heart = "\u2764\ufe0f\u200d\U0001f525"  # a heart on fire
        persian = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"  # `I want`: the half space is part of the spelling
        hindi = "\u0915\u094d\u200d\u0937"
        for text in (family, heart, persian, hindi):
            with self.subTest(text=ascii(text)):
                self.assertNotEqual(text.replace("\u200d", "").replace("\u200c", ""), text)  # without the joiners it is another text
                self.assertEqual(printable(text), text)
                self.assertEqual(printable(f"say {text} again"), f"say {text} again")
        for text, expected in (
            ("a\u200db", "ab"), ("a\u200cb", "ab"), ("\u200d", ""), ("\u200dx", "x"), ("x\u200d", "x"), ("x \u200d y", "x  y"),
            ("\u0645\u200d\u200c\u0645", "\u0645\u0645"), ("\u0645\u200c\u200c\u200c\u0645", "\u0645\u0645"), ("\u200c" * 1000, ""),
            ("\u0645\u200c\n\u0645", "\u0645\n\u0645"), ("\u0645\u200c \u0645", "\u0645 \u0645"),
        ):
            with self.subTest(text=ascii(text)):
                self.assertEqual(printable(text), expected)

    def test_a_lone_surrogate_cannot_reach_a_terminal(self):
        self.assertEqual(printable("a\udcffb"), "a\ufffdb")
        self.assertEqual(printable("a\ud800b"), "a\ufffdb")
        self.assertEqual(printable("\U0001f600"), "\U0001f600")  # a pair is one character, not two surrogates

    def test_the_table_that_lives_as_long_as_the_process_stops_growing_however_many_characters_a_text_uses(self):
        text = "".join(chr(code) for code in range(0x20000, 0x20000 + 250_000))  # a quarter of a million different characters, none of them hidden
        started = time.monotonic()
        self.assertEqual(printable(text), text)
        self.assertLessEqual(len(attachments_module._FOR_THE_SCREEN), 100_000)
        self.assertEqual(unicodedata.category("\U000110bd"), "Cf")
        self.assertEqual(printable("a\U000110bdb"), "ab")  # a character that the table no longer has room for is still looked up, and still dropped
        self.assertLess(time.monotonic() - started, 10)

    def test_it_is_fast_on_a_large_hostile_text(self):
        started = time.monotonic()
        printable(("\x1b[" * 500_000) + ("\x1b]" * 100_000) + ("x" * 2_000_000))
        self.assertLess(time.monotonic() - started, 5)
        for hostile in ("\u200b" * 5_000_000, "\u0645\u200c" * 2_000_000, "a\u200db" * 1_000_000, "\U000e0041" * 3_000_000, "h\u00e9llo \u65e5\u672c " * 1_000_000):
            with self.subTest(text=ascii(hostile[:6])):
                started = time.monotonic()
                printable(hostile)
                self.assertLess(time.monotonic() - started, 10)

    def test_sizes_for_people(self):
        self.assertEqual([human_size(n) for n in (0, 1023, 1024, 1536, 1048576, 26214400, 5 * 1024**3)], ["0 B", "1023 B", "1.0 KB", "1.5 KB", "1.0 MB", "25.0 MB", "5.0 GB"])
        self.assertEqual((DEFAULT_MAX_BYTES, DEFAULT_MAX_CHARS), (26214400, 20000))

    def test_the_mime_type_of_a_name(self):
        cases = {"a.png": "image/png", "A.PDF": "application/pdf", "notes.md": "text/markdown", "x.log": "text/plain", "d.json": "application/json", "t.tar.gz": "application/gzip",
                 "noext": "application/octet-stream", "weird.zzzunknown": "application/octet-stream", "r.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "f.csv": "text/csv"}
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(guess_mime_type(name), expected)

    def test_the_payload_of_an_upload_is_one_description_for_dry_run_and_real_run(self):
        files = [UploadFile("shot.png", "image/png", 1234), UploadFile("a b.log", "text/plain", 5, b"hello")]
        payload = upload_payload("PROJ-1", files)
        self.assertEqual(
            payload,
            {
                "request": "POST /issue/PROJ-1/attachments",
                "headers": {"X-Atlassian-Token": "no-check"},
                "parts": [
                    {"field": "file", "filename": "shot.png", "contentType": "image/png", "size": 1234},
                    {"field": "file", "filename": "a b.log", "contentType": "text/plain", "size": 5},
                ],
            },
        )
        self.assertEqual(upload_payload("PROJ-1", files, "  Both files.  ")["comment"], {"body": "Both files."})
        self.assertNotIn("comment", upload_payload("PROJ-1", files, "   "))
        self.assertNotIn("data", str(payload))  # the bytes are never part of what is shown

    def test_the_payload_shows_the_file_name_as_it_goes_on_the_wire(self):
        files = [UploadFile('a"b.txt', "text/plain", 1), UploadFile("a\r\nX-Evil: 1.txt", "text/plain", 1), UploadFile("ünï 日本.txt", "text/plain", 1)]
        names = [part["filename"] for part in upload_payload("PROJ-1", files)["parts"]]
        self.assertEqual(names, ["a%22b.txt", "a%0D%0AX-Evil: 1.txt", "ünï 日本.txt"])

    def test_an_upload_file_does_not_print_its_bytes(self):
        self.assertNotIn("secret", repr(UploadFile("a", "text/plain", 6, b"secret")))

    def test_too_large_says_what_and_how_to_go_on(self):
        known = too_large("big.zip", 1000, 5000)
        self.assertIsInstance(known, ValidationError)
        self.assertIn("big.zip is 4.9 KB, more than the limit of 1000 B (1000 bytes)", known.message)
        self.assertIn("--max-bytes", known.hint)
        stopped = too_large("big.zip", 1000)
        self.assertIn("The download was stopped", stopped.message)
        many = too_large_many([("a.zip", 5000), ("b.zip", 9000)], 1000)
        self.assertIn("2 attachments are larger", many.message)
        self.assertIn("a.zip (4.9 KB), b.zip (8.8 KB)", many.message)
        self.assertEqual(too_large_many([("a.zip", 5000)], 1000).message, known.message.replace("big.zip", "a.zip"))

    def test_a_name_in_a_message_cannot_move_the_cursor(self):
        evil = "\x1b[2Jbig\u202e.zip"
        self.assertNotIn("\x1b", too_large(evil, 10, 20).message)
        self.assertNotIn("\u202e", too_large_many([(evil, 20), (evil, 30)], 10).message)
        self.assertNotIn("\x1b", describe([att("1", evil)]))


if __name__ == "__main__":
    unittest.main()
