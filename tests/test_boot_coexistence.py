from pathlib import Path
from unittest.mock import patch
import unittest
from monitorize_vkms import helper


class CoexistenceTests(unittest.TestCase):
    def test_protected_destroy_refuses_even_disconnected_and_before_worker(self):
        for status in ('1','2'):
            with self.subTest(status=status),patch.object(helper,'_require_bootstrap',return_value=(Path('/fake'),Path('/fake/connector0'))), \
                 patch.object(helper,'_read_lease',return_value={'protected':True}), \
                 patch.object(helper,'_read',return_value=status), \
                 patch.object(helper,'_disconnect_connector',side_effect=AssertionError('must not disconnect')):
                with self.assertRaisesRegex(helper.VkmsHelperError,'managed'):
                    helper._destroy_slot(Path('/fake'),[],'mon1',1000,'3',{},None)

    def test_automatic_slot_skips_disconnected_reservation(self):
        # Selection is shared by ordinary and custom create, before any EDID write.
        with patch.object(helper,'_read_lease',side_effect=lambda slot:{'protected':True} if slot=='mon1' else None), \
             patch.object(helper,'_require_bootstrap',side_effect=lambda root,logs,slot:(root,Path('/fake')/slot)), \
             patch.object(helper,'_read',return_value='2'):
            self.assertEqual(helper._select_available_slot(Path('/fake'),[]),'mon2')

    def test_inconsistent_ordinary_lease_does_not_get_overwritten(self):
        with patch.object(helper,'_read_lease',return_value={'uid':1000}), \
             patch.object(helper,'_require_bootstrap',return_value=(Path('/fake'),Path('/fake/mon1'))), \
             patch.object(helper,'_read',return_value='2'):
            with self.assertRaises(helper.VkmsHelperError):helper._select_available_slot(Path('/fake'),[])
