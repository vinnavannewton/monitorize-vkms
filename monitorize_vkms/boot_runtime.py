"""Installed remote-login display effects, separate from the pure lease policy."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import time

from .boot_policy import MODE, validate_protected
from .edid import generate_edid
from .lease import read_lease, write_lease
from .drm import monitorize_drm_connectors as discover

ENROLLMENT = Path('/etc/monitorize/remote-login.json')
CGROUP_ROOT = Path('/sys/fs/cgroup')
IDENTITY_ROOT = Path('/var/lib/monitorize/remote-login')


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


def desktop_context(uid: int) -> str:
    """Resolve the sole local seat0 desktop without trusting a client session ID."""
    from . import helper
    if type(uid) is not int or uid <= 0:
        raise PermissionError('Invalid enrolled account.')
    session, environment = helper._active_session(uid)
    users = [row for row in local_sessions() if row.get('seat') == 'seat0'
             and row.get('class') not in ('manager', 'greeter')]
    if (environment.get('XDG_SESSION_TYPE') != 'wayland' or len(users) != 1
            or users[0].get('class') not in ('user', 'user-early', 'user-incomplete')
            or users[0].get('uid') != uid or users[0].get('session') != session):
        raise PermissionError('A unique enrolled local Wayland desktop is required.')
    return session


def verify_desktop_reservation(*, connector: Path, lease: dict, uid: int,
                               session: str, boot_id: str, context_check) -> dict:
    """Read-only validation; callers hold the lifecycle lock."""
    context_check()
    validate_protected(lease)
    if (lease['uid'] != uid or lease['session'] != session or lease['boot_id'] != boot_id
            or lease['phase'] != 'desktop' or lease['state'] not in ('reserved', 'active')):
        raise PermissionError('Live reservation belongs to another lifecycle or needs recovery.')
    connector_ready(connector)
    if hashlib.sha256((connector/'edid').read_bytes()).hexdigest() != lease['edid_sha256']:
        raise PermissionError('Live reservation EDID changed.')
    status = (connector/'status').read_text().strip()
    if lease['state'] == 'active':
        if status != '1':
            raise PermissionError('Active live reservation is disconnected.')
        output = _output(lease)
        if (Path(output['path'])/'enabled').read_text().strip() != 'enabled':
            raise PermissionError('Live reservation is not enabled.')
    elif status != '2':
        raise PermissionError('Disconnected reservation unexpectedly became connected.')
    context_check()
    return lease


def reserve_desktop_output(*, state: Path, connector: Path, uid: int, session: str,
                           boot_id: str, generation: str, context_check) -> dict:
    """Prepare an owned, disconnected monitor in an authenticated desktop."""
    if (type(uid) is not int or uid <= 0 or not isinstance(session, str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session) or not boot_id
            or not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{32}', generation)):
        raise ValueError('Invalid live reservation context.')
    context_check()
    connector_ready(connector)
    previous = read_lease(state, 'mon1')
    if previous is not None:
        validate_protected(previous)
        if (previous['uid'] != uid or previous['session'] != session
                or previous['boot_id'] != boot_id or previous['phase'] != 'desktop'
                or previous['generation'] != generation):
            raise PermissionError('Display slot belongs to another lifecycle.')
        if previous['state'] != 'pending':
            return verify_desktop_reservation(connector=connector, lease=previous, uid=uid,
                session=session, boot_id=boot_id, context_check=context_check)
    if (connector/'status').read_text().strip() != '2':
        raise RuntimeError('Live reservation requires a disconnected unused display slot.')
    edid = generate_edid(*MODE, 'mon1')
    digest = hashlib.sha256(edid).hexdigest()
    if previous is not None and (previous['edid_sha256'] != digest or previous['mode'] != MODE):
        raise PermissionError('Pending live reservation has a different display mode.')
    value = dict(schema_version=2, protected=True, owner_kind='remote-login', uid=uid,
                 phase='desktop', session=session, boot_id=boot_id, generation=generation,
                 mode=list(MODE), edid_sha256=digest, state='pending')
    context_check()
    write_lease(state, 'mon1', value)
    program_edid(connector, edid)
    if (connector/'edid').read_bytes() != edid:
        raise RuntimeError('Live reservation EDID readback failed.')
    context_check()
    value['state'] = 'reserved'
    write_lease(state, 'mon1', value)
    return value


def _same_output(lease: dict, output: dict) -> None:
    if (output.get('display') != 'mon1' or output.get('edid_sha256') != lease['edid_sha256']
            or type(output.get('connector_id')) is not int or output['connector_id'] <= 0
            or any(lease.get(key) is not None and lease[key] != output.get(target)
                   for key, target in (('connector', 'name'), ('card', 'card'), ('connector_id', 'connector_id')))):
        raise PermissionError('Reserved DRM connector identity changed.')


def _output(lease: dict) -> dict:
    matches = [value for value in discover().values() if value.get('display') == 'mon1']
    if len(matches) != 1:
        raise RuntimeError('The exact reserved DRM output is not available.')
    _same_output(lease, matches[0])
    return matches[0]


def set_status(connector: Path, value: str) -> None:
    connector_ready(connector)
    if value not in ('1', '2'):
        raise ValueError('Invalid connector status.')
    fd = os.open(connector / 'status', os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'w') as target:
        target.write(value)


def reconnect_reserved(*, state: Path, generation: str, boot_id: str, connector: Path) -> dict:
    """Connect only the already-owned reservation; caller holds the lifecycle lock."""
    value = read_lease(state, 'mon1')
    validate_protected(value)
    if value['generation'] != generation or value['boot_id'] != boot_id or value['state'] == 'released':
        raise PermissionError('Remote-login reservation is stale.')
    connector_ready(connector)
    if hashlib.sha256((connector / 'edid').read_bytes()).hexdigest() != value['edid_sha256']:
        raise PermissionError('Reserved configfs EDID changed.')
    if (connector / 'status').read_text().strip() == '1':
        _output(value)
    value['state'] = 'pending'
    write_lease(state, 'mon1', value)
    set_status(connector, '1')
    return value


def change_reserved_mode(*,state,connector,lease,mode):
    """Caller authenticates an idle desktop runner or holds capture quiescence."""
    from .boot_policy import validate_mode
    mode=validate_mode(mode);validate_protected(lease)
    if mode==lease['mode']:return lease
    if lease['phase']!='desktop':raise PermissionError('Only desktop mode may be resized.')
    edid=generate_edid(*mode,'mon1')  # Reject unsupported timing before any unplug.
    connector_ready(connector)
    old_edid=(connector/'edid').read_bytes()
    if hashlib.sha256(old_edid).hexdigest()!=lease['edid_sha256']:
        raise PermissionError('Reserved EDID changed before resize.')
    if (connector/'status').read_text().strip()=='1':_output(lease)
    set_status(connector,'2')
    updated=dict(lease,mode=mode,edid_sha256=hashlib.sha256(edid).hexdigest(),state='reserved')
    try:
        program_edid(connector,edid)
        write_lease(state,'mon1',updated)
    except Exception:
        program_edid(connector,old_edid)
        write_lease(state,'mon1',dict(lease,state='reserved'))
        raise
    return updated


def prepare_reserved(*, state: Path, connector: Path, lease: dict, activate,
                     context_check) -> dict:
    """Connect, run the verified compositor worker, then verify exact enabled output."""
    context_check()
    started = time.monotonic()
    value = reconnect_reserved(state=state, generation=lease['generation'],
                               boot_id=lease['boot_id'], connector=connector)
    deadline = started + 12
    while True:
        context_check()
        try:
            output = _output(value)
            break
        except PermissionError:
            raise
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise RuntimeError('Reserved DRM connector did not appear before the deadline.')
            time.sleep(0.1)
    value.update(connector=output['name'], card=output['card'], connector_id=output['connector_id'])
    write_lease(state, 'mon1', value)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError('Reserved output activation timed out.')
    result = activate(output, remaining)
    context_check()
    if not isinstance(result, dict) or result.get('success') is not True:
        raise RuntimeError('Reserved output activation result did not confirm success.')
    details = result.get('details')
    if not isinstance(details, dict):
        raise RuntimeError('Reserved output activation result omitted verified mode details.')
    rate = details.get('refresh_rate')
    if (details.get('width') != value['mode'][0] or details.get('height') != value['mode'][1]
            or type(rate) not in (int, float) or not math.isfinite(rate)
            or abs(rate - value['mode'][2]) > 0.75):
        raise RuntimeError('Reserved output activation result has the wrong mode: '
                           + str(details.get('width')) + 'x' + str(details.get('height'))
                           + '@' + str(rate) + '; required '+str(value['mode'])+'.')
    # Mutter's logical layout can be confirmed before the asynchronous DRM
    # commit becomes visible in sysfs. Keep the original bounded deadline and
    # recheck ownership and exact connector identity during that final commit.
    while True:
        context_check()
        current = _output(value)
        enabled = (Path(current['path']) / 'enabled').read_text().strip()
        remaining = deadline - time.monotonic()
        if remaining < 0:
            raise RuntimeError('Reserved output kernel activation timed out: status='
                               + str(current.get('status')) + ', enabled=' + enabled
                               + '; compositor verified '+str(value['mode'])+'; refresh='+str(rate)+'.')
        if current.get('status') == 'connected' and enabled == 'enabled':
            break
        time.sleep(min(0.05, remaining))
    value['state'] = 'active'
    write_lease(state, 'mon1', value)
    return value


def idle_reserved(*, state: Path, connector: Path, lease: dict) -> dict:
    """Caller holds lifecycle and identity locks and has verified capture exit."""
    validate_protected(lease)
    connector_ready(connector)
    if hashlib.sha256((connector / 'edid').read_bytes()).hexdigest() != lease['edid_sha256']:
        raise PermissionError('Reserved configfs EDID changed.')
    if (connector / 'status').read_text().strip() == '1':
        _output(lease)
    value = dict(lease, state='pending')
    write_lease(state, 'mon1', value)
    # Unlike ordinary remove, the protected lifecycle may unplug its sole output.
    # Kernel hotplug removes this connector from the compositor while retaining
    # registration and EDID. No physical output layout is rewritten here.
    set_status(connector, '2')
    if (connector / 'status').read_text().strip() != '2':
        raise RuntimeError('Reserved display did not disconnect.')
    value['state'] = 'reserved'
    write_lease(state, 'mon1', value)
    return value


def unit_properties(unit: str) -> dict:
    result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--no-pager',
                             '--property=MainPID', '--property=ControlGroup',
                             '--property=ActiveState', '--property=LoadState'],
                            capture_output=True, text=True, timeout=3, check=True)
    if len(result.stdout) > 8192:
        raise RuntimeError('Unexpected systemd unit response.')
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def runner_identity(pid: int, uid: int, phase: str) -> bool:
    try:
        process = Path('/proc') / str(pid)
        executable = (process / 'exe').resolve(strict=True)
        require_installed(executable)
        args = (process / 'cmdline').read_bytes().split(b'\0')
        return (process.stat().st_uid == uid and executable.name.startswith('python3')
                and args[1:] == [b'-I', b'/usr/libexec/monitorize/remote-login/entry.py',
                                  phase.encode(), b''])
    except (OSError, ValueError):
        return False


def verify_engine_cgroups(uid: int) -> None:
    for phase in ('boot', 'desktop'):
        unit = unit_properties(f'monitorize-remote-login-{phase}@{uid}.service')
        if unit.get('LoadState') == 'not-found':
            continue
        pid = int(unit.get('MainPID', '-1'))
        group = unit.get('ControlGroup', '')
        if pid == 0 and unit.get('ActiveState') in ('inactive', 'failed') and not group:
            continue
        if pid <= 0 and unit.get('ActiveState') not in ('inactive', 'failed'):
            raise RuntimeError('Managed engine has no reliable runner PID.')
        if (not group.startswith('/') or group == '/' or '..' in group.split('/')
                or '//' in group):
            raise ValueError('Cannot verify managed capture cgroup.')
        directory = CGROUP_ROOT / group.lstrip('/')
        # A vanished cgroup is acceptable only for a stopped unit.
        if not directory.exists() and pid == 0 and unit.get('ActiveState') in ('inactive', 'failed'):
            continue
        processes = set()
        paths = [directory / 'cgroup.procs', *directory.rglob('cgroup.procs')]
        if len(paths) > 1024:
            raise RuntimeError('Too many managed capture cgroups.')
        for path in paths:
            processes.update(int(value) for value in path.read_text().split())
        if pid == 0 and unit.get('ActiveState') in ('inactive', 'failed') and not processes:
            continue
        if pid <= 0 or processes != {pid}:
            raise RuntimeError('Capture processes remain; output retained.')
        if not runner_identity(pid, uid, phase):
            raise PermissionError('Managed engine runner identity changed.')


@contextmanager
def quiescent(uid: int):
    """Hold the authoritative identity lock across a verified idle/release mutation."""
    require_installed(IDENTITY_ROOT)
    directory = IDENTITY_ROOT / str(uid)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise PermissionError('Unsafe managed engine identity directory.')
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open('engine.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or info.st_mode & 0o077:
            raise PermissionError('Unsafe managed engine identity lock.')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_engine_cgroups(uid)
        yield
        verify_engine_cgroups(uid)
    finally:
        os.close(fd)


def verify_quiescent(uid: int) -> None:
    with quiescent(uid):
        pass


def greeter_context() -> tuple[int, str]:
    sessions = local_sessions()
    no_seat_owner(sessions, allow_greeter=True)
    candidates = [row for row in sessions if row.get('seat') == 'seat0' and row.get('class') == 'greeter']
    if len(candidates) != 1 or type(candidates[0].get('uid')) is not int:
        raise PermissionError('A unique active local greeter is required.')
    row = candidates[0]
    from . import helper
    session, environment = helper._active_session(row['uid'])
    if session != row.get('session') or environment.get('XDG_SESSION_TYPE') != 'wayland':
        raise PermissionError('The graphical greeter changed.')
    return row['uid'], session
