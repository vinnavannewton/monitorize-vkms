"""No real configfs, graphics, systemd or authorization operations."""
import tempfile
import unittest
from pathlib import Path
from monitorize_vkms.boot_display import reserve_boot_output,release_for_session,boot_output_status
from monitorize_vkms.edid import generate_edid

class ReleaseTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  base=Path(self.temp.name);self.connector=base/'connector';self.connector.mkdir();self.state=base/'state'
  for name,value in {'status':'2','enabled':'1','dynamic':'1','edid':'','edid_enabled':'0'}.items():
   (self.connector/name).write_text(value)
  lease=reserve_boot_output(enabled=True,connector=self.connector,state=self.state,boot_id='boot',sessions=[],generate_edid=generate_edid)
  self.args=dict(state=self.state,connector=self.connector,generation=lease['generation'],boot_id='boot',uid=1000,session='s1',session_resolver=lambda uid:('s1',{}),engines_stopped=True)
 def test_release_unplugs_and_removes_reservation_idempotently_via_caller(self):
  result=release_for_session(**self.args)
  self.assertTrue(result['changed']);self.assertIsNone(boot_output_status(self.state))
  self.assertEqual((self.connector/'status').read_text(),'2')
 def test_busy_engine_or_changed_session_retains_reservation(self):
  for extra in ({'engines_stopped':False},{'session':'other'},{'generation':'stale'}):
   with self.subTest(extra=extra),self.assertRaises(RuntimeError):release_for_session(**dict(self.args,**extra))
   self.assertIsNotNone(boot_output_status(self.state))
 def test_changed_edid_is_not_released(self):
  (self.connector/'edid').write_bytes(b'wrong')
  with self.assertRaises(RuntimeError):release_for_session(**self.args)
  self.assertIsNotNone(boot_output_status(self.state))

 def test_same_account_previous_login_reservation_can_be_retired(self):
  import json
  path=self.state/'primary.json';lease=json.loads(path.read_text())
  lease.update(uid=1000,session='old-session',phase='desktop_idle')
  path.write_text(json.dumps(lease))
  self.assertTrue(release_for_session(**self.args)['changed'])
  self.assertFalse(path.exists())
 def test_other_account_reservation_is_retained(self):
  import json
  path=self.state/'primary.json';lease=json.loads(path.read_text())
  lease.update(uid=1001,session='other-session',phase='desktop_idle')
  path.write_text(json.dumps(lease))
  with self.assertRaisesRegex(RuntimeError,'another account'):release_for_session(**self.args)
  self.assertTrue(path.exists())
