"""The use cases of attachments, with a gateway and files in memory: no HTTP, no disk."""
import dataclasses
import io
import time
import unittest
import zipfile
from unittest import mock

from jira_dc_cli.domain.errors import AuthError, NotFoundError, RejectedError, UnavailableError, UsageError, ValidationError
from tests.support.fakes import DEFAULT_SETTINGS, FakeGateway, MemoryFiles, RecordingDiagnostics, build_app
from tests.support.places_attachments import ExclusiveFiles
from tests.unit.test_attachments_ooxml import docx, para, strings_workbook

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


def settings(**changes):
    return dataclasses.replace(DEFAULT_SETTINGS, **changes)


class Case(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.files = MemoryFiles()
        self.diagnostics = RecordingDiagnostics()
        self.app = self.build()

    def build(self, **options):
        return build_app(gateway=self.gateway, files=self.files, diagnostics=self.diagnostics, **options).attachments

    def downloads(self):
        return self.gateway.called("download_attachment")


class ListTest(Case):
    def setUp(self):
        super().setUp()
        self.shot = self.gateway.add_attachment("PROJ-1", "shot.png", PNG, "image/png")
        self.notes = self.gateway.add_attachment("PROJ-1", "Notes.txt", b"hello", "text/plain")

    def test_it_lists_what_the_issue_has_and_leaves_the_people_out(self):
        result = self.app.list_attachments.execute("proj-1")
        self.assertEqual((result.key, result.total, [item.filename for item in result.attachments]), ("PROJ-1", 2, ["shot.png", "Notes.txt"]))
        self.assertTrue(all(item.author is None for item in result.attachments))
        self.assertEqual([call[0] for call in self.gateway.calls], ["list_attachments"])

    def test_the_uploader_only_with_users_and_never_under_the_privacy_policy(self):
        shown = self.app.list_attachments.execute("PROJ-1", with_users=True).attachments
        self.assertEqual(shown[0].author.name, "jdoe")
        guarded = self.build(settings=settings(hide_users=True)).list_attachments
        with self.assertRaises(ValidationError) as caught:
            guarded.execute("PROJ-1", with_users=True)
        self.assertIn("JIRA_HIDE_USERS", caught.exception.message)
        self.assertIsNone(guarded.execute("PROJ-1").attachments[0].author)

    def test_a_name_keeps_the_files_with_that_name_and_the_total_stays(self):
        result = self.app.list_attachments.execute("PROJ-1", name="notes.TXT")
        self.assertEqual(([item.id for item in result.attachments], result.total), ([self.notes.id], 2))
        self.assertEqual(self.app.list_attachments.execute("PROJ-1", name="nothing").attachments, [])

    def test_an_issue_without_attachments_and_one_that_does_not_exist(self):
        self.assertEqual(self.app.list_attachments.execute("PROJ-2").total, 0)
        with self.assertRaises(NotFoundError):
            self.app.list_attachments.execute("PROJ-999")
        with self.assertRaises(ValidationError):
            self.app.list_attachments.execute("not a key")


class DownloadTest(Case):
    def setUp(self):
        super().setUp()
        self.shot = self.gateway.add_attachment("PROJ-1", "shot.png", PNG, "image/png")
        self.notes = self.gateway.add_attachment("PROJ-1", "notes.txt", b"hello", "text/plain")

    def run_download(self, **options):
        return self.app.download.execute("PROJ-1", **{"everything": True, **options})

    def test_everything_is_saved_in_the_default_folder_under_its_names(self):
        result = self.run_download()
        self.assertEqual(result.out_dir, "tmp/jira-attachments/PROJ-1")
        self.assertEqual([(item.filename, item.path, item.action, item.size) for item in result.files], [
            ("shot.png", "tmp/jira-attachments/PROJ-1/shot.png", "written", len(PNG)),
            ("notes.txt", "tmp/jira-attachments/PROJ-1/notes.txt", "written", 5),
        ])
        self.assertEqual(self.files.read_bytes("tmp/jira-attachments/PROJ-1/shot.png"), PNG)  # byte for byte
        self.assertEqual(self.files.read_bytes("tmp/jira-attachments/PROJ-1/notes.txt"), b"hello")

    def test_one_by_id_and_by_name_into_a_chosen_folder(self):
        by_id = self.app.download.execute("PROJ-1", identifier=self.notes.id, out="here")
        self.assertEqual([item.path for item in by_id.files], ["here/notes.txt"])
        by_name = self.app.download.execute("PROJ-1", names=["SHOT.png"], out="there")
        self.assertEqual([item.path for item in by_name.files], ["there/shot.png"])
        self.assertEqual(sorted(self.files.blobs), ["here/notes.txt", "there/shot.png"])

    def test_a_hostile_name_never_writes_outside_the_folder(self):
        for name in ("..\\..\\evil1.txt", "../../evil2.txt", "C:\\evil3.txt", "/etc/evil4", "CON", "a.txt:stream"):
            self.gateway.add_attachment("PROJ-1", name, b"x", "text/plain")
        result = self.run_download(out="out")
        for item in result.files:
            self.assertTrue(item.path.startswith("out/") and item.path.count("/") == 1, item.path)
        self.assertTrue(all(path.startswith("out/") and path.count("/") == 1 for path in self.files.blobs))
        self.assertEqual(
            sorted(self.files.blobs), ["out/_CON", "out/a.txt_stream", "out/evil1.txt", "out/evil2.txt", "out/evil3.txt", "out/evil4", "out/notes.txt", "out/shot.png"]
        )

    def test_two_files_with_one_name_both_get_their_id(self):
        second = self.gateway.add_attachment("PROJ-1", "notes.txt", b"other", "text/plain")
        paths = [item.path for item in self.run_download().files]
        self.assertEqual(paths[1:], [f"tmp/jira-attachments/PROJ-1/notes-{self.notes.id}.txt", f"tmp/jira-attachments/PROJ-1/notes-{second.id}.txt"])
        self.assertEqual(self.files.read_bytes(paths[2]), b"other")

    def test_a_dry_run_says_what_would_happen_and_fetches_and_writes_nothing(self):
        result = self.run_download(dry_run=True)
        self.assertEqual([item.action for item in result.files], ["would-write", "would-write"])
        self.assertTrue(result.dry_run)
        self.assertEqual((self.files.blobs, self.files.files, self.downloads()), ({}, {}, []))

    def test_no_file_is_overwritten_without_force_and_nothing_is_fetched_first(self):
        self.files.write_bytes("tmp/jira-attachments/PROJ-1/notes.txt", b"mine")
        with self.assertRaises(ValidationError) as caught:
            self.run_download()
        self.assertIn("notes.txt already exists in tmp/jira-attachments/PROJ-1", caught.exception.message)
        self.assertIn("--force", caught.exception.hint)
        self.assertEqual(self.downloads(), [])  # nothing was fetched
        self.assertEqual(self.files.read_bytes("tmp/jira-attachments/PROJ-1/notes.txt"), b"mine")
        self.assertNotIn("tmp/jira-attachments/PROJ-1/shot.png", self.files.blobs)  # the other file waited too

    def test_every_file_that_is_there_is_named(self):
        for name in ("shot.png", "notes.txt"):
            self.files.write_bytes(f"tmp/jira-attachments/PROJ-1/{name}", b"mine")
        with self.assertRaises(ValidationError) as caught:
            self.run_download()
        self.assertIn("2 files already exist", caught.exception.message)
        self.assertIn("shot.png, notes.txt", caught.exception.message)

    def test_with_force_a_file_is_overwritten_and_a_dry_run_says_so(self):
        self.files.write_bytes("tmp/jira-attachments/PROJ-1/notes.txt", b"mine")
        preview = self.run_download(dry_run=True)
        self.assertEqual([item.action for item in preview.files], ["would-write", "exists"])  # a preview does not fail: it tells
        forced_preview = self.run_download(dry_run=True, force=True)
        self.assertEqual([item.action for item in forced_preview.files], ["would-write", "would-overwrite"])
        result = self.run_download(force=True)
        self.assertEqual([item.action for item in result.files], ["written", "overwritten"])
        self.assertEqual(self.files.read_bytes("tmp/jira-attachments/PROJ-1/notes.txt"), b"hello")

    def test_a_file_over_the_limit_is_refused_by_its_metadata_before_the_request(self):
        big = self.gateway.add_attachment("PROJ-1", "big.zip", b"z" * 5000, "application/zip")
        self.gateway.add_attachment("PROJ-1", "bigger.zip", b"z" * 9000, "application/zip")
        with self.assertRaises(ValidationError) as caught:
            self.run_download(max_bytes=1000)
        self.assertIn("2 attachments are larger than the limit", caught.exception.message)
        self.assertIn("big.zip", caught.exception.message)
        self.assertIn("bigger.zip", caught.exception.message)
        self.assertIn("--max-bytes", caught.exception.hint)
        self.assertEqual((self.downloads(), self.files.blobs), ([], {}))
        single = self.app.download.execute("PROJ-1", identifier=big.id, max_bytes=1000, dry_run=True)
        self.assertEqual([item.action for item in single.files], ["too-large"])

    def test_the_limit_is_enforced_while_reading_when_the_metadata_lies(self):
        liar = self.gateway.add_attachment("PROJ-1", "liar.bin", b"y" * 5000, "application/octet-stream", size=10)
        with self.assertRaises(ValidationError) as caught:
            self.app.download.execute("PROJ-1", identifier=liar.id, max_bytes=1000)
        self.assertIn("liar.bin is larger", caught.exception.message)
        self.assertEqual(self.files.blobs, {})
        self.assertEqual(self.downloads()[0][2], 1000)  # the limit went to the gateway, which enforces it on the wire

    def test_a_size_that_differs_from_the_metadata_is_a_warning_and_the_real_size_is_reported(self):
        short = self.gateway.add_attachment("PROJ-1", "short.txt", b"abc", "text/plain", size=99)
        result = self.app.download.execute("PROJ-1", identifier=short.id)
        self.assertEqual(result.files[0].size, 3)
        self.assertIn("short.txt: Jira lists 99 bytes and 3 arrived", self.diagnostics.warnings[0])

    def test_a_choice_that_is_missing_or_wrong_is_an_error_that_says_what_exists(self):
        with self.assertRaises(UsageError) as caught:
            self.app.download.execute("PROJ-1")
        self.assertIn("shot.png", caught.exception.hint)
        with self.assertRaises(NotFoundError):
            self.app.download.execute("PROJ-1", identifier="12345")
        with self.assertRaises(NotFoundError):
            self.app.download.execute("PROJ-1", names=["absent.txt"])
        self.gateway.add_attachment("PROJ-1", "NOTES.txt", b"again")
        with self.assertRaises(UsageError) as caught:
            self.app.download.execute("PROJ-1", names=["notes.txt"])
        self.assertIn(self.notes.id, caught.exception.message)
        self.assertEqual(self.downloads(), [])

    def test_all_on_an_issue_without_attachments_is_an_empty_result(self):
        result = self.app.download.execute("PROJ-2", everything=True)
        self.assertEqual((result.files, self.downloads(), self.files.blobs), ([], [], {}))

    def test_a_folder_that_is_a_file_is_refused_and_the_options_are_checked(self):
        self.files.write_bytes("taken", b"x")
        with self.assertRaises(ValidationError) as caught:
            self.run_download(out="taken")
        self.assertIn("is a file, not a folder", caught.exception.message)
        for bad in (0, -5):
            with self.assertRaises(UsageError):
                self.run_download(max_bytes=bad)

    def test_an_unknown_issue_and_a_bad_key(self):
        with self.assertRaises(NotFoundError):
            self.app.download.execute("PROJ-999", everything=True)
        with self.assertRaises(ValidationError):
            self.app.download.execute("nokey", everything=True)

    def test_the_result_never_holds_the_bytes(self):
        result = self.run_download()
        self.assertNotIn("PNG", repr(result))

    def test_the_option_for_people_is_refused_under_the_privacy_policy_also_when_saving(self):
        guarded = self.build(settings=settings(hide_users=True))
        with self.assertRaises(ValidationError) as caught:
            guarded.download.execute("PROJ-1", everything=True, with_users=True)
        self.assertIn("JIRA_HIDE_USERS", caught.exception.message)
        self.assertEqual(self.downloads(), [])
        self.assertEqual(len(guarded.download.execute("PROJ-1", everything=True).files), 2)


class ReadTextTest(Case):
    def read(self, **options):
        return self.app.read_text.execute("PROJ-1", **{"everything": True, **options})

    def test_a_text_file_comes_back_as_text_with_its_length(self):
        self.gateway.add_attachment("PROJ-1", "notes.txt", b"line one\r\nline two\r\n", "text/plain")
        entry = self.read().attachments[0]
        self.assertEqual((entry.kind, entry.action, entry.text, entry.truncated, entry.total_chars, entry.hint), ("text", "read", "line one\nline two\n", False, 18, ""))
        self.assertEqual(self.files.blobs, {})  # nothing is written

    def test_the_encoding_is_found_the_way_an_editor_or_powershell_wrote_it(self):
        for data, expected in (("Grüße ünï".encode("utf-8"), "Grüße ünï"), ("Grüße".encode("utf-8-sig"), "Grüße"), ("Grüße".encode("utf-16"), "Grüße"), ("Grüße".encode("cp1252"), "Grüße")):
            with self.subTest(data=data):
                gateway = FakeGateway()
                gateway.add_attachment("PROJ-1", "t.txt", data, "text/plain")
                self.assertEqual(build_app(gateway=gateway).attachments.read_text.execute("PROJ-1", everything=True).attachments[0].text, expected)

    def test_mentions_and_addresses_are_redacted_unless_people_are_asked_for(self):
        self.gateway.add_attachment("PROJ-1", "mail.txt", b"Ask [~jdoe] or jane.doe@example.com.", "text/plain")
        self.assertEqual(self.read().attachments[0].text, "Ask [~user] or [email].")
        self.assertEqual(self.read(with_users=True).attachments[0].text, "Ask [~jdoe] or jane.doe@example.com.")
        with self.assertRaises(ValidationError):
            self.build(settings=settings(hide_users=True)).read_text.execute("PROJ-1", everything=True, with_users=True)
        guarded = self.build(settings=settings(hide_users=True)).read_text.execute("PROJ-1", everything=True)
        self.assertEqual(guarded.attachments[0].text, "Ask [~user] or [email].")

    def test_an_address_is_redacted_before_the_cut_so_that_half_of_it_cannot_slip_through(self):
        self.gateway.add_attachment("PROJ-1", "mail.txt", ("x" * 20 + " jane.doe@example.com tail").encode(), "text/plain")
        for limit in range(15, 45):
            with self.subTest(limit=limit):
                shown = self.read(max_chars=limit).attachments[0].text
                self.assertNotIn("jane", shown)
                self.assertNotIn("@exam", shown)

    def test_a_long_text_is_cut_at_a_paragraph_and_says_how_long_it_is(self):
        text = "\n\n".join(f"paragraph number {n} " + "word " * 10 for n in range(20))
        self.gateway.add_attachment("PROJ-1", "long.md", text.encode(), "text/markdown")
        entry = self.read(max_chars=300).attachments[0]
        self.assertTrue(entry.truncated)
        self.assertEqual(entry.total_chars, len(text))
        self.assertLessEqual(len(entry.text), 300)
        self.assertTrue(entry.text.rstrip().endswith("word"), entry.text[-20:])
        self.assertTrue(text.startswith(entry.text))
        self.assertFalse(self.read(max_chars=len(text)).attachments[0].truncated)

    def test_a_file_that_holds_escape_sequences_does_not_send_them_to_the_terminal(self):
        self.gateway.add_attachment("PROJ-1", "colours.log", b"\x1b[31mred\x1b[0m line\r\nnext \x1b]0;title\x07 line\r\n", "text/plain")
        entry = self.read().attachments[0]
        self.assertEqual(entry.text, "red line\nnext  line\n")
        self.assertEqual(entry.total_chars, len(entry.text))

    def test_an_image_gets_its_metadata_and_a_hint_and_is_never_fetched(self):
        shot = self.gateway.add_attachment("PROJ-1", "shot.png", PNG, "image/png")
        entry = self.read().attachments[0]
        self.assertEqual((entry.kind, entry.action, entry.text, entry.total_chars, entry.size), ("other", "not-text", "", None, len(PNG)))
        self.assertIn(f"jira download PROJ-1 {shot.id}", entry.hint)
        self.assertEqual(self.downloads(), [])
        self.assertNotIn("PNG", repr(entry))

    def test_content_that_is_binary_despite_its_name_is_not_shown(self):
        self.gateway.add_attachment("PROJ-1", "fake.txt", b"PK\x03\x04\x00\x00\x00binary", "text/plain")
        entry = self.read().attachments[0]
        self.assertEqual((entry.action, entry.text), ("not-text", ""))
        self.assertIn("binary", entry.hint)
        self.assertIn("jira download PROJ-1", entry.hint)

    def test_an_empty_text_file(self):
        self.gateway.add_attachment("PROJ-1", "empty.txt", b"", "text/plain")
        entry = self.read().attachments[0]
        self.assertEqual((entry.action, entry.text, entry.total_chars, entry.hint), ("read", "", 0, "The file holds no text."))

    def test_office_documents_are_read_and_a_damaged_or_hostile_one_is_a_hint_not_a_failure(self):
        self.gateway.add_attachment("PROJ-1", "spec.docx", docx(para("Heading", "Heading1") + para("Body text for jane.doe@example.com")), "application/octet-stream")
        self.gateway.add_attachment("PROJ-1", "broken.docx", b"this is no zip", "application/octet-stream")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"A" * 60_000_000)
        self.gateway.add_attachment("PROJ-1", "bomb.docx", buffer.getvalue(), "application/octet-stream")
        self.gateway.add_attachment("PROJ-1", "after.txt", b"still read", "text/plain")
        entries = self.read().attachments
        self.assertEqual([(entry.filename, entry.kind, entry.action) for entry in entries], [
            ("spec.docx", "docx", "read"), ("broken.docx", "docx", "not-text"), ("bomb.docx", "docx", "not-text"), ("after.txt", "text", "read"),
        ])
        self.assertEqual(entries[0].text, "# Heading\nBody text for [email]")
        self.assertIn("ZIP", entries[1].hint)
        self.assertIn("bomb", entries[2].hint)
        self.assertIn("jira download PROJ-1", entries[2].hint)
        self.assertEqual(entries[3].text, "still read")

    def test_a_file_over_the_limit_is_refused_before_anything_is_fetched_and_an_image_is_no_matter(self):
        self.gateway.add_attachment("PROJ-1", "small.txt", b"ok", "text/plain")
        self.gateway.add_attachment("PROJ-1", "huge.txt", b"h" * 5000, "text/plain")
        self.gateway.add_attachment("PROJ-1", "huge.png", b"h" * 5000, "image/png")
        with self.assertRaises(ValidationError) as caught:
            self.read(max_bytes=1000)
        self.assertIn("huge.txt", caught.exception.message)
        self.assertNotIn("huge.png", caught.exception.message)  # it would not be fetched
        self.assertEqual(self.downloads(), [])

    def test_a_dry_run_fetches_nothing(self):
        self.gateway.add_attachment("PROJ-1", "a.txt", b"x", "text/plain")
        self.gateway.add_attachment("PROJ-1", "huge.txt", b"h" * 5000, "text/plain")
        self.gateway.add_attachment("PROJ-1", "b.png", PNG, "image/png")
        result = self.read(max_bytes=1000, dry_run=True)
        self.assertEqual([entry.action for entry in result.attachments], ["would-read", "too-large", "not-text"])
        self.assertTrue(result.dry_run)
        self.assertEqual(self.downloads(), [])

    def test_the_options_are_checked(self):
        self.gateway.add_attachment("PROJ-1", "a.txt", b"x", "text/plain")
        for options in ({"max_chars": 0}, {"max_bytes": 0}):
            with self.assertRaises(UsageError):
                self.read(**options)
        with self.assertRaises(UsageError):
            self.app.read_text.execute("PROJ-1")  # nothing chosen
        self.assertEqual(self.downloads(), [])


class AttachTest(Case):
    def setUp(self):
        super().setUp()
        self.files.write_bytes("shot.png", PNG)
        self.files.write_bytes("docs/trace.log", b"line\n" * 40)

    def test_a_dry_run_describes_and_checks_and_sends_nothing(self):
        result = self.app.attach.execute("PROJ-1", ["shot.png", "docs/trace.log"], dry_run=True)
        self.assertEqual([(item.name, item.size, item.mime_type) for item in result.files], [("shot.png", len(PNG), "image/png"), ("trace.log", 200, "text/plain")])
        self.assertEqual([call[0] for call in self.gateway.calls], ["list_attachments", "attachment_limits"])  # the issue exists, the limits are read
        self.assertTrue(result.dry_run and result.limits.enabled)
        self.assertEqual(result.limits.upload_limit, 10 * 1024 * 1024)
        self.assertEqual((result.attached, result.comment_id, result.warnings), ([], "", []))
        self.assertEqual(result.payload["parts"][0], {"field": "file", "filename": "shot.png", "contentType": "image/png", "size": len(PNG)})

    def test_the_real_run_uploads_the_bytes_and_reports_the_new_attachments(self):
        result = self.app.attach.execute("PROJ-1", ["shot.png", "docs/trace.log"])
        self.assertEqual([call[0] for call in self.gateway.calls], ["list_attachments", "attachment_limits", "upload_attachments"])
        sent = self.gateway.called("upload_attachments")[0][2]
        self.assertEqual([(item.name, item.mime_type, item.size, item.data) for item in sent], [("shot.png", "image/png", len(PNG), PNG), ("trace.log", "text/plain", 200, b"line\n" * 40)])
        self.assertEqual([(item.filename, item.size) for item in result.attached], [("shot.png", len(PNG)), ("trace.log", 200)])
        self.assertEqual([item.filename for item in self.gateway.issues["PROJ-1"].attachments], ["shot.png", "trace.log"])
        self.assertFalse(result.dry_run)

    def test_what_a_dry_run_shows_is_what_a_real_run_sends(self):
        dry = self.app.attach.execute("PROJ-1", ["shot.png"], comment="With a comment.", dry_run=True)
        real = self.app.attach.execute("PROJ-1", ["shot.png"], comment="With a comment.")
        self.assertEqual(dry.payload, real.payload)
        self.assertEqual(dry.payload["comment"], {"body": "With a comment."})
        self.assertEqual(self.gateway.called("add_comment"), [("add_comment", "PROJ-1", {"body": "With a comment."})])  # only the real run commented

    def test_a_name_that_exists_already_is_a_warning_and_nothing_stops_the_upload(self):
        old = self.gateway.add_attachment("PROJ-1", "SHOT.png", b"old", "image/png")
        result = self.app.attach.execute("PROJ-1", ["shot.png"], dry_run=True)
        self.assertEqual(result.files[0].existing, [old.id])
        self.assertIn(f"shot.png exists already on PROJ-1 (attachment {old.id})", result.warnings[0])
        self.assertIn("side by side", result.warnings[0])
        self.assertEqual(len(self.app.attach.execute("PROJ-1", ["shot.png"]).attached), 1)

    def test_a_name_given_twice_is_a_warning(self):
        self.files.write_bytes("other/shot.png", b"x")
        result = self.app.attach.execute("PROJ-1", ["shot.png", "other/shot.png"], dry_run=True)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("given more than once", result.warnings[0])

    def test_a_missing_empty_or_folder_path_is_an_error_naming_all_of_them_and_nothing_is_asked_of_jira(self):
        self.files.write_bytes("empty.txt", b"")
        with self.assertRaises(ValidationError) as caught:
            self.app.attach.execute("PROJ-1", ["shot.png", "missing.png", "empty.txt", "docs"])
        message = caught.exception.message
        for expected in ("missing.png does not exist", "empty.txt is empty", "docs is a folder"):
            self.assertIn(expected, message)
        self.assertNotIn("shot.png", message)
        self.assertEqual(self.gateway.calls, [])

    def test_a_file_over_the_limit_is_refused_before_any_upload_request_in_dry_run_and_real_run(self):
        self.gateway.attachment_upload_limit = 250  # shot.png has 264 bytes, trace.log 200
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(ValidationError) as caught:
                    self.app.attach.execute("PROJ-1", ["shot.png", "docs/trace.log"], dry_run=dry_run)
                self.assertIn("shot.png (264 B) is larger than the upload limit of 250 B", caught.exception.message)
                self.assertNotIn("trace.log", caught.exception.message)
                self.assertIn("Nothing was sent", caught.exception.hint)
        self.assertEqual(self.gateway.called("upload_attachments"), [])

    def test_a_limit_that_jira_does_not_state_is_no_limit(self):
        self.gateway.attachment_upload_limit = 0
        self.assertEqual(len(self.app.attach.execute("PROJ-1", ["shot.png"]).attached), 1)

    def test_switched_off_attachments_are_refused_before_the_upload(self):
        self.gateway.attachments_enabled = False
        with self.assertRaises(ValidationError) as caught:
            self.app.attach.execute("PROJ-1", ["shot.png"], dry_run=True)
        self.assertIn("switched off", caught.exception.message)
        self.assertEqual(self.gateway.called("upload_attachments"), [])

    def test_an_unknown_issue_is_not_found_and_a_bad_key_a_validation_error(self):
        with self.assertRaises(NotFoundError):
            self.app.attach.execute("PROJ-999", ["shot.png"], dry_run=True)
        with self.assertRaises(ValidationError):
            self.app.attach.execute("nokey", ["shot.png"])
        self.assertEqual(self.gateway.called("upload_attachments"), [])

    def test_no_file_and_an_empty_comment_are_errors_before_anything_happens(self):
        with self.assertRaises(UsageError):
            self.app.attach.execute("PROJ-1", [])
        with self.assertRaises(ValidationError):
            self.app.attach.execute("PROJ-1", ["shot.png"], comment="   ")
        self.assertEqual(self.gateway.calls, [])

    def test_the_comment_follows_the_upload(self):
        result = self.app.attach.execute("PROJ-1", ["shot.png"], comment="  Screenshot of the failure.  ")
        self.assertEqual([call[0] for call in self.gateway.calls][-2:], ["upload_attachments", "add_comment"])
        self.assertEqual(self.gateway.called("add_comment")[0][2], {"body": "Screenshot of the failure."})
        self.assertEqual(result.comment_id, "11")

    def test_when_the_upload_works_and_the_comment_fails_the_error_says_so_with_the_exit_code_of_the_comment(self):
        for error, exit_code in ((AuthError("Jira refused this request (HTTP 403).", hint="Ask an administrator.", details={"status": 403}), 5), (RejectedError("Jira refused the request (HTTP 400): bad"), 6)):
            with self.subTest(error=type(error).__name__):
                gateway = FakeGateway()
                gateway.errors["add_comment"] = error
                files = MemoryFiles()
                files.write_bytes("shot.png", PNG)
                with self.assertRaises(type(error)) as caught:
                    build_app(gateway=gateway, files=files).attachments.attach.execute("PROJ-1", ["shot.png"], comment="Note")
                raised = caught.exception
                self.assertEqual(raised.exit_code, exit_code)
                self.assertIn("The files were attached to PROJ-1", raised.message)
                self.assertIn("the comment was not added", raised.message)
                self.assertIn(error.message, raised.message)
                self.assertEqual(raised.details["attached"][0]["filename"], "shot.png")  # what was done is not lost
                self.assertEqual(len(gateway.issues["PROJ-1"].attachments), 1)
                self.assertTrue(raised.hint)

    def test_a_failing_upload_leaves_no_comment(self):
        self.gateway.errors["upload_attachments"] = AuthError("Jira refused this request (HTTP 403).", details={"status": 403})
        with self.assertRaises(AuthError):
            self.app.attach.execute("PROJ-1", ["shot.png"], comment="Note")
        self.assertEqual(self.gateway.called("add_comment"), [])

    def test_the_dry_run_shows_the_name_as_it_goes_on_the_wire(self):
        self.files.write_bytes('say "hi".png', PNG)
        dry = self.app.attach.execute("PROJ-1", ['say "hi".png'], dry_run=True)
        self.assertEqual(dry.files[0].name, 'say "hi".png')  # the name on the disk
        self.assertEqual(dry.payload["parts"][0]["filename"], "say %22hi%22.png")  # the name that Jira will get


CONFIG = "/home/me/.config/jira-dc-cli/config"
TOKEN = DEFAULT_SETTINGS.pat


class SafetyCase(Case):
    """The use cases with files that know their places and can create a name only when it is free, and a configuration file with a token."""

    def setUp(self):
        self.gateway = FakeGateway()
        self.files = ExclusiveFiles()
        self.diagnostics = RecordingDiagnostics()
        self.app = self.build()
        self.notes = self.gateway.add_attachment("PROJ-1", "notes.txt", b"hello", "text/plain")
        self.shot = self.gateway.add_attachment("PROJ-1", "shot.png", PNG, "image/png")
        self.files.put(CONFIG, f"JIRA_BASE_URL=https://jira.example.com\nJIRA_PAT={TOKEN}\n".encode())


class DownloadSafetyTest(SafetyCase):
    def download(self, **options):
        return self.app.download.execute("PROJ-1", **{"everything": True, "out": "out", **options})

    def test_a_file_is_created_only_when_nothing_is_there_and_replaced_only_with_force(self):
        self.download()
        self.assertEqual((self.files.created, self.files.replaced), (["out/notes.txt", "out/shot.png"], []))
        self.download(force=True)
        self.assertEqual((self.files.created, self.files.replaced), (["out/notes.txt", "out/shot.png"], ["out/notes.txt", "out/shot.png"]))

    def test_a_file_that_appears_while_the_download_runs_is_never_overwritten(self):
        self.files.arrivals["out/notes.txt"] = b"PRECIOUS LOCAL DATA"
        with self.assertRaises(ValidationError) as caught:
            self.download()
        self.assertIn("notes.txt appeared in out while the download ran", caught.exception.message)
        self.assertIn("--force", caught.exception.hint)
        self.assertEqual(self.files.read_bytes("out/notes.txt"), b"PRECIOUS LOCAL DATA")
        self.assertEqual(self.files.replaced, [])
        self.assertNotIn("written", caught.exception.details)  # it was the first file: nothing else was written

    def test_a_file_that_appears_after_others_were_written_names_those_in_the_error(self):
        self.files.arrivals["out/shot.png"] = b"PRECIOUS"
        with self.assertRaises(ValidationError) as caught:
            self.download()
        self.assertEqual([item["filename"] for item in caught.exception.details["written"]], ["notes.txt"])
        self.assertEqual(self.files.read_bytes("out/shot.png"), b"PRECIOUS")

    def test_a_failure_after_files_were_written_names_them_and_says_what_a_new_run_needs(self):
        self.gateway.add_attachment("PROJ-1", "third.txt", b"three", "text/plain")
        original = self.gateway.download_attachment
        seen = []

        def flaky(attachment, max_bytes):
            seen.append(attachment.filename)
            if len(seen) == 2:
                raise UnavailableError("Jira answered with a server error (HTTP 500): boom.", hint="Try again in a moment.", details={"status": 500})
            return original(attachment, max_bytes)

        self.gateway.download_attachment = flaky
        with self.assertRaises(UnavailableError) as caught:
            self.download()
        error = caught.exception
        self.assertEqual(error.exit_code, 7)
        self.assertIn("HTTP 500", error.message)
        self.assertIn("1 of the 3 files were written to out before that: notes.txt", error.message)
        self.assertIn("Try again in a moment.", error.hint)
        self.assertIn("--force", error.hint)  # a new run meets the file that is there
        self.assertEqual(error.details["status"], 500)  # what the error carried is kept
        self.assertEqual([(item["filename"], item["path"], item["size"]) for item in error.details["written"]], [("notes.txt", "out/notes.txt", 5)])
        self.assertEqual(error.details["failed"], {"id": self.shot.id, "filename": "shot.png"})
        self.assertTrue(self.files.exists("out/notes.txt") and not self.files.exists("out/shot.png"))
        self.gateway.download_attachment = original
        with self.assertRaises(ValidationError) as again:
            self.download()
        self.assertIn("notes.txt already exists", again.exception.message)  # the retry is as the hint says
        self.assertEqual([item.action for item in self.download(force=True).files], ["overwritten", "written", "written"])

    def test_a_failure_of_the_first_file_is_reported_as_it_is(self):
        self.gateway.errors["download_attachment"] = UnavailableError("boom", hint="later")
        with self.assertRaises(UnavailableError) as caught:
            self.download()
        self.assertEqual((caught.exception.message, caught.exception.hint, caught.exception.details), ("boom", "later", {}))

    def test_a_folder_where_a_file_goes_is_an_error_in_every_mode(self):
        self.files.put("out/notes.txt/inner", b"x")
        for options in ({}, {"force": True}, {"dry_run": True}, {"dry_run": True, "force": True}):
            with self.subTest(options=options):
                with self.assertRaises(ValidationError) as caught:
                    self.download(**options)
                self.assertIn("notes.txt is a folder in out", caught.exception.message)
                self.assertIn("--force does not replace a folder", caught.exception.hint)
        self.assertEqual((self.downloads(), self.files.created, self.files.replaced), ([], [], []))
        self.files.put("out/shot.png/inner", b"x")
        with self.assertRaises(ValidationError) as caught:
            self.download(dry_run=True)
        self.assertIn("2 of the names (notes.txt, shot.png) are folders", caught.exception.message)

    def test_nothing_is_written_into_the_folder_of_the_configuration_with_force_or_without(self):
        folder = "/home/me/.config/jira-dc-cli"
        spellings = {
            "the folder": folder, "with a slash": folder + "/", "with dots": folder + "/./", "up and down": "/work/../home/me/.config/../.config/jira-dc-cli",
        }
        for label, out in spellings.items():
            for options in ({}, {"force": True}, {"dry_run": True}):
                with self.subTest(label, **options):
                    with self.assertRaises(UsageError) as caught:
                        self.download(out=out, protected=(CONFIG,), **options)
                    self.assertEqual(caught.exception.exit_code, 2)
                    self.assertIn("--out", caught.exception.hint)
                    self.assertIn("--force too", caught.exception.hint)
        self.assertEqual((self.downloads(), self.files.created, self.files.replaced), ([], [], []))
        self.assertIn(TOKEN.encode(), self.files.read_bytes(CONFIG))  # the configuration is as it was

    def test_a_link_to_the_folder_and_another_case_of_its_name_are_the_same_place(self):
        files = ExclusiveFiles(links={"shortcut": "/home/me/.config/jira-dc-cli"}, ignore_case=True)
        files.put(CONFIG, b"x")
        app = build_app(gateway=self.gateway, files=files, diagnostics=self.diagnostics).attachments
        for out in ("shortcut", "/HOME/me/.Config/JIRA-dc-cli"):
            with self.subTest(out=out):
                with self.assertRaises(UsageError):
                    app.download.execute("PROJ-1", everything=True, out=out, protected=(CONFIG,), force=True)
        self.assertEqual((self.downloads(), files.created, files.replaced), ([], [], []))

    def test_a_folder_below_the_configuration_and_other_folders_are_no_risk(self):
        for out in ("/home/me/.config/jira-dc-cli/cache", "/home/me/.config", "elsewhere"):
            with self.subTest(out=out):
                self.assertEqual(len(self.download(out=out, protected=(CONFIG,)).files), 2)
        self.assertIn(TOKEN.encode(), self.files.read_bytes(CONFIG))

    def test_an_attachment_named_like_the_configuration_goes_elsewhere_without_a_problem(self):
        self.gateway.add_attachment("PROJ-1", "config", b"JIRA_BASE_URL=https://evil.example/jira\n", "text/plain")
        result = self.download(out="elsewhere", protected=(CONFIG,))
        self.assertIn("elsewhere/config", [item.path for item in result.files])
        self.assertIn(TOKEN.encode(), self.files.read_bytes(CONFIG))

    def test_a_use_case_protects_what_it_is_told_to_protect(self):
        self.assertEqual(len(self.download(out="/home/me/.config/jira-dc-cli").files), 2)  # the command line always names the configuration


class AttachSafetyTest(SafetyCase):
    def setUp(self):
        super().setUp()
        self.files.put("shot.png", PNG)
        self.files.put("backup/config.bak", f"old copy\nJIRA_PAT={TOKEN}\n".encode())
        self.files.put("utf16.txt", f"token: {TOKEN}".encode("utf-16"))
        self.files.put("utf16be.txt", f"token: {TOKEN}".encode("utf-16-be"))
        self.files.put("log.txt", f"header Authorization: Bearer {TOKEN}\n".encode())

    def attach(self, paths, **options):
        return self.app.attach.execute("PROJ-1", paths, **options)

    def test_the_configuration_file_is_never_attached_whatever_its_spelling(self):
        for path in (CONFIG, "/work/../home/me/.config/jira-dc-cli/./config"):
            for dry_run in (False, True):
                with self.subTest(path=path, dry_run=dry_run):
                    with self.assertRaises(UsageError) as caught:
                        self.attach([path], protected=(CONFIG,), dry_run=dry_run)
                    self.assertEqual(caught.exception.exit_code, 2)
                    self.assertIn("is the configuration file of this CLI", caught.exception.message)
                    self.assertNotIn(TOKEN, caught.exception.message + caught.exception.hint)
        self.assertEqual(self.gateway.calls, [])  # nothing was asked of Jira

    def test_a_link_to_the_configuration_is_caught_by_its_place_and_without_the_place_by_its_bytes(self):
        files = ExclusiveFiles(links={"mine.txt": CONFIG})
        files.put(CONFIG, f"JIRA_PAT={TOKEN}\n".encode())
        app = build_app(gateway=self.gateway, files=files).attachments
        with self.assertRaises(UsageError) as caught:
            app.attach.execute("PROJ-1", ["mine.txt"], protected=(CONFIG,))
        self.assertIn("configuration file", caught.exception.message)
        with self.assertRaises(UsageError) as caught:
            app.attach.execute("PROJ-1", ["mine.txt"])
        self.assertIn("holds the access token", caught.exception.message)

    def test_a_file_that_holds_the_token_is_never_attached_in_any_form_a_file_can_have_it(self):
        for path in ("backup/config.bak", "utf16.txt", "utf16be.txt", "log.txt"):
            for dry_run in (False, True):
                with self.subTest(path=path, dry_run=dry_run):
                    with self.assertRaises(UsageError) as caught:
                        self.attach(["shot.png", path], dry_run=dry_run)
                    error = caught.exception
                    self.assertEqual(error.exit_code, 2)
                    self.assertIn(f"{path} holds the access token", error.message)
                    self.assertNotIn(TOKEN, error.message + error.hint + repr(error.details))  # the token is never shown
                    self.assertIn("Nothing was sent", error.hint)
        self.assertEqual(self.gateway.called("upload_attachments"), [])

    def test_a_file_without_the_token_is_attached_as_before_and_no_token_is_no_search(self):
        self.assertEqual(len(self.attach(["shot.png"], protected=(CONFIG,)).attached), 1)
        no_token = build_app(gateway=self.gateway, files=self.files, settings=settings(pat="")).attachments
        self.assertEqual(len(no_token.attach.execute("PROJ-1", ["backup/config.bak"]).attached), 1)  # there is no token that could be in it

    def test_the_configuration_that_does_not_exist_yet_protects_nothing_and_costs_nothing(self):
        self.assertEqual(len(self.attach(["shot.png"], protected=("/nowhere/config",)).attached), 1)

    def test_a_name_that_is_no_text_is_refused_at_plan_time_in_every_mode(self):
        self.files.put("odd/a\udcffb.txt", b"x")
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(ValidationError) as caught:
                    self.attach(["odd/a\udcffb.txt"], dry_run=dry_run)
                self.assertIn("has a name that is no text", caught.exception.message)
                self.assertNotIn("\udcff", caught.exception.message)  # the damaged character is not printed
                self.assertIn("renamed", caught.exception.hint)
        self.assertEqual(self.gateway.calls, [])

    def test_what_a_message_names_cannot_move_the_cursor(self):
        with self.assertRaises(ValidationError) as caught:
            self.attach(["a\x1b[2Jb.png", "c\u202ed.png"])
        self.assertNotIn("\x1b", caught.exception.message)
        self.assertNotIn("\u202e", caught.exception.message)
        self.assertIn("does not exist", caught.exception.message)

    def test_a_dry_run_of_a_thousand_files_is_a_few_checks_per_file(self):
        names = [f"many/log{index:04d}.txt" for index in range(1000)]
        for name in names:
            self.files.put(name, b"x")
        started = time.monotonic()
        result = self.attach(names, dry_run=True, protected=(CONFIG,))
        self.assertEqual(len(result.files), 1000)
        self.assertLess(time.monotonic() - started, 5)


class ReaderFailureTest(Case):
    def test_a_reader_that_breaks_on_one_file_does_not_stop_the_others_and_never_echoes_the_file(self):
        self.gateway.add_attachment("PROJ-1", "weird.docx", docx(para("x")), "application/octet-stream")
        self.gateway.add_attachment("PROJ-1", "after.txt", b"still read", "text/plain")
        with mock.patch("jira_dc_cli.application.attachments.office_text", side_effect=RuntimeError("secret words from the file")):
            entries = self.app.read_text.execute("PROJ-1", everything=True).attachments
        self.assertEqual([(entry.filename, entry.action) for entry in entries], [("weird.docx", "not-text"), ("after.txt", "read")])
        self.assertIn("RuntimeError", entries[0].hint)
        self.assertIn("jira download PROJ-1", entries[0].hint)
        self.assertNotIn("secret", entries[0].hint + " ".join(self.diagnostics.debugs))
        self.assertTrue(any("RuntimeError" in line for line in self.diagnostics.debugs))  # the class is logged, never the content
        self.assertEqual(entries[1].text, "still read")

    def test_a_string_that_many_cells_point_to_is_one_hint_and_the_other_files_are_still_read(self):
        bomb = strings_workbook("A" * 1_000_000, 500)
        self.gateway.add_attachment("PROJ-1", "bomb.xlsx", bomb, "application/octet-stream")
        self.gateway.add_attachment("PROJ-1", "after.txt", b"still read", "text/plain")
        started = time.monotonic()
        entries = self.app.read_text.execute("PROJ-1", everything=True).attachments
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual([(entry.filename, entry.action) for entry in entries], [("bomb.xlsx", "not-text"), ("after.txt", "read")])
        self.assertIn("expands to more text than can be read", entries[0].hint)
        self.assertIn("jira download PROJ-1", entries[0].hint)
        self.assertEqual(entries[1].text, "still read")


if __name__ == "__main__":
    unittest.main()
