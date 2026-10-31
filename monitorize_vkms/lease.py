"""Bounded private lease persistence. Callers serialize mutations with the lifecycle lock."""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import stat
from monitorize_vkms.names import named_slot

OWNER_UID = 0
MAX_LEASE_BYTES = 65536


def _path(state: Path, slot: str) -> Path:
    if named_slot(slot):
        return Path(state) / (slot + '.json')
    if slot not in ('mon1', 'mon2'):
        raise ValueError('Invalid Monitorize display identity.')
    return Path(state) / ('primary.json' if slot == 'mon1' else 'mon2.json')


def _directory(state: Path) -> None:
    info = state.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != OWNER_UID or info.st_mode & 0o077:
        raise ValueError('Monitorize VKMS runtime state directory has unsafe ownership or permissions.')


def read_lease(state: Path, slot: str) -> dict | None:
    path = _path(state, slot)
    try:
        _directory(Path(state))
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != OWNER_UID
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_LEASE_BYTES):
            raise ValueError('Monitorize VKMS ownership state has unsafe permissions or size.')
        raw = source.read(MAX_LEASE_BYTES + 1)
        if len(raw) > MAX_LEASE_BYTES:
            raise ValueError('Monitorize VKMS ownership state is too large.')
        value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Monitorize VKMS ownership state is not an object.')
    if value.get('protected') is not None:
        from .boot_policy import validate_protected
        validate_protected(value)
        if slot != 'mon1':
            raise ValueError('Only mon1 may have a remote-login reservation.')
    return value


def write_lease(state: Path, slot: str, lease: dict) -> None:
    path = _path(state, slot)
    if not isinstance(lease, dict):
        raise ValueError('Ownership state must be an object.')
    if lease.get('protected') is not None:
        from .boot_policy import validate_protected
        validate_protected(lease)
        if slot != 'mon1':
            raise ValueError('Only mon1 may have a remote-login reservation.')
    data = json.dumps(lease, allow_nan=False).encode()
    if len(data) > MAX_LEASE_BYTES:
        raise ValueError('Monitorize VKMS ownership state is too large.')
    state = Path(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    _directory(state)
    temporary = state / ('.' + slot + '.' + secrets.token_hex(8) + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _sync(state)
    finally:
        temporary.unlink(missing_ok=True)


def _sync(state: Path) -> None:
    fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_lease(state: Path, slot: str) -> None:
    path = _path(state, slot)
    if read_lease(state, slot) is not None:
        path.unlink()
        _sync(Path(state))


def named_slots(state: Path, uid=None):
    """Enumerate validated private record names; callers hold the lifecycle lock."""
    from monitorize_vkms.names import slot_uid
    try:_directory(Path(state))
    except FileNotFoundError:return []
    result=[]
    for path in Path(state).glob('named-*.json'):
        slot=path.stem
        if named_slot(slot) and (uid is None or slot_uid(slot)==uid):
            result.append(slot)
    return sorted(result)
