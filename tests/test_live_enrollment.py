"""Live reservation tests use temporary attributes, never the host display."""
from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from monitorize_vkms import boot_runtime as runtime, boot_display as facade, helper, lease
from monitorize_vkms.edid import generate_edid


class LiveReservationTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.state = self.root/'state'
        self.connector = self.root/'connector'; self.connector.mkdir()
        for name, value in {'status':'2', 'enabled':'1', 'dynamic':'1', 'edid':'', 'edid_enabled':'1'}.items():
            (self.connector/name).write_text(value)
        owner = patch.object(lease, 'OWNER_UID', os.getuid()); owner.start(); self.addCleanup(owner.stop)
        self.check = Mock()
        self.options = dict(state=self.state, connector=self.connector, uid=1000,
                            session='3', boot_id='boot-a', generation='a'*32, context_check=self.check)

    def reserve(self, **changes):
        return runtime.reserve_desktop_output(**dict(self.options, **changes))

    def test_live_reservation_stays_disconnected_and_is_desktop_owned(self):
        result = self.reserve()
        self.assertEqual((result['phase'], result['session'], result['state']), ('desktop', '3', 'reserved'))
        self.assertEqual(result['mode'], [1920,1080,60])
        self.assertEqual(result['generation'], 'a'*32)
        self.assertEqual((self.connector/'status').read_text(), '2')
        self.assertEqual((self.connector/'edid').read_bytes(), generate_edid(1920,1080,60,'mon1'))
        self.assertGreaterEqual(self.check.call_count, 2)

    def test_live_retry_preserves_generation_and_identity(self):
        first = self.reserve()
        with patch.object(runtime, 'program_edid', side_effect=AssertionError('must not rewrite')):
            self.assertEqual(first, self.reserve())

    def test_session_change_foreign_or_connected_slot_refuses_mutation(self):
        self.check.side_effect = PermissionError('session changed')
        with self.assertRaises(PermissionError): self.reserve()
        self.assertIsNone(lease.read_lease(self.state, 'mon1'))
        self.check.side_effect = None
        (self.connector/'status').write_text('1')
        with self.assertRaises((PermissionError, RuntimeError)): self.reserve()
        (self.connector/'status').write_text('2')
        lease.write_lease(self.state, 'mon1', dict(uid=1001, state='active'))
        before = (self.state/'primary.json').read_bytes()
        with self.assertRaises((PermissionError, RuntimeError, ValueError)): self.reserve()
        self.assertEqual((self.state/'primary.json').read_bytes(), before)
        self.assertEqual((self.connector/'edid').read_bytes(), b'')

    def test_pending_edid_failure_is_recoverable(self):
        with patch.object(runtime, 'program_edid', side_effect=OSError('write interrupted')):
            with self.assertRaises(OSError): self.reserve()
        self.assertEqual(lease.read_lease(self.state, 'mon1')['state'], 'pending')
        self.assertEqual(self.reserve()['state'], 'reserved')
        with self.assertRaises(PermissionError): self.reserve(generation='b'*32)

    def test_verify_rejects_changed_edid_pending_and_wrong_session(self):
        value = self.reserve()
        args = {k:self.options[k] for k in ('connector','uid','session','boot_id','context_check')}
        runtime.verify_desktop_reservation(lease=value, **args)
        for change in (dict(state='pending'), dict(session='4'), dict(boot_id='boot-b')):
            with self.assertRaises((PermissionError, RuntimeError)):
                runtime.verify_desktop_reservation(lease=dict(value, **change), **args)
        (self.connector/'edid').write_bytes(b'changed')
        with self.assertRaises(PermissionError): runtime.verify_desktop_reservation(lease=value, **args)

    def installed(self):
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(facade, '_installed_context', return_value=(helper,runtime,dict(uid=1000,enabled=True))))
        stack.enter_context(patch.object(helper, 'STATE_DIRECTORY', self.state))
        stack.enter_context(patch.object(helper, '_acquire_lifecycle_lock', side_effect=lambda:os.open(self.root/'lock', os.O_CREAT|os.O_RDWR, 0o600)))
        stack.enter_context(patch.object(helper, '_configfs_root', return_value=self.root))
        stack.enter_context(patch.object(helper, '_require_bootstrap', return_value=(self.root,self.connector)))
        stack.enter_context(patch.object(runtime, 'desktop_context', return_value='3', create=True))
        stack.enter_context(patch.object(runtime, 'quiescent', return_value=nullcontext()))
        self.options['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return stack

    def test_rollback_requires_exact_generation_and_quiescence(self):
        with self.installed():
            self.reserve()
            with self.assertRaises(PermissionError):
                facade.release_live_desktop_installed(1000,'3',generation='b'*32)
            with patch.object(runtime, 'quiescent', side_effect=BlockingIOError('capture running')):
                with self.assertRaises(BlockingIOError):
                    facade.release_live_desktop_installed(1000,'3',generation='a'*32)
            self.assertIsNotNone(lease.read_lease(self.state,'mon1'))
            result = facade.release_live_desktop_installed(1000,'3',generation='a'*32)
            self.assertEqual(result['state'], 'released')
            self.assertIsNone(lease.read_lease(self.state,'mon1'))

    def test_installed_reservation_checks_account_and_session(self):
        with self.installed():
            with self.assertRaises(PermissionError): facade.reserve_desktop_installed(1001,'3',generation='a'*32)
            with self.assertRaises(PermissionError): facade.reserve_desktop_installed(1000,'4',generation='a'*32)
            self.assertEqual(facade.reserve_desktop_installed(1000,'3',generation='a'*32)['state'],'reserved')

    def test_boot_entry_verifies_live_reservation_without_writes(self):
        with self.installed():
            self.reserve()
            with patch.object(os,'geteuid',return_value=0), patch.object(runtime,'require_installed'), \
                 patch.object(runtime,'read_enrollment',return_value=dict(uid=1000,enabled=True)), \
                 patch.object(runtime,'reserve_boot_output',side_effect=AssertionError('boot creation forbidden')), \
                 patch.object(runtime,'program_edid',side_effect=AssertionError('read only')):
                self.assertEqual(facade.main(),0)

    def test_desktop_context_rejects_another_local_account(self):
        with patch.object(helper,'_active_session',return_value=('3',dict(XDG_SESSION_TYPE='wayland'))), \
             patch.object(runtime,'local_sessions',return_value=[dict(seat='seat0',session='4',uid=1001,**{'class':'user'})]):
            with self.assertRaises(PermissionError): runtime.desktop_context(1000)
