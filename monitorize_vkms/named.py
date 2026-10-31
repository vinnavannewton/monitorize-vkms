"""Named CLI displays use independent reusable DRM devices, without a slot cap.

The app/greeter's bootstrapped card is never resized or disabled. Removed named
outputs leave a disconnected device available for reuse until reboot, avoiding
live DRM-card teardown while a compositor may still reference it.
"""
import os
from pathlib import Path
import secrets
from types import SimpleNamespace

from monitorize_vkms.names import named_slot, validate_name
from monitorize_vkms.lease import named_slots


def records(helper, uid=None):
    return [(slot, helper._read_lease(slot)) for slot in named_slots(helper.STATE_DIRECTORY, uid)]


def topology(root, slot):
    if not named_slot(slot):
        raise ValueError('Invalid named display identity.')
    instance = root / 'monitorize-vkms' / ('monitorize-' + slot)
    return instance, instance / 'connectors/connector0'


def require_topology(root, slot, logs):
    from monitorize_vkms import helper as h
    instance, connector = topology(root, slot)
    if (h._read(instance / 'enabled') != '1' or h._read(connector / 'dynamic') != '1'
            or h._read(connector / 'enabled') != '1'):
        raise h.VkmsHelperError('The named VKMS device is not ready.')
    logs.append('Using independently registered named VKMS device')
    return instance, connector


def provision(root, slot, helper, logs):
    """Build before enabling a new device; never alter an active device's CRTCs."""
    instance, connector = topology(root, slot)
    if not instance.exists():
        instance.mkdir()
    if helper._read(instance / 'enabled') == '1':
        return require_topology(root, slot, logs)
    plane, crtc, encoder = (instance / 'planes/plane0', instance / 'crtcs/crtc0',
                             instance / 'encoders/encoder0')
    for item in (plane, crtc, encoder, connector):
        if not item.exists():
            item.mkdir()
    helper._write(plane / 'type', '1')
    for link, target in ((plane / 'possible_crtcs/crtc0', crtc),
                         (encoder / 'possible_crtcs/crtc0', crtc),
                         (connector / 'possible_encoders/encoder0', encoder)):
        if link.is_symlink():
            if link.resolve() != target.resolve():
                raise helper.VkmsHelperError('Unexpected named-device topology link.')
        elif link.exists():
            raise helper.VkmsHelperError('Unexpected named-device topology entry.')
        else:
            link.symlink_to(target)
    for name, value in (('dynamic', '1'), ('status', '2'), ('edid_enabled', '1'), ('enabled', '1')):
        helper._write(connector / name, value)
    helper._write(instance / 'enabled', '1')
    return require_topology(root, slot, logs)


def available_slot(root, uid, helper):
    base = root / 'monitorize-vkms'
    for instance in sorted(base.glob(f'monitorize-named-{uid}-*')):
        slot = instance.name[len('monitorize-'):]
        if not named_slot(slot) or helper._read_lease(slot) is not None:
            continue
        _, connector = topology(root, slot)
        if helper._read(instance / 'enabled') != '1' or helper._read(connector / 'status') == '2':
            return slot
    return f'named-{uid}-' + secrets.token_hex(16)


def create(args, root, uid, session, env, helper, logs):
    name = validate_name(args.name)
    if any(record and record.get('name') == name for _, record in records(helper, uid)):
        raise helper.VkmsHelperError(f'A display named {name!r} already exists. Remove it before creating it again.')
    mode = helper._validate_requested_mode(args.mode or [])
    from monitorize_vkms.edid import generate_cvt_timing
    generate_cvt_timing(*mode)  # Reject invalid modes before creating configfs objects.
    slot = available_slot(root, uid, helper)
    helper._write_lease(dict(name=name, slot=slot, uid=uid, session=session, mode=mode,
        boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        generation=secrets.token_hex(16), state='pending'), slot)
    provision(root, slot, helper, logs)
    request = SimpleNamespace(operation='create', slot=slot, mode=mode, edid_base64=None,
                              display_name=name)
    result = helper._create_request(request, root, logs, uid, session, env)
    result.update(name=name, display=name)
    return result


def remove_record(root, slot, record, uid, session, env, generation, helper, logs):
    if (not record or record.get('uid') != uid or record.get('session') != session
            or record.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or record.get('protected') or (generation and generation != record.get('generation'))):
        raise helper.VkmsHelperError('Only the creating session may remove this display generation.')
    instance, connector = topology(root, slot)
    # A failed pre-activation create is still listed and can release its own
    # pending record. No live resource or unowned output is destroyed here.
    registered = instance.exists() and helper._read(instance / 'enabled') == '1'
    if record.get('state') == 'pending' and (not registered or helper._read(connector / 'status') == '2'):
        helper._remove_lease(slot)
        return True
    return helper._destroy_slot(root, logs, slot, uid, session, env, generation)


def list_displays(helper, uid):
    from monitorize_vkms.drm import monitorize_drm_connectors
    connectors = list(monitorize_drm_connectors().values())
    displays = []
    for slot, record in records(helper, uid):
        if not record:
            continue
        current = next((item for item in connectors if item.get('display') == slot), {})
        displays.append(dict(name=record.get('name', slot), slot=slot,
            mode=record.get('mode'), state=record.get('state', 'unknown'),
            card=current.get('card'), connector=current.get('name'), managed=False,
            status=current.get('status', 'not detected'), session=record.get('session')))
    for slot in ('mon1', 'mon2'):
        record = helper._read_lease(slot)
        if record and record.get('uid') == uid:
            displays.append(dict(name='Monitorize ' + slot, slot=slot, mode=record.get('mode'),
                state=record.get('state', 'managed'), managed=True, protected=bool(record.get('protected'))))
    return displays


def dispatch(args, logs):
    from monitorize_vkms import helper as h
    uid = int(os.environ['PKEXEC_UID'])
    if uid <= 0:
        raise h.VkmsHelperError('Run as the normal desktop user, without sudo.')
    lock = h._acquire_lifecycle_lock()
    try:
        if args.operation == 'list-named':
            return dict(success=True, named_displays_protocol=1, displays=list_displays(h, uid))
        root = h._configfs_root(logs)
        session, env = h._active_session(uid)
        if args.operation == 'create-named':
            h._require_bootstrap(root, logs)
            return create(args, root, uid, session, env, h, logs)
        entries = records(h, uid)
        if args.operation == 'destroy-named':
            name = validate_name(args.name)
            entries = [(slot, record) for slot, record in entries if record and record.get('name') == name]
            if len(entries) != 1:
                raise h.VkmsHelperError(f'No unique display named {name!r} belongs to your user.')
        results = {}
        for slot, record in entries:
            name = record.get('name', slot) if record else slot
            try:
                changed = remove_record(root, slot, record, uid, session, env, args.generation, h, logs)
                results[name] = dict(changed=changed)
            except (h.VkmsHelperError, OSError, ValueError) as error:
                results[name] = dict(error=str(error))
        return dict(success=all('error' not in item for item in results.values()), results=results,
                    changed=any(item.get('changed') for item in results.values()), logs=logs)
    except (OSError, ValueError) as error:
        raise h.VkmsHelperError(str(error)) from error
    finally:
        os.close(lock)
