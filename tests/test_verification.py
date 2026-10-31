"""Installation fixtures exercise passive checks without accessing host devices."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from monitorize_vkms.verification import verify


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = []

    def write(self, name, value='fixture'):
        path = self.root / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def probe(self, args, **kwargs):
        self.commands.append(args)
        output = {('modinfo', '-k', 'test-kernel', '-n', 'monitorize_vkms'):
                  '/lib/modules/test-kernel/updates/monitorize_vkms.ko',
                  ('modinfo', '-k', 'test-kernel', '-n', 'vkms'):
                  '/lib/modules/test-kernel/kernel/drivers/gpu/drm/vkms/vkms.ko',
                  ('modinfo', '-F', 'name'): 'monitorize_vkms',
                  ('modinfo', '-F', 'vermagic'): 'test-kernel SMP',
                  ('modinfo', '-p'): 'create_default_dev:bool',
                  ('dkms',): 'monitorize-vkms/1.0, test-kernel, x86_64: installed',
                  ('systemctl',): 'active'}
        text = next((text for key, text in output.items() if tuple(args[:len(key)]) == key), '')
        return subprocess.CompletedProcess(args, 0, text, '')

    def healthy(self, service_base='/etc/systemd/system/'):
        self.write('/usr/lib/monitorize-vkms/VERSION', '1.0\n')
        self.write('/usr/lib/monitorize-vkms/monitorize_vkms/helper.py')
        for name in ('helper', 'dbus-service', 'authorization'):
            self.write('/usr/libexec/monitorize-vkms/monitorize-vkms-' + name)
        self.write('/sys/module/monitorize_vkms/parameters/create_default_dev', 'N')
        base = '/sys/kernel/config/monitorize-vkms/monitorize/'
        self.write(base + 'enabled', '1')
        for index in (0, 1):
            for name, value in [('enabled', '1'), ('status', '0'), ('edid', ''), ('edid_enabled', '0')]:
                self.write(base + f'connectors/connector{index}/' + name, value)
        for name in ('monitorize-vkms.service', 'monitorize-vkms-bootstrap.service'):
            self.write(service_base + name)
        for directory, name in (
            ('/usr/share/dbus-1/system-services/', 'io.github.vinnavannewton.MonitorizeVkms1.service'),
            ('/usr/share/dbus-1/interfaces/', 'io.github.vinnavannewton.MonitorizeVkms1.xml'),
            ('/usr/share/polkit-1/actions/', 'io.github.vinnavannewton.monitorize-vkms.policy'),
            ('/usr/share/dbus-1/system.d/', 'io.github.vinnavannewton.MonitorizeVkms1.conf')):
            self.write(directory + name)
        target = self.root / 'sys/devices/faux/monitorize/drm/card0'
        target.mkdir(parents=True)
        link = self.root / 'sys/class/drm/card0'
        link.parent.mkdir(parents=True)
        link.symlink_to(target)

    def test_idle_disconnected_connectors_are_working_for_both_service_paths(self):
        self.healthy('/usr/lib/systemd/system/')
        result = verify(root=self.root, kernel='test-kernel', run=self.probe)
        self.assertEqual(result['state'], 'working', result)
        self.assertTrue(all(args[0] in ('modinfo', 'dkms', 'systemctl') for args in self.commands))
        self.assertFalse(any('restart' in args or 'start' in args for args in self.commands))

    def test_no_markers_and_no_module_is_not_installed(self):
        result = verify(root=self.root, kernel='test-kernel',
                        run=lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'missing'))
        self.assertFalse(result['installed'])
        self.assertEqual(result['state'], 'not_installed')

    def test_partial_install_is_not_reported_as_missing(self):
        self.write('/usr/lib/monitorize-vkms/VERSION', '1.0')
        result = verify(root=self.root, kernel='test-kernel', run=self.probe)
        self.assertTrue(result['installed'])
        self.assertEqual(result['state'], 'needs_attention')

    def test_restart_marker_applies_only_to_installing_boot(self):
        self.healthy()
        self.write('/proc/sys/kernel/random/boot_id', 'current-boot')
        marker = self.write('/var/lib/monitorize-vkms/reboot-required.json', json.dumps({'boot_id': 'current-boot'}))
        self.assertEqual(verify(root=self.root, kernel='test-kernel', run=self.probe)['state'], 'restart_required')
        marker.write_text(json.dumps({'boot_id': 'previous-boot'}))
        self.assertEqual(verify(root=self.root, kernel='test-kernel', run=self.probe)['state'], 'working')

    def test_timeout_does_not_claim_missing_or_working(self):
        self.healthy()
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('modinfo', 3)
        result = verify(root=self.root, kernel='test-kernel', run=timeout)
        self.assertTrue(result['installed'])
        self.assertEqual(result['state'], 'could_not_verify')

    def test_inconsistent_helper_is_unhealthy(self):
        self.healthy()
        self.write('/usr/lib/monitorize-vkms/monitorize_vkms/helper.py', 'different revision')
        self.assertEqual(verify(root=self.root, kernel='test-kernel', run=self.probe)['state'], 'needs_attention')

    def test_idle_activatable_service_is_ready_but_failed_service_is_not(self):
        self.healthy()
        for state, expected in [('inactive', 'working'), ('failed', 'needs_attention')]:
            def probe(args, **kwargs):
                if args[0] == 'systemctl':
                    return subprocess.CompletedProcess(args, 0, state, '')
                return self.probe(args, **kwargs)
            self.assertEqual(verify(root=self.root, kernel='test-kernel', run=probe)['state'], expected)
