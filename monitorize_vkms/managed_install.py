"""Explicit GUI-authorized source installation, retaining remote-login lifecycle."""
import argparse
import fcntl
import inspect
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


class ExpiredMaintenance(RuntimeError):
    """Structured request for a user-approved recovery, never a shell command."""


def check_receipt(lifecycle, uid):
    receipt = lifecycle.load()
    if receipt and receipt['uid'] != uid:
        raise PermissionError('Maintenance belongs to another account.')
    return receipt


def resume_expired(lifecycle, uid):
    receipt = check_receipt(lifecycle, uid)
    if receipt:
        if receipt['boot_id'] == lifecycle.backend.boot_id:
            raise RuntimeError('Maintenance changed during recovery. Refresh status before retrying.')
        lifecycle.resume()
    return dict(success=True, message='Expired maintenance cleared. Retrying VKMS operation…')


def check_idle():
    """Called by install.sh while its global lifecycle lock is held."""
    sys.path.insert(0, str(ROOT))
    from monitorize_vkms.lease import read_lease, named_slots
    for slot in ('mon1', 'mon2', *named_slots(Path('/run/monitorize-vkms'))):
        if read_lease(Path('/run/monitorize-vkms'), slot) is not None:
            raise RuntimeError('A VKMS display is still owned. Stop it before updating.')
    for connector in Path('/sys/class/drm').glob('card*-*'):
        if '/faux/monitorize' in str((connector / 'device').resolve()):
            if (connector / 'status').read_text().strip() == 'connected':
                raise RuntimeError('A VKMS display is connected. Stop it before updating.')
    active = subprocess.run(['/usr/bin/systemctl', 'list-units', '--all', '--no-legend',
        '--plain', '--no-pager', '--state=active,activating,deactivating',
        'monitorize-remote-login*.service'], capture_output=True, text=True, check=True, timeout=10)
    if active.stdout.strip():
        raise RuntimeError('Remote-login services must finish pausing before updating.')


def validate_source(revision, uid):
    """Run Git only as the invoking user, never with root Git configuration."""
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid source revision.')
    own_paths = (ROOT, ROOT / 'install.sh', Path(__file__))
    for path in (*own_paths, *ROOT.parents):
        info = path.lstat()
        owners = (uid,) if path in own_paths else (0, uid)
        if info.st_uid not in owners or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Use an unmodified, caller-owned source checkout.')
    user = pwd.getpwuid(uid)
    env = {'PATH': '/usr/bin:/bin', 'HOME': user.pw_dir, 'GIT_TERMINAL_PROMPT': '0',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    def git(*args):
        return subprocess.run(['/usr/bin/git', '-C', str(ROOT), *args], user=uid,
            group=user.pw_gid, extra_groups=[], env=env, capture_output=True, text=True,
            check=True, timeout=15).stdout.strip()
    origin = git('remote', 'get-url', 'origin')
    allowed = ('https://github.com/vinnavannewton/monitorize-vkms',
               'https://github.com/vinnavannewton/monitorize-vkms.git',
               'git@github.com:vinnavannewton/monitorize-vkms.git',
               'ssh://git@github.com/vinnavannewton/monitorize-vkms.git')
    if (origin not in allowed or git('branch', '--show-current') != 'main'
            or git('rev-parse', 'HEAD') != revision
            or git('status', '--porcelain', '--untracked-files=normal')):
        raise ValueError('The source checkout changed after Update. Retry from a clean official main checkout.')


def prepare_lifecycle(lifecycle, uid):
    """Accept older installed helpers without skipping their ownership guards."""
    current, protected = lifecycle.backend.state()
    if current and current['uid'] != uid:
        raise PermissionError('Only the enrolled account can update this host.')
    if 'include_idle_services' in inspect.signature(lifecycle.prepare).parameters:
        lifecycle.prepare('upgrade', include_idle_services=True)
        return
    # Old helpers can resume a durable pause receipt even for disabled settings,
    # but cannot initiate one for leftover idle services. Seed the same receipt
    # as modern prepare(), then retain the installed helper's pause/release and
    # recovery checks. Never change enrollment or force a client disconnect.
    if current and not current['enabled'] and not protected:
        units = ['monitorize-remote-login.service', 'monitorize-vkms-boot-display.service',
                 *(f'monitorize-remote-login-{phase}@{uid}.service' for phase in ('boot', 'desktop'))]
        if any(lifecycle.backend.active(name) for name in units):
            if lifecycle.load() is None:
                if lifecycle.backend.connection_in_use(uid):
                    raise RuntimeError('A remote-login client is connected, or its connection state is unavailable. '
                                       'Disconnect it before retrying VKMS Update. Pairing is retained.')
                lifecycle.save(dict(uid=uid, operation='upgrade', boot_id=lifecycle.backend.boot_id,
                    restart=lifecycle.backend.active(units[0]),
                    masks=[name for name in units if not lifecycle.backend.masked(name)], stage='pausing'))
    lifecycle.prepare('upgrade')


def installed_lifecycle(uid):
    """Use the existing root-installed pause/release/resume implementation."""
    settings = Path('/etc/monitorize/remote-login.json')
    if not settings.exists():
        return None
    library = Path('/usr/lib/monitorize-remote-login')
    for target in (library, library / 'monitorize/remote_login/package_lifecycle.py'):
        info = target.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Update the installed Monitorize remote-login system helpers first.')
    sys.path.insert(0, str(library))
    from monitorize.remote_login.package_lifecycle import PackageLifecycle, InstalledBackend
    lifecycle = PackageLifecycle(InstalledBackend())
    current, _ = lifecycle.backend.state()
    if current and current['uid'] != uid:
        raise PermissionError('Only the enrolled account can recover this host.')
    return lifecycle


def remote_lifecycle(uid):
    lifecycle = installed_lifecycle(uid)
    if lifecycle is None:
        return None
    from monitorize.remote_login.live_setup import setup_lock
    with setup_lock():
        prepare_current(lifecycle, uid)
    return lifecycle


def prepare_current(lifecycle, uid):
    receipt = check_receipt(lifecycle, uid)
    if receipt and receipt['boot_id'] != lifecycle.backend.boot_id:
        raise ExpiredMaintenance('Remote-login maintenance from a previous boot needs recovery before continuing.')
    prepare_lifecycle(lifecycle, uid)


def recover_expired(revision, uid):
    validate_source(revision, uid)
    # Serialize with other setup/package recovery and revalidate the receipt
    # after authorization. Never resume a new current-boot transaction by race.
    lifecycle = installed_lifecycle(uid)
    if lifecycle is None:
        return dict(success=True, message='No maintenance remains.')
    from monitorize.remote_login.live_setup import setup_lock
    with setup_lock():
        current, _ = lifecycle.backend.state()
        if current and current['uid'] != uid:
            raise PermissionError('Enrollment changed during maintenance recovery.')
        return resume_expired(lifecycle, uid)


def install(revision, uid):
    if not Path('/usr/lib/monitorize-vkms/source-checkout').is_file():
        raise ValueError('Monitorize VKMS is no longer installed. Use the GitHub installation page.')
    validate_source(revision, uid)
    lifecycle = remote_lifecycle(uid)
    service = subprocess.run(['/usr/bin/systemctl', 'is-active', 'monitorize-vkms.service'],
        capture_output=True, text=True, check=False, timeout=10)
    try:
        # Never stop a shared service until ownership has been checked under
        # the same lock used by display creation. The installer rechecks later.
        fd = os.open('/run/lock/monitorize-vkms.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o022:
                raise ValueError('Unsafe VKMS lifecycle lock.')
            fcntl.flock(fd, fcntl.LOCK_EX)
            check_idle()
            subprocess.run(['/usr/bin/systemctl', 'stop', 'monitorize-vkms.service'], check=True, timeout=45)
        finally:
            os.close(fd)
        result = subprocess.run(['/usr/bin/bash', str(ROOT / 'install.sh'), '--managed-update'], cwd=ROOT)
        if result.returncode:
            return dict(success=False, partial=True, cleanup_pending=True,
                        message='VKMS installation failed. Review the log before retrying.')
        if service.returncode == 0:
            subprocess.run(['/usr/bin/systemctl', 'start', 'monitorize-vkms.service'], check=True)
        marker = Path('/var/lib/monitorize-vkms/reboot-required.json')
        reboot = marker.is_file() and json.loads(marker.read_text()).get('boot_id') == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if lifecycle and not reboot:
            lifecycle.resume()
        return dict(success=True, reboot_required=reboot, cleanup_pending=bool(lifecycle and reboot),
                    message='VKMS updated. System restart required.' if reboot else 'VKMS updated. Checking installation…')
    except Exception as error:
        return dict(success=False, partial=True, cleanup_pending=True,
                    message='VKMS update needs recovery: ' + str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'check-idle', 'resume-expired'))
    parser.add_argument('--revision')
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise PermissionError('Administrator authorization is required.')
        if args.action == 'check-idle':
            check_idle()
            return 0
        uid = int(os.environ.get('PKEXEC_UID', '0'))
        if uid <= 0:
            raise PermissionError('Start Update from the desktop application.')
        print(json.dumps(dict(management_started=True)), flush=True)
        result = (recover_expired(args.revision or '', uid) if args.action == 'resume-expired'
                  else install(args.revision or '', uid))
    except ExpiredMaintenance as error:
        result = dict(success=False, cleanup_pending=True,
                      recovery_required='resume_expired', message=str(error))
    except Exception as error:
        result = dict(success=False, cleanup_pending=Path('/var/lib/monitorize/remote-login-package.json').exists(), message=str(error))
    print(json.dumps(result), flush=True)
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
