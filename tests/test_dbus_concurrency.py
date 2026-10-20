"""Real isolated bus traffic; no root service, authorization or display changes."""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


PROGRAM = r'''
import concurrent.futures
import json
import multiprocessing
import dbus
from dbus.mainloop import NULL_MAIN_LOOP
from monitorize_vkms import dbus_service as service

def serve(ready):
    try:
        service.DBusGMainLoop(set_as_default=True)
        # All traffic, including worker identity checks, stays on the test bus.
        service.dbus.SystemBus = dbus.SessionBus
        def request(sender, uid, action, argv, interactive=True):
            if action == 'create':
                return json.dumps({'success': True, 'created': True, 'generation': 'test-generation'})
            return json.dumps({'success': True})
        service._request = request
        service._execute_helper = lambda *args, **kwargs: json.dumps({'success': True})
        service.authorization.read = lambda uid: {'enabled': True}
        bus = dbus.SessionBus(private=True)
        obj = service.MonitorizeVkmsService(bus)
        ready.send(True)
        service.GLib.MainLoop().run()
    except BaseException as exc:
        ready.send(str(exc))
        raise

# No connections or worker threads exist in the parent yet. Explicit fork also
# keeps this inline test compatible with Python 3.14's forkserver default.
context = multiprocessing.get_context('fork')
parent, child_pipe = context.Pipe()
child = context.Process(target=serve, args=(child_pipe,), daemon=True)
child.start()
try:
    assert parent.poll(5), 'test service did not start'
    assert parent.recv() is True, 'test service startup failed'
    def client(index):
        bus = dbus.SessionBus(private=True, mainloop=NULL_MAIN_LOOP)
        try:
            obj = dbus.Interface(bus.get_object(service.BUS_NAME, service.OBJECT_PATH, introspect=False), service.INTERFACE)
            for i in range(30):
                assert json.loads(str(obj.GetCapabilities(timeout=5)))['success']
                assert json.loads(str(obj.CreateDisplayUnattended('mon1', 1920, 1080, 60.0, timeout=5)))['created']
                assert json.loads(str(obj.DestroyDisplay('mon1', 'test-generation', timeout=5)))['success']
        finally:
            bus.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(client, range(4)))
    assert child.is_alive(), 'service crashed during concurrent requests'
    print('360 concurrent discovery/create/remove requests passed')
finally:
    child.terminate()
    child.join(5)
'''


class DbusConcurrencyTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('dbus-run-session'), 'dbus-run-session unavailable')
    def test_worker_liveness_checks_while_main_loop_dispatches(self):
        result = subprocess.run(
            ['dbus-run-session', '--', sys.executable, '-c', PROGRAM],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, timeout=35, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('360 concurrent', result.stdout)
