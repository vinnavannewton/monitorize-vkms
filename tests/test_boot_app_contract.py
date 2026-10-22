"""Run installed facade bodies against real temporary leases and fake session/DRM boundaries."""
from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_boot_greeter import GreeterTests
from monitorize_vkms import helper, boot_display as facade, boot_runtime as runtime, lease


class HostFacadeTests(GreeterTests):
    def context(self):
        stack=ExitStack()
        stack.enter_context(patch.object(facade,'_installed_context',return_value=(helper,runtime,dict(uid=1000,enabled=True))))
        stack.enter_context(patch.object(helper,'STATE_DIRECTORY',self.state))
        stack.enter_context(patch.object(helper,'_acquire_lifecycle_lock',side_effect=lambda:os.open(self.root/'lock',os.O_CREAT|os.O_RDWR,0o600)))
        stack.enter_context(patch.object(helper,'_configfs_root',return_value=self.root))
        stack.enter_context(patch.object(helper,'_require_bootstrap',return_value=(self.root,self.connector)))
        stack.enter_context(patch.object(helper,'_active_session',return_value=('3',dict(XDG_SESSION_TYPE='wayland'))))
        stack.enter_context(patch.object(runtime,'quiescent',return_value=nullcontext()))
        stack.enter_context(patch.object(runtime,'discover',return_value={'Virtual-2':self.output}))
        # Use the actual boot ID expected by the installed facade, not a fake one.
        value=lease.read_lease(self.state,'mon1')
        value['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        lease.write_lease(self.state,'mon1',value)
        self.addCleanup(stack.close)
        return stack

    def test_desktop_adoption_before_engine_activation_idle_and_release(self):
        with self.context():
            adopted=facade.desktop_operation_installed(1000,'3','adopt')
            self.assertEqual(adopted['phase'],'desktop')
            self.assertEqual((self.connector/'status').read_text(),'2')
            with patch.object(helper,'_run_worker',return_value=dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60))):
                active=facade.desktop_operation_installed(1000,'3','prepare')
            self.assertEqual(active['state'],'active')
            idle=facade.desktop_operation_installed(1000,'3','idle',engines_stopped=True)
            self.assertEqual(idle['state'],'reserved')
            released=facade.desktop_operation_installed(1000,'3','release',engines_stopped=True)
            self.assertEqual(released['state'],'released')
            self.assertIsNone(facade.boot_output_status(self.state))

    def test_wrong_session_or_capture_ownership_refuses_idle(self):
        with self.context():
            with self.assertRaises(PermissionError):facade.desktop_operation_installed(1000,'4','adopt')
            facade.desktop_operation_installed(1000,'3','adopt')
            with self.assertRaises(PermissionError):facade.desktop_operation_installed(1000,'3','idle')
            with patch.object(runtime,'quiescent',side_effect=BlockingIOError('capture owns identity')):
                with self.assertRaises(BlockingIOError):
                    facade.desktop_operation_installed(1000,'3','idle',engines_stopped=True)
            self.assertEqual(lease.read_lease(self.state,'mon1')['state'],'reserved')

    def test_logout_returns_disconnected_reservation_and_rotates_generation(self):
        with self.context():
            adopted=facade.desktop_operation_installed(1000,'3','adopt')
            with patch.object(runtime,'local_sessions',return_value=[]):
                result=facade.return_to_greeter(state=self.state,generation=adopted['generation'],
                    boot_id=adopted['boot_id'],sessions=[],engines_stopped=True,connector=self.connector)
            self.assertEqual(result['phase'],'greeter')
            self.assertEqual(result['session'],'')
            self.assertEqual(result['state'],'reserved')
            self.assertNotEqual(result['generation'],adopted['generation'])

    def test_live_user_session_blocks_logout_handoff(self):
        with self.context():
            adopted=facade.desktop_operation_installed(1000,'3','adopt')
            with patch.object(runtime,'local_sessions',return_value=[{'seat':'seat0','class':'user'}]):
                with self.assertRaises(RuntimeError):
                    facade.return_to_greeter(state=self.state,generation=adopted['generation'],
                        boot_id=adopted['boot_id'],sessions=[],engines_stopped=True,connector=self.connector)

    def test_greeter_prepare_and_idle_use_verified_context(self):
        with self.context(), patch.object(runtime,'greeter_context',return_value=(42,'g1')):
            result=facade.greeter_operation_installed('prepare',context_check=lambda:None,
                activate=lambda *args:dict(success=True,details=dict(width=1920,height=1080,refresh_rate=60)))
            self.assertEqual(result['state'],'active')
            self.assertEqual(result['session'],'g1')
            result=facade.greeter_operation_installed('idle',context_check=lambda:None,activate=None)
            self.assertEqual(result['state'],'reserved')
