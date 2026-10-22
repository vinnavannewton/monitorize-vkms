"""Idle cleanup preserves reservation, ownership and physical outputs."""
from unittest.mock import patch
from test_boot_greeter import GreeterTests
from monitorize_vkms import boot_runtime as runtime
from monitorize_vkms.boot_display import boot_output_status


class DesktopTests(GreeterTests):
    def test_idle_disconnects_only_owned_connector_and_retains_edid(self):
        active=self.prepare()
        with patch.object(runtime,'discover',return_value={'Virtual-2':self.output}):
            result=runtime.idle_reserved(state=self.state,connector=self.connector,lease=active)
        self.assertEqual(result['state'],'reserved')
        self.assertEqual((self.connector/'status').read_text(),'2')
        self.assertEqual(result['generation'],active['generation'])
        self.assertEqual(boot_output_status(self.state)['connector'],'Virtual-2')
        self.assertNotEqual((self.connector/'edid').read_bytes(),b'')

    def test_changed_connector_refuses_disconnect(self):
        active=self.prepare()
        replacement=dict(self.output,connector_id=99)
        with patch.object(runtime,'discover',return_value={'Virtual-2':replacement}):
            with self.assertRaises(PermissionError):
                runtime.idle_reserved(state=self.state,connector=self.connector,lease=active)
        self.assertEqual((self.connector/'status').read_text(),'1')

    def test_failed_disconnect_keeps_pending_responsibility(self):
        active=self.prepare()
        with patch.object(runtime,'discover',return_value={'Virtual-2':self.output}), \
             patch.object(runtime,'set_status',side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                runtime.idle_reserved(state=self.state,connector=self.connector,lease=active)
        self.assertEqual(boot_output_status(self.state)['state'],'pending')
