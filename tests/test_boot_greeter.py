"""Exercise actual configfs/lease effects with only DRM/compositor boundaries faked."""
from pathlib import Path
import unittest
from unittest.mock import patch
from test_boot_display import ReservationTests
from monitorize_vkms import boot_runtime as runtime
from monitorize_vkms.boot_display import boot_output_status


class GreeterTests(unittest.TestCase):
    reserve = ReservationTests.reserve

    def setUp(self):
        ReservationTests.setUp(self)
        self.value=self.reserve()
        self.drm=self.root/'card2-Virtual-2';self.drm.mkdir()
        (self.drm/'enabled').write_text('enabled')
        self.output=dict(name='Virtual-2',card='card2',connector_id=41,display='mon1',
                         status='connected',edid_sha256=self.value['edid_sha256'],path=str(self.drm))

    def prepare(self, activate=None, check=lambda:None):
        with patch.object(runtime,'discover',return_value={'Virtual-2':self.output}):
            return runtime.prepare_reserved(state=self.state,connector=self.connector,lease=self.value,
                activate=activate or (lambda output,remaining:dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60))),
                context_check=check)

    def test_prepare_marks_active_only_after_enabled_exact_mode(self):
        value=self.prepare()
        self.assertEqual(value['state'],'active')
        self.assertEqual(value['connector_id'],41)
        self.assertEqual((self.connector/'status').read_text(),'1')
        self.assertEqual(boot_output_status(self.state)['connector'],'Virtual-2')

    def test_mode_mismatch_disabled_output_and_greeter_change_retain_pending(self):
        with self.assertRaises(RuntimeError):
            self.prepare(lambda *args:dict(success=True,details=dict(width=1920,height=1080,refresh_rate=30)))
        self.assertEqual(boot_output_status(self.state)['state'],'pending')
        (self.drm/'enabled').write_text('disabled')
        with self.assertRaises(RuntimeError):self.prepare()
        (self.drm/'enabled').write_text('enabled')
        def changed():raise PermissionError('greeter replaced')
        with self.assertRaises(PermissionError):self.prepare(check=changed)
        self.assertEqual(boot_output_status(self.state)['state'],'pending')

    def test_connector_replacement_after_activation_never_publishes_active(self):
        def replace(output,remaining):
            self.output=dict(self.output,connector_id=99)
            return dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60))
        with patch.object(runtime,'discover',side_effect=lambda:{'Virtual-2':self.output}):
            with self.assertRaises(PermissionError):
                runtime.prepare_reserved(state=self.state,connector=self.connector,lease=self.value,
                                         activate=replace,context_check=lambda:None)
        self.assertEqual(boot_output_status(self.state)['state'],'pending')
