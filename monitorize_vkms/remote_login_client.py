"""Explicit CLI-only removal of the coordinator's protected remote-login slot."""
import json
from pathlib import Path
import shutil
import subprocess
from .errors import HelperError

NAME='io.github.vinnavannewton.MonitorizeRemoteLogin1'
PATH='/io/github/vinnavannewton/MonitorizeRemoteLogin1'


def remove_reserved():
    try:
        import dbus
        from dbus.mainloop import NULL_MAIN_LOOP
        bus=dbus.SystemBus(private=True,mainloop=NULL_MAIN_LOOP)
        try:
            interface=dbus.Interface(bus.get_object(NAME,PATH,introspect=False),NAME)
            return json.loads(interface.RemoveDisplay(timeout=22))
        finally:bus.close()
    except ImportError:
        return _recovery()
    except Exception as error:
        name=error.get_dbus_name() if hasattr(error,'get_dbus_name') else ''
        if name in ('org.freedesktop.DBus.Error.ServiceUnknown','org.freedesktop.DBus.Error.NameHasNoOwner'):
            return _recovery()
        raise HelperError('Managed remote-login removal failed. Stop streaming and retry; inspect the remote-login journal.') from None


def _recovery():
    helper=Path('/usr/libexec/monitorize/monitorize-system-setup');pkexec=shutil.which('pkexec')
    if not helper.is_file() or not pkexec:
        raise HelperError('Install Monitorize remote-login recovery support or start monitorize-remote-login.service before removing its reserved display.')
    result=subprocess.run([pkexec,str(helper),'--remote-login','remove'],capture_output=True,text=True,timeout=30)
    try:
        values=json.loads(result.stdout.strip())
        if not isinstance(values,dict):raise ValueError()
    except (ValueError,TypeError):
        raise HelperError('Administrator recovery did not return a valid result; check authorization and the installed helper.') from None
    if result.returncode or not values.get('success'):
        raise HelperError(str(values.get('message') or 'Administrator recovery failed; check authorization and retry.'))
    return values
