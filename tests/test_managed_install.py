"""GUI installer guards; subprocesses and global locks are always mocked."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import subprocess
from monitorize_vkms import managed_install as m


class ManagedInstallTests(unittest.TestCase):
    def test_foreign_lease_refuses_before_any_service_command(self):
        with patch('monitorize_vkms.lease.read_lease', return_value=dict(uid=2000)), \
             patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'still owned'):
                m.check_idle()
            run.assert_not_called()

    def test_source_git_validation_always_runs_as_the_authorizing_user(self):
        metadata = SimpleNamespace(st_uid=1000, st_mode=0o755)
        answers = ['https://github.com/vinnavannewton/monitorize-vkms.git', 'main', 'a' * 40, '']
        with patch.object(m.Path, 'lstat', return_value=metadata), \
             patch.object(m.pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir='/fixture', pw_gid=1000)), \
             patch.object(m.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0, x, '') for x in answers]) as run:
            m.validate_source('a' * 40, 1000)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['user'], 1000)
            self.assertEqual(call.kwargs['extra_groups'], [])
        self.assertFalse(any('fetch' in call.args[0] for call in run.call_args_list))

    def test_busy_host_is_not_stopped(self):
        lock = SimpleNamespace(st_uid=0, st_mode=0o100600, st_nlink=1)
        with patch.object(m.Path, 'is_file', return_value=True), patch.object(m, 'validate_source'), patch.object(m, 'remote_lifecycle', return_value=None), \
             patch.object(m.os, 'open', return_value=77), patch.object(m.os, 'close'), \
             patch.object(m.os, 'fstat', return_value=lock), patch.object(m.fcntl, 'flock'), \
             patch.object(m, 'check_idle', side_effect=RuntimeError('Foreign active display')), \
             patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'active', '')) as run:
            result = m.install('a' * 40, 1000)
        self.assertFalse(result['success'])
        self.assertEqual(len(run.call_args_list), 1)
        self.assertEqual(run.call_args.args[0][1], 'is-active')
