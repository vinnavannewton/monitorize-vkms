"""Behavioral checks for independent VKMS display ownership and D-Bus entry."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monitorize_vkms import helper
from monitorize_vkms import dbus_service
from monitorize_vkms.edid import generate_edid
from monitorize_vkms.drm import monitorize_drm_connectors


class TwoDisplayTests(unittest.TestCase):
    def setUp(self):
        # These cases exercise the unenrolled path regardless of host grants.
        grant = patch.object(dbus_service.authorization, 'read', return_value=None)
        grant.start()
        self.addCleanup(grant.stop)

    def test_edids_have_distinct_stable_identity(self):
        first = generate_edid(1920, 1080, 60, "mon1")
        second = generate_edid(1920, 1080, 60, "mon2")
        self.assertEqual(first[77:81], b"mon1")
        self.assertEqual(second[77:81], b"mon2")
        self.assertNotEqual(first[10:16], second[10:16])
        self.assertEqual(sum(first) % 256, 0)
        self.assertEqual(sum(second) % 256, 0)

    def test_drm_discovery_uses_edid_identity_instead_of_connector_number(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "faux" / "monitorize"
            device.mkdir(parents=True)
            connector = root / "card4-Virtual-7"
            connector.mkdir()
            (connector / "device").symlink_to(device)
            (connector / "status").write_text("connected\n")
            (connector / "modes").write_text("1920x1080\n")
            (connector / "connector_id").write_text("19\n")
            (connector / "edid").write_bytes(generate_edid(1920, 1080, 60, "mon2"))
            discovered = monitorize_drm_connectors(root)
        self.assertEqual(discovered["Virtual-7"]["display"], "mon2")

    def test_unnamed_create_fills_second_display_when_first_is_connected(self):
        def topology(_root, _logs, slot="mon1"):
            return Path("/fake/monitorize"), Path(f"/fake/monitorize/connectors/{helper.INSTANCE_NAMES[slot]}")

        def read(path):
            return "1" if path.name == "status" and path.parent.name == "connector0" else "2"

        second = {"Virtual-2": {"name": "Virtual-2", "card": "card2", "connector_id": 8,
                                "status": "connected", "modes": ["1920x1080"], "display": "mon2",
                                "edid_sha256": hashlib.sha256(generate_edid(1920, 1080, 60, "mon2")).hexdigest()}}
        output = io.StringIO()
        with patch.object(helper, "_require_polkit_root"), \
             patch.object(helper, "_configfs_root", return_value=Path("/fake")), \
             patch.object(helper, "_require_bootstrap", side_effect=topology), \
             patch.object(helper, "_acquire_lifecycle_lock", return_value=99), \
             patch.object(helper.os, "close"), \
             patch.dict(helper.os.environ, {"PKEXEC_UID": "1000"}), \
             patch.object(helper, "_active_session", return_value=("3", {})), \
             patch.object(helper, "_read", side_effect=read), \
             patch.object(helper, "_read_lease", return_value=None), \
             patch.object(helper, "_write_lease"), \
             patch.object(helper, "_enable_connector", return_value=True), \
             patch.object(helper, "_run_worker", side_effect=[{"desktop": None}, {"desktop": None, "details": {}}]), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", side_effect=[{}, second]), \
             patch.object(helper.sys, "argv", ["helper", "create", "--slot", "auto", "--mode", "1920", "1080", "60"]), \
             redirect_stdout(output):
            self.assertEqual(helper.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["slot"], "mon2")

    def test_remove_all_reports_partial_failure_without_claiming_success(self):
        output = io.StringIO()
        with patch.object(helper, "_require_polkit_root"), \
             patch.object(helper, "_configfs_root", return_value=Path("/fake")), \
             patch.object(helper, "_require_bootstrap", return_value=(Path("/fake"), Path("/fake/connector0"))), \
             patch.object(helper, "_acquire_lifecycle_lock", return_value=99), \
             patch.object(helper.os, "close"), \
             patch.dict(helper.os.environ, {"PKEXEC_UID": "1000"}), \
             patch.object(helper, "_active_session", return_value=("3", {})), \
             patch.object(helper, "_destroy_slot", side_effect=[True, helper.VkmsHelperError("unsafe to remove")]), \
             patch.object(helper.sys, "argv", ["helper", "destroy-all"]), \
             redirect_stdout(output):
            self.assertEqual(helper.main(), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["success"])
        self.assertTrue(result["results"]["mon1"]["changed"])
        self.assertIn("unsafe", result["results"]["mon2"]["error"])

    def test_named_removal_disconnects_only_its_own_connector(self):
        second_path = Path("/fake/monitorize/connectors/connector1")
        lease = {"uid": 1000, "session": "3", "boot_id":
                 Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                 "generation": "g2", "connector": "Virtual-7", "connector_id": 19,
                 "card": "card4", "display": "mon2", "edid_sha256": "digest", "desktop": None}
        drm = {"Virtual-7": {"name": "Virtual-7", "status": "connected", "connector_id": 19,
                             "card": "card4", "display": "mon2", "edid_sha256": "digest"}}
        with patch.object(helper, "_require_bootstrap", return_value=(Path("/fake/monitorize"), second_path)), \
             patch.object(helper, "_read_lease", return_value=lease) as read_lease, \
             patch.object(helper, "_read", return_value="1"), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value=drm), \
             patch.object(helper, "_disconnect_connector", return_value=True) as disconnect, \
             patch.object(helper, "_remove_lease") as remove_lease:
            self.assertTrue(helper._destroy_slot(Path("/fake"), [], "mon2", 1000, "3", {}, "g2"))
        read_lease.assert_called_once_with("mon2")
        disconnect.assert_called_once()
        self.assertEqual(disconnect.call_args.args[0], second_path)
        remove_lease.assert_called_once_with("mon2")

    def test_dbus_request_authorizes_unique_sender_and_sets_verified_uid(self):
        def completed(code, stdout=""):
            return subprocess.CompletedProcess([], code, stdout, "")

        with patch.object(dbus_service.subprocess, "run", side_effect=[
            completed(0), completed(0, '{"success": true, "slot": "mon2"}\n')]) as run:
            result = json.loads(dbus_service._request(":1.42", 1000, "create",
                                                        ["create", "--slot", "mon2"]))
        self.assertEqual(result["slot"], "mon2")
        self.assertIn(":1.42", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].kwargs["env"]["PKEXEC_UID"], "1000")

    def test_dbus_denial_never_invokes_privileged_helper(self):
        with patch.object(dbus_service.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")) as run:
            with self.assertRaises(PermissionError):
                dbus_service._request(":1.42", 1000, "create", ["create", "--slot", "mon1"])
        run.assert_called_once()

    def test_dbus_capabilities_reports_reboot_needed_for_old_topology(self):
        def completed(code, stdout=""):
            return subprocess.CompletedProcess([], code, stdout, "")

        with patch.object(dbus_service.subprocess, "run", side_effect=[
            completed(0), completed(1, '{"success": false, "message": "Two-display Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms."}\n')]):
            result = json.loads(dbus_service._request(":1.42", 1000, "capability", ["capability"]))
        self.assertFalse(result["topology_ready"])
        self.assertEqual(result["displays"], ["mon1", "mon2"])

    def test_dbus_capability_does_not_mask_other_helper_failure(self):
        with patch.object(dbus_service.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 1, '{"success": false, "message": "Permission denied"}\n', ""),
        ]):
            with self.assertRaisesRegex(RuntimeError, "Permission denied"):
                dbus_service._request(":1.42", 1000, "capability", ["capability"])


if __name__ == "__main__":
    unittest.main()
