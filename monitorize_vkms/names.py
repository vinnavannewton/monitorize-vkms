"""User display labels are metadata, never configfs paths or shell commands."""
import re

SLOT_PATTERN = re.compile(r'named-([1-9][0-9]*)-([a-f0-9]{32})\Z')


def validate_name(name):
    if (not isinstance(name, str) or not name or name != name.strip()
            or len(name.encode('utf-8')) > 128 or not name.isprintable()):
        raise ValueError('Use a nonempty display name of at most 128 UTF-8 bytes, without control characters or surrounding spaces.')
    return name


def named_slot(slot):
    return isinstance(slot, str) and SLOT_PATTERN.fullmatch(slot) is not None


def slot_uid(slot):
    match = SLOT_PATTERN.fullmatch(slot)
    if match is None:
        raise ValueError('Invalid named display identity.')
    return int(match[1])


def edid_identity(slot):
    match = SLOT_PATTERN.fullmatch(slot)
    if match is None:
        raise ValueError('Invalid named display identity.')
    return 'n' + match[2][:12]


def device_slot(path):
    """Recognize only our exact faux-device namespace, not a substring match."""
    parts = path.parts
    for index, part in enumerate(parts[:-1]):
        if part == 'faux':
            name = parts[index + 1]
            if name == 'monitorize':
                return 'managed'
            if name.startswith('monitorize-') and named_slot(name[len('monitorize-'):]):
                return name[len('monitorize-'):]
    return None
