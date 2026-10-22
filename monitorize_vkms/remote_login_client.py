"""Authenticated user-side removal. Never called while a host lifecycle lock is held."""
import json
from .errors import HelperError


def remove_reserved() -> dict:
    try:
        import dbus
        bus = dbus.SystemBus(private=True)
        try:
            name = 'io.github.vinnavannewton.MonitorizeRemoteLogin1'
            proxy = bus.get_object(name, '/io/github/vinnavannewton/MonitorizeRemoteLogin1', introspect=False)
            raw = proxy.get_dbus_method('RemoveDisplay', name)(timeout=20)
            response = json.loads(raw)
        finally:
            bus.close()
        if not isinstance(response, dict) or response.get('success') is not True:
            raise ValueError('Managed removal did not complete.')
        return response
    except Exception as error:
        raise HelperError('Remote-login display could not be removed through its coordinator. '
                          'Use Monitorize Settings to disable or recover remote login.') from error
