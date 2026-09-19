"""Tests for operation-scoped Polkit authorization."""

from __future__ import annotations

from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


POLICY = (
    Path(__file__).resolve().parent.parent
    / "packaging/io.github.vinnavannewton.monitorize-vkms.policy"
)
ACTION_PREFIX = "io.github.vinnavannewton.monitorize-vkms."
HELPER_PATH = "/usr/libexec/monitorize-vkms/monitorize-vkms-helper"


class TestPolkitPolicy(unittest.TestCase):
    def test_helper_operations_have_scoped_authorization(self):
        root = ET.parse(POLICY).getroot()
        actions = {action.attrib["id"]: action for action in root.findall("action")}
        expected = {
            "create": "auth_admin_keep",
            "create-custom": "auth_admin_keep",
            "destroy": "yes",
            "status": "yes",
            "capability": "yes",
        }

        self.assertEqual(set(actions), {ACTION_PREFIX + name for name in expected})
        for operation, active_default in expected.items():
            with self.subTest(operation=operation):
                action = actions[ACTION_PREFIX + operation]
                annotations = {
                    item.attrib["key"]: item.text for item in action.findall("annotate")
                }
                self.assertEqual(
                    annotations["org.freedesktop.policykit.exec.path"], HELPER_PATH
                )
                self.assertEqual(
                    annotations["org.freedesktop.policykit.exec.argv1"], operation
                )
                self.assertEqual(action.findtext("defaults/allow_active"), active_default)

        destroy = actions[ACTION_PREFIX + "destroy"]
        self.assertEqual(destroy.findtext("defaults/allow_any"), "no")
        self.assertEqual(destroy.findtext("defaults/allow_inactive"), "no")


if __name__ == "__main__":
    unittest.main()
