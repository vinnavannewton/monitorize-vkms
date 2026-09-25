import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from monitorize_vkms import cli, compositor, cosmic_output
from monitorize_vkms.errors import CompositorError


def state(virtual=True, other=True):
    return (f'output "Virtual-2" enabled=#{str(virtual).lower()} {{\n}}\n'
            f'output "eDP-1" enabled=#{str(other).lower()} {{\n}}\n')


def result(output="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, output, stderr)


class CosmicOutputTests(unittest.TestCase):
    def test_detection_and_precedence(self):
        for env in (
            {"XDG_CURRENT_DESKTOP": "COSMIC"},
            {"XDG_CURRENT_DESKTOP": "CoSmIc:GNOME"},
            {"XDG_SESSION_DESKTOP": "cosmic"},
        ):
            with self.subTest(env=env), patch.dict(compositor.os.environ, env, clear=True):
                self.assertEqual(compositor.detect_compositor(), "cosmic")

    def test_create_routes_exact_monitorize_name(self):
        with patch.dict(compositor.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-2": {"status": "connected"}}), \
             patch.object(cosmic_output, "set_enabled", return_value=True) as change:
            ok, details, _ = compositor.activate_in_compositor(
                "cosmic", None, 2340, 1080, 60, expected_output_name="Virtual-2")
        self.assertTrue(ok)
        self.assertEqual(details["name"], "Virtual-2")
        change.assert_called_once_with("Virtual-2", True)

    def test_create_rejects_wrong_drm_connector_id(self):
        with patch.dict(compositor.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-2": {"status": "connected", "connector_id": 23}}), \
             patch.object(cosmic_output, "set_enabled") as change:
            with self.assertRaisesRegex(CompositorError, "identify"):
                compositor.activate_in_compositor(
                    "cosmic", None, 2340, 1080, 60,
                    expected_output_name="Virtual-2", expected_connector_id=24)
        change.assert_not_called()

    def test_activation_waits_for_confirmed_state(self):
        with patch.object(cosmic_output.shutil, "which", return_value="/usr/bin/cosmic-randr"), \
             patch.object(cosmic_output.subprocess, "run", side_effect=[
                 result(state(False)), result(), result(state(False)), result(state(True))]) as run, \
             patch.object(cosmic_output.time, "sleep"):
            self.assertTrue(cosmic_output.set_enabled("Virtual-2", True))
        self.assertEqual(run.call_args_list[1].args[0],
                         ["/usr/bin/cosmic-randr", "enable", "Virtual-2"])
        self.assertEqual(run.call_count, 4)

    def test_rejection_fails(self):
        with patch.object(cosmic_output.shutil, "which", return_value="/usr/bin/cosmic-randr"), \
             patch.object(cosmic_output.subprocess, "run", side_effect=[
                 result(state(False)), result(returncode=1, stderr="configuration failed")]):
            with self.assertRaisesRegex(CompositorError, "rejected"):
                cosmic_output.set_enabled("Virtual-2", True)

    def test_unconfirmed_activation_times_out(self):
        with patch.object(cosmic_output.shutil, "which", return_value="/usr/bin/cosmic-randr"), \
             patch.object(cosmic_output.subprocess, "run", side_effect=[
                 result(state(False)), result(), result(state(False))]), \
             patch.object(cosmic_output.time, "monotonic", side_effect=[0, 0, 0, 0, 6]):
            with self.assertRaisesRegex(CompositorError, "Timed out confirming"):
                cosmic_output.set_enabled("Virtual-2", True)

    def test_removal_targets_only_monitorize_output(self):
        with patch.dict(compositor.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-2": {"status": "connected"}}), \
             patch.object(cosmic_output, "set_enabled", return_value=True) as change:
            self.assertTrue(compositor.deactivate_in_compositor("cosmic", None))
        change.assert_called_once_with("Virtual-2", False)

    def test_ambiguous_removal_refused(self):
        with patch.dict(compositor.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-1": {"status": "connected"},
                 "Virtual-2": {"status": "connected"}}), \
             patch.object(cosmic_output, "set_enabled") as change:
            with self.assertRaisesRegex(CompositorError, "ambiguous"):
                compositor.deactivate_in_compositor("cosmic", None)
        change.assert_not_called()

    def test_disable_requires_fresh_confirmation(self):
        with patch.object(cosmic_output.shutil, "which", return_value="/usr/bin/cosmic-randr"), \
             patch.object(cosmic_output.subprocess, "run", side_effect=[
                 result(state()), result(), result(state(False))]) as run:
            self.assertTrue(cosmic_output.set_enabled("Virtual-2", False))
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["disable", "Virtual-2"])

    def test_disable_preserves_last_active_output(self):
        with patch.object(cosmic_output.shutil, "which", return_value="/usr/bin/cosmic-randr"), \
             patch.object(cosmic_output.subprocess, "run", return_value=result(state(True, False))) as run:
            with self.assertRaisesRegex(CompositorError, "last active"):
                cosmic_output.set_enabled("Virtual-2", False)
        self.assertEqual(run.call_count, 1)

    def test_removal_failure_preserves_kernel_connector(self):
        with patch.object(cli, "get_compositor_snapshot", return_value=("cosmic", None)), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-2": {"status": "connected"}}), \
             patch.object(cosmic_output, "set_enabled", side_effect=CompositorError("not confirmed")), \
             patch.object(cli, "helper_response") as helper, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["remove", "--json"]), 1)
        helper.assert_not_called()

    def test_duplicate_or_missing_head_fails_closed(self):
        with self.assertRaisesRegex(CompositorError, "duplicate"):
            cosmic_output._heads(state() + state())
        with patch.object(cosmic_output.shutil, "which", return_value="/usr/bin/cosmic-randr"), \
             patch.object(cosmic_output.subprocess, "run", return_value=result(state().replace("Virtual-2", "Virtual-1"))):
            with self.assertRaisesRegex(CompositorError, "not exposed"):
                cosmic_output.set_enabled("Virtual-2", False)


if __name__ == "__main__":
    unittest.main()
