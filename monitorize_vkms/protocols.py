"""Versioned userspace contracts, independent of kernel/topology availability."""
BOOT_DISPLAY_PROTOCOL = 1
GREETER_ON_DEMAND_PROTOCOL = 1
IDLE_DISPLAY_PROTOCOL = 1


def capabilities() -> dict:
    return dict(boot_display=BOOT_DISPLAY_PROTOCOL, greeter_on_demand=GREETER_ON_DEMAND_PROTOCOL,
                idle_display=IDLE_DISPLAY_PROTOCOL)


def manifest(version: str) -> dict:
    return dict(schema_version=1, version=version, remote_login_protocols=capabilities())
