"""Tests for persistent connector state transitions in the privileged helper."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from monitorize_vkms.helper import (
    CONNECTOR_STATUS_CONNECTED,
    CONNECTOR_STATUS_DISCONNECTED,
    VkmsHelperError,
    _disconnect_connector,
    _enable_connector,
)


class TestPersistentConnectorHelper(unittest.TestCase):
    def setUp(self):
        self.connector = Path("/config/vkms/monitorize/connectors/connector0")
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


if __name__ == "__main__":
    unittest.main()
