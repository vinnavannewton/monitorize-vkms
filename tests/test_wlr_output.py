import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from monitorize_vkms import cli, compositor, wlr_output
from monitorize_vkms.errors import CompositorError


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

    def test_foreground_cleanup_preserves_connector_on_error(self):
        with patch.object(cli, "get_compositor_snapshot", return_value=("sway", None)), \
             patch.object(cli, "monitorize_drm_connectors", return_value={}), \
             patch.object(cli, "helper_response", return_value={}) as helper, \
             patch.object(cli, "wait_for_new_drm_connector", return_value={
                 "name": "Virtual-1", "card": "card0", "path": "/sys/class/drm/card0-Virtual-1"}), \
             patch.object(cli, "activate_in_compositor", return_value=(True, {}, "ready")), \
             patch.object(cli, "deactivate_in_compositor", side_effect=CompositorError("rejected")), \
             patch.object(cli.signal, "signal"), \
             patch.object(cli.select, "select", return_value=([object()], [], [])), \
             patch.object(cli.sys, "stdin", io.StringIO("")), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["create", "1920x1080@60", "--foreground", "--json"]), 1)
        self.assertEqual([call.args[0] for call in helper.call_args_list], ["create-custom"])

    def test_cli_never_disconnects_after_deactivation_error(self):
        with patch.object(cli, "get_compositor_snapshot", return_value=("hyprland", None)), \
             patch.object(cli, "deactivate_in_compositor", side_effect=CompositorError("timeout")), \
             patch.object(cli, "helper_response") as helper, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["remove", "--json"]), 1)
            helper.assert_not_called()

    def test_cli_confirms_compositor_before_kernel_disconnect(self):
        calls = []
        with patch.object(cli, "get_compositor_snapshot", return_value=("sway", None)), \
             patch.object(cli, "deactivate_in_compositor", side_effect=lambda *a: calls.append("compositor")), \
             patch.object(cli, "helper_response", side_effect=lambda *a: calls.append("kernel") or {"changed": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["remove", "--json"]), 0)
        self.assertEqual(calls, ["compositor", "kernel"])

    def test_unknown_wayland_compositor_uses_capability_path(self):
        with patch.dict(compositor.os.environ, {"WAYLAND_DISPLAY": "wayland-1"}, clear=True), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-7": {"status": "connected"}}), \
             patch.object(wlr_output, "set_enabled") as change:
            compositor.deactivate_in_compositor(None, None)
        change.assert_called_once_with("Virtual-7", False)

    def test_hyprland_removal_never_disables_or_disconnects(self):
        for desktop, env in [("hyprland", {}), (None, {
                "WAYLAND_DISPLAY": "wayland-1", "HYPRLAND_INSTANCE_SIGNATURE": "test"})]:
            with self.subTest(desktop=desktop), \
                 patch.dict(compositor.os.environ, env, clear=True), \
                 patch.object(cli, "get_compositor_snapshot", return_value=(desktop, None)), \
                 patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                     "Virtual-1": {"status": "connected"}}), \
                 patch.object(wlr_output, "set_enabled") as change, \
                 patch.object(cli, "helper_response") as helper, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cli.main(["remove", "--json"]), 1)
                self.assertIn("left connected", json.loads(output.getvalue())["message"])
                change.assert_not_called()
                helper.assert_not_called()

    def test_hyprland_already_disconnected_is_noop(self):
        with patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                "Virtual-1": {"status": "disconnected"}}), \
             patch.object(wlr_output, "set_enabled") as change:
            self.assertTrue(compositor.deactivate_in_compositor("hyprland", None))
            change.assert_not_called()


if __name__ == "__main__":
    unittest.main()
