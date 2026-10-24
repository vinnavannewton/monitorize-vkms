import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io
import json
from monitorize_vkms import protocols


class ProtocolTests(unittest.TestCase):
    def test_manifest_exposes_implemented_facade_and_protocols(self):
        from monitorize_vkms import boot_display
        manifest=protocols.manifest('0.3.0')
        self.assertEqual(manifest,dict(schema_version=1,version='0.3.0',remote_login_protocols=dict(
            boot_display=1,greeter_on_demand=1,idle_display=1)))
        for function in ('boot_output_status','return_to_greeter','reconnect_reserved',
                         'desktop_operation_installed','greeter_operation_installed'):
            self.assertTrue(callable(getattr(boot_display,function)))

    def test_capability_reports_protocols_without_bootstrapped_topology(self):
        from monitorize_vkms import helper
        output=io.StringIO()
        with patch.object(helper,'_require_polkit_root'), \
             patch.object(helper,'_configfs_root',side_effect=helper.VkmsHelperError('not bootstrapped')), \
             patch.object(helper.sys,'argv',['helper','capability']),redirect_stdout(output):
            self.assertEqual(helper.main(),0)
        result=json.loads(output.getvalue())
        self.assertFalse(result['topology_ready'])
        self.assertEqual(result['remote_login_protocols']['boot_display'],1)
