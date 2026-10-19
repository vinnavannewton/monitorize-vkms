"""Tests for persistent connector state transitions in the privileged helper."""

from __future__ import annotations

from pathlib import Path
import shutil
import stat
import subprocess
import sys
import contextlib
import io
import json
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from monitorize_vkms.helper import (
    CONNECTOR_STATUS_CONNECTED,
    CONNECTOR_STATUS_DISCONNECTED,
    VkmsHelperError,
    _configfs_root,
    _disconnect_connector,
    _enable_connector,
    _lease_matches_request,
    _parse_session_environment,
    _validate_compositor_support,
    _validate_edid_mode,
    _acquire_lifecycle_lock,
    _worker_main,
)
from monitorize_vkms.edid import generate_edid
from monitorize_vkms.errors import CompositorError


class TestInstalledHelperStartup(unittest.TestCase):
    def test_imports_package_from_installed_layout(self):
        source = Path(__file__).resolve().parent.parent / "monitorize_vkms"
        with TemporaryDirectory() as directory:
            usr = Path(directory) / "usr"
            helper_dir = usr / "libexec" / "monitorize-vkms"
            package_dir = usr / "lib" / "monitorize-vkms" / "monitorize_vkms"
            helper_dir.mkdir(parents=True)
            package_dir.mkdir(parents=True)
            helper = helper_dir / "monitorize-vkms-helper"
            shutil.copy2(source / "helper.py", helper)
            for filename in ("__init__.py", "configfs.py"):
                shutil.copy2(source / filename, package_dir / filename)

            result = subprocess.run(
                [sys.executable, "-I", str(helper), "--help"],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("create-custom", result.stdout)


class TestConfigfsOwnership(unittest.TestCase):
    def test_uses_monitorize_namespace_when_stock_namespace_also_exists(self):
        with TemporaryDirectory() as directory:
            mount = Path(directory)
            (mount / "vkms").mkdir()
            (mount / "monitorize-vkms").mkdir()
            logs: list[str] = []
            with patch("monitorize_vkms.helper._configfs_mounts", return_value=[mount]):
                self.assertEqual(_configfs_root(logs), mount)
            self.assertEqual(logs, [f"Using configfs mountpoint {mount}"])

    def test_rejects_stock_namespace_without_monitorize_namespace(self):
        with TemporaryDirectory() as directory:
            mount = Path(directory)
            (mount / "vkms").mkdir()
            with patch("monitorize_vkms.helper._configfs_mounts", return_value=[mount]):
                with self.assertRaisesRegex(VkmsHelperError, "Monitorize VKMS configfs is not registered"):
                    _configfs_root([])


class TestLifecyclePreflight(unittest.TestCase):
    def test_lease_is_bound_to_owner_boot_edid_mode_and_state(self):
        lease = {"uid": 1000, "session": "3", "boot_id": "boot-a", "edid_sha256": "edid-a",
                 "mode": [1920, 1080, 60], "state": "active"}
        self.assertTrue(_lease_matches_request(lease, uid=1000, session="3", boot_id="boot-a",
                                               edid_sha256="edid-a", mode=[1920, 1080, 60]))
        for changes in ({"uid": 1001}, {"session": "4"}, {"boot_id": "boot-b"},
                        {"edid_sha256": "edid-b"}, {"mode": [2560, 1440, 60]}, {"state": "pending"}):
            with self.subTest(changes=changes):
                altered = {**lease, **changes}
                self.assertFalse(_lease_matches_request(altered, uid=1000, session="3", boot_id="boot-a",
                                                        edid_sha256="edid-a", mode=[1920, 1080, 60]))

    def test_session_environment_uses_nul_separators(self):
        parsed = _parse_session_environment(
            b"PATH=/bin\0DISPLAY=:0\0WAYLAND_DISPLAY=wayland-0\0UNTRUSTED=value\0"
        )
        self.assertEqual(parsed, {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"})

    def test_unvalidated_desktops_and_unknown_sessions_fail_before_connect(self):
        for desktop in ("cosmic", "sway", "unknown"):
            with self.subTest(desktop=desktop), self.assertRaises(VkmsHelperError):
                _validate_compositor_support(desktop, {"WAYLAND_DISPLAY": "wayland-0"})
        with self.assertRaisesRegex(VkmsHelperError, "unmanaged output"):
            _validate_compositor_support(None, {"DISPLAY": ":0"})
        _validate_compositor_support("kde", {"WAYLAND_DISPLAY": "wayland-0"})
        _validate_compositor_support("hyprland", {"WAYLAND_DISPLAY": "wayland-0"})
        _validate_compositor_support("niri", {"WAYLAND_DISPLAY": "wayland-0", "NIRI_SOCKET": "/run/user/1000/niri.sock"})
        with self.assertRaisesRegex(VkmsHelperError, "Niri IPC socket"):
            _validate_compositor_support("niri", {"WAYLAND_DISPLAY": "wayland-0"})

    def test_worker_treats_false_deactivation_as_failure(self):
        request = {"action": "deactivate", "name": "Virtual-1", "connector_id": 7}
        with patch("monitorize_vkms.helper.os.geteuid", return_value=1000), \
             patch("monitorize_vkms.helper.sys.stdin", io.StringIO(json.dumps(request))), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-1": {"status": "connected", "connector_id": 7}}), \
             patch("monitorize_vkms.compositor.detect_compositor", return_value="kde"), \
             patch("monitorize_vkms.compositor.deactivate_in_compositor", return_value=False), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(CompositorError, "did not confirm deactivation"):
                _worker_main()

    def test_lifecycle_lock_rejects_concurrent_operation(self):
        with TemporaryDirectory() as directory, \
             patch("monitorize_vkms.helper.LIFECYCLE_LOCK", Path(directory) / "lifecycle.lock"), \
             patch("monitorize_vkms.helper.os.geteuid", return_value=0), \
             patch("monitorize_vkms.helper.os.fstat", return_value=SimpleNamespace(
                 st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1
             )):
            first = _acquire_lifecycle_lock()
            try:
                with self.assertRaisesRegex(VkmsHelperError, "Another Monitorize VKMS"):
                    _acquire_lifecycle_lock()
            finally:
                import os
                os.close(first)


class TestPersistentConnectorHelper(unittest.TestCase):
    def setUp(self):
        self.connector = Path("/config/monitorize-vkms/monitorize/connectors/connector0")
        self.values = {
            "enabled": "1",
            "status": CONNECTOR_STATUS_DISCONNECTED,
            "edid_enabled": "1",
        }
        self.writes: list[tuple[str, str | bytes]] = []

    def _read(self, path: Path) -> str:
        return self.values[path.name]

    def _write(self, path: Path, value: str) -> None:
        self.writes.append((path.name, value))
        self.values[path.name] = value

    def _write_bytes(self, path: Path, value: bytes) -> None:
        self.writes.append((path.name, value))

    def test_destroy_disconnects_without_unregistering_connector(self):
        self.values["status"] = CONNECTOR_STATUS_CONNECTED
        with (
            patch("monitorize_vkms.helper._read", side_effect=self._read),
            patch("monitorize_vkms.helper._write", side_effect=self._write),
        ):
            changed = _disconnect_connector(self.connector, [])

        self.assertTrue(changed)
        self.assertEqual(self.values["enabled"], "1")
        self.assertEqual(
            self.writes,
            [("status", CONNECTOR_STATUS_DISCONNECTED)],
        )

    def test_create_custom_writes_edid_while_disconnected_then_connects(self):
        edid = b"\x00\xff\xff\xff\xff\xff\xff\x00" + bytes(119)
        edid += bytes([(-sum(edid)) % 256])
        with (
            patch("monitorize_vkms.helper._read", side_effect=self._read),
            patch("monitorize_vkms.helper._write", side_effect=self._write),
            patch("monitorize_vkms.helper._write_bytes", side_effect=self._write_bytes),
            patch.object(Path, "is_file", return_value=True),
        ):
            changed = _enable_connector(self.connector, [], edid)

        self.assertTrue(changed)
        self.assertEqual(
            self.writes,
            [
                ("edid", edid),
                ("edid_enabled", "1"),
                ("status", CONNECTOR_STATUS_CONNECTED),
            ],
        )
        self.assertEqual(self.values["enabled"], "1")

    def test_unregistered_connector_fails_closed(self):
        self.values["enabled"] = "0"
        with patch("monitorize_vkms.helper._read", side_effect=self._read):
            with self.assertRaisesRegex(VkmsHelperError, "Reinstall and reboot"):
                _disconnect_connector(self.connector, [])

    def test_custom_edid_preferred_timing_must_match_requested_mode(self):
        edid = generate_edid(1920, 1080, 60)
        _validate_edid_mode(edid, [1920, 1080, 60])
        with self.assertRaisesRegex(VkmsHelperError, "does not match requested"):
            _validate_edid_mode(edid, [2560, 1440, 60])

    def test_helper_worker_is_started_with_isolated_python(self):
        source = (Path(__file__).resolve().parent.parent / "monitorize_vkms" / "helper.py").read_text()
        self.assertIn('sys.executable, "-I", str(Path(__file__).resolve()), "--worker"', source)


if __name__ == "__main__":
    unittest.main()
