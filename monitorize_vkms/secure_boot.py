"""Shared certificate enrollment checks, including Fedora mokutil 0.7.2."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

CERTIFICATES = ('/var/lib/dkms/mok.pub', '/var/lib/shim-signed/mok/MOK.der',
                '/etc/dkms/mok.pub')


def check_enrollment(certificate, *, execute=subprocess.run):
    """Require positive enrollment evidence; status 1 alone is not evidence."""
    result = execute(['mokutil', '--test-key', str(certificate)],
                     capture_output=True, text=True, timeout=10,
                     env={**os.environ, 'LC_ALL': 'C', 'LANG': 'C'})
    expected = str(certificate) + ' is already enrolled'
    warning = result.stderr.strip()
    # openSUSE mokutil can confirm enrollment via the MOK list even when
    # the kernel trusted keyring is unavailable.
    keyring_unavailable = 'Failed to access kernel trusted keyring: Required key not available'
    if (result.returncode in (0, 1) and result.stdout.strip() == expected
            and (not warning or (result.returncode == 0 and warning == keyring_unavailable))):
        return
    detail = (result.stdout + '\n' + result.stderr).strip()
    raise RuntimeError(f'Certificate enrollment check failed (exit {result.returncode}): '
                       f'{detail or "no enrollment evidence"}')


def require_trusted_module(module):
    def command(*args):
        result = subprocess.run(list(args), capture_output=True, text=True, check=True, timeout=10)
        return result.stdout.strip()
    key = command('modinfo', '-F', 'sig_key', module).replace(':', '').lower()
    if not re.fullmatch(r'[0-9a-f]{16,}', key):
        raise RuntimeError('Module has no usable signature key identifier')
    for name in CERTIFICATES:
        if not Path(name).is_file():
            continue
        text = command('openssl', 'x509', '-inform', 'DER', '-in', name,
                       '-noout', '-serial', '-issuer', '-nameopt', 'multiline',
                       '-ext', 'subjectKeyIdentifier')
        identifiers = re.findall(r'(?:[0-9A-Fa-f]{2}:){7,}[0-9A-Fa-f]{2}', text)
        matches_key_id = any(value.replace(':', '').lower() == key for value in identifiers)
        # kmod may report the PKCS#7 issuer and serial instead of the key ID.
        serial = re.search(r'^serial=([0-9a-f]+)$', text, re.MULTILINE | re.IGNORECASE)
        issuer_cn = re.search(r'^\s*commonName\s*=\s*(.+)$', text, re.MULTILINE)
        matches_issuer_serial = (
            serial and issuer_cn and int(serial[1], 16) == int(key, 16)
            and command('modinfo', '-F', 'signer', module) == issuer_cn[1].strip()
        )
        if matches_key_id or matches_issuer_serial:
            check_enrollment(name)
            print('Secure Boot trusts matching DKMS certificate ' + name)
            return
    raise RuntimeError('No known DKMS certificate matches the module signing key')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    args = parser.parse_args()
    try:
        require_trusted_module(args.module)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
