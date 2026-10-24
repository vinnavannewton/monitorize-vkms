"""Live runners are allowed; child capture processes must block host cleanup."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from monitorize_vkms import boot_runtime as runtime


class QuiescenceTests(unittest.TestCase):
    def test_capture_child_or_runner_replacement_blocks_cleanup(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);group=root/'engine';group.mkdir()
            (group/'cgroup.procs').write_text('23\n24\n')
            properties=dict(MainPID='23',ControlGroup='/engine',ActiveState='active')
            with patch.object(runtime,'unit_properties',return_value=properties), \
                 patch.object(runtime,'CGROUP_ROOT',root), \
                 patch.object(runtime,'runner_identity',return_value=True):
                with self.assertRaises(RuntimeError):runtime.verify_engine_cgroups(1000)
                (group/'cgroup.procs').write_text('23\n')
                runtime.verify_engine_cgroups(1000)
                with patch.object(runtime,'runner_identity',return_value=False):
                    with self.assertRaises(PermissionError):runtime.verify_engine_cgroups(1000)

    def test_unknown_cgroup_path_or_running_unit_without_main_pid_refused(self):
        for properties in (dict(MainPID='0',ControlGroup='/engine',ActiveState='active'),
                           dict(MainPID='23',ControlGroup='/../outside',ActiveState='active')):
            with self.subTest(properties=properties),patch.object(runtime,'unit_properties',return_value=properties):
                with self.assertRaises((RuntimeError,ValueError)):
                    runtime.verify_engine_cgroups(1000)
