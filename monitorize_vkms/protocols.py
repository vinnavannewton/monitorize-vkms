"""Versioned userspace contracts, independent of kernel/topology availability."""
BOOT_DISPLAY_PROTOCOL = 1
GREETER_ON_DEMAND_PROTOCOL = 1
IDLE_DISPLAY_PROTOCOL = 1


def capabilities() -> dict:
    return dict(boot_display=BOOT_DISPLAY_PROTOCOL, greeter_on_demand=GREETER_ON_DEMAND_PROTOCOL,
                idle_display=IDLE_DISPLAY_PROTOCOL)


def manifest(version: str) -> dict:
    return dict(schema_version=1, version=version, remote_login_protocols=capabilities())


def installed_capabilities() -> dict:
    """Read installed declarations; this is not proof of display-manager acceptance."""
    import json
    from pathlib import Path
    from .boot_runtime import require_installed
    path = Path('/usr/share/monitorize-vkms/capabilities.json')
    try:
        require_installed(path)
        if path.stat().st_size > 4096:
            return {}
        data = json.loads(path.read_bytes())
        if data.get('schema_version') != 1 or data.get('remote_login_protocols') != capabilities():
            return {}
        return data['remote_login_protocols']
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
