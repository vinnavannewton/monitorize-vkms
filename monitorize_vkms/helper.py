#!/usr/bin/python3 -I
"""Restricted root helper for Monitorize's source-install VKMS backend."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import secrets
import signal
import shutil
import stat
import socket
import struct
import subprocess
import sys
import time

if __name__ == "__main__":
    # Root wrappers live in libexec; imported helpers spawn their package copy.
    # Isolated Python strips the script directory, so both layouts need an
    # explicit package root derived from this fixed installed script.
    script = Path(__file__).resolve()
    package_root = (script.parent.parent if script.parent.name == "monitorize_vkms"
                    else script.parents[2] / "lib" / "monitorize-vkms")
    sys.path.insert(0, str(package_root))

from monitorize_vkms.configfs import CONFIGFS_SUBSYSTEM

INSTANCE_NAMES = {
    "mon1": "connector0",
    "mon2": "connector1",
}
CUSTOM_MODULE_NAME = "monitorize_vkms"
BOOT_DISPLAY_PROTOCOL = 1
CONNECTOR_STATUS_CONNECTED = "1"
CONNECTOR_STATUS_DISCONNECTED = "2"
LIFECYCLE_LOCK = Path("/run/lock/monitorize-vkms.lock")
STATE_DIRECTORY = Path("/run/monitorize-vkms")
LEASE_PATH = STATE_DIRECTORY / "primary.json"
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


def _acquire_lifecycle_lock():
    if os.geteuid() != 0:
        raise VkmsHelperError("Display lifecycle operations require the privileged helper.")
    fd = None
    try:
        fd = os.open(LIFECYCLE_LOCK, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise VkmsHelperError("The Monitorize VKMS lifecycle lock is not a root-owned regular file.")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, VkmsHelperError) as exc:
        if fd is not None:
            os.close(fd)
        if isinstance(exc, VkmsHelperError):
            raise
        raise VkmsHelperError("Another Monitorize VKMS install, bootstrap, or display operation is running.") from exc


def _active_session(uid: int, *, allow_inactive=False) -> tuple[str, dict[str, str]]:
    """Resolve one active local logind session and build a small worker environment."""
    loginctl = shutil.which("loginctl")
    if not loginctl:
        raise VkmsHelperError("systemd-logind is required to identify the caller's desktop session safely.")
    try:
        sessions = subprocess.run(
            [loginctl, "show-user", str(uid), "--property=Sessions", "--value"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.split()
        if len(sessions) > 16:
            raise VkmsHelperError("Too many login sessions to safely select a desktop session.")
        active: list[tuple[str, dict[str, str]]] = []
        for session in sessions:
            result = subprocess.run(
                [loginctl, "show-session", session, "--no-pager", "--property=Active", "--property=Remote",
                 "--property=Type", "--property=Desktop", "--property=Leader", "--property=Display", "--property=User"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if values.get("User") != str(uid):
                raise VkmsHelperError("The selected login session does not belong to the authenticated caller.")
            if (not allow_inactive and values.get("Active") != "yes") or values.get("Remote") != "no":
                continue
            if allow_inactive and values.get('Type') not in ('x11', 'wayland'):
                continue
            if values.get("Type") not in ("x11", "wayland", "tty"):
                continue
            env: dict[str, str] = {}
            try:
                leader = int(values.get("Leader", "0"))
                env.update(_parse_session_environment(Path(f"/proc/{leader}/environ").read_bytes()))
            except (OSError, ValueError):
                pass
            account = pwd.getpwuid(uid)
            env.update({"HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
                        "PATH": "/usr/local/bin:/usr/bin:/bin", "XDG_RUNTIME_DIR": f"/run/user/{uid}"})
            env["XDG_SESSION_TYPE"] = values.get("Type", "")
            if values.get("Desktop"):
                env.setdefault("XDG_CURRENT_DESKTOP", values["Desktop"])
            known_dbus_desktop = any(
                name in env.get("XDG_CURRENT_DESKTOP", "").lower()
                for name in ("kde", "plasma", "gnome", "cinnamon")
            )
            if values.get("Type") == "wayland" and not known_dbus_desktop and (
                not env.get("WAYLAND_DISPLAY")
                or ("niri" in env.get("XDG_CURRENT_DESKTOP", "").lower() and not env.get("NIRI_SOCKET"))
            ):
                env.update(_session_connection_environment(uid, session))
            if values.get("Type") == "wayland" and not env.get("WAYLAND_DISPLAY") and not known_dbus_desktop:
                raise VkmsHelperError("Could not resolve the caller's Wayland socket from the active session.")
            if values.get("Type") == "wayland" and not known_dbus_desktop:
                wayland_path = Path(env["WAYLAND_DISPLAY"])
                if not wayland_path.is_absolute():
                    wayland_path = Path(f"/run/user/{uid}") / wayland_path
                wayland_pid = _validate_session_socket(wayland_path, uid, session)
                if env.get("NIRI_SOCKET"):
                    niri_pid = _validate_session_socket(Path(env["NIRI_SOCKET"]), uid, session)
                    if wayland_pid != niri_pid:
                        raise VkmsHelperError("Wayland and Niri IPC sockets belong to different server processes.")
            if values.get("Type") == "x11" and not env.get("DISPLAY"):
                display = values.get("Display", "")
                if display:
                    env["DISPLAY"] = display
                else:
                    raise VkmsHelperError("Could not resolve the caller's X display from the active session.")
            if not env.get("DBUS_SESSION_BUS_ADDRESS") and Path(f"/run/user/{uid}/bus").exists():
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
            env["XDG_SESSION_TYPE"] = values.get("Type", "")
            env["XDG_SESSION_ID"] = session
            if values.get("Desktop"):
                env.setdefault("XDG_CURRENT_DESKTOP", values["Desktop"])
            active.append((session, env))
        if len(active) != 1:
            raise VkmsHelperError("The caller must have exactly one active local desktop session for VKMS changes.")
        if active[0][1].get("XDG_SESSION_TYPE") == "tty":
            _require_no_graphical_sessions(loginctl)
        return active[0]
    except (OSError, subprocess.SubprocessError) as exc:
        raise VkmsHelperError(f"Could not verify the caller's active desktop session: {exc}") from exc


def _require_no_graphical_sessions(loginctl: str) -> None:
    result = subprocess.run([loginctl, "list-sessions", "--no-legend", "--no-pager"],
                            capture_output=True, text=True, timeout=5, check=True)
    sessions = [line.split()[0] for line in result.stdout.splitlines() if line.split()]
    if len(sessions) > 32:
        raise VkmsHelperError("Too many sessions to safely verify headless operation.")
    for session in sessions:
        result = subprocess.run([loginctl, "show-session", session, "--property=Type", "--property=Remote"],
                                capture_output=True, text=True, timeout=3, check=True)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if values.get("Remote") != "yes" and values.get("Type") in {"wayland", "x11"}:
            raise VkmsHelperError("Headless VKMS operations require all local graphical sessions to be logged out.")


def _parse_session_environment(raw: bytes) -> dict[str, str]:
    allowed = {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP",
               "XDG_SESSION_DESKTOP", "DESKTOP_SESSION", "DBUS_SESSION_BUS_ADDRESS", "NIRI_SOCKET"}
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        name = key.decode(errors="ignore")
        if sep and name in allowed:
            env[name] = value.decode(errors="ignore")
    return env


def _process_in_session(process: Path, uid: int, session: str) -> bool:
    """Accept only same-user processes associated with the selected logind session."""
    if process.stat().st_uid != uid:
        return False
    scopes = process.joinpath("cgroup").read_text().splitlines()
    if any(f"session-{session}.scope" in line.split(":", 2)[-1].split("/") for line in scopes):
        return True
    # User-service terminals may live outside the session scope. Their inherited
    # session ID must still identify the already verified active local session.
    return f"XDG_SESSION_ID={session}".encode() in process.joinpath("environ").read_bytes().split(b"\0")


def _validate_session_socket(path: Path, uid: int, session: str) -> int:
    runtime = Path(f"/run/user/{uid}")
    if not path.is_absolute() or path.parent != runtime:
        raise VkmsHelperError("Desktop socket must be directly inside the caller's runtime directory.")
    runtime_info = runtime.lstat()
    info = path.lstat()
    if (not stat.S_ISDIR(runtime_info.st_mode) or runtime_info.st_uid != uid
            or runtime_info.st_mode & 0o022 or not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid):
        raise VkmsHelperError("Desktop socket has unsafe ownership or permissions.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(str(path))
        pid, peer_uid, _gid = struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    if peer_uid != uid or not _process_in_session(Path(f"/proc/{pid}"), uid, session):
        raise VkmsHelperError("Desktop socket does not belong to the selected login session.")
    return pid


def _session_connection_environment(uid: int, session: str) -> dict[str, str]:
    """Recover connection hints, never arbitrary environment or executable paths."""
    candidates: set[tuple[str, str]] = set()
    processes = sorted((path for path in Path("/proc").iterdir() if path.name.isdigit()),
                       key=lambda path: int(path.name))
    if len(processes) > 16384:
        raise VkmsHelperError("Too many processes to safely resolve the desktop session.")
    for process in processes:
        try:
            if not _process_in_session(process, uid, session):
                continue
            values = _parse_session_environment(process.joinpath("environ").read_bytes())
            wayland = values.get("WAYLAND_DISPLAY", "")
            niri = values.get("NIRI_SOCKET", "")
            if wayland:
                candidates.add((wayland, niri))
                if len(candidates) > 32:
                    raise VkmsHelperError("Too many desktop socket candidates in the active session.")
        except (OSError, ValueError):
            continue
    valid: set[tuple[str, str]] = set()
    for wayland, niri in candidates:
        try:
            wayland_path = Path(wayland)
            if not wayland_path.is_absolute():
                wayland_path = Path(f"/run/user/{uid}") / wayland_path
            wayland_pid = _validate_session_socket(wayland_path, uid, session)
            if niri:
                niri_pid = _validate_session_socket(Path(niri), uid, session)
                if wayland_pid != niri_pid:
                    continue
            # Validation confines Wayland sockets to this user's runtime dir.
            # Store their basename so relative and absolute spellings of the
            # same endpoint cannot create false ambiguity. Do not resolve
            # symlinks: socket ownership and path checks still apply first.
            valid.add((wayland_path.name, str(Path(niri)) if niri else ""))
        except (OSError, VkmsHelperError):
            continue
    # Complete Niri pairs have matching server PIDs. Other Wayland endpoints
    # (such as Xwayland Satellite) are not competing Niri compositor sockets.
    complete = {item for item in valid if item[1]}
    if complete:
        valid = complete
    if len(valid) != 1:
        raise VkmsHelperError("Could not uniquely resolve desktop sockets from the active session; run from a terminal in that session.")
    wayland, niri = valid.pop()
    result = {"WAYLAND_DISPLAY": wayland}
    if niri:
        result.update({"NIRI_SOCKET": niri, "XDG_CURRENT_DESKTOP": "niri"})
    return result


def _lease_path(slot: str) -> Path:
    if slot not in INSTANCE_NAMES:
        raise VkmsHelperError("Display must be mon1 or mon2.")
    return LEASE_PATH if slot == "mon1" else STATE_DIRECTORY / "mon2.json"


def _read_lease(slot: str = "mon1") -> dict | None:
    path = _lease_path(slot)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise VkmsHelperError("Monitorize VKMS ownership state has unsafe permissions.")
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise VkmsHelperError(f"Could not safely read Monitorize VKMS ownership state: {exc}") from exc


def _write_lease(lease: dict, slot: str = "mon1") -> None:
    path = _lease_path(slot)
    try:
        STATE_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = STATE_DIRECTORY.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise VkmsHelperError("Monitorize VKMS runtime state directory has unsafe ownership or permissions.")
        temporary = STATE_DIRECTORY / f".{slot}.{secrets.token_hex(8)}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(lease, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise VkmsHelperError(f"Could not persist Monitorize VKMS ownership state: {exc}") from exc


def _remove_lease(slot: str = "mon1") -> None:
    try:
        _lease_path(slot).unlink()
    except FileNotFoundError:
        pass


def _lease_matches_request(lease: dict | None, *, uid: int, session: str, boot_id: str,
                           edid_sha256: str, mode: list[float] | None) -> bool:
    return bool(
        lease
        and lease.get("uid") == uid
        and lease.get("session") == session
        and lease.get("boot_id") == boot_id
        and lease.get("edid_sha256") == edid_sha256
        and (mode is None or lease.get("mode") == mode)
        and lease.get("state") == "active"
    )


def _validate_compositor_support(desktop: str | None, env: dict[str, str]) -> None:
    if desktop == "cinnamon" and env.get("XDG_SESSION_TYPE") != "x11":
        raise VkmsHelperError("Cinnamon display creation is supported only in an X11 session.")
    if desktop == "cosmic":
        raise VkmsHelperError(
            f"Display creation is disabled on {desktop}: this checkout cannot safely "
            "verify mode activation and live removal for that compositor."
        )
    if desktop == "niri" and not env.get("NIRI_SOCKET"):
        raise VkmsHelperError("Niri IPC socket is unavailable in the active session.")
    # Hyprland lifecycle is experimental pending live validation on Aquamarine 0.15.1+.
    if desktop not in {"gnome", "kde", "cinnamon", "niri", "hyprland", None}:
        raise VkmsHelperError(
            f"Display creation is unsupported for detected compositor {desktop!r}; "
            "refusing to connect a display that cannot be safely managed."
        )
    if desktop is None and (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")
                            or env.get("XDG_SESSION_TYPE") in {"wayland", "x11"}):
        raise VkmsHelperError(
            "No supported desktop backend was detected; refusing to connect an unmanaged output."
        )


def _run_worker(uid: int, env: dict[str, str], payload: dict, timeout: float = 30.0) -> dict:
    setpriv = shutil.which("setpriv")
    if not setpriv:
        raise VkmsHelperError("setpriv is required to run compositor operations as the desktop-session owner.")
    command = [setpriv, f"--reuid={uid}", f"--regid={pwd.getpwuid(uid).pw_gid}", "--init-groups",
               sys.executable, "-I", str(Path(__file__).resolve()), "--worker"]
    try:
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, close_fds=True, start_new_session=True) as worker:
            try:
                stdout, stderr = worker.communicate(json.dumps(payload), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(worker.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                worker.communicate()
                raise VkmsHelperError("Desktop-session operation timed out; its command group was stopped.") from exc
            result = subprocess.CompletedProcess(command, worker.returncode, stdout, stderr)
    except OSError as exc:
        raise VkmsHelperError(f"Desktop-session operation could not complete safely: {exc}") from exc
    response = None
    for line in reversed(result.stdout.splitlines()):
        try:
            response = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if result.returncode or not isinstance(response, dict) or not response.get("success"):
        detail = response.get("message", "") if isinstance(response, dict) else ""
        raise VkmsHelperError(detail or result.stderr.strip() or "Desktop-session operation failed.")
    return response


def _worker_main() -> int:
    if os.geteuid() == 0:
        raise VkmsHelperError("Internal compositor worker must not run as root.")
    request = json.load(sys.stdin)
    from monitorize_vkms.compositor import (
        activate_in_compositor, deactivate_in_compositor, detect_compositor, get_compositor_snapshot,
    )
    from monitorize_vkms.errors import CompositorError
    from monitorize_vkms.drm import monitorize_drm_connectors
    action = request.get("action")
    if action == "snapshot":
        desktop, state = get_compositor_snapshot()
        if isinstance(state, set):
            state = sorted(state)
        print(json.dumps({"success": True, "desktop": desktop, "state": state}))
        return 0
    desktop = detect_compositor()
    _validate_compositor_support(desktop, dict(os.environ))
    name = request.get("name")
    current = monitorize_drm_connectors().get(name)
    if not current or current.get("status") != "connected":
        raise VkmsHelperError("The exact Monitorize DRM connector is no longer connected.")
    if request.get("connector_id") is not None and current.get("connector_id") != request["connector_id"]:
        raise VkmsHelperError("The Monitorize DRM connector identity changed during the operation.")
    if request.get("card") is not None and current.get("card") != request["card"]:
        raise VkmsHelperError("The Monitorize DRM card identity changed during the operation.")
    if desktop == "niri":
        matches = list(Path("/sys/class/drm").glob(f"card*-{name}"))
        if len(matches) != 1 or matches[0].name != f"{current.get('card')}-{name}":
            raise VkmsHelperError("Niri connector name is ambiguous across DRM cards; refusing output configuration.")
    if action == "activate":
        width, height, refresh = request["mode"]
        snapshot_state = request.get("snapshot")
        if desktop in ("gnome", "cinnamon") and isinstance(snapshot_state, dict):
            snapshot_state = {name: tuple(identity) for name, identity in snapshot_state.items()}
        ok, details, message = activate_in_compositor(
            desktop, snapshot_state, width, height, refresh,
            expected_output_name=name, expected_connector_id=request.get("connector_id"),
        )
        if not ok:
            raise CompositorError(message)
        print(json.dumps({"success": True, "desktop": desktop, "details": details, "message": message}))
        return 0
    if action == "deactivate":
        if not deactivate_in_compositor(
            desktop, None, target_name=name, target_connector_id=request.get("connector_id")
        ):
            raise CompositorError(f"The compositor did not confirm deactivation of {name}.")
        print(json.dumps({"success": True, "desktop": desktop}))
        return 0
    raise VkmsHelperError("Unknown internal compositor operation.")


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
        if (mount / CONFIGFS_SUBSYSTEM).is_dir():
            logs.append(f"Using configfs mountpoint {mount}")
            return mount
    if mounts:
        raise VkmsHelperError(
            "Monitorize VKMS configfs is not registered. Reboot after installing or upgrading monitorize-vkms."
        )
    raise VkmsHelperError("Linux configfs is unavailable; reboot after installing monitorize-vkms.")


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError as exc:
        raise VkmsHelperError(f"Could not read {path}: {exc}") from exc


def _require_bootstrap(root: Path, logs: list[str], slot: str = "mon1") -> tuple[Path, Path]:
    if not _module_loaded(CUSTOM_MODULE_NAME):
        raise VkmsHelperError(
            "Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms."
        )
    if slot not in INSTANCE_NAMES:
        raise VkmsHelperError("Display must be mon1 or mon2.")
    instance = root / CONFIGFS_SUBSYSTEM / "monitorize"
    required = [instance]
    for index in (0, 1):
        plane = instance / "planes" / f"plane{index}"
        crtc = instance / "crtcs" / f"crtc{index}"
        encoder = instance / "encoders" / f"encoder{index}"
        connector = instance / "connectors" / f"connector{index}"
        required.extend((plane, crtc, encoder, connector,
                         plane / "possible_crtcs" / f"crtc{index}",
                         encoder / "possible_crtcs" / f"crtc{index}",
                         connector / "possible_encoders" / f"encoder{index}"))
    if not all(path.exists() or path.is_symlink() for path in required):
        raise VkmsHelperError(
            "Two-display Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms."
        )
    connector = instance / "connectors" / INSTANCE_NAMES[slot]
    if (_read(instance / "enabled") != "1" or
            any(_read(instance / "connectors" / name / "dynamic") != "1" or
                _read(instance / "connectors" / name / "enabled") != "1"
                for name in INSTANCE_NAMES.values())):
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


def _validate_requested_mode(mode: list[float]) -> list[float]:
    try:
        width, height, refresh = int(mode[0]), int(mode[1]), float(mode[2])
        if (not all(math.isfinite(float(value)) for value in mode)
                or width != mode[0] or height != mode[1]
                or not (1 <= width <= 4095 and 1 <= height <= 4095 and 24 <= refresh <= 240)):
            raise ValueError("Mode dimensions and refresh must be finite and in the supported range")
    except (ValueError, OverflowError, IndexError, TypeError) as exc:
        raise VkmsHelperError(f"Invalid requested display mode: {exc}") from exc
    return [width, height, refresh]


def _validate_edid_mode(edid: bytes, mode: list[float]) -> None:
    from monitorize_vkms.edid import parse_preferred_timing

    width, height, refresh = _validate_requested_mode(mode)
    try:
        timing = parse_preferred_timing(edid)
    except (ValueError, ArithmeticError) as exc:
        raise VkmsHelperError(f"Could not validate custom EDID timing: {exc}") from exc
    if (timing.width, timing.height) != (width, height) or abs(timing.refresh_hz - refresh) > 0.75:
        raise VkmsHelperError(
            f"Custom EDID preferred timing {timing.width}x{timing.height}@{timing.refresh_hz:g}Hz "
            f"does not match requested {width}x{height}@{refresh:g}Hz."
        )


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
    if custom_edid is not None:
        _validate_edid(custom_edid)
        edid = connector / "edid"
        edid_enabled = connector / "edid_enabled"
        if not edid.is_file() or not edid_enabled.is_file():
            raise CustomEdidUnsupported("The bootstrapped VKMS connector does not expose per-connector EDID support.")
        logs.append("Writing validated custom EDID while connector is disconnected")
        if _read(connector / "status") != CONNECTOR_STATUS_DISCONNECTED:
            raise VkmsHelperError("Refusing to replace EDID on a connected Monitorize output; remove it first.")
        _write_bytes(edid, custom_edid)
        _write(edid_enabled, "1")
        logs.append("Custom EDID written and enabled")
    else:
        if _read(connector / "status") != CONNECTOR_STATUS_DISCONNECTED:
            raise VkmsHelperError("Refusing to replace a connected Monitorize output; remove it first.")
        _write(connector / "edid_enabled", "0")
    logs.append("Configuring persistent Monitorize VKMS connector while disconnected")
    logs.append("Connecting persistent Monitorize VKMS connector")
    _write(connector / "status", CONNECTOR_STATUS_CONNECTED)
    if _read(connector / "status") != CONNECTOR_STATUS_CONNECTED:
        raise VkmsHelperError("Persistent Monitorize VKMS connector did not connect.")
    return True


def _destroy_slot(root: Path, logs: list[str], slot: str, uid: int,
                  session_id: str, env: dict[str, str], generation: str | None) -> bool:
    _instance, connector = _require_bootstrap(root, logs, slot)
    lease = _read_lease(slot)
    if lease and (lease.get("kind") == "boot" or lease.get("protected")):
        raise VkmsHelperError("Boot remote-login output is reserved; ordinary cleanup cannot remove it.")
    connected = _read(connector / "status") == CONNECTOR_STATUS_CONNECTED
    if not connected:
        if lease:
            raise VkmsHelperError(f"{slot} is disconnected but still has an owner record; refusing cleanup.")
        return False
    if not lease:
        raise VkmsHelperError(f"Connected {slot} has no trusted owner record; refusing disconnect.")
    if lease.get("uid") != uid or lease.get("session") != session_id:
        raise VkmsHelperError(f"Only the active session that created {slot} can remove it.")
    if generation and generation != lease.get("generation"):
        raise VkmsHelperError(f"The caller no longer owns {slot}'s current generation.")
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != lease.get("boot_id"):
        raise VkmsHelperError(f"{slot} ownership belongs to a previous boot.")
    from monitorize_vkms.drm import monitorize_drm_connectors
    matches = [item for item in monitorize_drm_connectors().values()
               if item.get("name") == lease.get("connector") and item.get("status") == "connected"]
    if (len(matches) != 1 or type(lease.get("connector_id")) is not int
            or matches[0].get("connector_id") != lease.get("connector_id")
            or matches[0].get("card") != lease.get("card")
            or matches[0].get("display") != slot
            or matches[0].get("edid_sha256") != lease.get("edid_sha256")):
        raise VkmsHelperError(f"{slot}'s DRM connector identity changed; refusing disconnect.")
    if lease.get("desktop") is not None or env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"):
        _run_worker(uid, env, {"action": "deactivate", "name": lease["connector"],
                               "connector_id": lease.get("connector_id"), "card": lease.get("card")})
    changed = _disconnect_connector(connector, logs)
    _remove_lease(slot)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Monitorize's fixed VKMS configfs instances.")
    parser.add_argument("operation", nargs="?", choices=("create", "create-custom", "destroy", "destroy-all", "status", "capability"))
    parser.add_argument("--slot", choices=(*INSTANCE_NAMES, "auto"), default="mon1")
    parser.add_argument("edid_base64", nargs="?")
    parser.add_argument("--mode", nargs=3, type=float, metavar=("WIDTH", "HEIGHT", "REFRESH"))
    parser.add_argument("--generation")
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        return _worker_main()
    if args.slot == "auto" and args.operation not in ("create", "create-custom"):
        parser.error("automatic slot selection is available only for create")
    _require_polkit_root()
    if args.operation is None:
        parser.error("an operation is required")

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

    if args.operation in ("create", "create-custom"):
        lock_fd = _acquire_lifecycle_lock()
        try:
            uid = int(os.environ["PKEXEC_UID"])
            if os.environ.get('MONITORIZE_VKMS_UNATTENDED') == '1':
                from monitorize_vkms.authorization import read as authorization_record
                record = authorization_record(uid)
                if not record or (args.operation in ('create', 'create-custom') and not record['enabled']):
                    raise VkmsHelperError('Unattended VKMS authorization is missing or revoked.')
                session_id, env = _active_session(uid, allow_inactive=True)
            else:
                session_id, env = _active_session(uid)
            if args.slot == "auto":
                for candidate in INSTANCE_NAMES:
                    _instance, candidate_connector = _require_bootstrap(root, logs, candidate)
                    if _read(candidate_connector / "status") == CONNECTOR_STATUS_DISCONNECTED:
                        if _read_lease(candidate):
                            raise VkmsHelperError(f"{candidate} has inconsistent ownership state; refusing create.")
                        args.slot = candidate
                        break
                else:
                    raise VkmsHelperError("Both Monitorize virtual displays are occupied.")
            instance, connector = _require_bootstrap(root, logs, args.slot)
            response["slot"] = args.slot
            if args.mode is not None:
                args.mode = _validate_requested_mode(args.mode)
            if args.mode is None:
                raise VkmsHelperError("Display creation requires a requested mode.")
            custom_edid = None
            if args.operation == "create" and args.mode is not None:
                from monitorize_vkms.edid import generate_edid
                custom_edid = generate_edid(int(args.mode[0]), int(args.mode[1]), args.mode[2], args.slot)
            if args.operation == "create-custom":
                if not args.edid_base64:
                    raise VkmsHelperError("Custom VKMS creation requires an EDID payload.")
                try:
                    custom_edid = base64.b64decode(args.edid_base64, validate=True)
                except (ValueError, TypeError) as exc:
                    raise VkmsHelperError("Custom VKMS EDID payload is not valid base64.") from exc
                _validate_edid(custom_edid)
                if args.mode is None or len(args.mode) != 3:
                    raise VkmsHelperError("Custom VKMS creation requires the requested display mode.")
                _validate_edid_mode(custom_edid, args.mode)
                from monitorize_vkms.edid import generate_edid
                if custom_edid != generate_edid(int(args.mode[0]), int(args.mode[1]), args.mode[2], args.slot):
                    raise VkmsHelperError("EDID does not match the requested Monitorize display ID and mode.")
                args.mode = [int(args.mode[0]), int(args.mode[1]), args.mode[2]]
            connected = _read(connector / "status") == CONNECTOR_STATUS_CONNECTED
            lease = _read_lease(args.slot)
            if lease and (lease.get("kind") == "boot" or lease.get("protected")):
                raise VkmsHelperError("Boot remote-login output is reserved; use the managed handoff instead.")
            digest = hashlib.sha256(custom_edid or b"").hexdigest()
            if connected:
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                if _lease_matches_request(lease, uid=uid, session=session_id, boot_id=boot_id,
                                          edid_sha256=digest, mode=args.mode):
                    from monitorize_vkms.drm import monitorize_drm_connectors
                    current = monitorize_drm_connectors().get(lease.get("connector"))
                    if (not current or current.get("status") != "connected"
                            or current.get("connector_id") != lease.get("connector_id")
                            or current.get("card") != lease.get("card")
                            or current.get("display") != args.slot
                            or current.get("edid_sha256") != digest):
                        raise VkmsHelperError("Owned display's DRM connector identity is missing or changed.")
                    response.update({"changed": False, "created": False, "generation": lease["generation"],
                                     "connector": lease.get("connector"), "card": lease.get("card"),
                                     "compositor": lease.get("desktop"), "compositor_details": {}})
                    response["logs"] = logs + ["Identical owned display already exists; leaving its generation unchanged"]
                    print(json.dumps(response))
                    return 0
                raise VkmsHelperError("A connected Monitorize output is already present but cannot be safely replaced or claimed. Remove it from its owning session first.")
            if lease and lease.get("state") not in ("active", "pending"):
                raise VkmsHelperError("Monitorize VKMS ownership state is inconsistent; refusing another create.")
            from monitorize_vkms.drm import monitorize_drm_connectors
            before = [item for item in monitorize_drm_connectors().values()
                      if item.get("display") == args.slot and item.get("status") == "connected"]
            if before:
                raise VkmsHelperError(f"{args.slot}'s DRM connector is already connected.")
            snapshot = _run_worker(uid, env, {"action": "snapshot"})
            desktop = snapshot.get("desktop")
            _validate_compositor_support(desktop, env)
            if desktop is not None and args.mode is None:
                raise VkmsHelperError("Graphical display creation requires a requested mode for verified activation.")
            generation = secrets.token_hex(16)
            lease = {
                "uid": uid, "session": session_id, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "generation": generation, "state": "pending", "edid_sha256": digest,
                "desktop": snapshot.get("desktop"), "mode": args.mode,
            }
            _write_lease(lease, args.slot)
            changed = _enable_connector(connector, logs, custom_edid)
            deadline = time.monotonic() + 8.0
            drm_connector = None
            while time.monotonic() < deadline:
                current = monitorize_drm_connectors()
                candidates = [item for item in current.values()
                              if item.get("display") == args.slot
                              and item.get("status") == "connected"
                              and item.get("edid_sha256") == digest]
                if len(candidates) == 1:
                    drm_connector = candidates[0]
                    break
                if len(candidates) > 1:
                    raise VkmsHelperError(f"Multiple DRM connectors match {args.slot}; refusing activation.")
                time.sleep(0.1)
            if not drm_connector:
                raise VkmsHelperError("Monitorize VKMS connected, but its DRM connector did not appear; it remains connected for safe recovery.")
            if type(drm_connector.get("connector_id")) is not int or drm_connector["connector_id"] <= 0:
                raise VkmsHelperError("Monitorize DRM connector has no reliable object identity; it remains connected for safe recovery.")
            lease.update({"connector": drm_connector["name"], "card": drm_connector["card"],
                          "connector_id": drm_connector.get("connector_id")})
            _write_lease(lease, args.slot)
            if args.mode:
                width, height, refresh = args.mode
                mode_deadline = time.monotonic() + 5.0
                while (f"{width}x{height}" not in drm_connector.get("modes", [])
                       and time.monotonic() < mode_deadline):
                    time.sleep(0.1)
                    refreshed = monitorize_drm_connectors().get(drm_connector["name"])
                    if refreshed and refreshed.get("connector_id") == drm_connector.get("connector_id"):
                        drm_connector = refreshed
                if f"{width}x{height}" not in drm_connector.get("modes", []):
                    raise VkmsHelperError("Requested mode was not published by the Monitorize DRM connector; it remains connected for safe recovery.")
                worker_result = _run_worker(uid, env, {
                    "action": "activate", "name": drm_connector["name"],
                    "connector_id": drm_connector.get("connector_id"), "card": drm_connector["card"], "mode": args.mode,
                    "snapshot": snapshot.get("state"),
                })
                lease["state"] = "active"
                lease["desktop"] = worker_result.get("desktop")
                _write_lease(lease, args.slot)
                response["compositor"] = worker_result.get("desktop")
                response["compositor_details"] = worker_result.get("details", {})
            else:
                lease["state"] = "active"
                _write_lease(lease, args.slot)
            response.update({"changed": changed, "created": True, "generation": generation,
                             "connector": drm_connector["name"], "card": drm_connector["card"]})
        finally:
            os.close(lock_fd)
    elif args.operation in ("destroy", "destroy-all"):
        lock_fd = _acquire_lifecycle_lock()
        try:
            uid = int(os.environ["PKEXEC_UID"])
            if os.environ.get('MONITORIZE_VKMS_UNATTENDED') == '1':
                from monitorize_vkms.authorization import read as authorization_record
                record = authorization_record(uid)
                if not record or (args.operation in ('create', 'create-custom') and not record['enabled']):
                    raise VkmsHelperError('Unattended VKMS authorization is missing or revoked.')
                session_id, env = _active_session(uid, allow_inactive=True)
            else:
                session_id, env = _active_session(uid)
            slots = INSTANCE_NAMES if args.operation == "destroy-all" else (args.slot,)
            results = {}
            for slot in slots:
                try:
                    results[slot] = {"changed": _destroy_slot(root, logs, slot, uid, session_id, env,
                                                                 args.generation if args.operation == "destroy" else None)}
                except VkmsHelperError as exc:
                    results[slot] = {"error": str(exc)}
                    if args.operation == "destroy":
                        raise
            response["results"] = results
            changed = any(item.get("changed") for item in results.values())
            response["success"] = all("error" not in item for item in results.values())
        finally:
            os.close(lock_fd)
    elif args.operation == "create-custom":
        if not args.edid_base64:
            raise VkmsHelperError("Custom VKMS creation requires an EDID payload.")
        raise VkmsHelperError("Custom create did not enter the lifecycle transaction.")
    elif args.operation == "capability":
        capability = "supported" if _probe_custom_edid_support(root, logs) else "unsupported"
        response["capability"] = capability
        response["api_version"] = 1
        response["displays"] = list(INSTANCE_NAMES)
        response["topology_ready"] = True
    elif args.operation == "status":
        displays = {}
        for slot in INSTANCE_NAMES:
            _instance, selected = _require_bootstrap(root, logs, slot)
            displays[slot] = {
                "connector_enabled": _read(selected / "enabled") == "1",
                "connector_connected": _read(selected / "status") == CONNECTOR_STATUS_CONNECTED,
                "edid_enabled": _read(selected / "edid_enabled") == "1",
                "edid_supported": (selected / "edid").is_file() and (selected / "edid_enabled").is_file(),
            }
        response["displays"] = displays
        response.update(displays["mon1"])

    response.setdefault("changed", changed)
    response["logs"] = logs

    print(json.dumps(response))
    return 0 if response["success"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CustomEdidUnsupported as exc:
        print(json.dumps({"success": False, "message": str(exc), "error_kind": "custom_edid_unsupported"}))
        sys.exit(1)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}))
        sys.exit(1)
