"""CLI entry point for monitorize-vkms."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import shutil
import signal
import sys
from typing import Any

from monitorize_vkms import __version__
from monitorize_vkms.compositor import (
    activate_in_compositor,
    deactivate_in_compositor,
    detect_compositor,
    get_compositor_snapshot,
)
from monitorize_vkms.drm import (
    find_monitorize_card,
    monitorize_drm_connectors,
    wait_for_new_drm_connector,
)
from monitorize_vkms.edid import generate_edid
from monitorize_vkms.errors import (
    CompositorError,
    EdidError,
    HelperError,
    MonitorizeVkmsError,
    PolkitDenied,
)
from monitorize_vkms.helper_client import HELPER_PATH, helper_response
from monitorize_vkms.mode import parse_mode


def _json_out(data: dict[str, Any], exit_code: int = 0) -> int:
    print(json.dumps(data, indent=2))
    return exit_code


def _json_error(message: str, error_type: str = "error", exit_code: int = 1) -> int:
    print(json.dumps({"success": False, "error_type": error_type, "message": message}, indent=2))
    return exit_code


def cmd_create(args: argparse.Namespace) -> int:
    try:
        width, height, refresh = parse_mode(args.mode)
    except ValueError as exc:
        if args.json:
            return _json_error(str(exc), "invalid_mode")
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"Creating virtual display {width}x{height}@{refresh:g}Hz...")

    try:
        desktop, before_comp = get_compositor_snapshot()
    except CompositorError as exc:
        if args.json:
            return _json_error(str(exc), "compositor_error")
        print(f"Compositor Error: {exc}", file=sys.stderr)
        return 1
    drm_before = set(monitorize_drm_connectors())

    try:
        edid = generate_edid(width, height, refresh)
    except EdidError as exc:
        if args.json:
            return _json_error(str(exc), "edid_error")
        print(f"Error generating EDID: {exc}", file=sys.stderr)
        return 1

    try:
        helper_res = helper_response("create-custom", edid=edid)
    except PolkitDenied as exc:
        if args.json:
            return _json_error(str(exc), "polkit_denied")
        print(f"Authentication Error: {exc}", file=sys.stderr)
        return 1
    except HelperError as exc:
        if args.json:
            return _json_error(str(exc), "helper_error")
        print(f"Helper Error: {exc}", file=sys.stderr)
        return 1

    # Wait for DRM connector in sysfs
    def _drm_log(msg: str):
        if not args.json:
            print(f"  [DRM] {msg}")

    try:
        drm_conn = wait_for_new_drm_connector(
            drm_before, width, height, log=_drm_log, allow_existing=True
        )
    except MonitorizeVkmsError as exc:
        if args.json:
            return _json_error(str(exc), "drm_error")
        print(f"DRM Error: {exc}", file=sys.stderr)
        return 1

    # Activate in compositor if present
    comp_details = {}
    comp_msg = ""
    try:
        ok, comp_details, comp_msg = activate_in_compositor(
            desktop,
            before_comp,
            width,
            height,
            refresh,
            expected_output_name=drm_conn["name"],
        )
        if not ok:
            raise CompositorError(comp_msg)
    except CompositorError as exc:
        if args.json:
            return _json_error(str(exc), "compositor_error")
        print(f"Compositor Error: {exc}", file=sys.stderr)
        return 1

    result = {
        "success": True,
        "width": width,
        "height": height,
        "refresh_rate": refresh,
        "drm_connector": drm_conn["name"],
        "drm_card": drm_conn["card"],
        "drm_path": drm_conn["path"],
        "compositor": desktop,
        "compositor_details": comp_details,
        "status": "active",
    }

    if args.json and not args.foreground:
        return _json_out(result)

    if not args.json:
        print(f"Virtual display created successfully!")
        print(f"  Connector : {drm_conn['card']}-{drm_conn['name']}")
        print(f"  Mode      : {width}x{height}@{refresh:g}Hz")
        print(f"  Compositor: {desktop or 'None (DRM-only)'}")
        if comp_msg:
            print(f"  Status    : {comp_msg}")

    if args.foreground:
        if args.json:
            # Print initial json line then block
            print(json.dumps(result))
            sys.stdout.flush()
        else:
            print("\nRunning in foreground mode. Press Ctrl+C or send EOF to exit...")

        cleaned_up = False

        def _cleanup():
            nonlocal cleaned_up
            if cleaned_up:
                return
            cleaned_up = True
            if not args.json:
                print("\nCleaning up virtual display...")
            try:
                deactivate_in_compositor(desktop, before_comp)
            except Exception as exc:
                print(f"Cleanup refused; VKMS remains connected: {exc}", file=sys.stderr)
                return False
            try:
                helper_response("destroy")
            except Exception as exc:
                print(f"VKMS disconnect failed: {exc}", file=sys.stderr)
                return False
            return True

        def _stop_from_signal(*_):
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _stop_from_signal)
        signal.signal(signal.SIGTERM, _stop_from_signal)

        try:
            while True:
                ready, _, _ = select.select([sys.stdin], [], [], 0.5)
                if ready:
                    line = sys.stdin.readline()
                    if not line or line.strip() == "quit":
                        break
        except KeyboardInterrupt:
            pass
        finally:
            cleanup_ok = _cleanup()
        if not cleanup_ok:
            return 1

    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    try:
        desktop, before_comp = get_compositor_snapshot()
    except CompositorError as exc:
        message = f"Removal refused; VKMS remains connected: {exc}"
        if args.json:
            return _json_error(message, "compositor_error")
        print(message, file=sys.stderr)
        return 1

    try:
        deactivate_in_compositor(desktop, before_comp)
    except Exception as exc:
        message = f"Removal refused; VKMS remains connected: {exc}"
        if args.json:
            return _json_error(message, "compositor_error")
        print(message, file=sys.stderr)
        return 1

    try:
        helper_res = helper_response("destroy")
    except PolkitDenied as exc:
        if args.json:
            return _json_error(str(exc), "polkit_denied")
        print(f"Authentication Error: {exc}", file=sys.stderr)
        return 1
    except HelperError as exc:
        if args.json:
            return _json_error(str(exc), "helper_error")
        print(f"Helper Error: {exc}", file=sys.stderr)
        return 1

    changed = helper_res.get("changed", False)
    data = {
        "success": True,
        "removed": changed,
        "message": "Virtual display removed." if changed else "No active virtual display was found.",
    }
    if args.json:
        return _json_out(data)

    print(data["message"])
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    connectors = monitorize_drm_connectors()
    card = find_monitorize_card()
    card_name = card.name if card else "None"

    if args.json:
        return _json_out({
            "success": True,
            "card": card_name,
            "connectors": list(connectors.values()),
        })

    if not connectors:
        print(f"No active Monitorize VKMS displays found (Card: {card_name}).")
        return 0

    print(f"Monitorize VKMS Displays (Card: {card_name}):")
    for name, info in connectors.items():
        print(f"  • {info['card']}-{name} [{info['status']}]")
        modes_str = ", ".join(info["modes"][:5])
        if len(info["modes"]) > 5:
            modes_str += f" (+{len(info['modes']) - 5} more)"
        print(f"    Modes: {modes_str or 'none'}")
        print(f"    Path : {info['path']}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    module_loaded = Path("/sys/module/monitorize_vkms").is_dir()
    configfs_dev = Path("/sys/kernel/config/vkms/monitorize")
    device_enabled = (
        (configfs_dev / "enabled").read_text().strip() == "1"
        if (configfs_dev / "enabled").is_file()
        else False
    )
    connector_enabled = (
        (configfs_dev / "connectors/connector0/enabled").read_text().strip() == "1"
        if (configfs_dev / "connectors/connector0/enabled").is_file()
        else False
    )
    connector_status = (
        (configfs_dev / "connectors/connector0/status").read_text().strip()
        if (configfs_dev / "connectors/connector0/status").is_file()
        else ""
    )
    connector_connected = connector_status == "1"
    edid_enabled = (
        (configfs_dev / "connectors/connector0/edid_enabled").read_text().strip() == "1"
        if (configfs_dev / "connectors/connector0/edid_enabled").is_file()
        else False
    )
    card = find_monitorize_card()
    connectors = monitorize_drm_connectors()
    desktop = detect_compositor()

    status_data = {
        "success": True,
        "version": __version__,
        "kernel_module": {
            "name": "monitorize_vkms",
            "loaded": module_loaded,
        },
        "topology": {
            "configfs_path": str(configfs_dev),
            "device_enabled": device_enabled,
            "connector0_enabled": connector_enabled,
            "connector0_registered": connector_enabled,
            "connector0_connected": connector_connected,
            "connector0_edid_enabled": edid_enabled,
        },
        "drm": {
            "card": card.name if card else None,
            "card_path": str(card) if card else None,
            "active_connectors": list(connectors.values()),
        },
        "compositor": desktop,
    }

    if args.json:
        return _json_out(status_data)

    print(f"Monitorize VKMS Status (v{__version__}):")
    print(f"  Kernel Module   : {'Loaded' if module_loaded else 'NOT loaded'}")
    print(f"  ConfigFS Device : {'Enabled' if device_enabled else 'Disabled / Missing'}")
    print(f"  Connector0      : {'Registered' if connector_enabled else 'NOT registered'}")
    print(f"  Display State   : {'Connected' if connector_connected else 'Disconnected (ready)'}")
    print(f"  Custom EDID     : {'Enabled' if edid_enabled else 'Disabled'}")
    print(f"  DRM Card        : {card.name if card else 'None'}")
    print(f"  Desktop Session : {desktop or 'Unknown / None'}")
    if connectors:
        print("  Active Displays :")
        for name, info in connectors.items():
            print(f"    - {name}: status={info['status']}, modes={len(info['modes'])}")
    else:
        print("  Active Displays : None")

    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = []

    def check(name: str, passed: bool, detail: str = "", remedy: str = ""):
        checks.append({
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
            "remedy": remedy if not passed else "",
        })

    # 1. Kernel module
    mod_path = Path("/sys/module/monitorize_vkms")
    check(
        "Kernel module 'monitorize_vkms' loaded",
        mod_path.is_dir(),
        f"Path: {mod_path}" if mod_path.is_dir() else "Module not loaded in kernel",
        "Run: sudo ./install.sh and reboot, or run: sudo modprobe monitorize_vkms create_default_dev=0",
    )

    # 2. Stock VKMS conflict
    stock_path = Path("/sys/module/vkms")
    check(
        "Stock 'vkms' module not conflicting",
        not stock_path.is_dir(),
        "Stock vkms is not loaded" if not stock_path.is_dir() else "Stock vkms is loaded alongside monitorize_vkms!",
        "Stock vkms interferes with monitorize_vkms. Unload it: sudo rmmod vkms",
    )

    # 3. ConfigFS mounted
    configfs = Path("/sys/kernel/config")
    check(
        "ConfigFS filesystem mounted",
        configfs.is_dir(),
        f"Path: {configfs}",
        "Mount configfs: sudo mount -t configfs none /sys/kernel/config",
    )

    # 4. Topology bootstrapped
    instance = Path("/sys/kernel/config/vkms/monitorize")
    bootstrap_ok = (
        instance.is_dir()
        and (instance / "enabled").is_file()
        and (instance / "enabled").read_text().strip() == "1"
        and (instance / "connectors/connector0/enabled").is_file()
        and (instance / "connectors/connector0/enabled").read_text().strip() == "1"
    )
    check(
        "Persistent VKMS topology bootstrapped",
        bootstrap_ok,
        f"Device and persistent connector registered at {instance}"
        if bootstrap_ok
        else "Topology missing, disabled, or connector not registered",
        "Reinstall monitorize-vkms and reboot so KWin discovers the complete KMS card.",
    )

    # 5. Connector0 properties
    conn0 = instance / "connectors/connector0"
    edid_ok = (conn0 / "edid").is_file() and (conn0 / "edid_enabled").is_file()
    check(
        "Connector0 custom EDID attributes supported",
        edid_ok,
        "edid and edid_enabled files present in configfs" if edid_ok else "Missing custom EDID attributes",
        "Module does not support custom EDID. Ensure monitorize-vkms 0.2.1+ is installed.",
    )

    # 6. DRM Card identified
    card = find_monitorize_card()
    check(
        "Monitorize DRM card recognized in sysfs",
        card is not None,
        f"DRM Card: {card.name}" if card else "No /faux/monitorize card found in /sys/class/drm",
        "Check dmesg for driver errors or restart the bootstrap service.",
    )

    # 7. Helper binary
    helper_ok = HELPER_PATH.is_file() and os.access(HELPER_PATH, os.X_OK)
    check(
        "Privileged helper binary installed",
        helper_ok,
        f"Path: {HELPER_PATH}" if helper_ok else f"Missing or not executable: {HELPER_PATH}",
        "Run: sudo ./install.sh",
    )

    # 8. Polkit pkexec
    pkexec_path = shutil.which("pkexec")
    check(
        "Polkit 'pkexec' utility available",
        pkexec_path is not None,
        f"Path: {pkexec_path}" if pkexec_path else "pkexec not found in PATH",
        "Install polkit package on your distribution.",
    )

    # 9. Polkit policy
    polkit_policy = Path(
        "/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize-vkms.policy"
    )
    check(
        "Polkit policy installed",
        polkit_policy.is_file(),
        f"Path: {polkit_policy}" if polkit_policy else "Policy file missing",
        "Run: sudo ./install.sh",
    )

    # 10. Compositor
    desktop = detect_compositor()
    check(
        "Desktop compositor detected",
        desktop is not None,
        f"Detected: {desktop}" if desktop else "No supported compositor detected (running headless or unknown session)",
        "Use a supported GNOME, KDE Plasma, Cinnamon/X11, or wlroots session.",
    )

    all_passed = all(c["status"] == "PASS" for c in checks)

    if args.json:
        return _json_out({
            "success": all_passed,
            "all_passed": all_passed,
            "checks": checks,
        }, exit_code=0 if all_passed else 1)

    print(f"Monitorize VKMS Doctor (v{__version__}):\n")
    for c in checks:
        icon = "✓" if c["status"] == "PASS" else "✗"
        print(f"  [{icon}] {c['name']}")
        if c["detail"]:
            print(f"      {c['detail']}")
        if c["remedy"]:
            print(f"      Remedy: {c['remedy']}")

    print("\nResult: " + ("All checks passed!" if all_passed else "Some checks failed. See remedies above."))
    return 0 if all_passed else 1


def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="monitorize-vkms",
        description="Manage standalone Monitorize VKMS virtual displays.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # create
    p_create = subparsers.add_parser(
        "create", help="Create a virtual display with specified resolution and refresh rate"
    )
    p_create.add_argument(
        "mode",
        help="Resolution and refresh rate (e.g. 1920x1080, 2340x1080@60, 2560x1440@120)",
    )
    p_create.add_argument(
        "-f",
        "--foreground",
        action="store_true",
        help="Run in foreground and automatically destroy virtual display when exited or on stdin EOF",
    )
    p_create.add_argument(
        "--json",
        action="store_true",
        help="Output result as machine-readable JSON",
    )

    # remove
    p_remove = subparsers.add_parser(
        "remove", help="Remove the active virtual display"
    )
    p_remove.add_argument(
        "--json",
        action="store_true",
        help="Output result as machine-readable JSON",
    )

    # list
    p_list = subparsers.add_parser(
        "list", help="List active Monitorize DRM connectors"
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        help="Output result as machine-readable JSON",
    )

    # status
    p_status = subparsers.add_parser(
        "status", help="Show system status and VKMS topology"
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Output result as machine-readable JSON",
    )

    # doctor
    p_doc = subparsers.add_parser(
        "doctor", help="Diagnose environment and configuration issues"
    )
    p_doc.add_argument(
        "--json",
        action="store_true",
        help="Output result as machine-readable JSON",
    )

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        return 0

    if parsed.command == "create":
        return cmd_create(parsed)
    elif parsed.command == "remove":
        return cmd_remove(parsed)
    elif parsed.command == "list":
        return cmd_list(parsed)
    elif parsed.command == "status":
        return cmd_status(parsed)
    elif parsed.command == "doctor":
        return cmd_doctor(parsed)

    return 0


if __name__ == "__main__":
    sys.exit(main())
