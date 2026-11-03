import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from monitorize_vkms import kernel_build as worker


class KernelBuildTests(unittest.TestCase):
    def test_missing_headers_does_not_invoke_dkms(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(worker, 'MODULES', Path(directory)), patch.object(worker, 'save') as save, patch.object(worker, 'run') as run:
                worker.prepare('future-kernel')
                self.assertEqual(save.call_args.args[1]['state'], 'waiting_headers')
                run.assert_not_called()

    def test_compile_failure_never_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'future/build').mkdir(parents=True)
            (root / 'future/build/Makefile').touch()
            def command(*args, **kwargs):
                raise RuntimeError('compile failed')
            with patch.object(worker, 'MODULES', root), patch.object(worker, 'STATE', root), patch.object(worker, 'fingerprint', return_value='hash'), patch.object(worker, 'autoload_disabled', return_value=True), patch.object(worker, 'save') as save, patch.object(worker, 'run', side_effect=command) as run:
                worker.prepare('future')
                self.assertEqual(save.call_args.args[1]['state'], 'build_failed')
                self.assertFalse(any(call.args[:2] == ('dkms', 'install') for call in run.call_args_list))
                self.assertTrue(any('future' in call.args for call in run.call_args_list))

    def test_secure_boot_disabled_does_not_request_enrollment(self):
        with patch.object(worker.Path, 'exists', return_value=True), patch.object(worker, 'run', return_value='SecureBoot disabled') as run:
            worker.signing_check('module')
            self.assertEqual(run.call_count, 1)

    def test_wrong_certificate_does_not_pass_trust(self):
        def command(*args, **kwargs):
            if args[:2] == ('mokutil', '--sb-state'):
                return 'SecureBoot enabled'
            if args[0] == 'modinfo':
                return 'AA:BB'
            return 'Subject Key Identifier: 11:22:33:44:55:66:77:88'
        with patch.object(worker.Path, 'exists', return_value=True), patch.object(worker.Path, 'is_file', return_value=True), patch.object(worker, 'run', side_effect=command) as run:
            with self.assertRaisesRegex(RuntimeError, 'enroll'):
                worker.signing_check('module')
            self.assertFalse(any(call.args[:2] == ('mokutil', '--test-key') for call in run.call_args_list))

    def test_prepared_artifact_is_checked_without_build_or_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'future/build').mkdir(parents=True)
            (root / 'future/build/Makefile').touch()
            def command(*args, **kwargs):
                if args[:3] == ('modinfo', '-k', 'future'):
                    return '/lib/modules/future/extra/monitorize_vkms.ko.xz'
                return 'future SMP'
            with patch.object(worker, 'MODULES', root), patch.object(worker, 'STATE', root), patch.object(worker, 'fingerprint', return_value='hash'), patch.object(worker, 'autoload_disabled', return_value=True), patch.object(worker, 'signing_check'), patch.object(worker, 'save') as save, patch.object(worker, 'run', side_effect=command) as run:
                worker.prepare('future')
                self.assertEqual(save.call_args.args[1]['state'], 'prepared')
                self.assertFalse(any(call.args[0] in ('dkms', 'modprobe', 'systemctl') for call in run.call_args_list))

    def test_autoload_configuration_defers_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'future/build').mkdir(parents=True)
            (root / 'future/build/Makefile').touch()
            with patch.object(worker, 'MODULES', root), patch.object(worker, 'STATE', root), patch.object(worker, 'fingerprint', return_value='hash'), patch.object(worker, 'autoload_disabled', return_value=False), patch.object(worker, 'save') as save, patch.object(worker, 'run') as run:
                worker.prepare('future')
                self.assertEqual(save.call_args.args[1]['state'], 'needs_attention')
                run.assert_not_called()

    def test_worker_uses_shared_enrollment_check_for_matching_key(self):
        key = 'AA:BB:CC:DD:EE:FF:00:11'
        def command(*args, **kwargs):
            return 'SecureBoot enabled' if args[:2] == ('mokutil', '--sb-state') else key
        with patch.object(worker.Path, 'exists', return_value=True), patch.object(worker.Path, 'is_file', return_value=True), patch.object(worker, 'run', side_effect=command), patch.object(worker, 'check_enrollment') as enrollment:
            worker.signing_check('module')
            enrollment.assert_called_once_with(Path('/var/lib/dkms/mok.pub'))
