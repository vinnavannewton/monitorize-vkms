"""Boot reservation operates on a fake configfs tree, never the live GPU."""
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monitorize_vkms import boot_runtime as runtime, lease
from monitorize_vkms.boot_display import boot_output_status
from monitorize_vkms.edid import generate_edid


class ReservationTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.state=self.root/'state'
        self.connector=self.root/'connector0';self.connector.mkdir()
        for name,value in {'status':'2','enabled':'1','dynamic':'1','edid':'','edid_enabled':'1'}.items():
            (self.connector/name).write_text(value)
        owner=patch.object(lease,'OWNER_UID',os.getuid());owner.start();self.addCleanup(owner.stop)

    def reserve(self, sessions=None):
        return runtime.reserve_boot_output(state=self.state,connector=self.connector,uid=1000,
                                           boot_id='boot-a',sessions=sessions or [])

    def test_reserves_fixed_edid_without_connecting_and_is_idempotent(self):
        value=self.reserve()
        self.assertEqual(value['state'],'reserved')
        self.assertEqual(value['mode'],[1920,1080,60])
        self.assertEqual((self.connector/'status').read_text(),'2')
        self.assertEqual((self.connector/'edid').read_bytes(),generate_edid(1920,1080,60,'mon1'))
        self.assertEqual(boot_output_status(self.state),value)
        self.assertEqual(self.reserve(),value)

    def test_connected_output_foreign_lease_or_graphical_session_blocks_writes(self):
        (self.connector/'status').write_text('1')
        with self.assertRaises(RuntimeError):self.reserve()
        (self.connector/'status').write_text('2')
        for role in ('user','greeter','unknown'):
            with self.subTest(role=role),self.assertRaises(RuntimeError):
                self.reserve([dict(seat='seat0',**{'class':role})])
        lease.write_lease(self.state,'mon1',{'uid':1001,'state':'active'})
        with self.assertRaises(RuntimeError):self.reserve()
        self.assertEqual((self.connector/'edid').read_bytes(),b'')

    def test_edid_failure_retains_pending_ownership(self):
        with patch.object(runtime,'program_edid',side_effect=OSError('write failed')):
            with self.assertRaises(OSError):self.reserve()
        self.assertEqual(boot_output_status(self.state)['state'],'pending')
        self.assertEqual((self.connector/'status').read_text(),'2')

    def test_symlinked_attribute_cannot_write_outside_configfs(self):
        outside=self.root/'outside';outside.write_bytes(b'keep')
        (self.connector/'edid').unlink();(self.connector/'edid').symlink_to(outside)
        with self.assertRaises((OSError,RuntimeError,ValueError)):self.reserve()
        self.assertEqual(outside.read_bytes(),b'keep')


class BootEntryTests(unittest.TestCase):
    def test_missing_and_disabled_settings_do_not_acquire_lock_or_touch_gpu(self):
        from monitorize_vkms import boot_display, helper
        for settings in (None, {'enabled':False}):
            with self.subTest(settings=settings), patch.object(os,'geteuid',return_value=0), \
                 patch.object(runtime,'require_installed'), \
                 patch.object(runtime,'read_enrollment',return_value=settings), \
                 patch.object(helper,'_acquire_lifecycle_lock',side_effect=AssertionError('must not touch GPU')):
                self.assertEqual(boot_display.main(),0)

    def test_invalid_settings_and_nonroot_fail_without_gpu_changes(self):
        from monitorize_vkms import boot_display, helper
        with patch.object(os,'geteuid',return_value=1000):
            with self.assertRaises(PermissionError):boot_display.main()
        with patch.object(os,'geteuid',return_value=0), patch.object(runtime,'require_installed'), \
             patch.object(runtime,'read_enrollment',side_effect=ValueError('unsafe settings')), \
             patch.object(helper,'_acquire_lifecycle_lock',side_effect=AssertionError('must not touch GPU')):
            with self.assertRaises(ValueError):boot_display.main()
