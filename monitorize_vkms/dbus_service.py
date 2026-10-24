#!/usr/bin/python3 -I
"""Polkit-gated system-bus bridge to the host VKMS lifecycle helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib" / "monitorize-vkms"))

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


BUS_NAME = "io.github.vinnavannewton.MonitorizeVkms1"
OBJECT_PATH = "/io/github/vinnavannewton/MonitorizeVkms1"
INTERFACE = BUS_NAME
HELPER = Path("/usr/libexec/monitorize-vkms/monitorize-vkms-helper")
ACTION_PREFIX = "io.github.vinnavannewton.monitorize-vkms."
POOL = ThreadPoolExecutor(max_workers=2)


def _valid_display(display: str) -> str:
    if display not in ("mon1", "mon2"):
        raise ValueError("Display must be mon1 or mon2.")
    return display


def _request(sender: str, uid: int, action: str, argv: list[str], *, unattended=False) -> str:
    if unattended:
        if action!='create' or argv[0]!='create':raise PermissionError('Unsupported unattended operation.')
        from monitorize_vkms.authorization import require_grant
        from monitorize_vkms.helper import _active_session
        require_grant(uid)
        _active_session(uid)
    else:
        authorization = subprocess.run(
            ["/usr/bin/pkcheck", "--action-id", ACTION_PREFIX + action,
             "--system-bus-name", sender, "--allow-user-interaction"],
            capture_output=True, text=True, timeout=90, check=False,
        )
        if authorization.returncode:
            raise PermissionError("Polkit denied the Monitorize VKMS request.")
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PKEXEC_UID": str(uid)}
    result = subprocess.run(
        [sys.executable, "-I", str(HELPER), *argv],
        capture_output=True, text=True, timeout=120, check=False, env=environment,
    )
    response = None
    for line in reversed(result.stdout.splitlines()):
        try:
            response = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(response, dict):
        raise RuntimeError(result.stderr.strip() or "VKMS helper returned no response.")
    message = str(response.get("message") or "VKMS helper failed.")
    if (result.returncode and action == "capability"
            and message in (
                "Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms.",
                "Two-display Monitorize VKMS bootstrap is not initialized. Reboot after installing monitorize-vkms.",
                "Monitorize VKMS bootstrap is incomplete. Reboot after installing monitorize-vkms.",
            )):
        return json.dumps({"success": True, "api_version": 1, "displays": ["mon1", "mon2"],
                           "topology_ready": False, "message": message})
    if result.returncode and argv[0] != "destroy-all":
        raise RuntimeError(message)
    return json.dumps(response)


class MonitorizeVkmsService(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus) -> None:
        self.bus = bus
        self.name = dbus.service.BusName(BUS_NAME, bus=bus, do_not_queue=True)
        super().__init__(bus, OBJECT_PATH)

    def _dispatch(self, sender: str, action: str, argv: list[str], reply, error, *, unattended=False) -> None:
        # The unique bus sender, rather than any caller-supplied UID, is the
        # subject of both the Polkit check and the lifecycle ownership record.
        try:
            uid = int(self.bus.get_unix_user(sender))
        except (dbus.DBusException, ValueError) as exc:
            error(dbus.DBusException(f"Could not identify D-Bus caller: {exc}"))
            return

        def finished(future):
            try:
                result = future.result()
            except Exception as exc:
                GLib.idle_add(error, dbus.DBusException(str(exc)))
            else:
                GLib.idle_add(reply, result)

        future = POOL.submit(_request, sender, uid, action, argv, unattended=unattended)
        future.add_done_callback(finished)

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s", sender_keyword="sender")
    def GetAuthorizationState(self, sender=None):
        from monitorize_vkms.authorization import get_state
        return json.dumps(get_state(int(self.bus.get_unix_user(sender))))

    @dbus.service.method(INTERFACE, in_signature="siid", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def CreateDisplayUnattended(self, display, width, height, refresh, reply, error, sender=None):
        try:
            display=_valid_display(str(display))
            import math
            if not (1<=width<=4095 and 1<=height<=4095 and math.isfinite(refresh) and 24<=refresh<=240):
                raise ValueError('Requested display mode is out of range.')
        except (ValueError,TypeError) as exc:
            error(dbus.DBusException(str(exc)));return
        self._dispatch(sender,'create',['create','--slot',display,'--mode',str(width),str(height),str(refresh)],
                       reply,error,unattended=True)

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def GetCapabilities(self, reply, error, sender=None):
        self._dispatch(sender, "capability", ["capability"], reply, error)

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def GetStatus(self, reply, error, sender=None):
        self._dispatch(sender, "status", ["status"], reply, error)

    @dbus.service.method(INTERFACE, in_signature="siid", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def CreateDisplay(self, display, width, height, refresh, reply, error, sender=None):
        try:
            display = _valid_display(str(display))
            if not (1 <= width <= 4095 and 1 <= height <= 4095 and 24 <= refresh <= 240):
                raise ValueError("Requested display mode is out of range.")
        except (ValueError, TypeError) as exc:
            error(dbus.DBusException(str(exc)))
            return
        self._dispatch(sender, "create", ["create", "--slot", display, "--mode",
                                           str(width), str(height), str(refresh)], reply, error)

    @dbus.service.method(INTERFACE, in_signature="ss", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def DestroyDisplay(self, display, generation, reply, error, sender=None):
        try:
            display = _valid_display(str(display))
        except ValueError as exc:
            error(dbus.DBusException(str(exc)))
            return
        argv = ["destroy", "--slot", display]
        if generation:
            argv.extend(("--generation", str(generation)))
        self._dispatch(sender, "destroy", argv, reply, error)

    @dbus.service.method(INTERFACE, in_signature="", out_signature="s", sender_keyword="sender",
                         async_callbacks=("reply", "error"))
    def DestroyAll(self, reply, error, sender=None):
        self._dispatch(sender, "destroy-all", ["destroy-all"], reply, error)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Monitorize VKMS D-Bus service must run as root.")
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    MonitorizeVkmsService(bus)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
