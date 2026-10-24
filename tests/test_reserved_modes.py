import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
from monitorize_vkms.boot_policy import validate_protected,validate_mode
from monitorize_vkms import boot_runtime as runtime
from monitorize_vkms.edid import generate_edid

class ReservedModesTests(unittest.TestCase):
    def lease(self,phase='desktop'):
        return dict(schema_version=2,protected=True,owner_kind='remote-login',uid=1000,phase=phase,session='s1',
                    boot_id='boot',generation='a'*32,mode=[1920,1080,60],state='reserved',edid_sha256='b'*64)

    def test_custom_mode_only_for_desktop_and_numeric_bounds(self):
        lease=self.lease();lease['mode']=[2340,1600,60];validate_protected(lease)
        lease['phase']='greeter'
        with self.assertRaises(ValueError):validate_protected(lease)
        for mode in ([True,1600,60],[2340,1600,float('nan')],[5000,1600,60],[2340,1600,241]):
            with self.assertRaises(ValueError):validate_mode(mode)

    def test_edid_and_lease_update_together_and_failure_rolls_back_disconnected(self):
        with TemporaryDirectory() as directory:
            connector=Path(directory);old=generate_edid(1920,1080,60,'mon1')
            (connector/'edid').write_bytes(old);(connector/'status').write_text('2')
            lease=self.lease();lease['edid_sha256']=hashlib.sha256(old).hexdigest()
            with patch.object(runtime,'connector_ready'),patch.object(runtime,'set_status') as status,patch.object(runtime,'program_edid') as program,patch.object(runtime,'write_lease') as write:
                result=runtime.change_reserved_mode(state=Path('/state'),connector=connector,lease=lease,mode=[2340,1600,60])
                self.assertEqual(result['mode'],[2340,1600,60]);self.assertEqual(lease['mode'],[1920,1080,60])
                self.assertEqual(result['edid_sha256'],hashlib.sha256(program.call_args.args[1]).hexdigest())
                status.assert_called_once_with(connector,'2');write.assert_called_once()
                program.reset_mock();write.reset_mock();program.side_effect=[RuntimeError('write failed'),None]
                with self.assertRaises(RuntimeError):runtime.change_reserved_mode(state=Path('/state'),connector=connector,lease=lease,mode=[2340,1600,60])
                self.assertEqual(program.call_args.args[1],old)
                self.assertEqual(write.call_args.args[2]['mode'],[1920,1080,60])
