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

    def greeter(self):
        stack = self.installed()
        context = stack.enter_context(patch.object(runtime, 'greeter_context', return_value=(120, 'g1')))
        stack.enter_context(patch.object(runtime, 'local_sessions', return_value=[
            dict(seat='seat0', uid=120, session='g1', **{'class':'greeter'})]))
        return stack, context

    def test_live_greeter_reservation_remains_disconnected_and_idempotent(self):
        stack, context = self.greeter()
        with stack:
            value = facade.reserve_greeter_installed(1000)
            self.assertEqual((value['phase'], value['session'], value['state']), ('greeter', '', 'reserved'))
            self.assertEqual((self.connector/'status').read_text(), '2')
            self.assertEqual((self.connector/'edid').read_bytes(), generate_edid(1920,1080,60,'mon1'))
            self.assertEqual(facade.reserve_greeter_installed(1000), value)
            self.assertGreaterEqual(context.call_count, 6)

    def test_lightdm_reserves_with_an_x11_greeter_without_connecting_vkms(self):
        stack = self.installed()
        rows = [dict(seat='seat0', uid=113, session='g1', **{'class': 'greeter'})]
        with stack, patch.object(runtime, 'local_sessions', return_value=rows), \
             patch.object(helper, '_active_session', return_value=('g1', {'XDG_SESSION_TYPE': 'x11'})), \
             patch.object(runtime.pwd, 'getpwuid', return_value=Mock(pw_name='lightdm')), \
             patch.object(facade, '_installed_context', return_value=(
                 helper, runtime, dict(uid=1000, enabled=True, manager='lightdm.service'))):
            self.assertEqual(facade.reserve_greeter_installed(1000)['state'], 'reserved')
            self.assertEqual((self.connector/'status').read_text(), '2')
            with self.assertRaises(PermissionError):
                runtime.greeter_context(x11_account='sddm')
            with self.assertRaises(PermissionError):
                runtime.greeter_context()

    def test_live_greeter_reservation_rejects_wrong_account_capture_and_seat_owner(self):
        stack, _ = self.greeter()
        with stack:
            with self.assertRaises(PermissionError):
                facade.reserve_greeter_installed(1001)
            with patch.object(runtime, 'quiescent', side_effect=BlockingIOError('capture running')):
                with self.assertRaises(BlockingIOError):facade.reserve_greeter_installed(1000)
            with patch.object(runtime, 'local_sessions', return_value=[dict(seat='seat0', **{'class':'user'})]):
                with self.assertRaises(RuntimeError):facade.reserve_greeter_installed(1000)
            self.assertIsNone(lease.read_lease(self.state, 'mon1'))
            self.assertEqual((self.connector/'edid').read_bytes(), b'')

    def test_live_greeter_change_retains_pending_and_never_connects(self):
        stack, context = self.greeter()
        original = runtime.program_edid
        def change(connector, edid):
            original(connector, edid)
            context.return_value = (120, 'g2')
        with stack, patch.object(runtime, 'program_edid', side_effect=change):
            with self.assertRaisesRegex(PermissionError, 'greeter changed'):
                facade.reserve_greeter_installed(1000)
            self.assertEqual(lease.read_lease(self.state, 'mon1')['state'], 'pending')
            self.assertEqual((self.connector/'status').read_text(), '2')

    def test_live_greeter_refuses_connected_foreign_or_desktop_slot(self):
        stack, _ = self.greeter()
        with stack:
            (self.connector/'status').write_text('1')
            with self.assertRaises(RuntimeError):facade.reserve_greeter_installed(1000)
            (self.connector/'status').write_text('2')
            self.reserve()
            before=(self.state/'primary.json').read_bytes()
            with self.assertRaises(RuntimeError):facade.reserve_greeter_installed(1000)
            self.assertEqual((self.state/'primary.json').read_bytes(), before)

    def test_live_greeter_opt_in_requires_context_check(self):
        with self.assertRaises(ValueError):
            runtime.reserve_boot_output(state=self.state, connector=self.connector, uid=1000,
                boot_id='boot-a', sessions=[], allow_greeter=True)
        self.assertIsNone(lease.read_lease(self.state, 'mon1'))

    def test_boot_unit_restart_at_live_greeter_uses_guarded_reservation(self):
        stack, _ = self.greeter()
        with stack, patch.object(os, 'geteuid', return_value=0), patch.object(runtime, 'require_installed'), \
             patch.object(runtime, 'read_enrollment', return_value=dict(uid=1000, enabled=True)):
            self.assertEqual(facade.main(), 0)
            value = facade.boot_output_status(self.state)
            self.assertEqual(value['phase'], 'greeter')
            self.assertEqual((self.connector/'status').read_text(), '2')
            self.assertEqual(facade.main(), 0)
            self.assertEqual(facade.boot_output_status(self.state), value)

    def test_boot_entry_verifies_live_reservation_without_writes(self):
        with self.installed():
            self.reserve()
            with patch.object(os,'geteuid',return_value=0), patch.object(runtime,'require_installed'), \
                 patch.object(runtime,'read_enrollment',return_value=dict(uid=1000,enabled=True)), \
                 patch.object(runtime,'reserve_boot_output',side_effect=AssertionError('boot creation forbidden')), \
                 patch.object(runtime,'program_edid',side_effect=AssertionError('read only')):
                self.assertEqual(facade.main(),0)

    def test_local_sessions_works_without_loginctl_json(self):
        listing = 'c2 1000 vinnavan seat0 tty7 active no -\n8 1000 vinnavan - - active no -\n'
        properties = 'Id=c2\nUser=1000\nSeat=seat0\nClass=user\n\nId=8\nUser=1000\nSeat=\nClass=user\n'
        replies = [Mock(stdout=text) for text in (listing, properties, listing)]
        with patch.object(runtime.subprocess, 'run', side_effect=replies) as run:
            rows = runtime.local_sessions()
        self.assertEqual(rows, [
            {'session': 'c2', 'uid': 1000, 'seat': 'seat0', 'class': 'user'},
            {'session': '8', 'uid': 1000, 'seat': '', 'class': 'user'},
        ])
        self.assertEqual(run.call_args_list[1].args[0][-3:], ['--', 'c2', '8'])
        replies[-1] = Mock(stdout='c3 1001 other seat0 tty7 active no -\n')
        with patch.object(runtime.subprocess, 'run', side_effect=replies):
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                runtime.local_sessions()

    def test_desktop_context_accepts_cinnamon_x11_on_the_enrolled_seat(self):
        rows = [dict(seat='seat0', session='3', uid=1000, **{'class': 'user'})]
        with patch.object(runtime, 'local_sessions', return_value=rows), \
             patch.object(helper, '_active_session') as active:
            active.return_value = ('3', {'XDG_SESSION_TYPE': 'x11', 'XDG_CURRENT_DESKTOP': 'X-Cinnamon'})
            self.assertEqual(runtime.desktop_context(1000), '3')
            active.return_value = ('3', {'XDG_SESSION_TYPE': 'x11', 'XDG_CURRENT_DESKTOP': 'GNOME'})
            with self.assertRaises(PermissionError):
                runtime.desktop_context(1000)

    def test_desktop_context_rejects_another_local_account(self):
        with patch.object(helper,'_active_session',return_value=('3',dict(XDG_SESSION_TYPE='wayland'))), \
             patch.object(runtime,'local_sessions',return_value=[dict(seat='seat0',session='4',uid=1001,**{'class':'user'})]):
            with self.assertRaises(PermissionError): runtime.desktop_context(1000)
