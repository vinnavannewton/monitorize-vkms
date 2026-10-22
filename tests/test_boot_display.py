import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitorize_vkms.boot_display import ensure_boot_output, transfer_to_desktop, boot_output_status
from monitorize_vkms.edid import generate_edid


class BootDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.connector=root/'connector';self.connector.mkdir()
        self.state=root/'state';self.state.mkdir()
        for name,value in {'status':'2','enabled':'1','dynamic':'1','edid':'','edid_enabled':'0'}.items():
            (self.connector/name).write_text(value)
        self.context={'connector':self.connector,'state':self.state,'boot_id':'boot-one','sessions':[],'generate_edid':generate_edid}

    def ensure(self,**extra):return ensure_boot_output(**dict(self.context,**extra))

    def test_disabled_is_noop(self):
        self.assertFalse(self.ensure(enabled=False)['enabled'])
        self.assertEqual((self.connector/'status').read_text(),'2')
        self.assertFalse((self.state/'primary.json').exists())

    def test_reservation_is_idempotent_and_protected(self):
        first=self.ensure(enabled=True);second=self.ensure(enabled=True)
        self.assertEqual(first['generation'],second['generation'])
        self.assertEqual(first['kind'],'boot');self.assertTrue(first['protected'])
        self.assertEqual((self.connector/'status').read_text(),'1')
        self.assertEqual(first['boot_id'],'boot-one')

    def test_ordinary_owner_cannot_be_replaced(self):
        (self.state/'primary.json').write_text(json.dumps({'kind':'session'}))
        with self.assertRaises(RuntimeError):self.ensure(enabled=True)
        self.assertEqual((self.connector/'status').read_text(),'2')

    def test_never_adopt_unowned_connected_output(self):
        (self.connector/'status').write_text('1')
        with self.assertRaises(RuntimeError):self.ensure(enabled=True)

    def test_stale_boot_and_modified_edid_refused(self):
        self.ensure(enabled=True)
        with self.assertRaises(RuntimeError):self.ensure(enabled=True,boot_id='boot-two')
        (self.connector/'edid').write_bytes(b'invalid')
        with self.assertRaises(RuntimeError):self.ensure(enabled=True)

    def test_user_session_prevents_boot_connect(self):
        with self.assertRaises(RuntimeError):self.ensure(enabled=True,sessions=[{'class':'user'}])

    def test_explicit_transfer_keeps_output_connected(self):
        lease=self.ensure(enabled=True)
        with self.assertRaises(RuntimeError):transfer_to_desktop(self.state,'wrong','boot-one',1000,'s1')
        lease=transfer_to_desktop(self.state,lease['generation'],'boot-one',1000,'s1')
        self.assertEqual(lease['session'],'s1');self.assertEqual(lease['uid'],1000)
        self.assertTrue(lease['protected']);self.assertEqual(lease['phase'],'desktop')
        self.assertEqual((self.connector/'status').read_text(),'1')

    def test_pending_failure_is_reserved_not_claimable(self):
        def fail(*args):raise OSError('fixture')
        with patch('monitorize_vkms.boot_display._write_edid',side_effect=fail):
            with self.assertRaises(OSError):self.ensure(enabled=True)
        self.assertEqual(boot_output_status(self.state)['state'],'pending')
        with self.assertRaises(RuntimeError):self.ensure(enabled=True)

class HelperReservationTests(unittest.TestCase):
    def test_normal_cleanup_refuses_boot_reservation_even_for_desktop_owner(self):
        from monitorize_vkms import helper
        for lease in ({'kind':'boot','uid':1000,'session':'s1'}, {'protected':True,'uid':1000,'session':'s1'}):
            with patch.object(helper,'_require_bootstrap',return_value=(Path('/unused'),Path('/unused'))), \
                 patch.object(helper,'_read_lease',return_value=lease), \
                 patch.object(helper,'_disconnect_connector') as disconnect:
                with self.assertRaisesRegex(helper.VkmsHelperError,'reserved'):
                    helper._destroy_slot(Path('/unused'),[],'mon1',1000,'s1',{},None)
                disconnect.assert_not_called()

class AdoptionTests(unittest.TestCase):
    setUp=BootDisplayTests.setUp
    ensure=BootDisplayTests.ensure
    def adoption_context(self):
        from monitorize_vkms.boot_display import adopt_for_session
        lease=self.ensure(enabled=True)
        drm={'name':'Virtual-4','card':'card7','connector_id':45,'display':'mon1',
             'status':'connected','edid_sha256':lease['edid_sha256']}
        calls=[]
        args=dict(state=self.state,generation=lease['generation'],boot_id='boot-one',uid=1000,session='s1',
                  session_resolver=lambda uid:('s1',{'XDG_SESSION_TYPE':'wayland'}),
                  connectors=lambda:{'Virtual-4':drm},activate=lambda *args:calls.append(args) or {'desktop':'kde'})
        return adopt_for_session,args,calls,drm

    def test_adopt_waits_for_hotplug_status_and_edid(self):
        adopt,args,calls,drm=self.adoption_context()
        pending=dict(drm,status='disconnected',display=None,edid_sha256=None)
        samples=iter([{'Virtual-4':pending},{'Virtual-4':dict(drm,edid_sha256=None)}, {'Virtual-4':drm}])
        args['connectors']=lambda:next(samples)
        with patch('monitorize_vkms.boot_display.time.sleep'):
            self.assertEqual(adopt(**args)['phase'],'desktop')
        self.assertEqual(len(calls),1)

    def test_adopt_readiness_timeout_does_not_activate(self):
        adopt,args,calls,drm=self.adoption_context()
        drm.update(status='disconnected',display=None,edid_sha256=None)
        with self.assertRaisesRegex(RuntimeError,'readiness timed out'):
            adopt(**args,timeout=0)
        self.assertFalse(calls)

    def test_adopt_rechecks_session_while_waiting(self):
        adopt,args,calls,drm=self.adoption_context()
        drm.update(status='disconnected',display=None,edid_sha256=None)
        sessions=iter([('s1',{}),('changed',{})])
        args['session_resolver']=lambda uid:next(sessions)
        with patch('monitorize_vkms.boot_display.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'session changed'):
                adopt(**args)
        self.assertFalse(calls)

    def test_adopt_changed_edid_is_not_retried(self):
        adopt,args,calls,drm=self.adoption_context();drm['edid_sha256']='changed'
        with patch('monitorize_vkms.boot_display.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'identity changed'):
                adopt(**args)
            sleep.assert_not_called()
        self.assertFalse(calls)

    def test_adopt_binds_complete_drm_identity_without_disconnect(self):
        adopt,args,calls,drm=self.adoption_context();lease=adopt(**args)
        self.assertEqual(lease['connector'],'Virtual-4');self.assertEqual(lease['connector_id'],45)
        self.assertEqual(lease['card'],'card7');self.assertTrue(lease['protected'])
        self.assertEqual(lease['phase'],'desktop');self.assertEqual(lease['desktop'],'kde')
        self.assertEqual((self.connector/'status').read_text(),'1')
        adopt(**args);self.assertEqual(len(calls),1)

    def test_adopt_refuses_other_session_and_changed_edid(self):
        adopt,args,calls,drm=self.adoption_context()
        args['session']='other'
        with self.assertRaises(RuntimeError):adopt(**args)
        args['session']='s1';drm['edid_sha256']='changed'
        with self.assertRaises(RuntimeError):adopt(**args)
        self.assertEqual(calls,[])

    def test_reactivation_refuses_changed_drm_identity(self):
        adopt,args,calls,drm=self.adoption_context();adopt(**args)
        adopt(**args,reactivate=True);self.assertEqual(len(calls),2)
        drm['connector_id']+=1
        with self.assertRaises(RuntimeError):adopt(**args,reactivate=True)
        self.assertEqual(len(calls),2)

    def test_failed_activation_retains_protected_boot_output(self):
        adopt,args,calls,drm=self.adoption_context()
        def fail(*args):raise RuntimeError('activation failed')
        args['activate']=fail
        with self.assertRaises(RuntimeError):adopt(**args)
        lease=boot_output_status(self.state)
        self.assertEqual(lease['phase'],'greeter');self.assertTrue(lease['protected'])
        self.assertEqual((self.connector/'status').read_text(),'1')

class ReturnTests(unittest.TestCase):
    setUp=BootDisplayTests.setUp
    ensure=BootDisplayTests.ensure
    def test_return_requires_logout_and_actual_engine_exit(self):
        from monitorize_vkms.boot_display import return_to_greeter
        lease=self.ensure(enabled=True)
        transfer_to_desktop(self.state,lease['generation'],'boot-one',1000,'s1')
        values=dict(state=self.state,generation=lease['generation'],boot_id='boot-one',sessions=[],engines_stopped=True)
        for change in ({'engines_stopped':False},{'sessions':[{'class':'user','seat':'seat0'}]},{'generation':'stale'}):
            with self.assertRaises(RuntimeError):return_to_greeter(**dict(values,**change))
        result=return_to_greeter(**values)
        self.assertEqual(result['phase'],'greeter');self.assertNotIn('uid',result)
        self.assertEqual((self.connector/'status').read_text(),'1')

class IdleBootTests(unittest.TestCase):
    setUp=BootDisplayTests.setUp
    ensure=BootDisplayTests.ensure
    def test_idle_disconnect_preserves_generation_and_reconnects(self):
        from monitorize_vkms.boot_display import idle_for_session,reconnect_reserved
        lease=self.ensure(enabled=True)
        args=dict(state=self.state,connector=self.connector,generation=lease['generation'],boot_id='boot-one')
        idle_for_session(**args,uid=1000,session='s1',session_resolver=lambda uid:('s1',{}),engines_stopped=True)
        self.assertEqual((self.connector/'status').read_text(),'2')
        self.assertTrue(boot_output_status(self.state)['protected'])
        reconnect_reserved(**args)
        self.assertEqual((self.connector/'status').read_text(),'1')
        self.assertEqual(boot_output_status(self.state)['generation'],lease['generation'])
    def test_busy_engine_or_wrong_session_never_disconnects(self):
        from monitorize_vkms.boot_display import idle_for_session
        lease=self.ensure(enabled=True)
        args=dict(state=self.state,connector=self.connector,generation=lease['generation'],boot_id='boot-one',uid=1000,session='s1',session_resolver=lambda uid:('s1',{}),engines_stopped=False)
        with self.assertRaises(RuntimeError):idle_for_session(**args)
        args.update(engines_stopped=True,session='other')
        with self.assertRaises(RuntimeError):idle_for_session(**args)
        self.assertEqual((self.connector/'status').read_text(),'1')


class DisconnectedReservationTests(unittest.TestCase):
    setUp=BootDisplayTests.setUp
    def test_reserve_only_leaves_connector_disconnected_and_preserves_generation(self):
        from monitorize_vkms.boot_display import reserve_boot_output
        first=reserve_boot_output(**dict(self.context,enabled=True))
        second=reserve_boot_output(**dict(self.context,enabled=True))
        self.assertEqual(first['generation'],second['generation'])
        self.assertTrue(first['protected']);self.assertEqual(first['state'],'idle')
        self.assertEqual((self.connector/'status').read_text(),'2')
    def test_logout_returns_idle_reservation_without_connecting(self):
        from monitorize_vkms.boot_display import reserve_boot_output,return_to_greeter
        lease=reserve_boot_output(**dict(self.context,enabled=True))
        result=return_to_greeter(state=self.state,generation=lease['generation'],boot_id='boot-one',sessions=[],engines_stopped=True)
        self.assertEqual(result['phase'],'greeter');self.assertEqual((self.connector/'status').read_text(),'2')


class GreeterOperationTests(unittest.TestCase):
    setUp=BootDisplayTests.setUp
    def run_operation(self,operation,*,enabled=True,check=lambda:None,timeout=.1):
        from monitorize_vkms.boot_display import reserve_boot_output,greeter_operation_installed
        from contextlib import ExitStack
        from unittest.mock import Mock
        if not (self.state/'primary.json').exists():reserve_boot_output(**dict(self.context,enabled=True))
        drm=self.connector.parent/'drm';drm.mkdir(exist_ok=True);(drm/'enabled').write_text('enabled' if enabled else 'disabled')
        lease=boot_output_status(self.state)
        output=dict(name='Virtual-4',path=str(drm),card='card7',connector_id=45,display='mon1',status='connected',edid_sha256=lease['edid_sha256'])
        fd=os.open(self.state,os.O_RDONLY)
        with ExitStack() as stack:
            for name,value in (('monitorize_vkms.boot_display._require_root',lambda:None),('monitorize_vkms.helper.STATE_DIRECTORY',self.state),
                ('monitorize_vkms.helper._acquire_lifecycle_lock',lambda:fd),('monitorize_vkms.helper._configfs_root',lambda args:self.state),
                ('monitorize_vkms.helper._require_bootstrap',lambda *args:(None,self.connector)),
                ('monitorize_vkms.drm.monitorize_drm_connectors',lambda:{'Virtual-4':output})):
                stack.enter_context(patch(name,value))
            # Fixture boot ID must match the current-boot validation.
            lease['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            (self.state/'primary.json').write_text(json.dumps(lease))
            return greeter_operation_installed(operation,context_check=check,timeout=timeout)
    def test_hotplug_prepare_then_idle_preserves_reservation(self):
        result=self.run_operation('prepare');self.assertEqual(result['connector'],'Virtual-4')
        self.assertEqual((self.connector/'status').read_text(),'1')
        self.run_operation('idle');self.assertEqual((self.connector/'status').read_text(),'2')
        self.assertTrue(boot_output_status(self.state)['protected'])
    def test_context_refusal_never_connects(self):
        def deny():raise PermissionError('seat changed')
        with self.assertRaises(PermissionError):self.run_operation('prepare',check=deny)
        self.assertEqual((self.connector/'status').read_text(),'2')
    def test_connected_but_disabled_output_is_not_capture_ready(self):
        with self.assertRaisesRegex(RuntimeError,'did not activate'):self.run_operation('prepare',enabled=False,timeout=0)
        self.assertEqual((self.connector/'status').read_text(),'1')
        self.run_operation('idle');self.assertEqual((self.connector/'status').read_text(),'2')

    def test_login_during_hotplug_refuses_target_and_allows_later_cleanup(self):
        count=0
        def check():
            nonlocal count
            count+=1
            if count>=2:raise PermissionError('greeter ended during preparation')
        with self.assertRaises(PermissionError):self.run_operation('prepare',check=check)
        self.assertEqual((self.connector/'status').read_text(),'1')
        self.run_operation('idle');self.assertEqual((self.connector/'status').read_text(),'2')
