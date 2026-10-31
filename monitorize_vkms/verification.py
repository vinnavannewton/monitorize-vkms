"""Passive installation checks shared by the CLI and post-reboot verifier."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import stat

REBOOT_RECORD = '/var/lib/monitorize-vkms/reboot-required.json'


def verify(*, root=Path('/'), kernel=None, run=subprocess.run):
    """Inspect local facts only; never activate a service or request authorization."""
    root = Path(root)
    kernel = kernel or os.uname().release
    checks = []
    installed = None
    version = ''

    def path(name):
        return root / name.lstrip('/')

    def check(name, passed, detail=''):
        checks.append(dict(name=name, status='PASS' if passed else 'FAIL', detail=detail))

    def command(*args):
        result = run(list(args), capture_output=True, text=True, check=False, timeout=3)
        return result.returncode, result.stdout.strip()

    try:
        installed = any(path(name).exists() for name in (
            '/usr/lib/monitorize-vkms', '/usr/bin/monitorize-vkms',
            '/usr/libexec/monitorize-vkms/monitorize-vkms-helper',
            '/sys/module/monitorize_vkms'))
        code, module = command('modinfo', '-k', kernel, '-n', 'monitorize_vkms')
        installed = installed or code == 0
        if not installed:
            return dict(schema_version=1, installed=False, version='', state='not_installed',
                        message='Monitorize VKMS not installed', checks=[], reboot_required=False)
        version_file = path('/usr/lib/monitorize-vkms/VERSION')
        version = version_file.read_text().strip() if version_file.is_file() else ''
        check('Installed version', bool(re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version)), version)
        check('Module installed for running kernel', code == 0 and module.startswith(
            (f'/lib/modules/{kernel}/updates/', f'/lib/modules/{kernel}/extra/',
             f'/usr/lib/modules/{kernel}/updates/', f'/usr/lib/modules/{kernel}/extra/')), module)
        if code == 0:
            _, name = command('modinfo', '-F', 'name', module)
            _, vermagic = command('modinfo', '-F', 'vermagic', module)
            _, parameters = command('modinfo', '-p', module)
            check('Module identity and kernel compatibility', name == 'monitorize_vkms'
                  and vermagic.split()[:1] == [kernel] and 'create_default_dev:' in parameters)
        stock_code, stock = command('modinfo', '-k', kernel, '-n', 'vkms')
        if stock_code == 0:
            check('Stock VKMS preserved', stock == '(builtin)' or stock.startswith(
                (f'/lib/modules/{kernel}/kernel/', f'/usr/lib/modules/{kernel}/kernel/')), stock)
        loaded = path('/sys/module/monitorize_vkms')
        check('Kernel module loaded', loaded.is_dir())
        if loaded.is_dir():
            parameter = (loaded / 'parameters/create_default_dev').read_text().strip()
            check('Default device disabled', parameter in ('N', '0'), parameter)
        topology = next((path(name) / 'monitorize' for name in
            ('/sys/kernel/config/monitorize-vkms', '/config/monitorize-vkms') if path(name).is_dir()), None)
        ready = topology is not None and (topology / 'enabled').is_file()
        ready = ready and (topology / 'enabled').read_text().strip() == '1'
        check('Persistent topology enabled', ready)
        for index in (0, 1):
            connector = topology / f'connectors/connector{index}' if topology else None
            ready = connector is not None and all((connector / name).is_file()
                for name in ('enabled', 'status', 'edid', 'edid_enabled'))
            ready = ready and (connector / 'enabled').read_text().strip() == '1'
            check(f'Virtual connector {index + 1} ready', ready)
        cards = path('/sys/class/drm')
        check('Monitorize DRM card present', any('/faux/monitorize' in str(card.resolve())
            for card in cards.glob('card*') if '-' not in card.name))
        for name in ('monitorize-vkms-helper', 'monitorize-vkms-dbus-service', 'monitorize-vkms-authorization'):
            check(name, path('/usr/libexec/monitorize-vkms/' + name).is_file())
        for name in ('monitorize-vkms.service', 'monitorize-vkms-bootstrap.service'):
            check(name, any(path(base + name).is_file() for base in
                ('/etc/systemd/system/', '/usr/lib/systemd/system/', '/lib/systemd/system/')))
        for directory, name in (
            ('/usr/share/dbus-1/system-services/', 'io.github.vinnavannewton.MonitorizeVkms1.service'),
            ('/usr/share/dbus-1/interfaces/', 'io.github.vinnavannewton.MonitorizeVkms1.xml'),
            ('/usr/share/polkit-1/actions/', 'io.github.vinnavannewton.monitorize-vkms.policy')):
            check(name, path(directory + name).is_file())
        check('System D-Bus policy', any(path(base + 'io.github.vinnavannewton.MonitorizeVkms1.conf').is_file()
            for base in ('/etc/dbus-1/system.d/', '/usr/share/dbus-1/system.d/')))
        library = path('/usr/lib/monitorize-vkms/monitorize_vkms/helper.py')
        executable = path('/usr/libexec/monitorize-vkms/monitorize-vkms-helper')
        check('Helper copies consistent', library.is_file() and executable.is_file()
              and library.read_bytes() == executable.read_bytes())
        _, status = command('dkms', 'status', '-m', 'monitorize-vkms', '-k', kernel)
        check('DKMS installed', bool(re.search(r'[:,]\s*(?:installed|weak-installed)(?:$|,)', status, re.M)), status)
        _, service = command('systemctl', 'show', 'monitorize-vkms.service', '--property=ActiveState', '--value')
        # A Type=dbus host may be idle until its first client connects. Do not
        # activate it just to verify readiness, or label that idle state broken.
        activatable = path('/usr/share/dbus-1/system-services/io.github.vinnavannewton.MonitorizeVkms1.service').is_file()
        check('Host service ready', service == 'active' or (service == 'inactive' and activatable), service)
        record = path(REBOOT_RECORD)
        reboot = False
        if record.is_file():
            value = json.loads(record.read_text())
            reboot = value.get('boot_id') == path('/proc/sys/kernel/random/boot_id').read_text().strip()
        passed = all(item['status'] == 'PASS' for item in checks)
        state = 'restart_required' if reboot else 'working' if passed else 'needs_attention'
        message = {'restart_required': 'System restart required', 'working': 'Verified — working',
                   'needs_attention': 'Needs attention'}[state]
        return dict(schema_version=1, installed=True, version=version, state=state,
                    message=message, checks=checks, reboot_required=reboot)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return dict(schema_version=1, installed=installed, version=version, state='could_not_verify',
                    message='Could not verify installation', detail=str(error), checks=checks, reboot_required=False)


def record_reboot(*, root=Path('/')):
    """Record successful driver replacement; only a later boot clears the state."""
    if os.geteuid() != 0:
        raise PermissionError('Administrator authorization is required.')
    root = Path(root)
    target = root / REBOOT_RECORD.lstrip('/')
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    for directory in (target.parent, *target.parent.parents):
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe VKMS state directory.')
        if directory == root:break
    fd, name = tempfile.mkstemp(prefix='.reboot-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'boot_id': (root / 'proc/sys/kernel/random/boot_id').read_text().strip()}, stream)
            stream.flush();os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--record-reboot', action='store_true')
    args = parser.parse_args(argv)
    if args.record_reboot:
        record_reboot()
        return 0
    result = verify()
    if args.json:
        print(json.dumps(result))
    else:
        for check in result['checks']:
            print(f"[{check['status']}] {check['name']}: {check['detail']}")
        print(result['message'])
    return 0 if result['state'] == 'working' else 1


if __name__ == '__main__':
    raise SystemExit(main())
