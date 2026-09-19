#!/usr/bin/python3
"""Restricted root helper for Monitorize's source-install VKMS backend."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys


INSTANCE_NAMES = {
    "primary": "monitorize",
}
CUSTOM_MODULE_NAME = "monitorize_vkms"
CONNECTOR_STATUS_CONNECTED = "1"
CONNECTOR_STATUS_DISCONNECTED = "2"
MODULE_PATHS = {
    CUSTOM_MODULE_NAME: Path(f"/sys/module/{CUSTOM_MODULE_NAME}"),
}


class VkmsHelperError(RuntimeError):
    pass


class CustomEdidUnsupported(VkmsHelperError):
    pass


def _require_polkit_root() -> None:
    if os.geteuid() != 0:
        raise VkmsHelperError("Administrator authorization is required to create a VKMS virtual display.")
    if not os.environ.get("PKEXEC_UID", "").isdigit():
        raise VkmsHelperError("This helper must be started through Polkit.")


def _decode_mount_path(value: str) -> str:
    for escaped, literal in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escaped, literal)
    return value


def _configfs_mounts() -> list[Path]:
    mounts = []
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError as exc:
        raise VkmsHelperError(f"Could not inspect mounted filesystems: {exc}") from exc
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator + 1 < len(fields) and fields[separator + 1] == "configfs":
            mounts.append(Path(_decode_mount_path(fields[4])))
    return mounts


def _module_loaded(module_name: str) -> bool:
    return MODULE_PATHS[module_name].is_dir()


def _configfs_root(logs: list[str]) -> Path:
    mounts = _configfs_mounts()
    for mount in sorted(mounts, key=lambda path: len(str(path))):
        if (mount / "vkms").is_dir():
            logs.append(f"Using configfs mountpoint {mount}")
            return mount
    if mounts:
        raise VkmsHelperError("This kernel's VKMS driver does not expose the required configfs interface.")
    raise VkmsHelperError("Linux configfs is unavailable; reboot after installing monitorize-vkms.")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError as exc:
        raise VkmsHelperError(f"Could not read {path}: {exc}") from exc


def _require_bootstrap(root: Path, logs: list[str]) -> tuple[Path, Path]:
    if not _module_loaded(CUSTOM_MODULE_NAME):
        raise VkmsHelperError(
            "Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms."
        )
    instance = root / "vkms" / INSTANCE_NAMES["primary"]
    connector = instance / "connectors" / "connector0"
    required = (
        instance,
        instance / "planes" / "plane0",
        instance / "crtcs" / "crtc0",
        instance / "encoders" / "encoder0",
        connector,
        instance / "planes" / "plane0" / "possible_crtcs" / "crtc0",
        instance / "encoders" / "encoder0" / "possible_crtcs" / "crtc0",
        connector / "possible_encoders" / "encoder0",
    )
    if not all(path.exists() or path.is_symlink() for path in required):
        raise VkmsHelperError(
            "Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms."
        )
    if _read(instance / "enabled") != "1" or _read(connector / "dynamic") != "1":
        raise VkmsHelperError(
            "Monitorize VKMS bootstrap is incomplete. Reboot after installing monitorize-vkms."
        )
    logs.append("Using persistent bootstrapped Monitorize VKMS topology")
    return instance, connector


def _write(path: Path, value: str) -> None:
    try:
        path.write_text(value)
    except OSError as exc:
        raise VkmsHelperError(f"Could not write {path.name}: {exc}") from exc


def _write_bytes(path: Path, value: bytes) -> None:
    try:
        with path.open("wb", buffering=0) as target:
            target.write(value)
    except OSError as exc:
        raise VkmsHelperError(f"Could not write {path.name}: {exc}") from exc


def _validate_edid(edid: bytes) -> None:
    if len(edid) != 128:
        raise VkmsHelperError("Custom VKMS EDID must be exactly 128 bytes.")
    if edid[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00":
        raise VkmsHelperError("Custom VKMS EDID has an invalid header.")
    if sum(edid) % 256:
        raise VkmsHelperError("Custom VKMS EDID checksum is invalid.")


def _probe_custom_edid_support(root: Path, logs: list[str]) -> bool:
    _instance, connector = _require_bootstrap(root, logs)
    supported = (connector / "edid").is_file() and (connector / "edid_enabled").is_file()
    logs.append("VKMS custom EDID capability: " + ("supported" if supported else "unsupported"))
    return supported


def _disconnect_connector(connector: Path, logs: list[str]) -> bool:
    if _read(connector / "enabled") != "1":
        raise VkmsHelperError(
            "The persistent Monitorize VKMS connector is not registered. Reinstall and reboot."
        )
    if _read(connector / "status") == CONNECTOR_STATUS_DISCONNECTED:
        return False
    logs.append("Disconnecting persistent Monitorize VKMS connector")
    _write(connector / "status", CONNECTOR_STATUS_DISCONNECTED)
    return True


def _enable_connector(connector: Path, logs: list[str], custom_edid: bytes | None) -> bool:
    logs.append("Configuring persistent Monitorize VKMS connector while disconnected")
    _disconnect_connector(connector, logs)
    if custom_edid is not None:
        _validate_edid(custom_edid)
        edid = connector / "edid"
        edid_enabled = connector / "edid_enabled"
        if not edid.is_file() or not edid_enabled.is_file():
            raise CustomEdidUnsupported("The bootstrapped VKMS connector does not expose per-connector EDID support.")
        logs.append("Writing validated custom EDID while connector is disconnected")
        _write_bytes(edid, custom_edid)
        _write(edid_enabled, "1")
        logs.append("Custom EDID written and enabled")
    else:
        _write(connector / "edid_enabled", "0")
    logs.append("Connecting persistent Monitorize VKMS connector")
    _write(connector / "status", CONNECTOR_STATUS_CONNECTED)
    if _read(connector / "status") != CONNECTOR_STATUS_CONNECTED:
        raise VkmsHelperError("Persistent Monitorize VKMS connector did not connect.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Monitorize's fixed VKMS configfs instances.")
    parser.add_argument("operation", choices=("create", "create-custom", "destroy", "status", "capability"))
    parser.add_argument("--slot", choices=tuple(INSTANCE_NAMES), default="primary")
    parser.add_argument("edid_base64", nargs="?")
    args = parser.parse_args()
    _require_polkit_root()

    logs: list[str] = []
    root = _configfs_root(logs)
    instance, connector = _require_bootstrap(root, logs)
    changed = False
    
    response = {
        "success": True,
        "operation": args.operation,
        "slot": args.slot,
        "instance": str(instance),
    }

    if args.operation == "create":
        changed = _enable_connector(connector, logs, None)
    elif args.operation == "create-custom":
        if not args.edid_base64:
            raise VkmsHelperError("Custom VKMS creation requires an EDID payload.")
        try:
            custom_edid = base64.b64decode(args.edid_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise VkmsHelperError("Custom VKMS EDID payload is not valid base64.") from exc
        changed = _enable_connector(connector, logs, custom_edid)
    elif args.operation == "destroy":
        changed = _disconnect_connector(connector, logs)
    elif args.operation == "capability":
        capability = "supported" if _probe_custom_edid_support(root, logs) else "unsupported"
        response["capability"] = capability
    elif args.operation == "status":
        connector_enabled = _read(connector / "enabled") == "1"
        edid_supported = (connector / "edid").is_file() and (connector / "edid_enabled").is_file()
        edid_enabled = False
        if edid_supported:
            edid_enabled = _read(connector / "edid_enabled") == "1"
        
        response["connector_enabled"] = connector_enabled
        response["connector_connected"] = (
            _read(connector / "status") == CONNECTOR_STATUS_CONNECTED
        )
        response["edid_enabled"] = edid_enabled
        response["edid_supported"] = edid_supported

    response["changed"] = changed
    response["logs"] = logs

    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CustomEdidUnsupported as exc:
        print(json.dumps({"success": False, "message": str(exc), "error_kind": "custom_edid_unsupported"}))
        sys.exit(1)
    except (OSError, VkmsHelperError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}))
        sys.exit(1)
