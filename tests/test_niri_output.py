"""Niri must verify state, rather than treating an IPC acknowledgement as success."""

import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from monitorize_vkms import niri_output as niri
from monitorize_vkms.compositor import detect_compositor, activate_in_compositor, deactivate_in_compositor
from monitorize_vkms.errors import CompositorError


def output(name="Virtual-1", enabled=True, rate=75000):
    return {"name": name, "modes": [{"width": 2340, "height": 1080, "refresh_rate": rate,
                                     "is_preferred": True}],
            "current_mode": 0 if enabled else None, "logical": {} if enabled else None}


class NiriOutputTests(unittest.TestCase):
    def test_detection_and_dispatch(self):
        with patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "niri"}, clear=True):
            self.assertEqual(detect_compositor(), "niri")
        with patch.dict(os.environ, {"NIRI_SOCKET": "/run/user/1000/niri.sock"}, clear=True):
            self.assertEqual(detect_compositor(), "niri")
        with patch.object(niri, "activate", return_value=(True, {}, "ok")) as activate:
            activate_in_compositor("niri", [], 2340, 1080, 75, "Virtual-1", 9)
            activate.assert_called_once_with("Virtual-1", 2340, 1080, 75)
        with patch.object(niri, "deactivate", return_value=True) as deactivate:
            self.assertTrue(deactivate_in_compositor("niri", [], target_name="Virtual-1"))
            deactivate.assert_called_once_with("Virtual-1")

    def test_mode_selection_and_verification(self):
        target = output(rate=74998)
        with patch.object(niri, "outputs", side_effect=[{}, {"Virtual-1": target}, {"Virtual-1": target}]), \
             patch.object(niri, "_request") as request, patch.object(niri.time, "sleep"):
            ok, details, _message = niri.activate("Virtual-1", 2340, 1080, 75)
        self.assertTrue(ok)
        self.assertEqual(details["refresh_rate"], 74.998)
        self.assertEqual(request.call_args_list[0].args, ("output", "Virtual-1", "mode", "2340x1080@74.998"))
        self.assertEqual(request.call_args_list[1].args, ("output", "Virtual-1", "on"))

    def test_acknowledgement_without_activation_fails(self):
        with patch.object(niri, "outputs", return_value={"Virtual-1": output(enabled=False)}), \
             patch.object(niri, "_request"), patch.object(niri, "POLL_ATTEMPTS", 2), \
             patch.object(niri.time, "sleep"):
            with self.assertRaisesRegex(CompositorError, "did not confirm"):
                niri.activate("Virtual-1", 2340, 1080, 75)

    def test_missing_connector_and_wrong_mode_do_not_send_configuration(self):
        for state in ({}, {"Virtual-1": output(rate=60000)}):
            with self.subTest(state=state), patch.object(niri, "outputs", return_value=state), \
                 patch.object(niri, "_request") as request, patch.object(niri, "POLL_ATTEMPTS", 1), \
                 patch.object(niri.time, "sleep"):
                with self.assertRaises(CompositorError):
                    niri.activate("Virtual-1", 2340, 1080, 75)
                request.assert_not_called()

    def test_deactivation_requires_disabled_state_and_remaining_output(self):
        physical = output("eDP-1")
        initial = {"Virtual-1": output(), "eDP-1": physical}
        disabled = {"Virtual-1": output(enabled=False), "eDP-1": physical}
        with patch.object(niri, "outputs", side_effect=[initial, initial, disabled]), \
             patch.object(niri, "_request") as request, patch.object(niri.time, "sleep"):
            self.assertTrue(niri.deactivate("Virtual-1"))
        request.assert_called_once_with("output", "Virtual-1", "off")
        for state in (initial, {"Virtual-1": output(enabled=False), "eDP-1": output("eDP-1", False)},
                      {"eDP-1": physical}):
            with self.subTest(state=state), patch.object(niri, "outputs", side_effect=[initial, state]), \
                 patch.object(niri, "_request"), patch.object(niri, "POLL_ATTEMPTS", 1), \
                 patch.object(niri.time, "sleep"):
                with self.assertRaisesRegex(CompositorError, "did not confirm"):
                    niri.deactivate("Virtual-1")

    def test_last_active_output_and_physical_target_are_rejected(self):
        with patch.object(niri, "outputs", return_value={"Virtual-1": output()}), \
             patch.object(niri, "_request") as request:
            with self.assertRaisesRegex(CompositorError, "last active"):
                niri.deactivate("Virtual-1")
            with self.assertRaisesRegex(CompositorError, "virtual connector"):
                niri.deactivate("eDP-1")
            request.assert_not_called()

    def test_invalid_ipc_state_and_errors(self):
        invalid = [[], {"Virtual-1": {"name": "eDP-1"}}]
        missing_mode = output()
        del missing_mode["current_mode"]
        invalid.append({"Virtual-1": missing_mode})
        for changes in ({"current_mode": True}, {"current_mode": 8}, {"logical": "yes"},
                        {"modes": [{"width": 2340, "height": 1080, "refresh_rate": -1}]}):
            item = copy.deepcopy(output())
            item.update(changes)
            invalid.append({"Virtual-1": item})
        for state in invalid:
            with self.subTest(state=state), patch.object(niri, "_request", return_value=state):
                with self.assertRaises(CompositorError):
                    niri.outputs()
        with patch.object(niri.shutil, "which", return_value="/usr/bin/niri"), \
             patch.object(niri.subprocess, "run", return_value=SimpleNamespace(
                 returncode=1, stderr="socket unavailable", stdout="")):
            with self.assertRaisesRegex(CompositorError, "socket unavailable"):
                niri.outputs()
