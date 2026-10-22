"""Installed facade for the protected remote-login display.

Importing this module never changes system state. Root callers authenticate their
engine/session context before invoking the installed operations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitorize_vkms.lease import read_lease
from monitorize_vkms.boot_policy import validate_protected


def boot_output_status(state: Path) -> dict | None:
    value = read_lease(state, 'mon1')
    if value is None or value.get('protected') is not True:
        return None
    validate_protected(value)
    return value


def main() -> int:
    from monitorize_vkms import boot_runtime as runtime, helper
    if os.geteuid() != 0:
        raise PermissionError('Boot display reservation requires root.')
    runtime.require_installed(Path(__file__).absolute())
    runtime.require_installed(Path(helper.__file__).absolute())
    settings = runtime.read_enrollment()
    if settings is None or not settings['enabled']:
        return 0
    lock = helper._acquire_lifecycle_lock()
    try:
        root = helper._configfs_root([])
        _, connector = helper._require_bootstrap(root, [], 'mon1')
        runtime.reserve_boot_output(state=helper.STATE_DIRECTORY, connector=connector,
                                    uid=settings['uid'], boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                    sessions=runtime.local_sessions())
    finally:
        os.close(lock)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps(dict(success=False, message=str(error))), file=sys.stderr)
        raise SystemExit(1)
