"""Tests for monitorize_vkms.drm discovery and waiting using standard unittest."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from monitorize_vkms.drm import (
    find_monitorize_card,
    monitorize_drm_connectors,
    wait_for_new_drm_connector,
)
from monitorize_vkms.errors import MonitorizeVkmsError


class TestDrmDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.drm_root = base / "sys" / "class" / "drm"
        self.drm_root.mkdir(parents=True)

        devices_root = base / "sys" / "devices"
        self.faux_dev = devices_root / "faux" / "monitorize" / "drm" / "card0"
        self.pci_dev = devices_root / "pci0000:00" / "drm" / "card1"
        self.faux_dev.mkdir(parents=True)
        self.pci_dev.mkdir(parents=True)

        # card0 is monitorize faux device
        card0 = self.drm_root / "card0"
        card0.mkdir()
        (card0 / "device").symlink_to(self.faux_dev)

        # card1 is real GPU
        card1 = self.drm_root / "card1"
        card1.mkdir()
        (card1 / "device").symlink_to(self.pci_dev)

        # card1 connector (real)
        card1_edp = self.drm_root / "card1-eDP-1"
        card1_edp.mkdir()
        (card1_edp / "device").symlink_to(self.pci_dev)
        (card1_edp / "status").write_text("connected\n")
        (card1_edp / "modes").write_text("1920x1080\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_monitorize_card(self):
        card = find_monitorize_card(drm_root=self.drm_root)
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "card0")

    def test_find_monitorize_card_not_found(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            self.assertIsNone(find_monitorize_card(drm_root=Path(empty_dir)))

    def test_monitorize_drm_connectors(self):
        # Add Virtual-1 to card0
        conn = self.drm_root / "card0-Virtual-1"
        conn.mkdir()
        (conn / "device").symlink_to(self.faux_dev)
        (conn / "status").write_text("connected\n")
        (conn / "modes").write_text("2340x1080\n1920x1080\n")

        connectors = monitorize_drm_connectors(drm_root=self.drm_root)
        self.assertEqual(len(connectors), 1)
        self.assertIn("Virtual-1", connectors)
        self.assertEqual(connectors["Virtual-1"]["status"], "connected")
        self.assertEqual(connectors["Virtual-1"]["modes"], ["2340x1080", "1920x1080"])
        self.assertEqual(connectors["Virtual-1"]["card"], "card0")

    def test_wait_for_new_drm_connector_timeout(self):
        with self.assertRaises(MonitorizeVkmsError) as ctx:
            wait_for_new_drm_connector(
                before=set(),
                width=2340,
                height=1080,
                timeout=0.2,
                interval=0.05,
                drm_root=self.drm_root,
            )
        self.assertIn("FAIL_STAGE=DRM_CONNECTOR_APPEAR", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
