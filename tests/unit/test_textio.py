"""Decoding the text files that a person, or Windows PowerShell 5.1, wrote."""
import unittest

from jira_dc_cli.domain.textio import decode_text

TEXT = 'project = X AND "Epic Link" = Y ORDER BY key — Überschrift'


class DecodeTextTest(unittest.TestCase):
    def test_utf8_without_and_with_a_byte_order_mark(self):
        self.assertEqual(decode_text(TEXT.encode("utf-8")), TEXT)
        self.assertEqual(decode_text(b"\xef\xbb\xbf" + TEXT.encode("utf-8")), TEXT)

    def test_utf16_as_powershell_writes_it(self):
        self.assertEqual(decode_text(TEXT.encode("utf-16")), TEXT)  # little endian with a byte order mark
        self.assertEqual(decode_text(b"\xfe\xff" + TEXT.encode("utf-16-be")), TEXT)

    def test_utf32(self):
        self.assertEqual(decode_text(TEXT.encode("utf-32")), TEXT)

    def test_utf16_without_a_byte_order_mark(self):
        for encoding in ("utf-16-le", "utf-16-be"):
            with self.subTest(encoding=encoding):
                self.assertEqual(decode_text(TEXT.encode(encoding)), TEXT)
                self.assertEqual(decode_text("a".encode(encoding)), "a")
                self.assertNotIn("\x00", decode_text('project = "Größe"'.encode(encoding)))

    def test_text_that_only_looks_a_little_like_utf16_is_not_changed(self):
        for text in ("plain text", "Größe — ünïcode ✓", "x\x00y is no UTF-16, the rest of it is ASCII " * 3):
            with self.subTest(text=text[:12]):
                self.assertEqual(decode_text(text.encode("utf-8")), text)

    def test_ansi_text_that_is_no_utf8(self):
        self.assertEqual(decode_text("Überschrift".encode("cp1252")), "Überschrift")

    def test_empty(self):
        self.assertEqual(decode_text(b""), "")


if __name__ == "__main__":
    unittest.main()
