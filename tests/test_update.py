"""Updater regression tests using disposable Git repositories; never install."""

import contextlib
import io
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monitorize_vkms.cli import main


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.checkout = self.root / "checkout with spaces"
        self.publisher = self.root / "publisher"
        self.run_git("init", "--bare", "--initial-branch=main", str(self.remote))
        self.run_git("clone", str(self.remote), str(self.publisher))
        self.run_git("-C", str(self.publisher), "config", "user.name", "Updater Test")
        self.run_git("-C", str(self.publisher), "config", "user.email", "test@example.invalid")
        (self.publisher / "install.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.publisher / "uninstall.sh").write_text("#!/bin/sh\nexit 0\n")
        self.run_git("-C", str(self.publisher), "config", "commit.gpgsign", "false")
        self.commit("initial")
        self.run_git("clone", str(self.remote), str(self.checkout))
        self.record = self.root / "source-checkout"
        self.record.write_text(str(self.checkout) + "\n")
        self.calls = []
        self.install_result = 0
        self.real_run = subprocess.run

    @staticmethod
    def run_git(*args):
        return subprocess.run(["git", *args], check=True, capture_output=True,
                              text=True, timeout=10).stdout.strip()

    def commit(self, message):
        self.run_git("-C", str(self.publisher), "add", ".")
        self.run_git("-C", str(self.publisher), "commit", "-m", message)
        self.run_git("-C", str(self.publisher), "push", "origin", "main")

    def run_update(self, args=None, *, uid=None, official=True):
        # The production remote allowlist is replaced only for these local tests.
        def run(command, **kwargs):
            self.calls.append(command)
            if command[0] == "sudo":
                return subprocess.CompletedProcess(command, self.install_result)
            return self.real_run(command, **kwargs)

        with (
            patch("monitorize_vkms.update.SOURCE_RECORD", self.record),
            patch("monitorize_vkms.update.OFFICIAL_REMOTES", {str(self.remote)} if official else set()),
            patch("monitorize_vkms.update.os.geteuid", return_value=1000 if uid is None else uid),
            patch("monitorize_vkms.update.shutil.which", side_effect=lambda name: name),
            patch("monitorize_vkms.update.subprocess.run", side_effect=run),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
            patch("monitorize_vkms.update.Path.stat", autospec=True, side_effect=self.path_stat),
        ):
            # Tests also work in root-owned CI containers: pretend the checkout
            # belongs to an unprivileged caller without changing its ownership.
            result = main(args or ["update"])
        return result, output.getvalue(), errors.getvalue()

    def path_stat(self, path, **kwargs):
        result = self.real_stat(path, **kwargs)
        if path == self.checkout:
            values = list(result)
            values[4] = 1000
            return type(result)(values)
        return result

    real_stat = staticmethod(Path.stat)

    def assert_no_install(self):
        self.assertFalse(any(call[0] == "sudo" for call in self.calls))

    def test_uninstall_runs_existing_script_without_fetch_or_build(self):
        result, output, errors = self.run_update(["uninstall"])
        self.assertEqual(result, 0, errors)
        self.assertEqual(self.calls[-1], ["sudo", "--", str(self.checkout / "uninstall.sh")])
        self.assertFalse(any("fetch" in call for call in self.calls))
        self.assertIn("uninstalled", output)
        self.assertNotIn("Matching headers", output)

    def test_fast_forward_and_install_from_saved_path(self):
        (self.publisher / "new-file").write_text("new release")
        self.commit("release")
        result, output, errors = self.run_update()
        self.assertEqual(result, 0, errors)
        self.assertEqual((self.checkout / "new-file").read_text(), "new release")
        self.assertEqual(self.calls[-1], ["sudo", "--", str(self.checkout / "install.sh")])
        self.assertIn("Reboot to activate", output)

    def test_unchanged_source_still_updates_installed_files(self):
        result, output, errors = self.run_update()
        self.assertEqual(result, 0, errors)
        self.assertIn("Source is already up to date", output)
        self.assertEqual(self.calls[-1], ["sudo", "--", str(self.checkout / "install.sh")])

    def test_reinstall_installs_without_fetching_or_merging(self):
        # Reinstall works even when the remote is unavailable or has new commits.
        (self.publisher / "new-file").write_text("new release")
        self.commit("release")
        self.remote.rename(self.root / "unavailable.git")
        result, output, errors = self.run_update(["reinstall"])
        self.assertEqual(result, 0, errors)
        self.assertFalse((self.checkout / "new-file").exists())
        self.assertFalse(any("fetch" in call or "merge" in call for call in self.calls))
        self.assertEqual(self.calls[-1][0], "sudo")
        self.assertIn("reinstalled", output)

    def test_source_override_for_missing_record(self):
        self.record.unlink()
        result, _, errors = self.run_update(["reinstall", "--source", str(self.checkout)])
        self.assertEqual(result, 0, errors)
        self.assertEqual(self.calls[-1][0], "sudo")

    def test_missing_record_gives_recovery_command(self):
        self.record.unlink()
        result, _, errors = self.run_update()
        self.assertEqual(result, 1)
        self.assertIn("--source", errors)
        self.assert_no_install()

    def test_root_is_rejected_before_git(self):
        result, _, errors = self.run_update(uid=0)
        self.assertEqual(result, 1)
        self.assertIn("without sudo", errors)
        self.assertEqual(self.calls, [])

    def test_wrong_remote_is_rejected(self):
        result, _, errors = self.run_update(official=False)
        self.assertEqual(result, 1)
        self.assertIn("official", errors)
        self.assert_no_install()

    def test_dirty_tracked_and_untracked_files_are_preserved(self):
        for name in ("install.sh", "personal-file"):
            with self.subTest(name=name):
                path = self.checkout / name
                original = path.read_text() if path.exists() else None
                path.write_text("personal changes")
                result, _, errors = self.run_update()
                self.assertEqual(result, 1)
                self.assertIn("local changes", errors)
                self.assertEqual(path.read_text(), "personal changes")
                self.assert_no_install()
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original)

    def test_other_branch_and_detached_head_are_rejected(self):
        for args in (("checkout", "-b", "work"), ("checkout", "--detach")):
            self.run_git("-C", str(self.checkout), *args)
            result, _, errors = self.run_update()
            self.assertEqual(result, 1)
            self.assertIn("Switch", errors)
            self.assert_no_install()

    def test_local_commits_are_preserved_and_rejected(self):
        self.run_git("-C", str(self.checkout), "-c", "user.name=Test", "-c",
                     "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "local")
        head = self.run_git("-C", str(self.checkout), "rev-parse", "HEAD")
        result, _, errors = self.run_update()
        self.assertEqual(result, 1)
        self.assertIn("local commits", errors)
        self.assertEqual(self.run_git("-C", str(self.checkout), "rev-parse", "HEAD"), head)
        self.assert_no_install()

    def test_fetch_failure_stops_before_install(self):
        self.remote.rename(self.root / "unavailable.git")
        result, _, errors = self.run_update()
        self.assertEqual(result, 1)
        self.assertIn("Update stopped", errors)
        self.assert_no_install()

    def test_installer_failure_has_no_success_message(self):
        self.install_result = 7
        (self.publisher / "new-file").write_text("new release")
        self.commit("release")
        result, output, errors = self.run_update()
        self.assertEqual(result, 7)
        self.assertIn("Update failed", errors)
        self.assertIn("monitorize-vkms reinstall", errors)
        self.assertNotIn("Reboot to activate", output)
        self.calls.clear()
        self.install_result = 0
        result, _, errors = self.run_update(["reinstall"])
        self.assertEqual(result, 0, errors)
        self.assertEqual(self.calls[-1][0], "sudo")
