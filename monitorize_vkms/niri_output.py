"""Verified output configuration through Niri's user-session IPC client."""

from __future__ import annotations

import json
import shutil
import subprocess
import time

from .errors import CompositorError

POLL_ATTEMPTS = 50
POLL_DELAY = 0.1
REFRESH_TOLERANCE_HZ = 0.75


def _request(*arguments: str):
    executable = shutil.which("niri")
    if not executable:
        raise CompositorError("Niri IPC client is not installed.")
    try:
        result = subprocess.run([executable, "msg", "--json", *arguments],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CompositorError(f"Could not communicate with Niri: {exc}") from exc
    if result.returncode:
        raise CompositorError(f"Niri IPC failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise CompositorError("Niri IPC returned invalid JSON.") from exc


def outputs() -> dict:
    result = _request("outputs")
    if not isinstance(result, dict):
        raise CompositorError("Niri returned invalid output state.")
    for name, output in result.items():
        if not isinstance(output, dict) or not name or output.get("name") != name:
            raise CompositorError("Niri returned ambiguous output identities.")
        modes = output.get("modes")
        current = output.get("current_mode")
        if "current_mode" not in output or not isinstance(modes, list) or (current is not None and (
                type(current) is not int or not 0 <= current < len(modes))):
            raise CompositorError("Niri returned invalid current-mode state.")
        for mode in modes:
            if not isinstance(mode, dict) or any(type(mode.get(key)) is not int or mode[key] <= 0
                                                for key in ("width", "height", "refresh_rate")):
                raise CompositorError("Niri returned invalid mode information.")
        logical = output.get("logical")
        if "logical" not in output or (logical is not None and not isinstance(logical, dict)):
            raise CompositorError("Niri returned invalid enabled-output state.")
    return result


def _active(output: dict) -> bool:
    return output["current_mode"] is not None and output["logical"] is not None


def _require_virtual(name: str) -> None:
    if not isinstance(name, str) or not name.startswith("Virtual-"):
        raise CompositorError("Cannot configure Niri without an exact virtual connector identity.")


def activate(name: str, width: int, height: int, refresh: float):
    _require_virtual(name)
    target = None
    for _attempt in range(POLL_ATTEMPTS):
        target = outputs().get(name)
        if target is not None:
            break
        time.sleep(POLL_DELAY)
    if target is None:
        raise CompositorError(f"Niri did not discover Monitorize connector {name}; check whether this Niri build supports the VKMS DRM device. The connector remains connected for recovery.")
    modes = [mode for mode in target["modes"] if mode["width"] == width and mode["height"] == height
             and abs(mode["refresh_rate"] / 1000 - refresh) <= REFRESH_TOLERANCE_HZ]
    if not modes:
        raise CompositorError(f"Niri does not expose requested mode {width}x{height}@{refresh:g} on {name}.")
    selected = min(modes, key=lambda mode: abs(mode["refresh_rate"] / 1000 - refresh))
    rate = selected["refresh_rate"] / 1000
    _request("output", name, "mode", f"{width}x{height}@{rate:.3f}")
    _request("output", name, "on")
    for _attempt in range(POLL_ATTEMPTS):
        current = outputs().get(name)
        if current and _active(current) and current["modes"][current["current_mode"]] == selected:
            return True, {"name": name, "width": width, "height": height, "refresh_rate": rate}, \
                f"Niri activated {name} at {width}x{height}@{rate:g}Hz"
        time.sleep(POLL_DELAY)
    raise CompositorError(f"Niri did not confirm {name} active at the requested mode.")


def deactivate(name: str) -> bool:
    _require_virtual(name)
    state = outputs()
    target = state.get(name)
    if target is None:
        raise CompositorError(f"Niri could not identify output {name}; refusing disconnect.")
    if not any(_active(output) for other, output in state.items() if other != name):
        raise CompositorError("Refusing to disable the last active Niri output.")
    if target["current_mode"] is None and target["logical"] is None:
        return True
    _request("output", name, "off")
    for _attempt in range(POLL_ATTEMPTS):
        state = outputs()
        target = state.get(name)
        if (target is not None and target["current_mode"] is None and target["logical"] is None
                and any(_active(output) for other, output in state.items() if other != name)):
            return True
        time.sleep(POLL_DELAY)
    raise CompositorError(f"Niri did not confirm {name} disabled; leaving its DRM connector connected.")
