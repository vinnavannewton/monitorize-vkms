"""Cinnamon Wayland uses Muffin's DisplayConfig for VKMS lifecycle."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitorize_vkms import compositor as comp


def state(active=True):
    real = ("eDP-1", "AUO", "Panel", "real")
    virtual = ("Virtual-1", "MON", "Virtual-1", "new")
    physical = [
        (real, [("real-mode", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": True})], {}),
        (virtual, [("vkms-mode", 2340, 1080, 60.0, 1.0, [1.0], {"is-current": True})], {}),
    ]
    logical = [(0, 0, 1.0, 0, True, [real], {})]
    if active:
        logical.append((1920, 0, 1.0, 0, False, [virtual], {}))
    return (1, physical, logical, {"layout-mode": 2})


class CinnamonWaylandTest(unittest.TestCase):
    def test_snapshot_uses_muffin(self):
        interface = Mock()
        interface.GetCurrentState.return_value = state(False)
        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}),
            patch.object(comp, "detect_compositor", return_value="cinnamon"),
            patch.object(comp, "_muffin_display_config_interface", return_value=interface),
        ):
            desktop, identities = comp.get_compositor_snapshot()
        self.assertEqual(desktop, "cinnamon")
        self.assertIn("Virtual-1", identities)

    def test_autoactivated_vkms_succeeds_without_reapplying_layout(self):
        interface = Mock()
        interface.GetCurrentState.return_value = state()
        with (
            patch.object(comp, "_require_dbus", return_value=SimpleNamespace()),
            patch.object(comp, "_wait_for_new_vkms_connector", return_value=("Virtual-1", state(), "")),
        ):
            ok, details, message = comp.gnome_activate_vkms(
                {}, 2340, 1080, 60,
                display_config=interface,
                compositor_name="Muffin",
                expected_output_name="Virtual-1",
            )
        self.assertTrue(ok, message)
        self.assertEqual(details["name"], "Virtual-1")
        interface.ApplyMonitorsConfig.assert_not_called()

    def test_inactive_vkms_uses_muffin_apply_without_gnome_layout_property(self):
        interface = Mock()
        interface.GetCurrentState.return_value = state()
        with (
            patch.object(comp, "_require_dbus", return_value=SimpleNamespace()),
            patch.object(comp, "_wait_for_new_vkms_connector", return_value=("Virtual-1", state(False), "")),
        ):
            ok, _, message = comp.gnome_activate_vkms(
                {}, 2340, 1080, 60,
                display_config=interface,
                compositor_name="Muffin",
                expected_output_name="Virtual-1",
                preserve_global_properties=False,
            )
        self.assertTrue(ok, message)
        self.assertEqual(interface.ApplyMonitorsConfig.call_args.args[3], {})

    def test_remove_uses_connected_drm_target_and_preserves_laptop_display(self):
        interface = Mock()
        interface.GetCurrentState.side_effect = [state(), state(False)]
        with (
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"}),
            patch.object(comp, "_require_dbus", return_value=SimpleNamespace()),
            patch.object(comp, "_muffin_display_config_interface", return_value=interface),
            patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={
                "Virtual-1": {"status": "connected"}
            }),
        ):
            self.assertTrue(comp.deactivate_in_compositor("cinnamon", {"Virtual-1": ("old",)}))
        payload = interface.ApplyMonitorsConfig.call_args.args[2]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0][5][0][0], "eDP-1")
        self.assertEqual(interface.ApplyMonitorsConfig.call_args.args[3], {})


if __name__ == "__main__":
    unittest.main()
