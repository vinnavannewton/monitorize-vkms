"""Exercise the installer's Secure Boot check without running installation."""

from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallerSecureBootTest(unittest.TestCase):
    def check_boot(self, *, efi, mokutil=True, output='SecureBoot enabled', code=0):
        source = (ROOT / 'install.sh').read_text()
        match = re.search(r'^check_secure_boot\(\) \{\n.*?^\}', source, re.M | re.S)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as tmp:
            efi_path = Path(tmp) / 'efi'
            if efi:
                efi_path.mkdir()
            function = match.group().replace('/sys/firmware/efi', shlex.quote(str(efi_path)))
            mock = ''
            if mokutil:
                mock = f'''mokutil() {{
                    printf 'MOKUTIL_CALLED\\n' >&2
                    printf '%s\\n' {shlex.quote(output)}
                    return {code}
                }}'''
            script = f'''set -euo pipefail
                log() {{ printf '%s\\n' "$*"; }}
                die() {{ printf '%s\\n' "$*" >&2; exit 1; }}
                {mock}
                {function}
                check_secure_boot
                printf 'RESULT=%s\\n' "$SECURE_BOOT_STATE"
            '''
            return subprocess.run(['/bin/bash', '-c', script], env={'PATH': ''},
                                  capture_output=True, text=True, check=False)

    def test_legacy_bios_does_not_call_installed_mokutil(self):
        result = self.check_boot(efi=False, output='EFI variables are not supported on this system', code=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RESULT=disabled', result.stdout)
        self.assertNotIn('MOKUTIL_CALLED', result.stdout + result.stderr)

    def test_legacy_bios_without_mokutil(self):
        result = self.check_boot(efi=False, mokutil=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('non-EFI boot', result.stdout)

    def test_uefi_requires_mokutil(self):
        result = self.check_boot(efi=True, mokutil=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mokutil is required', result.stderr)

    def test_uefi_enabled_and_disabled(self):
        for state in ('enabled', 'disabled'):
            with self.subTest(state=state):
                result = self.check_boot(efi=True, output=f'SecureBoot {state}')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'RESULT={state}', result.stdout)

    def test_uefi_inaccessible_variables_remains_an_error(self):
        result = self.check_boot(efi=True, output='EFI variables are not supported on this system', code=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Could not determine Secure Boot state', result.stderr)
        self.assertNotIn('RESULT=', result.stdout)

    def test_uefi_unrecognized_state_remains_an_error(self):
        result = self.check_boot(efi=True, output='unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unrecognized Secure Boot state', result.stderr)


if __name__ == '__main__':
    unittest.main()
