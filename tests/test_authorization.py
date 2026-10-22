import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from monitorize_vkms import authorization as auth, dbus_service


class AuthorizationTest(unittest.TestCase):
    def test_worker_connection_is_not_attached_to_glib_dispatch(self):
        from unittest.mock import Mock
        bus = Mock()
        bus.get_unix_user.return_value = 1000
        with patch.object(dbus_service.dbus, 'SystemBus', return_value=bus) as connect:
            self.assertEqual(dbus_service._sender_uid(':1.9'), 1000)
        connect.assert_called_once_with(private=True, mainloop=dbus_service.NULL_MAIN_LOOP)
        bus.close.assert_called_once()

    def test_worker_connection_closes_on_disconnected_caller(self):
        from unittest.mock import Mock
        bus = Mock()
        bus.get_unix_user.side_effect = dbus_service.dbus.DBusException('disconnected')
        with patch.object(dbus_service.dbus, 'SystemBus', return_value=bus), self.assertRaises(dbus_service.dbus.DBusException):
            dbus_service._sender_uid(':1.9')
        bus.close.assert_called_once()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'authorization'
        for target, value in [('DIRECTORY', self.directory), ('OWNER_UID', os.getuid())]:
            item = patch.object(auth, target, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(auth, 'identity', return_value=dict(uid=1000, name='test', home_inode=42))
        self.identity = item.start()
        self.addCleanup(item.stop)
        item = patch.object(auth.os, 'geteuid', return_value=0)
        item.start()
        self.addCleanup(item.stop)

    def test_enroll_revoke_and_reload(self):
        self.assertFalse(auth.state(1000)['unattended_enabled'])
        self.assertTrue(auth.set_enabled(1000, True)['unattended_enabled'])
        self.assertEqual((self.directory / '1000.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(auth.set_enabled(1000, False)['unattended_enabled'])
        self.assertFalse(auth.read(1000)['enabled'])

    def test_stale_identity_is_not_authorized(self):
        auth.set_enabled(1000, True)
        self.identity.return_value = dict(uid=1000, name='replacement', home_inode=43)
        with self.assertRaises(PermissionError):
            auth.read(1000)

    def test_untrusted_permissions_and_symlinks_rejected(self):
        auth.set_enabled(1000, True)
        path = self.directory / '1000.json'
        path.chmod(0o666)
        with self.assertRaises(PermissionError):
            auth.read(1000)
        path.unlink()
        path.symlink_to(Path(self.temp.name) / 'missing')
        with self.assertRaises(OSError):
            auth.read(1000)

    def test_nonroot_cannot_enroll(self):
        with patch.object(auth.os, 'geteuid', return_value=1000):
            with self.assertRaises(PermissionError):
                auth.set_enabled(1000, True)

    def test_noninteractive_missing_grant_never_runs_pkcheck_or_helper(self):
        with patch.object(dbus_service.authorization, 'read', return_value=None), patch.object(dbus_service.subprocess, 'run') as run:
            with self.assertRaises(PermissionError):
                dbus_service._request(':1.1', 1000, 'create', ['create'], interactive=False)
            run.assert_not_called()

    def test_grant_skips_pkcheck_but_revocation_blocks_new_create(self):
        with patch.object(dbus_service.authorization, 'read', return_value={'enabled': True}), patch.object(dbus_service, '_execute_helper', return_value='ok') as execute:
            self.assertEqual(dbus_service._request(':1.1', 1000, 'create', ['create'], False), 'ok')
            execute.assert_called_once_with(1000, 'create', ['create'], unattended=True)
        with patch.object(dbus_service.authorization, 'read', return_value={'enabled': False}), patch.object(dbus_service, '_execute_helper', return_value='clean') as execute:
            with self.assertRaises(PermissionError):
                dbus_service._request(':1.1', 1000, 'create', ['create'], False)
            self.assertEqual(dbus_service._request(':1.1', 1000, 'destroy', ['destroy'], False), 'clean')
            execute.assert_called_once_with(1000, 'destroy', ['destroy'], unattended=True)

    def test_abandoned_result_cleans_only_its_explicit_generation(self):
        with patch.object(dbus_service.authorization, 'read', return_value={'enabled': False}), patch.object(dbus_service, '_execute_helper') as execute:
            dbus_service._cleanup_abandoned(1000, ['create', '--slot', 'mon2'], {'created': True, 'generation': 'old-generation'})
            execute.assert_called_once_with(1000, 'destroy', ['destroy', '--slot', 'mon2', '--generation', 'old-generation'], unattended=True)

    def test_stale_bus_identity_never_starts_helper(self):
        with patch.object(dbus_service, '_sender_uid', return_value=1001), patch.object(dbus_service, '_request') as request:
            with self.assertRaises(PermissionError):
                dbus_service._run_live_request(':1.1', 1000, 'create', [], False)
            request.assert_not_called()

    def test_revoked_user_can_still_use_explicit_interactive_manual_create(self):
        from subprocess import CompletedProcess
        with patch.object(dbus_service.authorization, 'read', return_value={'enabled': False}), patch.object(dbus_service.subprocess, 'run', return_value=CompletedProcess([], 0)), patch.object(dbus_service, '_execute_helper', return_value='ok') as execute:
            self.assertEqual(dbus_service._request(':1.1', 1000, 'create', ['create']), 'ok')
            execute.assert_called_once_with(1000, 'create', ['create'], unattended=False)

    def test_invalid_record_and_directory_never_authorize(self):
        auth.set_enabled(1000, True)
        path = self.directory / '1000.json'
        path.write_text('{"version":1,"enabled":"true"}')
        with self.assertRaises(PermissionError):
            auth.read(1000)
        self.directory.chmod(0o777)
        with self.assertRaises(PermissionError):
            auth.read(1000)

    def test_uid_path_injection_and_root_enrollment_are_rejected(self):
        for uid in ('../../other', True, 0, -1):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                auth.read(uid)

    def test_legacy_mount_device_changes_do_not_invalidate_grant(self):
        auth.set_enabled(1000,True)
        path=self.directory/'1000.json'
        record=json.loads(path.read_text());record['identity']['home_device']=123
        path.write_text(json.dumps(record))
        self.assertTrue(auth.state(1000)['unattended_enabled'])
        self.identity.return_value=dict(uid=1000,name='test',home_inode=99)
        self.assertFalse(auth.state(1000)['unattended_enabled'])
        self.assertTrue(auth.state(1000)['repair_required'])
        with self.assertRaises(auth.StaleAuthorizationError):auth.read(1000)

    def test_stale_record_allows_only_explicit_polkit_fallback(self):
        from subprocess import CompletedProcess
        with patch.object(dbus_service.authorization,'read',side_effect=auth.StaleAuthorizationError('stale')), \
             patch.object(dbus_service.subprocess,'run',return_value=CompletedProcess([],0)) as polkit, \
             patch.object(dbus_service,'_execute_helper',return_value='ok'):
            self.assertEqual(dbus_service._request(':1.1',1000,'create',['create'],True),'ok')
            polkit.assert_called_once()
            with self.assertRaises(auth.StaleAuthorizationError):
                dbus_service._request(':1.1',1000,'create',['create'],False)
