"""Compositor detection and virtual display activation (GNOME & KDE)."""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import time
from typing import Any

from monitorize_vkms.errors import CompositorError

log = logging.getLogger(__name__)

APPLY_METHOD_TEMPORARY = 1
WAIT_ATTEMPTS = 20
WAIT_DELAY = 0.1
VKMS_DISCOVERY_ATTEMPTS = 100
VKMS_ACTIVATION_ATTEMPTS = 30
VKMS_APPLY_RETRIES = 2
REFRESH_RATE_TOLERANCE_HZ = 0.75

MONITOR_CONFIG_PROPERTY_KEYS = {
    "color-mode",
    "rgb-range",
    "underscanning",
}
GLOBAL_CONFIG_PROPERTY_KEYS = {
    "layout-mode",
}


def detect_compositor() -> str | None:
    """Detect current running Wayland/X11 compositor or desktop environment."""
    current_desktop = (
        os.environ.get("XDG_CURRENT_DESKTOP")
        or os.environ.get("XDG_SESSION_DESKTOP")
        or ""
    ).lower()

    if "gnome" in current_desktop:
        return "gnome"
    if "kde" in current_desktop or "plasma" in current_desktop:
        return "kde"
    if "hyprland" in current_desktop:
        return "hyprland"
    if "sway" in current_desktop:
        return "sway"

    if os.environ.get("GNOME_DESKTOP_SESSION_ID"):
        return "gnome"
    if os.environ.get("KDE_FULL_SESSION") == "true":
        return "kde"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"

    return None


# =========================================================================
# GNOME Mutter D-Bus Integration
# =========================================================================


def _require_dbus():
    try:
        import dbus

        return dbus
    except ImportError as exc:
        raise CompositorError(
            "The 'dbus' Python module is required for GNOME compositor integration. "
            "Install 'python3-dbus' using your distribution package manager."
        ) from exc


def _display_config_interface(bus=None, dbus=None):
    dbus = dbus or _require_dbus()
    bus = bus or dbus.SessionBus()
    obj = bus.get_object(
        "org.gnome.Mutter.DisplayConfig",
        "/org/gnome/Mutter/DisplayConfig",
    )
    return dbus.Interface(obj, "org.gnome.Mutter.DisplayConfig")


def _mutter_state(display_config=None):
    display_config = display_config or _display_config_interface()
    return display_config.GetCurrentState()


def _connector_name(entry) -> str:
    try:
        spec = entry[0]
    except (TypeError, IndexError):
        return ""
    if isinstance(spec, str):
        return spec
    try:
        return str(spec[0])
    except (TypeError, IndexError):
        return ""


def _logical_connector_names(logical_monitor) -> list[str]:
    try:
        connectors = logical_monitor[5]
    except (TypeError, IndexError):
        return []
    names = []
    for item in connectors:
        try:
            connector = str(item[0])
        except (TypeError, IndexError):
            continue
        if connector:
            names.append(connector)
    return names


def _is_vkms_connector(connector: str) -> bool:
    return str(connector).lower().startswith("virtual-")


def _physical_monitor(state, connector: str):
    try:
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return None
    return next(
        (m for m in physical_monitors if _connector_name(m) == str(connector)),
        None,
    )


def _monitor_identity(monitor) -> tuple[str, ...]:
    try:
        spec = monitor[0]
        return tuple(str(value) for value in spec[:4])
    except (TypeError, IndexError):
        return ()


def physical_monitor_identities(state) -> dict[str, tuple[str, ...]]:
    try:
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return {}
    return {
        connector: _monitor_identity(m)
        for m in physical_monitors
        if (connector := _connector_name(m))
    }


def logical_connector_names(state) -> list[str]:
    try:
        logical_monitors = state[2]
    except (TypeError, IndexError):
        return []
    return [
        connector
        for logical_monitor in logical_monitors
        for connector in _logical_connector_names(logical_monitor)
    ]


def _new_vkms_connector_from_state(
    state, before_identities: dict[str, tuple[str, ...]], width=None, height=None
) -> tuple[str, str]:
    candidates = []
    for monitor in state[1] if len(state) > 1 else ():
        connector = _connector_name(monitor)
        if not _is_vkms_connector(connector):
            continue
        if before_identities.get(connector) == _monitor_identity(monitor):
            continue
        candidates.append(monitor)
    if len(candidates) == 1:
        return _connector_name(candidates[0]), ""
    if len(candidates) > 1 and width and height:
        matching = []
        for monitor in candidates:
            modes = monitor[1] if len(monitor) > 1 else ()
            if any(
                int(mode[1]) == int(width) and int(mode[2]) == int(height)
                for mode in modes
            ):
                matching.append(monitor)
        if len(matching) == 1:
            return _connector_name(matching[0]), ""
    names = ", ".join(_connector_name(m) for m in candidates) or "none"
    return "", f"expected 1 new VKMS connector; found {len(candidates)} ({names})"


def _wait_for_new_vkms_connector(
    before_identities: dict[str, tuple[str, ...]],
    width: int,
    height: int,
    display_config=None,
    attempts=VKMS_DISCOVERY_ATTEMPTS,
    delay=WAIT_DELAY,
) -> tuple[str, Any, str]:
    display_config = display_config or _display_config_interface()
    latest_error = ""
    for _attempt in range(attempts):
        state = _mutter_state(display_config)
        connector, error = _new_vkms_connector_from_state(
            state, before_identities, width, height
        )
        if connector:
            return connector, state, ""
        latest_error = error
        time.sleep(delay)
    return (
        "",
        None,
        f"Mutter did not expose new VKMS connector in DisplayConfig within "
        f"{attempts * delay:g}s: {latest_error}",
    )


def _variant_dict(dbus, values=None):
    values = values or {}
    if hasattr(dbus, "Dictionary"):
        return dbus.Dictionary(values, signature="sv")
    return dict(values)


def _typed(dbus, name, value):
    constructor = getattr(dbus, name, None)
    return constructor(value) if constructor else value


def _allowed_properties(source, allowed_keys):
    if not hasattr(source, "items"):
        return {}
    return {str(k): v for k, v in source.items() if str(k) in allowed_keys}


def _monitor_config_properties(source):
    properties = _allowed_properties(source, MONITOR_CONFIG_PROPERTY_KEYS)
    if "underscanning" not in properties and hasattr(source, "get"):
        try:
            if "is-underscanning" in source:
                properties["underscanning"] = source.get("is-underscanning")
        except (TypeError, ValueError):
            pass
    return properties


def _monitor_properties_by_connector(physical_monitors):
    properties = {}
    for monitor in physical_monitors:
        connector = _connector_name(monitor)
        if not connector:
            continue
        try:
            properties[connector] = _monitor_config_properties(monitor[2])
        except (TypeError, IndexError):
            properties[connector] = {}
    return properties


def _current_modes(physical_monitors):
    current_modes = {}
    for monitor in physical_monitors:
        connector = _connector_name(monitor)
        if not connector:
            continue
        try:
            modes = monitor[1]
        except (TypeError, IndexError):
            return None
        current = next(
            (
                m
                for m in modes
                if len(m) > 6
                and getattr(m[6], "get", lambda _k: False)("is-current")
            ),
            None,
        )
        if current is None:
            continue
        try:
            supported_scales = [float(s) for s in current[5]]
        except (TypeError, ValueError, IndexError):
            supported_scales = []
        current_modes[connector] = {
            "id": str(current[0]),
            "supported_scales": supported_scales,
        }
    return current_modes


def _current_mode_for_connector(physical_monitors, connector):
    monitor = next(
        (m for m in physical_monitors if _connector_name(m) == str(connector)),
        None,
    )
    if monitor is None:
        return None
    try:
        return next(
            m for m in monitor[1] if len(m) > 6 and m[6].get("is-current")
        )
    except (TypeError, IndexError, StopIteration, AttributeError):
        return None


def _select_vkms_mode(state, connector, width, height, refresh):
    monitor = _physical_monitor(state, connector)
    if monitor is None:
        return None, f"Mutter did not expose {connector}"
    try:
        modes = list(monitor[1])
    except (TypeError, IndexError):
        return None, f"Mutter reported no modes for {connector}"
    candidates = [
        m
        for m in modes
        if int(m[1]) == int(width) and int(m[2]) == int(height)
    ]
    if not candidates:
        available = (
            ", ".join(
                sorted(
                    {f"{int(m[1])}x{int(m[2])}" for m in modes if len(m) > 3}
                )
            )
            or "none"
        )
        return None, (
            f"Mutter does not offer mode {width}x{height} on {connector}; "
            f"available sizes: {available}"
        )
    selected = min(candidates, key=lambda m: abs(float(m[3]) - float(refresh)))
    if abs(float(selected[3]) - float(refresh)) > REFRESH_RATE_TOLERANCE_HZ:
        return None, (
            f"Mutter mode {width}x{height}@{float(selected[3]):g}Hz on {connector} "
            f"does not match requested {float(refresh):g}Hz"
        )
    return selected, ""


def _supported_scale_for_mode(mode):
    try:
        scales = [float(s) for s in mode[5] if float(s) > 0]
    except (TypeError, ValueError, IndexError):
        scales = []
    if not scales:
        return None
    return min(scales, key=lambda s: abs(s - 1.0))


def _logical_width(logical_monitor, physical_monitors):
    connectors = _logical_connector_names(logical_monitor)
    if not connectors:
        return None
    mode = _current_mode_for_connector(physical_monitors, connectors[0])
    if mode is None:
        return None
    try:
        width, height = int(mode[1]), int(mode[2])
        scale = float(logical_monitor[2])
        transform = int(logical_monitor[3])
    except (TypeError, ValueError, IndexError):
        return None
    if scale <= 0:
        return None
    if transform in (1, 3, 5, 7):
        width = height
    return int(math.ceil(width / scale))


def _right_edge_of_layout(logical_monitors, physical_monitors):
    right_edge = 0
    for logical_monitor in logical_monitors:
        logical_w = _logical_width(logical_monitor, physical_monitors)
        if logical_w is None:
            return None
        try:
            right_edge = max(right_edge, int(logical_monitor[0]) + logical_w)
        except (TypeError, ValueError, IndexError):
            return None
    return right_edge


def _monitor_config(dbus, connector, mode_id, properties=None):
    values = [
        _typed(dbus, "String", connector),
        _typed(dbus, "String", mode_id),
        _variant_dict(dbus, properties),
    ]
    if hasattr(dbus, "Struct"):
        return dbus.Struct(values, signature="ssa{sv}")
    return tuple(values)


def _logical_monitor_config(
    dbus, logical_monitor, current_modes, monitor_properties, target
):
    try:
        connectors = logical_monitor[5]
    except (TypeError, IndexError):
        return None

    monitor_configs = []
    for item in connectors:
        try:
            connector = str(item[0])
        except (TypeError, IndexError):
            return None
        mode = current_modes.get(connector)
        if not mode:
            return None
        monitor_configs.append(
            _monitor_config(
                dbus,
                connector,
                mode["id"],
                monitor_properties.get(connector, {}),
            )
        )

    values = [
        _typed(dbus, "Int32", int(float(target["x"]))),
        _typed(dbus, "Int32", int(float(target["y"]))),
        _typed(dbus, "Double", float(target["scale"])),
        _typed(dbus, "UInt32", int(target["transform"])),
        _typed(dbus, "Boolean", bool(target["primary"])),
        (
            dbus.Array(monitor_configs, signature="(ssa{sv})")
            if hasattr(dbus, "Array")
            else monitor_configs
        ),
    ]
    if hasattr(dbus, "Struct"):
        return dbus.Struct(values, signature="iiduba(ssa{sv})")
    return tuple(values)


def _build_vkms_activation_config(
    state, connector, width, height, refresh, dbus
):
    try:
        _serial, physical_monitors, logical_monitors, _properties = state
    except (TypeError, ValueError):
        return None, {}, "Mutter returned incomplete DisplayConfig state"
    if connector in logical_connector_names(state):
        return None, {}, f"{connector} is already active in Mutter's layout"
    selected, error = _select_vkms_mode(
        state, connector, width, height, refresh
    )
    if selected is None:
        return None, {}, error
    scale = _supported_scale_for_mode(selected)
    if scale is None:
        return None, {}, f"Mutter did not report supported scale for {connector}"
    right_edge = _right_edge_of_layout(logical_monitors, physical_monitors)
    if right_edge is None:
        return (
            None,
            {},
            "Mutter did not report enough data to preserve layout",
        )
    current_modes = _current_modes(physical_monitors)
    monitor_properties = _monitor_properties_by_connector(physical_monitors)
    if current_modes is None:
        return (
            None,
            {},
            "Mutter did not report current modes for existing displays",
        )

    configs = []
    for logical_monitor in logical_monitors:
        connectors = _logical_connector_names(logical_monitor)
        if not connectors or any(
            name not in current_modes for name in connectors
        ):
            return (
                None,
                {},
                "Mutter did not report current modes for every active display",
            )
        try:
            target = {
                "x": int(logical_monitor[0]),
                "y": int(logical_monitor[1]),
                "scale": float(logical_monitor[2]),
                "transform": int(logical_monitor[3]),
                "primary": bool(logical_monitor[4]),
            }
        except (TypeError, ValueError, IndexError):
            return (
                None,
                {},
                "Mutter returned invalid active logical-monitor layout",
            )
        config = _logical_monitor_config(
            dbus, logical_monitor, current_modes, monitor_properties, target
        )
        if config is None:
            return None, {}, "Mutter rejected an existing display configuration"
        configs.append(config)

    selected_mode_id = str(selected[0])
    virtual_config = _monitor_config(
        dbus,
        connector,
        selected_mode_id,
        monitor_properties.get(connector, {}),
    )
    values = [
        _typed(dbus, "Int32", right_edge),
        _typed(dbus, "Int32", 0),
        _typed(dbus, "Double", scale),
        _typed(dbus, "UInt32", 0),
        _typed(dbus, "Boolean", False),
        (
            dbus.Array([virtual_config], signature="(ssa{sv})")
            if hasattr(dbus, "Array")
            else [virtual_config]
        ),
    ]
    configs.append(
        dbus.Struct(values, signature="iiduba(ssa{sv})")
        if hasattr(dbus, "Struct")
        else tuple(values)
    )
    payload = (
        dbus.Array(configs, signature="(iiduba(ssa{sv}))")
        if hasattr(dbus, "Array")
        else configs
    )
    details = {
        "name": connector,
        "width": int(selected[1]),
        "height": int(selected[2]),
        "refresh_rate": float(selected[3]),
        "mode_id": selected_mode_id,
        "scale": scale,
        "x": right_edge,
        "y": 0,
    }
    return payload, details, ""


def _active_output_modes(state=None, display_config=None) -> dict[str, dict]:
    display_config = display_config or _display_config_interface()
    state = _mutter_state(display_config) if state is None else state
    try:
        logical_monitors = state[2]
        physical_monitors = state[1]
    except (TypeError, IndexError):
        return {}
    active_connectors = {
        connector
        for logical_monitor in logical_monitors
        for connector in _logical_connector_names(logical_monitor)
    }
    result = {}
    for connector in active_connectors:
        for monitor in physical_monitors:
            if _connector_name(monitor) != connector:
                continue
            try:
                current = next(
                    m for m in monitor[1] if m[6].get("is-current")
                )
                result[connector] = {
                    "width": int(current[1]),
                    "height": int(current[2]),
                    "refresh_rate": float(current[3]),
                }
            except (
                TypeError,
                ValueError,
                IndexError,
                StopIteration,
                AttributeError,
            ):
                pass
    return result


def _is_monitor_logically_active(state, connector: str) -> bool:
    return str(connector) in logical_connector_names(state)


def _new_vkms_connectors(state, before_identities):
    before_identities = dict(before_identities or {})
    connectors = []
    for monitor in state[1] if len(state) > 1 else ():
        connector = _connector_name(monitor)
        if (
            _is_vkms_connector(connector)
            and before_identities.get(connector) != _monitor_identity(monitor)
        ):
            connectors.append(connector)
    return connectors


def _build_layout_without_connectors(state, excluded_connectors, dbus):
    try:
        _serial, physical_monitors, logical_monitors, _properties = state
    except (TypeError, ValueError):
        return None, "Mutter returned incomplete DisplayConfig state"
    excluded_connectors = set(excluded_connectors)
    current_modes = _current_modes(physical_monitors)
    monitor_properties = _monitor_properties_by_connector(physical_monitors)
    if current_modes is None:
        return None, "Mutter did not report current modes for active displays"
    configs = []
    for logical_monitor in logical_monitors:
        connectors = _logical_connector_names(logical_monitor)
        excluded = [name for name in connectors if name in excluded_connectors]
        if excluded:
            if len(connectors) != len(excluded):
                return (
                    None,
                    "Refusing to remove VKMS connector mirrored with real display",
                )
            continue
        if not connectors or any(
            name not in current_modes for name in connectors
        ):
            return (
                None,
                "Mutter did not report current modes for preserved display",
            )
        try:
            target = {
                "x": int(logical_monitor[0]),
                "y": int(logical_monitor[1]),
                "scale": float(logical_monitor[2]),
                "transform": int(logical_monitor[3]),
                "primary": bool(logical_monitor[4]),
            }
        except (TypeError, ValueError, IndexError):
            return None, "Mutter returned invalid active layout"
        config = _logical_monitor_config(
            dbus, logical_monitor, current_modes, monitor_properties, target
        )
        if config is None:
            return None, "Mutter rejected preserved display mode or scale"
        configs.append(config)
    if not configs:
        return None, "Refusing to remove every active logical monitor"
    return (
        (
            dbus.Array(configs, signature="(iiduba(ssa{sv}))")
            if hasattr(dbus, "Array")
            else configs
        ),
        "",
    )


def gnome_activate_vkms(
    before_identities: dict[str, tuple[str, ...]],
    width: int,
    height: int,
    refresh: float,
) -> tuple[bool, dict[str, Any], str]:
    """Activate new VKMS connector in GNOME Mutter logical layout."""
    dbus = _require_dbus()
    display_config = _display_config_interface(dbus=dbus)

    connector, state, error = _wait_for_new_vkms_connector(
        before_identities, width, height, display_config
    )
    if not connector:
        return False, {}, error

    for apply_attempt in range(VKMS_APPLY_RETRIES):
        if apply_attempt > 0:
            state = _mutter_state(display_config)
        payload, details, error = _build_vkms_activation_config(
            state, connector, width, height, refresh, dbus
        )
        if payload is None:
            return False, {}, error

        try:
            display_config.ApplyMonitorsConfig(
                _typed(dbus, "UInt32", int(state[0])),
                _typed(dbus, "UInt32", APPLY_METHOD_TEMPORARY),
                payload,
                _variant_dict(
                    dbus,
                    _allowed_properties(state[3], GLOBAL_CONFIG_PROPERTY_KEYS),
                ),
            )
        except Exception as exc:
            if apply_attempt + 1 < VKMS_APPLY_RETRIES and "serial" in str(
                exc
            ).lower():
                continue
            return (
                False,
                {},
                f"Could not apply GNOME VKMS layout for {connector}: {exc}",
            )

        for _attempt in range(VKMS_ACTIVATION_ATTEMPTS):
            current = _mutter_state(display_config)
            mode = _active_output_modes(current, display_config).get(connector)
            if (
                _is_monitor_logically_active(current, connector)
                and mode
                and mode["width"] == details["width"]
                and mode["height"] == details["height"]
                and abs(mode["refresh_rate"] - details["refresh_rate"])
                <= REFRESH_RATE_TOLERANCE_HZ
            ):
                return (
                    True,
                    details,
                    f"Mutter activated {connector} at {details['width']}x"
                    f"{details['height']}@{details['refresh_rate']:g}Hz",
                )
            time.sleep(WAIT_DELAY)

        return (
            False,
            {},
            f"Mutter discovered {connector} but did not activate it within "
            f"{VKMS_ACTIVATION_ATTEMPTS * WAIT_DELAY:g}s",
        )

    return (
        False,
        {},
        "Mutter DisplayConfig serial changed repeatedly during activation",
    )


def gnome_deactivate_vkms(
    before_identities: dict[str, tuple[str, ...]],
    attempts=VKMS_ACTIVATION_ATTEMPTS,
    delay=WAIT_DELAY,
) -> bool:
    """Remove newly added VKMS monitors from Mutter layout."""
    try:
        dbus = _require_dbus()
        display_config = _display_config_interface(dbus=dbus)
        for _attempt in range(attempts):
            state = _mutter_state(display_config)
            targets = [
                c
                for c in _new_vkms_connectors(state, before_identities)
                if _is_monitor_logically_active(state, c)
            ]
            if not targets:
                time.sleep(delay)
                continue
            payload, error = _build_layout_without_connectors(
                state, targets, dbus
            )
            if payload is None:
                log.warning("Could not build GNOME VKMS removal layout: %s", error)
                return False
            try:
                display_config.ApplyMonitorsConfig(
                    _typed(dbus, "UInt32", int(state[0])),
                    _typed(dbus, "UInt32", APPLY_METHOD_TEMPORARY),
                    payload,
                    _variant_dict(
                        dbus,
                        _allowed_properties(
                            state[3], GLOBAL_CONFIG_PROPERTY_KEYS
                        ),
                    ),
                )
            except Exception as exc:
                if "serial" in str(exc).lower():
                    continue
                log.warning("Could not apply GNOME VKMS removal layout: %s", exc)
                return False

            for _verify in range(attempts):
                current = _mutter_state(display_config)
                if not any(
                    _is_monitor_logically_active(current, name)
                    for name in targets
                ):
                    return True
                time.sleep(delay)
            return False
        return False
    except Exception as exc:
        log.debug("Failed to remove GNOME VKMS layout: %s", exc)
        return False


# =========================================================================
# KDE Plasma / KWin Integration
# =========================================================================


def kde_outputs() -> list[dict]:
    """Query current outputs from kscreen-doctor -j."""
    if not shutil.which("kscreen-doctor"):
        return []
    try:
        res = subprocess.run(
            ["kscreen-doctor", "-j"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            return json.loads(res.stdout).get("outputs", [])
    except Exception as exc:
        log.debug("kscreen-doctor error: %s", exc)
    return []


def kde_activate_vkms(
    before_names: set[str],
    width: int,
    height: int,
    refresh: float,
    timeout: float = 10.0,
) -> tuple[bool, dict[str, Any], str]:
    """Wait for new output in KDE and set requested mode via kscreen-doctor."""
    if not shutil.which("kscreen-doctor"):
        return False, {}, "kscreen-doctor is not installed"

    deadline = time.monotonic() + timeout
    output_name = None
    output_info = None

    while time.monotonic() < deadline:
        current = kde_outputs()
        for out in current:
            name = str(out.get("name") or "")
            if name and name not in before_names and out.get("connected", True):
                output_name = name
                output_info = out
                break
        if output_name:
            break
        time.sleep(0.1)

    if not output_name or not output_info:
        return False, {}, f"KWin did not expose new output within {timeout:g}s"

    # Find matching mode
    target_mode_id = None
    for mode in output_info.get("modes", []):
        size = mode.get("size") or {}
        if int(size.get("width") or 0) == width and int(
            size.get("height") or 0
        ) == height:
            target_mode_id = str(mode.get("id") or "")
            break

    selector = str(
        output_info.get("id")
        if output_info.get("id") is not None
        else output_name
    )
    cmds = [f"output.{selector}.enable"]
    if target_mode_id:
        cmds.append(f"output.{selector}.mode.{target_mode_id}")

    res = subprocess.run(
        ["kscreen-doctor", *cmds],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode != 0:
        return (
            False,
            {},
            f"kscreen-doctor failed: {res.stderr.strip() or res.stdout.strip()}",
        )

    details = {
        "name": output_name,
        "width": width,
        "height": height,
        "refresh_rate": refresh,
    }
    return True, details, f"KWin activated {output_name} at {width}x{height}@{refresh:g}Hz"


# =========================================================================
# Unified Dispatcher
# =========================================================================


def get_compositor_snapshot() -> tuple[str | None, Any]:
    """Take a snapshot of existing displays before creating a virtual monitor."""
    desktop = detect_compositor()
    if desktop == "gnome":
        try:
            state = _mutter_state()
            return "gnome", physical_monitor_identities(state)
        except Exception as exc:
            log.warning("Could not read GNOME DisplayConfig state: %s", exc)
            return "gnome", {}
    elif desktop == "kde":
        outs = {str(o.get("name")) for o in kde_outputs() if o.get("name")}
        return "kde", outs
    return desktop, None


def activate_in_compositor(
    desktop: str | None,
    before_state: Any,
    width: int,
    height: int,
    refresh: float,
) -> tuple[bool, dict[str, Any], str]:
    """Activate newly added virtual display in the active desktop compositor."""
    if desktop == "gnome":
        return gnome_activate_vkms(
            before_state or {}, width, height, refresh
        )
    elif desktop == "kde":
        return kde_activate_vkms(
            before_state or set(), width, height, refresh
        )
    elif desktop is None:
        return (
            True,
            {
                "name": "Virtual-DRM",
                "width": width,
                "height": height,
                "refresh_rate": refresh,
            },
            f"No active compositor detected; display created on DRM card",
        )
    else:
        # Hyprland / Sway or other - auto-detected or managed by user compositor rules
        return (
            True,
            {
                "name": "Virtual-Auto",
                "width": width,
                "height": height,
                "refresh_rate": refresh,
            },
            f"{desktop.capitalize()} auto-detects new DRM connectors",
        )


def deactivate_in_compositor(desktop: str | None, before_state: Any) -> bool:
    """Clean up / remove virtual display from compositor layout."""
    if desktop == "gnome":
        return gnome_deactivate_vkms(before_state or {})
    return True
