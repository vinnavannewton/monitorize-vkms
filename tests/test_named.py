"""Named CLI lifecycle tests. Configfs/DRM operations never touch the host."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from monitorize_vkms import named, helper, lease
from monitorize_vkms.cli import main
from monitorize_vkms.drm import monitorize_drm_connectors, find_monitorize_card
from monitorize_vkms.edid import generate_edid, display_from_edid
from monitorize_vkms.names import validate_name, edid_identity


class NamedTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.addCleanup(patch.stopall)
        patch.object(lease, 'OWNER_UID', os.getuid()).start()
        patch.object(helper, 'STATE_DIRECTORY', self.state).start()

    def test_names_and_edid_do_not_use_label_as_path(self):
        for name in ('tab1', 'tab 1', '屏幕', '../tab', '$(touch marker)'):
            self.assertEqual(validate_name(name), name)
        for name in ('', ' tab', 'tab\n1', 'x' * 129):
            with self.assertRaises(ValueError):validate_name(name)
        slot = 'named-1000-' + 'a' * 32
        data = generate_edid(1920, 1080, 60, slot, label='my tablet 1')
        self.assertEqual(len(data), 128)
        self.assertEqual(sum(data) % 256, 0)
        self.assertEqual(display_from_edid(data), edid_identity(slot))
        self.assertIn(b'my tablet 1', data)
        self.assertNotEqual(data, generate_edid(1920, 1080, 60, 'named-1000-'+'b'*32, label='my tablet 1'))

    def test_cli_accepts_quoted_and_unquoted_names(self):
        for command, name in (('create tab1 1920x1080@60 --json', 'tab1'),
                              ('create "tab 1" 1920x1080@60 --json', 'tab 1')):
            with patch('monitorize_vkms.cli.helper_response', return_value={'connector':'Virtual-9'}) as request, \
                 patch('monitorize_vkms.cli.monitorize_drm_connectors', return_value={}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(shlex.split(command)), 0)
            self.assertEqual(request.call_args.kwargs['name'], name)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['create', '1920x1080@60'])

    def test_more_than_two_independent_names_and_duplicate_rejection(self):
        patch.object(named, 'provision').start()
        patch.object(helper, '_create_request', return_value={'success':True}).start()
        slots=[]
        for index in range(5):
            named.create(SimpleNamespace(name=f'tab {index}', mode=[1920,1080,60]),
                         self.root, 1000, 's1', {}, helper, [])
            slots.append(named.records(helper,1000)[-1][0])
        self.assertEqual(len(named.records(helper,1000)), 5)
        self.assertEqual(named.records(helper,1001), [])
        self.assertIsNone(helper._read_lease('mon1'))
        with self.assertRaisesRegex(helper.VkmsHelperError,'already exists'):
            named.create(SimpleNamespace(name='tab 0', mode=[1920,1080,60]), self.root,1000,'s1',{},helper,[])

    def test_pool_reuses_only_disconnected_unleased_device_of_same_uid(self):
        slot='named-1000-'+'a'*32
        instance,connector=named.topology(self.root,slot)
        connector.mkdir(parents=True)
        (instance/'enabled').write_text('1')
        (connector/'status').write_text('2')
        self.assertEqual(named.available_slot(self.root,1000,helper),slot)
        self.assertNotEqual(named.available_slot(self.root,1001,helper),slot)
        helper._write_lease({'name':'busy'},slot)
        self.assertNotEqual(named.available_slot(self.root,1000,helper),slot)
        helper._remove_lease(slot)
        (connector/'status').write_text('1')
        self.assertNotEqual(named.available_slot(self.root,1000,helper),slot)

    def test_remove_rejects_foreign_session_generation_and_boot(self):
        slot='named-1000-'+'a'*32
        record=dict(uid=1000,session='s1',generation='g1',boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),state='pending')
        for uid,session,generation in ((1001,'s1',None),(1000,'s2',None),(1000,'s1','g2')):
            with self.assertRaises(helper.VkmsHelperError):
                named.remove_record(self.root,slot,record,uid,session,{},generation,helper,[])
        helper._write_lease(record,slot)
        self.assertTrue(named.remove_record(self.root,slot,record,1000,'s1',{},'g1',helper,[]))
        self.assertIsNone(helper._read_lease(slot))

    def test_remove_all_continues_after_failure_and_never_touches_managed(self):
        for index in range(3):
            helper._write_lease({'name':f'tab{index}'},f'named-1000-{index:032x}')
        helper._write_lease({'name':'other'},'named-1001-'+'a'*32)
        helper._write_lease({'uid':1000},'mon2')
        lock=os.open(self.root/'lock',os.O_CREAT|os.O_RDWR,0o600)
        with patch.dict(os.environ,PKEXEC_UID='1000'), \
             patch.object(helper,'_acquire_lifecycle_lock',return_value=lock), \
             patch.object(helper,'_configfs_root',return_value=self.root), \
             patch.object(helper,'_active_session',return_value=('s1',{})), \
             patch.object(named,'remove_record',side_effect=[True,helper.VkmsHelperError('busy'),True]) as remove:
            result=named.dispatch(SimpleNamespace(operation='destroy-all-named',generation=None),[])
        self.assertFalse(result['success'])
        self.assertTrue(result['changed'])
        self.assertEqual(remove.call_count,3)
        self.assertTrue(all(call.args[1].startswith('named-1000-') for call in remove.call_args_list))
        self.assertIsNotNone(helper._read_lease('mon2'))

    def test_discovery_binds_edid_to_named_device_and_preserves_app_card(self):
        drm=self.root/'drm';drm.mkdir()
        for index in range(4):
            slot=f'named-1000-{index:032x}'
            device=self.root/'faux'/('monitorize-'+slot);device.mkdir(parents=True)
            card=drm/f'card{index}';card.mkdir();(card/'device').symlink_to(device)
            connector=drm/f'card{index}-Virtual-{index+1}';connector.mkdir()
            (connector/'device').symlink_to(device)
            (connector/'status').write_text('connected')
            (connector/'edid').write_bytes(generate_edid(1920,1080,60,slot,label=f'tab {index}'))
        self.assertIsNone(find_monitorize_card(drm))
        outputs=monitorize_drm_connectors(drm)
        self.assertEqual(len(outputs),4)
        self.assertEqual(outputs['Virtual-4']['display'],'named-1000-'+f'{3:032x}')
        device=self.root/'faux/monitorize';device.mkdir()
        card=drm/'card4';card.mkdir();(card/'device').symlink_to(device)
        self.assertEqual(find_monitorize_card(drm),card)
        # A valid named EDID on a different device must never claim that label.
        (drm/'card0-Virtual-1/edid').write_bytes(generate_edid(1920,1080,60,'named-1000-'+'f'*32,label='spoof'))
        self.assertIsNone(monitorize_drm_connectors(drm)['Virtual-1']['display'])

    def test_registered_topology_is_built_before_enable_and_never_unregistered(self):
        slot='named-1000-'+'a'*32
        instance,connector=named.topology(self.root,slot)
        for relative in ('planes/plane0/possible_crtcs','crtcs/crtc0',
                         'encoders/encoder0/possible_crtcs','connectors/connector0/possible_encoders'):
            (instance/relative).mkdir(parents=True,exist_ok=True)
        (instance/'enabled').write_text('0')
        writes=[]
        def write(path,value):
            if path==instance/'enabled':
                self.assertTrue((connector/'possible_encoders/encoder0').is_symlink())
            writes.append((path,value));path.write_text(value)
        with patch.object(helper,'_write',side_effect=write):
            named.provision(self.root,slot,helper,[])
        self.assertEqual(writes[-1],(instance/'enabled','1'))
        self.assertEqual((connector/'status').read_text(),'2')
        with patch.object(helper,'_write') as write:
            named.provision(self.root,slot,helper,[])
        write.assert_not_called()

    def test_named_create_runs_shared_edid_and_activation_transaction(self):
        import hashlib
        slot='named-1000-'+'a'*32
        instance,connector=named.topology(self.root,slot)
        connector.mkdir(parents=True)
        (connector/'status').write_text('2')
        edid=generate_edid(1920,1080,60,slot,label='tab 1')
        output=dict(name='Virtual-9',card='card9',connector_id=41,status='connected',
                    display=slot,edid_sha256=hashlib.sha256(edid).hexdigest(),modes=['1920x1080'])
        with patch.object(named,'available_slot',return_value=slot), \
             patch.object(named,'provision'), \
             patch.object(helper,'_require_bootstrap',return_value=(instance,connector)), \
             patch.object(helper,'_run_worker',side_effect=[{'desktop':None},{'desktop':None}]) as worker, \
             patch.object(helper,'_enable_connector',return_value=True) as enable, \
             patch('monitorize_vkms.drm.monitorize_drm_connectors',side_effect=[{}, {'Virtual-9':output}]):
            result=named.create(SimpleNamespace(name='tab 1',mode=[1920,1080,60]),
                                self.root,1000,'s1',{},helper,[])
        self.assertEqual(result['name'],'tab 1')
        self.assertEqual(result['connector'],'Virtual-9')
        self.assertEqual(enable.call_args.args[2],edid)
        self.assertEqual(worker.call_args.args[2]['action'],'activate')
        record=helper._read_lease(slot)
        self.assertEqual((record['name'],record['state'],record['connector_id']),('tab 1','active',41))

    def test_uninstall_refuses_any_named_lease(self):
        from monitorize_vkms import package_maintenance as maintenance
        with patch('monitorize_vkms.recovery.check_admission'), \
             patch.object(maintenance,'read_enrollment',return_value=None), \
             patch.object(maintenance,'named_slots',return_value=['named-1000-'+'a'*32]), \
             patch.object(maintenance,'read_lease',side_effect=[None,{'name':'tab 1'}]):
            with self.assertRaisesRegex(RuntimeError,'Remove all named'):
                maintenance.assert_removal_safe()
