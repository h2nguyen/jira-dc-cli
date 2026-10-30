"""Safe file names for what Jira calls an attachment: hostile, odd and long names, and names that clash."""
import itertools
import random
import unicodedata
import unittest

from jira_dc_cli.domain.filenames import MAX_BYTES, MAX_CHARS, safe_filename, unique_filenames

DEVICES = ("CON", "con", "Prn", "AUX", "NUL", "COM1", "com9", "LPT1", "lpt9", "COM\u00b9", "LPT\u00b3", "CONIN$", "conout$")


def is_safe(name: str) -> bool:
    """What every result must be, whatever went in."""
    return (
        bool(name)
        and not any(char in name for char in '/\\:<>"|?*')
        and not any(unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") for char in name)
        and name == name.rstrip(". ")
        and name not in (".", "..")
        and name.split(".", 1)[0].rstrip(" ").casefold() not in {device.casefold() for device in DEVICES}
        and len(name) <= MAX_CHARS
        and len(name.encode("utf-8")) <= MAX_BYTES
    )


class SafeFilenameTest(unittest.TestCase):
    def test_ordinary_names_stay_as_they_are(self):
        for name in ("report.pdf", "Übersicht ñ 日本語.docx", "name with spaces (1).png", ".bashrc", "archive.tar.gz", "no_extension", "a" * 150):
            with self.subTest(name=name):
                self.assertEqual(safe_filename(name, "1"), name)

    def test_nothing_leaves_the_folder(self):
        cases = {
            "..\\..\\x": "x",
            "../../etc/passwd": "passwd",
            "/etc/shadow": "shadow",
            "C:\\Windows\\system32\\a.txt": "a.txt",
            "C:a.txt": "a.txt",  # a drive without a folder is relative to the current folder of that drive
            "\\\\server\\share\\f.txt": "f.txt",
            "a/b\\c.txt": "c.txt",
            "..": "attachment-7",
            "foo/..": "attachment-7",
            "foo/": "attachment-7",
            "...": "attachment-7",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(safe_filename(name, "7"), expected)

    def test_characters_that_windows_refuses_and_control_characters_become_underscores(self):
        self.assertEqual(safe_filename('a<b>c:d"e|f?g*h.txt'), "a_b_c_d_e_f_g_h.txt")
        self.assertEqual(safe_filename("a\x00b\x1f\x7f.txt"), "a_b__.txt")
        self.assertEqual(safe_filename("tab\tand\nnewline.txt"), "tab_and_newline.txt")

    def test_invisible_characters_are_dropped(self):
        self.assertEqual(safe_filename("\u202egnp.exe"), "gnp.exe")  # a right-to-left override would turn `png.exe` around on the screen
        self.assertEqual(safe_filename("a\u200bb\ufeff.txt"), "ab.txt")
        self.assertEqual(safe_filename("lone\ud800surrogate.txt"), "lonesurrogate.txt")  # no file system takes it

    def test_an_alternate_data_stream_cannot_be_opened(self):
        self.assertEqual(safe_filename("report.txt:Zone.Identifier"), "report.txt_Zone.Identifier")

    def test_trailing_dots_and_blanks_go_because_windows_would_drop_them_silently(self):
        for name, expected in (("name. . ", "name"), ("  lead.txt", "lead.txt"), ("name.txt.", "name.txt"), ("a b .", "a b"), ("x. ", "x")):
            with self.subTest(name=name):
                self.assertEqual(safe_filename(name), expected)

    def test_windows_device_names_get_a_prefix_with_or_without_an_extension(self):
        for device in DEVICES:
            for name in (device, f"{device}.txt", f"{device}.tar.gz", f"{device} .txt", f"{device}."):
                with self.subTest(name=name):
                    safe = safe_filename(name)
                    self.assertTrue(safe.startswith("_"), safe)
                    self.assertTrue(is_safe(safe))
        for name in ("console.txt", "com10.txt", "nullable", "lpt0.txt", "xcon.txt", "aux_.txt", "com.txt"):
            with self.subTest(name=name):
                self.assertEqual(safe_filename(name), name)

    def test_a_long_name_is_shortened_and_keeps_its_extension(self):
        safe = safe_filename("a" * 300 + ".txt")
        self.assertEqual((len(safe), safe[-4:]), (MAX_CHARS, ".txt"))
        long_tail = safe_filename("x." + "b" * 100)
        self.assertEqual(len(long_tail), 102)  # a tail that long is no extension, and 102 characters fit
        self.assertLessEqual(len(safe_filename("y." + "b" * 400)), MAX_CHARS)

    def test_a_long_name_in_a_wide_script_is_kept_within_the_bytes_of_a_file_system(self):
        safe = safe_filename("日本語" * 100 + ".pdf")
        self.assertTrue(safe.endswith(".pdf"))
        self.assertLessEqual(len(safe.encode("utf-8")), MAX_BYTES)
        self.assertTrue(is_safe(safe))

    def test_a_name_that_is_nothing_becomes_attachment_and_the_id(self):
        self.assertEqual(safe_filename("", "10001"), "attachment-10001")
        self.assertEqual(safe_filename("   ", "10001"), "attachment-10001")
        self.assertEqual(safe_filename("", ""), "attachment")
        self.assertEqual(safe_filename("", "../x"), "attachment-x")  # the id is cleaned too
        self.assertEqual(safe_filename("....pdf", "5"), "attachment-5.pdf")
        self.assertEqual(safe_filename(None, "5"), "attachment-5")  # type: ignore[arg-type]

    def test_whatever_goes_in_a_safe_name_comes_out(self):
        pool = list("ab. :/\\<>|?*\"'~$%") + ["\x00", "\n", "\u202e", "\u200b", "\ud800", "é", "日", "CON", "NUL", "..", " .", "COM1", "lpt3", ".txt"]
        generator = random.Random(2026)
        for _ in range(3000):
            name = "".join(generator.choice(pool) for _ in range(generator.randint(0, 40)))
            with self.subTest(name=name):
                self.assertTrue(is_safe(safe_filename(name, "10001")), repr(safe_filename(name, "10001")))
        for name in ("x" * 5000, "日" * 5000, ("a." * 300), "." * 400 + "txt", "CON." + "x" * 400):
            self.assertTrue(is_safe(safe_filename(name, "1")), name[:20])


class UniqueFilenamesTest(unittest.TestCase):
    def test_different_names_stay(self):
        self.assertEqual(unique_filenames([("1", "a.txt"), ("2", "b.txt")]), {"1": "a.txt", "2": "b.txt"})
        self.assertEqual(unique_filenames([]), {})

    def test_every_name_that_would_clash_gets_its_id_before_the_extension(self):
        result = unique_filenames([("1", "a.txt"), ("2", "a.txt"), ("3", "b.txt"), ("4", "a.txt")])
        self.assertEqual(result, {"1": "a-1.txt", "2": "a-2.txt", "3": "b.txt", "4": "a-4.txt"})

    def test_names_that_differ_in_case_only_clash_because_windows_and_macos_see_one_file(self):
        self.assertEqual(unique_filenames([("1", "A.txt"), ("2", "a.TXT")]), {"1": "A-1.txt", "2": "a-2.TXT"})

    def test_names_that_clash_only_after_cleaning(self):
        result = unique_filenames([("1", "..\\x.txt"), ("2", "x.txt"), ("3", "CON"), ("4", "_CON")])
        self.assertEqual(result["1"], "x-1.txt")
        self.assertEqual(result["2"], "x-2.txt")
        self.assertEqual({result["3"], result["4"]}, {"_CON-3", "_CON-4"})

    def test_the_result_does_not_depend_on_the_order(self):
        entries = [("1", "a.txt"), ("2", "A.txt"), ("3", "b"), ("4", "../a.txt"), ("5", "")]
        expected = unique_filenames(entries)
        for order in itertools.permutations(entries):
            self.assertEqual(unique_filenames(list(order)), expected)

    def test_a_tag_that_is_a_name_already_does_not_make_two_files_one(self):
        result = unique_filenames([("1", "a.txt"), ("2", "a.txt"), ("3", "a-1.txt")])
        self.assertEqual(len({name.casefold() for name in result.values()}), 3)
        self.assertEqual(result["1"], "a-1.txt")

    def test_long_clashing_names_stay_within_the_limits_and_keep_their_id(self):
        result = unique_filenames([("10001", "n" * 300 + ".txt"), ("10002", "n" * 300 + ".txt")])
        for identifier, name in result.items():
            self.assertTrue(is_safe(name))
            self.assertTrue(name.endswith(f"-{identifier}.txt"), name)

    def test_nameless_attachments_are_told_apart(self):
        self.assertEqual(unique_filenames([("1", ""), ("2", "")]), {"1": "attachment-1", "2": "attachment-2"})


if __name__ == "__main__":
    unittest.main()
