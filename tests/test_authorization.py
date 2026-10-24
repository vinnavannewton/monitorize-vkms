import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from monitorize_vkms import authorization as auth
from monitorize_vkms import dbus_service as service


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.account=SimpleNamespace(pw_gid=1000,pw_name='account',pw_dir='/home/account')
        account=patch.object(auth.pwd,'getpwuid',return_value=self.account);account.start();self.addCleanup(account.stop)

    def state(self):
        # Unit tests use a user-owned /tmp fixture. Production checks all ancestors.
        with patch.object(auth,'_directory',return_value=self.directory):
            return auth.get_state(1000,directory=self.directory,owner_uid=os.getuid())

    def test_missing_valid_changed_account_and_revoked_grants(self):
        self.assertFalse(self.state()['unattended_enabled'])
        path=self.directory/'1000.json';path.write_text(json.dumps(auth._account(1000)));path.chmod(0o600)
        self.assertTrue(self.state()['unattended_enabled'])
        self.account.pw_name='replacement'
        self.assertFalse(self.state()['unattended_enabled']);self.assertTrue(self.state()['repair_required'])
        path.unlink();self.assertFalse(self.state()['unattended_enabled'])

    def test_symlink_writable_and_malformed_grants_are_never_accepted(self):
        path=self.directory/'1000.json'
        for content,mode in [('{}',0o600),(json.dumps(auth._account(1000)),0o666),('invalid',0o600)]:
            path.write_text(content);path.chmod(mode)
            self.assertFalse(self.state()['unattended_enabled']);self.assertTrue(self.state()['repair_required'])
        path.unlink();path.symlink_to('/etc/passwd')
        self.assertFalse(self.state()['unattended_enabled'])
        with self.assertRaises(PermissionError):auth._directory(self.directory)

    def test_grant_failure_never_prompts_or_starts_helper(self):
        with patch.object(auth,'require_grant',side_effect=PermissionError('revoked')),patch.object(service.subprocess,'run') as run:
            with self.assertRaises(PermissionError):service._request(':1.2',1000,'create',['create','--slot','mon1'],unattended=True)
            run.assert_not_called()

    def test_silent_create_checks_grant_and_active_session_and_only_runs_fixed_helper(self):
        with patch.object(auth,'require_grant') as grant,patch('monitorize_vkms.helper._active_session') as session,patch.object(service.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"success":true}',stderr='')) as run:
            result=service._request(':1.2',1000,'create',['create','--slot','mon1'],unattended=True)
            self.assertTrue(json.loads(result)['success']);grant.assert_called_once_with(1000);session.assert_called_once_with(1000)
            run.assert_called_once();self.assertEqual(run.call_args.args[0][2],str(service.HELPER))
            self.assertEqual(run.call_args.kwargs['env']['PKEXEC_UID'],'1000')
            run.reset_mock()
            with self.assertRaises(PermissionError):service._request(':1.2',1000,'destroy',['destroy'],unattended=True)
            run.assert_not_called()

    def test_interactive_create_still_requires_polkit(self):
        with patch.object(service.subprocess,'run',return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(PermissionError):service._request(':1.2',1000,'create',['create'])
            self.assertEqual(run.call_args.args[0][0],'/usr/bin/pkcheck')
