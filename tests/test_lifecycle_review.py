"""Behavioral regressions for pre-commit lifecycle review findings."""

from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from monitorize_vkms import compositor as comp, helper


def output(name="Virtual-1", identifier=7):
    return {"id": identifier, "name": name, "connected": True, "enabled": True,
            "currentModeId": "42", "modes": [
                {"id": "42", "refreshRate": 60, "size": {"width": 1920, "height": 1080}}]}


class LifecycleReviewTest(unittest.TestCase):
    def test_stale_foreground_generation_never_runs_compositor_or_disconnect(self):
        with patch.object(helper, "_require_polkit_root"), \
             patch.object(helper, "_configfs_root", return_value=Path("/fake")), \
             patch.object(helper, "_require_bootstrap", return_value=(Path("/fake/instance"), Path("/fake/connector"))), \
             patch.object(helper, "_acquire_lifecycle_lock", return_value=999), \
             patch.object(helper.os, "close"), \
             patch.dict(helper.os.environ, {"PKEXEC_UID": "1000"}), \
             patch.object(helper, "_active_session", return_value=("3", {})), \
             patch.object(helper, "_read", return_value="1"), \
             patch.object(helper, "_read_lease", return_value={"uid": 1000, "session": "3", "generation": "new"}), \
             patch.object(helper.sys, "argv", ["helper", "destroy", "--generation", "old"]), \
             patch.object(helper, "_run_worker") as worker, \
             patch.object(helper, "_disconnect_connector") as disconnect:
            with self.assertRaisesRegex(helper.VkmsHelperError, "no longer owns"):
                helper.main()
        worker.assert_not_called()
        disconnect.assert_not_called()

    def test_failed_compositor_removal_never_disconnects_or_discards_lease(self):
        lease = {"uid": 1000, "session": "3", "generation": "current", "desktop": "kde",
                 "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                 "connector": "Virtual-1", "connector_id": 7, "card": "card2", "edid_sha256": "digest"}
        with patch.object(helper, "_require_polkit_root"), \
             patch.object(helper, "_configfs_root", return_value=Path("/fake")), \
             patch.object(helper, "_require_bootstrap", return_value=(Path("/fake/instance"), Path("/fake/connector"))), \
             patch.object(helper, "_acquire_lifecycle_lock", return_value=999), \
             patch.object(helper.os, "close"), \
             patch.dict(helper.os.environ, {"PKEXEC_UID": "1000"}), \
             patch.object(helper, "_active_session", return_value=("3", {})), \
             patch.object(helper, "_read", return_value="1"), \
             patch.object(helper, "_read_lease", return_value=lease), \
             patch.object(helper.sys, "argv", ["helper", "destroy"]), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                 "Virtual-1": {"name": "Virtual-1", "status": "connected", "connector_id": 7, "card": "card2",
                               "display": "mon1", "edid_sha256": "digest"}}), \
             patch.object(helper, "_run_worker", side_effect=helper.VkmsHelperError("not confirmed")), \
             patch.object(helper, "_disconnect_connector") as disconnect, \
             patch.object(helper, "_remove_lease") as remove:
            with self.assertRaisesRegex(helper.VkmsHelperError, "not confirmed"):
                helper.main()
        disconnect.assert_not_called()
        remove.assert_not_called()

    def test_kde_physical_hotplug_does_not_steal_requested_target(self):
        target = output()
        physical = output("HDMI-A-2", 99)
        with patch.object(comp.shutil, "which", return_value="/usr/bin/kscreen-doctor"), \
             patch.object(comp, "kde_outputs", side_effect=[[physical, target], [physical, target]]), \
             patch.object(comp.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            ok, details, _message = comp.kde_activate_vkms(
                set(), 1920, 1080, 60, expected_output_name="Virtual-1")
        self.assertTrue(ok)
        self.assertEqual(details["name"], "Virtual-1")
        self.assertEqual(run.call_args.args[0], ["kscreen-doctor", "output.Virtual-1.enable", "output.Virtual-1.mode.42"])

    def test_kde_duplicate_target_never_applies_configuration(self):
        with patch.object(comp.shutil, "which", return_value="/usr/bin/kscreen-doctor"), \
             patch.object(comp, "kde_outputs", return_value=[output(), output(identifier=8)]), \
             patch.object(comp.subprocess, "run") as run:
            ok, _details, _message = comp.kde_activate_vkms(
                set(), 1920, 1080, 60, expected_output_name="Virtual-1")
        self.assertFalse(ok)
        run.assert_not_called()

    def test_kde_disconnected_enabled_head_does_not_count_as_fallback_display(self):
        other = {**output("HDMI-A-1", 9), "connected": False}
        with patch.object(comp.shutil, "which", return_value="/usr/bin/kscreen-doctor"), \
             patch.object(comp, "kde_outputs", return_value=[output(), other]), \
             patch.object(comp.subprocess, "run") as run:
            with self.assertRaisesRegex(comp.CompositorError, "last active KWin"):
                comp.kde_deactivate_vkms("Virtual-1")
        run.assert_not_called()

    def test_gnome_malformed_state_never_confirms_deactivation(self):
        interface = Mock()
        interface.GetCurrentState.return_value = (1, [], [()], {})
        with patch.object(comp, "_require_dbus", return_value=SimpleNamespace()):
            self.assertFalse(comp.gnome_deactivate_vkms(
                {}, display_config=interface, target_connectors=["Virtual-1"]))
        interface.ApplyMonitorsConfig.assert_not_called()

    def test_gnome_missing_target_never_confirms_deactivation(self):
        interface = Mock()
        interface.GetCurrentState.return_value = (1, [], [], {})
        with patch.object(comp, "_require_dbus", return_value=SimpleNamespace()):
            self.assertFalse(comp.gnome_deactivate_vkms(
                {}, display_config=interface, target_connectors=["Virtual-1"]))

    def test_logind_known_wayland_desktop_can_use_dbus_without_leader_socket_env(self):
        properties = "Active=yes\nRemote=no\nType=wayland\nDesktop=KDE\nLeader=0\nUser=1000\n"
        with patch.object(helper.shutil, "which", return_value="/usr/bin/loginctl"), \
             patch.object(helper.subprocess, "run", side_effect=[
                 SimpleNamespace(stdout="3"), SimpleNamespace(stdout=properties)]), \
             patch.object(helper.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir="/home/test", pw_name="test")):
            session, env = helper._active_session(1000)
        self.assertEqual(session, "3")
        self.assertEqual(env["XDG_SESSION_TYPE"], "wayland")
        self.assertEqual(env["XDG_CURRENT_DESKTOP"], "KDE")

    def test_unknown_graphical_session_without_socket_is_not_headless(self):
        with self.assertRaises(helper.VkmsHelperError):
            helper._validate_compositor_support(None, {"XDG_SESSION_TYPE": "wayland"})

    def test_unvalidated_cinnamon_wayland_fails_support_gate(self):
        with self.assertRaisesRegex(helper.VkmsHelperError, "only in an X11"):
            helper._validate_compositor_support("cinnamon", {"XDG_SESSION_TYPE": "wayland"})

    def test_direct_graphical_create_without_mode_never_connects(self):
        with patch.object(helper, "_require_polkit_root"), \
             patch.object(helper, "_configfs_root", return_value=Path("/fake")), \
             patch.object(helper, "_require_bootstrap", return_value=(Path("/fake/instance"), Path("/fake/connector"))), \
             patch.object(helper, "_acquire_lifecycle_lock", return_value=999), \
             patch.object(helper.os, "close"), \
             patch.dict(helper.os.environ, {"PKEXEC_UID": "1000"}), \
             patch.object(helper, "_active_session", return_value=("3", {"XDG_SESSION_TYPE": "wayland"})), \
             patch.object(helper, "_read", return_value="2"), \
             patch.object(helper, "_read_lease", return_value=None), \
             patch.object(helper.sys, "argv", ["helper", "create"]), \
             patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={}), \
             patch.object(helper, "_run_worker", return_value={"desktop": "kde", "state": []}), \
             patch.object(helper, "_write_lease") as lease, \
             patch.object(helper, "_enable_connector") as connect:
            with self.assertRaisesRegex(helper.VkmsHelperError, "requires a requested mode"):
                helper.main()
        connect.assert_not_called()
        lease.assert_not_called()

    def test_headless_guard_rejects_other_users_graphical_session(self):
        with patch.object(helper.subprocess, "run", side_effect=[
            SimpleNamespace(stdout="2 other-user\n"),
            SimpleNamespace(stdout="Type=wayland\nRemote=no\n")
        ]):
            with self.assertRaisesRegex(helper.VkmsHelperError, "all local graphical sessions"):
                helper._require_no_graphical_sessions("/usr/bin/loginctl")

    def test_nonfinite_refresh_and_fractional_dimensions_are_refused(self):
        from monitorize_vkms.edid import generate_edid
        edid = generate_edid(1920, 1080, 60)
        for mode in ([1920, 1080, float("nan")], [1920.5, 1080, 60], [1920, 1080, float("inf")]):
            with self.subTest(mode=mode), self.assertRaises(helper.VkmsHelperError):
                helper._validate_edid_mode(edid, mode)

    def test_worker_timeout_stops_its_entire_command_group(self):
        worker = Mock(pid=4321)
        worker.communicate.side_effect = [subprocess.TimeoutExpired("worker", 1), ("", "")]
        process = Mock()
        process.__enter__ = Mock(return_value=worker)
        process.__exit__ = Mock(return_value=False)
        with patch.object(helper.shutil, "which", return_value="/usr/bin/setpriv"), \
             patch.object(helper.pwd, "getpwuid", return_value=SimpleNamespace(pw_gid=1000)), \
             patch.object(helper.subprocess, "Popen", return_value=process) as launch, \
             patch.object(helper.os, "killpg") as kill:
            with self.assertRaisesRegex(helper.VkmsHelperError, "command group was stopped"):
                helper._run_worker(1000, {}, {"action": "snapshot"}, timeout=1)
        kill.assert_called_once_with(4321, signal.SIGKILL)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
