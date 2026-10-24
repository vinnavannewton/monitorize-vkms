"""Desktop connection recovery remains scoped to the verified logind session."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import struct

from monitorize_vkms import helper


class SessionDiscoveryTests(unittest.TestCase):
    def test_missing_desktop_recovers_gnome_from_session_process(self):
        properties = "Active=yes\nRemote=no\nType=wayland\nDesktop=\nLeader=0\nUser=1000\n"
        raw = b"XDG_CURRENT_DESKTOP=GNOME\0XDG_SESSION_ID=3\0"
        with patch.object(helper.shutil, "which", return_value="/usr/bin/loginctl"), \
             patch.object(helper.subprocess, "run", side_effect=[
                 SimpleNamespace(stdout="3"), SimpleNamespace(stdout=properties)]), \
             patch.object(helper.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir="/home/test", pw_name="test")), \
             patch.object(Path, "iterdir", return_value=iter([Path("/proc/10")])), \
             patch.object(Path, "read_bytes", side_effect=[b"", raw]), \
             patch.object(helper, "_process_in_session", return_value=True), \
             patch.object(helper, "_session_connection_environment") as resolve, \
             patch.object(helper, "_validate_session_socket") as validate:
            session, env = helper._active_session(1000)
        self.assertEqual(session, "3")
        self.assertEqual(env["XDG_CURRENT_DESKTOP"], "GNOME")
        resolve.assert_not_called()
        validate.assert_not_called()

    def test_desktop_recovery_ignores_processes_outside_session(self):
        with patch.object(Path, "iterdir", return_value=iter([Path("/proc/10")])), \
             patch.object(helper, "_process_in_session", return_value=False), \
             patch.object(Path, "read_bytes") as read:
            self.assertEqual(helper._session_desktop(1000, "3"), "")
        read.assert_not_called()

    def test_desktop_recovery_rejects_conflicting_metadata(self):
        with patch.object(Path, "iterdir", return_value=iter([Path("/proc/10"), Path("/proc/11")])), \
             patch.object(helper, "_process_in_session", return_value=True), \
             patch.object(Path, "read_bytes", side_effect=[
                 b"XDG_CURRENT_DESKTOP=GNOME\0", b"XDG_CURRENT_DESKTOP=KDE\0"]):
            with self.assertRaisesRegex(helper.VkmsHelperError, "desktop metadata"):
                helper._session_desktop(1000, "3")

    def test_session_process_membership_checks_owner_and_exact_session(self):
        with TemporaryDirectory() as directory:
            process = Path(directory)
            # Use mocks for /proc files, including their owner, without depending
            # on the test runner's actual login session.
            with patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=1000)), \
                 patch.object(Path, "read_text", return_value="0::/user.slice/session-20.scope"), \
                 patch.object(Path, "read_bytes", return_value=b"XDG_SESSION_ID=3\0"):
                self.assertTrue(helper._process_in_session(process, 1000, "3"))
                self.assertTrue(helper._process_in_session(process, 1000, "20"))
                self.assertFalse(helper._process_in_session(process, 1000, "2"))
                self.assertFalse(helper._process_in_session(process, 1001, "20"))

    def test_missing_leader_environment_recovers_niri(self):
        properties = "Active=yes\nRemote=no\nType=wayland\nDesktop=niri\nLeader=0\nUser=1000\n"
        recovered = {"WAYLAND_DISPLAY": "wayland-1", "NIRI_SOCKET": "/run/user/1000/niri.sock"}
        with patch.object(helper.shutil, "which", return_value="/usr/bin/loginctl"), \
             patch.object(helper.subprocess, "run", side_effect=[
                 SimpleNamespace(stdout="3"), SimpleNamespace(stdout=properties)]), \
             patch.object(helper.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir="/home/test", pw_name="test")), \
             patch.object(helper, "_validate_session_socket"), \
             patch.object(helper, "_session_connection_environment", return_value=recovered) as resolve:
            session, env = helper._active_session(1000)
        resolve.assert_called_once_with(1000, "3")
        self.assertEqual(session, "3")
        self.assertEqual(env["NIRI_SOCKET"], recovered["NIRI_SOCKET"])

    def test_recovery_rejects_remote_sessions_before_scanning(self):
        properties = "Active=yes\nRemote=yes\nType=wayland\nDesktop=niri\nLeader=0\nUser=1000\n"
        with patch.object(helper.shutil, "which", return_value="/usr/bin/loginctl"), \
             patch.object(helper.subprocess, "run", side_effect=[
                 SimpleNamespace(stdout="3"), SimpleNamespace(stdout=properties)]), \
             patch.object(helper, "_session_connection_environment") as resolve:
            with self.assertRaisesRegex(helper.VkmsHelperError, "exactly one"):
                helper._active_session(1000)
        resolve.assert_not_called()

    def test_candidate_recovery_rejects_ambiguity_and_prefers_complete_pair(self):
        paths = [Path("/proc/10"), Path("/proc/11")]
        for raw, expected in (
            ([b"WAYLAND_DISPLAY=wayland-1\0", b"WAYLAND_DISPLAY=wayland-1\0NIRI_SOCKET=/run/user/1000/niri.sock\0"],
             {"WAYLAND_DISPLAY": "wayland-1", "NIRI_SOCKET": "/run/user/1000/niri.sock", "XDG_CURRENT_DESKTOP": "niri"}),
            ([b"WAYLAND_DISPLAY=wayland-1\0", b"WAYLAND_DISPLAY=wayland-2\0"], None),
            ([b"WAYLAND_DISPLAY=wayland-1\0NIRI_SOCKET=/run/user/1000/a.sock\0",
              b"WAYLAND_DISPLAY=wayland-1\0NIRI_SOCKET=/run/user/1000/b.sock\0"], None),
        ):
            with self.subTest(raw=raw), patch.object(Path, "iterdir", return_value=iter(paths)), \
                 patch.object(helper, "_process_in_session", return_value=True), \
                 patch.object(Path, "read_bytes", side_effect=raw), \
                 patch.object(helper, "_validate_session_socket"):
                if expected is None:
                    with self.assertRaisesRegex(helper.VkmsHelperError, "uniquely"):
                        helper._session_connection_environment(1000, "3")
                else:
                    self.assertEqual(helper._session_connection_environment(1000, "3"), expected)

    def test_socket_outside_runtime_or_symlink_is_rejected(self):
        with self.assertRaisesRegex(helper.VkmsHelperError, "runtime"):
            helper._validate_session_socket(Path("/tmp/niri.sock"), 1000, "3")
        with patch.object(Path, "lstat", side_effect=[
                SimpleNamespace(st_mode=0o40700, st_uid=1000),
                SimpleNamespace(st_mode=0o120777, st_uid=1000)]):
            with self.assertRaisesRegex(helper.VkmsHelperError, "unsafe"):
                helper._validate_session_socket(Path("/run/user/1000/niri.sock"), 1000, "3")

    def test_relative_and_absolute_wayland_names_identify_one_socket(self):
        for niri in ("", "/run/user/1000/niri.sock"):
            raw = [f"WAYLAND_DISPLAY={display}\0NIRI_SOCKET={niri}\0".encode()
                   for display in ("wayland-1", "/run/user/1000/wayland-1")]
            with self.subTest(niri=niri), \
                 patch.object(Path, "iterdir", return_value=iter([Path("/proc/10"), Path("/proc/11")])), \
                 patch.object(helper, "_process_in_session", return_value=True), \
                 patch.object(Path, "read_bytes", side_effect=raw), \
                 patch.object(helper, "_validate_session_socket", return_value=1041) as validate:
                expected = {"WAYLAND_DISPLAY": "wayland-1"}
                if niri:
                    expected.update({"NIRI_SOCKET": niri, "XDG_CURRENT_DESKTOP": "niri"})
                self.assertEqual(helper._session_connection_environment(1000, "3"), expected)
                wayland_calls = [call.args[0] for call in validate.call_args_list
                                 if call.args[0].name == "wayland-1"]
                self.assertEqual(wayland_calls, [Path("/run/user/1000/wayland-1")] * 2)

    def test_satellite_socket_is_not_the_niri_wayland_socket(self):
        raw = [b"WAYLAND_DISPLAY=wayland-1\0NIRI_SOCKET=/run/user/1000/niri.sock\0",
               b"WAYLAND_DISPLAY=xwls-1\0NIRI_SOCKET=/run/user/1000/niri.sock\0"]
        with patch.object(Path, "iterdir", return_value=iter([Path("/proc/10"), Path("/proc/11")])), \
             patch.object(helper, "_process_in_session", return_value=True), \
             patch.object(Path, "read_bytes", side_effect=raw), \
             patch.object(helper, "_validate_session_socket", side_effect=lambda path, uid, session:
                          7444 if path.name == "xwls-1" else 1041):
            self.assertEqual(helper._session_connection_environment(1000, "3"), {
                "WAYLAND_DISPLAY": "wayland-1", "NIRI_SOCKET": "/run/user/1000/niri.sock",
                "XDG_CURRENT_DESKTOP": "niri"})

    def test_socket_peer_must_belong_to_same_user_and_session(self):
        for peer_uid, same_session in ((1001, True), (1000, False), (1000, True)):
            with self.subTest(peer_uid=peer_uid, same_session=same_session), \
                 patch.object(Path, "lstat", side_effect=[
                     SimpleNamespace(st_mode=0o40700, st_uid=1000),
                     SimpleNamespace(st_mode=0o140600, st_uid=1000)]), \
                 patch.object(helper.socket, "socket") as socket_factory, \
                 patch.object(helper, "_process_in_session", return_value=same_session):
                connection = socket_factory.return_value.__enter__.return_value
                connection.getsockopt.return_value = struct.pack("3i", 10, peer_uid, 1000)
                if peer_uid == 1000 and same_session:
                    helper._validate_session_socket(Path("/run/user/1000/niri.sock"), 1000, "3")
                else:
                    with self.assertRaisesRegex(helper.VkmsHelperError, "selected login session"):
                        helper._validate_session_socket(Path("/run/user/1000/niri.sock"), 1000, "3")
