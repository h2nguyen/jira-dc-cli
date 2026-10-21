"""install.py: install, update, uninstall, and the launchers it writes."""
import argparse
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
WINDOWS_ONLY = unittest.skipUnless(os.name == "nt", "the home drive is a Windows setting")


def load_installer():
    spec = importlib.util.spec_from_file_location("jira_installer", ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallerCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.app = self.root / "app"
        self.bin = self.root / "bin"
        self.env = {key: value for key, value in os.environ.items() if not key.startswith(("JIRA_", "PYTHON")) and key not in ("HOMEDRIVE", "HOMEPATH")}
        # Every place the program takes for a home or local folder points into the temporary folder: --purge and the
        # defaults must never reach the real ones.
        self.env.update(
            HOME=str(self.root),
            USERPROFILE=str(self.root / "profile"),
            LOCALAPPDATA=str(self.root / "local"),
            XDG_CONFIG_HOME=str(self.root / "xdg"),
            XDG_CACHE_HOME=str(self.root / "cache"),
        )

    def install(self, *extra, env=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "install.py"), "--prefix", str(self.app), "--bin-dir", str(self.bin), *extra],
            capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=120,
        )

    def uninstall(self, *extra):
        return self.install("--uninstall", *extra)

    def launcher(self):
        return self.bin / ("jira.cmd" if os.name == "nt" else "jira")

    def run_launcher(self, *argv, env=None, cwd=None):
        return subprocess.run([str(self.launcher()), *argv], capture_output=True, text=True, env={**self.env, **(env or {})}, cwd=cwd or self.root, timeout=60)


class DefaultPlacesTest(InstallerCase):
    def test_the_defaults_keep_the_program_on_a_local_disk_and_the_launchers_where_the_path_is(self):
        local = self.root / "local"
        env = {"LOCALAPPDATA": str(local)}
        done = subprocess.run([sys.executable, str(ROOT / "install.py"), "--dry-run"], capture_output=True, text=True, env={**self.env, **env}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        expected_program = local / "jira-dc-cli" if os.name == "nt" else self.root / ".local" / "share" / "jira-dc-cli"
        self.assertIn(f"Install to:  {expected_program}", done.stdout)
        self.assertIn(str(self.root / "bin" / "jira"), done.stdout)  # HOME is the folder of the launchers (H:\\bin in Git Bash)

    def test_the_environment_variables_choose_the_places(self):
        program, launchers = self.root / "elsewhere" / "program", self.root / "elsewhere" / "launchers"
        env = {"JIRA_CLI_INSTALL_DIR": str(program), "JIRA_CLI_BIN_DIR": str(launchers)}
        done = subprocess.run([sys.executable, str(ROOT / "install.py"), "--dry-run"], capture_output=True, text=True, env={**self.env, **env}, timeout=60)
        self.assertIn(f"Install to:  {program}", done.stdout)
        self.assertIn(str(launchers / "jira"), done.stdout)

    def test_removing_another_installation_leaves_the_launchers_of_this_one(self):
        self.install()
        other = self.root / "other-installation"
        other_bin = self.root / "other-bin"
        done = subprocess.run([sys.executable, str(ROOT / "install.py"), "--prefix", str(other), "--bin-dir", str(other_bin)], capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        done = subprocess.run([sys.executable, str(ROOT / "install.py"), "--uninstall", "--prefix", str(other), "--bin-dir", str(self.bin)], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(other.exists())
        self.assertTrue((self.app / "jira.py").is_file())
        self.assertEqual(self.run_launcher("version").stdout, f"jira-dc-cli {VERSION}\n")  # the launchers still start the first program

    def test_a_mapped_network_drive_is_kept_as_written(self):
        """`Path.resolve()` turns H:\\ into \\\\server\\share\\...; the launchers must keep the drive letter."""
        installer = load_installer()
        self.assertEqual(os.fspath(installer.absolute(r"H:\bin")), os.path.abspath(r"H:\bin"))


class HomeFoldersTest(InstallerCase):
    """PowerShell and cmd have no HOME: the installer and the program must choose the folders Git Bash chooses."""

    def home_drive(self):
        """The home drive and path of a managed Windows laptop (`H:` and `\\...`)."""
        folder = self.root / "mapped"
        folder.mkdir()
        drive, rest = os.path.splitdrive(str(folder))
        return folder, {"HOMEDRIVE": drive, "HOMEPATH": rest}

    def environment(self, *, home=False, **extra):
        """The isolated environment of the tests, without HOME unless asked for: that is PowerShell and cmd."""
        environment = {**self.env, **extra}
        if not home:
            if os.name == "nt":
                environment.pop("HOME")
            else:
                environment["HOME"] = environment["USERPROFILE"]  # there is no profile variable: Path.home() reads HOME
        return environment

    def run_installer(self, *args, **extra):
        return subprocess.run([sys.executable, str(ROOT / "install.py"), *args], capture_output=True, text=True, env=self.environment(**extra), timeout=120)

    def purge(self, **extra):
        return self.run_installer("--uninstall", "--purge", "--prefix", str(self.app), "--bin-dir", str(self.bin), **extra)

    @WINDOWS_ONLY
    def test_without_home_the_launchers_go_to_the_home_drive_like_in_git_bash(self):
        folder, env = self.home_drive()
        done = self.run_installer("--dry-run", **env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(str(folder / "bin" / "jira"), done.stdout)

    def test_the_installer_and_the_program_agree_on_the_home_folders(self):
        from jira_dc_cli.adapters.driven.paths import home_directories

        installer = load_installer()
        vectors = {
            "nothing but the profile": self.environment(),
            "HOME set": self.environment(home=True),
            "HOME somewhere else": {**self.environment(home=True), "HOME": str(self.root / "elsewhere")},
            "a home drive that is not a drive letter": self.environment(HOMEDRIVE=str(self.root), HOMEPATH=os.sep + "tmp"),
        }
        if os.name == "nt":
            folder, env = self.home_drive()
            drive, rest = env["HOMEDRIVE"], env["HOMEPATH"]
            vectors.update(
                {
                    "PowerShell or cmd: no HOME, a home drive": self.environment(**env),
                    "Git Bash: HOME and a home drive": self.environment(home=True, **env),
                    "a home drive that is not there": self.environment(HOMEDRIVE=drive, HOMEPATH=rest + "-nowhere"),
                    "a malformed home path": self.environment(HOMEDRIVE=drive, HOMEPATH=folder.name),
                }
            )
        for name, environment in vectors.items():
            with self.subTest(name), mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(installer.home_directories(), home_directories(environment))

    @WINDOWS_ONLY
    def test_purge_removes_the_configuration_and_cache_of_every_place_the_program_looks(self):
        self.install()
        folder, env = self.home_drive()
        profile = self.root / "profile"
        files = [
            folder / ".config" / "jira-dc-cli" / "config",
            profile / ".config" / "jira-dc-cli" / "config",
            folder / ".cache" / "jira-dc-cli" / "fields-PROJ.json",
            profile / ".cache" / "jira-dc-cli" / "issue-types-PROJ.json",
        ]
        for path in files:
            path.parent.mkdir(parents=True)
            path.write_text("x")
        done = self.purge(**env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([path for path in (*files, *(path.parent for path in files)) if path.exists()], [])

    def test_purge_deletes_the_files_of_the_program_and_never_a_folder_that_holds_something_else(self):
        config, cache = self.root / "xdg" / "jira-dc-cli", self.root / "cache" / "jira-dc-cli"
        for directory, name in ((config, "config"), (cache, "fields-PROJ.json")):
            directory.mkdir(parents=True)
            (directory / name).write_text("x")
            (directory / "notes.txt").write_text("keep")
        done = self.purge()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((config / "config").exists() or (cache / "fields-PROJ.json").exists())
        self.assertEqual([(config / "notes.txt").read_text(), (cache / "notes.txt").read_text()], ["keep", "keep"])

    def test_the_folder_of_a_config_file_kept_elsewhere_is_never_deleted(self):
        """`JIRA_CONFIG_FILE` may point into any folder, also one named like the program, such as a source checkout."""
        checkout = self.root / "jira-dc-cli"
        (checkout / "src").mkdir(parents=True)
        (checkout / "src" / "kept.py").write_text("keep")
        (checkout / "config").write_text("JIRA_PAT=x\n")
        alone = self.root / "alone" / "jira-dc-cli"
        alone.mkdir(parents=True)
        (alone / "config").write_text("JIRA_PAT=x\n")
        for config in (checkout / "config", alone / "config"):
            done = self.install("--uninstall", "--purge", env={"JIRA_CONFIG_FILE": str(config)})
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse(config.exists())
        self.assertEqual((checkout / "src" / "kept.py").read_text(), "keep")
        self.assertTrue(alone.is_dir())  # empty now, but it was not made by the program: it is left

    def test_a_folder_gets_rmdir_not_a_recursive_delete_so_a_late_arrival_survives(self):
        installer = load_installer()
        config = self.root / "xdg" / "jira-dc-cli" / "config"
        config.parent.mkdir(parents=True)
        config.write_text("x")
        remove_file = installer.remove_file

        def remove_and_let_something_arrive(path):
            remove_file(path)
            (path.parent / "late.txt").write_text("arrived after the plan was made")

        arguments = argparse.Namespace(prefix=str(self.app), bin_dir=str(self.bin), purge=True, dry_run=False)
        with mock.patch.dict(os.environ, self.environment(home=True), clear=True), mock.patch.object(installer, "remove_file", remove_and_let_something_arrive), redirect_stdout(io.StringIO()):
            self.assertEqual(installer.uninstall(arguments), 0)
        self.assertFalse(config.exists())
        self.assertEqual((config.parent / "late.txt").read_text(), "arrived after the plan was made")

    def test_a_configuration_inside_the_program_folder_goes_with_it_without_an_error(self):
        self.install()
        (self.app / "config").write_text("JIRA_PAT=x\n")
        done = self.install("--uninstall", "--purge", env={"JIRA_CONFIG_FILE": str(self.app / "config")})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.app.exists())


class InstallTest(InstallerCase):
    def test_dry_run_changes_nothing(self):
        done = self.install("--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Dry run: nothing was changed.", done.stdout)
        self.assertFalse(self.app.exists() or self.bin.exists())

    def test_install_lays_out_the_program_and_the_launchers(self):
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"Installed jira-dc-cli {VERSION}.", done.stdout)
        for relative in ("jira.py", "VERSION", "LICENSE", "CHANGELOG.md", "README.md", "install.py", "jira_dc_cli/bootstrap.py", "jira_dc_cli/domain/wiki.py", "examples/issues.csv"):
            self.assertTrue((self.app / relative).is_file(), relative)
        for leftover in ("tests", "scripts", "src"):
            self.assertFalse((self.app / leftover).exists(), leftover)
        self.assertEqual([entry.name for entry in self.root.iterdir() if entry.name.startswith("app.")], [])

    def test_the_launchers_start_the_installed_python_program(self):
        self.install()
        sh = (self.bin / "jira").read_bytes()
        self.assertTrue(sh.startswith(b"#!/bin/sh\n") and b"\r" not in sh)
        self.assertIn(sys.executable.replace("\\", "/").encode(), sh)
        if os.name == "nt":
            cmd = (self.bin / "jira.cmd").read_bytes()
            self.assertIn(b"\r\n", cmd)
            self.assertIn(b"-I -X utf8", cmd)
            self.assertIn(str(self.app / "jira.py").encode(), cmd)
        done = self.run_launcher("version")
        self.assertEqual((done.returncode, done.stdout), (0, f"jira-dc-cli {VERSION}\n"), done.stderr)

    @unittest.skipUnless(os.name != "nt" or GIT_BASH.exists(), "needs a POSIX shell")
    def test_the_sh_launcher_works_in_a_posix_shell(self):
        self.install()
        shell = [str(GIT_BASH)] if os.name == "nt" else ["sh"]
        done = subprocess.run([*shell, str(self.bin / "jira"), "version"], capture_output=True, text=True, env=self.env, cwd=self.root, timeout=60)
        self.assertEqual((done.returncode, done.stdout), (0, f"jira-dc-cli {VERSION}\n"), done.stderr)

    def test_exit_codes_pass_through_the_launcher(self):
        self.install()
        self.assertEqual(self.run_launcher("nonsense").returncode, 2)

    def test_a_stray_pythonpath_cannot_hijack_the_launcher(self):
        self.install()
        evil = self.root / "evil" / "jira_dc_cli"
        evil.mkdir(parents=True)
        (evil / "__init__.py").write_text("")
        (evil / "bootstrap.py").write_text("def main(argv=None):\n    print('HIJACKED')\n    return 0\n")
        done = self.run_launcher("version", env={"PYTHONPATH": str(self.root / "evil")})
        self.assertEqual(done.stdout, f"jira-dc-cli {VERSION}\n")

    def test_the_installed_program_does_not_need_the_source_checkout(self):
        self.install()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        done = self.run_launcher("help", cwd=elsewhere)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("transition", done.stdout)

    def test_the_installed_documentation_and_skill_are_readable_offline(self):
        self.install()
        topics = self.run_launcher("how-to")
        self.assertEqual(topics.returncode, 0, topics.stderr)
        for name in ("readme", "daily-workflow", "use-with-ai-agents", "import-tickets", "architecture", "json-output"):
            self.assertIn(name, topics.stdout)
        self.assertIn("jira-dc-cli", self.run_launcher("how-to", "readme").stdout)
        self.assertIn(f"## [{VERSION}]", self.run_launcher("changelog").stdout)
        self.assertIn("name: jira-dc-cli", self.run_launcher("skill").stdout)
        target = self.root / "skills"
        done = self.run_launcher("skill", "install", "--dir", str(target))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("name: jira-dc-cli", (target / "jira-dc-cli" / "SKILL.md").read_text(encoding="utf-8"))

    def test_update_replaces_everything_and_leaves_nothing_behind(self):
        self.install()
        (self.app / "jira_dc_cli" / "stale_module.py").write_text("x = 1\n")
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.app / "jira_dc_cli" / "stale_module.py").exists())
        self.assertEqual(sorted(entry.name for entry in self.root.iterdir() if entry.name.startswith("app")), ["app"])

    def test_an_update_keeps_the_configuration_and_rewrites_the_launchers(self):
        self.install()
        config = self.root / "xdg" / "jira-dc-cli" / "config"
        config.parent.mkdir(parents=True)
        config.write_text("JIRA_PROJECT=PROJ\n")
        (self.bin / "jira").write_text('#!/bin/sh\n# jira-dc-cli launcher, written by install.py\nexec python "/somewhere/else/jira.py" "$@"\n')
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(config.read_text(), "JIRA_PROJECT=PROJ\n")
        self.assertIn(str(self.app / "jira.py").replace("\\", "/"), (self.bin / "jira").read_text())

    def test_a_folder_that_is_not_ours_is_left_alone(self):
        self.app.mkdir()
        (self.app / "notes.txt").write_text("precious")
        done = self.install()
        self.assertEqual(done.returncode, 1)
        self.assertIn("not an installation of jira-dc-cli", done.stderr)
        self.assertEqual((self.app / "notes.txt").read_text(), "precious")

    def test_a_jira_py_alone_does_not_make_a_folder_ours(self):
        self.app.mkdir()
        (self.app / "jira.py").write_text("print('a different tool')\n")
        done = self.install()
        self.assertEqual(done.returncode, 1)
        self.assertEqual((self.app / "jira.py").read_text(), "print('a different tool')\n")

    def test_force_moves_a_foreign_folder_aside_instead_of_deleting_it(self):
        self.app.mkdir()
        (self.app / "notes.txt").write_text("precious")
        done = self.install("--force")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("kept in", done.stdout)
        kept = [entry for entry in self.root.iterdir() if entry.name.startswith("app.old-")]
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "notes.txt").read_text(), "precious")
        self.assertTrue((self.app / "jira.py").is_file())

    def test_folders_that_happen_to_be_named_like_staging_folders_are_never_touched(self):
        for name in ("app.new", "app.old"):
            (self.root / name).mkdir()
            (self.root / name / "mine.txt").write_text("keep me")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.install().returncode, 0)  # an update as well
        for name in ("app.new", "app.old"):
            self.assertEqual((self.root / name / "mine.txt").read_text(), "keep me")
        self.assertEqual(sorted(entry.name for entry in self.root.iterdir() if entry.name.startswith("app")), ["app", "app.new", "app.old"])

    def test_a_foreign_launcher_is_not_overwritten_without_force(self):
        self.bin.mkdir()
        (self.bin / "jira").write_text("#!/bin/sh\necho someone elses tool\n")
        done = self.install()
        self.assertEqual(done.returncode, 1)
        self.assertIn("--force", done.stderr)
        self.assertEqual((self.bin / "jira").read_text(), "#!/bin/sh\necho someone elses tool\n")
        self.assertFalse(self.app.exists())  # refused before anything was changed
        self.assertEqual(self.install("--force").returncode, 0)
        self.assertIn("jira.py", (self.bin / "jira").read_text())

    def test_a_launcher_that_only_mentions_the_program_is_not_ours(self):
        self.bin.mkdir()
        wrapper = "#!/bin/sh\n# my own wrapper around jira-dc-cli and its jira.py\nexec something-else\n"
        (self.bin / "jira").write_text(wrapper)
        self.assertEqual(self.install().returncode, 1)
        self.assertEqual((self.bin / "jira").read_text(), wrapper)

    def test_read_only_launchers_are_replaced_and_removed(self):
        self.install()
        for launcher in self.bin.iterdir():
            os.chmod(launcher, 0o444)
        self.assertEqual(self.install().returncode, 0)
        for launcher in self.bin.iterdir():
            os.chmod(launcher, 0o444)
        done = self.uninstall()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(list(self.bin.iterdir()), [])

    def test_the_source_folder_cannot_be_the_target(self):
        done = subprocess.run([sys.executable, str(ROOT / "install.py"), "--prefix", str(ROOT), "--bin-dir", str(self.bin)], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertIn("source folder", done.stderr)

    def test_another_python_can_be_chosen(self):
        done = self.install("--python", sys.executable)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"Python:      {os.path.abspath(sys.executable)}", done.stdout)

    def test_a_python_that_is_too_old_is_refused_with_a_clear_message(self):
        installer = load_installer()
        error = io.StringIO()
        with mock.patch.object(sys, "version_info", (3, 9, 0)), redirect_stderr(error), redirect_stdout(io.StringIO()):
            code = installer.main(["--prefix", str(self.app), "--bin-dir", str(self.bin)])
        self.assertEqual(code, 1)
        self.assertIn("Python 3.10 or newer", error.getvalue())
        self.assertFalse(self.app.exists())

    def test_a_read_only_folder_in_the_source_does_not_block_removal(self):
        self.install()
        folder = self.app / "docs"
        folder.mkdir(exist_ok=True)
        os.chmod(folder, 0o555) if os.name != "nt" else subprocess.run(["attrib", "+R", str(folder)], capture_output=True)
        self.assertEqual(self.uninstall().returncode, 0)
        self.assertFalse(self.app.exists())


class UninstallTest(InstallerCase):
    def test_removes_the_program_and_the_launchers_but_keeps_the_configuration(self):
        self.install()
        config = self.root / "xdg" / "jira-dc-cli" / "config"
        config.parent.mkdir(parents=True)
        config.write_text("JIRA_PAT=x\n")
        done = self.uninstall()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.app.exists())
        self.assertFalse((self.bin / "jira").exists() or (self.bin / "jira.cmd").exists())
        self.assertEqual(config.read_text(), "JIRA_PAT=x\n")
        self.assertIn("configuration was kept", done.stdout)

    def test_purge_removes_the_configuration_and_the_cache_too(self):
        self.install()
        for base, name in (("xdg", "config"), ("cache", "fields-PROJ.json")):
            target = self.root / base / "jira-dc-cli" / name
            target.parent.mkdir(parents=True)
            target.write_text("x")
        self.assertEqual(self.uninstall("--purge").returncode, 0)
        self.assertFalse((self.root / "xdg" / "jira-dc-cli").exists() or (self.root / "cache" / "jira-dc-cli").exists())

    def test_it_does_not_touch_what_is_not_ours(self):
        self.bin.mkdir()
        (self.bin / "jira").write_text("echo not ours\n")
        self.app.mkdir()
        (self.app / "notes.txt").write_text("keep")
        (self.app / "jira.py").write_text("print('someone else')\n")  # a jira.py alone is not an installation
        done = self.uninstall()
        self.assertEqual(done.returncode, 0)
        self.assertTrue((self.bin / "jira").exists() and (self.app / "notes.txt").exists() and (self.app / "jira.py").exists())
        self.assertIn("does not look like an installation", done.stdout)

    def test_dry_run_and_nothing_to_remove(self):
        self.install()
        done = self.uninstall("--dry-run")
        self.assertIn("Would remove", done.stdout)
        self.assertTrue(self.app.exists())
        self.uninstall()
        self.assertIn("Nothing to remove.", self.uninstall().stdout)


if __name__ == "__main__":
    unittest.main()
