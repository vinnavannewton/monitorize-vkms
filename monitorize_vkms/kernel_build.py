"""Prepare installed kernels without joining an OS package transaction."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitorize_vkms import __version__
from monitorize_vkms.secure_boot import check_enrollment

STATE = Path('/var/lib/monitorize-vkms/kernel-builds')
MODULES = Path('/lib/modules')
SOURCE = Path('/usr/src') / ('monitorize-vkms-' + __version__)


def run(*args, timeout=600):
    with tempfile.TemporaryFile(mode='w+') as log:
        process = subprocess.Popen(list(args), stdout=log, stderr=log, start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError('Command timed out: ' + args[0])
        log.seek(0, 2)
        log.seek(max(0, log.tell() - 8000))
        output = log.read()
        if process.returncode:
            raise RuntimeError(output or str(args))
        return output.strip()


def trusted_directory(path):
    for entry in (path, *path.parents):
        info = entry.lstat()
        if entry.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe maintenance directory: ' + str(entry))


def maintenance_lock():
    fd = os.open('/run/lock/monitorize-vkms-maintenance.lock',
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        os.close(fd)
        raise ValueError('Unsafe maintenance lock')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


def save(kernel, value):
    STATE.mkdir(parents=True, exist_ok=True)
    trusted_directory(STATE)
    fd, name = tempfile.mkstemp(dir=STATE, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o644)
        os.replace(name, STATE / (kernel + '.json'))
    finally:
        Path(name).unlink(missing_ok=True)


def fingerprint(headers):
    digest = hashlib.sha256()
    for path in sorted(SOURCE.rglob('*')):
        if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
            raise ValueError('Unsafe registered source: ' + str(path))
        if path.is_file():
            digest.update(str(path.relative_to(SOURCE)).encode())
            digest.update(path.read_bytes())
    for relative in ('Makefile', 'include/generated/utsrelease.h', 'Module.symvers'):
        path = headers / relative
        digest.update(path.read_bytes() if path.is_file() else b'missing')
    return digest.hexdigest()


def autoload_disabled():
    # A global admin setting can load modules as a side effect of DKMS install.
    # Defer rather than modifying global policy or depending on DKMS internals.
    files = [Path('/etc/dkms/framework.conf')]
    files.extend(Path('/etc/dkms/framework.conf.d').glob('*.conf'))
    for path in files:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if re.match(r'\s*(?:export\s+)?modprobe_on_install\s*=', line):
                if not re.fullmatch(r"\s*modprobe_on_install\s*=\s*(?:''|\"\")?\s*(?:#.*)?", line):
                    return False
    return True


def signing_check(module):
    if not Path('/sys/firmware/efi').exists():
        return
    state = run('mokutil', '--sb-state', timeout=10)
    if 'SecureBoot disabled' in state:
        return
    if 'SecureBoot enabled' not in state:
        raise RuntimeError('Cannot determine Secure Boot trust state')
    key = run('modinfo', '-F', 'sig_key', module, timeout=10).replace(':', '').lower()
    # Only certificates actually matching the artifact are accepted.
    certificates = [Path('/var/lib/dkms/mok.pub'), Path('/etc/dkms/mok.pub'),
                    Path('/var/lib/shim-signed/mok/MOK.der')]
    for certificate in certificates:
        if not certificate.is_file():
            continue
        text = run('openssl', 'x509', '-inform', 'DER', '-in', str(certificate),
                   '-noout', '-ext', 'subjectKeyIdentifier', timeout=10)
        identifiers = re.findall(r'(?:[0-9A-Fa-f]{2}:){7,}[0-9A-Fa-f]{2}', text)
        if key and any(value.replace(':', '').lower() == key for value in identifiers):
            check_enrollment(certificate)
            return
    raise RuntimeError('Secure Boot: enroll the certificate matching the module signing key; custom signing configuration requires manual verification')


def prepare(kernel):
    headers = MODULES / kernel / 'build'
    value = dict(kernel=kernel, version=__version__, updated=int(time.time()), state='queued')
    try:
        if not (headers / 'Makefile').is_file():
            value.update(state='waiting_headers', detail='Install matching kernel development files')
            return
        stamp = fingerprint(headers)
        value['fingerprint'] = stamp
        try:
            old = json.loads((STATE / (kernel + '.json')).read_text())
        except (OSError, ValueError):
            old = {}
        if old.get('fingerprint') == stamp and old.get('state') == 'build_failed':
            value.update(state='build_failed', detail=old.get('detail', 'Build failed'))
            return
        if not autoload_disabled():
            value.update(state='needs_attention', detail='DKMS modprobe_on_install must be empty for unattended preparation')
            return
        # Already installed modules are preserved, never force-replaced by this worker.
        try:
            module = run('modinfo', '-k', kernel, '-n', 'monitorize_vkms', timeout=10)
        except RuntimeError:
            module = ''
        if not module:
            value['state'] = 'building'
            save(kernel, value)
            try:
                run('dkms', 'build', '-m', 'monitorize-vkms', '-v', __version__, '-k', kernel, '--directive', 'parallel_jobs=2')
            except RuntimeError:
                value['state'] = 'build_failed'
                raise
            candidates = list((Path('/var/lib/dkms/monitorize-vkms') / __version__ / kernel).glob('*/module/monitorize_vkms.ko*'))
            if len(candidates) != 1:
                raise RuntimeError('Cannot identify signed DKMS artifact')
            signing_check(str(candidates[0]))
            value['state'] = 'installing'
            save(kernel, value)
            run('dkms', 'install', '-m', 'monitorize-vkms', '-v', __version__, '-k', kernel)
            module = run('modinfo', '-k', kernel, '-n', 'monitorize_vkms', timeout=10)
        if not any(part in module for part in ('/extra/', '/updates/')):
            raise RuntimeError('Unexpected external module installation path')
        vermagic = run('modinfo', '-F', 'vermagic', module, timeout=10).split()
        if not vermagic or vermagic[0] != kernel:
            raise RuntimeError('Module kernel identity mismatch')
        signing_check(module)
        value.update(state='prepared', detail='Installed for this kernel; runtime acceptance requires boot verification')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if value['state'] != 'build_failed':
            value['state'] = 'needs_attention'
        value['detail'] = str(error)[-8000:]
    finally:
        save(kernel, value)


def main():
    if os.geteuid() != 0:
        raise PermissionError('Kernel maintenance requires root')
    fd = maintenance_lock()
    try:
        trusted_directory(SOURCE)
        for target in sorted(MODULES.iterdir()):
            if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+~-]{0,200}', target.name) and target.is_dir():
                prepare(target.name)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
