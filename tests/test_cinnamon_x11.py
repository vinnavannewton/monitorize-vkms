"""Cinnamon/X11 compositor detection and XRandR activation coverage."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from monitorize_vkms import cli, compositor
from monitorize_vkms.errors import CompositorError


def output_state(
    name, *, active, connector_id, primary=False, virtual=False
):
    return {
        "name": name,
        "connected": True,
        "primary": primary,
        "active": active,
        "width": 2340 if virtual and active else (1920 if active else None),
        "height": 1080 if active else None,
        "x": 1920 if virtual and active else (0 if active else None),
        "y": 0 if active else None,
        "connector_id": connector_id,
        "modes": (
            [{
                "name": "2340x1080",
                "width": 2340,
                "height": 1080,
                "rates": [{"rate": 60.0, "current": active}],
            }]
            if virtual
            else []
        ),
    }


class CinnamonX11Test(unittest.TestCase):
    def test_cinnamon_wins_over_deprecated_gnome_marker(self):
        environment = {
            "XDG_CURRENT_DESKTOP": "X-Cinnamon",
            "XDG_SESSION_DESKTOP": "cinnamon",
            "XDG_SESSION_TYPE": "x11",
            "GNOME_DESKTOP_SESSION_ID": "this-is-deprecated",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(compositor.detect_compositor(), "cinnamon")

    def test_deprecated_gnome_marker_is_not_a_gnome_session(self):
        with patch.dict(
            os.environ,
            {"GNOME_DESKTOP_SESSION_ID": "this-is-deprecated"},
            clear=True,
        ):
            self.assertIsNone(compositor.detect_compositor())

    def test_xrandr_parser_keeps_connection_geometry_and_rates(self):
        parsed = compositor._parse_xrandr_outputs(
            "eDP-1 connected primary 1920x1080+0+0 (normal left)\n"
            "\tCONNECTOR_ID: 39\n"
            "   1920x1080     60.01*+  59.93\n"
            "Virtual-1-2 connected (normal left)\n"
            "\tCONNECTOR_ID: 41\n"
            "   2340x1080     60.00+\n"
        )
        self.assertTrue(parsed[0]["active"])
        self.assertTrue(parsed[0]["primary"])
        self.assertFalse(parsed[1]["active"])
        self.assertEqual(parsed[1]["connector_id"], 41)
        self.assertEqual(parsed[1]["modes"][0]["rates"][0]["rate"], 60.0)

    def test_activation_maps_drm_connector_id_to_xrandr_output(self):
        primary = output_state(
            "Virtual-1", active=True, connector_id=39, primary=True
        )
        virtual = output_state(
            "Virtual-1-2", active=False, connector_id=41, virtual=True
        )
        active_virtual = output_state(
            "Virtual-1-2", active=True, connector_id=41, virtual=True
        )
        completed = subprocess.CompletedProcess([], 0, "", "")

        with (
            patch.object(compositor.shutil, "which", return_value="/usr/bin/xrandr"),
            patch.object(
                compositor,
                "xrandr_outputs",
                side_effect=[[primary, virtual], [primary, active_virtual]],
            ),
            patch.object(compositor.subprocess, "run", return_value=completed) as run,
        ):
            ok, details, message = compositor.xrandr_activate_vkms(
                [primary, virtual],
                2340,
                1080,
                60.0,
                "Virtual-2",
                expected_connector_id=41,
            )

        self.assertTrue(ok, message)
        self.assertEqual(details["name"], "Virtual-1-2")
        run.assert_called_once_with(
            [
                "/usr/bin/xrandr",
                "--output",
                "Virtual-1-2",
                "--mode",
                "2340x1080",
                "--rate",
                "60",
                "--right-of",
                "Virtual-1",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def test_cinnamon_x11_dispatches_to_xrandr(self):
        with (
            patch.dict(
                os.environ,
                {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
                clear=True,
            ),
            patch.object(
                compositor,
                "xrandr_activate_vkms",
                return_value=(True, {"name": "Virtual-2"}, "ready"),
            ) as activate,
        ):
            result = compositor.activate_in_compositor(
                "cinnamon", [], 2340, 1080, 60.0, "Virtual-2", 41
            )

        self.assertTrue(result[0])
        activate.assert_called_once_with([], 2340, 1080, 60.0, "Virtual-2", 41)

    def test_removal_maps_connector_id_before_kernel_disconnect(self):
        primary = output_state(
            "Virtual-1", active=True, connector_id=39, primary=True
        )
        virtual = output_state(
            "Virtual-1-2", active=True, connector_id=41, virtual=True
        )
        disabled = output_state(
            "Virtual-1-2", active=False, connector_id=41, virtual=True
        )
        completed = subprocess.CompletedProcess([], 0, "", "")

        with (
            patch.object(compositor.shutil, "which", return_value="/usr/bin/xrandr"),
            patch.object(
                compositor,
                "xrandr_outputs",
                side_effect=[[primary, virtual], [primary, disabled]],
            ),
            patch.object(compositor.subprocess, "run", return_value=completed) as run,
        ):
            self.assertTrue(
                compositor.xrandr_deactivate_vkms({"Virtual-2": 41})
            )

        run.assert_called_once_with(
            ["/usr/bin/xrandr", "--output", "Virtual-1-2", "--off"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def test_activation_rejects_ambiguous_connector_id(self):
        first = output_state(
            "Virtual-1-2", active=False, connector_id=41, virtual=True
        )
        second = output_state(
            "Virtual-3-2", active=False, connector_id=41, virtual=True
        )

        with (
            patch.object(compositor.shutil, "which", return_value="/usr/bin/xrandr"),
            patch.object(compositor, "xrandr_outputs", return_value=[first, second]),
        ):
            ok, _details, message = compositor.xrandr_activate_vkms(
                [], 2340, 1080, 60.0, "Virtual-2", expected_connector_id=41
            )

        self.assertFalse(ok)
        self.assertIn("Multiple XRandR outputs", message)

    def test_missing_mutter_service_becomes_compositor_error(self):
        bus = Mock()
        bus.get_object.side_effect = RuntimeError("service has no owner")
        dbus = Mock()
        with self.assertRaisesRegex(CompositorError, "DisplayConfig is unavailable"):
            compositor._display_config_interface(bus=bus, dbus=dbus)

    def test_snapshot_failure_is_reported_without_creating_connector(self):
        args = SimpleNamespace(mode="2340x1080@60", json=False, foreground=False)
        with (
            patch.object(
                cli,
                "get_compositor_snapshot",
                side_effect=CompositorError("xrandr unavailable"),
            ),
            patch.object(cli, "helper_response") as helper,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(cli.cmd_create(args), 1)
        helper.assert_not_called()
        self.assertIn("xrandr unavailable", error.getvalue())


if __name__ == "__main__":
    unittest.main()
