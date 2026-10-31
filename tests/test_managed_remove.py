import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from monitorize_vkms import cli
from monitorize_vkms.errors import ManagedDisplayError


class ManagedRemoveTests(unittest.TestCase):
    def test_explicit_remove_routes_protected_display_through_coordinator(self):
        output=io.StringIO()
        with patch.object(cli,'helper_response',side_effect=ManagedDisplayError('managed')), \
             patch('monitorize_vkms.remote_login_client.remove_reserved',return_value=dict(success=True,changed=True)), \
             redirect_stdout(output):
            self.assertEqual(cli.cmd_remove(SimpleNamespace(display='mon1',json=True,managed=True)),0)
        self.assertTrue(json.loads(output.getvalue())['removed'])

    def test_destroy_all_keeps_partial_failure_when_coordinator_unavailable(self):
        from monitorize_vkms.errors import HelperError
        response=dict(success=False,changed=True,results={'mon1':{'error_kind':'protected_display','error':'managed'},'mon2':{'changed':True}})
        output=io.StringIO()
        with patch.object(cli,'helper_response',return_value=response), \
             patch('monitorize_vkms.remote_login_client.remove_reserved',side_effect=HelperError('Use Settings recovery')), \
             redirect_stdout(output):
            self.assertEqual(cli.cmd_remove(SimpleNamespace(display=None,json=True,managed=True)),1)
        result=json.loads(output.getvalue())
        self.assertFalse(result['success'])
        self.assertTrue(result['removed'])
        self.assertIn('Settings',result['results']['mon1']['error'])
