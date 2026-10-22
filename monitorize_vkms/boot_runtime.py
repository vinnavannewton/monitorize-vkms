"""Installed remote-login display effects, separate from the pure lease policy."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import subprocess

from .boot_policy import MODE, validate_protected
from .edid import generate_edid
from .lease import read_lease, write_lease

ENROLLMENT = Path('/etc/monitorize/remote-login.json')


def require_installed(path: Path) -> None:
    for item in (Path(path), *Path(path).parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise PermissionError('Remote login requires trusted root-installed code and paths.')


def read_enrollment(path: Path = ENROLLMENT) -> dict | None:
    try:
        require_installed(path.parent)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or info.st_nlink != 1 or info.st_size > 4096):
            raise ValueError('Unsafe remote-login enrollment settings.')
        raw = source.read(4097)
    if len(raw) > 4096:
        raise ValueError('Remote-login enrollment is too large.')
    settings = json.loads(raw)
    if (not isinstance(settings, dict) or type(settings.get('schema_version')) is not int
            or settings['schema_version'] != 1 or type(settings.get('enabled')) is not bool
            or type(settings.get('uid')) is not int or settings['uid'] <= 0
            or type(settings.get('gid')) is not int or settings['gid'] < 0):
        raise ValueError('Invalid remote-login enrollment settings.')
    account = pwd.getpwuid(settings['uid'])
    if (account.pw_name != settings.get('username') or account.pw_gid != settings['gid']
            or account.pw_dir != settings.get('home')):
        raise ValueError('Remote-login enrollment account changed.')
    return settings


def local_sessions() -> list[dict]:
    result = subprocess.run(['/usr/bin/loginctl', 'list-sessions', '--json=short'],
                            capture_output=True, text=True, timeout=3, check=True)
    if len(result.stdout) > 65536:
        raise RuntimeError('Too many sessions to verify boot ownership.')
    values = json.loads(result.stdout)
    if not isinstance(values, list) or len(values) > 32 or any(not isinstance(v, dict) for v in values):
        raise ValueError('Invalid login session snapshot.')
    return values


def no_seat_owner(sessions: list[dict], *, allow_greeter: bool = False) -> None:
    allowed = {'manager'} | ({'greeter'} if allow_greeter else set())
    if any(not isinstance(row, dict) or (row.get('seat') == 'seat0' and row.get('class') not in allowed)
           for row in sessions):
        raise RuntimeError('A graphical seat already has an owner.')


def _attribute(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Unexpected configfs attribute or symlink.')


def connector_ready(connector: Path) -> None:
    for name in ('status', 'enabled', 'dynamic', 'edid', 'edid_enabled'):
        _attribute(connector / name)
    if (connector / 'enabled').read_text().strip() != '1' or (connector / 'dynamic').read_text().strip() != '1':
        raise RuntimeError('Persistent Monitorize connector is not initialized.')


def program_edid(connector: Path, edid: bytes) -> None:
    connector_ready(connector)
    if (connector / 'status').read_text().strip() != '2':
        raise RuntimeError('Refusing EDID replacement on a connected output.')
    # O_NOFOLLOW also protects the final open, not only the preceding lstat.
    for name, data in (('edid', edid), ('edid_enabled', b'1')):
        fd = os.open(connector / name, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)


def reserve_boot_output(*, state: Path, connector: Path, uid: int,
                        boot_id: str, sessions: list[dict]) -> dict:
    if type(uid) is not int or uid <= 0 or not isinstance(boot_id, str) or not boot_id:
        raise ValueError('Invalid boot display owner.')
    no_seat_owner(sessions)
    connector_ready(connector)
    if (connector / 'status').read_text().strip() != '2':
        raise RuntimeError('Boot output is already connected; refusing reservation.')
    edid = generate_edid(*MODE, 'mon1')
    digest = hashlib.sha256(edid).hexdigest()
    previous = read_lease(state, 'mon1')
    if previous is not None:
        if previous.get('protected') is not True:
            raise RuntimeError('Boot output already belongs to an ordinary display.')
        validate_protected(previous)
        if (previous['uid'] != uid or previous['boot_id'] != boot_id
                or previous['phase'] != 'greeter' or previous['session']
                or previous['state'] not in ('reserved', 'pending') or previous['edid_sha256'] != digest):
            raise RuntimeError('Boot output already belongs to another lifecycle.')
        value = dict(previous, state='pending')
    else:
        value = dict(schema_version=2, protected=True, owner_kind='remote-login', uid=uid,
                     phase='greeter', session='', boot_id=boot_id, generation=secrets.token_hex(16),
                     mode=list(MODE), edid_sha256=digest, state='pending')
    write_lease(state, 'mon1', value)
    program_edid(connector, edid)
    value['state'] = 'reserved'
    write_lease(state, 'mon1', value)
    return value
