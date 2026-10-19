"""Regression checks for upgrade preservation and installer rollback ordering."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "install.sh").read_text()


def function(name):
    start = SOURCE.index(name + "() {")
    end = SOURCE.index("\n}\n", start) + 3
    return SOURCE[start:end]


class InstallerRollbackTest(unittest.TestCase):
    def run_shell(self, functions, body):
        with TemporaryDirectory() as directory:
            script = "set -euo pipefail\n" + "\n".join(function(name) for name in functions)
            script += "\n" + body
            result = subprocess.run(["bash", "-c", script, "test", directory],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_backup_and_restore_existing_and_previously_absent_files(self):
        self.run_shell(["backup_one_target", "restore_install_targets"], '''
WORK_DIR="$1/backup"
mkdir -p "$WORK_DIR"
BACKUP_TARGETS=(); BACKUP_EXISTS=(); BOOTSTRAP_WAS_ENABLED=0
systemctl() { return 0; }
warn() { echo "$*" >&2; }
printf old > "$1/tool"
backup_one_target "$1/tool"
backup_one_target "$1/absent"
printf new > "$1/tool"
printf new > "$1/absent"
restore_install_targets
[[ "$(cat "$1/tool")" == old && ! -e "$1/absent" ]]
''')

    def test_failed_backup_never_registers_partial_snapshot(self):
        self.run_shell(["backup_one_target"], '''
WORK_DIR="$1/backup"
BACKUP_TARGETS=(); BACKUP_EXISTS=()
printf old > "$1/tool"
cp() { return 1; }
if backup_one_target "$1/tool"; then exit 1; fi
[[ ${#BACKUP_TARGETS[@]} == 0 && ${#BACKUP_EXISTS[@]} == 0 ]]
[[ "$(cat "$1/tool")" == old ]]
''')

    def test_cli_install_records_checkout_and_rollback_restores_previous_location(self):
        self.run_shell(["install_cli_and_tools", "backup_one_target", "restore_install_targets"], '''
WORK_DIR="$1/backup"
REPOSITORY_DIR="$1/source with spaces"
PYTHON_LIB_DIR="$1/lib"
mkdir -p "$WORK_DIR" "$PYTHON_LIB_DIR" "$REPOSITORY_DIR"
BACKUP_TARGETS=(); BACKUP_EXISTS=(); BOOTSTRAP_WAS_ENABLED=0
printf '/previous/checkout\\n' > "$PYTHON_LIB_DIR/source-checkout"
backup_one_target "$PYTHON_LIB_DIR"
for variable in HELPER_SOURCE HELPER_PATH DBUS_SOURCE DBUS_PATH DBUS_UNIT_SOURCE DBUS_UNIT_PATH \\
  DBUS_ACTIVATION_SOURCE DBUS_ACTIVATION_PATH DBUS_POLICY_SOURCE DBUS_POLICY_PATH \\
  DBUS_INTERFACE_SOURCE DBUS_INTERFACE_PATH POLKIT_POLICY_SOURCE POLKIT_POLICY_PATH CLI_SOURCE CLI_PATH; do
  printf -v "$variable" '%s' "$1/unused"
done
install() { :; }
systemctl() { :; }
chown() { :; }
chmod() { :; }
log() { :; }
warn() { :; }
install_cli_and_tools
[[ "$(cat "$PYTHON_LIB_DIR/source-checkout")" == "$REPOSITORY_DIR" ]]
restore_install_targets
[[ "$(cat "$PYTHON_LIB_DIR/source-checkout")" == /previous/checkout ]]
''')

    def test_failed_snapshot_stage_does_not_restore_or_delete_existing_targets(self):
        self.run_shell(["rollback_failed_install"], '''
WORK_DIR="$1/backup"; mkdir "$WORK_DIR"
INSTALL_COMMITTED=0; SYSTEM_MUTATION_STARTED=0
BACKUP_TARGETS=("$1/tool"); BACKUP_EXISTS=(1)
printf old > "$1/tool"
restore_install_targets() { printf bad > "$1/tool"; }
rollback_failed_install 1
[[ "$(cat "$1/tool")" == old ]]
''')

    def test_registered_same_version_can_build_for_another_kernel(self):
        self.run_shell(["install_dkms"], '''
PACKAGE_NAME=monitorize-vkms; MODULE_PACKAGE_VERSION=0.2.4
MODULE_NAME=monitorize_vkms; KERNEL=test-kernel
NEW_PACKAGE_ADDED=0; TARGET_KERNEL_ATTEMPTED=0
CALLS="$1/calls"
dkms() {
  if [[ "$1" == status ]]; then printf 'monitorize-vkms/0.2.4, old-kernel: installed\n'
  else printf '%s\n' "$*" >> "$CALLS"; fi
}
source_matches_installed() { return 0; }
stage_dkms_source() { exit 99; }
depmod() { return 0; }
log() { return 0; }
die() { exit 99; }
install_dkms
[[ "$NEW_PACKAGE_ADDED" == 0 && "$TARGET_KERNEL_ATTEMPTED" == 1 ]]
[[ "$(cat "$CALLS")" == $'build -m monitorize-vkms -v 0.2.4 -k test-kernel\ninstall -m monitorize-vkms -v 0.2.4 -k test-kernel' ]]
''')

    def test_same_version_reuse_compares_build_inputs_and_extra_sources(self):
        self.run_shell(["source_matches_installed"], '''
REPOSITORY_DIR="$1/repository"; SOURCE_DIR="$1/installed"
for directory in "$REPOSITORY_DIR" "$SOURCE_DIR"; do
  mkdir -p "$directory/src/vkms" "$directory/scripts"
  for file in VERSION dkms.conf src/vkms/Makefile scripts/probe-drm-atomic-api.sh src/vkms/a.c src/vkms/a.h; do
    printf original > "$directory/$file"
  done
done
source_matches_installed
printf changed > "$SOURCE_DIR/scripts/probe-drm-atomic-api.sh"
if source_matches_installed; then exit 1; fi
printf original > "$SOURCE_DIR/scripts/probe-drm-atomic-api.sh"
printf extra > "$SOURCE_DIR/src/vkms/extra.c"
if source_matches_installed; then exit 1; fi
''')

    def test_upgrade_snapshots_monitorize_owned_paths_before_mutation(self):
        main = SOURCE.split("main() {", 1)[1].split("\n}\n\nmain", 1)[0]
        self.assertLess(main.index("backup_install_targets"), main.index("remove_legacy_versions"))
        for path in ("$MODPROBE_CONFIG", "$HELPER_PATH", "$CLI_PATH", "$PYTHON_LIB_DIR",
                     "$BOOTSTRAP_UNIT_PATH", "$BOOTSTRAP_PATH"):
            self.assertIn(path, SOURCE)

    def test_failed_upgrade_restores_snapshots_before_discarding_backup_directory(self):
        rollback = SOURCE.split("rollback_failed_install() {", 1)[1].split("\n}\n\nmain", 1)[0]
        self.assertLess(rollback.index("restore_install_targets"), rollback.index('rm -rf -- "$WORK_DIR"'))
        self.assertIn("INSTALL_COMMITTED", rollback)

    def test_install_does_not_remove_prior_isolated_dkms_releases_mid_transaction(self):
        main = SOURCE.split("main() {", 1)[1].split("\n}\n\nmain", 1)[0]
        self.assertNotIn("remove_old_versions", main)
        self.assertIn("preserving it for manual repair", SOURCE)


if __name__ == "__main__":
    unittest.main()
