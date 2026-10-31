from __future__ import annotations

import os
import hashlib
import re
import time
from pathlib import Path
from typing import Callable, Any

from monitorize_vkms.errors import MonitorizeVkmsError
from monitorize_vkms.edid import display_from_edid
from monitorize_vkms.names import device_slot, edid_identity

DRM_DISCOVERY_TIMEOUT = 5.0
POLL_INTERVAL = 0.1
DRM_ROOT = Path("/sys/class/drm")


def find_monitorize_card(drm_root: Path | None = None) -> Path | None:
    if drm_root is None:
        drm_root = DRM_ROOT
        
    if not drm_root.exists():
        return None
        
    for entry in drm_root.iterdir():
        if re.match(r"^card\d+$", entry.name):
            device_link = entry / "device"
            if device_link.is_symlink() or device_link.exists():
                try:
                    resolved = device_link.resolve()
                    if device_slot(resolved) == "managed":
                        return entry
                except OSError:
                    pass
    return None


def monitorize_drm_connectors(drm_root: Path | None = None) -> dict[str, dict]:
    if drm_root is None:
        drm_root = DRM_ROOT
        
    connectors = {}
    if not drm_root.exists():
        return connectors
        
    for entry in drm_root.iterdir():
        if re.match(r"^card\d+-.+$", entry.name):
            device_link = entry / "device"
            if device_link.is_symlink() or device_link.exists():
                try:
                    resolved = device_link.resolve()
                    instance_slot = device_slot(resolved)
                    if instance_slot is not None:
                        # Device namespace and EDID together bind a named identity.
                        connector_name = entry.name.split("-", 1)[1]
                        card_name = entry.name.split("-", 1)[0]
                        
                        status = "unknown"
                        try:
                            status = (entry / "status").read_text().strip()
                        except OSError:
                            pass
                            
                        modes = []
                        try:
                            modes = [m for m in (entry / "modes").read_text().splitlines() if m.strip()]
                        except OSError:
                            pass

                        connector_id = None
                        try:
                            connector_id = int(
                                (entry / "connector_id").read_text().strip()
                            )
                        except (OSError, ValueError):
                            pass

                        try:
                            with (entry / "edid").open("rb") as stream:
                                edid = stream.read(128)
                            display = display_from_edid(edid)
                            if instance_slot != 'managed':
                                display = instance_slot if display == edid_identity(instance_slot) else None
                            edid_sha256 = hashlib.sha256(edid).hexdigest() if display else None
                        except OSError:
                            display = None
                            edid_sha256 = None
                            
                        if connector_name in connectors:
                            raise MonitorizeVkmsError(
                                f"Multiple Monitorize DRM cards expose {connector_name}; refusing ambiguous discovery."
                            )
                        connectors[connector_name] = {
                            "name": connector_name,
                            "instance_slot": instance_slot,
                            "path": str(entry),
                            "card": card_name,
                            "status": status,
                            "modes": modes,
                            "connector_id": connector_id,
                            "display": display,
                            "edid_sha256": edid_sha256,
                        }
                except OSError:
                    pass
                    
    return connectors


def wait_for_new_drm_connector(
    before: set[str], 
    width: int, 
    height: int, 
    timeout: float = DRM_DISCOVERY_TIMEOUT, 
    interval: float = POLL_INTERVAL, 
    log: Callable[[str], Any] | None = None,
    drm_root: Path | None = None,
    allow_existing: bool = False,
) -> dict:
    started = time.monotonic()
    deadline = started + timeout
    requested = f"{int(width)}x{int(height)}"
    appeared = None
    appeared_at = None
    last_status = None
    became_connected = False
    waiting_for_mode_logged = False

    def _log(msg: str):
        if log:
            log(msg)

    while time.monotonic() < deadline:
        current = monitorize_drm_connectors(drm_root=drm_root)
        candidates = [entry for name, entry in current.items() if name not in before]
        reused = False
        if not candidates and allow_existing:
            candidates = list(current.values())
            reused = bool(candidates)
        if len(candidates) == 1:
            connector = candidates[0]
            if appeared is None:
                appeared = connector
                appeared_at = time.monotonic()
                if reused:
                    _log(
                        "Reusing persistent DRM connector: "
                        f"{connector['card']}-{connector['name']}"
                    )
                else:
                    _log(f"DRM connector appeared: {connector['card']}-{connector['name']}")

            status = connector["status"].strip().lower()
            if status != last_status:
                if last_status is None:
                    _log(f"Initial DRM status: {status or 'unknown'}")
                else:
                    _log(f"DRM connector status changed: {last_status} -> {status or 'unknown'}")
                last_status = status

            if status == "connected":
                if not became_connected:
                    elapsed_ms = int((time.monotonic() - (appeared_at or time.monotonic())) * 1000)
                    _log(f"DRM connector became connected after {elapsed_ms} ms")
                    became_connected = True
                if requested in connector["modes"]:
                    _log(f"DRM modes ready: {', '.join(connector['modes'])}")
                    return connector
                if not waiting_for_mode_logged:
                    _log(f"DRM connector is connected; waiting for requested mode {requested}")
                    waiting_for_mode_logged = True
        elif len(candidates) > 1:
            raise MonitorizeVkmsError(
                "FAIL_STAGE=DRM_CONNECTOR_APPEAR: kernel registered multiple new "
                "Monitorize VKMS connectors: " + ", ".join(item["name"] for item in candidates)
            )
        time.sleep(interval)

    if appeared is None:
        raise MonitorizeVkmsError(
            "FAIL_STAGE=DRM_CONNECTOR_APPEAR: no new Monitorize Virtual-* connector "
            f"appeared within {timeout:g} seconds"
        )
    if not became_connected:
        raise MonitorizeVkmsError(
            "FAIL_STAGE=DRM_CONNECTOR_STATUS: "
            f"{appeared['card']}-{appeared['name']} appeared but did not become connected "
            f"within {timeout:g} seconds (last status: {last_status or 'unknown'})"
        )
    raise MonitorizeVkmsError(
        "FAIL_STAGE=DRM_MODE_READY: "
        f"{appeared['card']}-{appeared['name']} became connected but requested mode "
        f"{requested} did not appear within {timeout:g} seconds"
    )
