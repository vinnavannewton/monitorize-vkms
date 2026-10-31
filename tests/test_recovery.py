import unittest
from unittest.mock import Mock
from monitorize_vkms.recovery import Recovery
from unittest.mock import patch


class HostRecoveryTests(unittest.TestCase):
    def backend(self,**facts):
        initial=dict(busy=False,installed_compatible=True,topology_ready=False,module_available=True,**facts)
        b=Mock();b.lock.return_value=10;b.block.return_value='token'
        b.inspect.side_effect=[initial,{**initial,'topology_ready':True}]
        return b
    def test_release_lifecycle_lock_before_bootstrap_then_verify_before_admission(self):
        b=self.backend();self.assertTrue(Recovery(b).run(1000)['success'])
        methods=[c[0] for c in b.mock_calls]
        self.assertLess(methods.index('unlock'),methods.index('bootstrap'))
        self.assertLess(max(i for i,x in enumerate(methods) if x=='inspect'),methods.index('unblock'))
    def test_foreign_or_busy_host_never_mutates(self):
        b=self.backend();b.inspect.side_effect=[dict(busy=True)]
        self.assertEqual(Recovery(b).run(1000)['code'],'host_in_use')
        b.block.assert_not_called();b.restart_service.assert_not_called()
    def test_missing_module_is_a_precise_prerequisite(self):
        b=self.backend();b.inspect.side_effect=[dict(busy=False,installed_compatible=True,topology_ready=False,module_available=False)]
        self.assertEqual(Recovery(b).run(1000)['code'],'module_missing');b.block.assert_not_called()
    def test_restart_failure_retains_retry_gate(self):
        b=self.backend();b.restart_service.side_effect=RuntimeError('service failed')
        result=Recovery(b).run(1000);self.assertTrue(result['partial']);b.unblock.assert_not_called()
    def test_service_restart_does_not_bootstrap_or_unload_driver(self):
        b=self.backend()
        result=Recovery(b).run(1000,restart_only=True)
        self.assertTrue(result['success'])
        b.bootstrap.assert_not_called();b.restart_service.assert_called_once()
    def test_service_restart_with_missing_topology_requires_reboot(self):
        b=self.backend()
        b.inspect.side_effect=[dict(busy=False,installed_compatible=True,topology_ready=False,module_available=True)]*2
        result=Recovery(b).run(1000,restart_only=True)
        self.assertTrue(result['reboot_required'])
        b.bootstrap.assert_not_called();b.unblock.assert_not_called()
    def test_failed_topology_verification_retains_gate_and_requests_reboot(self):
        b=self.backend();b.inspect.side_effect=[dict(busy=False,installed_compatible=True,topology_ready=False,module_available=True)]*2
        self.assertTrue(Recovery(b).run(1000)['reboot_required']);b.unblock.assert_not_called()


class RecoveryAdmissionTests(unittest.TestCase):
    def test_pending_recovery_blocks_other_operations(self):
        from monitorize_vkms import recovery
        with patch.object(recovery,'read_gate',return_value=dict(uid=1000,token='fixed')),patch.dict(recovery.os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'in progress'):recovery.check_admission()
            with patch.dict(recovery.os.environ,MONITORIZE_VKMS_RECOVERY_TOKEN='fixed'):recovery.check_admission()
