"""The multipart/form-data body of an upload: its exact bytes, and that it reads back, whatever the files hold."""
import random
import unittest

from jira_dc_cli.domain.multipart import FALLBACK_TYPE, FilePart, encode_multipart, is_sendable_name, wire_filename


def read_back(body: bytes, content_type: str) -> list[dict]:
    """The parts of a multipart body, read by hand as a server does (the `cgi` module is gone from Python 3.13)."""
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1].encode("ascii")
    assert body.startswith(b"--" + boundary + b"\r\n"), "the body starts with the first delimiter"
    assert body.endswith(b"\r\n--" + boundary + b"--\r\n"), "the body ends with the closing delimiter and a line break"
    chunks = (b"\r\n" + body).split(b"\r\n--" + boundary)
    assert chunks[0] == b"" and chunks[-1] == b"--\r\n"
    parts = []
    for chunk in chunks[1:-1]:
        assert chunk.startswith(b"\r\n")
        head, separator, data = chunk[2:].partition(b"\r\n\r\n")
        assert separator
        headers = dict(line.split(": ", 1) for line in head.decode("utf-8").split("\r\n"))
        parts.append({"disposition": headers["Content-Disposition"], "type": headers["Content-Type"], "data": data})
    return parts


class EncodeMultipartTest(unittest.TestCase):
    def test_the_exact_bytes_of_one_file(self):
        body, content_type = encode_multipart([FilePart("a.txt", b"hello", "text/plain")], boundary="XYZ")
        self.assertEqual(
            body,
            b'--XYZ\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\nhello\r\n--XYZ--\r\n',
        )
        self.assertEqual(content_type, "multipart/form-data; boundary=XYZ")

    def test_several_files_are_several_parts_all_named_file(self):
        body, content_type = encode_multipart(
            [FilePart("a.txt", b"one", "text/plain"), FilePart("b.png", b"\x89PNG", "image/png"), FilePart("a.txt", b"two", "text/plain")]
        )
        parts = read_back(body, content_type)
        self.assertEqual([part["data"] for part in parts], [b"one", b"\x89PNG", b"two"])
        self.assertTrue(all(part["disposition"].startswith('form-data; name="file"; filename="') for part in parts))
        self.assertEqual([part["type"] for part in parts], ["text/plain", "image/png", "text/plain"])

    def test_whatever_the_files_hold_it_reads_back(self):
        generator = random.Random(7)
        contents = [
            b"",
            bytes(range(256)),
            b"line one\r\nline two\r\n\r\n",
            b"\r\n",
            b"--",
            b"\r\n--not-the-boundary\r\n",
            b"--jira-dc-cli-0123456789abcdef0123456789abcdef--",
            bytes(generator.randrange(256) for _ in range(5000)),
            b"x" * 300_000,
        ]
        parts = [FilePart(f"file{index}.bin", data) for index, data in enumerate(contents)]
        body, content_type = encode_multipart(parts)
        self.assertEqual([item["data"] for item in read_back(body, content_type)], contents)

    def test_a_name_is_written_as_a_browser_writes_it(self):
        body, content_type = encode_multipart([FilePart('say "hi".txt', b"1"), FilePart("two\r\nlines.txt", b"2"), FilePart("Übersicht 日本.pdf", b"3"), FilePart("back\\slash.txt", b"4")])
        names = [part["disposition"] for part in read_back(body, content_type)]
        self.assertIn('filename="say %22hi%22.txt"', names[0])  # a quote cannot end the value
        self.assertIn('filename="two%0D%0Alines.txt"', names[1])  # a line break cannot end the header
        self.assertIn('filename="Übersicht 日本.pdf"', names[2])  # UTF-8 as it is, which Jira reads
        self.assertIn('filename="back\\slash.txt"', names[3])
        self.assertEqual(len(read_back(body, content_type)), 4)

    def test_a_type_that_could_start_another_header_is_replaced(self):
        body, content_type = encode_multipart([FilePart("a", b"x", "text/plain\r\nX-Evil: 1"), FilePart("b", b"y", ""), FilePart("c", b"z", "not a type")])
        self.assertNotIn(b"X-Evil", body)
        self.assertEqual([part["type"] for part in read_back(body, content_type)], [FALLBACK_TYPE] * 3)

    def test_the_boundary_is_made_from_the_content_and_stands_in_none_of_it(self):
        parts = [FilePart("a.bin", bytes(range(256)) * 10)]
        first = encode_multipart(parts)
        self.assertEqual(first, encode_multipart(parts))  # no randomness: the same files make the same body
        self.assertNotEqual(first[1], encode_multipart([FilePart("a.bin", b"other")])[1])
        boundary = first[1].split("boundary=")[1].encode("ascii")
        self.assertNotIn(boundary, parts[0].data)

    def test_a_boundary_that_stands_in_a_part_is_refused_and_a_malformed_one_too(self):
        with self.assertRaises(ValueError):
            encode_multipart([FilePart("a", b"before XYZ after")], boundary="XYZ")
        for boundary in ("", "x" * 71, "with space", "line\r\nbreak", "ünicode"):
            with self.subTest(boundary=boundary):
                with self.assertRaises(ValueError):
                    encode_multipart([FilePart("a", b"x")], boundary=boundary)

    def test_no_part_is_no_body(self):
        with self.assertRaises(ValueError):
            encode_multipart([])

    def test_the_wire_name_is_what_the_body_carries(self):
        for name in ('a"b.txt', "a\r\nX-Evil: 1.txt", "a\rb", "a\nb", "ünï 日本.txt", "plain.txt", '""', "%22.txt"):
            with self.subTest(name=name):
                body, content_type = encode_multipart([FilePart(name, b"x")])
                self.assertIn(f'filename="{wire_filename(name)}"\r\n'.encode("utf-8"), body)
                self.assertEqual(len(read_back(body, content_type)), 1)
        self.assertEqual(wire_filename('a"b'), "a%22b")
        self.assertEqual(wire_filename("a\r\nb"), "a%0D%0Ab")
        self.assertEqual(wire_filename("%22"), "%22")  # a percent sign is no escape that has to be escaped: a browser leaves it

    def test_a_name_with_a_lone_surrogate_has_no_utf8_form_and_never_breaks_the_encoding(self):
        for name in ("a\udcffb.txt", "\ud800", "x\udfffy"):
            with self.subTest(name=ascii(name)):
                self.assertFalse(is_sendable_name(name))
                body, content_type = encode_multipart([FilePart(name, b"payload")])  # no UnicodeEncodeError: the damaged character is replaced
                (part,) = read_back(body, content_type)
                self.assertEqual(part["data"], b"payload")
                self.assertIn("?", part["disposition"])
        for name in ("a.txt", "ünï 日本.txt", "\U0001f600.png", "", 'q"uote'):
            with self.subTest(name=ascii(name)):
                self.assertTrue(is_sendable_name(name))


if __name__ == "__main__":
    unittest.main()
