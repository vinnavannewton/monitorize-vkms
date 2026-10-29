import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT=Path(__file__).resolve().parents[1]


class HostDeliveryTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        admission=patch('monitorize_vkms.recovery.read_gate',return_value=None)
        admission.start();self.addCleanup(admission.stop)

    def test_stage_produces_matching_helpers_and_no_enrollment_or_service_enablement(self):
        script=ROOT/'scripts/stage-host-assets.py'
        spec=importlib.util.spec_from_file_location('stage_host',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with TemporaryDirectory() as directory:
            stage=Path(directory)
            mask=os.umask(0o077)
            try:module.stage_host_assets(stage)
            finally:os.umask(mask)
            helper=stage/'usr/libexec/monitorize-vkms/monitorize-vkms-helper'
            self.assertEqual((stage/'usr/libexec/monitorize-vkms/monitorize-vkms-authorization').read_bytes(), (ROOT/'monitorize_vkms/authorization_setup.py').read_bytes())
            package=stage/'usr/lib/monitorize-vkms/monitorize_vkms/helper.py'
            self.assertEqual(helper.read_bytes(),package.read_bytes())
            self.assertTrue((stage/'usr/lib/monitorize-vkms/monitorize_vkms/boot_display.py').is_file())
            manifest=json.loads((stage/'usr/share/monitorize-vkms/capabilities.json').read_text())
            self.assertEqual(manifest['version'],(ROOT/'VERSION').read_text().strip())
            self.assertEqual(manifest['remote_login_protocols']['idle_display'],1)
            self.assertEqual(helper.stat().st_mode & 0o777,0o755)
            self.assertFalse((stage/'etc/monitorize').exists())
            self.assertFalse(list(stage.rglob('*.wants')))
            self.assertFalse(list(stage.rglob('*.ko')))
            self.assertFalse((stage/'usr/lib/systemd/system/monitorize-vkms-boot-display.service').exists())
            for path in stage.rglob('*'):
                if path.is_dir():self.assertEqual(path.stat().st_mode & 0o777,0o755)

    def test_removal_guard_rejects_enabled_or_protected_ownership(self):
        from unittest.mock import patch
        from monitorize_vkms import package_maintenance as maintenance
        for settings,value in (({'enabled':True},None),({'enabled':False},{'protected':True})):
            with patch.object(maintenance,'read_enrollment',return_value=settings), \
                 patch.object(maintenance,'read_lease',return_value=value):
                with self.assertRaises(RuntimeError):maintenance.assert_removal_safe()
        with patch.object(maintenance,'read_enrollment',return_value=None), \
             patch.object(maintenance,'read_lease',return_value=None):
            maintenance.assert_removal_safe()
