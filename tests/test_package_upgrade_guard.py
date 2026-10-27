"""Compatible upgrades must not use the destructive removal gate."""
from pathlib import Path
import unittest
from unittest.mock import patch
from monitorize_vkms import package_maintenance as maintenance

ROOT = Path(__file__).resolve().parents[1]


class PackageUpgradeGuardTests(unittest.TestCase):
    def test_release_versions_match_version_file_syntax(self):
        for value, expected in [('1.0', (1, 0, 0)), ('1.0-1', (1, 0, 0)),
                                ('0.3', (0, 3, 0)), ('0.3.0-2', (0, 3, 0)),
                                ('1', None), ('1.0rc1', None), ('1.0.0.0', None)]:
            self.assertEqual(maintenance.release_version(value), expected)

    def test_compatible_forward_or_same_version_upgrade_does_not_require_disable(self):
        with patch.object(maintenance, 'installed_release', return_value=(0, 3, 0)), \
             patch.object(maintenance, 'assert_removal_safe') as removal:
            for version in ('0.3', '0.3.0', '1.0', '1.0.0', '1.0-1'):
                maintenance.assert_upgrade_safe(version)
            removal.assert_not_called()

    def test_unsupported_unknown_or_downgrade_target_keeps_removal_protection(self):
        with patch.object(maintenance, 'installed_release', return_value=(1, 0, 0)), \
             patch.object(maintenance, 'assert_removal_safe', side_effect=RuntimeError('protected')):
            for version in ('0.2.9', '0.3.0', 'unknown', None):
                with self.subTest(version=version), self.assertRaises(RuntimeError):
                    maintenance.assert_upgrade_safe(version)

    def test_source_installer_validates_then_checks_upgrade_target(self):
        main = (ROOT/'install.sh').read_text().split('main() {', 1)[1]
        self.assertLess(main.index('validate_metadata'), main.index(' pre-upgrade'))
        self.assertIn('pre-upgrade --target-version "$MODULE_PACKAGE_VERSION"', main)
        self.assertNotIn(' pre-remove', main)

    def test_native_hooks_distinguish_upgrade_from_remove(self):
        arch = (ROOT/'packaging/arch/monitorize-vkms-guard.hook').read_text()
        self.assertNotIn('Operation = Upgrade', arch)
        self.assertIn('Operation = Remove', arch)
        spec = (ROOT/'packaging/rpm/monitorize-vkms.spec').read_text()
        self.assertIn('pre-upgrade --target-version %{version}', spec.split('%pretrans',1)[1].split('%preun',1)[0])
        self.assertIn('pre-remove', spec.split('%preun',1)[1])
        self.assertIn('pre-upgrade --target-version "$2"', (ROOT/'packaging/deb/debian/prerm').read_text())
