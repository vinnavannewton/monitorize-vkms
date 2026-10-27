import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FILES = ("VERSION", "dkms.conf", "packaging/rpm/monitorize-vkms.spec",
         "packaging/arch/PKGBUILD", "packaging/deb/debian/postrm",
         "packaging/deb/debian/changelog")
SPEC = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts/bump-version.py")
bump = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bump)
NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


class BumpVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        self.patch_root = patch.object(bump, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.old = (self.root / "VERSION").read_text().strip()
        self.new = f"{int(self.old.split('.')[0]) + 1}.0"

    def snapshot(self):
        return {relative: (self.root / relative).read_bytes() for relative in FILES}

    def test_bump_synchronizes_all_fields_and_preserves_history_and_modes(self):
        history = (self.root / FILES[-1]).read_text()
        postrm = self.root / "packaging/deb/debian/postrm"
        mode = stat.S_IMODE(postrm.stat().st_mode)
        (self.root / "unrelated.txt").write_text("keep this")
        old, changes = bump.prepare(self.new, NOW)
        self.assertEqual(old, self.old)
        self.assertEqual(set(changes), set(FILES))
        bump.write_all(changes)
        self.assertEqual((self.root / "VERSION").read_text(), self.new + "\n")
        self.assertIn(f'PACKAGE_VERSION="{self.new}"', (self.root / "dkms.conf").read_text())
        self.assertIn(f"Version: {self.new}\n", (self.root / FILES[2]).read_text())
        self.assertIn(f"pkgver={self.new}\n", (self.root / FILES[3]).read_text())
        self.assertIn(f"-v {self.new} --all", postrm.read_text())
        changelog = (self.root / FILES[-1]).read_text()
        self.assertTrue(changelog.startswith(f"monitorize-vkms ({self.new}-1) unstable; urgency=medium\n"))
        self.assertTrue(changelog.endswith(history))
        self.assertEqual(stat.S_IMODE(postrm.stat().st_mode), mode)
        self.assertEqual((self.root / "unrelated.txt").read_text(), "keep this")
        self.assertFalse(list(self.root.rglob(".bump-version-*")))

    def test_three_component_version_is_supported(self):
        _, changes = bump.prepare(self.new + ".1", NOW)
        self.assertEqual(changes["VERSION"], self.new + ".1\n")

    def test_package_revisions_are_reset(self):
        for relative, old, new in ((FILES[2], "Release: 1%{?dist}", "Release: 9%{?dist}"),
                                   (FILES[3], "pkgrel=1", "pkgrel=7"),
                                   (FILES[-1], f"({self.old}-1)", f"({self.old}-8)")):
            target = self.root / relative
            target.write_text(target.read_text().replace(old, new, 1))
        _, changes = bump.prepare(self.new, NOW)
        self.assertIn("Release: 1%{?dist}\n", changes[FILES[2]])
        self.assertIn("pkgrel=1\n", changes[FILES[3]])
        self.assertTrue(changes[FILES[-1]].startswith(f"monitorize-vkms ({self.new}-1)"))

    def test_invalid_or_unchanged_version_leaves_files_untouched(self):
        before = self.snapshot()
        for version in (self.old, "1", "v1.0", "01.0", "1.0.0.1", "1.0-rc1", "1.0\n"):
            with self.subTest(version=version), self.assertRaises(bump.BumpError):
                bump.prepare(version, NOW)
            self.assertEqual(self.snapshot(), before)

    def test_each_mismatched_version_is_rejected_without_writes(self):
        originals = self.snapshot()
        for relative in FILES[1:]:
            with self.subTest(relative=relative):
                target = self.root / relative
                target.write_text(target.read_text().replace(self.old, "999.0", 1))
                before = self.snapshot()
                with self.assertRaises(bump.BumpError):
                    bump.prepare(self.new, NOW)
                self.assertEqual(self.snapshot(), before)
                target.write_bytes(originals[relative])

    def test_duplicate_version_field_is_rejected(self):
        target = self.root / "dkms.conf"
        with target.open("a") as stream:
            stream.write(f'\nPACKAGE_VERSION="{self.old}"\n')
        before = self.snapshot()
        with self.assertRaises(bump.BumpError):
            bump.prepare(self.new, NOW)
        self.assertEqual(self.snapshot(), before)

    def test_write_failure_restores_original_files_and_cleans_temporary_files(self):
        before = self.snapshot()
        _, changes = bump.prepare(self.new, NOW)
        replace = os.replace
        calls = 0

        def fail_second_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated replacement failure")
            replace(source, destination)

        with patch.object(bump.os, "replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "simulated replacement failure"):
                bump.write_all(changes)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.root.rglob(".bump-version-*")))

    def test_executable_command_works_from_another_directory(self):
        script = self.root / "scripts/bump-version.py"
        script.parent.mkdir()
        shutil.copy2(ROOT / "scripts/bump-version.py", script)
        result = subprocess.run([str(script), self.new], cwd=self.root.parent,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Version synchronized successfully: {self.new}", result.stdout)
        self.assertEqual((self.root / "VERSION").read_text().strip(), self.new)
