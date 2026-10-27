"""Installed facade for the protected remote-login display.

Importing this module never changes system state. Root callers authenticate their
engine/session context before invoking the installed operations.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from contextlib import contextmanager
import secrets

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitorize_vkms.lease import read_lease
from monitorize_vkms.boot_policy import validate_protected
GREETER_ON_DEMAND_PROTOCOL = 1
IDLE_DISPLAY_PROTOCOL = 1
LIVE_ENROLLMENT_PROTOCOL = 1


def boot_output_status(state: Path) -> dict | None:
    value = read_lease(state, 'mon1')
    if value is None or value.get('protected') is not True:
        return None
    validate_protected(value)
    return value


def _installed_context(*, enabled: bool = True):
    from monitorize_vkms import helper, boot_runtime as runtime
    if os.geteuid() != 0:
        raise PermissionError('Remote-login display operations require root.')
    for module in (sys.modules[__name__], helper, runtime):
        runtime.require_installed(Path(module.__file__).absolute())
    settings = runtime.read_enrollment()
    if settings is None or (enabled and not settings['enabled']):
        raise PermissionError('Remote login is not enabled for this account.')
    return helper, runtime, settings


@contextmanager
def _transaction(*, enabled: bool = True, allow_missing: bool = False):
    helper, runtime, settings = _installed_context(enabled=enabled)
    lock = helper._acquire_lifecycle_lock()
    try:
        value = boot_output_status(helper.STATE_DIRECTORY)
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if value is None and allow_missing and read_lease(helper.STATE_DIRECTORY, 'mon1') is None:
            yield helper, runtime, settings, None, None
            return
        if value is None or value['uid'] != settings['uid'] or value['boot_id'] != boot_id:
            raise PermissionError('No current enrolled boot display reservation.')
        root = helper._configfs_root([])
        _, connector = helper._require_bootstrap(root, [], 'mon1')
        yield helper, runtime, settings, value, connector
    finally:
        os.close(lock)


def reconnect_reserved(*, state: Path, generation: str, boot_id: str, connector: Path) -> dict:
    # Low-level root service API: caller already holds the lifecycle lock.
    helper, runtime, settings = _installed_context()
    if state != helper.STATE_DIRECTORY or boot_id != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise PermissionError('Invalid installed reservation context.')
    value = boot_output_status(state)
    if value is None or value['uid'] != settings['uid']:
        raise PermissionError('No enrolled reservation.')
    root = helper._configfs_root([])
    _, expected = helper._require_bootstrap(root, [], 'mon1')
    if connector != expected:
        raise PermissionError('Invalid reserved configfs connector.')
    return runtime.reconnect_reserved(state=state, generation=generation, boot_id=boot_id, connector=connector)


def reserve_desktop_installed(uid: int, session: str, *, generation: str) -> dict:
    helper, runtime, settings = _installed_context()
    if type(uid) is not int or uid != settings['uid']:
        raise PermissionError('Only the enrolled account may reserve the display.')
    def check():
        if runtime.desktop_context(uid) != session:
            raise PermissionError('Enrolled desktop session changed.')
    check()
    lock = helper._acquire_lifecycle_lock()
    try:
        root = helper._configfs_root([])
        _, connector = helper._require_bootstrap(root, [], 'mon1')
        with runtime.quiescent(uid):
            return runtime.reserve_desktop_output(state=helper.STATE_DIRECTORY, connector=connector,
                uid=uid, session=session, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                generation=generation, context_check=check)
    finally:
        os.close(lock)


def release_live_desktop_installed(uid: int, session: str, *, generation: str) -> dict:
    with _transaction(enabled=False, allow_missing=True) as (helper, runtime, settings, value, connector):
        if type(uid) is not int or uid != settings['uid'] or runtime.desktop_context(uid) != session:
            raise PermissionError('Enrolled desktop session changed.')
        if value is None:
            return dict(state='released', changed=False)
        if value['phase'] != 'desktop' or value['session'] != session or value['generation'] != generation:
            raise PermissionError('Reservation generation changed; retained for its owner.')
        with runtime.quiescent(uid):
            if runtime.desktop_context(uid) != session:
                raise PermissionError('Enrolled desktop session changed.')
            runtime.connector_ready(connector)
            if (connector/'status').read_text().strip() != '2':
                runtime.idle_reserved(state=helper.STATE_DIRECTORY, connector=connector, lease=value)
            from .lease import remove_lease
            remove_lease(helper.STATE_DIRECTORY, 'mon1')
        return dict(state='released', changed=True)


def greeter_operation_installed(operation: str, *, context_check, activate) -> dict:
    if operation not in ('prepare', 'idle'):
        raise ValueError('Unknown greeter display operation.')
    with _transaction() as (helper, runtime, settings, value, connector):
        context = runtime.greeter_context()
        if value['phase'] != 'greeter' or value['session'] not in ('', context[1]):
            raise PermissionError('Reservation belongs to another graphical session.')
        def check():
            context_check()
            if runtime.greeter_context() != context:
                raise PermissionError('Greeter changed during display operation.')
        check()
        value['session'] = context[1]
        from monitorize_vkms.lease import write_lease
        write_lease(helper.STATE_DIRECTORY, 'mon1', value)
        if operation == 'prepare':
            return runtime.prepare_reserved(state=helper.STATE_DIRECTORY, connector=connector,
                                            lease=value, activate=activate, context_check=check)
        with runtime.quiescent(settings['uid']):
            check()
            result = runtime.idle_reserved(state=helper.STATE_DIRECTORY, connector=connector, lease=value)
            check()
            return result


def desktop_operation_installed(uid: int, session: str, operation: str, *, engines_stopped: bool = False, mode=None, capture_idle=False) -> dict:
    if operation not in ('adopt', 'prepare', 'idle', 'release'):
        raise ValueError('Unknown desktop display operation.')
    with _transaction(enabled=operation in ('adopt', 'prepare'), allow_missing=operation=='release') as (helper, runtime, settings, value, connector):
        if type(uid) is not int or uid != settings['uid']:
            raise PermissionError('Only the enrolled desktop may manage its reservation.')
        actual, environment = helper._active_session(uid)
        if actual != session or environment.get('XDG_SESSION_TYPE') not in ('wayland', 'x11'):
            raise PermissionError('Selected desktop session changed.')
        if value is None:
            # Release may have succeeded before fallback grant publication failed.
            # Complete that retry without creating a lease or touching an output;
            # authenticate the live account/session and capture exclusion again.
            if operation != 'release' or not engines_stopped:
                raise PermissionError('Capture must be stopped before display removal.')
            with runtime.quiescent(uid):
                if helper._active_session(uid)[0] != session:
                    raise PermissionError('Desktop session changed during display operation.')
                return dict(state='released', changed=False)
        if value['phase'] == 'desktop' and value['session'] != session and operation != 'release':
            raise PermissionError('Reservation belongs to another desktop session.')
        def check():
            if helper._active_session(uid)[0] != session:
                raise PermissionError('Desktop session changed during display operation.')
        from monitorize_vkms.lease import write_lease, remove_lease
        if value['phase'] == 'greeter':
            if operation == 'prepare':
                raise PermissionError('Adopt the desktop reservation before starting its engine.')
            with runtime.quiescent(uid):
                check()
                value.update(phase='desktop', session=session, generation=secrets.token_hex(16))
                write_lease(helper.STATE_DIRECTORY, 'mon1', value)
        if operation == 'adopt':
            return value
        if operation == 'prepare':
            # Snapshot before hotplug so existing virtual-named VM screens retain
            # their identities. 3s snapshot + 12s prepare fits the 18s control RPC.
            snapshot=helper._run_worker(uid,environment,dict(action='snapshot'),timeout=3)
            if snapshot.get('success') is not True:raise RuntimeError('Desktop snapshot failed before display preparation.')
            if mode is not None and list(mode)!=value['mode']:
                if capture_idle is not True:raise PermissionError('An idle authenticated desktop runner is required to resize.')
                check()
                value=runtime.change_reserved_mode(state=helper.STATE_DIRECTORY,connector=connector,lease=value,mode=mode)
            def activate(output, remaining):
                if remaining<=1:raise RuntimeError('Reserved output activation budget exhausted.')
                return helper._run_worker(uid, environment, dict(action='activate', name=output['name'],
                    card=output['card'], connector_id=output['connector_id'], mode=value['mode'], snapshot=snapshot.get('state')),
                    timeout=min(10, remaining-1))
            return runtime.prepare_reserved(state=helper.STATE_DIRECTORY, connector=connector,
                                            lease=value, activate=activate, context_check=check)
        if not engines_stopped:
            raise PermissionError('Capture must be stopped before display removal.')
        with runtime.quiescent(uid):
            check()
            result = runtime.idle_reserved(state=helper.STATE_DIRECTORY, connector=connector, lease=value)
            check()
            if operation == 'release':
                remove_lease(helper.STATE_DIRECTORY, 'mon1')
                result = dict(result, state='released')
            return result


def return_to_greeter(*, state: Path, generation: str, boot_id: str,
                      sessions: list[dict], engines_stopped: bool, connector: Path) -> dict:
    # The coordinator already holds the lifecycle lock around this API.
    helper, runtime, settings = _installed_context()
    current_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if state != helper.STATE_DIRECTORY or boot_id != current_boot or not engines_stopped:
        raise PermissionError('Invalid greeter handoff context.')
    value = boot_output_status(state)
    if value is None or value['generation'] != generation or value['boot_id'] != boot_id or value['uid'] != settings['uid']:
        raise PermissionError('Greeter handoff reservation changed.')
    root = helper._configfs_root([])
    _, expected = helper._require_bootstrap(root, [], 'mon1')
    if connector != expected:
        raise PermissionError('Unexpected greeter connector.')
    runtime.no_seat_owner(sessions, allow_greeter=True)
    runtime.no_seat_owner(runtime.local_sessions(), allow_greeter=True)
    with runtime.quiescent(settings['uid']):
        result = runtime.idle_reserved(state=state, connector=connector, lease=value)
        from .boot_policy import MODE
        result=runtime.change_reserved_mode(state=state,connector=connector,lease=result,mode=MODE)
        runtime.no_seat_owner(runtime.local_sessions(), allow_greeter=True)
        result.update(phase='greeter', session='', generation=secrets.token_hex(16))
        from monitorize_vkms.lease import write_lease
        write_lease(state, 'mon1', result)
        return result


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
        value = boot_output_status(helper.STATE_DIRECTORY)
        if value is not None and value['phase'] == 'desktop':
            session = runtime.desktop_context(settings['uid'])
            runtime.verify_desktop_reservation(connector=connector, lease=value, uid=settings['uid'],
                session=session, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                context_check=lambda: _check_desktop(runtime, settings['uid'], session))
            return 0
        runtime.reserve_boot_output(state=helper.STATE_DIRECTORY, connector=connector,
                                    uid=settings['uid'], boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                    sessions=runtime.local_sessions())
    finally:
        os.close(lock)
    return 0


def _check_desktop(runtime, uid, session):
    if runtime.desktop_context(uid) != session:
        raise PermissionError('Enrolled desktop session changed.')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps(dict(success=False, message=str(error))), file=sys.stderr)
        raise SystemExit(1)
