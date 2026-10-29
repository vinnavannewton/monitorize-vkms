"""Root-only native package hooks. Never enroll remote login or unload a GPU."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitorize_vkms.boot_runtime import read_enrollment
from monitorize_vkms.lease import read_lease


def assert_removal_safe() -> None:
    from monitorize_vkms.recovery import check_admission
    check_admission()
    settings = read_enrollment()
    value = read_lease(Path('/run/monitorize-vkms'), 'mon1')
    if (settings and settings['enabled']) or (value and value.get('protected') is True):
        raise RuntimeError('Disable remote login and release its reserved display in Monitorize Settings before removing or downgrading the host package.')


def release_version(value):
    """Accept the same MAJOR.MINOR[.PATCH] syntax as VERSION/bump-version."""
    import re
    raw = value.split('-', 1)[0]
    if not re.fullmatch(r'[0-9]+\.[0-9]+(?:\.[0-9]+)?', raw):
        return None
    return (tuple(map(int, raw.split('.'))) + (0,))[:3]


def installed_release():
    path = Path('/usr/lib/monitorize-vkms/VERSION')
    if not path.exists():
        return None
    from monitorize_vkms.boot_runtime import require_installed
    require_installed(path)
    if path.stat().st_size > 128:
        raise ValueError('Invalid installed host version.')
    version = release_version(path.read_text().strip())
    if version is None:
        raise ValueError('Invalid installed host version; repair it before upgrading.')
    return version


def assert_upgrade_safe(target_version):
    from monitorize_vkms.recovery import check_admission
    check_admission()
    target = release_version(target_version or '')
    # Compatible forward upgrades do not unload a module, unplug a display or
    # re-enroll remote login. Destructive/unknown targets still require release.
    current = installed_release()
    if target is None or target < (0, 3, 0) or (current is not None and target < current):
        assert_removal_safe()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pre-remove', 'pre-upgrade', 'post-install', 'post-remove'))
    parser.add_argument('--target-version')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise PermissionError('Package maintenance requires root.')
    from monitorize_vkms import __version__
    if args.action == 'pre-remove':
        assert_removal_safe()
    elif args.action == 'pre-upgrade':
        assert_upgrade_safe(args.target_version)
    elif args.action == 'post-install':
        subprocess.run(['dkms', 'install', '-m', 'monitorize-vkms', '-v', __version__, '-k', os.uname().release], check=True)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        # Bootstrap only affects the next boot. Do not start it against a live GPU.
        subprocess.run(['systemctl', 'enable', 'monitorize-vkms-bootstrap.service'], check=True)
    elif args.action == 'post-remove':
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
