import subprocess
import unittest
from unittest.mock import Mock, patch
from monitorize_vkms import secure_boot

CERT = '/var/lib/dkms/mok.pub'


class EnrollmentTests(unittest.TestCase):
    def result(self, code, stdout, stderr=''):
        return Mock(returncode=code, stdout=stdout, stderr=stderr)

    def test_accepts_enrolled_zero_and_fedora_one(self):
        for code in (0, 1):
            with self.subTest(code=code):
                execute = Mock(return_value=self.result(code, CERT+' is already enrolled\n'))
                secure_boot.check_enrollment(CERT, execute=execute)
                self.assertEqual(execute.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_opensuse_accepts_positive_enrollment_with_keyring_warning(self):
        warning = 'Failed to access kernel trusted keyring: Required key not available'
        execute = Mock(return_value=self.result(0, CERT+' is already enrolled', warning))
        secure_boot.check_enrollment(CERT, execute=execute)
        execute.return_value.stdout = CERT+' is not enrolled'
        with self.assertRaisesRegex(RuntimeError, 'enrollment check failed'):
            secure_boot.check_enrollment(CERT, execute=execute)

    def test_serial_match_requires_the_issuer_name(self):
        certificate = ('serial=AABBCCDDEEFF0011\nissuer=\n'
                       '    commonName = DKMS signing key\n'
                       '    11:22:33:44:55:66:77:88')
        for signer in ('DKMS signing key', 'Unexpected issuer'):
            with self.subTest(signer=signer):
                def command(args, **kwargs):
                    if args[0] == 'openssl':
                        return Mock(stdout=certificate)
                    return Mock(stdout=signer if args[2] == 'signer' else 'AA:BB:CC:DD:EE:FF:00:11')
                with patch.object(secure_boot.Path, 'is_file', return_value=True), \
                     patch.object(secure_boot.subprocess, 'run', side_effect=command), \
                     patch.object(secure_boot, 'check_enrollment') as enrollment:
                    if signer == 'DKMS signing key':
                        secure_boot.require_trusted_module('module')
                        enrollment.assert_called_once_with(CERT)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'matches'):
                            secure_boot.require_trusted_module('module')
                        enrollment.assert_not_called()

    def test_rejects_non_enrollment_unknown_and_errors(self):
        for code, output, error in [(1, CERT+' is not enrolled', ''),
                                    (0, CERT+' is blocked in dbx', ''),
                                    (1, '', ''), (1, 'unrecognized result', ''),
                                    (2, CERT+' is already enrolled', ''),
                                    (1, CERT+' is already enrolled', 'EFI read failure')]:
            with self.subTest(output=output, code=code):
                with self.assertRaisesRegex(RuntimeError, 'enrollment check failed'):
                    secure_boot.check_enrollment(CERT, execute=Mock(return_value=self.result(code, output, error)))

    def test_wrong_certificate_never_tests_enrollment(self):
        def command(args, **kwargs):
            return Mock(stdout='AA:BB:CC:DD:EE:FF:00:11' if args[0]=='modinfo'
                        else '11:22:33:44:55:66:77:88')
        with patch.object(secure_boot.Path, 'is_file', return_value=True), patch.object(secure_boot.subprocess, 'run', side_effect=command), patch.object(secure_boot, 'check_enrollment') as enrollment:
            with self.assertRaisesRegex(RuntimeError, 'matches'):
                secure_boot.require_trusted_module('module')
            enrollment.assert_not_called()

    def test_matching_certificate_is_tested(self):
        key='AA:BB:CC:DD:EE:FF:00:11'
        with patch.object(secure_boot.Path, 'is_file', return_value=True), patch.object(secure_boot.subprocess, 'run', return_value=Mock(stdout=key)), patch.object(secure_boot, 'check_enrollment') as enrollment:
            secure_boot.require_trusted_module('module')
            enrollment.assert_called_once_with(CERT)

    def test_actual_process_enrolled_with_status_one(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'mokutil'
            executable.write_text('#!/bin/sh\nprintf "%s is already enrolled\\n" "$2"\nexit 1\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'PATH': directory + os.pathsep + os.environ['PATH']}):
                secure_boot.check_enrollment(CERT)
