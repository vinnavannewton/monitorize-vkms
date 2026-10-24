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


class ExactGnomeActivationTest(unittest.TestCase):
    def vm_state(self, active=False):
        old=state(active=active)
        physical=list(old[1])
        # The existing QEMU monitor also has a Virtual-* name and the same mode.
        real=('Virtual-9','RHT','QEMU Monitor','existing')
        physical[0]=(real,[('real-mode',2340,1080,60.0,1.0,[1.0],{'is-current':True})],{})
        logical=[(0,0,1.0,0,True,[real],{})]
        if active:logical.append((2340,0,1.0,0,False,[physical[1][0]],{}))
        return old[0],physical,logical,{}

    def test_exact_target_ignores_ambiguous_virtual_named_vm_screen_and_preserves_layout(self):
        interface=Mock();pending=self.vm_state(False);active=self.vm_state(True)
        before=comp.physical_monitor_identities((pending[0],[pending[1][0]],pending[2],{}))
        with patch.object(comp,'_require_dbus',return_value=SimpleNamespace()),patch.object(comp,'_mutter_state',side_effect=[pending,active]),patch.object(comp,'_wait_for_new_vkms_connector',side_effect=AssertionError('must not guess')):
            ok,details,error=comp.gnome_activate_vkms(before,2340,1080,60,display_config=interface,expected_output_name='Virtual-1')
        self.assertTrue(ok,error);self.assertEqual(details['name'],'Virtual-1')
        payload=interface.ApplyMonitorsConfig.call_args.args[2]
        self.assertEqual(payload[0][:5],pending[2][0][:5])
        self.assertEqual(payload[0][5][0][:2],('Virtual-9','real-mode'))
        self.assertEqual(payload[1][5][0][0],'Virtual-1')

    def test_exact_target_can_be_reactivated_without_a_new_connector(self):
        initial=self.vm_state(False);active=self.vm_state(True)
        interface=Mock()
        with patch.object(comp,'_require_dbus',return_value=SimpleNamespace()),patch.object(comp,'_mutter_state',side_effect=[initial,active]):
            ok,_,error=comp.gnome_activate_vkms(comp.physical_monitor_identities(initial),2340,1080,60,display_config=interface,expected_output_name='Virtual-1')
        self.assertTrue(ok,error)

    def test_missing_or_duplicate_target_does_not_choose_other_monitor(self):
        value=self.vm_state(False)
        for target,physical in [('Virtual-77',value[1]),('Virtual-1',value[1]+[value[1][1]])]:
            with self.subTest(target=target),patch.object(comp,'_mutter_state',return_value=(value[0],physical,value[2],{})),patch.object(comp.time,'sleep'):
                name,_,error=comp._wait_for_expected_vkms_connector(target,Mock(),attempts=2)
                self.assertFalse(name);self.assertTrue(error)

    def test_existing_screen_replacement_is_rejected_before_applying(self):
        pending=self.vm_state(False);interface=Mock()
        before={'Virtual-9':('Virtual-9','different','screen','identity')}
        with patch.object(comp,'_require_dbus',return_value=SimpleNamespace()),patch.object(comp,'_mutter_state',return_value=pending):
            ok,_,error=comp.gnome_activate_vkms(before,2340,1080,60,display_config=interface,expected_output_name='Virtual-1')
        self.assertFalse(ok);self.assertIn('identity changed',error);interface.ApplyMonitorsConfig.assert_not_called()
