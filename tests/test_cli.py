"""Tests for monitorize_vkms.cli argument handling and commands using standard unittest."""

from __future__ import annotations

import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from monitorize_vkms.cli import cmd_create, main
from monitorize_vkms.compositor import kde_activate_vkms


class TestCli(unittest.TestCase):
    def test_cli_help(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            with self.assertRaises(SystemExit) as ctx:
                main(["--help"])
            self.assertEqual(ctx.exception.code, 0)
        self.assertIn("Manage standalone Monitorize VKMS virtual displays", f.getvalue())

    def test_cli_version(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
            self.assertEqual(ctx.exception.code, 0)
        self.assertIn("monitorize-vkms", f.getvalue())

    def test_cli_no_args(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main([])
        self.assertEqual(ret, 0)
        self.assertIn("usage: monitorize-vkms", f.getvalue())

    def test_cli_status_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["status", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(f.getvalue())
        self.assertTrue(data["success"])
        self.assertIn("version", data)
        self.assertIn("kernel_module", data)
        self.assertIn("topology", data)
        self.assertIn("drm", data)

    def test_cli_list_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["list", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(f.getvalue())
        self.assertTrue(data["success"])
        self.assertIn("card", data)
        self.assertIn("connectors", data)
        self.assertIsInstance(data["connectors"], list)

    def test_cli_doctor_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["doctor", "--json"])
        self.assertIn(ret, (0, 1))
        data = json.loads(f.getvalue())
        self.assertIn("all_passed", data)
        self.assertIn("checks", data)
        self.assertGreaterEqual(len(data["checks"]), 8)

    def test_cli_create_invalid_mode_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["create", "not-a-mode", "--json"])
        self.assertEqual(ret, 1)
        data = json.loads(f.getvalue())
        self.assertFalse(data["success"])
        self.assertEqual(data["error_type"], "invalid_mode")

    def test_kde_activation_reuses_a_reenabled_persistent_output(self):
        output = {
            "id": 7,
            "name": "Virtual-1",
            "connected": True,
            "enabled": True,
            "currentModeId": "42",
            "modes": [{"id": "42", "refreshRate": 60, "size": {"width": 2340, "height": 1080}}],
        }
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("monitorize_vkms.compositor.shutil.which", return_value="/usr/bin/kscreen-doctor"),
            patch("monitorize_vkms.compositor.kde_outputs", side_effect=[[output], [output]]),
            patch("monitorize_vkms.compositor.subprocess.run", return_value=completed) as run,
        ):
            ok, details, _message = kde_activate_vkms(
                {"Virtual-1"},
                2340,
                1080,
                60,
                expected_output_name="Virtual-1",
            )

        self.assertTrue(ok)
        self.assertEqual(details["name"], "Virtual-1")
        run.assert_called_once_with(
            ["kscreen-doctor", "output.Virtual-1.enable", "output.Virtual-1.mode.42"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def test_kde_activation_refuses_missing_requested_refresh_mode(self):
        output = {
            "id": 7,
            "name": "Virtual-1",
            "connected": True,
            "enabled": False,
            "modes": [{"id": "42", "refreshRate": 75, "size": {"width": 1920, "height": 1080}}],
        }
        with (
            patch("monitorize_vkms.compositor.shutil.which", return_value="/usr/bin/kscreen-doctor"),
            patch("monitorize_vkms.compositor.kde_outputs", return_value=[output]),
            patch("monitorize_vkms.compositor.subprocess.run") as run,
        ):
            ok, _details, message = kde_activate_vkms(
                set(), 1920, 1080, 60, expected_output_name="Virtual-1"
            )
        self.assertFalse(ok)
        self.assertIn("refusing to enable it at another mode", message)
        run.assert_not_called()

    def test_foreground_exit_destroys_display_once(self):
        args = SimpleNamespace(mode="1920x1080@60", json=True, foreground=True)
        helper_result = {"success": True, "created": True, "generation": "gen-1"}

        with (
            patch("monitorize_vkms.cli.helper_response", return_value=helper_result) as helper,
            patch("monitorize_vkms.cli.signal.signal"),
            patch("monitorize_vkms.cli.select.select", return_value=([object()], [], [])),
            patch("monitorize_vkms.cli.sys.stdin", io.StringIO("")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = cmd_create(args)

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[0] for call in helper.call_args_list],
            ["create", "destroy"],
        )
        self.assertEqual(helper.call_args_list[-1].kwargs, {"generation": "gen-1", "slot": "mon1"})


if __name__ == "__main__":
    unittest.main()
