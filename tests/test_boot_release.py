"""Release regressions retained across the protected lease API migration."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from test_boot_greeter import GreeterTests
from monitorize_vkms import boot_display, boot_runtime as runtime
from monitorize_vkms.lease import write_lease

class ReleaseTests(GreeterTests):
    def release(self, *, session='s1', engines_stopped=True, uid=1000):
        value=boot_display.boot_output_status(self.state)
        helper=SimpleNamespace(STATE_DIRECTORY=self.state, _active_session=lambda uid:('s1',{'XDG_SESSION_TYPE':'wayland'}))
        @contextmanager
        def transaction(**kwargs):
            yield helper,runtime,{'uid':1000},value,self.connector
        with patch.object(boot_display,'_transaction',transaction), \
             patch.object(runtime,'quiescent',return_value=nullcontext()), \
             patch.object(runtime,'discover',return_value={'Virtual-2':self.output}):
            return boot_display.desktop_operation_installed(uid,session,'release',engines_stopped=engines_stopped)

    def owned(self, session='s1'):
        value=self.prepare()
        value.update(phase='desktop',session=session)
        write_lease(self.state,'mon1',value)

    def test_release_unplugs_and_removes_reservation(self):
        self.owned()
        self.assertEqual(self.release()['state'],'released')
        self.assertIsNone(boot_display.boot_output_status(self.state))
        self.assertEqual((self.connector/'status').read_text(),'2')

    def test_busy_engine_or_changed_session_retains_reservation(self):
        self.owned()
        for extra in ({'engines_stopped':False},{'session':'other'},{'uid':1001}):
            with self.subTest(extra=extra),self.assertRaises(PermissionError):self.release(**extra)
            self.assertIsNotNone(boot_display.boot_output_status(self.state))

    def test_changed_edid_is_not_released(self):
        self.owned()
        (self.connector/'edid').write_bytes(b'wrong')
        with self.assertRaises(PermissionError):self.release()
        self.assertIsNotNone(boot_display.boot_output_status(self.state))

    def test_same_account_previous_login_reservation_can_be_retired(self):
        self.owned('old-session')
        self.assertEqual(self.release()['state'],'released')
