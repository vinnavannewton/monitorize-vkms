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


class LifecycleCompatibilityTests(unittest.TestCase):
    def lifecycle(self, *, modern=False, enabled=True, protected=False, active=False, uid=1000):
        events = []
        if modern:
            def prepare(operation, *, include_idle_services=False):
                events.append((operation, include_idle_services))
        else:
            def prepare(operation, *, allow_disconnect=False):
                events.append((operation, allow_disconnect))
        backend = Mock()
        backend.state.return_value = ({'uid': uid, 'enabled': enabled}, protected)
        backend.active.return_value = active
        return SimpleNamespace(backend=backend, prepare=prepare), events

    def test_modern_helper_includes_disabled_idle_services(self):
        lifecycle, events = self.lifecycle(modern=True, enabled=False, active=True)
        m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events, [('upgrade', True)])

    def test_legacy_enabled_helper_uses_supported_signature(self):
        lifecycle, events = self.lifecycle(active=True)
        m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events, [('upgrade', False)])
        lifecycle.backend.active.assert_not_called()

    def test_legacy_disabled_idle_helper_is_safe_without_services(self):
        lifecycle, events = self.lifecycle(enabled=False)
        m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events, [('upgrade', False)])
        self.assertEqual(lifecycle.backend.active.call_count, 4)

    def test_legacy_protected_output_still_uses_ownership_pause(self):
        lifecycle, events = self.lifecycle(enabled=False, protected=True, active=True)
        m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events, [('upgrade', False)])
        lifecycle.backend.active.assert_not_called()

    def test_legacy_disabled_live_services_require_repair_before_mutation(self):
        lifecycle, events = self.lifecycle(enabled=False, active=True)
        with self.assertRaisesRegex(RuntimeError, 'Repair remote login'):
            m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events, [])

    def test_foreign_enrollment_refused_before_prepare(self):
        lifecycle, events = self.lifecycle(uid=2000)
        with self.assertRaises(PermissionError):
            m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events, [])

    def test_type_error_inside_modern_prepare_is_not_retried(self):
        calls = []
        def prepare(operation, *, include_idle_services=False):
            calls.append(operation)
            raise TypeError('internal failure after pause')
        lifecycle, _ = self.lifecycle(modern=True)
        lifecycle.prepare = prepare
        with self.assertRaisesRegex(TypeError, 'internal failure'):
            m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(calls, ['upgrade'])
