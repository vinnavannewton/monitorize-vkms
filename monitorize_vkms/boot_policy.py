"""Pure validation for the fixed protected remote-login display."""
from __future__ import annotations

import re
import math

MODE = [1920, 1080, 60]


def validate_mode(mode):
    if (not isinstance(mode,(list,tuple)) or len(mode)!=3 or
        type(mode[0]) is not int or type(mode[1]) is not int or
        not 1<=mode[0]<=4095 or not 1<=mode[1]<=4095 or
        type(mode[2]) not in (int,float) or not math.isfinite(mode[2]) or not 24<=mode[2]<=240):
        raise ValueError('Invalid desktop display mode.')
    return list(mode)


def validate_protected(lease: dict) -> None:
    if (not isinstance(lease, dict) or type(lease.get('schema_version')) is not int
            or lease['schema_version'] != 2 or lease.get('protected') is not True
            or lease.get('owner_kind') != 'remote-login'
            or type(lease.get('uid')) is not int or lease['uid'] <= 0
            or lease.get('phase') not in ('greeter', 'desktop')
            or not isinstance(lease.get('session'), str)
            or (not lease['session'] and lease['phase'] != 'greeter')
            or not isinstance(lease.get('boot_id'), str) or not lease['boot_id']
            or not isinstance(lease.get('generation'), str)
            or not re.fullmatch(r'[0-9a-f]{32}', lease['generation'])
            or not isinstance(lease.get('edid_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', lease['edid_sha256'])
            or (lease.get('phase')=='greeter' and lease.get('mode') != MODE)
            or lease.get('state') not in ('reserved', 'pending', 'active', 'released')):
        raise ValueError('Invalid protected remote-login ownership state.')
    validate_mode(lease.get('mode'))
    identity_present = any(k in lease for k in ('connector', 'card', 'connector_id'))
    if identity_present or lease['state'] == 'active':
        if (not isinstance(lease.get('connector'), str) or not re.fullmatch(r'Virtual-[0-9]+', lease['connector'])
                or not isinstance(lease.get('card'), str) or not re.fullmatch(r'card[0-9]+', lease['card'])
                or type(lease.get('connector_id')) is not int or lease['connector_id'] <= 0):
            raise ValueError('Protected display has no reliable connector identity.')


def validate_transition(lease: dict, operation: str, *, uid: int, session: str,
                        boot_id: str, generation: str) -> dict:
    validate_protected(lease)
    if (type(uid) is not int or uid != lease['uid'] or session != lease['session']
            or boot_id != lease['boot_id'] or generation != lease['generation']):
        raise PermissionError('Remote-login display ownership changed.')
    states = {'prepare': 'pending', 'activated': 'active', 'idle': 'reserved', 'release': 'released'}
    if operation not in states or lease['state'] == 'released':
        raise ValueError('Unsupported protected display transition.')
    result = dict(lease, state=states[operation])
    validate_protected(result)
    return result
