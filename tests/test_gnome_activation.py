"""Regression coverage for Mutter enabling a connector during hotplug."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitorize_vkms import compositor as comp


def state(active=True, wrong_mode=False, mirrored=False, serial=1):
    real = ('DP-1', 'Vendor', 'Panel', 'real')
    virtual = ('Virtual-1', 'Monitorize', 'Virtual', 'new')
    modes = [('requested', 2340, 1080, 60.0, 1.0, [1.0, 2.0], {'is-current': not wrong_mode})]
    if wrong_mode:
        modes.append(('old', 1920, 1080, 60.0, 1.0, [1.0], {'is-current': True}))
    physical = [(real, [('real-mode', 1920, 1080, 60.0, 1.0, [1.0], {'is-current': True})], {}),
                (virtual, modes, {})]
    logical = [(0, 0, 1.0, 0, True, [real], {})]
    if active:
        logical.append((1920, 0, 1.0, 0, False, [virtual, real] if mirrored else [virtual], {}))
    return (serial, physical, logical, {})


class GnomeActivationTest(unittest.TestCase):
    def test_autoactivated_connector_is_configured_once(self):
        for active, wrong_mode in [(False, False), (True, False), (True, True)]:
            with self.subTest(active=active, wrong_mode=wrong_mode):
                payload, details, error = comp._build_vkms_activation_config(
                    state(active, wrong_mode), 'Virtual-1', 2340, 1080, 60, SimpleNamespace())
                self.assertEqual(error, '')
                self.assertEqual(len(payload), 2)
                self.assertEqual(payload[0][5][0][:2], ('DP-1', 'real-mode'))
                self.assertEqual(payload[1][5][0][:2], ('Virtual-1', 'requested'))
                self.assertEqual(payload[1][:5], (1920, 0, 1.0, 0, False))
                self.assertEqual(details['width'], 2340)

    def test_mirrored_connector_is_not_reconfigured(self):
        payload, _, error = comp._build_vkms_activation_config(
            state(mirrored=True), 'Virtual-1', 2340, 1080, 60, SimpleNamespace())
        self.assertIsNone(payload)
        self.assertIn('mirrored', error)

    def test_autoactivation_during_serial_retry_succeeds(self):
        interface = Mock()
        interface.ApplyMonitorsConfig.side_effect = [RuntimeError('stale serial'), None]
        with (patch.object(comp, '_require_dbus', return_value=SimpleNamespace()),
              patch.object(comp, '_display_config_interface', return_value=interface),
              patch.object(comp, '_wait_for_new_vkms_connector', return_value=('Virtual-1', state(False), '')),
              patch.object(comp, '_mutter_state', return_value=state(serial=2))):
            ok, details, error = comp.gnome_activate_vkms({}, 2340, 1080, 60)
        self.assertTrue(ok, error)
        self.assertEqual(details['name'], 'Virtual-1')
        self.assertEqual(interface.ApplyMonitorsConfig.call_count, 2)


if __name__ == '__main__':
    unittest.main()
