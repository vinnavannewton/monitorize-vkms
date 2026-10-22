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
from monitorize_vkms.configfs import CONFIGFS_INSTANCE
from monitorize_vkms.compositor import detect_compositor
from monitorize_vkms.drm import (
    find_monitorize_card,
    monitorize_drm_connectors,
)
from monitorize_vkms.edid import generate_edid
from monitorize_vkms.errors import (
    MonitorizeVkmsError,
    EdidError,
    HelperError,
    ManagedDisplayError,
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
        generate_edid(width, height, refresh)
    except EdidError as exc:
        if args.json:
            return _json_error(str(exc), "edid_error")
        print(f"Error generating EDID: {exc}", file=sys.stderr)
        return 1

    try:
        helper_res = helper_response("create", mode=(width, height, refresh), slot="auto")
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

    drm_conn = monitorize_drm_connectors().get(helper_res.get("connector"), {})
    desktop = helper_res.get("compositor")
    comp_details = helper_res.get("compositor_details", {})
    comp_msg = ""

    result = {
        "success": True,
        "width": width,
        "height": height,
        "refresh_rate": refresh,
        "drm_connector": helper_res.get("connector", drm_conn.get("name", "unknown")),
        "drm_card": helper_res.get("card", drm_conn.get("card", "unknown")),
        "drm_path": drm_conn.get("path", ""),
        "compositor": desktop,
        "compositor_details": comp_details,
        "status": "active",
        "created": helper_res.get("created", True),
        "display": helper_res.get("slot", "mon1"),
    }

    if args.json and (not args.foreground or not helper_res.get("created", True)):
        return _json_out(result)

    if not args.json:
        print(f"Virtual display created successfully!")
        print(f"  Display   : {result['display']}")
        print(f"  Connector : {result['drm_card']}-{result['drm_connector']}")
        print(f"  Mode      : {width}x{height}@{refresh:g}Hz")
        print(f"  Compositor: {desktop or 'None (DRM-only)'}")
        if comp_msg:
            print(f"  Status    : {comp_msg}")

    if args.foreground and helper_res.get("created", True):
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
                helper_response("destroy", generation=helper_res.get("generation"), slot=result["display"])
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
        try:
            helper_res = helper_response("destroy" if args.display else "destroy-all", slot=args.display)
        except ManagedDisplayError:
            from monitorize_vkms.remote_login_client import remove_reserved
            helper_res = remove_reserved()
        outcomes = helper_res.get("results", {})
        if outcomes.get("mon1", {}).get("error_kind") == "protected_display":
            from monitorize_vkms.remote_login_client import remove_reserved
            try:
                managed = remove_reserved()
                outcomes["mon1"] = {"changed": bool(managed.get("changed"))}
            except HelperError as error:
                outcomes["mon1"] = {"error": str(error)}
            helper_res["success"] = all("error" not in item for item in outcomes.values())
            helper_res["changed"] = any(item.get("changed") for item in outcomes.values())
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
        "success": helper_res.get("success", True),
        "removed": changed,
        "results": helper_res.get("results", {}),
        "message": ("Some displays were removed; others need attention."
                    if not helper_res.get("success", True) and changed else
                    "Virtual display removed." if changed else
                    "No active virtual display was found."),
    }
    if args.json:
        return _json_out(data, 0 if data["success"] else 1)

    print(data["message"])
    for slot, outcome in data["results"].items():
        if "error" in outcome:
            print(f"{slot}: {outcome['error']}", file=sys.stderr)
    return 0 if data["success"] else 1


def cmd_list(args: argparse.Namespace) -> int:
    connectors = monitorize_drm_connectors()
    card = find_monitorize_card()
    card_name = card.name if card else "None"
    labelled = list(connectors.values())

    if args.json:
        return _json_out({
            "success": True,
            "card": card_name,
            "connectors": labelled,
        })

    if not connectors:
        print(f"No active Monitorize VKMS displays found (Card: {card_name}).")
        return 0

    print(f"Monitorize VKMS Displays (Card: {card_name}):")
    for info in labelled:
        print(f"  • {info['display'] or 'unknown'} ({info['card']}-{info['name']}) [{info['status']}]")
        modes_str = ", ".join(info["modes"][:5])
        if len(info["modes"]) > 5:
            modes_str += f" (+{len(info['modes']) - 5} more)"
        print(f"    Modes: {modes_str or 'none'}")
        print(f"    Path : {info['path']}")

    return 0


def cmd_status(args: argparse.Namespace) -> int:
    module_loaded = Path("/sys/module/monitorize_vkms").is_dir()
    configfs_dev = CONFIGFS_INSTANCE
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
    second_path = configfs_dev / "connectors/connector1"
    second_enabled = (second_path / "enabled").is_file() and (second_path / "enabled").read_text().strip() == "1"
    second_connected = (second_path / "status").is_file() and (second_path / "status").read_text().strip() == "1"
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
            "connector1_enabled": second_enabled,
            "connector1_registered": second_enabled,
            "connector1_connected": second_connected,
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
    print(f"  Connector1      : {'Registered' if second_enabled else 'NOT registered'}")
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

    # 2. ConfigFS mounted
    configfs = Path("/sys/kernel/config")
    check(
        "ConfigFS filesystem mounted",
        configfs.is_dir(),
        f"Path: {configfs}",
        "Mount configfs: sudo mount -t configfs none /sys/kernel/config",
    )

    # 3. Topology bootstrapped
    instance = CONFIGFS_INSTANCE
    bootstrap_ok = (
        instance.is_dir()
        and (instance / "enabled").is_file()
        and (instance / "enabled").read_text().strip() == "1"
        and (instance / "connectors/connector0/enabled").is_file()
        and (instance / "connectors/connector0/enabled").read_text().strip() == "1"
        and (instance / "connectors/connector1/enabled").is_file()
        and (instance / "connectors/connector1/enabled").read_text().strip() == "1"
    )
    check(
        "Persistent VKMS topology bootstrapped",
        bootstrap_ok,
        f"Device and persistent connector registered at {instance}"
        if bootstrap_ok
        else "Topology missing, disabled, or connector not registered",
        "Reinstall monitorize-vkms and reboot so KWin discovers the complete KMS card.",
    )

    # 5. Both registered connectors need per-display EDID support.
    edid_ok = all((instance / f"connectors/connector{index}/edid").is_file()
                  and (instance / f"connectors/connector{index}/edid_enabled").is_file()
                  for index in (0, 1))
    check(
        "Both connectors expose custom EDID attributes",
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
    headless_drm = (desktop is None and not os.environ.get("WAYLAND_DISPLAY")
                    and not os.environ.get("DISPLAY")
                    and os.environ.get("XDG_SESSION_TYPE") not in {"wayland", "x11"})
    check(
        "Desktop compositor detected",
        desktop is not None or headless_drm,
        f"Detected: {desktop}" if desktop else ("No compositor; DRM-only mode" if headless_drm else "Unknown display session"),
        "Use GNOME, KDE Plasma, Cinnamon/X11, Niri, or a headless DRM session.",
    )

    create_backends = {"gnome", "kde", "cinnamon", "niri", "hyprland"}
    create_supported = (desktop in create_backends or headless_drm) and (
        desktop != "cinnamon" or os.environ.get("XDG_SESSION_TYPE") == "x11"
    )
    check(
        "Create backend is enabled",
        create_supported,
        ("Hyprland: experimental lifecycle via wlr-randr; requires Aquamarine 0.15.1+; requested mode needs live verification"
         if desktop == "hyprland" else f"Detected backend: {desktop or 'headless DRM'}"),
        "Use GNOME, KDE Plasma, Cinnamon/X11, Niri, or a headless DRM session; other compositor adapters are disabled until mode verification is implemented.",
    )
    if desktop == "niri":
        try:
            from monitorize_vkms.niri_output import outputs
            outputs()
            niri_ok, detail = True, "Niri IPC output queries succeeded"
        except MonitorizeVkmsError as exc:
            niri_ok, detail = False, str(exc)
        check("Niri IPC connection available", niri_ok, detail,
              "Run from a terminal inside Niri with NIRI_SOCKET set and a Polkit agent running.")
    remove_supported = create_supported
    check(
        "Live removal backend is enabled",
        remove_supported,
        f"Live removal backend: {desktop or 'headless DRM'}",
        "Live removal is disabled for COSMIC and unvalidated compositors because protocol acknowledgement does not prove renderer teardown.",
    )
    has_systemd = bool(shutil.which("loginctl") and shutil.which("systemctl")
                       and Path("/run/systemd/system").is_dir())
    check(
        "systemd/logind lifecycle support",
        has_systemd,
        "loginctl and systemctl are available" if has_systemd else "This source installer requires systemd and systemd-logind",
        "Use a systemd-based distribution or install through a separately maintained service-manager adapter.",
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

    p_update = subparsers.add_parser(
        "update", help="Update the source checkout and run the installer with sudo"
    )
    p_update.add_argument("--source", metavar="PATH", help="Original Git checkout (overrides saved location)")

    p_reinstall = subparsers.add_parser(
        "reinstall", help="Run the installer from the existing source checkout without pulling"
    )
    p_reinstall.add_argument("--source", metavar="PATH", help="Original Git checkout (overrides saved location)")

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
        "remove", help="Remove mon1, mon2, or both owned virtual displays"
    )
    p_remove.add_argument("display", nargs="?", choices=("mon1", "mon2"),
                          help="Display to remove; omit to remove both")
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

    if parsed.command == "update":
        from monitorize_vkms.update import update
        return update(parsed.source)
    elif parsed.command == "reinstall":
        from monitorize_vkms.update import update
        return update(parsed.source, reinstall=True)
    elif parsed.command == "create":
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
