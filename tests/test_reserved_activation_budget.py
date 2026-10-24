import unittest
from contextlib import contextmanager
from unittest.mock import Mock,patch
from monitorize_vkms import boot_display as boot


class ReservedActivationBudgetTests(unittest.TestCase):
    def fixture(self):
        helper=Mock();runtime=Mock();value=dict(uid=1000,phase='desktop',session='s1',mode=[1920,1080,60])
        helper._active_session.return_value=('s1',{'XDG_SESSION_TYPE':'wayland'})
        @contextmanager
        def transaction(**kwargs):yield helper,runtime,{'uid':1000},value,object()
        return helper,runtime,transaction

    def test_snapshot_precedes_hotplug_and_activation_preserves_snapshot_with_bounded_deadlines(self):
        helper,runtime,transaction=self.fixture()
        snapshot={'Virtual-9':['Virtual-9','RHT','QEMU','existing']}
        helper._run_worker.side_effect=[dict(success=True,state=snapshot),dict(success=True)]
        def prepare(**kwargs):
            self.assertEqual(helper._run_worker.call_count,1)
            return kwargs['activate'](dict(name='Virtual-2',card='card2',connector_id=14),12)
        runtime.prepare_reserved.side_effect=prepare
        with patch.object(boot,'_transaction',side_effect=transaction):boot.desktop_operation_installed(1000,'s1','prepare')
        calls=helper._run_worker.call_args_list
        self.assertEqual(calls[0].args[2],dict(action='snapshot'));self.assertEqual(calls[0].kwargs['timeout'],3)
        self.assertEqual(calls[1].args[2]['name'],'Virtual-2')
        self.assertEqual(calls[1].args[2]['snapshot'],snapshot)
        self.assertEqual(calls[1].kwargs['timeout'],10)

    def test_snapshot_failure_never_connects_a_display(self):
        helper,runtime,transaction=self.fixture();helper._run_worker.side_effect=RuntimeError('snapshot timed out')
        with patch.object(boot,'_transaction',side_effect=transaction),self.assertRaises(RuntimeError):boot.desktop_operation_installed(1000,'s1','prepare')
        runtime.prepare_reserved.assert_not_called()
