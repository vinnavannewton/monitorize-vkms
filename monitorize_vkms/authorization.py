"""Root-owned enrollment for bounded, caller-owned VKMS operations."""
import json
import os
from pathlib import Path
import pwd
import stat
import tempfile

DIRECTORY = Path('/var/lib/monitorize-vkms/authorization')
VERSION = 1
OWNER_UID = 0


def identity(uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError('A non-root Unix user is required.')
    account = pwd.getpwuid(uid)
    home = Path(account.pw_dir).stat()
    if home.st_uid != uid or not stat.S_ISDIR(home.st_mode):
        raise PermissionError('The user home directory must belong to that user.')
    return dict(uid=uid, name=account.pw_name, home=account.pw_dir,
                home_device=home.st_dev, home_inode=home.st_ino)


def _directory(create=False):
    parent = DIRECTORY.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != OWNER_UID or parent.st_mode & 0o022:
        raise PermissionError('Insecure VKMS state directory.')
    if create:
        DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != OWNER_UID or info.st_mode & 0o077:
        raise PermissionError('Insecure VKMS authorization directory.')


def read(uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError('A non-root Unix user is required.')
    try:
        _directory()
        fd = os.open(DIRECTORY / f'{uid}.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != OWNER_UID or info.st_mode & 0o077 or info.st_size > 4096:
            raise PermissionError('Insecure VKMS authorization record.')
        record = json.load(stream)
    current = identity(uid)
    if (not isinstance(record, dict) or type(record.get('version')) is not int or record.get('version') != VERSION
            or type(record.get('enabled')) is not bool or record.get('identity') != current):
        raise PermissionError('Invalid or stale VKMS authorization. Run setup again.')
    return record


def state(uid):
    record = read(uid)
    return dict(success=True, authorization_version=VERSION,
                unattended_enabled=bool(record and record['enabled']))


def set_enabled(uid, enabled):
    if os.geteuid() != 0 or type(enabled) is not bool:
        raise PermissionError('Administrator authorization is required.')
    current = identity(uid)
    _directory(create=True)
    # Revocation retains a disabled tombstone solely for existing-lease cleanup.
    record = dict(version=VERSION, identity=current, enabled=enabled)
    fd, temporary = tempfile.mkstemp(prefix='.authorization-', dir=DIRECTORY)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, DIRECTORY / f'{uid}.json')
        directory_fd = os.open(DIRECTORY, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return state(uid)
