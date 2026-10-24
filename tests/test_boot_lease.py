"""Ownership files must survive failed publication and reject unsafe inputs."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monitorize_vkms import lease


class LeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / 'state'
        self.owner = patch.object(lease, 'OWNER_UID', os.getuid())
        self.owner.start()
        self.addCleanup(self.owner.stop)

    def test_ordinary_lease_round_trip_and_remove(self):
        lease.write_lease(self.state, 'mon1', {'uid': 1000, 'state': 'active'})
        self.assertEqual(lease.read_lease(self.state, 'mon1'), {'uid': 1000, 'state': 'active'})
        self.assertEqual((self.state/'primary.json').stat().st_mode & 0o777, 0o600)
        lease.remove_lease(self.state, 'mon1')
        self.assertIsNone(lease.read_lease(self.state, 'mon1'))

    def test_failed_replace_retains_previous_state_and_cleans_temporary(self):
        lease.write_lease(self.state, 'mon1', {'state': 'active'})
        with patch.object(lease.os, 'replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                lease.write_lease(self.state, 'mon1', {'state': 'pending'})
        self.assertEqual(lease.read_lease(self.state, 'mon1'), {'state': 'active'})
        self.assertEqual([p.name for p in self.state.iterdir()], ['primary.json'])

    def test_symlink_hardlink_permissions_and_oversize_rejected(self):
        lease.write_lease(self.state, 'mon1', {'state': 'active'})
        path = self.state/'primary.json'
        for value in ('[]', '{', ' ' * 65537):
            path.write_text(value)
            with self.assertRaises(ValueError):
                lease.read_lease(self.state, 'mon1')
        path.write_text('{}')
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            lease.read_lease(self.state, 'mon1')
        path.chmod(0o600)
        os.link(path, self.state/'other')
        with self.assertRaises(ValueError):
            lease.read_lease(self.state, 'mon1')
        path.unlink()
        path.symlink_to(self.state/'other')
        with self.assertRaises((OSError, ValueError)):
            lease.read_lease(self.state, 'mon1')

    def test_unsafe_directory_and_unknown_slot_rejected(self):
        self.state.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            lease.write_lease(self.state, 'mon1', {})
        with self.assertRaises(ValueError):
            lease.read_lease(self.state, '../outside')
