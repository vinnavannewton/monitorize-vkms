import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from monitorize_vkms import cli, compositor, wlr_output
from monitorize_vkms.errors import CompositorError, HelperError


def result(enabled=True):
    return subprocess.CompletedProcess([], 0, json.dumps([
        {"name": "Virtual-1", "enabled": enabled},
        {"name": "eDP-1", "enabled": True},
    ]), "")


class OutputManagementTests(unittest.TestCase):
    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_disable_waits_for_confirmation(self, _which):
        with patch.object(wlr_output.subprocess, "run", side_effect=[
            result(), result(), result(), result(False),
        ]) as run, patch.object(wlr_output.time, "sleep"):
            self.assertTrue(wlr_output.set_enabled("Virtual-1", False))
        self.assertEqual(run.call_args_list[1].args[0],
                         ["/usr/bin/wlr-randr", "--output", "Virtual-1", "--off"])
        self.assertEqual(run.call_count, 4)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_recreation_enables_disabled_output(self, _which):
        with patch.object(wlr_output.subprocess, "run", side_effect=[
            result(False), result(), result(),
        ]) as run:
            wlr_output.set_enabled("Virtual-1", True)
        self.assertEqual(run.call_args_list[1].args[0][-1], "--on")

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_rejection_and_invalid_state_fail_closed(self, _which):
        for response in [subprocess.CompletedProcess([], 1, "", "unsupported protocol"),
                         subprocess.CompletedProcess([], 0, "not JSON", ""),
                         subprocess.CompletedProcess([], 0, "[]", "")]:
            with self.subTest(response=response), patch.object(wlr_output.subprocess, "run", return_value=response):
                with self.assertRaises(CompositorError):
                    wlr_output.set_enabled("Virtual-1", False)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_last_monitor_is_preserved(self, _which):
        response = subprocess.CompletedProcess([], 0, '[{"name":"Virtual-1","enabled":true}]', "")
        with patch.object(wlr_output.subprocess, "run", return_value=response) as run:
            with self.assertRaisesRegex(CompositorError, "last active"):
                wlr_output.set_enabled("Virtual-1", False)
            self.assertEqual(run.call_count, 1)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_timeout_fails_closed(self, _which):
        with patch.object(wlr_output.subprocess, "run", side_effect=subprocess.TimeoutExpired("wlr-randr", 5)):
            with self.assertRaises(CompositorError):
                wlr_output.set_enabled("Virtual-1", False)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_successful_apply_without_disabled_state_times_out(self, _which):
        with patch.object(wlr_output.subprocess, "run", return_value=result()), \
             patch.object(wlr_output.time, "monotonic", side_effect=[0, 0, 0, 0, 6]):
            with self.assertRaisesRegex(CompositorError, "Timed out confirming"):
                wlr_output.set_enabled("Virtual-1", False)

    def test_missing_client_fails_closed(self):
        with patch.object(wlr_output.shutil, "which", return_value=None):
            with self.assertRaisesRegex(CompositorError, "Install wlr-randr"):
                wlr_output.set_enabled("Virtual-1", False)

    def test_cli_delegates_lifecycle_to_privileged_helper(self):
        with patch.object(cli, "helper_response", return_value={"changed": True}) as helper, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["remove", "--json"]), 0)
        helper.assert_called_once_with("destroy-all", slot=None)

    def test_cli_reports_helper_failure_without_claiming_removal(self):
        with patch.object(cli, "helper_response", side_effect=HelperError("unsafe")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["remove", "--json"]), 1)
        self.assertFalse(json.loads(output.getvalue())["success"])

    def test_hyprland_live_removal_is_fail_closed(self):
        with patch.object(wlr_output, "set_enabled") as change:
            with self.assertRaisesRegex(CompositorError, "Live VKMS removal is blocked"):
                compositor.deactivate_in_compositor("hyprland", None, target_name="Virtual-1")
        change.assert_not_called()

    def test_hyprland_creation_enables_the_identified_output(self):
        with patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={"Virtual-1": {}}), \
             patch.object(wlr_output, "set_enabled", return_value=True) as change:
            ok, details, _ = compositor.activate_in_compositor(
                "hyprland", None, 1920, 1080, 60, expected_output_name="Virtual-1"
            )
        self.assertTrue(ok)
        self.assertEqual(details["name"], "Virtual-1")
        change.assert_called_once_with("Virtual-1", True)

    def test_hyprland_doctor_distinguishes_creation_from_removal(self):
        with patch.object(cli, "detect_compositor", return_value="hyprland"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main(["doctor", "--json"])
        checks = {item["name"]: item for item in json.loads(output.getvalue())["checks"]}
        self.assertEqual(checks["Create backend is enabled"]["status"], "PASS")
        self.assertIn("experimental", checks["Create backend is enabled"]["detail"])
        self.assertEqual(checks["Live removal backend is enabled"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
