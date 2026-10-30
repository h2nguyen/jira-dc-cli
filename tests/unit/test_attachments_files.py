"""Creating a file only when nothing is there, and one name for one place, on the real file system."""
import errno
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from jira_dc_cli.adapters.driven.atomic import write_bytes_atomically, write_bytes_exclusively
from jira_dc_cli.adapters.driven.system import LocalFiles
from jira_dc_cli.application.ports_attachments import CreatesExclusively, ResolvesPaths
from jira_dc_cli.domain.errors import ValidationError


def take_away_link(link: Path) -> None:
    """Remove the link, and never what it leads to: `rmdir` takes a junction or a link to a folder on Windows and fails on a symbolic link
    on Linux and macOS (`NotADirectoryError`), where `unlink` takes it."""
    for remove in (os.rmdir, os.unlink):
        try:
            return remove(link)
        except OSError:
            continue


def make_folder_link(test: unittest.TestCase, link: Path, target: Path) -> None:
    """`link` as another way to reach the folder `target`: a symbolic link, or on Windows, where that needs a privilege, a junction. The
    link (and not what it leads to) is taken away again when the test ends. The test is skipped on a system that lets neither be made."""
    made = False
    try:
        os.symlink(target, link, target_is_directory=True)
        made = True
    except (OSError, NotImplementedError, AttributeError):
        if os.name == "nt":
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0
    if not made:
        test.skipTest("this system does not let the test make a link to a folder")
    test.addCleanup(take_away_link, link)


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def names(self, folder=None):
        return sorted(path.name for path in (folder or self.root).iterdir())


class ExclusiveWriteTest(Case):
    def test_the_file_is_created_whole_and_missing_folders_too(self):
        target = self.root / "a" / "b" / "file.bin"
        write_bytes_exclusively(target, bytes(range(256)) * 100)
        self.assertEqual(target.read_bytes(), bytes(range(256)) * 100)
        self.assertEqual(self.names(target.parent), ["file.bin"])  # no temporary file is left

    def test_something_that_is_there_is_left_exactly_as_it_is(self):
        target = self.root / "notes.txt"
        target.write_bytes(b"mine")
        before = target.stat().st_mtime_ns
        with self.assertRaises(FileExistsError):
            write_bytes_exclusively(target, b"theirs")
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), (b"mine", before))
        self.assertEqual(self.names(), ["notes.txt"])

    def test_a_folder_at_the_path_counts_as_something_there(self):
        (self.root / "taken").mkdir()
        with self.assertRaises(FileExistsError):
            write_bytes_exclusively(self.root / "taken", b"data")
        self.assertEqual(self.names(), ["taken"])

    def test_a_file_where_a_folder_should_be_is_no_file_that_exists(self):
        (self.root / "afile").write_bytes(b"x")
        for target in (self.root / "afile" / "x.txt", self.root / "afile" / "sub" / "x.txt"):
            with self.subTest(target=target.name):
                with self.assertRaises(OSError) as caught:
                    write_bytes_exclusively(target, b"data")
                self.assertNotIsInstance(caught.exception, FileExistsError)  # the target did not exist: the folder is the problem
        self.assertEqual(self.names(), ["afile"])

    def test_of_many_writers_that_race_for_one_name_exactly_one_wins_with_its_whole_content(self):
        for round_number in range(15):
            target = self.root / f"race{round_number}.bin"
            writers = 12
            barrier = threading.Barrier(writers)
            results: list[object] = []

            def write(index):
                barrier.wait()
                try:
                    write_bytes_exclusively(target, bytes([index]) * 50_000)
                    results.append(index)
                except FileExistsError:
                    results.append("late")

            threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            winners = [item for item in results if item != "late"]
            self.assertEqual((len(winners), results.count("late")), (1, writers - 1))
            data = target.read_bytes()
            self.assertEqual(data, bytes([winners[0]]) * 50_000)  # the winner's file, whole: never a mixture
        self.assertEqual(len(self.names()), 15)  # and no temporary file of any of them

    def test_without_hard_links_the_file_is_still_created_only_when_nothing_is_there(self):
        target = self.root / "plain.txt"
        with mock.patch("os.link", side_effect=OSError(errno.EPERM, "this file system has no links")):
            write_bytes_exclusively(target, b"first")
            self.assertEqual(target.read_bytes(), b"first")
            with self.assertRaises(FileExistsError):
                write_bytes_exclusively(target, b"second")
        self.assertEqual((target.read_bytes(), self.names()), (b"first", ["plain.txt"]))

    def test_a_write_in_place_that_fails_removes_what_it_created(self):
        target = self.root / "full.txt"
        with mock.patch("os.link", side_effect=OSError(errno.EPERM, "no links")), mock.patch("os.fsync", side_effect=[None, OSError(errno.ENOSPC, "disk full")]):
            with self.assertRaises(OSError) as caught:
                write_bytes_exclusively(target, b"data")
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertEqual(self.names(), [])  # neither a half written target nor a temporary file

    def test_a_write_that_fails_before_the_link_leaves_nothing_behind(self):
        with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "io error")):
            with self.assertRaises(OSError):
                write_bytes_exclusively(self.root / "x.txt", b"data")
        self.assertEqual(self.names(), [])

    def test_a_link_that_leads_nowhere_counts_as_something_there(self):
        try:
            os.symlink(self.root / "missing", self.root / "target")
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("this system does not let the test make a symbolic link")
        with self.assertRaises(FileExistsError):
            write_bytes_exclusively(self.root / "target", b"data")
        self.assertFalse((self.root / "missing").exists())  # it did not write through the link

    def test_the_file_has_the_permissions_of_a_file_that_is_written_the_ordinary_way(self):
        ordinary, exclusive = self.root / "ordinary.txt", self.root / "exclusive.txt"
        write_bytes_atomically(ordinary, b"x")
        write_bytes_exclusively(exclusive, b"x")
        self.assertEqual(ordinary.stat().st_mode & 0o777, exclusive.stat().st_mode & 0o777)

    def test_the_atomic_write_that_replaces_is_as_it_was(self):
        target = self.root / "notes.txt"
        target.write_bytes(b"old")
        write_bytes_atomically(target, b"new")
        self.assertEqual((target.read_bytes(), self.names()), (b"new", ["notes.txt"]))


class LocalFilesTest(Case):
    def test_the_real_files_have_both_abilities(self):
        files = LocalFiles()
        self.assertIsInstance(files, CreatesExclusively)
        self.assertIsInstance(files, ResolvesPaths)

    def test_create_bytes_creates_and_never_replaces_and_says_so_with_the_error_that_the_use_case_looks_for(self):
        files = LocalFiles()
        target = str(self.root / "out" / "a.txt")
        files.create_bytes(target, b"one")
        with self.assertRaises(FileExistsError):
            files.create_bytes(target, b"two")  # not turned into a message: the caller knows what it means
        self.assertEqual(Path(target).read_bytes(), b"one")
        files.write_bytes(target, b"three")  # the other call replaces, as it always did
        self.assertEqual(Path(target).read_bytes(), b"three")

    def test_any_other_failure_is_a_message_with_the_path_and_the_reason(self):
        files = LocalFiles()
        (self.root / "afile").write_bytes(b"x")
        with self.assertRaises(ValidationError) as caught:
            files.create_bytes(str(self.root / "afile" / "sub" / "a.txt"), b"x")
        self.assertIn("Cannot write", caught.exception.message)
        self.assertIn("a.txt", caught.exception.message)

    def test_two_spellings_of_one_place_resolve_to_one_name(self):
        files = LocalFiles()
        (self.root / "sub").mkdir()
        direct = files.resolve(str(self.root / "sub" / "file.txt"))
        self.assertEqual(files.resolve(str(self.root / "sub" / ".." / "sub" / "file.txt")), direct)
        self.assertEqual(files.resolve(str(self.root / "sub" / "." / "file.txt")), direct)
        # a relative path needs the drive of the folder: a Windows runner keeps the repository on D: and the temp folder on C:
        original = os.getcwd()
        os.chdir(self.root.parent)
        self.addCleanup(os.chdir, original)
        self.assertEqual(files.resolve(os.path.relpath(self.root / "sub" / "file.txt")), direct)
        self.assertNotEqual(files.resolve(str(self.root / "other" / "file.txt")), direct)
        self.assertTrue(os.path.isabs(files.resolve("relative.txt")))

    @unittest.skipUnless(os.name == "nt", "other systems tell A from a")
    def test_on_windows_the_case_of_a_name_makes_no_other_place(self):
        files = LocalFiles()
        self.assertEqual(files.resolve(str(self.root / "Sub" / "FILE.txt")), files.resolve(str(self.root / "sub" / "file.TXT")))

    def test_a_link_to_a_folder_resolves_to_the_folder(self):
        (self.root / "real").mkdir()
        (self.root / "real" / "file.txt").write_bytes(b"x")
        make_folder_link(self, self.root / "link", self.root / "real")
        files = LocalFiles()
        self.assertEqual(files.resolve(str(self.root / "link" / "file.txt")), files.resolve(str(self.root / "real" / "file.txt")))
        self.assertEqual(files.resolve(str(self.root / "link" / "new.txt")), files.resolve(str(self.root / "real" / "new.txt")))  # also for a file that is not there yet

    def test_a_name_that_cannot_be_resolved_stays_what_it_is(self):
        files = LocalFiles()
        self.assertIsInstance(files.resolve("a\x00b"), str)
        self.assertIsInstance(files.resolve(str(self.root / "does" / "not" / "exist")), str)


class TakeAwayLinkTest(Case):
    """The cleanup of a test that made a link: it removes the link, on every system, and never what the link leads to."""

    def test_a_symbolic_link_is_taken_away_with_unlink_where_rmdir_refuses_it(self):
        link = self.root / "link"
        refusal = NotADirectoryError(errno.ENOTDIR, "Not a directory")  # what `os.rmdir` says to a symbolic link on Linux and macOS
        with mock.patch("os.rmdir", side_effect=refusal) as rmdir, mock.patch("os.unlink") as unlink:
            take_away_link(link)
        rmdir.assert_called_once_with(link)
        unlink.assert_called_once_with(link)

    def test_a_junction_or_a_link_to_a_folder_on_windows_is_taken_away_with_rmdir_alone(self):
        link = self.root / "link"
        with mock.patch("os.rmdir") as rmdir, mock.patch("os.unlink") as unlink:
            take_away_link(link)
        rmdir.assert_called_once_with(link)
        unlink.assert_not_called()

    def test_a_link_that_is_gone_already_is_no_error(self):
        with mock.patch("os.rmdir", side_effect=FileNotFoundError), mock.patch("os.unlink", side_effect=FileNotFoundError):
            take_away_link(self.root / "gone")

    def test_the_cleanup_of_make_folder_link_takes_the_link_away_and_leaves_the_folder_it_led_to(self):
        (self.root / "real").mkdir()
        (self.root / "real" / "file.txt").write_bytes(b"x")
        link = self.root / "link"
        probe = unittest.TestCase()  # a test of its own, so that its cleanups can be run here
        try:
            make_folder_link(probe, link, self.root / "real")
        except unittest.SkipTest:
            self.skipTest("this system does not let the test make a link to a folder")
        self.assertTrue(os.path.lexists(link))
        probe.doCleanups()
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((self.root / "real" / "file.txt").read_bytes(), b"x")


if __name__ == "__main__":
    unittest.main()
