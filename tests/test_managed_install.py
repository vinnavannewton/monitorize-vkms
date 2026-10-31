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
        backend.connection_in_use.return_value = False
        backend.masked.return_value = False
        backend.boot_id = 'boot-fixture'
        return SimpleNamespace(backend=backend, prepare=prepare, load=Mock(return_value=None), save=Mock()), events

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

    def test_legacy_disabled_idle_services_get_durable_receipt_before_prepare(self):
        lifecycle, events = self.lifecycle(enabled=False, active=True)
        units = ['monitorize-remote-login.service', 'monitorize-vkms-boot-display.service',
                 'monitorize-remote-login-boot@1000.service', 'monitorize-remote-login-desktop@1000.service']
        lifecycle.backend.masked.side_effect = lambda name: name == units[1]
        lifecycle.save.side_effect = lambda value: events.append(('saved', value))
        m.prepare_lifecycle(lifecycle, 1000)
        self.assertEqual(events[0], ('saved', dict(uid=1000, operation='upgrade', boot_id='boot-fixture',
            restart=True, masks=[units[0], units[2], units[3]], stage='pausing')))
        self.assertEqual(events[1], ('upgrade', False))
        self.assertFalse(lifecycle.backend.state.return_value[0]['enabled'])

    def test_legacy_disabled_connected_services_refuse_before_receipt_or_pause(self):
        lifecycle, events = self.lifecycle(enabled=False, active=True)
        lifecycle.backend.connection_in_use.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'Disconnect'):
            m.prepare_lifecycle(lifecycle, 1000)
        lifecycle.save.assert_not_called()
        self.assertEqual(events, [])

    def test_existing_receipt_is_preserved_for_legacy_recovery_checks(self):
        lifecycle, events = self.lifecycle(enabled=False, active=True)
        lifecycle.load.return_value = dict(uid=2000, operation='remove', boot_id='old')
        m.prepare_lifecycle(lifecycle, 1000)
        lifecycle.save.assert_not_called()
        self.assertEqual(events, [('upgrade', False)])

    def test_receipt_write_failure_never_calls_prepare(self):
        lifecycle, events = self.lifecycle(enabled=False, active=True)
        lifecycle.save.side_effect = OSError('disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
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


class ExpiredMaintenanceTests(unittest.TestCase):
    def lifecycle(self, receipt):
        lifecycle=Mock()
        lifecycle.load.return_value=receipt
        lifecycle.backend.boot_id='current'
        return lifecycle

    def test_previous_boot_reports_recoverable_code_before_prepare(self):
        lifecycle=self.lifecycle(dict(uid=1000,boot_id='previous'))
        with patch.object(m,'prepare_lifecycle') as prepare:
            with self.assertRaises(m.ExpiredMaintenance):m.prepare_current(lifecycle,1000)
        prepare.assert_not_called()
        lifecycle.resume.assert_not_called()

    def test_same_boot_uses_existing_prepare(self):
        lifecycle=self.lifecycle(dict(uid=1000,boot_id='current'))
        with patch.object(m,'prepare_lifecycle') as prepare:m.prepare_current(lifecycle,1000)
        prepare.assert_called_once_with(lifecycle,1000)

    def test_resume_expired_only_calls_installed_resume(self):
        lifecycle=self.lifecycle(dict(uid=1000,boot_id='previous'))
        self.assertTrue(m.resume_expired(lifecycle,1000)['success'])
        lifecycle.resume.assert_called_once_with()
        lifecycle.prepare.assert_not_called()

    def test_no_receipt_is_idempotent(self):
        lifecycle=self.lifecycle(None)
        self.assertTrue(m.resume_expired(lifecycle,1000)['success'])
        lifecycle.resume.assert_not_called()

    def test_current_boot_replacement_receipt_refuses_resume(self):
        lifecycle=self.lifecycle(dict(uid=1000,boot_id='current'))
        with self.assertRaisesRegex(RuntimeError,'changed'):m.resume_expired(lifecycle,1000)
        lifecycle.resume.assert_not_called()

    def test_other_user_receipt_never_prepares_or_resumes(self):
        lifecycle=self.lifecycle(dict(uid=2000,boot_id='previous'))
        with self.assertRaises(PermissionError):m.prepare_current(lifecycle,1000)
        with self.assertRaises(PermissionError):m.resume_expired(lifecycle,1000)
        lifecycle.resume.assert_not_called()

    def test_root_response_exposes_action_not_command(self):
        import contextlib,io,json
        with patch.object(m.os,'geteuid',return_value=0),patch.dict(m.os.environ,PKEXEC_UID='1000'), \
             patch.object(m.sys,'argv',['helper','install','--revision','a'*40]), \
             patch.object(m,'install',side_effect=m.ExpiredMaintenance('Expired maintenance')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(m.main(),1)
        result=json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result['recovery_required'],'resume_expired')
        self.assertNotIn('command',result)

    def test_recovery_revalidates_owner_under_setup_lock(self):
        from contextlib import contextmanager
        lifecycle=self.lifecycle(dict(uid=1000,boot_id='previous'))
        lifecycle.backend.state.return_value=({'uid':2000},False)
        events=[]
        @contextmanager
        def lock():
            events.append('locked')
            yield
            events.append('unlocked')
        with patch.object(m,'validate_source') as validate, \
             patch.object(m,'installed_lifecycle',return_value=lifecycle), \
             patch.dict(m.sys.modules,{'monitorize.remote_login.live_setup':SimpleNamespace(setup_lock=lock)}):
            with self.assertRaisesRegex(PermissionError,'changed'):m.recover_expired('a'*40,1000)
        validate.assert_called_once_with('a'*40,1000)
        lifecycle.resume.assert_not_called()
        self.assertEqual(events[0],'locked')

    def test_recovery_executes_resume_inside_setup_lock(self):
        from contextlib import contextmanager
        lifecycle=self.lifecycle(dict(uid=1000,boot_id='previous'))
        lifecycle.backend.state.return_value=({'uid':1000},False)
        events=[]
        @contextmanager
        def lock():
            events.append('locked')
            yield
            events.append('unlocked')
        lifecycle.resume.side_effect=lambda:events.append('resume')
        with patch.object(m,'validate_source'),patch.object(m,'installed_lifecycle',return_value=lifecycle), \
             patch.dict(m.sys.modules,{'monitorize.remote_login.live_setup':SimpleNamespace(setup_lock=lock)}):
            self.assertTrue(m.recover_expired('a'*40,1000)['success'])
        self.assertEqual(events,['locked','resume','unlocked'])
