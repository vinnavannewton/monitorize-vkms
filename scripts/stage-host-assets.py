#!/usr/bin/env python3
"""Stage native host userspace and optionally DKMS sources; never install or enable."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from monitorize_vkms.protocols import manifest


def stage_host_assets(destination: Path) -> None:
    destination = Path(destination).absolute()
    if destination == Path('/'):
        raise ValueError('Stage to a package root, never the live system.')
    files = {
        'monitorize_vkms/recovery.py': ('usr/libexec/monitorize-vkms/monitorize-vkms-recovery', 0o755),
        'monitorize_vkms/authorization_setup.py': ('usr/libexec/monitorize-vkms/monitorize-vkms-authorization', 0o755),
        'VERSION': ('usr/lib/monitorize-vkms/VERSION', 0o644),
        'monitorize_vkms/helper.py': ('usr/libexec/monitorize-vkms/monitorize-vkms-helper', 0o755),
        'monitorize_vkms/dbus_service.py': ('usr/libexec/monitorize-vkms/monitorize-vkms-dbus-service', 0o755),
        'scripts/monitorize-vkms-bootstrap.sh': ('usr/libexec/monitorize-vkms/monitorize-vkms-bootstrap', 0o755),
        'scripts/monitorize-vkms-cli': ('usr/bin/monitorize-vkms', 0o755),
        'systemd/monitorize-vkms.service': ('usr/lib/systemd/system/monitorize-vkms.service', 0o644),
        'systemd/monitorize-vkms-bootstrap.service': ('usr/lib/systemd/system/monitorize-vkms-bootstrap.service', 0o644),
        'packaging/monitorize-vkms.conf': ('usr/lib/modprobe.d/monitorize-vkms.conf', 0o644),
        'LICENSE': ('usr/share/licenses/monitorize-vkms/LICENSE', 0o644),
    }
    copies = list(files.items())
    for source in (ROOT / 'monitorize_vkms').glob('*.py'):
        copies.append((str(source.relative_to(ROOT)), ('usr/lib/monitorize-vkms/monitorize_vkms/' + source.name,
                                                     0o755 if source.name == 'helper.py' else 0o644)))
    for source in (ROOT / 'packaging').glob('io.github.*'):
        suffix = source.suffix
        directory = {'.policy': 'polkit-1/actions', '.xml': 'dbus-1/interfaces',
                     '.service': 'dbus-1/system-services', '.conf': 'dbus-1/system.d'}[suffix]
        copies.append((str(source.relative_to(ROOT)), ('usr/share/' + directory + '/' + source.name, 0o644)))
    for source, (relative, mode) in copies:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
        target.chmod(mode)
    target = destination / 'usr/share/monitorize-vkms/capabilities.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest((ROOT / 'VERSION').read_text().strip()), indent=2) + '\n')
    target.chmod(0o644)
    for path in destination.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)


def stage_dkms_sources(destination: Path) -> None:
    version = (ROOT / 'VERSION').read_text().strip()
    target = Path(destination) / ('usr/src/monitorize-vkms-' + version)
    target.mkdir(parents=True, exist_ok=True)
    for relative in ('dkms.conf', 'VERSION', 'LICENSE', 'scripts/probe-drm-atomic-api.sh'):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
        path.chmod(0o755 if path.suffix == '.sh' else 0o644)
    for source in (ROOT / 'src/vkms').iterdir():
        if source.name == 'Makefile' or source.suffix in ('.c', '.h') and source.name not in ('vkms_oot_features.h',):
            if source.name.endswith('.mod.c'):
                continue
            path = target / 'src/vkms' / source.name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
            path.chmod(0o644)
    for path in target.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destdir', type=Path, required=True)
    parser.add_argument('--dkms', action='store_true')
    args = parser.parse_args()
    stage_host_assets(args.destdir)
    if args.dkms:
        stage_dkms_sources(args.destdir)
