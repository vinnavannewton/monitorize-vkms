import contextlib
import io
import json
import subprocess
import unittest
from unittest.mock import patch

from monitorize_vkms import cli, compositor, wlr_output
from monitorize_vkms.errors import CompositorError, HelperError


def result(enabled=True):
    return subprocess.CompletedProcess([], 0, json.dumps([
        {"name": "Virtual-1", "enabled": enabled},
        {"name": "eDP-1", "enabled": True},
    ]), "")


class OutputManagementTests(unittest.TestCase):
    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_disable_waits_for_confirmation(self, _which):
        with patch.object(wlr_output.subprocess, "run", side_effect=[
            result(), result(), result(), result(False),
        ]) as run, patch.object(wlr_output.time, "sleep"):
            self.assertTrue(wlr_output.set_enabled("Virtual-1", False))
        self.assertEqual(run.call_args_list[1].args[0],
                         ["/usr/bin/wlr-randr", "--output", "Virtual-1", "--off"])
        self.assertEqual(run.call_count, 4)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_recreation_enables_disabled_output(self, _which):
        with patch.object(wlr_output.subprocess, "run", side_effect=[
            result(False), result(), result(),
        ]) as run:
            wlr_output.set_enabled("Virtual-1", True)
        self.assertEqual(run.call_args_list[1].args[0][-1], "--on")

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_rejection_and_invalid_state_fail_closed(self, _which):
        for response in [subprocess.CompletedProcess([], 1, "", "unsupported protocol"),
                         subprocess.CompletedProcess([], 0, "not JSON", ""),
                         subprocess.CompletedProcess([], 0, "[]", "")]:
            with self.subTest(response=response), patch.object(wlr_output.subprocess, "run", return_value=response):
                with self.assertRaises(CompositorError):
                    wlr_output.set_enabled("Virtual-1", False)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_last_monitor_is_preserved(self, _which):
        response = subprocess.CompletedProcess([], 0, '[{"name":"Virtual-1","enabled":true}]', "")
        with patch.object(wlr_output.subprocess, "run", return_value=response) as run:
            with self.assertRaisesRegex(CompositorError, "last active"):
                wlr_output.set_enabled("Virtual-1", False)
            self.assertEqual(run.call_count, 1)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_timeout_fails_closed(self, _which):
        with patch.object(wlr_output.subprocess, "run", side_effect=subprocess.TimeoutExpired("wlr-randr", 5)):
            with self.assertRaises(CompositorError):
                wlr_output.set_enabled("Virtual-1", False)

    @patch.object(wlr_output.shutil, "which", return_value="/usr/bin/wlr-randr")
    def test_successful_apply_without_disabled_state_times_out(self, _which):
        with patch.object(wlr_output.subprocess, "run", return_value=result()), \
             patch.object(wlr_output.time, "monotonic", side_effect=[0, 0, 0, 0, 6]):
            with self.assertRaisesRegex(CompositorError, "Timed out confirming"):
                wlr_output.set_enabled("Virtual-1", False)

    def test_missing_client_fails_closed(self):
        with patch.object(wlr_output.shutil, "which", return_value=None):
            with self.assertRaisesRegex(CompositorError, "Install wlr-randr"):
                wlr_output.set_enabled("Virtual-1", False)

    def test_cli_delegates_lifecycle_to_privileged_helper(self):
        with patch.object(cli, "helper_response", return_value={"changed": True}) as helper, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["remove", "--json"]), 0)
        helper.assert_called_once_with("destroy-all-named")

    def test_cli_reports_helper_failure_without_claiming_removal(self):
        with patch.object(cli, "helper_response", side_effect=HelperError("unsafe")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["remove", "--json"]), 1)
        self.assertFalse(json.loads(output.getvalue())["success"])

    def test_hyprland_live_removal_disables_exact_output(self):
        with patch.object(wlr_output, "set_enabled") as change:
            self.assertTrue(compositor.deactivate_in_compositor("hyprland", None, target_name="Virtual-1"))
        change.assert_called_once_with("Virtual-1", False)

    def test_hyprland_removal_propagates_disable_failure(self):
        with patch.object(wlr_output, "set_enabled", side_effect=CompositorError("disable rejected")):
            with self.assertRaisesRegex(CompositorError, "disable rejected"):
                compositor.deactivate_in_compositor("hyprland", None, target_name="Virtual-1")

    def test_hyprland_creation_enables_the_identified_output(self):
        with patch("monitorize_vkms.drm.monitorize_drm_connectors", return_value={"Virtual-1": {}}), \
             patch.object(wlr_output, "set_enabled", return_value=True) as change:
            ok, details, _ = compositor.activate_in_compositor(
                "hyprland", None, 1920, 1080, 60, expected_output_name="Virtual-1"
            )
        self.assertTrue(ok)
        self.assertEqual(details["name"], "Virtual-1")
        change.assert_called_once_with("Virtual-1", True)

    def test_hyprland_doctor_reports_experimental_lifecycle_enabled(self):
        with patch.object(cli, "detect_compositor", return_value="hyprland"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main(["doctor", "--json"])
        checks = {item["name"]: item for item in json.loads(output.getvalue())["checks"]}
        self.assertEqual(checks["Create backend is enabled"]["status"], "PASS")
        self.assertIn("experimental", checks["Create backend is enabled"]["detail"])
        self.assertEqual(checks["Live removal backend is enabled"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()


class LegacyOutputTests(unittest.TestCase):
    def text(self,enabled=True,physical=True):
        return subprocess.CompletedProcess([],0,'Virtual-1 "Monitorize"\n  Enabled: '+('yes' if enabled else 'no')+
            '\n  Modes:\n    1920x1080 px, 60.000000 Hz (current)\n'+
            ('eDP-1 "Laptop"\n  Enabled: yes\n' if physical else ''),'')

    @patch.object(wlr_output.shutil,'which',return_value='/usr/bin/wlr-randr')
    def test_legacy_disable_queries_and_confirms_without_json_after_rejection(self,_which):
        unsupported=subprocess.CompletedProcess([],1,'',"wlr-randr: unrecognized option '--json'")
        with patch.object(wlr_output.subprocess,'run',side_effect=[unsupported,self.text(),self.text(),self.text(False)]) as run:
            self.assertTrue(wlr_output.set_enabled('Virtual-1',False))
        self.assertEqual([c.args[0][1:] for c in run.call_args_list],[['--json'],[],['--output','Virtual-1','--off'],[]])
        self.assertTrue(all(c.kwargs['env']['LC_ALL']=='C' for c in run.call_args_list))

    @patch.object(wlr_output.shutil,'which',return_value='/usr/bin/wlr-randr')
    def test_legacy_last_output_is_never_disabled(self,_which):
        unsupported=subprocess.CompletedProcess([],1,'',"unknown option '--json'")
        with patch.object(wlr_output.subprocess,'run',side_effect=[unsupported,self.text(physical=False)]) as run:
            with self.assertRaisesRegex(CompositorError,'last active'):wlr_output.set_enabled('Virtual-1',False)
        self.assertEqual(run.call_count,2)

    def test_text_missing_duplicate_or_invalid_enabled_state_refuses(self):
        for text in ('Virtual-1 "Monitorize"\n',
                     'Virtual-1 "Monitorize"\n  Enabled: maybe\n',
                     'Virtual-1 "Monitorize"\n  Enabled: yes\n  Enabled: no\n',
                     'Virtual-1 "Monitorize"\n  Enabled: yes\nVirtual-1 "Other"\n  Enabled: yes\n',
                     'garbage\n  Enabled: yes\n'):
            with self.subTest(text=text),self.assertRaises(CompositorError):wlr_output.text_heads(text)

    @patch.object(wlr_output.shutil,'which',return_value='/usr/bin/wlr-randr')
    def test_protocol_failure_never_falls_back(self,_which):
        denied=subprocess.CompletedProcess([],1,'','--json: compositor protocol denied')
        with patch.object(wlr_output.subprocess,'run',return_value=denied) as run:
            with self.assertRaises(CompositorError):wlr_output.set_enabled('Virtual-1',False)
        self.assertEqual(run.call_count,1)

    @patch.object(wlr_output.shutil,'which',return_value='/usr/bin/wlr-randr')
    def test_malformed_json_never_falls_back(self,_which):
        with patch.object(wlr_output.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'broken','')) as run:
            with self.assertRaisesRegex(CompositorError,'valid JSON'):wlr_output.set_enabled('Virtual-1',False)
        self.assertEqual(run.call_count,1)
