#!/usr/bin/python3 -I
"""Fixed-purpose administrator-authorized VKMS enrollment command."""
import json
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib' / 'monitorize-vkms'))
from monitorize_vkms.authorization import set_enabled


def main():
    if os.geteuid() != 0:
        raise PermissionError('Administrator authorization is required.')
    uid = os.environ.get('PKEXEC_UID', '')
    if not uid.isdecimal() or len(sys.argv) != 2 or sys.argv[1] not in ('enable', 'disable'):
        raise ValueError('Invoke through the authorized setup action with enable or disable.')
    result = set_enabled(int(uid), sys.argv[1] == 'enable')
    result['message'] = 'Unattended VKMS displays enabled.' if result['unattended_enabled'] else 'Unattended VKMS displays disabled. Existing displays can still clean up.'
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(success=False, message=str(exc))))
        raise SystemExit(1)
